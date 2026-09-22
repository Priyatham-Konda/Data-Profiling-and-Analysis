"""HTTP API.

Implements API_CONTRACT.md revision 2 exactly. Every error response carries
`{"error": "..."}` with a message written for a person, because the frontend
shows that string verbatim.
"""
from __future__ import annotations

import logging
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from .. import config
from ..cde import detector
from ..models import DatasetProfile
from ..report.renderer import render_report
from ..runner import cancel, resubmit_with_cdes, submit
from ..store import artifacts, registry

log = logging.getLogger("dqa.api")
router = APIRouter()

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


def _new_run_id() -> str:
    return f"run_{datetime.now():%y%m%d}_{uuid.uuid4().hex[:8]}"


# ==========================================================================
# POST /runs
# ==========================================================================
@router.post("/runs", status_code=201)
async def create_run(request: Request, file: UploadFile = File(...)) -> Any:
    filename = Path(file.filename or "upload.csv").name
    extension = Path(filename).suffix.lower()

    if extension not in config.ACCEPTED_EXTENSIONS:
        return _error(
            415,
            f"Only CSV files are accepted. This file appears to be "
            f"{extension or 'an unknown type'} — export it as CSV and try again.",
        )

    if file.content_type and file.content_type not in config.ACCEPTED_CONTENT_TYPES:
        return _error(
            415,
            f"This file was uploaded as {file.content_type}, which is not a "
            f"CSV type. Export it as CSV and try again.",
        )

    run_id = _new_run_id()
    run_path = artifacts.ensure(run_id)
    destination = artifacts.source_path(run_id)

    # Stream to disk with a running size check: never hold the upload in
    # memory, and stop the moment the cap is exceeded.
    written = 0
    try:
        with destination.open("wb") as out:
            while True:
                block = await file.read(1024 * 1024)
                if not block:
                    break
                written += len(block)
                if written > config.MAX_UPLOAD_BYTES:
                    out.close()
                    shutil.rmtree(run_path, ignore_errors=True)
                    limit_mb = config.MAX_UPLOAD_BYTES // (1024 * 1024)
                    return _error(
                        413,
                        f"This file is larger than the {limit_mb} MB limit. "
                        f"Split the export into smaller files, or export "
                        f"fewer columns.",
                    )
                out.write(block)
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(run_path, ignore_errors=True)
        log.exception("upload failed")
        return _error(500, f"The upload could not be saved: {exc}")

    if written == 0:
        shutil.rmtree(run_path, ignore_errors=True)
        return _error(400, "The uploaded file is empty.")

    registry.create(run_id, filename)
    artifacts.write_meta(
        run_id,
        {
            "id": run_id,
            "file": filename,
            "status": "processing",
            "stage": config.STAGES[0],
            "stageIndex": 0,
            "stageCount": len(config.STAGES),
            "progress": 0.0,
            "bytes": written,
        },
    )
    submit(run_id, str(destination), filename)

    return {"id": run_id, "file": filename, "status": "processing"}


# ==========================================================================
# GET /runs
# ==========================================================================
@router.get("/runs")
async def list_runs() -> Any:
    return registry.list_runs()


