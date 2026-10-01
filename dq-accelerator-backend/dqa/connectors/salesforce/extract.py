"""Download one Salesforce object to CSV with SOQL.

Bulk API 2.0 is the primary path: it takes the SOQL in a request body, so
wide objects never hit a URL length limit, returns CSV that goes straight to
the engine, and costs a handful of API calls however large the object is.
The few objects the Bulk API doesn't support fall back to the ordinary query
API, which pages through JSON; that path writes the same CSV.

Either way the file is written as it arrives, page by page, so memory stays
flat regardless of the object's size.
"""
from __future__ import annotations

import csv
import json
import logging
import time
from pathlib import Path

import httpx

from ... import config
from ..base import CheckCancelledFn, ExtractionError, ProgressFn
from .client import SalesforceApiError, SalesforceClient

log = logging.getLogger("dqa.connectors.salesforce")

# Compound fields repeat their components (BillingAddress holds BillingCity
# and the rest), and neither the Bulk API nor a meaningful quality check can
# use them. base64 fields are file bodies, not data to assess.
_EXCLUDED_TYPES = {"address", "location", "base64"}

# A test seam: the Bulk API is polled, and tests shouldn't wait for real.
_sleep = time.sleep


class _BulkUnsupported(Exception):
    """The Bulk API won't export this object; use the query API instead."""


# --------------------------------------------------------------------------
# Fields
# --------------------------------------------------------------------------
def downloadable_fields(describe: dict) -> list[dict]:
    """Every field worth downloading, with Id first."""
    fields = [
        f for f in describe.get("fields", [])
        if f.get("type") not in _EXCLUDED_TYPES and not f.get("deprecatedAndHidden")
    ]
    fields.sort(key=lambda f: f.get("name") != "Id")  # stable: Id first, rest as declared
    return fields


def field_metadata(fields: list[dict]) -> dict[str, dict]:
    """What the profile's `salesforce` block shows for each column."""
    out = {}
    for f in fields:
        picklist = None
        if f.get("type") in ("picklist", "multipicklist"):
            picklist = [p.get("value") for p in f.get("picklistValues") or []
                        if p.get("active", True)]
        out[f["name"]] = {
            "label": f.get("label") or f["name"],
            "type": f.get("type", "string"),
            "custom": bool(f.get("custom")),
            # Salesforce's own notion of required: must be supplied on create
            # and has no default. `nillable` alone is false for system fields
            # like Id and CreatedDate, which nobody enters.
            "required": (not f.get("nillable", True))
                        and bool(f.get("createable"))
                        and not f.get("defaultedOnCreate"),
            "referenceTo": list(f.get("referenceTo") or []) or None,
            "picklistValues": picklist,
            "length": f.get("length") or None,
        }
    return out


def build_soql(object_name: str, fields: list[dict]) -> str:
    return f"SELECT {', '.join(f['name'] for f in fields)} FROM {object_name}"


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def extract_object(
    client: SalesforceClient,
    object_name: str,
    dest: Path,
    progress: ProgressFn,
    check_cancelled: CheckCancelledFn,
) -> dict:
    """Download every record of `object_name` to `dest` as CSV.

    Returns the metadata stored beside the run: the object, how many records
    came down and how, and each field's Salesforce description.
    """
    try:
        describe = client.describe(object_name)
    except SalesforceApiError as exc:
        raise ExtractionError(
            f"Salesforce wouldn't describe {object_name} to the Run As user: {exc.message}"
        ) from None

    label = describe.get("label") or object_name
    fields = downloadable_fields(describe)
    if not fields:
        raise ExtractionError(f"{label} has no fields the Run As user can read.")
    soql = build_soql(object_name, fields)
    progress(0.0, "Waiting for Salesforce to prepare the export")

    try:
        try:
            records = _bulk(client, soql, dest, progress, check_cancelled)
            method = "bulk"
        except _BulkUnsupported as exc:
            log.info("bulk export unsupported for %s (%s); using the query API",
                     object_name, exc)
            records = _query(client, soql, [f["name"] for f in fields], dest,
                             progress, check_cancelled)
            method = "query"
    except SalesforceApiError as exc:
        raise ExtractionError(
            f"Salesforce refused to return {label} records to the Run As user: "
            f"{exc.message}"
        ) from None
    except httpx.HTTPError:
        raise ExtractionError(
            f"The connection to Salesforce dropped while downloading {label}. "
            "Start the assessment again."
        ) from None

    if records == 0:
        raise ExtractionError(f"{label} has no records to assess.")

    return {
        "type": "salesforce",
        "object": object_name,
        "label": label,
        "records": records,
        "method": method,
        "fields": field_metadata(fields),
    }


