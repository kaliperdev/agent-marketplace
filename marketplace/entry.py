"""The catalog entry: one version of one agent, as one JSON object.

The same shape is imported, stored, exported and served, so there is one format
to keep straight. FORMAT names it: a later shape gets FORMAT = 2 and a converter,
rather than silently changing what the rows already stored mean.

validate() returns every problem it finds, not just the first, each worded for
the person who has to fix the file.
"""

from __future__ import annotations

import base64
import binascii
import re
import urllib.parse
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
# Or the logo itself, so a new agent needs no file in the page's image. Drawn as
# <img src>, where an SVG cannot run scripts.
_LOGO_DATA = re.compile(r"^data:image/(svg\+xml|png);base64,([A-Za-z0-9+/]+={0,2})$")
MAX_LOGO_CHARS = 64 * 1024
# A key file field's accepted files: extensions or media types, comma-separated.
_ACCEPT = re.compile(r"^(\.[a-z0-9]+|[a-z]+/[a-z0-9.+-]+)(,(\.[a-z0-9]+|[a-z]+/[a-z0-9.+-]+))*$")
# The router imports whatever the catalog names here (importlib, in
# router/app/agents.py), so only code inside the router package is allowed.
_FACTORY = re.compile(r"^app(\.[a-z_]+)+:[a-z_]+$")

# Format 1 is a closed shape: anything else is refused, so nothing per-client
# (a `sample`, saved `values`, a login) can ride along into the global catalog.
# Each list is also the order entries are written out in: the database (jsonb)
# re-sorts keys, so without this every export would come out scrambled.
TOP_ORDER = ["format", "id", "version", "kind", "service", "needs_router", "publisher", "display", "connection", "router"]
SERVICE_ORDER = ["image", "url", "port", "timeout_seconds", "memory_mb", "start_seconds", "max_result_chars"]
DISPLAY_ORDER = ["name", "summary", "categories", "logo", "examples"]
LOGO_ORDER = ["letter", "color", "image"]
CONNECTION_ORDER = ["method", "provider", "covers", "note", "send", "signin", "fields"]
SIGNIN_ORDER = ["client", "client_id_field", "client_secret_field", "authorization_url", "token_url", "scopes",
                "resource", "authorize_params"]
FIELD_ORDER = ["key", "label", "type", "required", "placeholder", "default", "options", "pattern", "patternHelp", "accept",
               "help"]
ROUTER_ORDER = ["source", "source_config", "agent", "tool_descriptions"]
# The router's own order in router/config/agents.json.
AGENT_ORDER = ["name", "source", "tools", "description", "owns", "passthrough", "verbatim", "enabled", "extra_tools",
               "time_limit_seconds", "holds_documents", "first_for"]
SOURCE_ORDER = ["tools_factory", "answerer_factory", "requires_credential", "requires_credentials", "always_enabled"]
TOP_KEYS, DISPLAY_KEYS, LOGO_KEYS = set(TOP_ORDER), set(DISPLAY_ORDER), set(LOGO_ORDER)
CONNECTION_KEYS, FIELD_KEYS, ROUTER_KEYS = set(CONNECTION_ORDER), set(FIELD_ORDER), set(ROUTER_ORDER)
SIGNIN_KEYS = set(SIGNIN_ORDER)
# What a sign-in's own steps set (router/app/oauth.py): an entry's
# authorize_params may add to them, never replace them.
SIGN_IN_PARAMS = {"response_type", "client_id", "redirect_uri", "state", "code_challenge",
                  "code_challenge_method", "resource", "scope"}
AGENT_KEYS, SOURCE_KEYS, SERVICE_KEYS = set(AGENT_ORDER), set(SOURCE_ORDER), set(SERVICE_ORDER)

