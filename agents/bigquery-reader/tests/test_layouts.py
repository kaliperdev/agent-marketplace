"""Known table layouts: notes on how to query them, and warnings about a query's result.

Each rule comes from a wrong answer measured on 2026-11-16 (Webtage's data on the
laptop), or from a trap of the same layout that would give one."""

from bigquery_reader_agent.bigquery import Rows, TableRef
from bigquery_reader_agent.layouts import lint, recognize

GA4 = ["event_date", "event_timestamp", "event_name", "event_params", "user_pseudo_id"]
GSC = ["data_date", "site_url", "query", "is_anonymized_query", "country", "device", "impressions", "clicks",
       "sum_top_position"]
GSC_URL = GSC[:-1] + ["url", "sum_position"]
ADS = ["campaign_id", "segments_date", "segments_device", "metrics_clicks", "metrics_cost_micros"]


def ref(table: str) -> TableRef:
    return TableRef("p", "d", table)


def rows(columns, data, truncated=False) -> Rows:
    return Rows(columns, data, truncated, 0, len(data))


def notes(table, columns) -> str:
    return " ".join(recognize(table, columns).notes)


# -- recognize ------------------------------------------------------------

def test_analytics_events_are_recognised_with_how_to_read_their_details():
    layout = recognize("events_20261112", GA4)
    assert layout.name == "Google Analytics export"
    text = " ".join(layout.notes)
    assert "_TABLE_SUFFIX" in text and "UNNEST(event_params)" in text and "COUNT(DISTINCT user_pseudo_id)" in text


def test_users_over_several_days_are_counted_once_not_added_up():
    # Measured 2026-11-16: 19 + 33 + 18 daily users reported as "70 users", where 69 different people came.
    text = notes("events_20261112", GA4)
    assert "over several days" in text and "never add daily user counts" in text


def test_analytics_users_tables_are_daily_snapshots_never_added_together():
    text = notes("pseudonymous_users_20261112", ["pseudo_user_id", "stream_id", "user_info"])
    assert "snapshot" in text and "never add" in text


def test_search_console_says_rows_are_per_breakdown_and_must_be_summed_by_query():
    text = notes("searchdata_site_impression", GSC)
    assert "one row per day, query, country, device and search type" in text
    assert "GROUP BY query" in text and "SUM(sum_top_position)" in text
    assert "anonymized" in text and "COALESCE(query, 'Anonymized searches')" in text


def test_search_console_by_page_uses_sum_position():
    text = notes("searchdata_url_impression", GSC_URL)
    assert "GROUP BY url" in text and "SUM(sum_position)" in text and "SUM(sum_top_position)" not in text


def test_google_ads_transfer_money_is_in_micros():
    text = notes("ads_CampaignBasicStats_1234567890", ADS)
    assert "1,000,000" in text and "GROUP BY" in text


def test_a_table_of_dated_snapshots_reads_the_latest_one():
    text = notes("keyword_rankings", ["snapshot_date", "keyword", "position"])
    assert "latest" in text and "never add" in text


def test_a_name_alone_is_not_enough_without_the_layouts_columns():
    assert recognize("events_20261112", ["id", "name"]) is None
    assert recognize("orders", ["id", "total"]) is None


# -- lint ------------------------------------------------------------------

def test_search_console_ranked_without_group_by_is_flagged():
    sql = "SELECT query, impressions FROM d.searchdata_site_impression WHERE data_date = '2026-11-11' ORDER BY impressions DESC"
    found = lint(sql, [ref("searchdata_site_impression")], rows(["query", "impressions"], [["a", 1]]))
    assert any("GROUP BY query" in w and "SUM(impressions)" in w for w in found)


def test_search_console_with_group_by_is_not_flagged():
    sql = "SELECT query, SUM(impressions) i FROM d.searchdata_site_impression GROUP BY query"
    assert lint(sql, [ref("searchdata_site_impression")], rows(["query", "i"], [["a", 3]])) == []


def test_a_null_search_is_named_as_googles_anonymized_searches():
    sql = "SELECT query, SUM(impressions) i FROM d.searchdata_site_impression GROUP BY query"
    found = lint(sql, [ref("searchdata_site_impression")], rows(["query", "i"], [[None, 24], ["webtage", 3]]))
    assert any("anonymized" in w for w in found)
    # Named in the query itself, so a report's row reads "Anonymized searches", not a blank.
    assert any("COALESCE(query, 'Anonymized searches')" in w for w in found)


def test_a_full_limit_slice_is_not_the_whole_period():
    sql = "SELECT query, SUM(impressions) i FROM d.searchdata_site_impression GROUP BY query ORDER BY i DESC LIMIT 2"
    found = lint(sql, [ref("searchdata_site_impression")], rows(["query", "i"], [["a", 3], ["b", 2]]))
    assert any("LIMIT 2" in w and "not totals" in w for w in found)


def test_a_limit_the_result_did_not_reach_is_not_flagged():
    sql = "SELECT query FROM d.t GROUP BY query LIMIT 20"
    assert lint(sql, [ref("t")], rows(["query"], [["a"]])) == []


def test_money_left_in_micros_is_flagged():
    sql = "SELECT campaign_id, SUM(metrics_cost_micros) AS metrics_cost_micros FROM d.ads_CampaignBasicStats_1 GROUP BY 1"
    found = lint(sql, [ref("ads_CampaignBasicStats_1")], rows(["campaign_id", "metrics_cost_micros"], [[1, 5_000_000]]))
    assert any("1,000,000" in w for w in found)


def test_an_ordinary_query_gets_no_warnings():
    sql = "SELECT event_date, COUNT(*) FROM d.events_* WHERE event_name = 'page_view' GROUP BY 1"
    assert lint(sql, [ref("events_20261112")], rows(["event_date", "n"], [["20261112", 22]])) == []
