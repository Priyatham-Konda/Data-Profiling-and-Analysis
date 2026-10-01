"""Salesforce connections, held in memory. The only place a secret lives.

A connection holds a client's secret and an access token into their CRM, so
it is kept in process memory only: never written to disk, never logged,
never returned by the API. It serves one assessment and is closed as soon as
that assessment's objects are downloaded, when it is deleted, or after
SF_CONNECTION_IDLE_MINUTES unused. Closing revokes the token at Salesforce
and drops both values.

A backend restart therefore closes every connection. That is the intended
failure mode: the user connects again, and nothing sensitive survived the
process.
"""
from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from ... import config
from ..base import ConnectorError
from . import auth
from . import client as sf_client
from .client import SalesforceApiError, SalesforceClient

# Closed connections stay answerable -- GET reports why they closed -- for a
# day, then are forgotten.
_KEEP_CLOSED = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


@dataclass
class Connection:
    id: str
    instance_url: str
    client_id: str = field(repr=False)
    client_secret: Optional[str] = field(repr=False)
    access_token: Optional[str] = field(repr=False, default=None)

    org_id: Optional[str] = None
    org_name: Optional[str] = None
    org_edition: Optional[str] = None
    is_sandbox: bool = False
    username: Optional[str] = None

    connected_at: datetime = field(default_factory=_now)
    last_used: datetime = field(default_factory=_now)
    closed_at: Optional[datetime] = None
    state: str = "connected"
    closed_reason: Optional[str] = None
    assessment_id: Optional[str] = None
    sobjects: Optional[list[dict]] = field(repr=False, default=None)

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # -- the API view ----------------------------------------------------------
    @property
    def environment(self) -> str:
        return "sandbox" if self.is_sandbox else "production"

    @property
    def display_name(self) -> str:
        name = self.org_name or self.instance_url.removeprefix("https://")
        return f"{name} (Sandbox)" if self.is_sandbox else name

    def expires_at(self) -> Optional[datetime]:
        if self.state != "connected" or self.assessment_id:
            return None
        return self.last_used + timedelta(minutes=config.SF_CONNECTION_IDLE_MINUTES)

    def to_api(self) -> dict:
        # Built field by field: the secret, the client ID and the token must
        # never be serialised by accident, so nothing here is generic.
        return {
            "id": self.id,
            "state": self.state,
            "environment": self.environment,
            "orgId": self.org_id,
            "orgName": self.org_name,
            "orgEdition": self.org_edition,
            "instanceUrl": self.instance_url,
            "username": self.username,
            "connectedAt": _iso(self.connected_at),
            "expiresAt": _iso(self.expires_at()),
            "closedReason": self.closed_reason,
            "assessmentId": self.assessment_id,
        }

    # -- access ----------------------------------------------------------------
    def token(self, force_new: bool = False) -> str:
        """The access token, re-issued from the stored credentials if needed."""
        with self._lock:
            if self.state != "connected" or not self.client_secret:
                raise ConnectorError(
                    "not_connected",
                    "The Salesforce connection has closed. Connect again to continue.",
                    http_status=409,
                )
            if force_new or not self.access_token:
                with sf_client.new_http_client() as http:
                    issued = auth.request_token(
                        http, self.instance_url, self.client_id, self.client_secret
                    )
                self.access_token = issued["access_token"]
            return self.access_token

    def client(self, http: httpx.Client) -> SalesforceClient:
        return SalesforceClient(http, self.instance_url, self.token)

    def touch(self) -> None:
        self.last_used = _now()

    def _close(self, reason: str) -> Optional[str]:
        """Drop the credentials; return the token for revoking, if any."""
        with self._lock:
            if self.state == "closed":
                return None
            token = self.access_token
            self.client_secret = None
            self.access_token = None
            self.sobjects = None
            self.state = "closed"
            self.closed_reason = reason
            self.closed_at = _now()
            return token


class ConnectionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Connection] = {}

    def add(self, connection: Connection) -> None:
        with self._lock:
            self._items[connection.id] = connection

    def get(self, connection_id: str) -> Optional[Connection]:
        self.sweep()
        with self._lock:
            return self._items.get(connection_id)

    def claim(self, connection_id: str, assessment_id: str) -> Connection:
        """Reserve a connection for one assessment, atomically."""
        self.sweep()
        with self._lock:
            connection = self._items.get(connection_id)
            if connection is None:
                raise ConnectorError(
                    "not_found",
                    "That Salesforce connection doesn't exist. Connect again.",
                    http_status=404,
                )
            if connection.state != "connected":
                raise ConnectorError(
                    "not_connected",
                    "That Salesforce connection has closed. Connect again to start "
                    "another assessment.",
                    http_status=409,
                )
            if connection.assessment_id:
                raise ConnectorError(
                    "in_use",
                    "That Salesforce connection is already being used by another "
                    "assessment. Connect again to start a new one.",
                    http_status=409,
                )
            connection.assessment_id = assessment_id
            return connection

    def close(self, connection_id: str, reason: str) -> None:
        with self._lock:
            connection = self._items.get(connection_id)
        if connection is None:
            return
        token = connection._close(reason)
        if token:
            with sf_client.new_http_client() as http:
                auth.revoke(http, connection.instance_url, token)

    def sweep(self) -> None:
        """Close idle connections; forget long-closed ones."""
        now = _now()
        with self._lock:
            items = list(self._items.values())
        for connection in items:
            expiry = connection.expires_at()
            if expiry and now >= expiry:
                self.close(connection.id, "idle")
        with self._lock:
            for connection_id in [
                c.id for c in self._items.values()
                if c.closed_at and now - c.closed_at > _KEEP_CLOSED
            ]:
                del self._items[connection_id]

    def clear(self) -> None:
        """Close everything. Used at shutdown and between tests."""
        with self._lock:
            ids = list(self._items)
        for connection_id in ids:
            self.close(connection_id, "disconnected")
        with self._lock:
            self._items.clear()


STORE = ConnectionStore()


def connect(instance_url: str, client_id: str, client_secret: str) -> Connection:
    """Exchange the credentials for a token and identify the org.

    Nothing is stored unless every step succeeds: a failed attempt leaves no
    connection and no secret behind.
    """
    instance = auth.normalise_instance_url(instance_url)
    client_id = client_id.strip()
    client_secret = client_secret.strip()

    with sf_client.new_http_client() as http:
        issued = auth.request_token(http, instance, client_id, client_secret)
        # Salesforce says where the org's API lives; trust it only if it is
        # itself a Salesforce address.
        try:
            instance = auth.normalise_instance_url(issued.get("instance_url") or instance)
        except ConnectorError:
            pass

        connection = Connection(
            id=f"sfc_{secrets.token_urlsafe(12)}",
            instance_url=instance,
            client_id=client_id,
            client_secret=client_secret,
            access_token=issued["access_token"],
        )
        api = connection.client(http)
        try:
            user = api.userinfo()
            org = api.organization()
        except SalesforceApiError as exc:
            auth.revoke(http, instance, issued["access_token"])
            if exc.code == "API_DISABLED_FOR_ORG" or "api is not enabled" in exc.message.lower():
                raise ConnectorError(
                    "api_disabled",
                    "Salesforce accepted the credentials, but API access isn't "
                    "enabled for this org or for the Run As user. Professional and "
                    "Essentials editions need Salesforce's API add-on; otherwise "
                    "the Run As user needs the API Enabled permission.",
                ) from None
            raise ConnectorError(
                "failed",
                f"Salesforce accepted the credentials but refused to identify the "
                f"org: {exc.message}",
            ) from None
        except httpx.HTTPError:
            auth.revoke(http, instance, issued["access_token"])
            raise ConnectorError(
                "unreachable",
                "The connection to Salesforce dropped while connecting. Try again.",
                http_status=502,
            ) from None

    connection.username = user.get("preferred_username") or user.get("email")
    connection.org_id = org.get("Id") or user.get("organization_id")
    connection.org_name = org.get("Name")
    connection.org_edition = org.get("OrganizationType")
    connection.is_sandbox = bool(org.get("IsSandbox"))
    STORE.add(connection)
    return connection