_TOOL = re.compile(r"^[a-z][a-z0-9_]*$")
# A vendor's own tool names: what MCP 2025-11-25 asks servers to use, plus the
# "/" and ":" some still do.
_VENDOR_TOOL = re.compile(r"^[A-Za-z0-9_.:/-]{1,128}$")
# Every tool the router's model sees is also given the clock (router/app/tools/clock.py).
RESERVED_TOOLS = {"current_time"}
# How a vendor's server is sent the key typed into the form.
_HEADER = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")
RESERVED_HEADERS = {"host", "content-type", "content-length", "accept", "cookie", "mcp-protocol-version",
                    "mcp-session-id", "mcp-method", "mcp-name", "x-request-id", "x-run-id"}
# The page re-checks fields in the browser with JavaScript's RegExp, which has
# no Python named groups, comments or \A / \Z anchors.
_PYTHON_ONLY_REGEX = re.compile(r"\(\?P[<=]|\(\?#|\\[AZ]")
# The router tests these with truthiness, so "false" (a string) would mean ON.
AGENT_SWITCHES = ("passthrough", "verbatim", "enabled", "holds_documents")


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
        if isinstance(out["connection"].get("signin"), dict):
            out["connection"]["signin"] = _in_order(out["connection"]["signin"], SIGNIN_ORDER)
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


