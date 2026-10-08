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


@pytest.mark.parametrize("path", sorted(AGENTS_DIR.glob("*/catalog-*.json")), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_entry_in_the_agents_folder_is_valid(path):
    entry = json.loads(path.read_text(encoding="utf-8"))
    assert validate(entry) == []
    assert path.name == f"catalog-{entry['version']}.json" and path.parent.name == entry["id"]


# ── an MCP server the vendor runs (router 0.5.0) ─────────────────────────────

from marketplace.entry import safe_tool_name


def _linear():
    return {
        "format": 1, "id": "linear", "version": "1.0.0", "kind": "mcp",
        "service": {"url": "https://mcp.linear.app/mcp/readonly", "timeout_seconds": 60},
        "needs_router": "0.5.0", "publisher": "Kaliper",
        "display": {"name": "Linear", "summary": "Reads Linear issues and projects.", "categories": ["Engineering"],
                    "logo": {"letter": "L", "color": "#5E6AD2"}, "examples": ["What is open in the Mobile project?"]},
        "connection": {"method": "form", "send": {"bearer": "LINEAR_API_KEY"}, "fields": [
            {"key": "LINEAR_API_KEY", "label": "API key", "type": "password", "required": True}]},
        "router": {"source": "linear", "source_config": {},
                   "agent": {"name": "linear", "source": "linear", "tools": ["list_issues", "get_issue"],
                             "description": "Reads Linear: issues and their status.", "owns": "You own Linear issues."},
                   "tool_descriptions": {"list_issues": "List issues.", "get_issue": "Read one issue."}},
    }


def _linear_broken(mutate):
    entry = _linear()
    mutate(entry)
    return validate(entry)


def test_an_mcp_server_the_vendor_runs_is_valid():
    assert validate(_linear()) == []


@pytest.mark.parametrize("url", [
    "http://mcp.linear.app/mcp", "https://user:pw@mcp.linear.app/mcp", "https://mcp.linear.app/mcp#x",
    "https://localhost/mcp", "mcp.linear.app/mcp", "https://" + "a" * 600 + ".com/mcp",
])
def test_the_vendors_address_must_be_a_plain_https_address(url):
    assert any("service.url" in m for m in _linear_broken(lambda e: e["service"].update({"url": url})))


def test_a_service_is_either_on_the_clients_server_or_the_vendors():
    errors = _linear_broken(lambda e: e["service"].update({"image": "kaliper/agent-linear:1.0.0", "port": 8080}))
    assert any("not both" in m for m in errors)
    for key, value in (("memory_mb", 256), ("start_seconds", 30)):
        assert any(f"service.{key}" in m for m in _linear_broken(lambda e: e["service"].update({key: value}))), key


def test_a_vendors_server_may_cap_how_much_text_a_tool_returns():
    assert _linear_broken(lambda e: e["service"].update({"max_result_chars": 40000})) == []
    for bad in (10, 500000):
        assert any("max_result_chars" in m for m in _linear_broken(
            lambda e: e["service"].update({"max_result_chars": bad}))), bad
    assert any("max_result_chars" in m for m in _mcp_broken(lambda e: e["service"].update({"max_result_chars": 40000})))


def test_a_vendors_server_needs_router_0_5_0():
    assert any("0.5.0" in m for m in _linear_broken(lambda e: e.update({"needs_router": "0.4.0"})))


def test_how_the_key_is_sent_must_be_said_and_must_name_a_secret_field():
    assert any("connection.send" in m for m in _linear_broken(lambda e: e["connection"].pop("send")))
    assert any("connection.send" in m for m in _linear_broken(
        lambda e: e["connection"].update({"send": {"bearer": "NOT_A_FIELD"}})))
    assert any("connection.send" in m for m in _linear_broken(
        lambda e: e["connection"]["fields"][0].update({"type": "text"})))
    assert _linear_broken(lambda e: e["connection"].update(
        {"send": {"header": "X-Api-Key", "field": "LINEAR_API_KEY"}})) == []
    for header in ("Mcp-Session-Id", "Host", "bad header"):
        assert any("connection.send" in m for m in _linear_broken(
            lambda e: e["connection"].update({"send": {"header": header, "field": "LINEAR_API_KEY"}}))), header
    # Only a vendor's server is sent a key this way.
    assert any("connection.send" in m for m in _mcp_broken(
        lambda e: e["connection"].update({"send": {"bearer": e["connection"]["fields"][0]["key"]}})))


def test_a_vendors_tool_names_are_its_own():
    names = ["notion-search", "searchJiraIssuesUsingJql", "fireflies_get_transcript", "a.b", "github/search_code",
             "slack:read"]
    def mutate(e):
        e["router"]["agent"]["tools"] = names
        e["router"]["tool_descriptions"] = {n: "d" for n in names}
    assert _linear_broken(mutate) == []


@pytest.mark.parametrize("names,why", [
    (["a.b", "a_b"], "the same"),
    (["current_time"], "current_time"),
    (["has space"], "not a valid tool name (letters, digits, _ - . : and /, up to 128)"),
])
def test_a_vendors_tool_names_must_stay_distinct_when_made_safe(names, why):
    def mutate(e):
        e["router"]["agent"]["tools"] = names
        e["router"]["tool_descriptions"] = {n: "d" for n in names}
    assert any(why in m for m in _linear_broken(mutate)), names


def test_a_safe_tool_name_is_what_every_model_accepts():
    # Anthropic allows ^[a-zA-Z0-9_-]{1,128}$, OpenAI ^[a-zA-Z0-9_-]{1,64}$: the overlap.
    assert safe_tool_name("notion-search") == "notion-search"
    assert safe_tool_name("jira.search/issues") == "jira_search_issues"
    assert len(safe_tool_name("x" * 100)) == 64


def test_signing_in_to_a_vendors_server_is_not_offered_before_router_0_6_0():
    errors = _linear_broken(lambda e: e["connection"].update({"method": "signin", "provider": "Linear"}))
    assert any("0.6.0" in m for m in errors)


def test_a_key_may_be_sent_as_basic_from_two_fields():
    # Atlassian's API tokens: Authorization: Basic base64(email:token).
    def basic(e, pair=("ATLASSIAN_EMAIL", "LINEAR_API_KEY")):
        e["connection"]["fields"].insert(0, {"key": "ATLASSIAN_EMAIL", "label": "Email", "type": "email",
                                             "required": True})
        e["connection"]["send"] = {"basic": list(pair) if isinstance(pair, tuple) else pair}
    assert _linear_broken(basic) == []
    for bad in (("LINEAR_API_KEY", "LINEAR_API_KEY"), ("ATLASSIAN_EMAIL",), "ATLASSIAN_EMAIL", ({"x": 1}, "LINEAR_API_KEY")):
        assert any("connection.send" in m for m in _linear_broken(lambda e, bad=bad: basic(e, bad))), bad


# ── "Sign in with…" (router 0.6.0) ──────────────────────────────────────────


def _notion():
    entry = _linear()
    entry.update({"id": "notion", "needs_router": "0.6.0"})
    entry["service"] = {"url": "https://mcp.notion.com/mcp"}
    entry["display"]["name"] = "Notion"
    entry["connection"] = {"method": "signin", "provider": "Notion", "signin": {"client": "automatic"}, "fields": []}
    entry["router"]["source"] = "notion"
    entry["router"]["agent"].update({"name": "notion", "source": "notion", "tools": ["notion-search"]})
    entry["router"]["tool_descriptions"] = {"notion-search": "Search Notion."}
    return entry


def _asana():
    entry = _notion()
    entry["service"] = {"url": "https://mcp.asana.com/v2/mcp"}
    entry["connection"] = {"method": "signin", "provider": "Asana", "signin": {
        "client": "own-app", "client_id_field": "ASANA_CLIENT_ID", "client_secret_field": "ASANA_CLIENT_SECRET"},
        "fields": [{"key": "ASANA_CLIENT_ID", "label": "App client ID", "type": "text", "required": True},
                   {"key": "ASANA_CLIENT_SECRET", "label": "App client secret", "type": "password", "required": True}]}
    return entry


def _own_google():
    entry = _asana()
    entry["service"] = {"image": "kaliper/agent-gdrive:1.0.0", "port": 8080}
    entry["router"]["agent"]["tools"] = ["search_files"]
    entry["router"]["tool_descriptions"] = {"search_files": "Search Drive."}
    entry["connection"]["provider"] = "Google"
    entry["connection"]["signin"].update({
        "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/drive.readonly"],
        "authorize_params": {"access_type": "offline", "prompt": "consent"}})
    return entry


