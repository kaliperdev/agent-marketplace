import json
from pathlib import Path

import pytest

from cliq_agent.server import build_tools
from cliq_agent.zoho_cliq import CliqError, CliqReader

CATALOG = json.loads((Path(__file__).resolve().parents[1] / "catalog-2.0.0.json").read_text(encoding="utf-8"))
ENV = {"ZOHO_DC": "in", "ZOHO_CLIENT_ID": "1000.x", "ZOHO_CLIENT_SECRET": "s", "ZOHO_REFRESH_TOKEN": "1000.r"}


def test_the_server_offers_exactly_the_tools_its_catalog_entry_names():
    tools, problem = build_tools(ENV)
    assert problem is None
    assert [t.name for t in tools] == CATALOG["router"]["agent"]["tools"]


def test_without_its_zoho_logins_it_offers_nothing_and_says_why():
    assert build_tools({"ZOHO_DC": "in"}) == ([], "ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN are not set")


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_the_read_limit_is_per_minute_not_for_the_life_of_the_service(monkeypatch):
    # In the router the limit was per reader, and a reader lived as long as the
    # router's agent set: after 8 reads, Cliq answered nothing until a restart.
    clock = _Clock()
    reader = CliqReader("in", "id", "secret", "refresh", client=None, read_budget=2, clock=clock)
    monkeypatch.setattr(reader, "_get", lambda path, params: {"data": [{"text": "hi"}]})
    reader.messages("c1")
    reader.messages("c1")
    with pytest.raises(CliqError, match="per minute"):
        reader.messages("c1")
    clock.now += 61
    assert reader.messages("c1") == [{"text": "hi"}]
