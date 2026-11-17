"""The made-up marketing data: deterministic, inside the Analytics sample's dates,
shaped like its schemas, and holding one planted answer per business question."""

import datetime as dt
import sys
from collections import defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "testdata"))
import generate  # noqa: E402

FIRST, LAST = dt.date(2020, 11, 1), dt.date(2021, 1, 31)
ADS = "ads_CampaignBasicStats_1234567890"


@pytest.fixture(scope="module")
def data():
    return generate.build()


def campaigns(data) -> dict[int, str]:
    return {r["campaign_id"]: r["campaign_name"] for r in data["ads_Campaign_1234567890"]}


def test_build_is_deterministic(data):
    assert generate.build() == data


def test_every_table_has_a_schema_and_rows(data):
    assert set(data) == set(generate.SCHEMAS)
    assert all(data[name] for name in data)


def test_rows_match_their_schemas(data):
    for name, rows in data.items():
        fields = [f["name"] for f in generate.SCHEMAS[name]]
        assert all(list(row) == fields for row in rows), name


def test_every_date_is_inside_the_sample_span(data):
    for name, rows in data.items():
        for column in ("segments_date", "date_start", "data_date", "date"):
            for row in rows:
                if column in row:
                    assert FIRST <= dt.date.fromisoformat(row[column]) <= LAST, (name, row[column])


def test_google_ads_money_is_integer_micros(data):
    assert all(isinstance(r["metrics_cost_micros"], int) for r in data[ADS])
    assert all(r["metrics_cost_micros"] % 10_000 == 0 for r in data[ADS])  # whole cents


def test_planted_december_spend_is_about_30_percent_above_november(data):
    spend = defaultdict(int)
    for r in data[ADS]:
        spend[r["segments_date"][:7]] += r["metrics_cost_micros"]
    assert 1.2 <= spend["2020-12"] / spend["2020-11"] <= 1.4


def test_planted_wasted_campaign_has_spend_and_no_conversions(data):
    names = campaigns(data)
    by = defaultdict(lambda: [0, 0.0])
    for r in data[ADS]:
        by[names[r["campaign_id"]]][0] += r["metrics_cost_micros"]
        by[names[r["campaign_id"]]][1] += r["metrics_conversions"]
    total = sum(cost for cost, _ in by.values())
    cost, conversions = by["Brand Awareness - Display"]
    assert cost / total >= 0.15 and conversions == 0


def test_planted_facebook_campaign_with_the_highest_cost_per_purchase(data):
    by = defaultdict(lambda: [0.0, 0])
    for r in data["facebook_ads_insights"]:
        by[r["campaign_name"]][0] += r["spend"]
        by[r["campaign_name"]][1] += r["purchases"]
    worst = max(by, key=lambda name: by[name][0] / by[name][1])
    assert worst == "Retargeting Broad"


def test_planted_drop_week_has_no_apparel_rows_and_about_40_percent_fewer_conversions(data):
    apparel = next(i for i, n in campaigns(data).items() if n == "Search - Apparel")
    week = {(dt.date(2021, 1, 11) + dt.timedelta(d)).isoformat() for d in range(7)}
    before = {(dt.date(2021, 1, 4) + dt.timedelta(d)).isoformat() for d in range(7)}
    assert not [r for r in data[ADS] if r["campaign_id"] == apparel and r["segments_date"] in week]
    conv = lambda days: sum(r["metrics_conversions"] for r in data[ADS] if r["segments_date"] in days)
    assert 0.3 <= 1 - conv(week) / conv(before) <= 0.5


def test_planted_overlap_has_at_least_five_terms(data):
    paid = {r["search_term_view_search_term"] for r in data["ads_SearchQueryStats_1234567890"]
            if r["metrics_cost_micros"] > 0}
    totals = defaultdict(lambda: [0, 0])
    for r in data["searchdata_site_impression"]:
        totals[r["query"]][0] += r["sum_top_position"]
        totals[r["query"]][1] += r["impressions"]
    top3 = {q for q, (s, i) in totals.items() if s / i + 1 <= 3}
    assert len(paid & top3) >= 5


def test_planted_competitor_gap_has_at_least_ten_keywords(data):
    rows = data["semrush_organic_positions"]
    ours = {r["keyword"] for r in rows if r["domain"] == generate.SHOP}
    theirs = {r["keyword"] for r in rows if r["domain"] != generate.SHOP and r["position"] <= 10}
    assert len(theirs - ours) >= 10


def test_competitor_domains_are_made_up(data):
    assert {r["domain"] for r in data["semrush_organic_positions"]} - {generate.SHOP} <= {
        "swagstore.example", "brandgear.example"}


def test_write_puts_rows_and_a_schema_per_table(tmp_path):
    generate.write(tmp_path)
    for name in generate.SCHEMAS:
        assert (tmp_path / f"{name}.json").exists() and (tmp_path / f"{name}.schema.json").exists()
