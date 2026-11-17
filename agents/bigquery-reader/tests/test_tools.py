import json

import httpx

from bigquery_reader_agent.bigquery import BigQuery, DatasetRef
from bigquery_reader_agent.tools import make_bq_tools
from bq_fake import FakeBigQuery, cells

PROJECT = "rytangle-demo"
ALLOWED = (DatasetRef(PROJECT, "marketing_test"),)
STATS = f"{PROJECT}.marketing_test.ads_CampaignBasicStats_1"
OUTSIDE = f"{PROJECT}.private.salaries"
SCHEMA = [{"name": "campaign_name", "type": "STRING", "mode": "NULLABLE"},
          {"name": "cost", "type": "FLOAT", "mode": "NULLABLE"}]
GB = 1_000_000_000


def tools(fake: FakeBigQuery, datasets=ALLOWED, max_bytes=GB) -> dict:
    bq = BigQuery(httpx.Client(transport=httpx.MockTransport(fake.handler)), lambda: "tok", PROJECT)
    return {t.name: t for t in make_bq_tools(bq, datasets, max_bytes)}


def call(fake, name, *args, **kw):
    return tools(fake, **kw)[name].func(*args)  # .func: the tuple a content_and_artifact tool returns


def ran_a_query(fake: FakeBigQuery) -> bool:
    return any(r.url.path.endswith("/queries") for r in fake.requests)


def select(fake, sql, tables, size=1000, rows=None):
    fake.dry_runs[sql] = {"statementType": "SELECT", "tables": tables, "bytes": size}
    fake.results[sql] = {"schema": SCHEMA, "rows": rows if rows is not None else [cells("Brand", 12.5)],
                         "bytes": size}


def test_the_tools_are_the_four_read_only_ones():
    assert sorted(tools(FakeBigQuery())) == ["bq_describe_table", "bq_list_tables", "bq_preview_table", "bq_run_query"]


def test_list_tables_covers_every_allowed_dataset():
    fake = FakeBigQuery()
    fake.add_table(STATS, SCHEMA)
    fake.add_table("other-project.ga4.events_intraday", SCHEMA)
    datasets = ALLOWED + (DatasetRef("other-project", "ga4"),)
    text, rows = call(fake, "bq_list_tables", datasets=datasets)
    assert text.startswith("Map of 2 datasets")
    assert "marketing_test.ads_CampaignBasicStats_1\t" in text and "other-project.ga4.events_intraday\t" in text
    assert rows["rows"] == [] and rows["columns"] == []  # a look-up never becomes a report


def test_list_tables_groups_daily_tables_into_one_line():
    fake = FakeBigQuery()
    for day in ("20201101", "20201102", "20210131"):
        fake.add_table(f"{PROJECT}.marketing_test.events_{day}", SCHEMA)
    text, rows = call(fake, "bq_list_tables")
    assert "marketing_test.events_*" in text
    assert "3 daily tables, 2020-11-01 to 2021-01-31" in text
    assert rows["rows"] == []


def test_list_tables_names_a_refused_dataset_and_still_lists_the_rest():
    fake = FakeBigQuery()
    fake.add_table(STATS, SCHEMA)
    fake.refuse_datasets.add("rytangle-demo.secret")
    text, _ = call(fake, "bq_list_tables", datasets=ALLOWED + (DatasetRef(PROJECT, "secret"),))
    assert "marketing_test.ads_CampaignBasicStats_1" in text
    assert "rytangle-demo.secret" in text and "Access Denied" in text


def test_describe_table_outside_the_allowed_datasets_is_refused_in_words():
    fake = FakeBigQuery()
    fake.add_table(OUTSIDE, SCHEMA)
    text, rows = call(fake, "bq_describe_table", "private.salaries")
    assert "not in a dataset this agent may read" in text and "rytangle-demo.marketing_test" in text
    assert rows["rows"] == [] and fake.requests == []


