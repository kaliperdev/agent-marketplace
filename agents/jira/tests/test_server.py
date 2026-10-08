import json
from pathlib import Path

from jira_agent.server import build_tools

CATALOG = json.loads((Path(__file__).resolve().parents[1] / "catalog-2.0.0.json").read_text(encoding="utf-8"))
ENV = {"JIRA_DOMAIN": "acme.atlassian.net", "JIRA_EMAIL": "ops@acme.example", "JIRA_API_TOKEN": "tok-abcd"}


def test_the_server_offers_exactly_the_tools_its_catalog_entry_names(tmp_path):
    tools, problem = build_tools({**ENV, "CACHE_DIR": str(tmp_path)})
    assert problem is None
    assert [t.name for t in tools] == CATALOG["router"]["agent"]["tools"]


def test_without_its_logins_it_offers_nothing_and_says_why():
    assert build_tools({"JIRA_DOMAIN": "acme.atlassian.net"}) == ([], "JIRA_EMAIL, JIRA_API_TOKEN are not set")
