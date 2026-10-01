"""Response shapes shared by more than one endpoint.

A run's shape is returned both by GET /runs/{id} and, for each object, inside
GET /assessments/{id}. Building it in one place is what keeps the two
identical, which the contract promises.
"""
from __future__ import annotations

from typing import Any, Optional

from .. import config
from ..store import artifacts, registry


def run_view(record: dict) -> Optional[dict[str, Any]]:
    """A run's GET /runs/{id} body for its status.

    None when a completed run's results are missing, which the caller turns
    into an error. Upload runs come out exactly as in revision 4; object runs
    of an assessment additionally carry `assessmentId` and `source`.
    """
    run_id = record["id"]
    meta = artifacts.read_meta(run_id) or {}
    status = record["status"]

    if status == "processing":
        body: dict[str, Any] = {
            "id": run_id,
            "file": record["file"],
            "status": "processing",
            "stage": meta.get("stage", config.STAGES[0]),
            "stageIndex": meta.get("stageIndex", 0),
            "stageCount": len(config.STAGES),
            "progress": meta.get("progress", 0.0),
        }
        if meta.get("stageDetail"):
            body["stageDetail"] = meta["stageDetail"]

    elif status == "awaiting_cdes":
        # No scores and no overall: Evaluating has not run. The three counts
        # below were all established during Profiling, which is what lets
        # this state exist at all.
        body = {
            "id": run_id,
            "file": record["file"],
            "status": "awaiting_cdes",
            "records": meta.get("records", 0),
            "columns": meta.get("columns", 0),
            "cdes": meta.get("cdes", 0),
        }

    elif status == "failed":
        body = {
            "id": run_id,
            "file": record["file"],
            "status": "failed",
            "error": meta.get("error", "The assessment failed for an unknown reason."),
        }

    else:
        results = artifacts.read_results(run_id)
        if results is None:
            return None
        body = {
            "id": run_id,
            "file": record["file"],
            "status": "completed",
            "overall": results.get("overall"),
            "records": results.get("records", 0),
            "cdes": results.get("cdes", 0),
            "scores": results.get("scores", {}),
            # Additive fields, see API_CONTRACT.md
            "columns": results.get("columns"),
            "sampled": results.get("sampled", False),
            "sampledRows": results.get("sampledRows"),
            "notAssessed": results.get("notAssessed", {}),
            "parseWarnings": results.get("parseWarnings", 0),
            "cdeOverridden": results.get("cdeOverridden", False),
        }

    if record.get("assessment_id"):
        assessment = registry.get_assessment(record["assessment_id"]) or {}
        source = assessment.get("source", {})
        body["assessmentId"] = record["assessment_id"]
        body["source"] = {
            "type": source.get("type"),
            "orgName": source.get("orgName"),
            "object": record.get("object_name"),
            "label": record.get("object_label"),
        }
    return body
