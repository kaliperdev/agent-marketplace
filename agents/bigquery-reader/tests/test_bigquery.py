import json

import httpx
import pytest

from bigquery_reader_agent.bigquery import (
    BigQuery, BigQueryError, DatasetRef, TableRef, parse_datasets, parse_max_bytes)
from bq_fake import FakeBigQuery, cells

PROJECT = "rytangle-demo"
STATS = f"{PROJECT}.marketing_test.ads_CampaignBasicStats_1"
STATS_REF = TableRef(PROJECT, "marketing_test", "ads_CampaignBasicStats_1")
SCHEMA = [{"name": "campaign_id", "type": "INTEGER", "mode": "NULLABLE"},
          {"name": "metrics_cost_micros", "type": "INTEGER", "mode": "NULLABLE"},
          {"name": "ctr", "type": "FLOAT", "mode": "NULLABLE"},
          {"name": "active", "type": "BOOLEAN", "mode": "NULLABLE"},
          {"name": "segments_date", "type": "DATE", "mode": "NULLABLE"}]


def make(fake: FakeBigQuery) -> BigQuery:
    client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    return BigQuery(client, lambda: "tok-1", PROJECT)


def test_parse_datasets_accepts_bare_and_project_qualified_names():
    found = parse_datasets("marketing_test, bigquery-public-data.ga4_obfuscated_sample_ecommerce\n", PROJECT)
    assert [str(d) for d in found] == [
        "rytangle-demo.marketing_test", "bigquery-public-data.ga4_obfuscated_sample_ecommerce"]
    with pytest.raises(ValueError, match="no datasets given"):
        parse_datasets(" , ", PROJECT)


def test_parse_max_bytes_defaults_to_one_gb_and_refuses_nonsense():
    assert parse_max_bytes(None) == parse_max_bytes("") == 1_000_000_000
    assert parse_max_bytes("2.5") == 2_500_000_000
    for bad in ("-1", "0", "lots"):
        with pytest.raises(ValueError):
            parse_max_bytes(bad)


def test_tables_lists_every_page_with_kind_and_creation_date():
    fake = FakeBigQuery()
    fake.page_size = 2
    for day in ("20201101", "20201102", "20201103"):
        fake.add_table(f"{PROJECT}.marketing_test.events_{day}", SCHEMA)
    found = make(fake).tables(DatasetRef(PROJECT, "marketing_test"))
    assert [t.ref.table for t in found] == ["events_20201101", "events_20201102", "events_20201103"]
    assert found[0].kind == "TABLE" and found[0].created == "2020-11-01"
    assert fake.requests[0].headers["authorization"] == "Bearer tok-1"


def test_table_flattens_nested_fields_to_dotted_names():
    fake = FakeBigQuery()
    fake.add_table(f"{PROJECT}.marketing_test.events_20201101", [
        {"name": "event_name", "type": "STRING", "mode": "NULLABLE", "description": "what happened"},
        {"name": "event_params", "type": "RECORD", "mode": "REPEATED", "fields": [
            {"name": "key", "type": "STRING", "mode": "NULLABLE"},
            {"name": "value", "type": "RECORD", "mode": "NULLABLE", "fields": [
                {"name": "string_value", "type": "STRING", "mode": "NULLABLE"}]}]}],
        numBytes="2048", lastModifiedTime="1609459200000",
        timePartitioning={"type": "DAY", "field": "event_date"})
    detail = make(fake).table(TableRef(PROJECT, "marketing_test", "events_20201101"))
    assert [(c.name, c.type, c.mode) for c in detail.columns] == [
        ("event_name", "STRING", "NULLABLE"), ("event_params", "RECORD", "REPEATED"),
        ("event_params.key", "STRING", "NULLABLE"), ("event_params.value", "RECORD", "NULLABLE"),
        ("event_params.value.string_value", "STRING", "NULLABLE")]
    assert detail.columns[0].description == "what happened"
    assert detail.partition == "by day on event_date"
    assert (detail.size_bytes, detail.modified) == (2048, "2021-01-01")


def test_preview_converts_cells_by_schema():
    fake = FakeBigQuery()
    fake.add_table(STATS, SCHEMA, [cells(12, 3_500_000, 1.5, "true", "2020-11-01"), cells(None, 1, 0, "false", None)])
    rows = make(fake).preview(STATS_REF, 5)
    assert rows.columns == ["campaign_id", "metrics_cost_micros", "ctr", "active", "segments_date"]
    assert rows.rows == [[12, 3_500_000, 1.5, True, "2020-11-01"], [None, 1, 0.0, False, None]]
    assert "maxResults=5" in str(fake.requests[-1].url)


