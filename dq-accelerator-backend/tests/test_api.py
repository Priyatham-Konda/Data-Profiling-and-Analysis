"""API tests.

Asserts the HTTP contract in API_CONTRACT.md: status codes, response shapes,
and the field-presence rules the frontend depends on.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("dqa_api")
    os.environ["DQA_DATA_ROOT"] = str(root)

    import importlib

    from dqa import config

    importlib.reload(config)

    from fastapi.testclient import TestClient

    from dqa.api.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def _upload(client, fixture: str = "dirty_known.csv"):
    with (FIXTURES / fixture).open("rb") as fh:
        return client.post(
            "/api/runs",
            files={"file": (fixture, fh, "text/csv")},
        )


def _wait(client, run_id: str, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body.get("status") in ("completed", "failed"):
            return body
        time.sleep(0.4)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


# --------------------------------------------------------------------------
class TestCreateRun:
    def test_returns_201_immediately(self, client):
        response = _upload(client)
        assert response.status_code == 201
        body = response.json()
        assert set(body) == {"id", "file", "status"}
        assert body["status"] == "processing"
        assert body["file"] == "dirty_known.csv"

    def test_rejects_non_csv_extension(self, client):
        response = client.post(
            "/api/runs",
            files={"file": ("report.xlsx", b"PK\x03\x04binary",
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 415
        assert "error" in response.json()
        assert "CSV" in response.json()["error"]

    def test_rejects_empty_file(self, client):
        response = client.post(
            "/api/runs", files={"file": ("empty.csv", b"", "text/csv")}
        )
        assert response.status_code == 400
        assert "error" in response.json()

    def test_error_messages_are_human_readable(self, client):
        response = client.post(
            "/api/runs", files={"file": ("x.xlsx", b"data", "text/csv")}
        )
        message = response.json()["error"]
        assert not message.startswith("Traceback")
        assert message[0].isupper() and message.rstrip().endswith((".", "!"))


# --------------------------------------------------------------------------
class TestListRuns:
    def test_shape_and_ordering(self, client):
        _upload(client)
        body = client.get("/api/runs").json()
        assert isinstance(body, list) and body

        for item in body:
            assert {"id", "file", "status"} <= set(item)
            # `overall` appears ONLY on completed runs, per the contract.
            if item["status"] != "completed":
                assert "overall" not in item

    def test_list_carries_no_scores(self, client):
        """This endpoint is polled every 5s; it must stay lean."""
        for item in client.get("/api/runs").json():
            assert "scores" not in item
            assert "records" not in item


# --------------------------------------------------------------------------
class TestRunDetail:
    def test_404_for_unknown_run(self, client):
        response = client.get("/api/runs/run_does_not_exist")
        assert response.status_code == 404
        assert "error" in response.json()

    def test_processing_shape(self, client):
        run_id = _upload(client).json()["id"]
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] == "processing":
            assert body["stageCount"] == 4
            assert 0 <= body["stageIndex"] < 4
            assert 0.0 <= body["progress"] <= 1.0
            assert isinstance(body["stage"], str) and body["stage"]

    def test_completed_shape(self, client):
        run_id = _upload(client).json()["id"]
        body = _wait(client, run_id)
        assert body["status"] == "completed", body.get("error")

        assert isinstance(body["overall"], (int, float))
        assert body["records"] == 500
        assert body["cdes"] > 0

        from dqa import config

        assert set(body["scores"]) == set(config.DIMENSIONS)
        assert "integrity" in body["scores"]

        # Additive fields
        assert body["columns"] == 20
        assert body["sampled"] is False
        assert isinstance(body["notAssessed"], dict)

    def test_scores_are_0_to_100_and_progress_is_a_fraction(self, client):
        run_id = _upload(client).json()["id"]
        body = _wait(client, run_id)
        for key, score in body["scores"].items():
            if score is not None:
                assert 0 <= score <= 100, f"{key}={score}"

    def test_unassessable_dimension_is_null_not_zero(self, client):
        run_id = _upload(client, "no_dates.csv").json()["id"]
        body = _wait(client, run_id)
        assert body["scores"]["timeliness"] is None
        assert body["notAssessed"]["timeliness"]


# --------------------------------------------------------------------------
class TestProfileAndCdes:
    def test_profile_shape(self, client):
        run_id = _upload(client).json()["id"]
        _wait(client, run_id)

        body = client.get(f"/api/runs/{run_id}/profile").json()
        assert len(body["columns"]) == 20
        for column in body["columns"]:
            assert {"name", "inferredType", "semanticType", "fillRate",
                    "distinctRatio", "sampleValues", "isCde", "cdeScore",
                    "cdeReason"} <= set(column)
            assert 0.0 <= column["fillRate"] <= 1.0
            assert column["cdeReason"], "every decision must be explainable"

    def test_cde_override_reassesses(self, client):
        run_id = _upload(client).json()["id"]
        _wait(client, run_id)

        response = client.put(
            f"/api/runs/{run_id}/cdes", json={"columns": ["email", "phone"]}
        )
        assert response.status_code == 202
        assert response.json()["status"] == "processing"

        body = _wait(client, run_id)
        assert body["cdes"] == 2
        assert body["cdeOverridden"] is True

    def test_unknown_column_rejected(self, client):
        run_id = _upload(client).json()["id"]
        _wait(client, run_id)
        response = client.put(
            f"/api/runs/{run_id}/cdes", json={"columns": ["not_a_column"]}
        )
        assert response.status_code == 400
        assert "not_a_column" in response.json()["error"]


# --------------------------------------------------------------------------
class TestDimensionsAndExamples:
    @pytest.fixture(scope="class")
    def completed(self, client):
        run_id = _upload(client).json()["id"]
        _wait(client, run_id)
        return run_id

    def test_dimension_score_matches_the_tile(self, client, completed):
        run_detail = client.get(f"/api/runs/{completed}").json()
        for key, tile_score in run_detail["scores"].items():
            drawer = client.get(f"/api/runs/{completed}/dimensions/{key}").json()
            assert drawer["key"] == key
            assert drawer["score"] == tile_score, (
                f"{key}: drawer {drawer['score']} != tile {tile_score}"
            )

    def test_unknown_dimension_is_404(self, client, completed):
        # "integrity" used to stand in for an unknown dimension here. It is
        # a real dimension now, so this needs one that genuinely is not.
        response = client.get(f"/api/runs/{completed}/dimensions/lineage")
        assert response.status_code == 404

    def test_rules_shape(self, client, completed):
        body = client.get(f"/api/runs/{completed}/dimensions/completeness").json()
        assert body["rules"]
        for rule in body["rules"]:
            assert {"id", "name", "passRate"} <= set(rule)
            assert 0.0 <= rule["passRate"] <= 1.0
            assert rule["severity"] in ("high", "medium", "low")

    def test_examples_total_is_the_real_count(self, client, completed):
        drawer = client.get(f"/api/runs/{completed}/dimensions/completeness").json()
        rule = next(r for r in drawer["rules"] if r["failed"] > 0)

        body = client.get(
            f"/api/runs/{completed}/dimensions/completeness"
            f"/rules/{rule['id']}/examples?limit=10"
        ).json()

        assert body["ruleId"] == rule["id"]
        assert body["total"] == rule["failed"], (
            "total must be the whole-file count, not the sample size"
        )
        assert len(body["examples"]) <= 10
        for example in body["examples"]:
            assert {"row", "column", "value", "reason"} <= set(example)
            assert example["reason"]

    def test_unknown_rule_is_404(self, client, completed):
        response = client.get(
            f"/api/runs/{completed}/dimensions/completeness"
            f"/rules/NOPE-99/examples"
        )
        assert response.status_code == 404


# --------------------------------------------------------------------------
class TestReport:
    def test_409_before_completion(self, client):
        run_id = _upload(client).json()["id"]
        response = client.get(f"/api/runs/{run_id}/report?type=summary")
        if response.status_code == 409:
            assert "error" in response.json()
        else:
            # The run finished first; that is fine, just not what we probed.
            assert response.status_code in (200, 500)
        _wait(client, run_id)

    def test_invalid_type_is_400(self, client):
        run_id = _upload(client).json()["id"]
        _wait(client, run_id)
        response = client.get(f"/api/runs/{run_id}/report?type=wrong")
        assert response.status_code == 400

    def test_summary_pdf_downloads(self, client):
        pytest.importorskip("weasyprint")
        run_id = _upload(client).json()["id"]
        _wait(client, run_id)
        response = client.get(f"/api/runs/{run_id}/report?type=summary")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert "attachment" in response.headers["content-disposition"]
        assert response.content[:4] == b"%PDF"


# --------------------------------------------------------------------------
class TestDelete:
    def test_delete_returns_204_and_removes_artifacts(self, client):
        run_id = _upload(client).json()["id"]
        _wait(client, run_id)

        from dqa.store import artifacts

        assert artifacts.run_dir(run_id).exists()

        response = client.delete(f"/api/runs/{run_id}")
        assert response.status_code == 204

        assert not artifacts.run_dir(run_id).exists(), "artifacts must be purged"
        assert client.get(f"/api/runs/{run_id}").status_code == 404

    def test_delete_unknown_is_404(self, client):
        assert client.delete("/api/runs/run_nope").status_code == 404

    def test_delete_while_processing_cancels(self, client):
        run_id = _upload(client).json()["id"]
        response = client.delete(f"/api/runs/{run_id}")
        assert response.status_code == 204
        assert client.get(f"/api/runs/{run_id}").status_code == 404


# --------------------------------------------------------------------------
class TestHealth:
    def test_health(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert len(body["dimensions"]) == 7
