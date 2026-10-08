"""GitHub as its own MCP server (catalog kind "mcp").

The tools are the router's GitHub tools, unchanged; the router's model still
drives them, over MCP. Settings come from the environment the client's
settings service starts this with: GITHUB_REPO ("owner/name") and an optional
GITHUB_TOKEN (needed for a private repository and for code search).

  python -m github_agent.server
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import httpx

from agent_kit.cache import DiskCache
from agent_kit.mcp_server import serve
from agent_kit.settings import credential
from github_agent.github import GitHubReader, make_github_tools

# The router's own default (router/app/config.py), so an unset repo means the same thing.
DEFAULT_REPO = "kaliperdev/langgraph-prototype"
HTTP_TIMEOUT_SECONDS = 30.0


def build_tools(env: Mapping[str, str], client: httpx.Client | None = None) -> tuple[list, str | None]:
    """The tools, or none and why."""
    repo = (env.get("GITHUB_REPO") or "").strip() or DEFAULT_REPO
    owner, _, name = repo.partition("/")
    if not owner or not name or "/" in name:
        return [], f"GITHUB_REPO must be 'owner/name', got {repo!r}"
    reader = GitHubReader(
        repo=repo,
        client=client or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS),
        cache=DiskCache(Path(env.get("CACHE_DIR") or ".cache") / "github"),
        token=credential(env, "GITHUB_TOKEN"),
    )
    return list(make_github_tools(reader)), None


def main() -> None:
    tools, problem = build_tools(os.environ)
    serve(tools, int(os.environ.get("PORT") or 8080), problem, name="github")


if __name__ == "__main__":
    main()
