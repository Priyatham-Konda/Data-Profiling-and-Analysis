"""Upgrading an installation that already has runs from before accounts.

Its own module because it needs a database that has runs and no users yet,
which is exactly the state every other module's fixtures move past.
"""
from __future__ import annotations

import os

import pytest

PASSWORD = "correct horse battery"


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    root = tmp_path_factory.mktemp("dqa_upgrade")
    os.environ["DQA_DATA_ROOT"] = str(root)

    import importlib

    from dqa import config

    importlib.reload(config)

    from fastapi.testclient import TestClient

    from dqa.api.app import create_app
    from dqa.store import registry

    # The database as an older release left it: the same tables, with rows
    # that predate the owner_id column.
    registry.init()
    with registry._connect() as conn:
        conn.execute(
            "INSERT INTO runs (id, file, status, overall, created_at, updated_at) "
            "VALUES ('run_from_before', 'old.csv', 'failed', NULL, "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO assessments (id, name, source, objects, overall, created_at, "
            "updated_at) VALUES ('asm_from_before', 'Acme', '{}', 0, NULL, "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )

    application = create_app()
    with TestClient(application):
        yield application


def _signed_up(app, email):
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.post("/api/auth/signup",
                           json={"name": "Someone", "email": email, "password": PASSWORD})
    assert response.status_code == 201
    return client


def test_first_account_inherits_everything_from_before(app):
    first = _signed_up(app, "first@example.com")
    second = _signed_up(app, "second@example.com")

    assert [r["id"] for r in first.get("/api/runs").json()] == ["run_from_before"]
    assert [a["id"] for a in first.get("/api/assessments").json()] == ["asm_from_before"]

    assert second.get("/api/runs").json() == []
    assert second.get("/api/assessments").json() == []
    assert second.get("/api/runs/run_from_before").status_code == 404
