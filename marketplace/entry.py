"""The catalog entry: one version of one agent, as one JSON object.

The same shape is imported, stored, exported and served, so there is one format
to keep straight. FORMAT names it: a later shape gets FORMAT = 2 and a converter,
rather than silently changing what the rows already stored mean.

validate() returns every problem it finds, not just the first, each worded for
the person who has to fix the file.
"""

from __future__ import annotations

import re
from typing import Any

FORMAT = 1
# builtin: the code is inside the router. remote: the agent runs as its own
# service (its own container on the client's server) and answers whole
# questions. mcp: the agent runs as its own MCP server and the router's model
# drives its tools.
KINDS = {"builtin", "remote", "mcp"}
# An agent service's package name, like kaliper/agent-textql:2.0.0.
_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/-]*:[A-Za-z0-9._-]+$")
METHODS = {"form", "signin", "builtin"}
FIELD_TYPES = {"text", "password", "email", "file", "select"}

_ID = re.compile(r"^[a-z][a-z0-9_-]*$")
# One spelling per number: no leading zeros, ASCII digits only. Otherwise
# "1.0.00" is a new version text for the same number, and slips past the
# "bump the version" rule.
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_LOGO_IMAGE = re.compile(r"^logos/[a-z0-9-]+\.(svg|png)$")
# The router imports whatever the catalog names here (importlib, in
# router/app/agents.py), so only code inside the router package is allowed.
_FACTORY = re.compile(r"^app(\.[a-z_]+)+:[a-z_]+$")

# Format 1 is a closed shape: anything else is refused, so nothing per-client
# (a `sample`, saved `values`, a login) can ride along into the global catalog.
# Each list is also the order entries are written out in: the database (jsonb)
# re-sorts keys, so without this every export would come out scrambled.
TOP_ORDER = ["format", "id", "version", "kind", "service", "needs_router", "publisher", "display", "connection", "router"]
SERVICE_ORDER = ["image", "port", "timeout_seconds"]
DISPLAY_ORDER = ["name", "summary", "categories", "logo", "examples"]
LOGO_ORDER = ["letter", "color", "image"]
CONNECTION_ORDER = ["method", "provider", "covers", "note", "fields"]
FIELD_ORDER = ["key", "label", "type", "required", "placeholder", "default", "options", "pattern", "patternHelp", "help"]
ROUTER_ORDER = ["source", "source_config", "agent", "tool_descriptions"]
# The router's own order in router/config/agents.json.
AGENT_ORDER = ["name", "source", "tools", "description", "owns", "passthrough", "verbatim", "enabled", "extra_tools"]
SOURCE_ORDER = ["tools_factory", "answerer_factory", "requires_credential", "requires_credentials", "always_enabled"]
TOP_KEYS, DISPLAY_KEYS, LOGO_KEYS = set(TOP_ORDER), set(DISPLAY_ORDER), set(LOGO_ORDER)
CONNECTION_KEYS, FIELD_KEYS, ROUTER_KEYS = set(CONNECTION_ORDER), set(FIELD_ORDER), set(ROUTER_ORDER)
AGENT_KEYS, SOURCE_KEYS, SERVICE_KEYS = set(AGENT_ORDER), set(SOURCE_ORDER), set(SERVICE_ORDER)

_TOOL = re.compile(r"^[a-z][a-z0-9_]*$")
# The page re-checks fields in the browser with JavaScript's RegExp, which has
# no Python named groups, comments or \A / \Z anchors.
_PYTHON_ONLY_REGEX = re.compile(r"\(\?P[<=]|\(\?#|\\[AZ]")
# The router tests these with truthiness, so "false" (a string) would mean ON.
AGENT_SWITCHES = ("passthrough", "verbatim", "enabled")


def parse_version(text: Any) -> tuple[int, int, int] | None:
    match = _SEMVER.match(str(text or ""))
    return tuple(int(p) for p in match.groups()) if match else None  # type: ignore[return-value]


def required_credentials(entry: dict) -> list[str]:
    router = entry.get("router") if isinstance(entry.get("router"), dict) else {}
    config = router.get("source_config") if isinstance(router.get("source_config"), dict) else {}
    many = config.get("requires_credentials")
    if isinstance(many, list):
        return [c for c in many if isinstance(c, str)]
    one = config.get("requires_credential")
    return [one] if isinstance(one, str) and one else []


def _in_order(obj: Any, order: list[str]) -> Any:
    if not isinstance(obj, dict):
        return obj
    known = {key: obj[key] for key in order if key in obj}
    return known | {key: value for key, value in obj.items() if key not in known}


