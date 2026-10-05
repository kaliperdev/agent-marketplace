import copy
import json
from pathlib import Path

import pytest

from marketplace.entry import parse_version, required_credentials, validate
from marketplace.seed import entries_from_files
from marketplace.views import router_registry


def _broken(entries, agent_id, mutate):
    entry = copy.deepcopy(next(e for e in entries if e["id"] == agent_id))
    mutate(entry)
    return validate(entry)


def test_every_seeded_entry_is_valid(entries):
    for entry in entries:
        assert validate(entry) == [], entry["id"]


def test_seeding_keeps_the_router_order(entries, registry):
    assert [e["id"] for e in entries] == [a["name"] for a in registry["agents"]]


def test_seeding_never_carries_per_client_sample_data(entries):
    for entry in entries:
        assert "sample" not in entry
        assert "sample" not in entry["display"]


def test_seeding_refuses_display_details_for_an_unknown_agent(registry, extras):
    with pytest.raises(ValueError, match="ghost"):
        entries_from_files(registry, {**extras, "ghost": extras["jira"]}, needs_router="0.1.0")


def test_seeding_refuses_an_agent_without_display_details(registry, extras):
    del extras["jira"]
    with pytest.raises(ValueError, match="jira"):
        entries_from_files(registry, extras, needs_router="0.1.0")


def test_the_router_registry_rebuilt_from_the_catalog_equals_the_original(entries, registry):
    assert router_registry(entries) == registry


def test_a_login_the_form_does_not_ask_for_is_refused(entries):
    errors = _broken(entries, "jira", lambda e: e["connection"]["fields"].pop())
    assert any("JIRA_API_TOKEN" in m for m in errors)


def test_a_tool_without_a_description_is_refused(entries):
    errors = _broken(entries, "jira", lambda e: e["router"]["tool_descriptions"].pop("read_jira_issue"))
    assert any("read_jira_issue" in m for m in errors)


def test_an_entry_naming_code_outside_the_router_is_refused(entries):
    errors = _broken(entries, "jira", lambda e: e["router"]["source_config"].__setitem__("tools_factory", "os:system"))
    assert any("tools_factory" in m for m in errors)


def test_a_bad_colour_and_a_bad_version_are_both_named(entries):
    def mutate(e):
        e["display"]["logo"]["color"] = "red;background:url(https://x)"
        e["version"] = "1.0"
    errors = _broken(entries, "jira", mutate)
    assert any("colour" in m for m in errors)
    assert any("version" in m for m in errors)


def test_a_passthrough_agent_needs_an_answerer(entries):
    errors = _broken(entries, "textql", lambda e: e["router"]["source_config"].__setitem__("answerer_factory", None))
    assert any("answerer_factory" in m for m in errors)


def test_an_unknown_kind_is_refused(entries):
    errors = _broken(entries, "jira", lambda e: e.__setitem__("kind", "plugin"))
    assert any("kind" in m for m in errors)


REMOTE_TEXTQL = Path(__file__).resolve().parent.parent / "agents" / "textql" / "catalog-2.0.0.json"


def _remote():
    return json.loads(REMOTE_TEXTQL.read_text(encoding="utf-8"))


def _remote_broken(mutate):
    entry = _remote()
    mutate(entry)
    return validate(entry)


def test_the_remote_textql_entry_is_valid():
    assert validate(_remote()) == []


def test_a_remote_agent_needs_a_service_with_an_image_and_a_port():
    assert any("service" in m for m in _remote_broken(lambda e: e.pop("service")))
    errors = _remote_broken(lambda e: e["service"].update({"image": "", "port": 0}))
    assert any("service.image" in m for m in errors)
    assert any("service.port" in m for m in errors)


def test_a_remote_agent_names_no_router_code():
    errors = _remote_broken(lambda e: e["router"]["source_config"].update({"tools_factory": "app.agents:jira_tools"}))
    assert any("must be empty" in m for m in errors)


def test_a_remote_agent_answers_whole_questions():
    errors = _remote_broken(lambda e: e["router"]["agent"].update({"passthrough": False}))
    assert any("passthrough" in m for m in errors)