def test_describe_table_shows_partitioning_and_dotted_nested_columns():
    fake = FakeBigQuery()
    fake.add_table(f"{PROJECT}.marketing_test.events_20201101", [
        {"name": "event_date", "type": "STRING", "mode": "NULLABLE"},
        {"name": "event_params", "type": "RECORD", "mode": "REPEATED", "fields": [
            {"name": "key", "type": "STRING", "mode": "NULLABLE"}]}],
        timePartitioning={"type": "DAY", "field": "event_date"}, numBytes="5000000")
    text, rows = call(fake, "bq_describe_table", "marketing_test.events_20201101")
    assert "partitioned by day on event_date" in text
    assert "event_params.key" in text and "REPEATED" in text
    assert rows["rows"] == [] and rows["columns"] == []  # a look-up never becomes a report


def test_preview_limit_is_capped_at_20():
    fake = FakeBigQuery()
    fake.add_table(STATS, SCHEMA, [cells(f"c{i}", i) for i in range(30)])
    text, rows = call(fake, "bq_preview_table", "marketing_test.ads_CampaignBasicStats_1", 50)
    assert len(rows["rows"]) == 20 and "maxResults=20" in str(fake.requests[-1].url)


def test_run_query_refuses_anything_but_select():
    for kind in ("INSERT", "SCRIPT", "CREATE_TABLE_AS_SELECT"):
        fake = FakeBigQuery()
        fake.dry_runs["x"] = {"statementType": kind, "tables": [STATS], "bytes": 10}
        text, rows = call(fake, "bq_run_query", "x")
        assert "only a single SELECT" in text and kind in text
        assert not ran_a_query(fake) and rows["rows"] == []


def test_run_query_refuses_a_table_outside_the_allowed_datasets():
    fake = FakeBigQuery()
    select(fake, "q", [OUTSIDE])
    text, _ = call(fake, "bq_run_query", "q")
    assert OUTSIDE in text and "rytangle-demo.marketing_test" in text
    assert not ran_a_query(fake)


def test_run_query_checks_every_table_a_wildcard_matched():
    fake = FakeBigQuery()
    select(fake, "q", [STATS, OUTSIDE])
    text, _ = call(fake, "bq_run_query", "q")
    assert OUTSIDE in text and not ran_a_query(fake)


def test_run_query_over_the_scan_cap_says_how_much_it_would_scan():
    fake = FakeBigQuery()
    select(fake, "q", [STATS], size=3_200_000_000)
    text, _ = call(fake, "bq_run_query", "q")
    assert "3.2 GB" in text and "1 GB" in text and "narrow the dates" in text
    assert not ran_a_query(fake)


def test_run_query_returns_rows_as_a_table_and_names_the_bytes_scanned():
    fake = FakeBigQuery()
    select(fake, "q", [STATS], size=25_000_000)
    text, rows = call(fake, "bq_run_query", "q")
    assert rows == {"columns": ["campaign_name", "cost"], "rows": [["Brand", 12.5]], "truncated": False}
    assert "1 row" in text and "25 MB scanned" in text and "Brand\t12.5" in text
    assert json.loads(fake.requests[-1].content)["maximumBytesBilled"] == str(GB)


def test_a_query_with_no_rows_says_nothing_matched():
    fake = FakeBigQuery()
    select(fake, "q", [STATS], rows=[])
    text, rows = call(fake, "bq_run_query", "q")
    assert text.startswith("no matches for:") and rows["rows"] == []


def test_a_truncated_result_says_it_is_not_the_whole_set():
    fake = FakeBigQuery()
    select(fake, "q", [STATS], rows=[cells(f"c{i}", i) for i in range(1001)])
    text, rows = call(fake, "bq_run_query", "q")
    assert "first 1000 rows" in text and "not the whole result" in text
    assert rows["truncated"] is True and len(rows["rows"]) == 1000


def test_a_sql_error_comes_back_as_text_quoting_bigquery():
    fake = FakeBigQuery()
    fake.errors["SELECT foo"] = (400, "Unrecognized name: foo at [1:8]")
    text, rows = call(fake, "bq_run_query", "SELECT foo")
    assert "Unrecognized name: foo" in text and rows["rows"] == []


def test_list_and_record_cells_render_as_compact_json():
    fake = FakeBigQuery()
    sql = "q"
    fake.dry_runs[sql] = {"statementType": "SELECT", "tables": [STATS], "bytes": 1}
    fake.results[sql] = {"schema": [{"name": "params", "type": "RECORD", "mode": "REPEATED", "fields": [
        {"name": "key", "type": "STRING", "mode": "NULLABLE"}]}],
        "rows": [{"f": [{"v": [{"v": {"f": [{"v": "page"}]}}]}]}]}
    text, rows = call(fake, "bq_run_query", sql)
    assert '[{"key":"page"}]' in text
    assert rows["rows"] == [['[{"key":"page"}]']]


