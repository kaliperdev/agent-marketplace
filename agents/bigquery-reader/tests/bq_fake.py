"""BigQuery's REST API, as far as this agent asks it, for httpx.MockTransport.

Shaped like Google's answers as its documentation describes them (not recorded
from a live project): numbers arrive as strings, a row is {"f": [{"v": ...}]},
a RECORD cell is {"v": {"f": [...]}} and a REPEATED cell is {"v": [{"v": ...}]}.
A query that is still running answers jobComplete false and is fetched again
with getQueryResults.
"""

from __future__ import annotations

import json

import httpx

ACCESS_DENIED = "Access Denied: Dataset {}: Permission bigquery.tables.list denied"


def cells(*values) -> dict:
    """One row in BigQuery's wire form, from plain values (None stays null)."""
    return {"f": [{"v": None if v is None else str(v)} for v in values]}


class FakeBigQuery:
    def __init__(self) -> None:
        # "project.dataset.table" -> {"schema": [...fields], "rows": [wire rows], "numRows", "numBytes",
        #                              "lastModifiedTime", "timePartitioning", "description", "creationTime"}
        self.tables: dict[str, dict] = {}
        # sql -> {"statementType", "tables": ["p.d.t", ...], "bytes": int}
        self.dry_runs: dict[str, dict] = {}
        # sql -> {"schema": [...fields], "rows": [wire rows], "bytes": int}
        self.results: dict[str, dict] = {}
        # sql -> (status, message): BigQuery refusing the statement
        self.errors: dict[str, tuple[int, str]] = {}
        self.pending_polls = 0
        self.refuse_datasets: set[str] = set()
        self.page_size = 1000
        self.job_location: str | None = "US"
        self.requests: list[httpx.Request] = []

    # -- helpers ---------------------------------------------------------
    def add_table(self, ref: str, schema: list[dict], rows: list[dict] | None = None, **meta) -> None:
        self.tables[ref] = {"schema": schema, "rows": rows or [], **meta}

    def _error(self, status: int, message: str) -> httpx.Response:
        return httpx.Response(status, json={"error": {"code": status, "message": message}})

    def _result(self, sql: str, max_results: int) -> dict:
        result = self.results[sql]
        rows = result["rows"]
        body = {"jobComplete": True, "jobReference": {"projectId": "p", "jobId": "job-1", "location": "US"},
                "schema": {"fields": result["schema"]}, "rows": rows[:max_results],
                "totalRows": str(len(rows)), "totalBytesProcessed": str(result.get("bytes", 0))}
        if result.get("errors"):
            body["errors"] = result["errors"]
        if len(rows) > max_results:
            body["pageToken"] = "next"
        return body

    # -- the transport ---------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        parts = path.split("/")
        # /bigquery/v2/projects/{p}/...
        project = parts[4] if len(parts) > 4 else ""
        if request.method == "POST" and path.endswith("/jobs"):
            config = json.loads(request.content)["configuration"]
            sql = config["query"]["query"]
            if sql in self.errors:
                return self._error(*self.errors[sql])
            dry = self.dry_runs[sql]
            referenced = [dict(zip(("projectId", "datasetId", "tableId"), t.split("."))) for t in dry["tables"]]
            return httpx.Response(200, json={"statistics": {
                "totalBytesProcessed": str(dry["bytes"]),
                "query": {"statementType": dry["statementType"], "referencedTables": referenced,
                          "totalBytesProcessed": str(dry["bytes"])}}})
        if request.method == "POST" and path.endswith("/queries"):
            body = json.loads(request.content)
            sql = body["query"]
            if sql in self.errors:
                return self._error(*self.errors[sql])
            self._last = (sql, int(body.get("maxResults", 1000)))
            if self.pending_polls:
                reference = {"projectId": project, "jobId": "job-1"}
                if self.job_location:
                    reference["location"] = self.job_location
                return httpx.Response(200, json={"jobComplete": False, "jobReference": reference})
            return httpx.Response(200, json=self._result(sql, self._last[1]))
        if request.method == "GET" and "/queries/" in path:
            if self.pending_polls:
                self.pending_polls -= 1
                if self.pending_polls:
                    return httpx.Response(200, json={"jobComplete": False})
            return httpx.Response(200, json=self._result(*self._last))
        if request.method == "GET" and "/datasets/" in path:
            dataset = parts[6]
            if f"{project}.{dataset}" in self.refuse_datasets:
                return self._error(403, ACCESS_DENIED.format(f"{project}:{dataset}"))
            if path.endswith("/tables"):
                names = sorted(k for k in self.tables if k.startswith(f"{project}.{dataset}."))
                start = int(request.url.params.get("pageToken") or 0)
                page = names[start:start + self.page_size]
                body = {"tables": [{"tableReference": dict(zip(("projectId", "datasetId", "tableId"), n.split("."))),
                                    "type": self.tables[n].get("type", "TABLE"),
                                    "creationTime": self.tables[n].get("creationTime", "1604188800000")}
                                   for n in page]}
                if start + self.page_size < len(names):
                    body["nextPageToken"] = str(start + self.page_size)
                return httpx.Response(200, json=body)
            table = parts[8]
            ref = f"{project}.{dataset}.{table}"
            if ref not in self.tables:
                return self._error(404, f"Not found: Table {project}:{dataset}.{table}")
            t = self.tables[ref]
            if path.endswith("/data"):
                limit = int(request.url.params.get("maxResults") or 100)
                return httpx.Response(200, json={"rows": t["rows"][:limit], "totalRows": str(len(t["rows"]))})
            meta = {k: v for k, v in t.items() if k not in ("schema", "rows")}
            return httpx.Response(200, json={
                "tableReference": {"projectId": project, "datasetId": dataset, "tableId": table},
                "schema": {"fields": t["schema"]}, "numRows": str(len(t["rows"])), **meta})
        return self._error(404, f"no such route {request.method} {path}")
