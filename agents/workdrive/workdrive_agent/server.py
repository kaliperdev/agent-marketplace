"""Zoho WorkDrive as its own MCP server (catalog kind "mcp").

The tools are the router's WorkDrive tools, unchanged; the router's model still
drives them, over MCP. They in turn reach Zoho's own WorkDrive MCP server, and
read file contents through Zoho's download API, as they did in the router.
Settings come from the environment the client's settings service starts this
with: WORKDRIVE_MCP_URL from the form, and WORKDRIVE_CLIENT_ID and
WORKDRIVE_REFRESH_TOKEN, which the Zoho sign-in covers.

  python -m workdrive_agent.server
"""

from __future__ import annotations

import os
from collections.abc import Mapping

import httpx

from agent_kit.mcp_server import serve
from agent_kit.settings import credential, missing
from workdrive_agent.mcp import MCPClient, refresh_token_provider
from workdrive_agent.workdrive import WorkDriveReader, make_workdrive_tools
from workdrive_agent.workdrive_direct import WorkDriveDirect

KEYS = ("WORKDRIVE_MCP_URL", "WORKDRIVE_CLIENT_ID", "WORKDRIVE_REFRESH_TOKEN")
HTTP_TIMEOUT_SECONDS = 30.0


def build_tools(env: Mapping[str, str], client: httpx.Client | None = None) -> tuple[list, str | None]:
    """The tools, or none and why. Wired as the router's workdrive_tools wires them."""
    problem = missing(env, KEYS)
    if problem:
        return [], problem
    url = credential(env, "WORKDRIVE_MCP_URL")
    client = client or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
    token = refresh_token_provider(url, credential(env, "WORKDRIVE_CLIENT_ID"),
                                   credential(env, "WORKDRIVE_REFRESH_TOKEN"), client)
    reader = WorkDriveReader(MCPClient(url=url, token=token, client=client))
    content = None
    if (env.get("WORKDRIVE_DIRECT_API") or "true").strip().lower() not in ("false", "0", "no"):
        # The download host is named by the token response, so it is read lazily.
        def api_domain() -> str:
            token()
            return getattr(token, "api_domain", lambda: "")() or "https://www.zohoapis.com"

        content = WorkDriveDirect(api_domain=api_domain, token=token, client=client)
    return list(make_workdrive_tools(reader, content)), None


def main() -> None:
    tools, problem = build_tools(os.environ)
    serve(tools, int(os.environ.get("PORT") or 8080), problem, name="workdrive")


if __name__ == "__main__":
    main()
