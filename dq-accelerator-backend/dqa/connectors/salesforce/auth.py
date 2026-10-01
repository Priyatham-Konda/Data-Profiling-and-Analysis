"""The org's address, and exchanging client credentials for an access token.

The address check is a security control, not input tidying. The client
secret is sent to whatever host this returns, so only Salesforce's own
domains are accepted. Anything else would let a typo -- or a crafted
request -- post a client's secret to a host we don't control.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from ..base import ConnectorError

# What people copy from the address bar, mapped to the org's My Domain, which
# is the only host the client credentials flow accepts.
_REWRITES = (
    (".lightning.force.com", ".my.salesforce.com"),
    (".my.salesforce-setup.com", ".my.salesforce.com"),
)
_MY_DOMAIN = ".my.salesforce.com"
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_GENERIC_LOGIN_HOSTS = {"login.salesforce.com", "test.salesforce.com"}


def _invalid(shown: str) -> ConnectorError:
    return ConnectorError(
        "invalid_url",
        f"{shown} isn't a Salesforce address. Copy the address from your browser "
        "while signed in to Salesforce -- it ends in my.salesforce.com or "
        "lightning.force.com.",
        http_status=400,
    )


def normalise_instance_url(raw: str) -> str:
    """The org's My Domain as `https://<host>`, or ConnectorError.

    Accepts what users actually paste: with or without a scheme, with a path,
    as a Lightning address, or as a sandbox or Developer Edition form.
    """
    text = (raw or "").strip()
    if not text:
        raise ConnectorError("invalid_url", "Enter the org's Salesforce address.", 400)
    shown = text if len(text) <= 80 else text[:77] + "..."

    candidate = text if "://" in text else f"https://{text}"
    try:
        parsed = urlparse(candidate)
        port = parsed.port
    except ValueError:
        raise _invalid(shown) from None
    if parsed.scheme not in ("https", "http") or parsed.username or port:
        raise _invalid(shown)

    host = (parsed.hostname or "").lower().rstrip(".")
    if host in _GENERIC_LOGIN_HOSTS:
        raise ConnectorError(
            "invalid_url",
            f"{host} is Salesforce's general sign-in address, not the org's own. "
            "Use the org's address, which ends in my.salesforce.com -- it is "
            "under Setup → My Domain.",
            http_status=400,
        )
    for old, new in _REWRITES:
        if host.endswith(old):
            host = host[: -len(old)] + new
            break

    if not host.endswith(_MY_DOMAIN):
        raise _invalid(shown)
    labels = host[: -len(_MY_DOMAIN)].split(".")
    if not labels or not all(_LABEL.match(label) for label in labels):
        raise _invalid(shown)
    # Plain HTTP is upgraded: the secret never travels unencrypted to Salesforce.
    return f"https://{host}"


def request_token(
    http: httpx.Client, instance_url: str, client_id: str, client_secret: str
) -> dict:
    """POST the client credentials to the org's token endpoint.

    Returns Salesforce's token response. Every failure becomes a
    ConnectorError naming the setup step to check, because the person who
    sees it is a sales rep or the client's administrator, not a developer.
    """
    host = instance_url.removeprefix("https://")
    try:
        response = http.post(
            f"{instance_url}/services/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError:
        raise ConnectorError(
            "unreachable",
            f"Nothing answered at {host}. Check the address, and that this "
            "server can reach Salesforce.",
            http_status=502,
        ) from None

    try:
        body = response.json()
    except ValueError:
        body = {}

    if response.status_code == 200 and body.get("access_token"):
        return body
    if response.status_code >= 500 or not isinstance(body, dict) or "error" not in body:
        raise ConnectorError(
            "unreachable",
            f"{host} didn't answer like a Salesforce org (HTTP {response.status_code}). "
            "Check the address.",
            http_status=502,
        )
    raise _token_error(body.get("error", ""), body.get("error_description", ""), host)


def _token_error(error: str, description: str, host: str) -> ConnectorError:
    text = description.lower()
    if error == "invalid_client_id":
        return ConnectorError(
            "invalid_client",
            "Salesforce doesn't recognise that client ID. Copy the Consumer Key "
            "again from the app's OAuth settings. New credentials can take a few "
            "minutes to start working after the app is saved.",
        )
    if error == "invalid_client":
        return ConnectorError(
            "invalid_client",
            "Salesforce rejected the client secret for that client ID. Copy both "
            "again from the app's OAuth settings -- the secret may also have "
            "been changed since it was shared.",
        )
    if error == "unsupported_grant_type":
        return ConnectorError(
            "flow_not_enabled",
            f"The app at {host} isn't set up for the client credentials flow. In "
            "the app's settings, tick Enable Client Credentials Flow under Flow "
            "Enablement and again under the OAuth Policies.",
        )
    if error == "invalid_grant" and "client credentials" in text and "user" in text:
        return ConnectorError(
            "no_run_as_user",
            "The app has no Run As user. On the app's Policies tab, under OAuth "
            "Policies, enter the username the assessment should run as.",
        )
    if error == "invalid_grant" and "domain" in text:
        return ConnectorError(
            "invalid_url",
            "Salesforce only accepts these credentials at the org's own address, "
            "which ends in my.salesforce.com -- it is under Setup → My Domain.",
            http_status=400,
        )
    detail = f" Salesforce said: {description}" if description else ""
    return ConnectorError(
        "failed",
        f"Salesforce refused the credentials ({error or 'unknown error'}).{detail}",
    )


def revoke(http: httpx.Client, instance_url: str, token: str) -> None:
    """Revoke an access token. Best effort: a failure here must not block."""
    try:
        http.post(f"{instance_url}/services/oauth2/revoke", data={"token": token})
    except httpx.HTTPError:
        pass
