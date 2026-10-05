"""Zoho Cliq as its own MCP server (catalog kind "mcp").

The tools are the router's Cliq tools; the router's model still drives them,
over MCP. Settings come from the environment the client's settings service
starts this with: ZOHO_DC (the region) from the form, and ZOHO_CLIENT_ID,
ZOHO_CLIENT_SECRET and ZOHO_REFRESH_TOKEN, which the Zoho sign-in covers.

  python -m cliq_agent.server
"""

from __future__ import annotations

import os
from collections.abc import Mapping

import httpx

from agent_kit.mcp_server import serve
from agent_kit.settings import credential, missing
from cliq_agent.zoho_cliq import CliqReader, make_cliq_tools

KEYS = ("ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN")
HTTP_TIMEOUT_SECONDS = 30.0


def build_tools(env: Mapping[str, str], client: httpx.Client | None = None) -> tuple[list, str | None]:
    """The tools, or none and why."""
    problem = missing(env, KEYS)
    if problem:
        return [], problem
    reader = CliqReader(
        dc=(env.get("ZOHO_DC") or "com").strip().lstrip("."),
        client_id=credential(env, "ZOHO_CLIENT_ID"),
        client_secret=credential(env, "ZOHO_CLIENT_SECRET"),
        refresh_token=credential(env, "ZOHO_REFRESH_TOKEN"),
        client=client or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS),
    )
    return list(make_cliq_tools(reader)), None


def main() -> None:
    tools, problem = build_tools(os.environ)
    serve(tools, int(os.environ.get("PORT") or 8080), problem, name="cliq")


if __name__ == "__main__":
    main()
