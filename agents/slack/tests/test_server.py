import json
from pathlib import Path

from slack_agent.server import build_tools

CATALOG = json.loads((Path(__file__).resolve().parents[1] / "catalog-2.0.0.json").read_text(encoding="utf-8"))
ENV = {"SLACK_BOT_TOKEN": "xoxb-1-abc", "SLACK_USER_TOKEN": "xoxp-1-abc"}


def test_the_server_offers_exactly_the_tools_its_catalog_entry_names():
    tools, problem = build_tools(ENV)
    assert problem is None
    assert [t.name for t in tools] == CATALOG["router"]["agent"]["tools"]


def test_without_its_tokens_it_offers_nothing_and_says_why():
    assert build_tools({"SLACK_BOT_TOKEN": "xoxb-REPLACE-ME"}) == ([], "SLACK_BOT_TOKEN, SLACK_USER_TOKEN are not set")
