#!/usr/bin/env bash
# Publish a catalog entry to the hosted catalog (Kaliper prod), then save the
# published record under published/ for git. The database listens only on that
# server, so this reaches it through an SSH tunnel for the length of the command.
#
#   deploy/publish-to-hosted.sh agents/jira/catalog-2.1.0.json
#   deploy/publish-to-hosted.sh --list          # what the hosted catalog holds
#
# Reads from this repo's git-ignored .env:
#   HOSTED_CATALOG_SSH=ec2-user@<kaliper prod>
#   HOSTED_CATALOG_SSH_KEY=~/.ssh/<key>.pem
#   HOSTED_CATALOG_DATABASE_URL=postgresql://catalog:<password>@127.0.0.1:55434/catalog
# Upload the agent's package first (agents/publish-image.sh).
set -euo pipefail
cd "$(dirname "$0")/.."

setting() { sed -n "s/^$1=//p" .env | tail -1; }
ssh_target="${HOSTED_CATALOG_SSH:-$(setting HOSTED_CATALOG_SSH)}"
ssh_key="${HOSTED_CATALOG_SSH_KEY:-$(setting HOSTED_CATALOG_SSH_KEY)}"
database="${HOSTED_CATALOG_DATABASE_URL:-$(setting HOSTED_CATALOG_DATABASE_URL)}"
[ -n "$ssh_target" ] && [ -n "$database" ] || { echo "set HOSTED_CATALOG_SSH and HOSTED_CATALOG_DATABASE_URL in .env" >&2; exit 2; }
ssh_key="${ssh_key/#\~/$HOME}"

usage="usage: deploy/publish-to-hosted.sh <entry.json> | --list"
what="${1:?$usage}"
if [ "$what" != "--list" ]; then
  [ -f "$what" ] || { echo "no file $what" >&2; exit 2; }
  id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$what")
  version=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$what")
fi

port=55434
ssh ${ssh_key:+-i "$ssh_key"} -o ExitOnForwardFailure=yes -o BatchMode=yes -N -L "$port:127.0.0.1:55433" "$ssh_target" &
tunnel=$!
trap 'kill "$tunnel" 2>/dev/null' EXIT
for _ in $(seq 1 40); do
  nc -z 127.0.0.1 "$port" 2>/dev/null && break
  kill -0 "$tunnel" 2>/dev/null || { echo "the SSH tunnel did not open" >&2; exit 1; }
  sleep 0.25
done

export CATALOG_DATABASE_URL="$database"
if [ "$what" = "--list" ]; then
  uv run catalog list
  exit
fi

uv run catalog import "$what"
mkdir -p "published/$id"
uv run catalog export "$id" --version "$version" > "published/$id/$version.json"
echo "published $id $version to the hosted catalog; commit published/$id/$version.json"
