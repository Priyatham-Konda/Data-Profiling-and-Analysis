"""Salesforce connections and multi-object assessments (API contract rev 5).

Runs against tests/fake_salesforce.py, never a live org. The fake serves the
existing fixtures as Salesforce objects, so every object goes through the
same engine an uploaded file does -- which is the property under test.
"""
from __future__ import annotations

import io
import os
import time
import zipfile
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from fake_salesforce import FakeSalesforce

FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("dqa_sf")
    os.environ["DQA_DATA_ROOT"] = str(root)

    import importlib

    from dqa import config

    importlib.reload(config)

    from fastapi.testclient import TestClient

    from dqa.api.app import create_app

    with TestClient(create_app()) as test_client:
        test_client.data_root = root
        yield test_client


@pytest.fixture
def fake(monkeypatch):
    from dqa import config
    from dqa.connectors.salesforce import client as sf_client
    from dqa.connectors.salesforce import extract
    from dqa.connectors.salesforce.connections import STORE

    org = FakeSalesforce()
    org.add_csv_object("Account", "Account", FIXTURES / "clean.csv")
    org.add_csv_object("Contact", "Contact", FIXTURES / "dirty_known.csv")
    # Not supported by the Bulk API: exercises the query API fallback.
    org.add_csv_object("Invoice__c", "Invoice", FIXTURES / "clean.csv",
                       custom=True, bulk=False)
    org.add_empty_object("Empty__c", "Empty Object")

    monkeypatch.setattr(sf_client, "new_http_client",
                        lambda: httpx.Client(transport=org.transport))
    monkeypatch.setattr(extract, "_sleep", lambda seconds: None)
    # Small pages, so multi-page stitching is exercised on small fixtures.
    monkeypatch.setattr(config, "SF_BULK_PAGE_RECORDS", 120)
    monkeypatch.setattr(config, "SF_REST_BATCH_SIZE", 70)
    yield org
    STORE.clear()


def _connect(client, org: FakeSalesforce, **overrides):
    body = {"instanceUrl": org.host, "clientId": org.client_id,
            "clientSecret": org.client_secret}
    body.update(overrides)
    return client.post("/api/salesforce/connections", json=body)


def _assess(client, connection_id: str, objects: list[str]):
    return client.post("/api/assessments", json={
        "source": {"type": "salesforce", "connectionId": connection_id},
        "objects": objects,
    })


