"""The table map: one call tells the agent which table holds what, its dates and main columns."""

from datetime import datetime

import httpx

from bigquery_reader_agent.bigquery import BigQuery, DatasetRef
from bigquery_reader_agent.tablemap import MapCache, build_map, range_sql
from bq_fake import FakeBigQuery, cells

PROJECT = "rytangle-demo"
DS = DatasetRef(PROJECT, "web")
GB = 1_000_000_000
NOW = lambda: datetime(2026, 11, 16, 9, 10)  # noqa: E731

GSC_SCHEMA = [{"name": n, "type": t, "mode": "NULLABLE"} for n, t in (
    ("data_date", "DATE"), ("site_url", "STRING"), ("query", "STRING"), ("country", "STRING"),
    ("device", "STRING"), ("impressions", "INTEGER"), ("clicks", "INTEGER"), ("sum_top_position", "FLOAT"))]
GA4_SCHEMA = [
    {"name": "event_date", "type": "STRING", "mode": "NULLABLE"},
    {"name": "event_name", "type": "STRING", "mode": "NULLABLE"},
    {"name": "event_params", "type": "RECORD", "mode": "REPEATED", "fields": [
        {"name": "key", "type": "STRING", "mode": "NULLABLE"}]},
    {"name": "device", "type": "RECORD", "mode": "NULLABLE", "fields": [
        {"name": "category", "type": "STRING", "mode": "NULLABLE"}]},
]
GSC = f"{PROJECT}.web.searchdata_site_impression"
RANGE = {"schema": [{"name": "f0_", "type": "STRING", "mode": "NULLABLE"},
                    {"name": "f1_", "type": "STRING", "mode": "NULLABLE"}]}


def bq(fake: FakeBigQuery) -> BigQuery:
    return BigQuery(httpx.Client(transport=httpx.MockTransport(fake.handler)), lambda: "tok", PROJECT)


def with_gsc(fake: FakeBigQuery, scan: int = 1_000) -> None:
    fake.add_table(GSC, GSC_SCHEMA, numRows="500")
    sql = range_sql(GSC, "data_date")
    fake.dry_runs[sql] = {"statementType": "SELECT", "tables": [GSC], "bytes": scan}
    fake.results[sql] = {**RANGE, "rows": [cells("2026-11-11", "2026-11-11")]}


def ran_queries(fake: FakeBigQuery) -> int:
    return sum(1 for r in fake.requests if r.url.path.endswith("/queries"))


def test_a_table_line_has_its_layout_rows_date_range_and_columns():
    fake = FakeBigQuery()
    with_gsc(fake)
    made = build_map(bq(fake), (DS,), GB, now=NOW)
    assert made.text.splitlines()[0].startswith("Map of 1 dataset, built 2026-11-16 09:10")
    assert ("web.searchdata_site_impression\tSearch Console export\t500 rows, data_date 2026-11-11 to 2026-11-11"
            in made.text)
    assert "columns: data_date, site_url, query, country, device, impressions, clicks, sum_top_position" in made.text
    assert made.rows[0][:2] == ["web.searchdata_site_impression", "Search Console export"]


def test_daily_tables_take_their_range_from_names_and_size_from_the_newest_day_without_a_query():
    fake = FakeBigQuery()
    for day, n in (("20261112", "60"), ("20261113", "62"), ("20261114", "66")):
        fake.add_table(f"{PROJECT}.web.events_{day}", GA4_SCHEMA, numRows=n)
    made = build_map(bq(fake), (DS,), GB, now=NOW)
    assert ("web.events_*\tGoogle Analytics export\t3 daily tables, 2026-11-12 to 2026-11-14, newest day 66 rows"
            in made.text)
    assert ran_queries(fake) == 0
    described = [r.url.path for r in fake.requests if "/tables/" in r.url.path]
    assert described == [f"/bigquery/v2/projects/{PROJECT}/datasets/web/tables/events_20261114"]


def test_lists_and_records_are_marked_in_the_columns():
    fake = FakeBigQuery()
    fake.add_table(f"{PROJECT}.web.events_20261112", GA4_SCHEMA)
    assert "columns: event_date, event_name, event_params[], device{}" in build_map(bq(fake), (DS,), GB, now=NOW).text


def test_more_than_twelve_columns_are_counted_not_listed():
    fake = FakeBigQuery()
    fake.add_table(f"{PROJECT}.web.wide", [{"name": f"c{i}", "type": "STRING", "mode": "NULLABLE"} for i in range(20)])
    text = build_map(bq(fake), (DS,), GB, now=NOW).text
    assert "c11, (+8 more)" in text and "c12" not in text


def test_a_range_that_would_scan_too_much_is_left_out_and_said():
    fake = FakeBigQuery()
    with_gsc(fake, scan=300_000_000)
    text = build_map(bq(fake), (DS,), GB, now=NOW).text
    assert "dates not read (would scan 300 MB)" in text
    assert ran_queries(fake) == 0


def test_the_scan_limit_also_caps_the_range_query():
    fake = FakeBigQuery()
    with_gsc(fake, scan=60_000_000)
    assert "dates not read" in build_map(bq(fake), (DS,), 50_000_000, now=NOW).text


def test_a_table_without_a_date_column_gets_no_range_and_no_query():
    fake = FakeBigQuery()
    fake.add_table(f"{PROJECT}.web.domain_overview", [{"name": "domain", "type": "STRING", "mode": "NULLABLE"}],
                   numRows="1")
    text = build_map(bq(fake), (DS,), GB, now=NOW).text
    assert "web.domain_overview\t\t1 row" in text and ran_queries(fake) == 0


def test_a_refused_dataset_is_named_and_the_rest_still_mapped():
    fake = FakeBigQuery()
    with_gsc(fake)
    fake.refuse_datasets.add(f"{PROJECT}.secret")
    text = build_map(bq(fake), (DS, DatasetRef(PROJECT, "secret")), GB, now=NOW).text
    assert "secret: could not be listed" in text and "searchdata_site_impression" in text


def test_beyond_sixty_tables_only_names_are_listed():
    fake = FakeBigQuery()
    for i in range(65):
        fake.add_table(f"{PROJECT}.web.t{i:02d}", [{"name": "x", "type": "STRING", "mode": "NULLABLE"}])
    made = build_map(bq(fake), (DS,), GB, now=NOW)
    assert "5 more tables, names only (describe them to see more): web.t60, web.t61, web.t62, web.t63, web.t64" in made.text
    assert sum(1 for r in fake.requests if "/tables/" in r.url.path) == 60


def test_out_of_time_the_map_is_partial_and_names_what_it_did_not_reach():
    fake = FakeBigQuery()
    with_gsc(fake)
    made = build_map(bq(fake), (DS,), GB, budget_s=0, now=NOW)
    assert made.partial == ["web.searchdata_site_impression"]
    assert "Not reached in time (describe them instead): web.searchdata_site_impression" in made.text


def test_the_cache_is_reused_within_a_day_and_rebuilt_after_it_or_on_refresh():
    built, clock = [], [0.0]
    cache = MapCache(lambda: built.append(1) or f"map {len(built)}", ttl_s=86_400, clock=lambda: clock[0])
    assert cache.get() == "map 1" and cache.get() == "map 1"
    clock[0] = 86_401
    assert cache.get() == "map 2"
    assert cache.get(refresh=True) == "map 3"


def test_a_partial_map_is_not_kept():
    calls = []
    cache = MapCache(lambda: calls.append(1) or type("M", (), {"partial": ["x"]})(), clock=lambda: 0.0)
    cache.get()
    cache.get()
    assert len(calls) == 2
