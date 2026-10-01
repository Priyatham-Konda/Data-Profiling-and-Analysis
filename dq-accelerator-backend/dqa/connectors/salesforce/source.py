"""Turn a selection of Salesforce objects into an AssessmentPlan."""
from __future__ import annotations

from pathlib import Path

import httpx

from ..base import (
    AssessmentPlan,
    CheckCancelledFn,
    ConnectorError,
    ProgressFn,
    SourceObject,
)
from . import catalogue
from . import client as sf_client
from .client import SalesforceApiError
from .connections import STORE, Connection
from .extract import extract_object


def plan(source: dict, objects: list[str], assessment_id: str) -> AssessmentPlan:
    """Validate the selection, reserve the connection, and describe the work.

    Raises ConnectorError for anything the user must fix. On success the
    connection belongs to `assessment_id` and is closed by `plan.close`.
    """
    connection_id = source.get("connectionId")
    if not connection_id:
        raise ConnectorError(
            "invalid_source", "source.connectionId is required.", http_status=400
        )

    connection = STORE.claim(connection_id, assessment_id)
    try:
        known = catalogue.labels(_sobjects(connection))
        unknown = [name for name in objects if name not in known]
        if unknown:
            raise ConnectorError(
                "unknown_object",
                f"These objects aren't available in {connection.display_name}: "
                f"{', '.join(unknown)}.",
                http_status=400,
            )
    except BaseException:
        # Validation failed: release the connection for another attempt.
        connection.assessment_id = None
        raise

    return AssessmentPlan(
        name=connection.display_name,
        source={
            "type": "salesforce",
            "orgName": connection.org_name,
            "orgId": connection.org_id,
            "environment": connection.environment,
        },
        objects=[SourceObject(name=name, label=known[name]) for name in objects],
        extractor=lambda name: _extractor(connection, name),
        close=lambda reason: STORE.close(connection.id, reason),
    )


def _sobjects(connection: Connection) -> list[dict]:
    """describe_global for the connection, fetched once and cached on it."""
    if connection.sobjects is None:
        try:
            with sf_client.new_http_client() as http:
                connection.sobjects = connection.client(http).describe_global()
        except SalesforceApiError as exc:
            raise ConnectorError(
                "failed",
                f"Salesforce wouldn't list the org's objects: {exc.message}",
                http_status=502,
            ) from None
        except httpx.HTTPError:
            raise ConnectorError(
                "unreachable",
                "The connection to Salesforce dropped while listing objects. Try again.",
                http_status=502,
            ) from None
    connection.touch()
    return connection.sobjects


def _extractor(connection: Connection, object_name: str):
    def extract(dest: Path, progress: ProgressFn, check_cancelled: CheckCancelledFn) -> dict:
        with sf_client.new_http_client() as http:
            return extract_object(
                connection.client(http), object_name, dest, progress, check_cancelled
            )

    return extract


def objects_listing(connection: Connection, include_all: bool) -> dict:
    """The body of GET /salesforce/connections/{id}/objects."""
    sobjects = _sobjects(connection)
    visible = [
        s["name"] for s in sobjects
        if catalogue.assessable(s) and (include_all or catalogue.visible_by_default(s))
    ]
    try:
        with sf_client.new_http_client() as http:
            counts = connection.client(http).record_counts(visible)
    except httpx.HTTPError:
        counts = {}  # counts are a nicety; the picker works without them
    return catalogue.build(sobjects, counts, include_all)