def safe_tool_name(name: str) -> str:
    """The name the router's model is given for a vendor's tool: what both
    Anthropic (^[a-zA-Z0-9_-]{1,128}$) and OpenAI (^[a-zA-Z0-9_-]{1,64}$)
    accept. The router makes the same name (router/app/tools/mcp_tools.py) and
    calls the vendor by its own."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)[:64]


def _vendor_url(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 512:
        return False
    parts = urllib.parse.urlsplit(value)
    host = parts.hostname or ""
    return (parts.scheme == "https" and "@" not in parts.netloc and not parts.fragment
            and "." in host and not host.replace(".", "").isdigit())


def _whole(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _logo_image(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if _LOGO_IMAGE.match(value):
        return True
    data = _LOGO_DATA.match(value)
    if not data or len(value) > MAX_LOGO_CHARS:
        return False
    try:
        base64.b64decode(data.group(2), validate=True)
    except (binascii.Error, ValueError):
        return False
    return True


def _check_signin(entry: dict, connection: dict, signin: Any, fields: Any, hosted: bool, need, closed,
                  errors: list[str]) -> None:
    """connection.signin: how "Sign in with…" works for this agent (router 0.6.0)."""
    if not isinstance(signin, dict) or connection.get("method") != "signin":
        errors.append("connection.signin is an object, for connection.method \"signin\" only")
        return
    closed(signin, SIGNIN_KEYS, "connection.signin")
    # The router hands a sign-in's token only to an MCP agent's tool calls: any
    # other kind would sign in and never be given its token.
    need(entry.get("kind") == "mcp", "only an MCP agent (kind mcp) can sign in: no other kind is handed the token")
    by_key = {f.get("key"): f for f in fields if isinstance(f, dict)} if isinstance(fields, list) else {}
    client = signin.get("client")
    need(client in ("automatic", "own-app"), 'connection.signin.client must be "automatic" or "own-app"')
    urls = [key for key in ("authorization_url", "token_url") if key in signin]
    need(len(urls) in (0, 2), "connection.signin names both authorization_url and token_url, or neither")
    for key in urls:
        need(_vendor_url(signin[key]), f"connection.signin.{key} must be an https address")
    need(hosted or len(urls) == 2,
         "an agent that signs in without a vendor's MCP server to ask needs authorization_url and token_url")
    if client == "automatic":
        need(hosted and not urls, "automatic sign-in registers with a vendor's MCP server: it needs service.url")
    if client == "own-app":
        id_field = by_key.get(signin.get("client_id_field"))
        need(bool(id_field) and id_field.get("type") == "text",
             "connection.signin.client_id_field must name a text field of this form (the app's client ID)")
        if "client_secret_field" in signin:
            secret_field = by_key.get(signin["client_secret_field"])
            need(bool(secret_field) and secret_field.get("type") == "password",
                 "connection.signin.client_secret_field must name a password field of this form")
    if "scopes" in signin:
        need(_strings(signin["scopes"]) and len(signin["scopes"]) <= 30,
             "connection.signin.scopes must be a list of scope names")
    if "resource" in signin:
        need(isinstance(signin["resource"], bool), "connection.signin.resource must be true or false")
    if "authorize_params" in signin:
        params = signin["authorize_params"]
        need(isinstance(params, dict) and len(params) <= 8
             and all(isinstance(k, str) and isinstance(v, str) and k not in SIGN_IN_PARAMS for k, v in params.items()),
             "connection.signin.authorize_params: up to 8 text values, none of them one the sign-in sets itself")
    need(not connection.get("covers"), "a sign-in's tokens are not settings: connection.covers must be empty")
    need((parse_version(entry.get("needs_router")) or (0, 0, 0)) >= (0, 6, 0),
         "an agent with connection.signin needs router 0.6.0 or later (needs_router)")


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
    # Run by the vendor at its own address, not on the client's server.
    hosted = served and isinstance(entry.get("service"), dict) and "url" in entry["service"]
    if served:
        service = _object(entry.get("service"))
        need(isinstance(entry.get("service"), dict), "an agent that runs as its own service needs a service (its image and port)")
        closed(service, SERVICE_KEYS, "service")
        if hosted:
            need(entry.get("kind") == "mcp", "service.url is for an MCP server the vendor runs (kind mcp)")
            need("image" not in service and "port" not in service,
                 "a service has an image and a port (it runs on the client's server) or a url "
                 "(the vendor runs it), not both")
            need(_vendor_url(service.get("url")),
                 "service.url must be the vendor's https address, like https://mcp.linear.app/mcp")
            for key in ("memory_mb", "start_seconds"):
                need(key not in service, f"service.{key} is for a container; the vendor runs this one")
            if "max_result_chars" in service:
                need(_whole(service["max_result_chars"], 1000, 200_000),
                     "service.max_result_chars must be a whole number from 1000 to 200000")
            need((parse_version(entry.get("needs_router")) or (0, 0, 0)) >= (0, 5, 0),
                 "an MCP server the vendor runs needs router 0.5.0 or later (needs_router)")
        else:
            need(isinstance(service.get("image"), str) and bool(_IMAGE.match(service.get("image") or "")),
                 "service.image must name a package like kaliper/agent-textql:2.0.0")
            port = service.get("port")
            need(isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535,
                 "service.port must be a number from 1 to 65535")
            need("max_result_chars" not in service, "service.max_result_chars is for an MCP server the vendor runs")
        if "timeout_seconds" in service:
            seconds = service["timeout_seconds"]
            need(isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and 0 < seconds <= 600,
                 "service.timeout_seconds must be a number of seconds up to 600")
        if "memory_mb" in service:
            need(_whole(service["memory_mb"], 64, 2048), "service.memory_mb must be a whole number from 64 to 2048")
        if "start_seconds" in service:
            need(_whole(service["start_seconds"], 5, 300), "service.start_seconds must be a whole number from 5 to 300")
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
        need(_logo_image(logo["image"]),
             "display.logo.image must look like logos/name.svg, or be a data:image/svg+xml or "
             "data:image/png base64 image of at most 64 KB")

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
    if "time_limit_seconds" in agent:
        # The bot gives up on a question at 600 s: one agent may take up to 300,
        # leaving time to plan, answer, and perhaps look once more.
        need(_whole(agent["time_limit_seconds"], 30, 300),
             "router.agent.time_limit_seconds must be a whole number from 30 to 300")
    if "first_for" in agent:
        need(_text(agent["first_for"]) and len(agent["first_for"]) <= 200,
             "router.agent.first_for must be text of at most 200 characters, e.g. \"clients, projects and decisions\"")
    need(agent.get("name") == agent_id, "router.agent.name must equal id")
    need(agent.get("source") == source, "router.agent.source must equal router.source")
    need(_text(agent.get("description")), "router.agent.description is required")
    need(_text(agent.get("owns")), "router.agent.owns is required")
    tools = agent.get("tools")
    need(_strings(tools), "router.agent.tools must list at least one tool")
    descriptions = _object(router.get("tool_descriptions"))
    seen_tools: set = set()
    safe_names: dict[str, str] = {}
    for tool in tools if isinstance(tools, list) else []:
        if hosted:
            # The vendor's own names; the router hands the model safe_tool_name(name).
            valid = isinstance(tool, str) and bool(_VENDOR_TOOL.match(tool))
            need(valid, f"{tool!r} is not a valid tool name (letters, digits, _ - . : and /, up to 128)")
            if valid:
                safe = safe_tool_name(tool)
                need(safe not in RESERVED_TOOLS, f"tool {tool!r} would be called {safe!r}, which current_time "
                     "(the clock every agent has) already is")
                need(safe not in safe_names or safe_names[safe] == tool,
                     f"tools {safe_names.get(safe)!r} and {tool!r} would both be called {safe!r}: the same name")
                safe_names.setdefault(safe, tool)
        else:
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
    signin = connection.get("signin")
    if method == "signin":
        need(_text(connection.get("provider")), "a sign-in connection needs connection.provider")
        need(not hosted or isinstance(signin, dict),
             "signing in to a vendor's own server needs connection.signin (router 0.6.0)")
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
        if "accept" in field:
            need(field.get("type") == "file" and isinstance(field["accept"], str) and bool(_ACCEPT.match(field["accept"])),
                 f"field {key!r}: accept is for key files only, and lists extensions or types like .json,.pem")
        need(isinstance(field.get("required"), bool), f"field {key!r} must say whether it is required")
        if field.get("pattern") is not None:
            try:
                re.compile(field["pattern"])
            except (re.error, TypeError):
                errors.append(f"field {key!r} has a format check that is not a valid pattern")
            else:
                need(not _PYTHON_ONLY_REGEX.search(field["pattern"]),
                     f"field {key!r} has a format check (pattern) the page cannot run in the browser")
    if signin is not None:
        _check_signin(entry, connection, signin, fields, hosted, need, closed, errors)
    send = connection.get("send")
    if send is not None and method == "signin":
        errors.append("connection.send is for a key typed into the form, not a sign-in")
        send = None
    if hosted and method == "form":
        need(isinstance(send, dict), "a vendor's server needs connection.send: how its key is sent, "
             "e.g. {\"bearer\": \"LINEAR_API_KEY\"}")
    if send is not None:
        types = {f.get("key"): f.get("type") for f in fields if isinstance(f, dict) and isinstance(f.get("key"), str)} \
            if isinstance(fields, list) else {}
        if not hosted:
            errors.append("connection.send is for an MCP server the vendor runs (service.url)")
        elif not isinstance(send, dict) or set(send) not in ({"bearer"}, {"header", "field"}, {"basic"}):
            errors.append('connection.send must be {"bearer": "<FIELD>"}, {"header": "<Name>", "field": "<FIELD>"} '
                          'or {"basic": ["<USER FIELD>", "<SECRET FIELD>"]}')
        elif "basic" in send:
            pair = send["basic"]
            need(isinstance(pair, list) and len(pair) == 2 and all(isinstance(key, str) for key in pair)
                 and types.get(pair[0]) in ("text", "email") and types.get(pair[1]) == "password",
                 "connection.send.basic names a text or email field (the user) and a password field (the secret)")
        else:
            field = send.get("bearer", send.get("field"))
            need(isinstance(field, str) and types.get(field) == "password",
                 f"connection.send names {field!r}, which must be a password field of this form")
            if "header" in send:
                header = send["header"]
                need(isinstance(header, str) and bool(_HEADER.match(header)) and header.lower() not in RESERVED_HEADERS,
                     f"connection.send: {header!r} is not a header the key can be sent in")
    covers = connection.get("covers", [])
    need(_strings(covers, allow_empty=True), "connection.covers must be a list of setting names")
    covered = keys | {c for c in covers if isinstance(c, str)} if isinstance(covers, list) else keys
    missing = [c for c in required_credentials(entry) if c not in covered]
    need(not missing, f"the agent needs {', '.join(missing)} but its connection form does not ask for it")
    return errors
