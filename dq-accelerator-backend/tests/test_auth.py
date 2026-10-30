"""Accounts, sessions, and ownership.

Two people share one server here: alice and bob, each with their own
cookie jar. Nothing alice creates may be visible to bob, and an id of
alice's must look to bob exactly like an id that doesn't exist.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
PASSWORD = "correct horse battery"


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    root = tmp_path_factory.mktemp("dqa_auth")
    os.environ["DQA_DATA_ROOT"] = str(root)

    import importlib

    from dqa import config

    importlib.reload(config)

    from fastapi.testclient import TestClient

    from dqa.api.app import create_app

    application = create_app()
    # Entered once so startup (registry.init) runs; every client below
    # shares the app but keeps its own cookies.
    with TestClient(application):
        yield application


def _client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)


def _signup(client, email: str, name: str = "Someone", password: str = PASSWORD):
    return client.post("/api/auth/signup",
                       json={"name": name, "email": email, "password": password})


@pytest.fixture(scope="module")
def alice(app):
    client = _client(app)
    client.user = _signup(client, "alice@example.com", "Alice").json()
    return client


@pytest.fixture(scope="module")
def bob(app):
    client = _client(app)
    client.user = _signup(client, "bob@example.com", "Bob").json()
    return client


class TestSignedOut:
    @pytest.mark.parametrize("method, path", [
        ("get", "/api/runs"),
        ("get", "/api/runs/run_anything"),
        ("get", "/api/runs/run_anything/report"),
        ("get", "/api/assessments"),
        ("get", "/api/salesforce/connections/sfc_anything"),
        ("get", "/api/auth/me"),
    ])
    def test_needs_a_session(self, app, method, path):
        response = getattr(_client(app), method)(path)
        assert response.status_code == 401
        assert response.json() == {"error": "Sign in to continue."}

    def test_upload_needs_a_session(self, app):
        with (FIXTURES / "clean.csv").open("rb") as fh:
            response = _client(app).post(
                "/api/runs", files={"file": ("clean.csv", fh, "text/csv")})
        assert response.status_code == 401

    def test_health_stays_open(self, app):
        assert _client(app).get("/health").status_code == 200


class TestSignUp:
    def test_creates_the_account_and_signs_in(self, app):
        client = _client(app)
        response = _signup(client, "  Carol@Example.com ", "Carol")
        assert response.status_code == 201
        body = response.json()
        assert set(body) == {"id", "name", "email"}
        assert body["email"] == "carol@example.com"
        assert client.get("/api/auth/me").json() == body

    def test_cookie_is_httponly(self, app):
        response = _signup(_client(app), "dave@example.com")
        cookie = response.headers["set-cookie"].lower()
        assert cookie.startswith("dqa_session=")
        assert "httponly" in cookie
        assert "samesite=lax" in cookie

    def test_password_is_stored_hashed(self, app):
        from dqa.store import registry

        _signup(_client(app), "erin@example.com")
        stored = registry.user_by_email("erin@example.com")["password_hash"]
        assert stored.startswith("scrypt$")
        assert PASSWORD not in stored

    def test_email_is_taken_regardless_of_case(self, app, alice):
        response = _signup(_client(app), "ALICE@example.com")
        assert response.status_code == 409
        assert "already exists" in response.json()["error"]

    @pytest.mark.parametrize("body, message", [
        ({"name": " ", "email": "x@example.com", "password": PASSWORD}, "name"),
        ({"name": "X", "email": "not-an-email", "password": PASSWORD}, "email"),
        ({"name": "X", "email": "x@example.com", "password": "short"}, "8 characters"),
    ])
    def test_rejects_bad_input(self, app, body, message):
        response = _client(app).post("/api/auth/signup", json=body)
        assert response.status_code == 400
        assert message in response.json()["error"]


class TestSignIn:
    def test_right_password_signs_in(self, app, alice):
        client = _client(app)
        response = client.post("/api/auth/login",
                               json={"email": "Alice@Example.com", "password": PASSWORD})
        assert response.status_code == 200
        assert response.json() == alice.user
        assert client.get("/api/auth/me").json() == alice.user

    def test_wrong_password_and_unknown_email_look_the_same(self, app, alice):
        wrong = _client(app).post("/api/auth/login",
                                  json={"email": "alice@example.com", "password": "nope-nope"})
        unknown = _client(app).post("/api/auth/login",
                                    json={"email": "nobody@example.com", "password": PASSWORD})
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()
        assert "set-cookie" not in wrong.headers


class TestSignOut:
    def test_ends_the_session_on_the_server(self, app):
        client = _client(app)
        _signup(client, "frank@example.com")
        token = client.cookies.get("dqa_session")

        assert client.post("/api/auth/logout").status_code == 204
        # The old token is dead even when presented again, not just forgotten
        # by this browser.
        replay = _client(app)
        replay.cookies.set("dqa_session", token)
        assert replay.get("/api/auth/me").status_code == 401

    def test_succeeds_without_a_session(self, app):
        assert _client(app).post("/api/auth/logout").status_code == 204

    def test_expired_session_is_refused(self, app, alice):
        from dqa.auth import sessions
        from dqa.store import registry

        registry.create_session(sessions._hash("stale-token"), alice.user["id"],
                                datetime.now(timezone.utc) - timedelta(minutes=1))
        client = _client(app)
        client.cookies.set("dqa_session", "stale-token")
        assert client.get("/api/auth/me").status_code == 401


class TestRunsArePrivate:
    @pytest.fixture(scope="class")
    def run_id(self, alice):
        with (FIXTURES / "clean.csv").open("rb") as fh:
            response = alice.post("/api/runs",
                                  files={"file": ("clean.csv", fh, "text/csv")})
        assert response.status_code == 201
        return response.json()["id"]

    def test_owner_sees_it(self, alice, run_id):
        assert run_id in {r["id"] for r in alice.get("/api/runs").json()}
        assert alice.get(f"/api/runs/{run_id}").status_code == 200

    def test_others_dont_see_it_listed(self, bob, run_id):
        listed = {r["id"] for r in bob.get("/api/runs?include=all").json()}
        assert run_id not in listed

    @pytest.mark.parametrize("method, suffix", [
        ("get", ""),
        ("get", "/profile"),
        ("get", "/dimensions/completeness"),
        ("get", "/dimensions/completeness/rules/COMP-001/examples"),
        ("get", "/report?type=summary"),
        ("get", "/data"),
        ("delete", ""),
    ])
    def test_others_get_not_found(self, bob, run_id, method, suffix):
        response = getattr(bob, method)(f"/api/runs/{run_id}{suffix}")
        assert response.status_code == 404
        assert response.json() == {"error": "That assessment could not be found."}

    def test_others_cannot_rescore_it(self, bob, run_id):
        response = bob.put(f"/api/runs/{run_id}/cdes", json={"columns": ["email"]})
        assert response.status_code == 404

    def test_still_there_for_the_owner(self, alice, run_id):
        assert alice.get(f"/api/runs/{run_id}").status_code == 200


class TestAssessmentsArePrivate:
    @pytest.fixture(scope="class")
    def assessment_id(self, alice):
        from dqa.store import registry

        registry.create_assessment("asm_alices", "Acme Corporation",
                                   {"type": "salesforce"}, 0, alice.user["id"])
        return "asm_alices"

    def test_listed_only_for_the_owner(self, alice, bob, assessment_id):
        assert assessment_id in {a["id"] for a in alice.get("/api/assessments").json()}
        assert assessment_id not in {a["id"] for a in bob.get("/api/assessments").json()}

    @pytest.mark.parametrize("method, suffix", [
        ("get", ""),
        ("get", "/report?type=summary"),
        ("get", "/data"),
        ("delete", ""),
    ])
    def test_others_get_not_found(self, bob, assessment_id, method, suffix):
        response = getattr(bob, method)(f"/api/assessments/{assessment_id}{suffix}")
        assert response.status_code == 404
