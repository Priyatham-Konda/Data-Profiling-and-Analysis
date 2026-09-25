"""Run orchestration.

Four stages, reported to the frontend by name: Ingesting, Profiling,
Evaluating, Scoring. Two streaming passes over the file. Cancellation is
checked between chunks so a DELETE on a processing run actually stops the
work rather than leaving it running in the background.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Optional

from . import config
from .cde import detector
from .checks import integrity as integrity_checks
from .checks import timeliness as timeliness_checks
from .ingest.reader import CsvSource, IngestError, open_source
from .models import ColumnProfile, DatasetProfile, RunContext
from .profiling.stats import DatasetProfiler
from .rules.executor import RuleExecutor
from .rules.loader import expand, load_pack
from .scoring.aggregate import aggregate, band
from .store import artifacts, registry

log = logging.getLogger("dqa.runner")

_EXECUTOR = ThreadPoolExecutor(max_workers=config.MAX_WORKERS, thread_name_prefix="dqa")
_CANCEL: dict[str, threading.Event] = {}
_CANCEL_LOCK = threading.Lock()


class RunCancelled(Exception):
    pass


# --------------------------------------------------------------------------
# Cancellation
# --------------------------------------------------------------------------
def register_cancel(run_id: str) -> threading.Event:
    event = threading.Event()
    with _CANCEL_LOCK:
        _CANCEL[run_id] = event
    return event


def cancel(run_id: str) -> None:
    with _CANCEL_LOCK:
        event = _CANCEL.get(run_id)
    if event:
        event.set()


def _clear_cancel(run_id: str) -> None:
    with _CANCEL_LOCK:
        _CANCEL.pop(run_id, None)


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------
def _report(run_id: str, stage: str, progress: float) -> None:
    artifacts.update_meta(
        run_id,
        status="processing",
        stage=stage,
        stageIndex=config.STAGES.index(stage),
        stageCount=len(config.STAGES),
        progress=round(min(max(progress, 0.0), 1.0), 3),
    )


def _check_cancelled(ctx: RunContext) -> None:
    if ctx.cancelled():
        raise RunCancelled()


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def submit(run_id: str, source_file: str, original_filename: str) -> None:
    """Start a new run.

    Only the first half of the pipeline runs here. It stops after Profiling
    and waits for the detected critical data elements to be confirmed; the
    Evaluating and Scoring half is started by PUT /runs/{id}/cdes.
    """
    event = register_cancel(run_id)
    _EXECUTOR.submit(_execute, run_id, source_file, original_filename, event)


def _execute(
    run_id: str,
    source_file: str,
    original_filename: str,
    cancel_event: threading.Event,
) -> None:
    started = time.time()
    ctx = RunContext(
        run_id=run_id,
        source_path=source_file,
        original_filename=original_filename,
        cancel_event=cancel_event,
    )
    try:
        run_assessment(ctx, stop_after_profiling=True)
        log.info("run %s profiled in %.1fs, awaiting CDE confirmation",
                 run_id, time.time() - started)
    except RunCancelled:
        log.info("run %s cancelled", run_id)
    except IngestError as exc:
        _fail(run_id, str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("run %s failed", run_id)
        _fail(run_id, _friendly_error(exc))
    finally:
        _clear_cancel(run_id)


def _fail(run_id: str, message: str) -> None:
    artifacts.update_meta(run_id, status="failed", error=message)
    registry.set_status(run_id, "failed")


def _friendly_error(exc: Exception) -> str:
    """The `error` string is shown to a data analyst verbatim, not logged."""
    if isinstance(exc, MemoryError):
        return ("The file is too large to process in available memory. "
                "Try splitting it into smaller files.")
    if isinstance(exc, UnicodeDecodeError):
        return ("The file's character encoding could not be read. "
                "Re-export it as UTF-8 and try again.")
    return f"The assessment failed while processing this file: {exc}"


# --------------------------------------------------------------------------
# The pipeline
# --------------------------------------------------------------------------
@dataclass
class _ReadPlan:
    """How the file is read, decided once and shared by both halves."""
    source: CsvSource
    total_rows: int
    sample_every: int
    sampled: bool
    assessed_rows: int


def _sampling_plan(source: CsvSource) -> tuple[int, int, bool]:
    """(line-count estimate, sample_every, sampled) for this file.

    Deterministic in the file alone, so the second half recomputes exactly
    the sampling the first half used and evaluates the same rows it
    profiled.
    """
    total_rows = source.row_count()
    if total_rows > config.MAX_ROWS_FULL:
        every = max(2, math.ceil(total_rows / config.SAMPLE_TARGET_ROWS))
        return total_rows, every, True
    return total_rows, 1, False


def _decide_cdes(profile: DatasetProfile, cde_override: Optional[list[str]]) -> bool:
    """Detect CDEs, apply any override, and say whether it changed anything.

    detect() recomputes every decision from the stored statistics, so a
    profile carrying an earlier override is reset to pure detection before
    the new selection is applied.
    """
    detector.detect(profile)
    detected = {c.name for c in profile.cde_columns}
    if cde_override:
        unknown = detector.apply_override(profile, cde_override)
        if unknown:
            raise ValueError(f"Unknown column(s): {', '.join(unknown)}")

    # Confirming the detected set is not an override. Every run now passes
    # through PUT /cdes, so `bool(cde_override)` alone would mark every run
    # as overridden; comparing against what detection chose keeps the flag
    # meaning what the contract says it means.
    return bool(cde_override) and {c.name for c in profile.cde_columns} != detected


def _profile_from_dict(data: dict) -> DatasetProfile:
    """Rebuild the DatasetProfile that artifacts.write_profile stored.

    It was written with dataclasses.asdict, so every field round-trips.
    top_values is the one exception: JSON has no tuples, so its pairs come
    back as lists and are turned back into tuples here.
    """
    columns = []
    for raw in data.get("columns", []):
        fields = dict(raw)
        fields["top_values"] = [tuple(v) for v in fields.get("top_values", [])]
        columns.append(ColumnProfile(**fields))
    rest = {k: v for k, v in data.items() if k != "columns"}
    return DatasetProfile(columns=columns, **rest)


def run_assessment(
    ctx: RunContext,
    cde_override: Optional[list[str]] = None,
    stop_after_profiling: bool = False,
) -> dict:
    """Ingest and profile, then -- unless paused -- evaluate and score.

    `stop_after_profiling` is how a new upload runs only the first half and
    parks at `awaiting_cdes`. The second half is resume_from_profile.
    """
    plan, profile, cde_overridden = _ingest_and_profile(ctx, cde_override)
    if stop_after_profiling:
        return _await_cde_confirmation(ctx.run_id, profile, plan.total_rows)
    return _evaluate_and_score(ctx, plan, profile, cde_overridden)


def resume_from_profile(ctx: RunContext, cde_override: list[str]) -> dict:
    """The second half alone: Evaluating and Scoring from the stored profile.

    Neither re-parses nor re-profiles. That is the difference the frontend
    sees -- progress continues forward from Evaluating instead of jumping
    back to Ingesting -- and on a large file it skips a full pass.
    """
    run_id = ctx.run_id
    stored = artifacts.read_profile(run_id)
    if stored is None:
        raise ValueError("This assessment has no profile to resume from.")

    source = open_source(ctx.source_path)
    ctx.dialect = source.dialect
    _check_cancelled(ctx)

    profile = _profile_from_dict(stored)
    cde_overridden = _decide_cdes(profile, cde_override)
    ctx.profile = profile
    artifacts.write_profile(run_id, profile)

    line_estimate, sample_every, sampled = _sampling_plan(source)
    assessed_rows = profile.row_count
    plan = _ReadPlan(
        source=source,
        # Same correction the first half makes: the rows actually streamed
        # are the record count unless the file was sampled.
        total_rows=line_estimate if sampled else assessed_rows,
        sample_every=sample_every,
        sampled=sampled,
        assessed_rows=assessed_rows,
    )
    return _evaluate_and_score(ctx, plan, profile, cde_overridden)


def _ingest_and_profile(
    ctx: RunContext, cde_override: Optional[list[str]]
) -> tuple[_ReadPlan, DatasetProfile, bool]:
    """Stages 1 and 2. Writes profile.json, which the second half reads."""
    run_id = ctx.run_id

    # -- Stage 1: Ingesting -------------------------------------------------
    _report(run_id, "Ingesting", 0.0)
    source = open_source(ctx.source_path)
    ctx.dialect = source.dialect
    _check_cancelled(ctx)

    total_rows, sample_every, sampled = _sampling_plan(source)
    _report(run_id, "Ingesting", 1.0)

    # -- Stage 2: Profiling -------------------------------------------------
    _report(run_id, "Profiling", 0.0)
    profiler = DatasetProfiler(source.columns)
    seen = 0
    for chunk in source.iter_chunks(sample_every=sample_every):
        _check_cancelled(ctx)
        profiler.update(chunk.drop(columns=["__row__"], errors="ignore"))
        seen += len(chunk)
        _report(run_id, "Profiling", _fraction(seen, total_rows, sample_every))

    assessed_rows = seen
    # row_count() is a byte-line count, so it overstates any file whose
    # quoted fields contain newlines -- one Salesforce description field can
    # inflate it several times over. Now that the file has actually been
    # streamed, the rows pandas produced ARE the record count, so prefer that
    # for the number the client is shown. Sampling is the exception: there
    # `seen` is only the sample, and the line count stays the sole estimate
    # of the whole file available without a second pass.
    if not sampled:
        total_rows = assessed_rows

    profile: DatasetProfile = profiler.finalise(row_count=assessed_rows)
    profile.parse_warnings = source.parse_warnings
    profile.parse_warning_examples = source.parse_warning_examples
    profile.sampled = sampled
    profile.sampled_rows = assessed_rows if sampled else None

    cde_overridden = _decide_cdes(profile, cde_override)

    ctx.profile = profile
    artifacts.write_profile(run_id, profile)
    _report(run_id, "Profiling", 1.0)
    _check_cancelled(ctx)

    plan = _ReadPlan(source, total_rows, sample_every, sampled, assessed_rows)
    return plan, profile, cde_overridden


def _evaluate_and_score(
    ctx: RunContext,
    plan: _ReadPlan,
    profile: DatasetProfile,
    cde_overridden: bool,
) -> dict:
    """Stages 3 and 4. Identical whether reached straight through or resumed."""
    run_id = ctx.run_id
    source = plan.source
    total_rows = plan.total_rows
    sample_every = plan.sample_every
    sampled = plan.sampled
    assessed_rows = plan.assessed_rows

    # -- Stage 3: Evaluating ------------------------------------------------
    _report(run_id, "Evaluating", 0.0)

    not_assessed: dict[str, str] = {}
    assessable, reason = timeliness_checks.is_assessable(profile)
    if not assessable:
        not_assessed["timeliness"] = reason

    # Integrity infers relationships from the file rather than being told
    # them, so a file too small or too narrow to infer from is reported as
    # not assessed rather than scored on nothing.
    assessable, reason = integrity_checks.is_assessable(profile)
    if not assessable:
        not_assessed["integrity"] = reason

    pack = load_pack()
    rules = expand(pack, profile)
    rules = [r for r in rules if r.dimension not in not_assessed]

    rule_ctx: dict = {"profile": profile, "run_id": run_id}
    executor = RuleExecutor(rules, rule_ctx)

    seen = 0
    for chunk in source.iter_chunks(sample_every=sample_every):
        _check_cancelled(ctx)
        # Cross-field checks need the whole row, not just their own column.
        rule_ctx["_chunk"] = chunk
        executor.process_chunk(chunk)
        seen += len(chunk)
        _report(run_id, "Evaluating", _fraction(seen, total_rows, sample_every))

    rule_ctx.pop("_chunk", None)
    results, violations = executor.finalise()
    _report(run_id, "Evaluating", 1.0)
    _check_cancelled(ctx)

    # -- Stage 4: Scoring ---------------------------------------------------
    _report(run_id, "Scoring", 0.0)

    # A resumed run may be re-scoring a finished one; clear its previous
    # examples first, or a rule that no longer fails would keep serving
    # violations from the last selection.
    artifacts.clear_violations(run_id)
    for rule_id, rule_violations in violations.items():
        result = results.get(rule_id)
        if result and rule_violations:
            artifacts.write_violations(
                run_id, result.dimension, rule_id, rule_violations
            )

    scores, overall, not_assessed = aggregate(results, not_assessed)

    payload = {
        "runId": run_id,
        "file": ctx.original_filename,
        "records": total_rows,
        "columns": profile.column_count,
        "cdes": len(profile.cde_columns),
        "cdeColumns": [c.name for c in profile.cde_columns],
        "cdeOverridden": cde_overridden,
        "sampled": sampled,
        "sampledRows": assessed_rows if sampled else None,
        "parseWarnings": profile.parse_warnings,
        "scores": scores,
        "overall": overall,
        "notAssessed": not_assessed,
        "bands": {d: band(s) for d, s in scores.items()},
        "rules": {rid: asdict(r) | {"passRate": r.pass_rate}
                  for rid, r in results.items()},
        "ruleErrors": executor.errors,
        "dialect": asdict(ctx.dialect) if ctx.dialect else None,
    }
    artifacts.write_results(run_id, payload)

    # A re-score invalidates any report rendered from the previous scores.
    artifacts.clear_reports(run_id)

    artifacts.update_meta(
        run_id,
        status="completed",
        stage="Scoring",
        stageIndex=len(config.STAGES) - 1,
        stageCount=len(config.STAGES),
        progress=1.0,
        error=None,
    )
    registry.set_status(run_id, "completed", overall)
    return payload


def _await_cde_confirmation(run_id: str, profile: DatasetProfile, records: int) -> dict:
    """Park the run after Profiling until the columns are confirmed.

    Every number reported here comes from Profiling, which is why the state
    can exist at all without Evaluating having run. No scores and no overall
    are written, because nothing has been evaluated.
    """
    payload = {
        "records": records,
        "columns": profile.column_count,
        "cdes": len(profile.cde_columns),
        "cdeColumns": [c.name for c in profile.cde_columns],
    }
    artifacts.update_meta(
        run_id,
        status="awaiting_cdes",
        stage="Profiling",
        stageIndex=config.STAGES.index("Profiling"),
        stageCount=len(config.STAGES),
        progress=1.0,
        error=None,
        **payload,
    )
    registry.set_status(run_id, "awaiting_cdes")
    return payload


def _fraction(seen: int, total_rows: int, sample_every: int) -> float:
    if total_rows <= 0:
        return 0.5
    expected = total_rows / sample_every
    if expected <= 0:
        return 1.0
    return min(seen / expected, 1.0)


# --------------------------------------------------------------------------
# CDE re-evaluation
# --------------------------------------------------------------------------
def resubmit_with_cdes(run_id: str, columns: list[str], original_filename: str) -> None:
    """Run Evaluating and Scoring against a confirmed CDE set.

    Used both to start scoring for the first time on a run parked at
    `awaiting_cdes`, and to re-score a completed run against a different
    selection. Either way it resumes from the stored profile: the source
    file is retained for the life of the run, and nothing is re-profiled.
    """
    event = register_cancel(run_id)
    ctx = RunContext(
        run_id=run_id,
        source_path=str(artifacts.source_path(run_id)),
        original_filename=original_filename,
        cancel_event=event,
    )

    def task() -> None:
        try:
            resume_from_profile(ctx, columns)
        except RunCancelled:
            log.info("re-run %s cancelled", run_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("re-run %s failed", run_id)
            _fail(run_id, _friendly_error(exc))
        finally:
            _clear_cancel(run_id)

    registry.set_status(run_id, "processing")
    artifacts.update_meta(run_id, status="processing", progress=0.0,
                          stage="Evaluating", stageIndex=2,
                          stageCount=len(config.STAGES))
    _EXECUTOR.submit(task)
