"""Made-up marketing data for testing the BigQuery reader, with planted answers.

Google's public Analytics sample (bigquery-public-data.ga4_obfuscated_sample_ecommerce,
the Google Merchandise Store, 2020-11-01 to 2021-01-31) is real; everything here
is invented for the same shop and the same three months, laid out the way the
usual copying method lays it out. See README.md for the planted answers.

  python3 generate.py OUT_DIR    one TABLE.json (newline-delimited) and TABLE.schema.json per table

Standard library only, and deterministic: the same files every run.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import sys
from pathlib import Path

SEED = 20210131
FIRST, LAST = dt.date(2020, 11, 1), dt.date(2021, 1, 31)
CUSTOMER = "1234567890"
SHOP = "shop.googlemerchandisestore.com"
HOME = f"https://{SHOP}/"
DROP_WEEK = (dt.date(2021, 1, 11), dt.date(2021, 1, 17))  # Search - Apparel paused
MONTH_SPEND = {11: 1.0, 12: 1.26, 1: 1.1}  # December about 30% above November
ORDER_VALUE = 45.0

# id, name, channel, daily spend (USD), cost per click, click-through rate, conversion rate
ADS_CAMPAIGNS = [
    (101, "Search - Brand", "SEARCH", 40, 0.8, 0.20, 0.08),
    (102, "Search - Apparel", "SEARCH", 60, 1.5, 0.05, 0.12),
    (103, "Search - Drinkware", "SEARCH", 30, 1.2, 0.04, 0.04),
    (104, "Brand Awareness - Display", "DISPLAY", 45, 0.4, 0.004, 0.0),
    (105, "Shopping - All Products", "SHOPPING", 50, 1.0, 0.02, 0.05),
]
DEVICES = (("DESKTOP", 0.45), ("MOBILE", 0.55))
# campaign id -> (search term, share of the campaign's clicks)
SEARCH_TERMS = {
    101: [("google merchandise store", 0.5), ("google store", 0.3), ("google merch", 0.2)],
    102: [("google t shirt", 0.5), ("google hoodie", 0.3), ("youtube t shirt", 0.2)],
    103: [("google water bottle", 0.6), ("google mug", 0.4)],
}
# id, name, ad set, daily spend, cost per click, purchases per click
FACEBOOK = [
    (2001, "Prospecting - Lookalike", "Lookalike 1% US", 50, 0.9, 0.02),
    (2002, "Retargeting - Cart", "Cart abandoners 14d", 30, 1.1, 0.06),
    (2003, "Retargeting Broad", "All visitors 90d", 35, 1.0, 0.01),
]
# query, average position, daily impressions, click-through rate, landing page
ORGANIC = [
    ("google merchandise store", 1.2, 300, 0.35, HOME),
    ("google store", 1.6, 500, 0.12, HOME),
    ("google merch", 1.9, 200, 0.20, HOME),
    ("google t shirt", 2.6, 400, 0.08, f"{HOME}Google+Redesign/Apparel"),
    ("google hoodie", 2.7, 250, 0.07, f"{HOME}Google+Redesign/Apparel"),
    ("google mug", 2.4, 90, 0.06, f"{HOME}Google+Redesign/Drinkware"),
    ("youtube t shirt", 7.5, 150, 0.02, f"{HOME}Google+Redesign/Shop+by+Brand/YouTube"),
    ("google water bottle", 5.5, 120, 0.03, f"{HOME}Google+Redesign/Drinkware"),
    ("android plush", 11.0, 80, 0.01, f"{HOME}Google+Redesign/Lifestyle"),
    ("chrome dino plush", 14.0, 60, 0.005, f"{HOME}Google+Redesign/Lifestyle"),
]
# Semrush: keyword -> monthly search volume; who ranks where
VOLUME = {q: imp * 30 // 2 for q, _, imp, _, _ in ORGANIC} | {
    "custom t shirts": 90000, "branded water bottles": 8100, "tech swag": 2900, "developer stickers": 1900,
    "conference swag": 1600, "logo hoodie": 4400, "corporate gifts": 33100, "android figurine": 1300,
    "pixel phone case": 22200, "youtube merch": 6600, "eco tote bag": 5400, "laptop stickers": 12100,
}
COMPETITORS = {
    "swagstore.example": {"custom t shirts": 3, "tech swag": 1, "developer stickers": 2, "conference swag": 1,
                          "logo hoodie": 4, "corporate gifts": 7, "laptop stickers": 2, "google t shirt": 5,
                          "google store": 9},
    "brandgear.example": {"branded water bottles": 2, "android figurine": 6, "pixel phone case": 8,
                          "youtube merch": 3, "eco tote bag": 5, "corporate gifts": 4, "google hoodie": 6},
}


def _f(name: str, kind: str) -> dict:
    return {"name": name, "type": kind, "mode": "NULLABLE"}


SCHEMAS: dict[str, list[dict]] = {
    f"ads_Campaign_{CUSTOMER}": [
        _f("campaign_id", "INTEGER"), _f("campaign_name", "STRING"), _f("campaign_status", "STRING"),
        _f("campaign_advertising_channel_type", "STRING"), _f("_DATA_DATE", "DATE")],
    f"ads_CampaignBasicStats_{CUSTOMER}": [
        _f("campaign_id", "INTEGER"), _f("segments_date", "DATE"), _f("segments_device", "STRING"),
        _f("metrics_impressions", "INTEGER"), _f("metrics_clicks", "INTEGER"), _f("metrics_cost_micros", "INTEGER"),
        _f("metrics_conversions", "FLOAT"), _f("metrics_conversions_value", "FLOAT")],
    f"ads_SearchQueryStats_{CUSTOMER}": [
        _f("campaign_id", "INTEGER"), _f("search_term_view_search_term", "STRING"), _f("segments_date", "DATE"),
        _f("metrics_clicks", "INTEGER"), _f("metrics_cost_micros", "INTEGER"), _f("metrics_conversions", "FLOAT")],
    "facebook_ads_insights": [
        _f("date_start", "DATE"), _f("campaign_id", "STRING"), _f("campaign_name", "STRING"), _f("adset_name", "STRING"),
        _f("impressions", "INTEGER"), _f("clicks", "INTEGER"), _f("spend", "FLOAT"), _f("purchases", "INTEGER"),
        _f("purchase_value", "FLOAT")],
    "searchdata_site_impression": [
        _f("data_date", "DATE"), _f("site_url", "STRING"), _f("query", "STRING"), _f("is_anonymized_query", "BOOLEAN"),
        _f("country", "STRING"), _f("search_type", "STRING"), _f("device", "STRING"), _f("impressions", "INTEGER"),
        _f("clicks", "INTEGER"), _f("sum_top_position", "FLOAT")],
    "searchdata_url_impression": [
        _f("data_date", "DATE"), _f("site_url", "STRING"), _f("url", "STRING"), _f("query", "STRING"),
        _f("is_anonymized_query", "BOOLEAN"), _f("country", "STRING"), _f("search_type", "STRING"),
        _f("device", "STRING"), _f("impressions", "INTEGER"), _f("clicks", "INTEGER"), _f("sum_position", "FLOAT")],
    "semrush_organic_positions": [
        _f("date", "DATE"), _f("domain", "STRING"), _f("keyword", "STRING"), _f("position", "INTEGER"),
        _f("search_volume", "INTEGER"), _f("url", "STRING"), _f("traffic", "INTEGER")],
}


def _days():
    day = FIRST
    while day <= LAST:
        yield day
        day += dt.timedelta(days=1)


def _micros(dollars: float) -> int:
    return int(round(dollars * 100)) * 10_000  # whole cents, as Google Ads reports them


def _paused(campaign_id: int, day: dt.date) -> bool:
    return campaign_id == 102 and DROP_WEEK[0] <= day <= DROP_WEEK[1]


def _ads(rng: random.Random) -> tuple[list[dict], list[dict]]:
    stats, terms = [], []
    for day in _days():
        for cid, _, _, spend, cpc, ctr, rate in ADS_CAMPAIGNS:
            if _paused(cid, day):
                continue
            for device, share in DEVICES:
                cost = spend * MONTH_SPEND[day.month] * share * rng.uniform(0.9, 1.1)
                clicks = max(1, round(cost / cpc))
                conversions = round(clicks * rate * rng.uniform(0.85, 1.15), 2)
                stats.append({"campaign_id": cid, "segments_date": day.isoformat(), "segments_device": device,
                              "metrics_impressions": round(clicks / ctr), "metrics_clicks": clicks,
                              "metrics_cost_micros": _micros(cost), "metrics_conversions": conversions,
                              "metrics_conversions_value": round(conversions * ORDER_VALUE, 2)})
            for term, share in SEARCH_TERMS.get(cid, []):
                cost = spend * MONTH_SPEND[day.month] * share * rng.uniform(0.9, 1.1)
                clicks = max(1, round(cost / cpc))
                terms.append({"campaign_id": cid, "search_term_view_search_term": term,
                              "segments_date": day.isoformat(), "metrics_clicks": clicks,
                              "metrics_cost_micros": _micros(cost),
                              "metrics_conversions": round(clicks * rate * rng.uniform(0.85, 1.15), 2)})
    return stats, terms


def _facebook(rng: random.Random) -> list[dict]:
    rows = []
    for day in _days():
        for cid, name, adset, spend, cpc, rate in FACEBOOK:
            cost = round(spend * MONTH_SPEND[day.month] * rng.uniform(0.9, 1.1), 2)
            clicks = max(1, round(cost / cpc))
            purchases = int(clicks * rate + rng.random())
            rows.append({"date_start": day.isoformat(), "campaign_id": str(cid), "campaign_name": name,
                         "adset_name": adset, "impressions": round(clicks / 0.012), "clicks": clicks, "spend": cost,
                         "purchases": purchases, "purchase_value": round(purchases * ORDER_VALUE, 2)})
    return rows


def _search_console(rng: random.Random) -> tuple[list[dict], list[dict]]:
    site, by_url = [], []
    common = {"is_anonymized_query": False, "country": "usa", "search_type": "WEB"}
    for day in _days():
        for query, position, impressions, ctr, url in ORGANIC:
            for device, share in DEVICES:
                shown = max(1, round(impressions * share * rng.uniform(0.8, 1.2)))
                clicks = round(shown * ctr * rng.uniform(0.8, 1.2))
                top = round((position - 1 + rng.uniform(-0.3, 0.3)) * shown, 1)
                site.append({"data_date": day.isoformat(), "site_url": f"sc-domain:{SHOP}", "query": query,
                             **common, "device": device, "impressions": shown, "clicks": clicks,
                             "sum_top_position": max(0.0, top)})
                by_url.append({"data_date": day.isoformat(), "site_url": f"sc-domain:{SHOP}", "url": url,
                               "query": query, **common, "device": device, "impressions": shown, "clicks": clicks,
                               "sum_position": max(0.0, top)})
    return site, by_url


def _traffic(volume: int, position: int) -> int:
    share = {1: 0.28, 2: 0.15, 3: 0.11, 4: 0.08, 5: 0.06}.get(position, 0.03 if position <= 10 else 0.005)
    return round(volume * share)


def _semrush(rng: random.Random) -> list[dict]:
    rows = []
    for month in (dt.date(2020, 11, 1), dt.date(2020, 12, 1), dt.date(2021, 1, 1)):
        for query, position, _, _, url in ORGANIC:
            pos = max(1, round(position + rng.uniform(-0.4, 0.4)))
            rows.append({"date": month.isoformat(), "domain": SHOP, "keyword": query, "position": pos,
                         "search_volume": VOLUME[query], "url": url, "traffic": _traffic(VOLUME[query], pos)})
        for domain, ranks in COMPETITORS.items():
            for keyword, position in ranks.items():
                pos = max(1, position + rng.choice((-1, 0, 0, 1)))
                rows.append({"date": month.isoformat(), "domain": domain, "keyword": keyword, "position": pos,
                             "search_volume": VOLUME[keyword],
                             "url": f"https://{domain}/{keyword.replace(' ', '-')}",
                             "traffic": _traffic(VOLUME[keyword], pos)})
    return rows


def build() -> dict[str, list[dict]]:
    """Every table's rows, by table name. Same result every call."""
    rng = random.Random(SEED)
    stats, terms = _ads(rng)
    site, by_url = _search_console(rng)
    campaigns = [{"campaign_id": cid, "campaign_name": name, "campaign_status": "ENABLED",
                  "campaign_advertising_channel_type": channel, "_DATA_DATE": LAST.isoformat()}
                 for cid, name, channel, *_ in ADS_CAMPAIGNS]
    return {
        f"ads_Campaign_{CUSTOMER}": campaigns,
        f"ads_CampaignBasicStats_{CUSTOMER}": stats,
        f"ads_SearchQueryStats_{CUSTOMER}": terms,
        "facebook_ads_insights": _facebook(rng),
        "searchdata_site_impression": site,
        "searchdata_url_impression": by_url,
        "semrush_organic_positions": _semrush(rng),
    }


def write(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in build().items():
        (out_dir / f"{name}.json").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        (out_dir / f"{name}.schema.json").write_text(json.dumps(SCHEMAS[name], indent=1), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python3 generate.py OUT_DIR")
    write(Path(sys.argv[1]))
