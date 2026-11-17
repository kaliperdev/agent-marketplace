"""Read-only BigQuery over its REST API: list tables, describe one, preview its
rows, dry-run a query, run a query with a cap on how much it may scan. Nothing
here writes; whether a statement is allowed is decided by the tools, from the
dry run.

Written from Google's documentation, not recorded from a live project
(base https://bigquery.googleapis.com/bigquery/v2):
  GET  /projects/{p}/datasets/{d}/tables             tables.list (paged)
  GET  /projects/{p}/datasets/{d}/tables/{t}         tables.get
  GET  /projects/{p}/datasets/{d}/tables/{t}/data    tabledata.list (no query, free)
  POST /projects/{p}/jobs  {configuration: {dryRun}} a dry run: type, tables, bytes
  POST /projects/{p}/queries                         jobs.query
  GET  /projects/{p}/queries/{jobId}                 getQueryResults, while not complete
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

API = "https://bigquery.googleapis.com/bigquery/v2"
SCOPE = "https://www.googleapis.com/auth/bigquery"
GB = 1_000_000_000
POLL_MS = 10_000

TokenProvider = Callable[[], str]


class BigQueryError(RuntimeError):
    """A request was refused or unusable. The message is what the model reads."""


@dataclass(frozen=True)
class DatasetRef:
    project: str
    dataset: str

    def __str__(self) -> str:
        return f"{self.project}.{self.dataset}"


@dataclass(frozen=True)
class TableRef:
    project: str
    dataset: str
    table: str

    def __str__(self) -> str:
        return f"{self.project}.{self.dataset}.{self.table}"

    @property
    def dataset_ref(self) -> DatasetRef:
        return DatasetRef(self.project, self.dataset)


@dataclass(frozen=True)
class TableInfo:
    ref: TableRef
    kind: str      # TABLE, VIEW, EXTERNAL...
    created: str   # YYYY-MM-DD


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    mode: str
    description: str = ""


@dataclass(frozen=True)
class TableDetail:
    ref: TableRef
    columns: list[Column]
    partition: str
    description: str
    rows: int
    size_bytes: int
    modified: str


@dataclass(frozen=True)
class DryRun:
    statement_type: str
    tables: list[TableRef]
    bytes: int


@dataclass(frozen=True)
class Rows:
    columns: list[str]
    rows: list[list]
    truncated: bool
    bytes_scanned: int = 0
    total_rows: int = field(default=0)


def size_text(n: int) -> str:
    for unit, scale in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= scale:
            return f"{round(n / scale, 1):g} {unit}"
    return f"{n} bytes"


def parse_datasets(text: str, project: str) -> tuple[DatasetRef, ...]:
    """The datasets the agent may read: `dataset` (in `project`) or `project.dataset`."""
    found = []
    for item in text.replace(",", " ").split():
        owner, _, name = item.rpartition(".")
        found.append(DatasetRef(owner or project, name))
    if not found:
        raise ValueError("no datasets given")
    return tuple(dict.fromkeys(found))


def parse_max_bytes(text: str | None) -> int:
    """The scan cap in bytes, from a number of GB; 1 GB when blank."""
    if text is None or not text.strip():
        return GB
    try:
        gb = float(text)
    except ValueError:
        raise ValueError(f"not a number of GB: {text!r}") from None
    if not gb > 0:
        raise ValueError(f"must be more than 0 GB: {text!r}")
    return int(gb * GB)


def _day(millis: str | None) -> str:
    if not millis:
        return ""
    return datetime.fromtimestamp(int(millis) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _flatten(fields: list[dict], prefix: str = "") -> list[Column]:
    columns = []
    for f in fields:
        name = f"{prefix}{f['name']}"
        columns.append(Column(name, f.get("type", ""), f.get("mode", "NULLABLE"), f.get("description", "")))
        if f.get("fields"):
            columns.extend(_flatten(f["fields"], f"{name}."))
    return columns


def _scalar(kind: str, value):
    if value is None:
        return None
    if kind in ("INTEGER", "INT64"):
        return int(value)
    if kind in ("FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"):
        return float(value)
    if kind in ("BOOLEAN", "BOOL"):
        return str(value).lower() == "true"
    if kind == "TIMESTAMP":
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    return value


def _cell(f: dict, value):
    if f.get("mode") == "REPEATED":
        return [_cell({**f, "mode": "NULLABLE"}, item.get("v")) for item in (value or [])]
    if f.get("type") in ("RECORD", "STRUCT"):
        if value is None:
            return None
        return {sub["name"]: _cell(sub, c.get("v")) for sub, c in zip(f["fields"], value["f"])}
    return _scalar(f.get("type", ""), value)


def _decode(fields: list[dict], rows: list[dict]) -> list[list]:
    return [[_cell(f, c.get("v")) for f, c in zip(fields, row["f"])] for row in rows]


def _partition(table: dict) -> str:
    time_part = table.get("timePartitioning")
    if time_part:
        on = time_part.get("field") or "_PARTITIONTIME (load time)"
        return f"by {time_part.get('type', 'DAY').lower()} on {on}"
    if table.get("rangePartitioning"):
        return f"by range on {table['rangePartitioning'].get('field', '')}"
    return ""


class BigQuery:
    def __init__(self, client: httpx.Client, token_provider: TokenProvider, project: str) -> None:
        self._client = client
        self._token = token_provider
        self.project = project

    def _call(self, method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict:
        try:
            response = self._client.request(method, f"{API}{path}", json=body, params=params,
                                            headers={"authorization": f"Bearer {self._token()}"})
        except httpx.HTTPError as error:
            raise BigQueryError(f"BigQuery could not be reached: {error}") from None
        if response.status_code >= 400:
            try:
                said = str(response.json()["error"]["message"])
            except (ValueError, KeyError, TypeError):
                said = response.text[:300]
            raise BigQueryError(f"BigQuery refused ({response.status_code}): {said}")
        return response.json()

    @staticmethod
    def _table_path(ref: TableRef) -> str:
        return f"/projects/{ref.project}/datasets/{ref.dataset}/tables/{ref.table}"

    def tables(self, dataset: DatasetRef) -> list[TableInfo]:
        found, token = [], None
        while True:
            data = self._call("GET", f"/projects/{dataset.project}/datasets/{dataset.dataset}/tables",
                              params={"maxResults": 1000, **({"pageToken": token} if token else {})})
            for t in data.get("tables", []):
                r = t["tableReference"]
                found.append(TableInfo(TableRef(r["projectId"], r["datasetId"], r["tableId"]),
                                       t.get("type", "TABLE"), _day(t.get("creationTime"))))
            token = data.get("nextPageToken")
            if not token:
                return found

    def table(self, ref: TableRef) -> TableDetail:
        data = self._call("GET", self._table_path(ref))
        return TableDetail(ref, _flatten(data.get("schema", {}).get("fields", [])), _partition(data),
                           data.get("description", ""), int(data.get("numRows") or 0),
                           int(data.get("numBytes") or 0), _day(data.get("lastModifiedTime")))

    def preview(self, ref: TableRef, limit: int) -> Rows:
        fields = self._call("GET", self._table_path(ref)).get("schema", {}).get("fields", [])
        data = self._call("GET", f"{self._table_path(ref)}/data", params={"maxResults": limit})
        rows = _decode(fields, data.get("rows", []))
        total = int(data.get("totalRows") or len(rows))
        return Rows([f["name"] for f in fields], rows, total > len(rows), 0, total)

    def dry_run(self, sql: str) -> DryRun:
        data = self._call("POST", f"/projects/{self.project}/jobs", {
            "configuration": {"dryRun": True, "query": {"query": sql, "useLegacySql": False}}})
        stats = data.get("statistics", {})
        query = stats.get("query", {})
        tables = [TableRef(t["projectId"], t["datasetId"], t["tableId"]) for t in query.get("referencedTables", [])]
        return DryRun(query.get("statementType", ""), tables,
                      int(query.get("totalBytesProcessed") or stats.get("totalBytesProcessed") or 0))

    def query(self, sql: str, max_bytes: int, max_rows: int, wait_seconds: float = 45) -> Rows:
        deadline = time.monotonic() + wait_seconds
        data = self._call("POST", f"/projects/{self.project}/queries", {
            "query": sql, "useLegacySql": False, "maximumBytesBilled": str(max_bytes),
            "maxResults": max_rows, "timeoutMs": min(POLL_MS, int(wait_seconds * 1000))})
        job = data.get("jobReference", {})
        while not data.get("jobComplete"):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BigQueryError(
                    f"The query took too long (more than {wait_seconds:g} seconds) and was left running in "
                    "BigQuery. Narrow the dates or add filters and try again.")
            params = {"maxResults": max_rows, "timeoutMs": min(POLL_MS, int(remaining * 1000))}
            if job.get("location"):
                params["location"] = job["location"]
            data = self._call("GET", f"/projects/{job.get('projectId', self.project)}/queries/{job.get('jobId')}",
                              params=params)
        if data.get("errors") and not data.get("rows"):
            # A finished job that failed must never read as "no matches".
            raise BigQueryError(f"BigQuery refused: {data['errors'][0].get('message', 'the query failed')}")
        fields = data.get("schema", {}).get("fields", [])
        rows = _decode(fields, data.get("rows", []))[:max_rows]
        total = int(data.get("totalRows") or len(rows))
        return Rows([f["name"] for f in fields], rows, bool(data.get("pageToken")) or total > len(rows),
                    int(data.get("totalBytesProcessed") or 0), total)
