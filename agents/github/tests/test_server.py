import json
from pathlib import Path

from github_agent.server import DEFAULT_REPO, build_tools

CATALOG = json.loads((Path(__file__).resolve().parents[1] / "catalog-2.0.0.json").read_text(encoding="utf-8"))


def test_the_server_offers_exactly_the_tools_its_catalog_entry_names(tmp_path):
    tools, problem = build_tools({"GITHUB_REPO": "acme/app", "CACHE_DIR": str(tmp_path)})
    assert problem is None
    assert [t.name for t in tools] == CATALOG["router"]["agent"]["tools"]


def test_the_token_is_optional_and_the_repository_has_the_routers_default(tmp_path):
    tools, problem = build_tools({"CACHE_DIR": str(tmp_path)})
    assert problem is None and tools
    assert DEFAULT_REPO == "kaliperdev/langgraph-prototype"


def test_a_repository_that_is_not_owner_slash_name_is_refused():
    assert build_tools({"GITHUB_REPO": "just-a-name"}) == ([], "GITHUB_REPO must be 'owner/name', got 'just-a-name'")
