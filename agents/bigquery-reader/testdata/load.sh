#!/usr/bin/env bash
# Load the marketing test data into BigQuery: dataset marketing_test, location US.
#
#   bash load.sh [PROJECT]        (default rytangle-demo)
#
# Runs as YOUR gcloud login, never the agent's service account: the agent's account
# must stay read-only. Re-running replaces the made-up tables and re-copies the
# Analytics ones. To delete everything: bq rm -r -f PROJECT:marketing_test
set -euo pipefail

PROJECT="${1:-rytangle-demo}"
DATASET="marketing_test"
SAMPLE="bigquery-public-data:ga4_obfuscated_sample_ecommerce"
HERE="$(cd "$(dirname "$0")" && pwd)"

ACCOUNT="$(gcloud config get-value account 2>/dev/null)"
if [[ -z "$ACCOUNT" ]]; then
  echo "No gcloud login. Run: gcloud auth login" >&2; exit 1
fi
if [[ "$ACCOUNT" == *gserviceaccount.com ]]; then
  echo "Refusing: the active gcloud account is a service account ($ACCOUNT). Load with your own login." >&2; exit 1
fi
echo "Loading as $ACCOUNT into $PROJECT:$DATASET (location US)"

if ! bq --project_id="$PROJECT" show --format=none "$PROJECT:$DATASET" 2>/dev/null; then
  bq --project_id="$PROJECT" mk --dataset --location=US \
    --description="Made-up marketing data plus Google's Analytics sample, for testing the BigQuery reader" \
    "$PROJECT:$DATASET"
fi

OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
python3 -I "$HERE/generate.py" "$OUT"
for rows in "$OUT"/*.json; do
  case "$rows" in *.schema.json) continue ;; esac
  table="$(basename "$rows" .json)"
  bq --project_id="$PROJECT" load --replace --source_format=NEWLINE_DELIMITED_JSON \
    "$PROJECT:$DATASET.$table" "$rows" "$OUT/$table.schema.json"
  echo "loaded $table"
done

# Google Analytics: copy every daily events_ table of the public sample, as its own export lays them out.
copied=0
for table in $(bq ls -n 1000 --format=json "$SAMPLE" \
    | python3 -I -c 'import json,sys; print("\n".join(t["tableReference"]["tableId"] for t in json.load(sys.stdin) if t["tableReference"]["tableId"].startswith("events_")))'); do
  bq --project_id="$PROJECT" cp -f -q "$SAMPLE.$table" "$PROJECT:$DATASET.$table" >/dev/null
  copied=$((copied + 1))
done
echo "copied $copied Analytics tables from $SAMPLE"
echo "done: bq ls $PROJECT:$DATASET"
