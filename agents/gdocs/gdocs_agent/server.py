"""Google Docs as its own MCP server (catalog kind "mcp").

The tools are the router's Google Docs tools, unchanged; the router's model
still drives them, over MCP. GOOGLE_SA_JSON is the service account's key: the
client's settings service passes the key file's CONTENTS (a path on that server
means nothing in here), which are written to a private file for Google's
library. A path still works, for running this by hand.

  python -m gdocs_agent.server
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

import httpx

from agent_kit.cache import DiskCache
from agent_kit.mcp_server import serve
from agent_kit.settings import credential, missing
from gdocs_agent.drive import DRIVE_SCOPE, DriveIndex
from gdocs_agent.gdocs import DOCS_SCOPE, DocsReader, make_docs_tools, service_account_token_provider

SA_SCOPES = (DOCS_SCOPE, DRIVE_SCOPE)
HTTP_TIMEOUT_SECONDS = 30.0


def key_path(value: str, key_dir: Path | None = None) -> str:
    """A path to the key: the value itself, or a private file holding it."""
    if not value.lstrip().startswith("{"):
        return value
    handle, path = tempfile.mkstemp(prefix="sa-", suffix=".json", dir=key_dir)
    with os.fdopen(handle, "w", encoding="utf-8") as out:  # mkstemp makes it 0600
        out.write(value)
    return path


def build_tools(env: Mapping[str, str], client: httpx.Client | None = None,
                key_dir: Path | None = None) -> tuple[list, str | None]:
    """The tools, or none and why. Wired as the router's gdocs_tools wires them."""
    problem = missing(env, ["GOOGLE_SA_JSON"])
    if problem:
        return [], problem
    client = client or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
    token_provider = service_account_token_provider(key_path(credential(env, "GOOGLE_SA_JSON"), key_dir), SA_SCOPES)
    index = DriveIndex(client=client, token_provider=token_provider)
    reader = DocsReader(client=client, token_provider=token_provider,
                        cache=DiskCache(Path(env.get("CACHE_DIR") or ".cache") / "gdocs"))
    return list(make_docs_tools(index, reader)), None


def main() -> None:
    tools, problem = build_tools(os.environ)
    serve(tools, int(os.environ.get("PORT") or 8080), problem, name="gdocs")


if __name__ == "__main__":
    main()
