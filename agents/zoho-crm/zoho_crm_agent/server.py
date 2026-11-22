"""Draft service entry point; publication waits for a Zoho live probe."""

from __future__ import annotations

import os
import socket
from collections.abc import Mapping

import httpx

from agent_kit.mcp_server import serve
from agent_kit.settings import credential, missing

from .auth import refresh_token_provider
from .mcp import ZohoCRMDataInsightsClient
from .security import Resolver, UnsafeDestination, validate_mcp_url

KEYS = ("ZOHO_CRM_MCP_URL", "ZOHO_CRM_CLIENT_ID", "ZOHO_CRM_REFRESH_TOKEN")
HTTP_TIMEOUT_SECONDS = 30.0
LIVE_PROBE_GATE = (
    "Zoho CRM Data Insights tool names, schemas, OAuth metadata, and read-only behavior "
    "must pass a live probe before any tools are exposed."
)


def build_client(
    env: Mapping[str, str],
    client: httpx.Client | None = None,
    resolver: Resolver = socket.getaddrinfo,
) -> tuple[ZohoCRMDataInsightsClient | None, str | None]:
    """Build the guarded MCP/OAuth client; no remote calls occur until used."""
    problem = missing(env, KEYS)
    if problem:
        return None, problem
    try:
        url = validate_mcp_url(credential(env, "ZOHO_CRM_MCP_URL"))
    except UnsafeDestination:
        return None, "ZOHO_CRM_MCP_URL must be a Zoho CRM Data Insights URL"

    client = client or httpx.Client(
        timeout=HTTP_TIMEOUT_SECONDS,
        follow_redirects=False,
        trust_env=False,
    )
    token = refresh_token_provider(
        url,
        credential(env, "ZOHO_CRM_CLIENT_ID"),
        credential(env, "ZOHO_CRM_REFRESH_TOKEN"),
        client,
        resolver,
    )
    return ZohoCRMDataInsightsClient(url, token, client, resolver), None


def startup_problem(env: Mapping[str, str]) -> str | None:
    """Check connection shape while keeping the service explicitly inert."""
    problem = missing(env, KEYS)
    if problem:
        return problem
    try:
        validate_mcp_url(credential(env, "ZOHO_CRM_MCP_URL"))
    except UnsafeDestination:
        return "ZOHO_CRM_MCP_URL must be a Zoho CRM Data Insights URL"
    return LIVE_PROBE_GATE


def main() -> None:
    # The draft service intentionally publishes no tools before the live-probe
    # gate. A successful connection shape check does not satisfy that gate.
    serve([], int(os.environ.get("PORT") or 8080), startup_problem(os.environ), name="zoho-crm-data-insights")


if __name__ == "__main__":
    main()
