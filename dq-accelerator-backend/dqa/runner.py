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
from dataclasses import asdict
from typing import Optional

from . import config
from .cde import detector
from .checks import integrity as integrity_checks
from .checks import timeliness as timeliness_checks
from .ingest.reader import CsvSource, IngestError, open_source
from .models import DatasetProfile, RunContext
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
        run_assessment(ctx)
        log.info("run %s completed in %.1fs", run_id, time.time() - started)
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
def run_assessment(ctx: RunContext, cde_override: Optional[list[str]] = None) -> dict:
    run_id = ctx.run_id

    # -- Stage 1: Ingesting -------------------------------------------------
    _report(run_id, "Ingesting", 0.0)
    source = open_source(ctx.source_path)
    ctx.dialect = source.dialect
    _check_cancelled(ctx)

    total_rows = source.row_count()
    sample_every = 1
    sampled = False
    if total_rows > config.MAX_ROWS_FULL:
        sample_every = max(2, math.ceil(total_rows / config.SAMPLE_TARGET_ROWS))
        sampled = True
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

    detector.detect(profile)
    if cde_override:
        unknown = detector.apply_override(profile, cde_override)
        if unknown:
            raise ValueError(f"Unknown column(s): {', '.join(unknown)}")

    ctx.profile = profile
    artifacts.write_profile(run_id, profile)
    _report(run_id, "Profiling", 1.0)
    _check_cancelled(ctx)

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
        "cdeOverridden": bool(cde_override),
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
    """Re-run evaluation and scoring against a manual CDE set.

    The source file is retained for the life of the run, so this repeats the
    Evaluating and Scoring stages only.
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
            run_assessment(ctx, cde_override=columns)
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
