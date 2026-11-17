# Marketing test data

Test data for the BigQuery reader, all in one dataset, `marketing_test`, located in `US`.

- **Real:** Google Analytics. These are the daily `events_YYYYMMDD` tables copied from Google's public sample
  `bigquery-public-data.ga4_obfuscated_sample_ecommerce`. They hold Google Merchandise Store data
  from 2020-11-01 to 2021-01-31, laid out exactly as Analytics' own BigQuery export lays it out.
- **Made up** by `generate.py`, for the same shop and the same three months:

| Table | Layout copied | Rows |
|---|---|---|
| `ads_Campaign_1234567890` | Google Ads through Google's transfer service (one current snapshot, `_DATA_DATE` 2021-01-31) | 5 |
| `ads_CampaignBasicStats_1234567890` | the same; money in `metrics_cost_micros` | 906 |
| `ads_SearchQueryStats_1234567890` | the same | 715 |
| `facebook_ads_insights` | a plain one table per day per campaign; `spend` in dollars, not micros | 276 |
| `searchdata_site_impression` | Search Console's bulk export | 1,840 |
| `searchdata_url_impression` | the same, by page | 1,840 |
| `semrush_organic_positions` | a plain monthly table (Semrush has no standard layout); competitors are made-up `.example` domains | 78 |

The column names come from Google's documentation as I know it. They have **not** been checked
against a live transfer, so a real client's tables may differ slightly. That is acceptable:
the agent describes tables before querying them.

## Load and delete

```bash
gcloud auth login                     # your own login, never the agent's service account
bash load.sh rytangle-demo            # creates rytangle-demo:marketing_test if needed, then loads
bq rm -r -f rytangle-demo:marketing_test   # deletes everything
```

The cost falls within BigQuery's free tier. The load and the table copies are free, and the
storage used is small.

## Planted answers

Ask the questions about **January 2021** (or name the months). The data ends on 2021-01-31.

1. **Spend and results.** Google Ads: November $6,767.52 (371.48 conversions), December
   $8,802.64 (480.81), January $7,185.47 (378.58). December is about 30% above November.
   Facebook: November $3,466.57 (100 purchases), December $4,489.48 (124), January $3,947.77 (106).
2. **Wasted money.** `Brand Awareness - Display` spent $4,655.31 over the three months (20.5% of
   Google Ads) with **0 conversions**. On Facebook, `Retargeting Broad` has the highest cost per
   purchase: $90.58 (40 purchases for $3,623.10). The next highest is $42.50.
3. **What changed.** In the week of 2021-01-11 to 01-17, Google Ads conversions fell **40.3%**
   (from 94.96 to 56.72) compared with the week before, because `Search - Apparel` has no rows that
   week (it was paused).
4. **Best traffic.** This comes from the real Analytics sample, so there is no planted figure.
   Check it against `traffic_source.source` / `traffic_source.medium` with `purchase` events.
5. **Paying for what we get free.** Six searches rank in the top 3 in Search Console **and** have
   Google Ads spend: `google merchandise store` (position 1.21, $2,078.12 paid), `google store`
   (1.58, $1,237.78), `google merch` (1.90, $834.95), `google mug` (2.42, $1,225.94),
   `google t shirt` (2.60, $2,878.23), `google hoodie` (2.70, $1,718.19).
   Search Console's average position is `sum_top_position / impressions + 1`.
6. **Competitors.** There are 12 keywords where a competitor ranks in the top 10 and the shop does
   not appear: android figurine, branded water bottles, conference swag, corporate gifts,
   custom t shirts, developer stickers, eco tote bag, laptop stickers, logo hoodie,
   pixel phone case, tech swag, youtube merch.

If you change `generate.py`, re-run `uv run pytest tests/test_testdata.py` and update these figures.