def test_a_builtin_agent_cannot_carry_a_service(entries):
    errors = _broken(entries, "jira", lambda e: e.__setitem__("service", {"image": "x:1", "port": 1}))
    assert any("only a remote agent" in m for m in errors)


def test_not_an_object_is_refused():
    assert validate([1, 2]) == ["an entry must be a JSON object"]


def test_required_credentials_reads_both_spellings(entries):
    by_id = {e["id"]: e for e in entries}
    assert required_credentials(by_id["textql"]) == ["TEXTQL_API_KEY"]
    assert required_credentials(by_id["jira"]) == ["JIRA_DOMAIN", "JIRA_EMAIL", "JIRA_API_TOKEN"]
    assert required_credentials(by_id["github"]) == []


def test_versions_compare_as_numbers():
    assert parse_version("1.10.0") > parse_version("1.9.9")
    assert parse_version("1.0") is None
    assert parse_version(None) is None


def test_entries_are_plain_json(entries):
    assert json.loads(json.dumps(entries)) == entries


def test_a_version_written_with_leading_zeros_or_other_digits_is_refused(entries):
    for odd in ["1.0.00", "01.0.0", "١.٠.٠"]:
        errors = _broken(entries, "jira", lambda e, v=odd: e.__setitem__("version", v))
        assert any("version" in m for m in errors), odd


def test_client_data_cannot_ride_along_in_an_entry(entries):
    def mutate(e):
        e["sample"] = {"values": {"JIRA_API_TOKEN": "secret"}}
        e["display"]["sample"] = {"installed": True}
        e["connection"]["values"] = {"JIRA_API_TOKEN": "secret"}
    errors = _broken(entries, "jira", mutate)
    assert any("'sample'" in m and m.startswith("entry ") for m in errors)
    assert any("'sample'" in m and m.startswith("display ") for m in errors)
    assert any("'values'" in m and m.startswith("connection ") for m in errors)


def test_router_switches_must_be_true_or_false(entries):
    # The router reads any non-empty string as true: "false" would turn it ON.
    errors = _broken(entries, "jira", lambda e: e["router"]["agent"].__setitem__("enabled", "false"))
    assert any("enabled" in m for m in errors)


def test_validate_reports_wrong_types_instead_of_crashing(entries):
    def mutate(e):
        e["connection"]["covers"] = 5
        e["router"]["source_config"]["requires_credentials"] = 7
    errors = _broken(entries, "jira", mutate)
    assert any("covers" in m for m in errors)
    assert any("requires_credentials" in m for m in errors)


