"""The /assessments endpoints: multi-object assessments (API contract rev 5).

Source-agnostic. The request names a `source.type`; the matching system
under dqa/connectors/ validates the selection and returns the plan, and
dqa/assessments.py runs it.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from .. import assessments, config, connectors
from ..connectors import ConnectorError
from ..report.renderer import render_assessment_report
from ..store import artifacts, registry
from . import downloads

router = APIRouter(tags=["assessments"])
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


class AssessmentRequest(BaseModel):
    source: dict
    objects: list[str]


class CdeSelections(BaseModel):
    objects: dict[str, list[str]]


@router.post("/assessments", status_code=201)
def create_assessment(payload: AssessmentRequest) -> Any:
    objects = [name.strip() for name in payload.objects]
    if not objects:
        return _error(400, "Choose at least one object to assess.")
    if len(objects) > config.ASSESSMENT_MAX_OBJECTS:
        return _error(
            400,
            f"Choose at most {config.ASSESSMENT_MAX_OBJECTS} objects; "
            f"{len(objects)} were selected.",
        )
    duplicates = sorted({n for n in objects if objects.count(n) > 1})
    if duplicates:
        return _error(400, f"Each object can be selected once: {', '.join(duplicates)}.")

    assessment_id = assessments.new_id()
    try:
        system = connectors.source_for(str(payload.source.get("type", "")))
        plan = system.plan(payload.source, objects, assessment_id)
    except ConnectorError as exc:
        return JSONResponse(status_code=exc.http_status, content=exc.to_api())
    return assessments.start(assessment_id, plan)


@router.get("/assessments")
def list_assessments() -> Any:
    return assessments.listing()


@router.get("/assessments/{assessment_id}")
def get_assessment(assessment_id: str) -> Any:
    body = assessments.detail(assessment_id)
    if body is None:
        return _error(404, "That assessment could not be found.")
    return body


@router.put("/assessments/{assessment_id}/cdes", status_code=202)
def confirm_cdes(assessment_id: str, payload: CdeSelections) -> Any:
    try:
        return assessments.confirm_cdes(assessment_id, payload.objects)
    except assessments.RequestError as exc:
        return _error(exc.status, exc.message)


@router.get("/assessments/{assessment_id}/report")
def get_report(assessment_id: str, type: str = Query("summary")) -> Any:
    if type not in ("summary", "in-depth"):
        return _error(400, "Report type must be 'summary' or 'in-depth'.")
    body = assessments.detail(assessment_id)
    if body is None:
        return _error(404, "That assessment could not be found.")
    if body["status"] != "completed":
        return _error(
            409, "There is nothing to report on yet — this assessment has not finished."
        )

    path = artifacts.assessment_report_path(assessment_id, type)
    if not path.exists():
        try:
            render_assessment_report(assessment_id, type)
        except Exception as exc:  # noqa: BLE001
            return _error(500, f"The report could not be generated: {exc}")
    if not path.exists():
        return _error(500, "The report could not be generated.")

    stem = _SAFE_NAME.sub("_", body["name"]).strip("_") or "assessment"
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{stem}-{type}.pdf",
        headers={"Content-Disposition": f'attachment; filename="{stem}-{type}.pdf"'},
    )


@router.get("/assessments/{assessment_id}/data")
def get_data(assessment_id: str) -> Any:
    """Every object's data, one CSV each, zipped (API contract revision 5)."""
    if registry.get_assessment(assessment_id) is None:
        return _error(404, "That assessment could not be found.")
    blocked = downloads.assessment_zip_blocked(assessment_id)
    if blocked:
        return _error(409, blocked)
    path = downloads.assessment_zip(assessment_id)
    if path is None:
        return _error(409, "No object in this assessment has data to download.")
    name = registry.get_assessment(assessment_id)["name"]
    filename = f"{downloads.safe_name(name, 'assessment')}-data.zip"
    return FileResponse(
        path,
        media_type="application/zip",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/assessments/{assessment_id}")
def delete_assessment(assessment_id: str) -> Any:
    if not assessments.delete(assessment_id):
        return _error(404, "That assessment could not be found.")
    return Response(status_code=204)
