"""Per-run artifact storage.

Everything a run produces lives under one directory, which makes deletion a
single rmtree and satisfies the "run it, then destroy it" requirement without
hunting for stray files.

    data/runs/{run_id}/
        source.csv
        meta.json
        profile.json
        results.json
        violations/{dimension}/{rule_id}.jsonl
        report-summary.pdf
        report-in-depth.pdf
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from .. import config
from ..models import DatasetProfile, Violation


def run_dir(run_id: str) -> Path:
    return config.RUNS_DIR / run_id


def ensure(run_id: str) -> Path:
    path = run_dir(run_id)
    (path / "violations").mkdir(parents=True, exist_ok=True)
    return path


def source_path(run_id: str) -> Path:
    return run_dir(run_id) / "source.csv"


def report_path(run_id: str, report_type: str) -> Path:
    return run_dir(run_id) / f"report-{report_type}.pdf"


# --------------------------------------------------------------------------
# JSON documents
# --------------------------------------------------------------------------
# os.replace is atomic on POSIX, but on Windows it raises PermissionError
# while any process holds the destination open. GET /runs and GET /runs/{id}
# are polled every few seconds per open browser tab, so a reader is very
# often holding meta.json at the moment the worker rewrites it. Unretried,
# the worker's write raises and the run is marked failed -- because somebody
# was watching it. A short backoff outlasts a reader that only opens the
# file to parse a few hundred bytes.
_REPLACE_ATTEMPTS = 8
_REPLACE_BACKOFF = 0.02


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique temp name per writer: the worker and an API request can both
    # write meta.json, and a shared temp name lets one delete the other's
    # file out from under it.
    tmp = path.with_suffix(
        f"{path.suffix}.{os.getpid()}-{threading.get_ident()}.tmp"
    )
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            tmp.replace(path)  # atomic: a poller never reads a half-written file
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(_REPLACE_BACKOFF * (attempt + 1))


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_meta(run_id: str, meta: dict) -> None:
    _write_json(run_dir(run_id) / "meta.json", meta)


def read_meta(run_id: str) -> Optional[dict]:
    return _read_json(run_dir(run_id) / "meta.json")


def update_meta(run_id: str, **fields: Any) -> dict:
    meta = read_meta(run_id) or {}
    meta.update(fields)
    write_meta(run_id, meta)
    return meta


def write_profile(run_id: str, profile: DatasetProfile) -> None:
    _write_json(run_dir(run_id) / "profile.json", asdict(profile))


def read_profile(run_id: str) -> Optional[dict]:
    return _read_json(run_dir(run_id) / "profile.json")


def write_results(run_id: str, results: dict) -> None:
    _write_json(run_dir(run_id) / "results.json", results)


def read_results(run_id: str) -> Optional[dict]:
    return _read_json(run_dir(run_id) / "results.json")


# --------------------------------------------------------------------------
# Violations
# --------------------------------------------------------------------------
def violations_path(run_id: str, dimension: str, rule_id: str) -> Path:
    safe = rule_id.replace("/", "_").replace("\\", "_")
    return run_dir(run_id) / "violations" / dimension / f"{safe}.jsonl"


def write_violations(
    run_id: str, dimension: str, rule_id: str, violations: list[Violation]
) -> None:
    """Written as the rule executes, never regenerated on request."""
    path = violations_path(run_id, dimension, rule_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for violation in violations:
            fh.write(json.dumps(violation.to_api()) + "\n")


def read_violations(
    run_id: str, dimension: str, rule_id: str, limit: int = 10
) -> list[dict]:
    path = violations_path(run_id, dimension, rule_id)
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if len(out) >= limit:
                break
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


# --------------------------------------------------------------------------
# Deletion
# --------------------------------------------------------------------------
def purge(run_id: str) -> bool:
    """Remove every artifact of a run, including the uploaded source file."""
    path = run_dir(run_id)
    if not path.exists():
        return False
    shutil.rmtree(path, ignore_errors=True)
    return not path.exists()