# --------------------------------------------------------------------------
# Bulk API 2.0
# --------------------------------------------------------------------------
def _bulk(
    client: SalesforceClient,
    soql: str,
    dest: Path,
    progress: ProgressFn,
    check_cancelled: CheckCancelledFn,
) -> int:
    try:
        job_id = client.bulk_create(soql)
    except SalesforceApiError as exc:
        if exc.status in (400, 404):
            raise _BulkUnsupported(exc.message) from None
        raise

    finished = False
    try:
        total = _await_job(client, job_id, progress, check_cancelled)
        written = 0
        locator = None
        first = True
        with dest.open("wb") as out:
            while True:
                check_cancelled()
                body, locator, count = client.bulk_results(
                    job_id, locator, config.SF_BULK_PAGE_RECORDS
                )
                if not first:
                    body = _drop_header(body)
                if body and not body.endswith(b"\n"):
                    body += b"\n"
                out.write(body)
                first = False
                written += count or 0
                if total:
                    progress(
                        min(written / total, 1.0),
                        f"Downloading from Salesforce: {min(written, total):,} of "
                        f"{total:,} records",
                    )
                if not locator:
                    break
        finished = True
        return total or written
    finally:
        if not finished:
            client.bulk_abort(job_id)


def _await_job(
    client: SalesforceClient,
    job_id: str,
    progress: ProgressFn,
    check_cancelled: CheckCancelledFn,
) -> int:
    delay = config.SF_BULK_POLL_START
    while True:
        check_cancelled()
        status = client.bulk_status(job_id)
        state = status.get("state")
        processed = int(status.get("numberRecordsProcessed") or 0)
        if state == "JobComplete":
            return processed
        if state in ("Failed", "Aborted"):
            message = status.get("errorMessage") or "Salesforce stopped the export."
            if "not supported" in message.lower():
                raise _BulkUnsupported(message)
            raise ExtractionError(f"Salesforce couldn't export the records: {message}")
        progress(
            0.0,
            f"Salesforce is preparing the export: {processed:,} records so far"
            if processed else "Waiting for Salesforce to prepare the export",
        )
        _sleep(delay)
        delay = min(delay * 1.5, config.SF_BULK_POLL_MAX)


def _drop_header(body: bytes) -> bytes:
    """Every results page repeats the header row; keep only the first.

    Safe to cut at the first newline: the header holds field API names,
    which can contain neither quotes nor newlines.
    """
    cut = body.find(b"\n")
    return body[cut + 1:] if cut >= 0 else b""


# --------------------------------------------------------------------------
# Query API fallback
# --------------------------------------------------------------------------
def _query(
    client: SalesforceClient,
    soql: str,
    names: list[str],
    dest: Path,
    progress: ProgressFn,
    check_cancelled: CheckCancelledFn,
) -> int:
    written = 0
    with dest.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(names)
        for page in client.query_pages(soql):
            check_cancelled()
            total = int(page.get("totalSize") or 0)
            for record in page.get("records", []):
                writer.writerow([_cell(record.get(name)) for name in names])
            written += len(page.get("records", []))
            if total:
                progress(
                    min(written / total, 1.0),
                    f"Downloading from Salesforce: {written:,} of {total:,} records",
                )
    return written


def _cell(value) -> str:
    """One JSON value as the Bulk API would have written it in CSV."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)