@pytest.mark.parametrize("make", [_notion, _asana, _own_google], ids=["automatic", "own-app", "our-own-agent"])
def test_the_three_shapes_of_sign_in_are_valid(make):
    assert validate(make()) == []


def _broken_signin(make, mutate):
    entry = make()
    mutate(entry["connection"]["signin"], entry)
    return validate(entry)


@pytest.mark.parametrize("make,mutate,why", [
    (_own_google, lambda s, e: s.update({"client": "automatic", "client_id_field": None}), "automatic"),
    (_notion, lambda s, e: s.update({"client": "magic"}), "client"),
    (_asana, lambda s, e: s.pop("client_id_field"), "client_id_field"),
    (_asana, lambda s, e: s.update({"client_id_field": "ASANA_CLIENT_SECRET"}), "client_id_field"),
    (_asana, lambda s, e: s.update({"client_secret_field": "ASANA_CLIENT_ID"}), "client_secret_field"),
    (_own_google, lambda s, e: s.pop("token_url"), "token_url"),
    (_own_google, lambda s, e: s.update({"token_url": "http://oauth2.googleapis.com/token"}), "token_url"),
    (_own_google, lambda s, e: s.update({"authorize_params": {"state": "x"}}), "authorize_params"),
    (_own_google, lambda s, e: s.update({"authorize_params": {f"k{i}": "v" for i in range(9)}}), "authorize_params"),
    (_own_google, lambda s, e: s.update({"authorize_params": {"prompt": 1}}), "authorize_params"),
    (_own_google, lambda s, e: s.update({"scopes": ["read", ""]}), "scopes"),
    (_notion, lambda s, e: s.update({"resource": "yes"}), "resource"),
    (_notion, lambda s, e: s.update({"extra": 1}), "unknown key"),
    (_notion, lambda s, e: e["connection"].update({"covers": ["NOTION_TOKEN"]}), "covers"),
    (_notion, lambda s, e: e.update({"needs_router": "0.5.0"}), "0.6.0"),
    (_notion, lambda s, e: e["connection"].update({"method": "form"}), "connection.signin"),
    (_notion, lambda s, e: e["connection"].update({"send": {"bearer": "X"}}), "connection.send"),
])
def test_a_sign_in_that_cannot_work_is_refused(make, mutate, why):
    assert any(why in m for m in _broken_signin(make, mutate)), why


