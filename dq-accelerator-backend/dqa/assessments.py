"""Multi-object assessments (API contract revision 5).

An assessment groups one run per selected object and carries the overall
result across them. It knows nothing about any particular external system:
a system hands it an AssessmentPlan (dqa/connectors/base.py), and from then
on every object is an ordinary run going through the ordinary pipeline.

Status is never stored. It is derived from the object runs every time it is
read, so an assessment can't disagree with its own objects. The overall
result is computed once all objects have finished, and again whenever one
is re-scored.
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime
from typing import Any, Optional

from . import config
from .connectors.base import AssessmentPlan
from .models import RuleResult
from .scoring.aggregate import aggregate, band
from .store import artifacts, registry

log = logging.getLogger("dqa.assessments")

# Downloads still running per assessment, and how to release the system's
# credentials when the count reaches zero. In memory: after a restart there
# are no downloads left and no credentials to release.
_LOCK = threading.Lock()
_PENDING: dict[str, int] = {}
_PLANS: dict[str, AssessmentPlan] = {}

_TERMINAL = {"completed", "failed"}


def new_id() -> str:
    return f"asm_{uuid.uuid4().hex[:12]}"


def _new_run_id() -> str:
    return f"run_{datetime.now():%y%m%d}_{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------
def derive_status(statuses: list[str]) -> str:
    """The assessment's status from its objects' statuses. See the contract."""
    if not statuses or "processing" in statuses:
        return "processing"
    if "awaiting_cdes" in statuses:
        return "awaiting_cdes"
    if "completed" in statuses:
        return "completed"
    return "failed"


# --------------------------------------------------------------------------
# Starting
# --------------------------------------------------------------------------
def start(assessment_id: str, plan: AssessmentPlan) -> dict:
    """Create the object runs and queue every download."""
    from . import runner

    registry.create_assessment(assessment_id, plan.name, plan.source, len(plan.objects))
    org = plan.source.get("orgName") or plan.name

    runs = []
    for obj in plan.objects:
        run_id = _new_run_id()
        file_label = f"{org} · {obj.label}"
        artifacts.ensure(run_id)
        registry.create(run_id, file_label, assessment_id=assessment_id,
                        object_name=obj.name, object_label=obj.label)
        artifacts.write_meta(run_id, {
            "id": run_id, "file": file_label, "status": "processing",
            "stage": config.STAGES[0], "stageIndex": 0,
            "stageCount": len(config.STAGES), "progress": 0.0,
            "stageDetail": "Queued",
        })
        runs.append({"id": run_id, "object": obj.name, "label": obj.label,
                     "file": file_label, "status": "processing"})

    with _LOCK:
        _PENDING[assessment_id] = len(runs)
        _PLANS[assessment_id] = plan

    for run in runs:
        runner.submit_extraction(
            run["id"], run["file"], plan.extractor(run["object"]),
            on_extracted=lambda aid=assessment_id: _download_finished(aid),
        )

    return {
        "id": assessment_id,
        "name": plan.name,
        "status": "processing",
        "runs": [{k: v for k, v in run.items() if k != "file"} for run in runs],
    }


def _download_finished(assessment_id: str) -> None:
    """One object's download ended. After the last, release the credentials."""
    with _LOCK:
        remaining = _PENDING.get(assessment_id, 0) - 1
        if remaining > 0:
            _PENDING[assessment_id] = remaining
            return
        _PENDING.pop(assessment_id, None)
        plan = _PLANS.pop(assessment_id, None)
    if plan is not None:
        _close_plan(plan, "extracted")


def _close_plan(plan: AssessmentPlan, reason: str) -> None:
    try:
        plan.close(reason)
    except Exception:  # noqa: BLE001
        log.exception("closing the source connection failed")


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------
def _run_entry(record: dict) -> dict:
    """An object run as it appears inside the assessment: GET /runs/{id}'s
    shape for its status, without `file`, with `object` and `label`."""
    from .api.views import run_view

    view = run_view(record) or {
        "id": record["id"], "status": "failed",
        "error": "This object's results are missing.",
    }
    view.pop("file", None)
    view.pop("assessmentId", None)
    view.pop("source", None)
    return {"id": view.pop("id"), "object": record["object_name"],
            "label": record["object_label"], **view}


def _phase_fraction(entry: dict) -> float:
    """How far one object is through whichever half of the pipeline it's in."""
    if entry["status"] != "processing":
        return 1.0
    index = entry.get("stageIndex", 0)
    within = index if index < 2 else index - 2
    return min((within + entry.get("progress", 0.0)) / 2, 1.0)


def detail(assessment_id: str) -> Optional[dict]:
    """The body of GET /assessments/{id}, or None if it doesn't exist."""
    record = registry.get_assessment(assessment_id)
    if record is None:
        return None
    run_records = registry.assessment_runs(assessment_id)
    entries = [_run_entry(r) for r in run_records]
    status = derive_status([r["status"] for r in run_records])

    body: dict[str, Any] = {
        "id": assessment_id,
        "name": record["name"],
        "status": status,
        "source": record["source"],
    }
    if status == "processing":
        body["progress"] = round(
            sum(_phase_fraction(e) for e in entries) / max(len(entries), 1), 3
        )
    if status == "completed":
        results = artifacts.read_assessment_results(assessment_id) or compute(assessment_id)
        body.update({
            "overall": results["overall"],
            "records": results["records"],
            "objects": len(run_records),
            "partial": results["partial"],
            "scores": results["scores"],
            "notAssessed": results["notAssessed"],
        })
    body["runs"] = entries
    return body


