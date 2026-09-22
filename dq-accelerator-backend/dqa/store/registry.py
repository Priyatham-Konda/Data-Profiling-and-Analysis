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
"""


def _connect() -> sqlite3.Connection:
    config.DATA_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    with _LOCK, _connect() as conn:
        conn.executescript(SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create(run_id: str, filename: str) -> None:
    now = _now()
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO runs (id, file, status, overall, created_at, updated_at) "
            "VALUES (?, ?, 'processing', NULL, ?, ?)",
            (run_id, filename, now, now),
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


def list_runs(limit: int = 200) -> list[dict[str, Any]]:
    """Newest first. Exactly the shape GET /runs returns."""
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT id, file, status, overall FROM runs "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    out = []
    for row in rows:
        item: dict[str, Any] = {
            "id": row["id"],
            "file": row["file"],
            "status": row["status"],
        }
        # `overall` is present ONLY on completed runs, per the contract.
        if row["status"] == "completed" and row["overall"] is not None:
            item["overall"] = row["overall"]
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