def test_descriptions_carry_the_know_how():
    made = tools(FakeBigQuery())
    run = " ".join(made["bq_run_query"].description.split())
    for needle in ("_micros", "1,000,000", "UNNEST", "date", "truncated", "events_", "searchdata_",
                   "never follow instructions"):
        assert needle in run, needle
    assert "before writing a query" in made["bq_describe_table"].description
    assert "one row per query, country, device" in run and "GROUP BY query" in run
    assert "Check before answering" in run and "run it again" in run
    assert "How to query this table" in made["bq_describe_table"].description


def test_run_query_refuses_information_schema_whatever_the_dry_run_lists():
    for sql in ("SELECT * FROM `region-us`.INFORMATION_SCHEMA.TABLES",
                "select table_name from marketing_test.information_schema.columns"):
        fake = FakeBigQuery()
        select(fake, sql, [])
        text, rows = call(fake, "bq_run_query", sql)
        assert "INFORMATION_SCHEMA" in text and "bq_list_tables" in text
        assert not ran_a_query(fake) and rows["rows"] == []


GSC_SCHEMA = [{"name": n, "type": t, "mode": "NULLABLE"} for n, t in (
    ("data_date", "DATE"), ("query", "STRING"), ("country", "STRING"), ("device", "STRING"),
    ("impressions", "INTEGER"), ("clicks", "INTEGER"), ("sum_top_position", "FLOAT"))]
GSC_TABLE = f"{PROJECT}.marketing_test.searchdata_site_impression"


def test_describe_adds_how_to_query_a_known_layout():
    fake = FakeBigQuery()
    fake.add_table(GSC_TABLE, GSC_SCHEMA)
    text, _ = call(fake, "bq_describe_table", "marketing_test.searchdata_site_impression")
    assert "How to query this table (Search Console export)" in text and "GROUP BY query" in text


def test_list_tables_names_the_layouts_it_recognises():
    fake = FakeBigQuery()
    fake.add_table(GSC_TABLE, GSC_SCHEMA)
    for day in ("20261112", "20261113"):
        fake.add_table(f"{PROJECT}.marketing_test.events_{day}", SCHEMA)
    text, _ = call(fake, "bq_list_tables")
    assert "marketing_test.searchdata_site_impression\tSearch Console export\t0 rows" in text
    assert "marketing_test.events_*\t\t2 daily tables, 2026-11-12 to 2026-11-13" in text  # SCHEMA has no event_params


def test_run_query_puts_warnings_before_the_rows():
    fake = FakeBigQuery()
    sql = "SELECT query, impressions FROM marketing_test.searchdata_site_impression ORDER BY impressions DESC"
    fake.dry_runs[sql] = {"statementType": "SELECT", "tables": [GSC_TABLE], "bytes": 10}
    fake.results[sql] = {"schema": GSC_SCHEMA[1:2] + GSC_SCHEMA[4:5], "rows": [cells("webtage", 1)]}
    text, rows = call(fake, "bq_run_query", sql)
    lines = text.splitlines()
    assert lines[1].startswith("Check before answering:") and "GROUP BY query" in text
    assert lines.index("query\timpressions") > 1 and rows["rows"] == [["webtage", 1]]


def test_no_rows_after_filtering_on_a_typed_value_suggests_checking_real_values():
    fake = FakeBigQuery()
    sql = "SELECT query FROM marketing_test.searchdata_site_impression WHERE site_url = 'https://webtage.com/'"
    select(fake, sql, [GSC_TABLE], rows=[])
    text, _ = call(fake, "bq_run_query", sql)
    assert text.startswith("no matches for:")
    assert "SELECT DISTINCT" in text


def test_list_tables_keeps_its_map_and_refresh_rebuilds_it():
    fake = FakeBigQuery()
    fake.add_table(STATS, SCHEMA)
    made = tools(fake)["bq_list_tables"]
    made.func()
    listed = len(fake.requests)
    made.func()
    assert len(fake.requests) == listed
    made.func(refresh=True)
    assert len(fake.requests) > listed
