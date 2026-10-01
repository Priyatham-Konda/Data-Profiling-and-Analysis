"""A fake Salesforce org, served through httpx.MockTransport.

Implements the slice of Salesforce the connector uses -- the client
credentials token endpoint, identity, object listing, describe, record
counts, Bulk API 2.0 query jobs and the query API -- and refuses what real
Salesforce refuses: compound and binary fields in a query, the Bulk API on
objects it doesn't support, unknown or revoked tokens, other hosts. A
connector bug therefore fails a test loudly instead of passing quietly.
"""
from __future__ import annotations

import csv
import io
import json
import re
import uuid
from pathlib import Path
from urllib.parse import parse_qs

import httpx

from dqa import config


def _err(status: int, code: str, message: str) -> httpx.Response:
    return httpx.Response(status, json=[{"errorCode": code, "message": message}])


def _oauth_err(error: str, description: str) -> httpx.Response:
    return httpx.Response(400, json={"error": error, "error_description": description})


class FakeObject:
    def __init__(self, name: str, label: str, rows: list[dict], fields: list[dict], *,
                 custom: bool = False, bulk: bool = True, record_count: int | None = None,
                 deny_records: bool = False):
        self.name = name
        self.label = label
        self.rows = rows
        self.fields = fields
        self.custom = custom
        self.bulk = bulk
        self.record_count = len(rows) if record_count is None else record_count
        self.deny_records = deny_records

    def sobject(self) -> dict:
        return {"name": self.name, "label": self.label, "custom": self.custom,
                "queryable": True, "retrieveable": True, "layoutable": True,
                "deprecatedAndHidden": False, "customSetting": False}