def ordered(entry: dict) -> dict:
    """The entry with every part's keys in their written-out order."""
    out = _in_order(entry, TOP_ORDER)
    if isinstance(out.get("service"), dict):
        out["service"] = _in_order(out["service"], SERVICE_ORDER)
    if isinstance(out.get("display"), dict):
        out["display"] = _in_order(out["display"], DISPLAY_ORDER)
        out["display"]["logo"] = _in_order(out["display"].get("logo"), LOGO_ORDER)
    if isinstance(out.get("connection"), dict):
        out["connection"] = _in_order(out["connection"], CONNECTION_ORDER)
        if isinstance(out["connection"].get("fields"), list):
            out["connection"]["fields"] = [_in_order(f, FIELD_ORDER) for f in out["connection"]["fields"]]
    router = out.get("router")
    if isinstance(router, dict):
        router = out["router"] = _in_order(router, ROUTER_ORDER)
        router["source_config"] = _in_order(router.get("source_config"), SOURCE_ORDER)
        router["agent"] = _in_order(router.get("agent"), AGENT_ORDER)
        tools = router["agent"].get("tools") if isinstance(router["agent"], dict) else None
        if isinstance(router.get("tool_descriptions"), dict) and isinstance(tools, list):
            router["tool_descriptions"] = _in_order(router["tool_descriptions"], [t for t in tools if isinstance(t, str)])
    return out


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _strings(value: Any, allow_empty: bool = False) -> bool:
    return isinstance(value, list) and (allow_empty or len(value) > 0) and all(_text(v) for v in value)