@pytest.mark.parametrize("kind", ["remote", "builtin"])
def test_only_an_mcp_agent_can_sign_in(kind):
    # The router hands a sign-in's token only to an MCP agent's tool calls: an
    # agent of any other kind would sign in and never be given its token.
    entry = _own_google()
    entry["kind"] = kind
    assert any("kind mcp" in m for m in validate(entry))


def test_a_sign_in_entry_is_written_out_in_order():
    from marketplace.entry import ordered

    signin = ordered(_own_google())["connection"]["signin"]
    assert list(signin) == ["client", "client_id_field", "client_secret_field", "authorization_url", "token_url",
                            "scopes", "authorize_params"]


def test_an_agent_may_name_the_tools_that_change_something():
    def writes(value, needs="0.7.0"):
        def mutate(e):
            e["needs_router"] = needs
            e["router"]["agent"]["writes"] = value
        return _mcp_broken(mutate)

    first = _mcp("jira")["router"]["agent"]["tools"][0]
    assert writes([first]) == []
    assert any("writes" in m for m in writes([])), "an empty list says nothing"
    assert any("writes" in m for m in writes(["not_a_tool"]))
    assert any("writes" in m for m in writes("create_issue"))
    assert any("0.7.0" in m for m in writes([first], needs="0.6.0"))