def _keys_anywhere(value):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from _keys_anywhere(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _keys_anywhere(inner)


def test_seeding_carries_no_per_client_sample_data_at_any_depth(entries):
    for entry in entries:
        assert "sample" not in set(_keys_anywhere(entry)), entry["id"]
        assert "values" not in set(_keys_anywhere(entry)), entry["id"]


def test_a_tool_listed_twice_or_a_field_asked_twice_is_refused(entries):
    def mutate(e):
        e["router"]["agent"]["tools"].append("read_jira_issue")
        e["connection"]["fields"].append(dict(e["connection"]["fields"][0]))
    errors = _broken(entries, "jira", mutate)
    assert any("read_jira_issue" in m and "twice" in m for m in errors)
    assert any("JIRA_DOMAIN" in m and "twice" in m for m in errors)


def test_a_tool_name_must_be_a_plain_identifier(entries):
    def mutate(e):
        e["router"]["agent"]["tools"][0] = "search jira"
        e["router"]["tool_descriptions"] = {
            ("search jira" if k == "search_jira_issues" else k): v for k, v in e["router"]["tool_descriptions"].items()
        }
    errors = _broken(entries, "jira", mutate)
    assert any("'search jira'" in m and "tool name" in m for m in errors)


def test_a_dropdown_needs_options(entries):
    errors = _broken(entries, "cliq", lambda e: e["connection"]["fields"][0].pop("options"))
    assert any("options" in m for m in errors)


def test_a_format_check_the_page_cannot_run_is_refused(entries):
    # The page checks fields in the browser with JavaScript's RegExp, which does
    # not understand Python's named groups.
    errors = _broken(entries, "jira", lambda e: e["connection"]["fields"][0].__setitem__("pattern", r"^(?P<site>[a-z]+)\.atlassian\.net$"))
    assert any("JIRA_DOMAIN" in m and "pattern" in m for m in errors)


AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"


def _mcp(agent_id="jira"):
    return json.loads((AGENTS_DIR / agent_id / "catalog-2.0.0.json").read_text(encoding="utf-8"))


def _mcp_broken(mutate, agent_id="jira"):
    entry = _mcp(agent_id)
    mutate(entry)
    return validate(entry)


@pytest.mark.parametrize("agent_id", ["jira", "github", "slack", "cliq", "workdrive", "gdocs"])
def test_the_mcp_entries_are_valid(agent_id):
    assert validate(_mcp(agent_id)) == []


def test_an_mcp_agent_needs_a_service():
    assert any("needs a service" in m for m in _mcp_broken(lambda e: e.pop("service")))


def test_an_mcp_agent_names_no_router_code():
    errors = _mcp_broken(lambda e: e["router"]["source_config"].update({"tools_factory": "app.agents:jira_tools"}))
    assert any("must be empty" in m for m in errors)


def test_an_mcp_agent_is_driven_by_the_router_not_passed_through():
    errors = _mcp_broken(lambda e: e["router"]["agent"].update({"passthrough": True}))
    assert any("not passthrough" in m for m in errors)


# ── what an entry may now say about itself (router 0.4.0) ────────────────────

import base64


@pytest.mark.parametrize("key,good,bad", [
    ("memory_mb", 512, 32),
    ("start_seconds", 90, 1),
])
def test_an_mcp_agent_may_ask_for_its_own_memory_and_start_time(key, good, bad):
    assert _mcp_broken(lambda e: e["service"].update({key: good})) == []
    assert any(f"service.{key}" in m for m in _mcp_broken(lambda e: e["service"].update({key: bad})))
    assert any(f"service.{key}" in m for m in _mcp_broken(lambda e: e["service"].update({key: True})))


def test_an_agent_may_set_its_own_time_limit_within_what_the_bot_waits():
    assert _mcp_broken(lambda e: e["router"]["agent"].update({"time_limit_seconds": 300})) == []
    for bad in (10, 301, "300"):
        errors = _mcp_broken(lambda e: e["router"]["agent"].update({"time_limit_seconds": bad}))
        assert any("time_limit_seconds" in m for m in errors), bad


def test_an_agent_may_say_it_holds_documents_and_what_to_ask_it_first():
    ok = _mcp_broken(lambda e: e["router"]["agent"].update(
        {"holds_documents": True, "first_for": "meetings and what was said in them"}))
    assert ok == []
    assert any("holds_documents" in m for m in _mcp_broken(
        lambda e: e["router"]["agent"].update({"holds_documents": "yes"})))
    for bad in ("", "x" * 201, 7):
        assert any("first_for" in m for m in _mcp_broken(
            lambda e: e["router"]["agent"].update({"first_for": bad}))), bad


def test_a_key_file_field_may_say_which_files_it_takes():
    def with_accept(accept, field_type="file"):
        def mutate(e):
            e["connection"]["fields"][0].update({"type": field_type, "accept": accept})
        return _mcp_broken(mutate)

    assert with_accept(".json,.pem,application/json") == []
    assert any("accept" in m for m in with_accept("json; rm -rf"))
    assert any("accept" in m for m in with_accept(".json", field_type="text"))


def _svg_logo(size=200):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect/></svg>' + b" " * size
    return "data:image/svg+xml;base64," + base64.b64encode(svg).decode()


def test_a_logo_may_travel_in_the_entry():
    assert _mcp_broken(lambda e: e["display"]["logo"].update({"image": _svg_logo()})) == []
    assert _mcp_broken(lambda e: e["display"]["logo"].update({"image": "logos/jira.svg"})) == []
    too_big = _svg_logo(size=64 * 1024)
    for bad in (too_big, "data:image/svg+xml;base64,not*base64", "data:text/html;base64,PGI+", "https://x.example/a.svg"):
        assert any("display.logo.image" in m for m in _mcp_broken(
            lambda e: e["display"]["logo"].update({"image": bad}))), bad[:40]
