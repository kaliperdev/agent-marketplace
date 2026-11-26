"""Serve an agent's LangChain tools as an MCP server.

The router's model still drives the tools; this only moves them out of the
router. The wire is MCP's streamable HTTP with plain JSON replies:

  POST /mcp   initialize, notifications/initialized, tools/list, tools/call
  GET  /health  200 {"ok": true} when the agent can work
  POST /check   {} or {"values": {...}}: does the vendor take the login? (agent_kit/login.py)
                With an x-agent-access header: does the access the note and
                bearer token describe work? (the agent's access_check)

A tool that returns rows (response_format="content_and_artifact") sends its
text as `content` and its rows as `structuredContent: {"table": {...}}`, so the
router can count them and draw pages exactly as when the tool ran inside it.

Stateless: a session id is handed out on initialize, as the protocol expects,
but nothing is kept per session.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent_kit import auth, login
from agent_kit.log import log, redact

PROTOCOL_VERSION = "2025-06-18"
# A tool call is a name and a few arguments; anything larger is refused unread.
MAX_BODY_BYTES = 1_000_000


def describe(tool) -> dict:
    schema = dict(tool.tool_call_schema.model_json_schema())
    schema.pop("title", None)
    schema.pop("description", None)
    return {"name": tool.name, "description": tool.description, "inputSchema": schema}


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def call(tool, arguments: dict, request: str | None = None, run: str | None = None) -> dict:
    """One tool call as an MCP result. A failing tool is a tool error the
    model can read, never a protocol error.

    One log line per call, with the router's request and run ids. Not
    detectable here: a tool that returns its error as text instead of raising
    logs as ok=true."""
    started = time.perf_counter()
    try:
        message = tool.invoke({"type": "tool_call", "name": tool.name, "args": arguments, "id": "mcp"})
    except Exception as error:  # noqa: BLE001 - reported to the caller as text
        log("WARN", "mcp", "call", tool=tool.name, request=request, run=run, ms=_ms(started), ok=False,
            error=f"{type(error).__name__}: {redact(error)}")
        return {"content": [{"type": "text", "text": f"{type(error).__name__}: {redact(error)}"}], "isError": True}
    content = message.content if isinstance(message.content, str) else json.dumps(message.content)
    result: dict = {"content": [{"type": "text", "text": content}], "isError": False}
    artifact = getattr(message, "artifact", None)
    if isinstance(artifact, dict) and isinstance(artifact.get("columns"), list):
        result["structuredContent"] = {"table": artifact}
    # The tool's name and time only: arguments can carry what was asked.
    log("INFO", "mcp", "call", tool=tool.name, request=request, run=run, ms=_ms(started), ok=True)
    return result


def build_handler(tools: Sequence, ready_error: str | None = None, name: str = "agent",
                  check: Callable[[Mapping[str, str]], dict] | None = None,
                  access_check: Callable[[], dict] | None = None) -> type[BaseHTTPRequestHandler]:
    """`ready_error` set means the agent cannot work (a missing login): /health
    says so. Callers pass no tools in that case; tools/list lists what it is given.
    `check` asks the vendor whether it takes a login (POST /check); without one
    there is no such route, as in every image before it existed.
    `access_check` answers a /check that carries an access note, run inside the
    call so it reads the note and token as a tool would; without one, a /check
    with a note is 404."""
    by_name = {tool.name: tool for tool in tools}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            pass

        def _json(self, code: int, body: dict | None, headers: dict | None = None) -> None:
            data = b"" if body is None else json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            if body is not None:
                self.send_header("content-type", "application/json")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/mcp":
                # This server sends no stream of its own; the protocol says 405.
                self._json(405, {"error": "this server does not open a stream"})
            elif self.path == "/health":
                if ready_error:
                    self._json(503, {"ok": False, "error": ready_error})
                else:
                    self._json(200, {"ok": True})
            else:
                self._json(404, {"error": f"no route {self.path}"})

        def _check(self) -> None:
            try:
                length = int(self.headers.get("content-length") or 0)
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_BODY_BYTES:
                self._json(413 if length > MAX_BODY_BYTES else 400, {"error": "bad content-length"})
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                body = None
            if not isinstance(body, dict):
                self._json(400, {"error": "send a JSON object"})
                return
            note = self.headers.get(auth.NOTE_HEADER)
            if note:
                # Neither the pass nor the note can reach the detail.
                with auth.serving(self.headers.get("authorization"), note):
                    try:
                        token = auth.access_token()
                    except auth.NotSignedIn:
                        token = ""
                    answer = login.run(lambda _values: access_check(), {"pass": token, "note": note})
                log("INFO", "login", "check", agent=name, state=answer["state"], values="note")
                self._json(200, answer)
                return
            typed = body.get("values")
            if typed is None:
                values, source = dict(os.environ), "saved"
            elif isinstance(typed, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in typed.items()):
                values, source = typed, "typed"
            else:
                self._json(400, {"error": "values must be an object of text"})
                return
            answer = login.run(check, values)
            # Which agent, what it found and where the values came from: never a value.
            log("INFO", "login", "check", agent=name, state=answer["state"], values=source)
            self._json(200, answer)

        def do_POST(self) -> None:  # noqa: N802
            ids = {"request": self.headers.get("x-request-id") or None, "run": self.headers.get("x-run-id") or None}
            # A note asks the access check; no note asks the login check.
            wanted = access_check if self.headers.get(auth.NOTE_HEADER) else check
            if self.path == "/check" and wanted is not None:
                self._check()
                return
            if self.path != "/mcp":
                self._json(404, {"error": f"no route {self.path}"})
                return
            try:
                length = int(self.headers.get("content-length") or 0)
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_BODY_BYTES:
                # Refused unread: a negative length reads until the caller hangs up.
                log("WARN", "mcp", "bad request", reason="content-length", **ids)
                self._json(413 if length > MAX_BODY_BYTES else 400,
                           {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "bad content-length"}})
                return
            try:
                message = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                log("WARN", "mcp", "bad request", reason="not JSON", **ids)
                self._json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "not JSON"}})
                return
            if not isinstance(message, dict):
                log("WARN", "mcp", "bad request", reason="not a request", **ids)
                self._json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "not a request"}})
                return
            method, params, request_id = message.get("method"), message.get("params") or {}, message.get("id")
            if request_id is None:  # a notification: nothing to answer
                self._json(202, None)
                return
            if not isinstance(params, dict) or not isinstance(params.get("name", ""), str):
                log("WARN", "mcp", "bad request", reason="params must be an object with a text name", **ids)
                self._json(200, {"jsonrpc": "2.0", "id": request_id,
                                 "error": {"code": -32602, "message": "params must be an object with a text name"}})
                return

            def reply(result: dict, headers: dict | None = None) -> None:
                self._json(200, {"jsonrpc": "2.0", "id": request_id, "result": result}, headers)

            def error(code: int, text: str) -> None:
                self._json(200, {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": text}})

            if method == "initialize":
                reply({
                    "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": name, "version": "1.0"},
                }, {"mcp-session-id": uuid.uuid4().hex})
            elif method == "tools/list":
                reply({"tools": [describe(tool) for tool in by_name.values()]})
            elif method == "tools/call":
                tool = by_name.get(params.get("name"))
                if tool is None:
                    log("WARN", "mcp", "unknown tool", tool=redact(params.get("name"), 80), **ids)
                    error(-32602, f"unknown tool {params.get('name')!r}")
                    return
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    log("WARN", "mcp", "bad request", reason="arguments must be an object", tool=tool.name, **ids)
                    error(-32602, "arguments must be an object")
                    return
                # An agent that signs in reads this call's token (agent_kit.auth).
                with auth.serving(self.headers.get("authorization"), self.headers.get(auth.NOTE_HEADER)):
                    reply(call(tool, arguments, **ids))
            elif method == "ping":
                reply({})
            else:
                log("WARN", "mcp", "unsupported method", method=redact(method, 80), **ids)
                error(-32601, f"method {method!r} is not supported")

    return Handler


def serve(tools: Sequence, port: int, ready_error: str | None = None, name: str = "agent",
          check: Callable[[Mapping[str, str]], dict] | None = None,
          access_check: Callable[[], dict] | None = None) -> None:
    state = "ready" if not ready_error else ready_error
    log("INFO" if not ready_error else "WARN", "mcp", "listening", agent=name, port=port, state=state)
    ThreadingHTTPServer(("0.0.0.0", port), build_handler(tools, ready_error, name, check, access_check)).serve_forever()
