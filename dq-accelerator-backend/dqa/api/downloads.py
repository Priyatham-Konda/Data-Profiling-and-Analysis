"""The assessed data, as files: one CSV per run, or a ZIP of an assessment.

The file served is the run's source.csv -- exactly what profiling and
scoring read -- never a regenerated copy, so the download can't differ from
the data the scores describe.
"""
from __future__ import annotations

import re
import threading
import zipfile
from pathlib import Path
from typing import Iterator, Optional

from fastapi.responses import FileResponse, StreamingResponse

from ..store import artifacts, registry

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_BOM = b"\xef\xbb\xbf"
_CHUNK = 1 << 20

# One build at a time per assessment: two clicks shouldn't zip twice.
_ZIP_LOCKS: dict[str, threading.Lock] = {}
_ZIP_LOCKS_GUARD = threading.Lock()


def safe_name(text: str, fallback: str) -> str:
    return _SAFE_NAME.sub("_", text).strip("_") or fallback


def run_data_ready(run_id: str) -> bool:
    """Profiling has finished, so the run's data file is complete.

    A profile is only written after the whole file has been read, which is
    only after a download from an external system has finished. Checking for
    it -- rather than the status -- keeps the file available while a
    completed run is re-scored, and unavailable while it is still arriving.
    """
    return artifacts.has_profile(run_id) and artifacts.source_path(run_id).exists()


def _with_bom(path: Path) -> Iterator[bytes]:
    """The file's bytes, preceded by a UTF-8 byte-order mark unless it has one.

    Files downloaded from an external system are always UTF-8; without the
    mark, Excel on Windows opens them in the local code page and mangles
    every accented name.
    """
    with path.open("rb") as fh:
        head = fh.read(len(_BOM))
        if head != _BOM:
            yield _BOM
        yield head
        while chunk := fh.read(_CHUNK):
            yield chunk


def run_data_response(record: dict):
    """The CSV for GET /runs/{id}/data. The caller has checked readiness."""
    path = artifacts.source_path(record["id"])
    if record.get("assessment_id"):
        filename = safe_name(record["file"], record["id"]) + ".csv"
        return StreamingResponse(
            _with_bom(path),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    # An upload is returned exactly as uploaded: its encoding is whatever the
    # user's file used, so adding a UTF-8 mark could corrupt it.
    filename = safe_name(Path(record["file"]).name, "data.csv")
    return FileResponse(
        path,
        media_type="text/csv",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def assessment_zip(assessment_id: str) -> Optional[Path]:
    """Build (once) and return the assessment's ZIP, or None if nothing to put in it.

    Built only when every object has either finished profiling or failed, so
    the set of files can't change afterwards and the cached ZIP never goes
    stale: re-scoring changes scores, not data.
    """
    dest = artifacts.assessment_data_path(assessment_id)
    with _ZIP_LOCKS_GUARD:
        lock = _ZIP_LOCKS.setdefault(assessment_id, threading.Lock())
    with lock:
        if dest.exists():
            return dest
        runs = [r for r in registry.assessment_runs(assessment_id) if run_data_ready(r["id"])]
        if not runs:
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".zip.tmp")
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for run in runs:
                name = safe_name(run.get("object_name") or run["id"], run["id"]) + ".csv"
                # Streamed entry by entry: a large org's objects never have
                # to fit in memory together.
                with zf.open(name, "w", force_zip64=True) as out:
                    for chunk in _with_bom(artifacts.source_path(run["id"])):
                        out.write(chunk)
        tmp.replace(dest)
        return dest


def assessment_zip_blocked(assessment_id: str) -> Optional[str]:
    """Why the ZIP can't be built yet, or None if it can."""
    runs = registry.assessment_runs(assessment_id)
    if any(r["status"] == "processing" and not run_data_ready(r["id"]) for r in runs):
        return ("The data is still being downloaded or profiled. It can be "
                "downloaded once every object has finished profiling.")
    if not any(run_data_ready(r["id"]) for r in runs):
        return "No object in this assessment has data to download: every object failed."
    return None
