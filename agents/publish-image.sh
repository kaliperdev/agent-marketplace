#!/usr/bin/env bash
# Build one agent's image for both chips (x86 for servers, ARM for Macs) and
# upload it to the registry servers download agents from.
#
#   agents/publish-image.sh jira 2.0.0
#   agents/publish-image.sh catalog-service 0.1.0
#
# Needs a one-time `docker login ghcr.io` with a token that can write packages.
# Then publish the agent's catalog entry (uv run catalog import ...) as usual.
set -euo pipefail

usage="usage: agents/publish-image.sh <agent|catalog-service> <version>"
agent="${1:?$usage}"
version="${2:?$usage}"
registry="${AGENT_REGISTRY:-ghcr.io/kaliperdev}"

cd "$(dirname "$0")/.."  # the repo root

[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must look like 1.2.3, got $version" >&2; exit 2; }

case "$agent" in
  catalog-service) name=catalog-service; dockerfile=Dockerfile; context=.; project=pyproject.toml ;;
  textql)          name=agent-textql; dockerfile=agents/textql/Dockerfile; context=agents/textql; project=agents/textql/pyproject.toml ;;
  *)               name="agent-$agent"; dockerfile="agents/$agent/Dockerfile"; context=agents; project="agents/$agent/pyproject.toml" ;;
esac
[ -f "$dockerfile" ] || { echo "no agent called $agent ($dockerfile not found)" >&2; exit 2; }

# The tag must be the code's own version, so an image called 2.0.0 always holds 2.0.0.
code_version=$(sed -nE 's/^version *= *"([^"]+)".*/\1/p' "$project" | head -1)
[ "$code_version" = "$version" ] || { echo "$project says $code_version, not $version: bump it first" >&2; exit 2; }

image="$registry/$name:$version"
# Published means locked, here as in the catalog: a version is never uploaded twice.
if docker buildx imagetools inspect "$image" >/dev/null 2>&1; then
  echo "$image already exists: publish a new version instead" >&2
  exit 1
fi

docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --label org.opencontainers.image.source=https://github.com/kaliperdev/agent-marketplace \
  --label org.opencontainers.image.version="$version" \
  -f "$dockerfile" -t "$image" --push "$context"

echo "published $image for:"
docker buildx imagetools inspect "$image" | sed -nE 's/^ *Platform: *(linux\/(amd64|arm64))$/  \1/p'