# ==========================================================================
# GET /runs/{id}
# ==========================================================================
@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> Any:
    record = registry.get(run_id)
    if record is None:
        return _error(404, "That assessment could not be found.")

    meta = artifacts.read_meta(run_id) or {}
    status = record["status"]

    if status == "processing":
        return {
            "id": run_id,
            "file": record["file"],
            "status": "processing",
            "stage": meta.get("stage", config.STAGES[0]),
            "stageIndex": meta.get("stageIndex", 0),
            "stageCount": len(config.STAGES),
            "progress": meta.get("progress", 0.0),
        }

    if status == "failed":
        return {
            "id": run_id,
            "file": record["file"],
            "status": "failed",
            "error": meta.get("error", "The assessment failed for an unknown reason."),
        }

    results = artifacts.read_results(run_id)
    if results is None:
        return _error(500, "The assessment finished but its results are missing.")

    return {
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


# ==========================================================================
# GET /runs/{id}/profile
# ==========================================================================
@router.get("/runs/{run_id}/profile")
async def get_profile(run_id: str) -> Any:
    if registry.get(run_id) is None:
        return _error(404, "That assessment could not be found.")

    profile = artifacts.read_profile(run_id)
    if profile is None:
        return _error(409, "This assessment has not finished profiling yet.")

    columns = []
    for raw in profile.get("columns", []):
        total = raw.get("total", 0) or 0
        populated = max(total - raw.get("nulls", 0) - raw.get("blanks", 0), 0)
        fill_rate = populated / total if total else 0.0
        distinct = raw.get("distinct_estimate", 0)
        distinct_ratio = min(distinct / populated, 1.0) if populated else 0.0
        columns.append(
            {
                "name": raw.get("name"),
                "inferredType": raw.get("inferred_type"),
                "semanticType": raw.get("semantic_type"),
                "fillRate": round(fill_rate, 4),
                "distinctRatio": round(distinct_ratio, 4),
                "sampleValues": raw.get("sample_values", []),
                "isCde": raw.get("is_cde", False),
                "cdeScore": round(raw.get("cde_score", 0.0), 3),
                "cdeReason": "; ".join(raw.get("cde_reasons", []))
                             or "No strong signal either way",
            }
        )
    return {"columns": columns}


# ==========================================================================
# PUT /runs/{id}/cdes
# ==========================================================================
class CdeOverride(BaseModel):
    columns: list[str] = Field(..., min_length=1)


@router.put("/runs/{run_id}/cdes", status_code=202)
async def override_cdes(run_id: str, payload: CdeOverride) -> Any:
    record = registry.get(run_id)
    if record is None:
        return _error(404, "That assessment could not be found.")
    if record["status"] != "completed":
        return _error(
            409,
            "The columns can only be changed once the assessment has finished.",
        )

    profile = artifacts.read_profile(run_id)
    if profile is None:
        return _error(409, "This assessment has no profile to work from.")

    known = {c.get("name") for c in profile.get("columns", [])}
    unknown = [c for c in payload.columns if c not in known]
    if unknown:
        return _error(
            400,
            f"These columns are not in the file: {', '.join(unknown)}.",
        )

    if not artifacts.source_path(run_id).exists():
        return _error(
            409,
            "The source file for this assessment is no longer available, so it "
            "cannot be re-assessed. Upload the file again.",
        )

    resubmit_with_cdes(run_id, payload.columns, record["file"])
    return {"id": run_id, "status": "processing"}


# ==========================================================================
# GET /runs/{id}/dimensions/{dim}
# ==========================================================================
@router.get("/runs/{run_id}/dimensions/{dimension}")
async def get_dimension(run_id: str, dimension: str) -> Any:
    if dimension not in config.DIMENSIONS:
        return _error(
            404,
            f"'{dimension}' is not one of the assessed dimensions.",
        )

    results = artifacts.read_results(run_id)
    if results is None:
        if registry.get(run_id) is None:
            return _error(404, "That assessment could not be found.")
        return _error(409, "This assessment has not finished yet.")

    not_assessed = results.get("notAssessed", {})
    if dimension in not_assessed:
        return {
            "key": dimension,
            "score": None,
            "rules": [],
            "notAssessedReason": not_assessed[dimension],
        }

    rules = [
        r for r in results.get("rules", {}).values()
        if r.get("dimension") == dimension
    ]
    rules.sort(key=lambda r: (r.get("passRate", 1.0), r.get("rule_id", "")))

    return {
        "key": dimension,
        "score": results.get("scores", {}).get(dimension),
        "rules": [
            {
                "id": r.get("rule_id"),
                "name": r.get("rule_name"),
                "passRate": round(r.get("passRate", 1.0), 4),
                "column": r.get("column"),
                "severity": r.get("severity"),
                "evaluated": r.get("evaluated", 0),
                "failed": r.get("failed", 0),
            }
            for r in rules
        ],
    }


# ==========================================================================
# GET /runs/{id}/dimensions/{dim}/rules/{ruleId}/examples
# ==========================================================================
@router.get("/runs/{run_id}/dimensions/{dimension}/rules/{rule_id}/examples")
async def get_examples(
    run_id: str,
    dimension: str,
    rule_id: str,
    limit: int = Query(10, ge=1, le=config.EXAMPLE_CAP),
) -> Any:
    results = artifacts.read_results(run_id)
    if results is None:
        if registry.get(run_id) is None:
            return _error(404, "That assessment could not be found.")
        return _error(409, "This assessment has not finished yet.")

    rule = results.get("rules", {}).get(rule_id)
    if rule is None or rule.get("dimension") != dimension:
        return _error(
            404,
            f"Rule '{rule_id}' was not evaluated for {dimension} in this assessment.",
        )

    examples = artifacts.read_violations(run_id, dimension, rule_id, limit=limit)
    return {
        "ruleId": rule_id,
        "ruleName": rule.get("rule_name"),
        "passRate": round(rule.get("passRate", 1.0), 4),
        "total": rule.get("failed", 0),
        "examples": examples,
    }


# ==========================================================================
# GET /runs/{id}/report
# ==========================================================================
@router.get("/runs/{run_id}/report")
async def get_report(run_id: str, type: str = Query("summary")) -> Any:
    if type not in ("summary", "in-depth"):
        return _error(400, "Report type must be either 'summary' or 'in-depth'.")

    record = registry.get(run_id)
    if record is None:
        return _error(404, "That assessment could not be found.")
    if record["status"] != "completed":
        return _error(
            409,
            "There is nothing to report on yet — this assessment has not finished.",
        )

    path = artifacts.report_path(run_id, type)
    if not path.exists():
        try:
            render_report(run_id, type)
        except Exception as exc:  # noqa: BLE001
            log.exception("report generation failed")
            return _error(500, f"The report could not be generated: {exc}")

    if not path.exists():
        return _error(500, "The report could not be generated.")

    stem = _SAFE_NAME.sub("_", Path(record["file"]).stem)
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{stem}-{type}.pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{stem}-{type}.pdf"'
        },
    )


# ==========================================================================
# DELETE /runs/{id}
# ==========================================================================
# Not declared as status_code=204 on the decorator: a 204 may carry no body,
# but this route also returns a 404 with an {"error": ...} body. Declaring
# 204 makes FastAPI reject any body at all, at app startup.
@router.delete("/runs/{run_id}")
async def delete_run(run_id: str) -> Any:
    record = registry.get(run_id)
    if record is None:
        return _error(404, "That assessment could not be found.")

    # Cancel first: the contract states that deleting a processing run
    # cancels the underlying job, and the UI warns the user of exactly that.
    if record["status"] == "processing":
        cancel(run_id)

    artifacts.purge(run_id)
    registry.delete(run_id)
    return Response(status_code=204)
