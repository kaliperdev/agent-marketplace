"""A map of the allowed datasets: which table holds what, its rows, its dates and
its main columns, so one call replaces the agent's exploring.

Measured on 2026-11-16: the agent (a small model) spent 1-3 calls looking around
before each query, and found the right table only when a person had written
"Your tables" notes. The map is built here instead, from table metadata (free)
plus one small MIN/MAX query per table with a known date column, kept for a day.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from bigquery_reader_agent.bigquery import BigQuery, BigQueryError, DatasetRef, TableRef, size_text
from bigquery_reader_agent.layouts import recognize

MAX_TABLES = 60          # in detail; names only beyond
MAX_COLUMNS = 12
MAX_MAPS = 20            # one per access (login, project, datasets); the least recently used is dropped
RANGE_SCAN_LIMIT = 200_000_000   # a date range is worth a little, never much
DATE_COLUMNS = ("data_date", "snapshot_date", "segments_date", "date_start", "date", "_DATA_DATE")
DATE_TYPES = {"DATE", "DATETIME", "TIMESTAMP"}
DAILY = re.compile(r"^(.*_)(\d{8})$")


@dataclass
class TableMap:
    text: str
    rows: list[list] = field(default_factory=list)
    partial: list[str] = field(default_factory=list)


@dataclass
class _Entry:
    name: str            # as shown: dataset.table (project.dataset.table for another project)
    ref: TableRef        # the table described: the newest day for daily tables
    days: list[str] = field(default_factory=list)   # YYYYMMDD, for daily tables
    line: str = ""
    columns: str = ""
    layout: str = ""
    facts: str = ""


def range_sql(table: str, column: str) -> str:
    return f"SELECT CAST(MIN(`{column}`) AS STRING), CAST(MAX(`{column}`) AS STRING) FROM `{table}`"


def _day(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


def _rows(n: int) -> str:
    return f"{n} row{'' if n == 1 else 's'}"


def _columns(detail) -> str:
    top = [c for c in detail.columns if "." not in c.name]
    marked = [c.name + ("[]" if c.type in ("RECORD", "STRUCT") and c.mode == "REPEATED"
                        else "{}" if c.type in ("RECORD", "STRUCT") else "") for c in top]
    shown = marked[:MAX_COLUMNS]
    if len(marked) > MAX_COLUMNS:
        shown.append(f"(+{len(marked) - MAX_COLUMNS} more)")
    return ", ".join(shown)


def _date_column(detail) -> str | None:
    types = {c.name: c.type for c in detail.columns if "." not in c.name}
    return next((c for c in DATE_COLUMNS if types.get(c) in DATE_TYPES), None)


def build_map(bq: BigQuery, datasets: tuple[DatasetRef, ...], max_bytes: int, budget_s: float = 20.0,
              workers: int = 8, now: Callable[[], datetime] = datetime.now) -> TableMap:
    deadline = time.monotonic() + budget_s
    problems, entries = [], []
    for dataset in datasets:
        try:
            found = bq.tables(dataset)
        except BigQueryError as error:
            problems.append(f"{dataset}: could not be listed. {error}")
            continue
        prefix = f"{dataset.dataset}." if dataset.project == bq.project else f"{dataset}."
        groups: dict[str, list] = {}
        for t in found:
            match = DAILY.match(t.ref.table)
            key = f"{prefix}{match.group(1)}*" if match else f"{prefix}{t.ref.table}"
            groups.setdefault(key, []).append((match.group(2) if match else "", t.ref))
        for key, members in groups.items():
            if len(members) > 1:
                members.sort()
                entries.append(_Entry(key, members[-1][1], [d for d, _ in members]))
            else:
                entries.append(_Entry(key, members[0][1]))
    detailed, rest = entries[:MAX_TABLES], entries[MAX_TABLES:]
    range_limit = min(max_bytes, RANGE_SCAN_LIMIT)

    def describe(entry: _Entry) -> bool:
        if time.monotonic() >= deadline:
            return False
        try:
            detail = bq.table(entry.ref)
        except BigQueryError as error:
            entry.facts = f"could not be described: {error}"
            return True
        layout = recognize(entry.ref.table, [c.name for c in detail.columns if "." not in c.name])
        entry.layout = layout.name if layout else ""
        entry.columns = _columns(detail)
        if entry.days:
            entry.facts = (f"{len(entry.days)} daily tables, {_day(entry.days[0])} to {_day(entry.days[-1])}, "
                           f"newest day {_rows(detail.rows)}")
            return True
        entry.facts = _rows(detail.rows)
        column = _date_column(detail)
        if column and time.monotonic() < deadline:
            sql = range_sql(str(entry.ref), column)
            try:
                dry = bq.dry_run(sql)
                if dry.bytes > range_limit:
                    entry.facts += f", dates not read (would scan {size_text(dry.bytes)})"
                else:
                    low, high = bq.query(sql, range_limit, 1, wait_seconds=max(1.0, deadline - time.monotonic())).rows[0]
                    if low:
                        entry.facts += f", {column} {str(low)[:10]} to {str(high)[:10]}"
            except Exception:  # a date range is a nicety: no failure of it may cost the map
                entry.facts += ", dates not read"
        return True

    with ThreadPoolExecutor(max_workers=workers) as pool:
        reached = list(pool.map(describe, detailed))
    partial = [e.name for e, ok in zip(detailed, reached) if not ok]

    count = len(datasets)
    lines = [f"Map of {count} dataset{'' if count == 1 else 's'}, built {now():%Y-%m-%d %H:%M} "
             "(kept for a day; refresh=true rebuilds it)", *problems]
    rows = []
    for entry, ok in zip(detailed, reached):
        if not ok:
            continue
        lines.append(f"{entry.name}\t{entry.layout}\t{entry.facts}")
        if entry.columns:
            lines.append(f"  columns: {entry.columns}")
        rows.append([entry.name, entry.layout, entry.facts, entry.columns])
    if rest:
        lines.append(f"{len(rest)} more tables, names only (describe them to see more): "
                     + ", ".join(e.name for e in rest))
    if partial:
        lines.append("Not reached in time (describe them instead): " + ", ".join(partial))
    return TableMap("\n".join(lines), rows, partial)


class MapCache:
    """One map, kept for `ttl_s`; a partial map is never kept, so the next call tries again."""

    def __init__(self, build: Callable[[], TableMap] | None = None, ttl_s: float = 86_400,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._build, self._ttl, self._clock = build, ttl_s, clock
        self._lock = threading.Lock()
        self._held = None
        self._at = 0.0

    def get(self, refresh: bool = False, build: Callable[[], TableMap] | None = None):
        """A build the caller gives wins, so a map rebuilt after a day uses that call's pass."""
        with self._lock:
            if refresh or self._held is None or self._clock() - self._at > self._ttl:
                made = (build or self._build)()
                if getattr(made, "partial", []):
                    return made
                self._held, self._at = made, self._clock()
            return self._held


class MapCaches:
    """The table maps of the accesses seen lately, one per access: the least recently used past `size` is dropped."""

    def __init__(self, size: int = MAX_MAPS) -> None:
        self._size = size
        self._lock = threading.Lock()
        self._caches: OrderedDict[tuple, MapCache] = OrderedDict()

    def cache(self, key: tuple) -> MapCache:
        with self._lock:
            found = self._caches.get(key)
            if found is None:
                found = self._caches[key] = MapCache()
                while len(self._caches) > self._size:
                    self._caches.popitem(last=False)
            else:
                self._caches.move_to_end(key)
            return found