def _object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def validate(entry: Any) -> list[str]:
    if not isinstance(entry, dict):
        return ["an entry must be a JSON object"]
    errors: list[str] = []

    def need(ok: bool, message: str) -> None:
        if not ok:
            errors.append(message)

    def closed(obj: Any, allowed: set[str], where: str) -> None:
        if isinstance(obj, dict):
            for key in obj:
                if key not in allowed:
                    errors.append(f"{where} has an unknown key {key!r}")

    agent_id = entry.get("id")
    closed(entry, TOP_KEYS, "entry")
    need(entry.get("format") == FORMAT, f"format must be {FORMAT}")
    need(isinstance(agent_id, str) and bool(_ID.match(agent_id)),
         "id must be lowercase letters, digits, - or _, starting with a letter")
    need(parse_version(entry.get("version")) is not None, "version must look like 1.2.3")
    need(parse_version(entry.get("needs_router")) is not None, "needs_router must look like 1.2.3")
    need(entry.get("kind") in KINDS, f"kind must be one of {sorted(KINDS)}")
    remote = entry.get("kind") == "remote"
    served = entry.get("kind") in ("remote", "mcp")
    if served:
        service = _object(entry.get("service"))
        need(isinstance(entry.get("service"), dict), "an agent that runs as its own service needs a service (its image and port)")
        closed(service, SERVICE_KEYS, "service")
        need(isinstance(service.get("image"), str) and bool(_IMAGE.match(service.get("image") or "")),
             "service.image must name a package like kaliper/agent-textql:2.0.0")
        port = service.get("port")
        need(isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535,
             "service.port must be a number from 1 to 65535")
        if "timeout_seconds" in service:
            seconds = service["timeout_seconds"]
            need(isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and 0 < seconds <= 600,
                 "service.timeout_seconds must be a number of seconds up to 600")
    else:
        need("service" not in entry, "only a remote agent has a service")
    need(_text(entry.get("publisher")), "publisher is required")

    display = _object(entry.get("display"))
    need(isinstance(entry.get("display"), dict), "display is required")
    closed(display, DISPLAY_KEYS, "display")
    need(_text(display.get("name")), "display.name is required")
    need(_text(display.get("summary")), "display.summary is required")
    need(_strings(display.get("categories")), "display.categories must be a list of words")
    need(_strings(display.get("examples"), allow_empty=True), "display.examples must be a list of questions")
    logo = _object(display.get("logo"))
    closed(logo, LOGO_KEYS, "display.logo")
    letter = logo.get("letter")
    need(_text(letter) and len(letter) <= 2, "display.logo.letter must be 1 or 2 characters")
    need(isinstance(logo.get("color"), str) and bool(_COLOR.match(logo["color"])),
         "display.logo.color must be a colour like #1a73e8")
    if logo.get("image") is not None:
        need(isinstance(logo["image"], str) and bool(_LOGO_IMAGE.match(logo["image"])),
             "display.logo.image must look like logos/name.svg")

    router = _object(entry.get("router"))
    need(isinstance(entry.get("router"), dict), "router is required")
    closed(router, ROUTER_KEYS, "router")
    source = router.get("source")
    need(_text(source), "router.source is required")
    agent = _object(router.get("agent"))
    closed(agent, AGENT_KEYS, "router.agent")
    for switch in AGENT_SWITCHES:
        if switch in agent:
            need(isinstance(agent[switch], bool), f"router.agent.{switch} must be true or false")
    if "extra_tools" in agent:
        need(_strings(agent["extra_tools"], allow_empty=True), "router.agent.extra_tools must be a list of tool names")
    need(agent.get("name") == agent_id, "router.agent.name must equal id")
    need(agent.get("source") == source, "router.agent.source must equal router.source")
    need(_text(agent.get("description")), "router.agent.description is required")
    need(_text(agent.get("owns")), "router.agent.owns is required")
    tools = agent.get("tools")
    need(_strings(tools), "router.agent.tools must list at least one tool")
    descriptions = _object(router.get("tool_descriptions"))
    seen_tools: set = set()
    for tool in tools if isinstance(tools, list) else []:
        need(isinstance(tool, str) and bool(_TOOL.match(tool)),
             f"{tool!r} is not a valid tool name (lowercase letters, digits and _)")
        need(tool not in seen_tools, f"tool {tool!r} is listed twice in router.agent.tools")
        if isinstance(tool, str):
            seen_tools.add(tool)
        need(_text(descriptions.get(tool)), f"tool {tool!r} has no description in router.tool_descriptions")
    for tool in descriptions:
        need(isinstance(tools, list) and tool in tools,
             f"router.tool_descriptions describes {tool!r}, which router.agent.tools does not list")
    config = _object(router.get("source_config"))
    need(isinstance(router.get("source_config"), dict), "router.source_config is required")
    closed(config, SOURCE_KEYS, "router.source_config")
    if "always_enabled" in config:
        need(isinstance(config["always_enabled"], bool), "router.source_config.always_enabled must be true or false")
    if "requires_credentials" in config:
        need(_strings(config["requires_credentials"]), "router.source_config.requires_credentials must be a list of setting names")
    if config.get("requires_credential") is not None:
        need(_text(config["requires_credential"]), "router.source_config.requires_credential must be a setting name")
    if served:
        # The client fills this in: it names the container, so it knows the address.
        need(config == {}, "an agent service's router.source_config must be empty: the client fills in its address")
        if remote:
            need(agent.get("passthrough") is True, "a remote agent must be passthrough: it answers whole questions")
        else:
            need(agent.get("passthrough") is not True, "an MCP agent is not passthrough: the router's model drives its tools")
    else:
        need(isinstance(config.get("tools_factory"), str) and bool(_FACTORY.match(config.get("tools_factory") or "")),
             "router.source_config.tools_factory must name code inside the router, like app.agents:jira_tools")
        answerer = config.get("answerer_factory")
        need(answerer is None or (isinstance(answerer, str) and bool(_FACTORY.match(answerer))),
             "router.source_config.answerer_factory must be null or name code inside the router")
        if agent.get("passthrough"):
            need(answerer is not None, "a passthrough agent needs router.source_config.answerer_factory")

    connection = _object(entry.get("connection"))
    closed(connection, CONNECTION_KEYS, "connection")
    method = connection.get("method")
    need(method in METHODS, f"connection.method must be one of {sorted(METHODS)}")
    if method == "signin":
        need(_text(connection.get("provider")), "a sign-in connection needs connection.provider")
    fields = connection.get("fields")
    need(isinstance(fields, list), "connection.fields must be a list")
    keys: set[str] = set()
    for field in fields if isinstance(fields, list) else []:
        if not isinstance(field, dict):
            errors.append("every connection field must be an object")
            continue
        key = field.get("key")
        closed(field, FIELD_KEYS, f"connection field {key!r}")
        need(key not in keys, f"connection field {key!r} is asked twice")
        if isinstance(key, str):
            keys.add(key)
        if field.get("type") == "select":
            need(_strings(field.get("options")), f"field {key!r} is a dropdown with no options")
        need(_text(key) and _text(field.get("label")), "every connection field needs a key and a label")
        need(field.get("type") in FIELD_TYPES, f"field {key!r} has an unknown type {field.get('type')!r}")
        need(isinstance(field.get("required"), bool), f"field {key!r} must say whether it is required")
        if field.get("pattern") is not None:
            try:
                re.compile(field["pattern"])
            except (re.error, TypeError):
                errors.append(f"field {key!r} has a format check that is not a valid pattern")
            else:
                need(not _PYTHON_ONLY_REGEX.search(field["pattern"]),
                     f"field {key!r} has a format check (pattern) the page cannot run in the browser")
    covers = connection.get("covers", [])
    need(_strings(covers, allow_empty=True), "connection.covers must be a list of setting names")
    covered = keys | {c for c in covers if isinstance(c, str)} if isinstance(covers, list) else keys
    missing = [c for c in required_credentials(entry) if c not in covered]
    need(not missing, f"the agent needs {', '.join(missing)} but its connection form does not ask for it")
    return errors
