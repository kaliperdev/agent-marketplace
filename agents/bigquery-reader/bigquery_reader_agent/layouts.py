"""Table layouts the reader knows, so their traps are told to the model at the
moment it looks at a table or reads a result, not left to a rule it has to
remember.

Measured on 2026-11-16 with Webtage's data: an agent told in its instructions to
add Search Console rows up per search still ranked single rows (one per country
and device) and called four searches with 3 impressions each "tied at 1". The
same rule written next to the table was followed. So:

- recognize(): notes on how to query a known layout, shown by bq_describe_table
  (and its name by bq_list_tables);
- lint(): warnings about one query's result, shown above its rows by bq_run_query.

A layout is recognised by its table name AND a column it always has, so a
client's own table that happens to share a name is left alone.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from bigquery_reader_agent.bigquery import Rows, TableRef


@dataclass(frozen=True)
class Layout:
    name: str
    notes: tuple[str, ...]


GA4_EVENTS = Layout("Google Analytics export", (
    "One row per event, one table per day (events_YYYYMMDD; events_intraday_ is today's, still filling). Query "
    "events_* and pick days with _TABLE_SUFFIX, for example _TABLE_SUFFIX BETWEEN '20261112' AND '20261114'; "
    "event_date is text, YYYYMMDD.",
    "An event's details are in event_params, a list: read one with (SELECT value.string_value FROM "
    "UNNEST(event_params) WHERE key = 'page_location'); numbers are in value.int_value or value.double_value.",
    "Users over several days: one COUNT(DISTINCT user_pseudo_id) over the whole range; never add daily user "
    "counts, a person who came on two days would be counted twice.",
    "Page views: event_name = 'page_view'. Users: COUNT(DISTINCT user_pseudo_id). Sessions: COUNT(DISTINCT "
    "CONCAT(user_pseudo_id, CAST((SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') "
    "AS STRING))). Purchases: event_name = 'purchase', revenue in ecommerce.purchase_revenue.",
    "Where a visit came from: session_traffic_source_last_click (or collected_traffic_source) on each event; "
    "traffic_source is where the user first came from, not this visit.",
))
GA4_USERS = Layout("Google Analytics users export", (
    "One row per user as that day ended: a snapshot, not visits. Count users from one day's table and never add "
    "days together, or the same people are counted again.",
))
GSC_COMMON = (
    "Days are on data_date (Search Console data arrives 2 to 3 days late); filter on data_date, not on site_url, "
    "unless the person names a property.",
    "A NULL query is Google's anonymized searches (is_anonymized_query = true), hidden for privacy: select "
    "COALESCE(query, 'Anonymized searches') AS query, so it is named wherever it is shown, and report it as "
    "anonymized searches, never as a search.",
)
GSC_SITE = Layout("Search Console export", (
    "There is one row per day, query, country, device and search type: SUM(impressions) and SUM(clicks) with "
    "GROUP BY query before ranking or totalling searches. Never rank single rows.",
    "Average position = SUM(sum_top_position) / SUM(impressions) + 1.",
    *GSC_COMMON,
))
GSC_URL = Layout("Search Console export, by page", (
    "There is one row per day, page, query, country, device and search type: SUM(impressions) and SUM(clicks) "
    "with GROUP BY url (or GROUP BY url, query) before ranking or totalling. Never rank single rows.",
    "Average position = SUM(sum_position) / SUM(impressions) + 1.",
    *GSC_COMMON,
))
GOOGLE_ADS = Layout("Google Ads transfer", (
    "Money in columns ending in _micros is millionths of the account currency: divide by 1,000,000.",
    "Stats tables have one row per day and segment (device, network): SUM the metrics with GROUP BY campaign (or "
    "the level asked about) before ranking. Cost per conversion = SUM(cost) / SUM(conversions), never an average "
    "of daily ratios.",
    "Names and statuses (ads_Campaign_ and similar) are daily snapshots: take the latest _DATA_DATE before joining "
    "on campaign_id, or names repeat.",
))
SNAPSHOTS = Layout("dated snapshots", (
    "Each snapshot date holds a full copy of the data: use the latest snapshot unless the person names a date, and "
    "never add snapshots together.",
))

DAILY = re.compile(r"^(events_|events_intraday_)\d{8}$")
USERS = re.compile(r"^pseudonymous_users_\d{8}$")
ADS = re.compile(r"^p?_?ads_[A-Za-z]+_\d+$")


def recognize(table: str, columns: Sequence[str] | None = None) -> Layout | None:
    """The layout of a table, from its name and (when known) its top-level columns.
    With no columns, the name alone decides: enough for a label in a listing."""
    have = (lambda column: True) if columns is None else (lambda column: column in set(columns))
    if DAILY.match(table) and have("event_params"):
        return GA4_EVENTS
    if USERS.match(table) and have("pseudo_user_id"):
        return GA4_USERS
    if table == "searchdata_site_impression" and have("impressions"):
        return GSC_SITE
    if table == "searchdata_url_impression" and have("impressions"):
        return GSC_URL
    if ADS.match(table) and (columns is None or any(c.startswith(("metrics_", "campaign_")) for c in columns)):
        return GOOGLE_ADS
    if columns is not None and ("snapshot_date" in columns or "_DATA_DATE" in columns):
        return SNAPSHOTS
    return None


LIMIT = re.compile(r"\blimit\s+(\d+)\s*;?\s*$", re.IGNORECASE)
TYPED_VALUE = re.compile(r"\b\w+\s*=\s*'[^']*'")


def lint(sql: str, tables: Sequence[TableRef], rows: Rows) -> list[str]:
    """What to fix or say before answering from this result. Empty when nothing looks off."""
    found: list[str] = []
    names = {t.table for t in tables}
    lowered = sql.lower()
    columns = [c.lower() for c in rows.columns]
    searchdata = any(n.startswith("searchdata_") for n in names)
    if searchdata and "group by" not in lowered and {"impressions", "clicks"} & set(columns):
        found.append("Search Console has one row per day, query, country and device, so these rows are pieces, not "
                     "searches. Run it again with SUM(impressions), SUM(clicks) and GROUP BY query (or url) "
                     "before ranking or totalling.")
    if searchdata and "query" in columns:
        at = columns.index("query")
        if any(row[at] is None for row in rows.rows):
            found.append("A NULL query is Google's anonymized searches (hidden for privacy): run it again with "
                         "COALESCE(query, 'Anonymized searches') AS query, so it is named wherever it is shown, "
                         "and call it anonymized searches, not a search.")
    limit = LIMIT.search(sql)
    if limit and len(rows.rows) == int(limit.group(1)):
        found.append(f"This is a LIMIT {limit.group(1)} slice: sums over these rows are not totals for the whole "
                     "period. Get totals from a separate query without LIMIT.")
    micros = [c for c in rows.columns if c.lower().endswith("_micros")]
    if micros:
        found.append(f"{', '.join(micros)} {'is' if len(micros) == 1 else 'are'} in millionths of the currency: "
                     "divide by 1,000,000 before reporting money.")
    return found


def no_rows_hint(sql: str) -> str:
    """For a query that matched nothing: a filter on a typed value is the usual cause."""
    if TYPED_VALUE.search(sql):
        return (" If a filter used a value you typed (a site, a name, a status), check the real values first with "
                "SELECT DISTINCT on that column, then run it again.")
    return ""
