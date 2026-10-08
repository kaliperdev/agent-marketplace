import json
from pathlib import Path

from workdrive_agent.server import build_tools

CATALOG = json.loads((Path(__file__).resolve().parents[1] / "catalog-2.0.0.json").read_text(encoding="utf-8"))
ENV = {"WORKDRIVE_MCP_URL": "https://mcp.example.invalid/mcp/abc", "WORKDRIVE_CLIENT_ID": "1000.x",
       "WORKDRIVE_REFRESH_TOKEN": "1000.r"}


def test_the_server_offers_exactly_the_tools_its_catalog_entry_names():
    tools, problem = build_tools(ENV)
    assert problem is None
    # The router offers them in the catalog's order, whatever order they are listed in.
    names = [t.name for t in tools]
    assert sorted(names) == sorted(CATALOG["router"]["agent"]["tools"]) and len(set(names)) == len(names)


def test_without_its_zoho_logins_it_offers_nothing_and_says_why():
    assert build_tools({"WORKDRIVE_MCP_URL": "https://mcp.example.invalid/mcp/abc"}) == (
        [], "WORKDRIVE_CLIENT_ID, WORKDRIVE_REFRESH_TOKEN are not set")
