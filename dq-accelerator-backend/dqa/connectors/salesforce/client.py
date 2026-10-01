"""The Salesforce API calls the accelerator makes. Reads only.

Nothing here creates, updates or deletes Salesforce data. The one write-shaped
call, `bulk_abort`, cancels an export job we started ourselves.
"""
from __future__ import annotations

from typing import Callable, Iterator, Optional

import httpx

from ... import config

# token_provider(force_new) -> access token. force_new re-issues after a 401.
TokenProvider = Callable[[bool], str]


def new_http_client() -> httpx.Client:
    """The HTTP client every Salesforce call goes through.

    A module-level factory so the tests can substitute a fake Salesforce.
    Redirects are not followed: a redirect off Salesforce's domain would
    carry the bearer token with it.
    """
    return httpx.Client(timeout=config.SF_HTTP_TIMEOUT, follow_redirects=False)


class SalesforceApiError(Exception):
    """An error response from a Salesforce API, with Salesforce's own code."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    @classmethod
    def from_response(cls, response: httpx.Response) -> "SalesforceApiError":
        code, message = "UNKNOWN_ERROR", f"HTTP {response.status_code}"
        try:
            body = response.json()
        except ValueError:
            body = None
        # Salesforce returns either a list of {errorCode, message} or a dict.
        if isinstance(body, list) and body and isinstance(body[0], dict):
            code = body[0].get("errorCode", code)
            message = body[0].get("message", message)
        elif isinstance(body, dict):
            code = body.get("errorCode") or body.get("error") or code
            message = body.get("message") or body.get("error_description") or message
        return cls(response.status_code, code, message)


class SalesforceClient:
    def __init__(self, http: httpx.Client, instance_url: str, token_provider: TokenProvider):
        self.http = http
        self.instance_url = instance_url
        self._token = token_provider

    # -- plumbing ------------------------------------------------------------
    def _url(self, path: str) -> str:
        if path.startswith("https://"):
            return path
        return f"{self.instance_url}{path}"

    @staticmethod
    def api(path: str) -> str:
        return f"/services/data/v{config.SF_API_VERSION}{path}"

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> httpx.Response:
        """One call, re-authenticating once if the token has expired.

        Client credentials tokens last as long as the Run As user's session
        timeout. A long export can outlive that, so a 401 re-issues the token
        from the stored credentials and retries once.
        """
        for attempt in (0, 1):
            merged = {"Authorization": f"Bearer {self._token(attempt == 1)}",
                      "Accept": "application/json"}
            merged.update(headers or {})
            response = self.http.request(
                method, self._url(path), params=params, json=json, headers=merged
            )
            if response.status_code == 401 and attempt == 0:
                continue
            if response.status_code >= 400:
                raise SalesforceApiError.from_response(response)
            return response
        raise AssertionError("unreachable")  # pragma: no cover

    def _get_json(self, path: str, params: Optional[dict] = None) -> dict:
        return self.request("GET", path, params=params).json()

    # -- identity --------------------------------------------------------------
    def userinfo(self) -> dict:
        """The Run As user: preferred_username, organization_id, user_id."""
        return self._get_json("/services/oauth2/userinfo")

    def organization(self) -> dict:
        page = self._get_json(
            self.api("/query"),
            {"q": "SELECT Id, Name, OrganizationType, IsSandbox FROM Organization"},
        )
        records = page.get("records") or [{}]
        return records[0]

    # -- metadata --------------------------------------------------------------
    def describe_global(self) -> list[dict]:
        return self._get_json(self.api("/sobjects")).get("sobjects", [])

    def describe(self, object_name: str) -> dict:
        return self._get_json(self.api(f"/sobjects/{object_name}/describe"))

    def record_counts(self, names: list[str]) -> dict[str, int]:
        """Salesforce's approximate counts. Missing objects are simply absent."""
        counts: dict[str, int] = {}
        # Chunked: the names travel in the query string.
        for start in range(0, len(names), 100):
            chunk = names[start:start + 100]
            try:
                body = self._get_json(
                    self.api("/limits/recordCount"), {"sObjects": ",".join(chunk)}
                )
            except SalesforceApiError:
                continue  # counts are a nicety; the picker works without them
            for item in body.get("sObjects", []):
                counts[item["name"]] = int(item.get("count", 0))
        return counts

    # -- query API (fallback path) ---------------------------------------------
    def query_pages(self, soql: str) -> Iterator[dict]:
        headers = {"Sforce-Query-Options": f"batchSize={config.SF_REST_BATCH_SIZE}"}
        response = self.request("GET", self.api("/query"), params={"q": soql}, headers=headers)
        page = response.json()
        yield page
        while not page.get("done", True) and page.get("nextRecordsUrl"):
            page = self.request("GET", page["nextRecordsUrl"], headers=headers).json()
            yield page

    # -- Bulk API 2.0 (primary path) -------------------------------------------
    def bulk_create(self, soql: str) -> str:
        body = self.request(
            "POST",
            self.api("/jobs/query"),
            json={
                "operation": "query",
                "query": soql,
                "contentType": "CSV",
                "columnDelimiter": "COMMA",
                "lineEnding": "LF",
            },
        ).json()
        return body["id"]

    def bulk_status(self, job_id: str) -> dict:
        return self._get_json(self.api(f"/jobs/query/{job_id}"))

    def bulk_results(
        self, job_id: str, locator: Optional[str], max_records: int
    ) -> tuple[bytes, Optional[str], Optional[int]]:
        """One page of CSV: (bytes, next locator or None, records in page)."""
        params: dict = {"maxRecords": max_records}
        if locator:
            params["locator"] = locator
        response = self.request(
            "GET",
            self.api(f"/jobs/query/{job_id}/results"),
            params=params,
            headers={"Accept": "text/csv"},
        )
        next_locator = response.headers.get("Sforce-Locator")
        if not next_locator or next_locator == "null":
            next_locator = None
        count = response.headers.get("Sforce-NumberOfRecords")
        return response.content, next_locator, int(count) if count else None

    def bulk_abort(self, job_id: str) -> None:
        try:
            self.request("PATCH", self.api(f"/jobs/query/{job_id}"), json={"state": "Aborted"})
        except (SalesforceApiError, httpx.HTTPError):
            pass  # already finished, or gone; nothing to clean up
