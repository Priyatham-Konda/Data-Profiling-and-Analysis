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

CREATE TABLE IF NOT EXISTS users (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    email          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash  TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions (expires_at);
"""

# Added with multi-object assessments (API contract revision 5). A run that
# belongs to one carries its assessment and object; an uploaded file's run
# leaves all three NULL. Added by migration so an existing runs.db keeps
# every run it already holds.
#
# owner_id arrived with accounts. Every run and assessment belongs to the
# person who started it and is invisible to everyone else. Rows created
# before accounts existed start with a NULL owner and are handed to the
# first account ever created (create_user), so an existing installation
# keeps its history.
_RUN_COLUMNS = ("assessment_id", "object_name", "object_label", "owner_id")
_ASSESSMENT_COLUMNS = ("owner_id",)


def _connect() -> sqlite3.Connection:
    config.DATA_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    with _LOCK, _connect() as conn:
        conn.executescript(SCHEMA)
        for table, columns in (("runs", _RUN_COLUMNS), ("assessments", _ASSESSMENT_COLUMNS)):
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for column in columns:
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runs_assessment ON runs (assessment_id)"
        )
        # GET /runs and GET /assessments are the polled reads, and both now
        # filter on the owner before sorting by date.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runs_owner ON runs (owner_id, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_assessments_owner "
            "ON assessments (owner_id, created_at DESC)"
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create(
    run_id: str,
    filename: str,
    owner_id: str,
    assessment_id: Optional[str] = None,
    object_name: Optional[str] = None,
    object_label: Optional[str] = None,
) -> None:
    now = _now()
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO runs (id, file, status, overall, created_at, updated_at, "
            "owner_id, assessment_id, object_name, object_label) "
            "VALUES (?, ?, 'processing', NULL, ?, ?, ?, ?, ?, ?)",
            (run_id, filename, now, now, owner_id, assessment_id, object_name,
             object_label),
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


def list_runs(
    owner_id: str, limit: int = 200, include_assessment_runs: bool = False
) -> list[dict[str, Any]]:
    """The owner's runs, newest first. Exactly the shape GET /runs returns.

    Runs belonging to an assessment are listed through GET /assessments, so
    they are left out unless asked for.
    """
    where = "WHERE owner_id = ? "
    if not include_assessment_runs:
        where += "AND assessment_id IS NULL "
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT id, file, status, overall, assessment_id, created_at FROM runs "
            f"{where}ORDER BY created_at DESC LIMIT ?",
            (owner_id, limit),
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
def create_assessment(
    assessment_id: str, name: str, source: dict, objects: int, owner_id: str
) -> None:
    now = _now()
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO assessments (id, name, source, objects, overall, created_at, "
            "updated_at, owner_id) VALUES (?, ?, ?, ?, NULL, ?, ?, ?)",
            (assessment_id, name, json.dumps(source), objects, now, now, owner_id),
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


def list_assessments(owner_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """The owner's assessments, newest first, each with its object runs'
    statuses. One query.

    Polled like GET /runs, so it stays a single indexed read: the statuses
    come back as one concatenated column rather than a query per assessment.
    """
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT a.id, a.name, a.objects, a.overall, a.created_at, "
            "  (SELECT group_concat(r.status, ',') FROM runs r "
            "   WHERE r.assessment_id = a.id) AS statuses "
            "FROM assessments a WHERE a.owner_id = ? "
            "ORDER BY a.created_at DESC LIMIT ?",
            (owner_id, limit),
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


# --------------------------------------------------------------------------
# Users and sessions (dqa/auth/)
# --------------------------------------------------------------------------
# Sessions are looked up on every request, the 5-second polls included, so
# the lookup is one primary-key read joined to one user row. The table holds
# a hash of each token, never the token itself: a copy of runs.db must not be
# enough to sign in as anyone.
def create_user(user_id: str, name: str, email: str, password_hash: str) -> Optional[dict]:
    """The new user, or None when the email is already registered.

    The first account ever created also takes ownership of every run and
    assessment from before accounts existed. Same transaction, so two
    simultaneous first sign-ups can't both claim them.
    """
    try:
        with _LOCK, _connect() as conn:
            conn.execute(
                "INSERT INTO users (id, name, email, password_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, name, email, password_hash, _now()),
            )
            if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1:
                for table in ("runs", "assessments"):
                    conn.execute(
                        f"UPDATE {table} SET owner_id = ? WHERE owner_id IS NULL",
                        (user_id,),
                    )
    except sqlite3.IntegrityError:
        return None
    return {"id": user_id, "name": name, "email": email}


def user_by_email(email: str) -> Optional[dict[str, Any]]:
    """Includes password_hash, for signing in. Never return this to a client."""
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return dict(row) if row else None


def create_session(token_hash: str, user_id: str, expires_at: datetime) -> None:
    now = _now()
    with _LOCK, _connect() as conn:
        # Housekeeping rides along with sign-in rather than needing a job.
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (token_hash, user_id, now, expires_at.astimezone(timezone.utc).isoformat()),
        )


def session_user(token_hash: str) -> Optional[dict[str, Any]]:
    """The signed-in user for a live session: id, name and email only."""
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT u.id, u.name, u.email FROM sessions s "
            "JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at > ?",
            (token_hash, _now()),
        ).fetchone()
    return dict(row) if row else None


def delete_session(token_hash: str) -> None:
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