def _wait(client, assessment_id: str, until: set[str], timeout: float = 240.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/assessments/{assessment_id}").json()
        if body["status"] in until:
            return body
        time.sleep(0.3)
    raise AssertionError(f"assessment {assessment_id} never reached {until}")


def _detected(client, run_id: str) -> list[str]:
    columns = client.get(f"/api/runs/{run_id}/profile").json()["columns"]
    return [c["name"] for c in columns if c["isCde"]]


def _confirm_all(client, detail: dict) -> None:
    selections = {r["object"]: _detected(client, r["id"])
                  for r in detail["runs"] if r["status"] == "awaiting_cdes"}
    response = client.put(f"/api/assessments/{detail['id']}/cdes",
                          json={"objects": selections})
    assert response.status_code == 202, response.text


# --------------------------------------------------------------------------
class TestAddress:
    """The address check decides where the secret is sent."""

    @pytest.mark.parametrize("raw,expected", [
        ("acme.my.salesforce.com", "https://acme.my.salesforce.com"),
        ("https://acme.my.salesforce.com/", "https://acme.my.salesforce.com"),
        ("http://acme.my.salesforce.com", "https://acme.my.salesforce.com"),
        ("acme.lightning.force.com", "https://acme.my.salesforce.com"),
        ("https://acme.lightning.force.com/lightning/page/home",
         "https://acme.my.salesforce.com"),
        ("acme--uat.sandbox.lightning.force.com",
         "https://acme--uat.sandbox.my.salesforce.com"),
        ("acme--uat.sandbox.my.salesforce.com",
         "https://acme--uat.sandbox.my.salesforce.com"),
        ("acme-dev-ed.develop.lightning.force.com",
         "https://acme-dev-ed.develop.my.salesforce.com"),
        ("  ACME.My.Salesforce.com  ", "https://acme.my.salesforce.com"),
    ])
    def test_accepted_forms(self, raw, expected):
        from dqa.connectors.salesforce.auth import normalise_instance_url

        assert normalise_instance_url(raw) == expected

    @pytest.mark.parametrize("raw", [
        "acme.com",
        "acme.my.salesforce.com.evil.example",
        "evilmy.salesforce.com",
        "my.salesforce.com",
        "https://user@acme.my.salesforce.com",
        "https://acme.my.salesforce.com:8443",
        "ftp://acme.my.salesforce.com",
        "",
    ])
    def test_rejected(self, raw):
        from dqa.connectors.base import ConnectorError
        from dqa.connectors.salesforce.auth import normalise_instance_url

        with pytest.raises(ConnectorError) as caught:
            normalise_instance_url(raw)
        assert caught.value.code == "invalid_url"

    def test_generic_login_host_is_explained(self):
        from dqa.connectors.base import ConnectorError
        from dqa.connectors.salesforce.auth import normalise_instance_url

        with pytest.raises(ConnectorError) as caught:
            normalise_instance_url("https://login.salesforce.com")
        assert "My Domain" in caught.value.message


# --------------------------------------------------------------------------
class TestConnect:
    def test_connects_and_identifies_the_org(self, client, fake):
        response = _connect(client, fake)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["state"] == "connected"
        assert body["orgName"] == "Acme Corporation"
        assert body["username"] == "integration@acme.com"
        assert body["environment"] == "production"
        assert body["instanceUrl"] == "https://acme.my.salesforce.com"
        assert body["expiresAt"] is not None
        assert set(body) == {
            "id", "state", "environment", "orgId", "orgName", "orgEdition",
            "instanceUrl", "username", "connectedAt", "expiresAt",
            "closedReason", "assessmentId",
        }

    def test_secret_and_client_id_are_never_returned(self, client, fake):
        connection_id = _connect(client, fake).json()["id"]
        for response in (client.get(f"/api/salesforce/connections/{connection_id}"),):
            assert fake.client_secret not in response.text
            assert fake.client_id not in response.text

    def test_sandbox_is_detected(self, client, fake):
        fake.sandbox = True
        assert _connect(client, fake).json()["environment"] == "sandbox"

    @pytest.mark.parametrize("setup,overrides,status,code", [
        ("wrong_secret", {"clientSecret": "not-the-secret"}, 422, "invalid_client"),
        ("wrong_id", {"clientId": "3MVG9unknown"}, 422, "invalid_client"),
        ("flow_off", {}, 422, "flow_not_enabled"),
        ("no_run_as", {}, 422, "no_run_as_user"),
        ("api_off", {}, 422, "api_disabled"),
        ("bad_url", {"instanceUrl": "acme.example.com"}, 400, "invalid_url"),
        ("elsewhere", {"instanceUrl": "globex.my.salesforce.com"}, 502, "unreachable"),
    ])
    def test_errors_name_what_to_fix(self, client, fake, setup, overrides, status, code):
        from dqa.connectors.salesforce.connections import STORE

        fake.flow_enabled = setup != "flow_off"
        fake.run_as_set = setup != "no_run_as"
        fake.api_enabled = setup != "api_off"
        before = len(STORE._items)

        response = _connect(client, fake, **overrides)
        assert response.status_code == status, response.text
        body = response.json()
        assert body["errorCode"] == code
        assert body["error"]
        # A failed attempt keeps nothing.
        assert len(STORE._items) == before

    def test_a_rejected_org_has_its_token_revoked(self, client, fake):
        fake.api_enabled = False
        _connect(client, fake)
        assert fake.revoked and not fake.live_tokens

    def test_missing_field_is_a_plain_validation_error(self, client, fake):
        response = client.post("/api/salesforce/connections",
                               json={"instanceUrl": fake.host, "clientId": "x"})
        assert response.status_code == 400
        assert "error" in response.json()

    def test_unknown_connection_is_404(self, client, fake):
        assert client.get("/api/salesforce/connections/sfc_nope").status_code == 404

    def test_idle_connection_closes_and_revokes(self, client, fake):
        from dqa.connectors.salesforce.connections import STORE

        connection_id = _connect(client, fake).json()["id"]
        STORE._items[connection_id].last_used -= timedelta(minutes=31)

        body = client.get(f"/api/salesforce/connections/{connection_id}").json()
        assert body["state"] == "closed"
        assert body["closedReason"] == "idle"
        assert STORE._items[connection_id].client_secret is None
        assert not fake.live_tokens

    def test_delete_revokes(self, client, fake):
        from dqa.connectors.salesforce.connections import STORE

        connection_id = _connect(client, fake).json()["id"]
        assert client.delete(f"/api/salesforce/connections/{connection_id}").status_code == 204
        body = client.get(f"/api/salesforce/connections/{connection_id}").json()
        assert (body["state"], body["closedReason"]) == ("closed", "disconnected")
        assert STORE._items[connection_id].client_secret is None
        assert not fake.live_tokens

    def test_delete_refused_while_an_assessment_holds_it(self, client, fake):
        from dqa.connectors.salesforce.connections import STORE

        connection_id = _connect(client, fake).json()["id"]
        STORE._items[connection_id].assessment_id = "asm_downloading"
        assert client.delete(f"/api/salesforce/connections/{connection_id}").status_code == 409


# --------------------------------------------------------------------------
class TestObjects:
    def test_default_list_is_business_objects(self, client, fake):
        connection_id = _connect(client, fake).json()["id"]
        body = client.get(f"/api/salesforce/connections/{connection_id}/objects").json()
        names = [o["name"] for o in body["objects"]]

        for hidden in ("AccountHistory", "AccountShare", "Invoice__History",
                       "AccountChangeEvent", "Setting__c", "NotQueryable"):
            assert hidden not in names, hidden
        # Ends in "Event" but is the calendar object: business data.
        assert "Event" in names
        assert {"Account", "Contact", "Invoice__c"} <= set(names)
        # Five system objects hidden; NotQueryable can't be assessed at all.
        assert body["hiddenCount"] == 5

    def test_fields_counts_and_order(self, client, fake):
        connection_id = _connect(client, fake).json()["id"]
        objects = client.get(f"/api/salesforce/connections/{connection_id}/objects").json()["objects"]
        by_name = {o["name"]: o for o in objects}

        assert by_name["Contact"]["recordCount"] == 500
        assert by_name["Event"]["recordCount"] is None
        assert by_name["Account"]["suggested"] is True
        assert by_name["Invoice__c"] == {"name": "Invoice__c", "label": "Invoice",
                                         "custom": True, "recordCount": 300,
                                         "suggested": False}
        labels = [o["label"].casefold() for o in objects]
        assert labels == sorted(labels)

    def test_include_all_shows_system_objects_but_never_unqueryable(self, client, fake):
        connection_id = _connect(client, fake).json()["id"]
        body = client.get(
            f"/api/salesforce/connections/{connection_id}/objects?include=all"
        ).json()
        names = {o["name"] for o in body["objects"]}
        assert "AccountHistory" in names
        assert "NotQueryable" not in names
        assert body["hiddenCount"] == 0

    def test_closed_connection_can_not_list(self, client, fake):
        connection_id = _connect(client, fake).json()["id"]
        client.delete(f"/api/salesforce/connections/{connection_id}")
        response = client.get(f"/api/salesforce/connections/{connection_id}/objects")
        assert response.status_code == 409


# --------------------------------------------------------------------------
class TestAssessmentRequest:
    @pytest.mark.parametrize("objects,fragment", [
        ([], "at least one"),
        (["Account"] * 2, "once"),
        ([f"Obj{i}__c" for i in range(26)], "at most 25"),
    ])
    def test_selection_is_validated(self, client, fake, objects, fragment):
        connection_id = _connect(client, fake).json()["id"]
        response = _assess(client, connection_id, objects)
        assert response.status_code == 400
        assert fragment in response.json()["error"]

    def test_unknown_object_names_it_and_frees_the_connection(self, client, fake):
        connection_id = _connect(client, fake).json()["id"]
        response = _assess(client, connection_id, ["Account", "Nope__c"])
        assert response.status_code == 400
        assert "Nope__c" in response.json()["error"]
        # The failed attempt must not have used the connection up.
        detail = client.get(f"/api/salesforce/connections/{connection_id}").json()
        assert detail["assessmentId"] is None
        retry = _assess(client, connection_id, ["Empty__c"])
        assert retry.status_code == 201
        _wait(client, retry.json()["id"], {"failed", "awaiting_cdes"})

    def test_unknown_source_type(self, client, fake):
        response = client.post("/api/assessments", json={
            "source": {"type": "oracle", "connectionId": "x"}, "objects": ["A"]})
        assert response.status_code == 400
        assert response.json()["errorCode"] == "unknown_source"

    def test_unknown_and_closed_connections(self, client, fake):
        assert _assess(client, "sfc_nope", ["Account"]).status_code == 404
        connection_id = _connect(client, fake).json()["id"]
        client.delete(f"/api/salesforce/connections/{connection_id}")
        assert _assess(client, connection_id, ["Account"]).status_code == 409

    def test_one_connection_serves_one_assessment(self, client, fake):
        connection_id = _connect(client, fake).json()["id"]
        first = _assess(client, connection_id, ["Empty__c"])
        assert first.status_code == 201
        second = _assess(client, connection_id, ["Account"])
        assert second.status_code == 409
        assert second.json()["errorCode"] in ("in_use", "not_connected")
        _wait(client, first.json()["id"], {"failed", "awaiting_cdes"})


# --------------------------------------------------------------------------
class TestAssessmentFlow:
    """One full assessment, checked stage by stage."""

    @pytest.fixture
    def parked(self, client, fake):
        connection_id = _connect(client, fake).json()["id"]
        created = _assess(client, connection_id, ["Account", "Contact", "Invoice__c"])
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["status"] == "processing"
        assert [r["object"] for r in body["runs"]] == ["Account", "Contact", "Invoice__c"]
        detail = _wait(client, body["id"], {"awaiting_cdes", "failed", "completed"})
        return {"connection": connection_id, "detail": detail, "fake": fake}

    def test_parks_at_awaiting_cdes_with_per_object_shapes(self, client, parked):
        detail = parked["detail"]
        assert detail["status"] == "awaiting_cdes"
        assert detail["name"] == "Acme Corporation"
        assert detail["source"] == {"type": "salesforce", "orgName": "Acme Corporation",
                                    "orgId": "00D5g000004XyZAEA0",
                                    "environment": "production"}
        for run in detail["runs"]:
            assert set(run) == {"id", "object", "label", "status",
                                "records", "columns", "cdes"}, run
            assert run["status"] == "awaiting_cdes"
        records = {r["object"]: r["records"] for r in detail["runs"]}
        assert records == {"Account": 300, "Contact": 500, "Invoice__c": 300}

    def test_connection_closes_once_everything_is_downloaded(self, client, parked):
        from dqa.connectors.salesforce.connections import STORE

        body = client.get(f"/api/salesforce/connections/{parked['connection']}").json()
        assert body["state"] == "closed"
        assert body["closedReason"] == "extracted"
        assert STORE._items[parked["connection"]].client_secret is None
        assert not parked["fake"].live_tokens, "the access token must be revoked"

    def test_bulk_and_fallback_download_the_same_way(self, client, parked):
        from dqa.store import artifacts

        runs = {r["object"]: r["id"] for r in parked["detail"]["runs"]}
        account = artifacts.read_source_meta(runs["Account"])
        invoice = artifacts.read_source_meta(runs["Invoice__c"])
        assert account["method"] == "bulk"
        assert invoice["method"] == "query"
        # Same data, two download paths: same header, same records.
        account_csv = artifacts.source_path(runs["Account"]).read_text(encoding="utf-8")
        invoice_csv = artifacts.source_path(runs["Invoice__c"]).read_text(encoding="utf-8")
        assert account_csv.splitlines()[0] == invoice_csv.splitlines()[0]
        assert len(account_csv.splitlines()) == len(invoice_csv.splitlines()) == 301

    def test_compound_and_binary_fields_are_never_queried(self, client, parked):
        for soql in parked["fake"].soql_seen:
            assert "BillingAddress" not in soql and "Photo__c" not in soql, soql

    def test_profile_carries_salesforce_field_metadata(self, client, parked):
        contact = next(r for r in parked["detail"]["runs"] if r["object"] == "Contact")
        columns = client.get(f"/api/runs/{contact['id']}/profile").json()["columns"]
        by_name = {c["name"]: c for c in columns}

        assert "BillingAddress" not in by_name and "Photo__c" not in by_name
        assert list(by_name)[0] == "Id"
        assert by_name["email"]["salesforce"] == {
            "label": "Email", "type": "email", "custom": False, "required": False,
            "referenceTo": None, "picklistValues": None, "length": 255,
        }
        # nillable=False and createable, with no default: required.
        assert by_name["customer_id"]["salesforce"]["required"] is True

    def test_object_runs_stay_out_of_the_run_list(self, client, parked):
        ids = {r["id"] for r in parked["detail"]["runs"]}
        listed = {r["id"] for r in client.get("/api/runs").json()}
        assert not ids & listed
        everything = client.get("/api/runs?include=all").json()
        tagged = {r["id"]: r.get("assessmentId") for r in everything if r["id"] in ids}
        assert set(tagged) == ids
        assert set(tagged.values()) == {parked["detail"]["id"]}

    def test_object_run_carries_its_source(self, client, parked):
        contact = next(r for r in parked["detail"]["runs"] if r["object"] == "Contact")
        body = client.get(f"/api/runs/{contact['id']}").json()
        assert body["file"] == "Acme Corporation · Contact"
        assert body["assessmentId"] == parked["detail"]["id"]
        assert body["source"] == {"type": "salesforce", "orgName": "Acme Corporation",
                                  "object": "Contact", "label": "Contact"}

    def test_object_runs_are_confirmed_and_deleted_through_the_assessment(self, client, parked):
        run_id = parked["detail"]["runs"][0]["id"]
        columns = _detected(client, run_id)
        assert client.put(f"/api/runs/{run_id}/cdes",
                          json={"columns": columns}).status_code == 409
        assert client.delete(f"/api/runs/{run_id}").status_code == 409

    def test_confirmation_is_all_or_nothing(self, client, parked):
        detail = parked["detail"]
        account = next(r for r in detail["runs"] if r["object"] == "Account")
        url = f"/api/assessments/{detail['id']}/cdes"

        missing = client.put(url, json={"objects": {"Account": _detected(client, account["id"])}})
        assert missing.status_code == 400
        assert "Missing" in missing.json()["error"]

        bad = {r["object"]: _detected(client, r["id"]) for r in detail["runs"]}
        bad["Contact"] = ["not_a_column"]
        response = client.put(url, json={"objects": bad})
        assert response.status_code == 400
        assert "not_a_column" in response.json()["error"]

        empty = {r["object"]: _detected(client, r["id"]) for r in detail["runs"]}
        empty["Invoice__c"] = []
        assert client.put(url, json={"objects": empty}).status_code == 400
        # Nothing started.
        assert client.get(f"/api/assessments/{detail['id']}").json()["status"] == "awaiting_cdes"

    def test_scores_pool_every_object(self, client, parked):
        from dqa import config
        from dqa.models import RuleResult
        from dqa.scoring.aggregate import aggregate
        from dqa.store import artifacts

        _confirm_all(client, parked["detail"])
        done = _wait(client, parked["detail"]["id"], {"completed", "failed"})
        assert done["status"] == "completed"
        assert set(done["scores"]) == set(config.DIMENSIONS)
        assert done["records"] == 1100 and done["objects"] == 3
        assert done["partial"] is False

        # Recompute independently from the three objects' own results.
        pooled = {}
        for run in done["runs"]:
            assert run["status"] == "completed"
            for rid, rule in artifacts.read_results(run["id"])["rules"].items():
                pooled[f"{run['id']}:{rid}"] = RuleResult(
                    rule_id=rid, rule_name=rule["rule_name"],
                    dimension=rule["dimension"], severity=rule["severity"],
                    column=rule["column"], evaluated=rule["evaluated"],
                    failed=rule["failed"],
                )
        scores, overall, _ = aggregate(pooled, {})
        assert done["scores"] == scores
        assert done["overall"] == overall

        listed = next(a for a in client.get("/api/assessments").json()
                      if a["id"] == done["id"])
        assert listed == {"id": done["id"], "name": "Acme Corporation",
                          "status": "completed", "overall": overall, "objects": 3}

    def test_reports_render(self, client, parked):
        _confirm_all(client, parked["detail"])
        done = _wait(client, parked["detail"]["id"], {"completed", "failed"})
        for report_type in ("summary", "in-depth"):
            response = client.get(f"/api/assessments/{done['id']}/report?type={report_type}")
            assert response.status_code == 200, response.text
            assert response.headers["content-type"] == "application/pdf"
            assert response.content.startswith(b"%PDF")
        # A single object's own report still works.
        run_id = done["runs"][0]["id"]
        assert client.get(f"/api/runs/{run_id}/report?type=summary").status_code == 200

    def test_rescoring_one_object_recomputes_the_overall(self, client, parked):
        _confirm_all(client, parked["detail"])
        first = _wait(client, parked["detail"]["id"], {"completed"})
        contact = next(r for r in first["runs"] if r["object"] == "Contact")

        narrowed = _detected(client, contact["id"])[:2]
        response = client.put(f"/api/runs/{contact['id']}/cdes", json={"columns": narrowed})
        assert response.status_code == 202
        second = _wait(client, first["id"], {"completed"})
        rescored = next(r for r in second["runs"] if r["object"] == "Contact")
        assert rescored["cdes"] == 2
        assert second["overall"] != first["overall"] or second["scores"] != first["scores"]

    def test_delete_removes_everything(self, client, parked):
        from dqa.store import artifacts

        detail = parked["detail"]
        assert client.delete(f"/api/assessments/{detail['id']}").status_code == 204
        assert client.get(f"/api/assessments/{detail['id']}").status_code == 404
        for run in detail["runs"]:
            assert client.get(f"/api/runs/{run['id']}").status_code == 404
            assert not artifacts.run_dir(run["id"]).exists()


# --------------------------------------------------------------------------
class TestPartialAndFailed:
    def test_an_empty_object_fails_alone(self, client, fake):
        connection_id = _connect(client, fake).json()["id"]
        created = _assess(client, connection_id, ["Account", "Empty__c"]).json()
        parked = _wait(client, created["id"], {"awaiting_cdes", "failed"})
        assert parked["status"] == "awaiting_cdes"
        empty = next(r for r in parked["runs"] if r["object"] == "Empty__c")
        assert empty["status"] == "failed"
        assert "no records" in empty["error"]

        _confirm_all(client, parked)
        done = _wait(client, created["id"], {"completed", "failed"})
        assert done["status"] == "completed"
        assert done["partial"] is True
        assert done["objects"] == 2 and done["records"] == 300

    def test_all_objects_failing_fails_the_assessment(self, client, fake):
        connection_id = _connect(client, fake).json()["id"]
        created = _assess(client, connection_id, ["Empty__c"]).json()
        done = _wait(client, created["id"], {"failed", "completed", "awaiting_cdes"})
        assert done["status"] == "failed"
        response = client.put(f"/api/assessments/{created['id']}/cdes",
                              json={"objects": {"Empty__c": ["Name"]}})
        assert response.status_code == 409

    def test_refused_records_fail_that_object_with_a_reason(self, client, fake):
        fake.objects["Contact"].deny_records = True
        connection_id = _connect(client, fake).json()["id"]
        created = _assess(client, connection_id, ["Contact"]).json()
        done = _wait(client, created["id"], {"failed", "awaiting_cdes"})
        assert done["status"] == "failed"
        assert "insufficient access" in done["runs"][0]["error"]


# --------------------------------------------------------------------------
class TestCredentialHandling:
    def test_expired_token_is_reissued_mid_download(self, client, fake):
        connection_id = _connect(client, fake).json()["id"]
        fake.expire_next_call = True
        created = _assess(client, connection_id, ["Account"]).json()
        parked = _wait(client, created["id"], {"awaiting_cdes", "failed"})
        assert parked["status"] == "awaiting_cdes", parked
        assert fake.tokens_issued == 2

    def test_secret_is_never_written_to_disk(self, client, fake):
        connection_id = _connect(client, fake).json()["id"]
        created = _assess(client, connection_id, ["Account", "Invoice__c"]).json()
        parked = _wait(client, created["id"], {"awaiting_cdes"})
        _confirm_all(client, parked)
        _wait(client, created["id"], {"completed"})

        secret = fake.client_secret.encode()
        leaks = [p for p in Path(client.data_root).rglob("*")
                 if p.is_file() and secret in p.read_bytes()]
        assert not leaks, f"client secret found on disk in {leaks}"

    def test_upload_runs_are_unchanged(self, client, fake):
        with (FIXTURES / "clean.csv").open("rb") as fh:
            run_id = client.post("/api/runs", files={"file": ("clean.csv", fh, "text/csv")}).json()["id"]
        body = client.get(f"/api/runs/{run_id}").json()
        assert "assessmentId" not in body and "source" not in body
        assert run_id in {r["id"] for r in client.get("/api/runs").json()}


# --------------------------------------------------------------------------
BOM = b"\xef\xbb\xbf"


class TestDataDownload:
    """The data each object was assessed on, as CSV (API contract rev 5).

    The property that matters: the download is the file profiling and
    scoring actually read, not a regenerated copy that could drift from it.
    """

    @pytest.fixture
    def parked(self, client, fake):
        # Account via the Bulk API, Invoice__c via the query API fallback,
        # Empty__c fails for having no records.
        connection_id = _connect(client, fake).json()["id"]
        created = _assess(client, connection_id, ["Account", "Invoice__c", "Empty__c"])
        assert created.status_code == 201, created.text
        return _wait(client, created.json()["id"], {"awaiting_cdes", "failed"})

    def test_object_csv_is_the_profiled_file(self, client, parked):
        from dqa.store import artifacts

        account = next(r for r in parked["runs"] if r["object"] == "Account")
        response = client.get(f"/api/runs/{account['id']}/data")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/csv")
        assert 'filename="Acme_Corporation_Account.csv"' in response.headers["content-disposition"]

        stored = artifacts.source_path(account["id"]).read_bytes()
        assert response.content == BOM + stored
        lines = response.content[len(BOM):].decode("utf-8").splitlines()
        assert lines[0].split(",")[0] == "Id"
        assert len(lines) == 301  # header plus every record

    def test_both_download_paths_serve_the_same_shape(self, client, parked):
        runs = {r["object"]: r["id"] for r in parked["runs"]}
        bulk = client.get(f"/api/runs/{runs['Account']}/data").content
        fallback = client.get(f"/api/runs/{runs['Invoice__c']}/data").content
        assert bulk.splitlines()[0] == fallback.splitlines()[0]
        assert len(bulk.splitlines()) == len(fallback.splitlines())

    def test_zip_holds_one_csv_per_downloaded_object(self, client, parked):
        from dqa.store import artifacts

        response = client.get(f"/api/assessments/{parked['id']}/data")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/zip"
        assert 'filename="Acme_Corporation-data.zip"' in response.headers["content-disposition"]

        archive = zipfile.ZipFile(io.BytesIO(response.content))
        # Empty__c failed to download, so it has no file.
        assert sorted(archive.namelist()) == ["Account.csv", "Invoice__c.csv"]
        runs = {r["object"]: r["id"] for r in parked["runs"]}
        for obj in ("Account", "Invoice__c"):
            stored = artifacts.source_path(runs[obj]).read_bytes()
            assert archive.read(f"{obj}.csv") == BOM + stored, obj

        again = client.get(f"/api/assessments/{parked['id']}/data")
        assert again.content == response.content, "the ZIP is built once and reused"

    def test_still_available_after_scoring(self, client, parked):
        _confirm_all(client, parked)
        done = _wait(client, parked["id"], {"completed"})
        run_id = next(r["id"] for r in done["runs"] if r["object"] == "Account")
        assert client.get(f"/api/runs/{run_id}/data").status_code == 200
        assert client.get(f"/api/assessments/{done['id']}/data").status_code == 200

    def test_failed_object_has_no_data(self, client, parked):
        empty = next(r for r in parked["runs"] if r["object"] == "Empty__c")
        assert client.get(f"/api/runs/{empty['id']}/data").status_code == 409

    def test_not_ready_while_downloading(self, client, fake):
        # Built directly rather than raced against a live download.
        from dqa.store import artifacts, registry

        registry.create_assessment("asm_downloading_now", "Acme Corporation",
                                   {"type": "salesforce"}, 1)
        registry.create("run_downloading_now", "Acme Corporation · Account",
                        assessment_id="asm_downloading_now",
                        object_name="Account", object_label="Account")
        artifacts.ensure("run_downloading_now")
        artifacts.source_path("run_downloading_now").write_text("Id,Name\n001,partial")

        run = client.get("/api/runs/run_downloading_now/data")
        assert run.status_code == 409
        zipped = client.get("/api/assessments/asm_downloading_now/data")
        assert zipped.status_code == 409
        assert "still being downloaded" in zipped.json()["error"]

    def test_all_failed_has_nothing_to_zip(self, client, fake):
        connection_id = _connect(client, fake).json()["id"]
        created = _assess(client, connection_id, ["Empty__c"]).json()
        _wait(client, created["id"], {"failed"})
        response = client.get(f"/api/assessments/{created['id']}/data")
        assert response.status_code == 409
        assert "every object failed" in response.json()["error"]

    def test_unknown_ids_are_404(self, client, fake):
        assert client.get("/api/runs/run_nope/data").status_code == 404
        assert client.get("/api/assessments/asm_nope/data").status_code == 404

    def test_upload_is_returned_exactly_as_uploaded(self, client, fake):
        original = (FIXTURES / "clean.csv").read_bytes()
        with (FIXTURES / "clean.csv").open("rb") as fh:
            run_id = client.post(
                "/api/runs", files={"file": ("clean.csv", fh, "text/csv")}
            ).json()["id"]
        deadline = time.time() + 120
        while client.get(f"/api/runs/{run_id}").json()["status"] == "processing":
            assert time.time() < deadline, "upload never finished profiling"
            time.sleep(0.3)

        response = client.get(f"/api/runs/{run_id}/data")
        assert response.status_code == 200
        # No byte-order mark added: an upload's encoding is the user's own.
        assert response.content == original
        assert 'filename="clean.csv"' in response.headers["content-disposition"]

