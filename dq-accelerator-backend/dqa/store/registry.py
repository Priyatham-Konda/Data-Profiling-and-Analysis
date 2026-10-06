"""Run registry.

Deliberately a single flat SQLite table with no joins. The API contract warns
that GET /runs is polled every 5 seconds per open browser tab for as long as
anything is in flight, so this is the hottest path in the system and the one
most likely to appear in a slow-query log. Keeping scores and counts out of
it is the whole point.

Anything richer lives in the run's JSON artifacts, which are read only when a
specific run is opened.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from .. import config

_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    file        TEXT NOT NULL,
    status      TEXT NOT NULL,
    overall     REAL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status  ON runs (status);

CREATE TABLE IF NOT EXISTS assessments (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    source      TEXT NOT NULL,
    objects     INTEGER NOT NULL,
    overall     REAL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assessments_created ON assessments (created_at DESC);
"""

# Added with multi-object assessments (API contract revision 5). A run that
# belongs to one carries its assessment and object; an uploaded file's run
# leaves all three NULL. Added by migration so an existing runs.db keeps
# every run it already holds.
_RUN_COLUMNS = ("assessment_id", "object_name", "object_label")


def _connect() -> sqlite3.Connection:
    config.DATA_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    with _LOCK, _connect() as conn:
        conn.executescript(SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
        for column in _RUN_COLUMNS:
            if column not in existing:
                conn.execute(f"ALTER TABLE runs ADD COLUMN {column} TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runs_assessment ON runs (assessment_id)"
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create(
    run_id: str,
    filename: str,
    assessment_id: Optional[str] = None,
    object_name: Optional[str] = None,
    object_label: Optional[str] = None,
) -> None:
    now = _now()
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO runs (id, file, status, overall, created_at, updated_at, "
            "assessment_id, object_name, object_label) "
            "VALUES (?, ?, 'processing', NULL, ?, ?, ?, ?, ?)",
            (run_id, filename, now, now, assessment_id, object_name, object_label),
        )


def set_status(run_id: str, status: str, overall: Optional[float] = None) -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            "UPDATE runs SET status = ?, overall = ?, updated_at = ? WHERE id = ?",
            (status, overall, _now(), run_id),
        )


def get(run_id: str) -> Optional[dict[str, Any]]:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def list_runs(limit: int = 200, include_assessment_runs: bool = False) -> list[dict[str, Any]]:
    """Newest first. Exactly the shape GET /runs returns.

    Runs belonging to an assessment are listed through GET /assessments, so
    they are left out unless asked for.
    """
    where = "" if include_assessment_runs else "WHERE assessment_id IS NULL "
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT id, file, status, overall, assessment_id, created_at FROM runs "
            f"{where}ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    out = []
    for row in rows:
        item: dict[str, Any] = {
            "id": row["id"],
            "file": row["file"],
            "status": row["status"],
            "createdAt": row["created_at"],
        }
        # `overall` is present ONLY on completed runs, per the contract.
        if row["status"] == "completed" and row["overall"] is not None:
            item["overall"] = row["overall"]
        if row["assessment_id"]:
            item["assessmentId"] = row["assessment_id"]
        out.append(item)
    return out


def delete(run_id: str) -> bool:
    with _LOCK, _connect() as conn:
        cursor = conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        return cursor.rowcount > 0


def expired(days: int | None = None) -> list[str]:
    """Run ids older than the retention period."""
    days = days if days is not None else config.RETENTION_DAYS
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT id FROM runs WHERE created_at < datetime('now', ?)",
            (f"-{days} days",),
        ).fetchall()
    return [r["id"] for r in rows]


# --------------------------------------------------------------------------
# Assessments (API contract revision 5)
# --------------------------------------------------------------------------
def create_assessment(assessment_id: str, name: str, source: dict, objects: int) -> None:
    now = _now()
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO assessments (id, name, source, objects, overall, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, NULL, ?, ?)",
            (assessment_id, name, json.dumps(source), objects, now, now),
        )


def get_assessment(assessment_id: str) -> Optional[dict[str, Any]]:
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM assessments WHERE id = ?", (assessment_id,)
        ).fetchone()
    if row is None:
        return None
    record = dict(row)
    record["source"] = json.loads(record["source"])
    return record


def assessment_runs(assessment_id: str) -> list[dict[str, Any]]:
    """The assessment's object runs, in the order the objects were selected."""
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM runs WHERE assessment_id = ? ORDER BY rowid",
            (assessment_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_assessments(limit: int = 200) -> list[dict[str, Any]]:
    """Newest first, each with its object runs' statuses. One query.

    Polled like GET /runs, so it stays a single indexed read: the statuses
    come back as one concatenated column rather than a query per assessment.
    """
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT a.id, a.name, a.objects, a.overall, a.created_at, "
            "  (SELECT group_concat(r.status, ',') FROM runs r "
            "   WHERE r.assessment_id = a.id) AS statuses "
            "FROM assessments a ORDER BY a.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out = []
    for row in rows:
        record = dict(row)
        record["statuses"] = record["statuses"].split(",") if record["statuses"] else []
        out.append(record)
    return out


def set_assessment_overall(assessment_id: str, overall: Optional[float]) -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            "UPDATE assessments SET overall = ?, updated_at = ? WHERE id = ?",
            (overall, _now(), assessment_id),
        )


def delete_assessment(assessment_id: str) -> bool:
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM runs WHERE assessment_id = ?", (assessment_id,))
        cursor = conn.execute("DELETE FROM assessments WHERE id = ?", (assessment_id,))
        return cursor.rowcount > 0

