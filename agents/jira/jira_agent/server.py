"""Jira as its own MCP server (catalog kind "mcp").

The tools are the router's Jira tools, unchanged; the router's model still
drives them, over MCP. Logins come from the environment the client's settings
service starts this with: JIRA_DOMAIN, JIRA_EMAIL, JIRA_API_TOKEN.

  python -m jira_agent.server
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import httpx

from agent_kit.cache import DiskCache
from agent_kit.mcp_server import serve
from agent_kit.settings import credential, missing
from jira_agent.jira import JiraReader, make_jira_tools

KEYS = ("JIRA_DOMAIN", "JIRA_EMAIL", "JIRA_API_TOKEN")
HTTP_TIMEOUT_SECONDS = 30.0


def build_tools(env: Mapping[str, str], client: httpx.Client | None = None) -> tuple[list, str | None]:
    """The tools, or none and why."""
    problem = missing(env, KEYS)
    if problem:
        return [], problem
    reader = JiraReader(
        domain=credential(env, "JIRA_DOMAIN"),
        email=credential(env, "JIRA_EMAIL"),
        token=credential(env, "JIRA_API_TOKEN"),
        client=client or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS),
        cache=DiskCache(Path(env.get("CACHE_DIR") or ".cache") / "jira"),
    )
    return list(make_jira_tools(reader)), None


def main() -> None:
    tools, problem = build_tools(os.environ)
    serve(tools, int(os.environ.get("PORT") or 8080), problem, name="jira")


if __name__ == "__main__":
    main()