def test_preview_converts_nested_and_repeated_cells():
    fake = FakeBigQuery()
    fake.add_table(STATS, [{"name": "params", "type": "RECORD", "mode": "REPEATED", "fields": [
        {"name": "key", "type": "STRING", "mode": "NULLABLE"},
        {"name": "n", "type": "INTEGER", "mode": "NULLABLE"}]}],
        [{"f": [{"v": [{"v": {"f": [{"v": "page"}, {"v": "3"}]}}]}]}])
    assert make(fake).preview(STATS_REF, 5).rows == [[[{"key": "page", "n": 3}]]]


def test_dry_run_reports_statement_type_tables_and_bytes():
    fake = FakeBigQuery()
    fake.dry_runs["SELECT 1"] = {"statementType": "SELECT", "tables": [STATS], "bytes": 1234}
    dry = make(fake).dry_run("SELECT 1")
    assert (dry.statement_type, dry.tables, dry.bytes) == ("SELECT", [STATS_REF], 1234)
    body = json.loads(fake.requests[0].content)
    assert body["configuration"]["dryRun"] is True
    assert body["configuration"]["query"]["useLegacySql"] is False


def test_query_sends_the_scan_cap_as_maximum_bytes_billed():
    fake = FakeBigQuery()
    fake.results["SELECT 1"] = {"schema": SCHEMA[:1], "rows": [cells(1)], "bytes": 10}
    rows = make(fake).query("SELECT 1", max_bytes=1_000_000_000, max_rows=5)
    body = json.loads(fake.requests[0].content)
    assert body["maximumBytesBilled"] == "1000000000" and body["useLegacySql"] is False
    assert (rows.rows, rows.truncated, rows.bytes_scanned) == ([[1]], False, 10)


def test_query_polls_until_complete():
    fake = FakeBigQuery()
    fake.results["SELECT 1"] = {"schema": SCHEMA[:1], "rows": [cells(1)]}
    fake.pending_polls = 2
    rows = make(fake).query("SELECT 1", max_bytes=10, max_rows=5)
    assert rows.rows == [[1]]
    assert sum(1 for r in fake.requests if "/queries/" in r.url.path) == 2


def test_query_that_never_completes_raises_a_too_slow_error():
    fake = FakeBigQuery()
    fake.results["SELECT 1"] = {"schema": SCHEMA[:1], "rows": [cells(1)]}
    fake.pending_polls = 99
    with pytest.raises(BigQueryError, match="took too long"):
        make(fake).query("SELECT 1", max_bytes=10, max_rows=5, wait_seconds=0)


def test_query_over_max_rows_is_truncated():
    fake = FakeBigQuery()
    fake.results["SELECT 1"] = {"schema": SCHEMA[:1], "rows": [cells(i) for i in range(7)]}
    rows = make(fake).query("SELECT 1", max_bytes=10, max_rows=5)
    assert rows.rows == [[0], [1], [2], [3], [4]] and rows.truncated is True


def test_a_google_refusal_carries_its_message():
    fake = FakeBigQuery()
    fake.errors["SELECT foo"] = (400, "Unrecognized name: foo at [1:8]")
    with pytest.raises(BigQueryError, match="Unrecognized name: foo"):
        make(fake).query("SELECT foo", max_bytes=10, max_rows=5)


def test_a_finished_job_with_errors_raises_instead_of_returning_no_rows():
    fake = FakeBigQuery()
    fake.results["SELECT 1"] = {"schema": SCHEMA[:1], "rows": [],
                                "errors": [{"message": "Resources exceeded during query execution"}]}
    with pytest.raises(BigQueryError, match="Resources exceeded"):
        make(fake).query("SELECT 1", max_bytes=10, max_rows=5)


def test_polling_leaves_out_an_unknown_location():
    fake = FakeBigQuery()
    fake.results["SELECT 1"] = {"schema": SCHEMA[:1], "rows": [cells(1)]}
    fake.pending_polls = 1
    fake.job_location = None
    make(fake).query("SELECT 1", max_bytes=10, max_rows=5)
    poll = next(r for r in fake.requests if "/queries/" in r.url.path)
    assert "location" not in poll.url.params