class FakeSalesforce:
    def __init__(self, host: str = "acme.my.salesforce.com", *,
                 org_name: str = "Acme Corporation", sandbox: bool = False,
                 client_id: str = "3MVG9fake.client.id",
                 client_secret: str = "fake-secret-7f2c9e41b8d0",
                 run_as: str = "integration@acme.com"):
        self.host = host
        self.org_name = org_name
        self.sandbox = sandbox
        self.client_id = client_id
        self.client_secret = client_secret
        self.run_as = run_as
        self.org_id = "00D5g000004XyZAEA0"

        # Setup switches a test can turn off.
        self.flow_enabled = True
        self.run_as_set = True
        self.api_enabled = True
        self.expire_next_call = False

        self.objects: dict[str, FakeObject] = {}
        self.live_tokens: set[str] = set()
        self.tokens_issued = 0
        self.revoked: list[str] = []
        self.aborted: list[str] = []
        self.bulk_jobs: dict[str, dict] = {}
        self.cursors: dict[str, dict] = {}
        self.soql_seen: list[str] = []

    # -- building the org ----------------------------------------------------
    def add_csv_object(self, name: str, label: str, csv_path: Path, **kwargs) -> FakeObject:
        with Path(csv_path).open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            columns = list(reader.fieldnames or [])
            rows = list(reader)
        # An export taken from Salesforce already has its records' Ids; keep
        # them. Otherwise invent them. Either way there is exactly one Id
        # field, as in real Salesforce.
        if "Id" not in columns:
            prefix = "001" if name == "Account" else "003" if name == "Contact" else "a0X"
            for i, row in enumerate(rows):
                row["Id"] = f"{prefix}{i:015d}"
        fields = [{"name": "Id", "label": f"{label} ID", "type": "id", "nillable": False,
                   "createable": False, "defaultedOnCreate": True, "length": 18}]
        columns = [c for c in columns if c != "Id"]
        for column in columns:
            fields.append({
                "name": column, "label": column.replace("_", " ").title(),
                "type": "email" if "email" in column else "string",
                "nillable": column != columns[0], "createable": True,
                "defaultedOnCreate": False, "custom": False, "length": 255,
            })
        # Fields Salesforce would refuse in a query, never in the rows. Added
        # only under names the data doesn't already use: real Salesforce
        # never has two fields with one name, and some exports already carry
        # a flattened "BillingAddress" column of their own.
        for extra in ({"name": "BillingAddress", "label": "Billing Address",
                       "type": "address", "nillable": True},
                      {"name": "Photo__c", "label": "Photo", "type": "base64",
                       "nillable": True, "custom": True}):
            if extra["name"] not in columns:
                fields.append(extra)
        obj = FakeObject(name, label, rows, fields, **kwargs)
        self.objects[name] = obj
        return obj

    def add_empty_object(self, name: str, label: str) -> FakeObject:
        fields = [{"name": "Id", "label": "Record ID", "type": "id", "nillable": False},
                  {"name": "Name", "label": "Name", "type": "string", "nillable": True}]
        obj = FakeObject(name, label, [], fields, custom=name.endswith("__c"))
        self.objects[name] = obj
        return obj

    def _system_sobjects(self) -> list[dict]:
        base = {"queryable": True, "retrieveable": True, "deprecatedAndHidden": False,
                "customSetting": False, "custom": False}
        return [
            {**base, "name": "AccountHistory", "label": "Account History", "layoutable": False},
            {**base, "name": "AccountShare", "label": "Account Share", "layoutable": False},
            {**base, "name": "Invoice__History", "label": "Invoice History", "layoutable": True},
            {**base, "name": "AccountChangeEvent", "label": "Account Change Event", "layoutable": True},
            {**base, "name": "Setting__c", "label": "Setting", "layoutable": True,
             "customSetting": True, "custom": True},
            {**base, "name": "NotQueryable", "label": "Not Queryable", "queryable": False,
             "layoutable": True},
            # A business object whose name ends in "Event": must stay visible.
            {**base, "name": "Event", "label": "Event", "layoutable": True},
        ]

    # -- the transport -------------------------------------------------------
    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != self.host:
            raise httpx.ConnectError(f"no route to {request.url.host}", request=request)
        path = request.url.path
        if path == "/services/oauth2/token":
            return self._token(request)
        if path == "/services/oauth2/revoke":
            token = parse_qs(request.content.decode()).get("token", [""])[0]
            self.revoked.append(token)
            self.live_tokens.discard(token)
            return httpx.Response(200)

        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ")
        if token not in self.live_tokens:
            return _err(401, "INVALID_SESSION_ID", "Session expired or invalid")
        if self.expire_next_call:
            self.expire_next_call = False
            self.live_tokens.discard(token)
            return _err(401, "INVALID_SESSION_ID", "Session expired or invalid")

        if path == "/services/oauth2/userinfo":
            if not self.api_enabled:
                return _err(403, "API_DISABLED_FOR_ORG",
                            "API is not enabled for this Organization or Partner")
            return httpx.Response(200, json={"preferred_username": self.run_as,
                                             "organization_id": self.org_id,
                                             "user_id": "0055g00000AbCdEAAV"})

        api = f"/services/data/v{config.SF_API_VERSION}"
        if not path.startswith(api):
            return _err(404, "NOT_FOUND", f"The requested resource does not exist: {path}")
        rest = path[len(api):]

        if rest == "/sobjects":
            sobjects = [o.sobject() for o in self.objects.values()] + self._system_sobjects()
            return httpx.Response(200, json={"sobjects": sobjects})
        if rest == "/limits/recordCount":
            names = request.url.params.get("sObjects", "").split(",")
            return httpx.Response(200, json={"sObjects": [
                {"name": n, "count": self.objects[n].record_count}
                for n in names if n in self.objects
            ]})
        match = re.fullmatch(r"/sobjects/([A-Za-z0-9_]+)/describe", rest)
        if match:
            obj = self.objects.get(match.group(1))
            if obj is None:
                return _err(404, "NOT_FOUND", "The requested resource does not exist")
            return httpx.Response(200, json={"name": obj.name, "label": obj.label,
                                             "fields": obj.fields})
        if rest == "/query":
            return self._query(request, request.url.params.get("q", ""))
        match = re.fullmatch(r"/query/([a-f0-9]+)-(\d+)", rest)
        if match:
            return self._query_page(request, match.group(1), int(match.group(2)))
        if rest == "/jobs/query" and request.method == "POST":
            return self._bulk_create(json.loads(request.content)["query"])
        match = re.fullmatch(r"/jobs/query/([A-Za-z0-9]+)(/results)?", rest)
        if match:
            job = self.bulk_jobs.get(match.group(1))
            if job is None:
                return _err(404, "NOT_FOUND", "Job not found")
            if request.method == "PATCH":
                self.aborted.append(job["id"])
                job["state"] = "Aborted"
                return httpx.Response(200, json={"id": job["id"], "state": "Aborted"})
            if match.group(2):
                return self._bulk_results(request, job)
            return self._bulk_status(job)
        return _err(404, "NOT_FOUND", f"Unhandled fake route {request.method} {rest}")

    # -- OAuth ---------------------------------------------------------------
    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if form.get("grant_type") != "client_credentials" or not self.flow_enabled:
            return _oauth_err("unsupported_grant_type", "grant type not supported")
        if form.get("client_id") != self.client_id:
            return _oauth_err("invalid_client_id", "client identifier invalid")
        if form.get("client_secret") != self.client_secret:
            return _oauth_err("invalid_client", "invalid client credentials")
        if not self.run_as_set:
            return _oauth_err("invalid_grant", "no client credentials user enabled")
        token = f"00D5g!fake.{uuid.uuid4().hex}"
        self.live_tokens.add(token)
        self.tokens_issued += 1
        return httpx.Response(200, json={
            "access_token": token, "instance_url": f"https://{self.host}",
            "id": f"https://login.salesforce.com/id/{self.org_id}/0055g00000AbCdEAAV",
            "token_type": "Bearer", "issued_at": "1727517243000", "signature": "x",
        })

    # -- SOQL ----------------------------------------------------------------
    def _parse(self, soql: str) -> tuple[list[str], str]:
        match = re.fullmatch(r"SELECT (.+) FROM ([A-Za-z0-9_]+)", soql.strip())
        if not match:
            raise ValueError(f"unparseable SOQL: {soql}")
        return [f.strip() for f in match.group(1).split(",")], match.group(2)

    def _check_fields(self, obj: FakeObject, fields: list[str]) -> httpx.Response | None:
        by_name = {f["name"]: f for f in obj.fields}
        for name in fields:
            described = by_name.get(name)
            if described is None:
                return _err(400, "INVALID_FIELD", f"No such column '{name}' on {obj.name}")
            if described["type"] in ("address", "location", "base64"):
                return _err(400, "INVALID_FIELD",
                            f"Compound or binary field '{name}' can't be queried here")
        if obj.deny_records:
            return _err(403, "INSUFFICIENT_ACCESS",
                        f"insufficient access rights on object {obj.name}")
        return None

    def _query(self, request: httpx.Request, soql: str) -> httpx.Response:
        self.soql_seen.append(soql)
        fields, object_name = self._parse(soql)
        if object_name == "Organization":
            return httpx.Response(200, json={"totalSize": 1, "done": True, "records": [{
                "attributes": {"type": "Organization"}, "Id": self.org_id,
                "Name": self.org_name, "OrganizationType": "Enterprise Edition",
                "IsSandbox": self.sandbox,
            }]})
        obj = self.objects.get(object_name)
        if obj is None:
            return _err(400, "INVALID_TYPE", f"sObject type '{object_name}' is not supported")
        refused = self._check_fields(obj, fields)
        if refused:
            return refused
        batch = 2000
        options = request.headers.get("Sforce-Query-Options", "")
        if options.startswith("batchSize="):
            batch = int(options.split("=")[1])
        cursor = uuid.uuid4().hex[:16]
        self.cursors[cursor] = {"obj": obj, "fields": fields, "batch": batch}
        return self._query_page(request, cursor, 0)

    def _query_page(self, request: httpx.Request, cursor: str, offset: int) -> httpx.Response:
        state = self.cursors[cursor]
        obj, fields, batch = state["obj"], state["fields"], state["batch"]
        page = obj.rows[offset:offset + batch]
        records = [{"attributes": {"type": obj.name},
                    **{f: (row.get(f) or None) for f in fields}} for row in page]
        done = offset + batch >= len(obj.rows)
        body = {"totalSize": len(obj.rows), "done": done, "records": records}
        if not done:
            body["nextRecordsUrl"] = (
                f"/services/data/v{config.SF_API_VERSION}/query/{cursor}-{offset + batch}"
            )
        return httpx.Response(200, json=body)

    # -- Bulk API 2.0 ----------------------------------------------------------
    def _bulk_create(self, soql: str) -> httpx.Response:
        self.soql_seen.append(soql)
        fields, object_name = self._parse(soql)
        obj = self.objects.get(object_name)
        if obj is None:
            return _err(400, "INVALIDENTITY", f"sObject type '{object_name}' is not supported")
        if not obj.bulk:
            return _err(400, "API_ERROR",
                        f"Entity '{object_name}' is not supported by the Bulk API.")
        refused = self._check_fields(obj, fields)
        if refused:
            return refused
        job_id = f"750{uuid.uuid4().hex[:15]}"
        self.bulk_jobs[job_id] = {"id": job_id, "obj": obj, "fields": fields,
                                  "state": "UploadComplete", "polls": 0}
        return httpx.Response(200, json={"id": job_id, "state": "UploadComplete"})

    def _bulk_status(self, job: dict) -> httpx.Response:
        job["polls"] += 1
        if job["state"] != "Aborted":
            job["state"] = "InProgress" if job["polls"] == 1 else "JobComplete"
        processed = len(job["obj"].rows) if job["state"] == "JobComplete" else 0
        return httpx.Response(200, json={"id": job["id"], "state": job["state"],
                                         "numberRecordsProcessed": processed})

    def _bulk_results(self, request: httpx.Request, job: dict) -> httpx.Response:
        offset = int(request.url.params.get("locator") or 0)
        size = int(request.url.params.get("maxRecords") or 50_000)
        page = job["obj"].rows[offset:offset + size]
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(job["fields"])
        for row in page:
            writer.writerow([row.get(f, "") for f in job["fields"]])
        nxt = offset + size
        locator = str(nxt) if nxt < len(job["obj"].rows) else "null"
        return httpx.Response(
            200,
            content=out.getvalue().encode("utf-8"),
            headers={"Content-Type": "text/csv", "Sforce-Locator": locator,
                     "Sforce-NumberOfRecords": str(len(page))},
        )
