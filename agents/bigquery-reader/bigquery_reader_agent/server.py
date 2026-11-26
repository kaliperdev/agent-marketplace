"""BigQuery, read only, as its own MCP server (catalog kind "mcp", agent id bigquery-reader).

GOOGLE_SA_JSON is the service account's key: the client's settings service
passes the key file's CONTENTS (a path on that server means nothing in here),
which are written to a private file for Google's library. A path still works,
for running this by hand. BQ_PROJECT is the project queries run (and are billed)
in; BQ_DATASETS the datasets the agent may read; BQ_MAX_GB the most one query
may scan (1 GB when blank). ACCESS_NOTE_KEY, when the router gives one, checks the
signed note a call may carry (access.py): that call then reads with its own pass,
project and datasets, and a call without a note reads as above.

The key and token code is copied from agents/gsc on purpose: agent_kit has no
Google dependency, and each agent carries its own, as gdocs, gdrive and gsc do.

  python -m bigquery_reader_agent.server
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import httpx

from agent_kit.mcp_server import serve
from agent_kit.settings import credential, missing
from bigquery_reader_agent import access
from bigquery_reader_agent.bigquery import SCOPE, BigQuery, parse_datasets, parse_max_bytes
from bigquery_reader_agent.tablemap import MapCaches
from bigquery_reader_agent.tools import make_bq_tools

HTTP_TIMEOUT_SECONDS = 60.0



def key_path(value: str, key_dir: Path | None = None) -> str:
    """A path to the key: the value itself, or a private file holding it."""
    if not value.lstrip().startswith("{"):
        return value
    handle, path = tempfile.mkstemp(prefix="sa-", suffix=".json", dir=key_dir)
    with os.fdopen(handle, "w", encoding="utf-8") as out:  # mkstemp makes it 0600
        out.write(value)
    return path


def service_account_token_provider(sa_json_path: str, scopes: Sequence[str]) -> Callable[[], str]:
    """Mint access tokens from a service-account key file for the given scopes."""

    # Built once and refreshed only when expired (tokens last an hour).
    held: dict = {}

    def provide() -> str:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        credentials = held.get("credentials")
        if credentials is None:
            with open(sa_json_path, encoding="utf-8") as handle:
                info = json.load(handle)
            credentials = held["credentials"] = service_account.Credentials.from_service_account_info(
                info, scopes=list(scopes))
        if not credentials.valid:
            credentials.refresh(Request())
        return str(credentials.token)

    return provide


def build_tools(env: Mapping[str, str], client: httpx.Client | None = None,
                key_dir: Path | None = None) -> tuple[list, str | None]:
    """The tools, or none and why."""
    problem = missing(env, ["GOOGLE_SA_JSON", "BQ_PROJECT", "BQ_DATASETS"])
    if problem:
        return [], problem
    project = credential(env, "BQ_PROJECT")
    try:
        datasets = parse_datasets(credential(env, "BQ_DATASETS"), project)
    except ValueError as error:
        return [], f"BQ_DATASETS: {error}"
    try:
        max_bytes = parse_max_bytes(env.get("BQ_MAX_GB"))
    except ValueError as error:
        return [], f"BQ_MAX_GB: {error}"
    client = client or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
    token_provider = service_account_token_provider(key_path(credential(env, "GOOGLE_SA_JSON"), key_dir), (SCOPE,))
    secret = env.get(access.NOTE_KEY_SETTING, "").strip() or None
    per_call = access.per_call(secret, client, MapCaches())
    return list(make_bq_tools(BigQuery(client, token_provider, project), datasets, max_bytes, per_call=per_call)), None


def main() -> None:
    tools, problem = build_tools(os.environ)
    serve(tools, int(os.environ.get("PORT") or 8080), problem, name="bigquery-reader")


if __name__ == "__main__":
    main()