def listing() -> list[dict]:
    """The body of GET /assessments."""
    out = []
    for record in registry.list_assessments():
        status = derive_status(record["statuses"])
        item: dict[str, Any] = {
            "id": record["id"], "name": record["name"],
            "status": status, "objects": record["objects"],
        }
        if status == "completed" and record["overall"] is not None:
            item["overall"] = record["overall"]
        out.append(item)
    return out


# --------------------------------------------------------------------------
# The overall result
# --------------------------------------------------------------------------
def compute(assessment_id: str) -> dict:
    """Pool every completed object's rules and score them as one dataset.

    The same severity-weighted formula a single run uses, so an object
    weighs in proportion to how much of it was checked. Stored, and returned.
    """
    run_records = registry.assessment_runs(assessment_id)
    pooled: dict[str, RuleResult] = {}
    records = 0
    completed = 0
    for run in run_records:
        if run["status"] != "completed":
            continue
        results = artifacts.read_results(run["id"])
        if not results:
            continue
        completed += 1
        records += int(results.get("records") or 0)
        for rule_id, rule in results.get("rules", {}).items():
            # Rule ids repeat across objects; the run id keeps them apart.
            pooled[f"{run['id']}:{rule_id}"] = RuleResult(
                rule_id=rule_id,
                rule_name=rule.get("rule_name", rule_id),
                dimension=rule["dimension"],
                severity=rule.get("severity", "medium"),
                column=rule.get("column"),
                evaluated=int(rule.get("evaluated", 0)),
                failed=int(rule.get("failed", 0)),
            )

    scores, overall, not_assessed = aggregate(pooled, {})
    # aggregate() words its reason for a single file; this is many objects.
    not_assessed = {
        dimension: f"No selected object could be assessed for {dimension}."
        for dimension in not_assessed
    }
    failures = {d: 0 for d in config.DIMENSIONS}
    for rule in pooled.values():
        if rule.dimension in failures:
            failures[rule.dimension] += rule.failed

    payload = {
        "assessmentId": assessment_id,
        "overall": overall,
        "records": records,
        "objectsCompleted": completed,
        "partial": completed < len(run_records),
        "scores": scores,
        "notAssessed": not_assessed,
        "bands": {d: band(s) for d, s in scores.items()},
        "failures": failures,
    }
    artifacts.write_assessment_results(assessment_id, payload)
    artifacts.clear_assessment_reports(assessment_id)
    registry.set_assessment_overall(assessment_id, overall)
    return payload


def run_finished(run_id: str) -> None:
    """Called by the runner when any run completes or fails."""
    record = registry.get(run_id)
    if not record or not record.get("assessment_id"):
        return
    assessment_id = record["assessment_id"]
    statuses = [r["status"] for r in registry.assessment_runs(assessment_id)]
    if derive_status(statuses) == "completed":
        compute(assessment_id)
    elif derive_status(statuses) == "failed":
        registry.set_assessment_overall(assessment_id, None)


# --------------------------------------------------------------------------
# Confirming CDEs
# --------------------------------------------------------------------------
class RequestError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def confirm_cdes(assessment_id: str, selections: dict[str, list[str]]) -> dict:
    """Validate every object's selection, then score them. All or nothing:
    a request with one bad list starts nothing."""
    from .runner import resubmit_with_cdes

    if registry.get_assessment(assessment_id) is None:
        raise RequestError(404, "That assessment could not be found.")
    run_records = registry.assessment_runs(assessment_id)
    status = derive_status([r["status"] for r in run_records])
    if status not in ("awaiting_cdes", "completed"):
        raise RequestError(
            409,
            "The columns can only be set once every object has finished profiling."
            if status == "processing" else
            "No object in this assessment could be assessed, so there is nothing to score.",
        )

    by_object = {r["object_name"]: r for r in run_records}
    unknown = [name for name in selections if name not in by_object]
    if unknown:
        raise RequestError(400, f"Not in this assessment: {', '.join(unknown)}.")

    if status == "awaiting_cdes":
        missing = [r["object_label"] for r in run_records
                   if r["status"] == "awaiting_cdes" and r["object_name"] not in selections]
        if missing:
            raise RequestError(
                400, f"Confirm the columns for every object. Missing: {', '.join(missing)}."
            )
    if not selections:
        raise RequestError(400, "Choose at least one object to re-score.")

    for name, columns in selections.items():
        run = by_object[name]
        label = run["object_label"]
        if run["status"] not in ("awaiting_cdes", "completed"):
            raise RequestError(400, f"{label} isn't ready to score ({run['status']}).")
        if not columns:
            raise RequestError(400, f"Choose at least one column for {label}.")
        profile = artifacts.read_profile(run["id"]) or {}
        known = {c.get("name") for c in profile.get("columns", [])}
        bad = [c for c in columns if c not in known]
        if bad:
            raise RequestError(400, f"These columns are not in {label}: {', '.join(bad)}.")
        if not artifacts.source_path(run["id"]).exists():
            raise RequestError(
                409, f"The downloaded data for {label} is no longer available. "
                     "Start a new assessment.",
            )

    for name, columns in selections.items():
        run = by_object[name]
        resubmit_with_cdes(run["id"], columns, run["file"])
    return {"id": assessment_id, "status": "processing"}


# --------------------------------------------------------------------------
# Deleting
# --------------------------------------------------------------------------
def delete(assessment_id: str) -> bool:
    from .runner import cancel

    if registry.get_assessment(assessment_id) is None:
        return False
    for run in registry.assessment_runs(assessment_id):
        if run["status"] in ("processing", "awaiting_cdes"):
            cancel(run["id"])
        artifacts.purge(run["id"])
    with _LOCK:
        _PENDING.pop(assessment_id, None)
        plan = _PLANS.pop(assessment_id, None)
    if plan is not None:
        _close_plan(plan, "disconnected")
    artifacts.purge_assessment(assessment_id)
    registry.delete_assessment(assessment_id)
    return True
