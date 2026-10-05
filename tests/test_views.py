import copy
import json

import pytest

from marketplace.views import page_view, router_registry

PAGE_KEYS = {
    "id", "name", "publisher", "version", "needsRouter", "summary", "categories", "logo",
    "kind", "routerDescription", "capabilities", "credentials", "connection", "examples",
}


def test_the_page_view_has_the_shape_the_catalog_page_reads(entries):
    jira = page_view(next(e for e in entries if e["id"] == "jira"))
    assert set(jira) == PAGE_KEYS
    assert [c["name"] for c in jira["capabilities"]] == ["search_jira_issues", "read_jira_issue", "list_jira_projects"]
    assert all(c["description"] for c in jira["capabilities"])
    assert jira["kind"] == "tools"
    assert jira["credentials"] == ["JIRA_DOMAIN", "JIRA_EMAIL", "JIRA_API_TOKEN"]


def test_whole_question_agents_show_as_answering(entries):
    kinds = {e["id"]: page_view(e)["kind"] for e in entries}
    assert kinds["textql"] == "answers"
    assert kinds["knowledge"] == "answers"


def test_the_repository_placeholder_never_reaches_the_page(entries):
    assert "{github_repo}" not in json.dumps([page_view(e) for e in entries])


def test_agents_that_disagree_about_a_shared_source_are_an_error(entries):
    a = copy.deepcopy(next(e for e in entries if e["id"] == "jira"))
    b = copy.deepcopy(a)
    b["id"] = b["router"]["agent"]["name"] = "jira-two"
    b["router"]["source_config"] = {**b["router"]["source_config"], "always_enabled": True}
    with pytest.raises(ValueError, match="jira"):
        router_registry([a, b])


def test_the_router_file_holds_only_builtin_agents(entries):
    import json as _json
    from pathlib import Path as _Path

    from marketplace.views import router_registry as _router_registry

    remote = _json.loads((_Path(__file__).resolve().parent.parent / "agents" / "textql" / "catalog-2.0.0.json").read_text(encoding="utf-8"))
    builtin = [e for e in entries if e["id"] != "textql"]
    assert _router_registry(builtin + [remote]) == _router_registry(builtin)


def test_the_page_view_says_which_router_an_agent_needs(entries):
    from marketplace.views import page_view as _page_view

    assert _page_view(entries[0])["needsRouter"] == entries[0]["needs_router"]


def test_any_setting_placeholder_reads_as_plain_words_on_the_page(entries):
    # The router fills {space_key} from what a server saved; the catalog has no
    # server, so the page says what it stands for instead of showing braces.
    entry = copy.deepcopy(entries[0])
    entry["router"]["agent"]["description"] = "Reads {space_key}, beside {github_repo}."
    assert page_view(entry)["routerDescription"] == "Reads the configured space key, beside the configured repository."
