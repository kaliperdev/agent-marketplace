"""Slack as its own MCP server (catalog kind "mcp").

The tools are the router's Slack tools, unchanged; the router's model still
drives them, over MCP. Logins come from the environment the client's settings
service starts this with: SLACK_BOT_TOKEN and SLACK_USER_TOKEN. No cache: Slack
reads always go live (see slack.py).

  python -m slack_agent.server
"""

from __future__ import annotations

import os
from collections.abc import Mapping

import httpx

from agent_kit.mcp_server import serve
from agent_kit.settings import credential, missing
from slack_agent.slack import SlackReader, make_slack_tools

KEYS = ("SLACK_BOT_TOKEN", "SLACK_USER_TOKEN")
HTTP_TIMEOUT_SECONDS = 30.0


def build_tools(env: Mapping[str, str], client: httpx.Client | None = None) -> tuple[list, str | None]:
    """The tools, or none and why."""
    problem = missing(env, KEYS)
    if problem:
        return [], problem
    reader = SlackReader(
        bot_token=credential(env, "SLACK_BOT_TOKEN"),
        user_token=credential(env, "SLACK_USER_TOKEN"),
        client=client or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS),
    )
    return list(make_slack_tools(reader)), None


def main() -> None:
    tools, problem = build_tools(os.environ)
    serve(tools, int(os.environ.get("PORT") or 8080), problem, name="slack")


if __name__ == "__main__":
    main()
