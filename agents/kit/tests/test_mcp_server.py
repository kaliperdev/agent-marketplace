import json
import re
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from langchain_core.tools import tool

from agent_kit.mcp_server import build_handler
from agent_kit.rows import table


@tool(response_format="content_and_artifact")
def list_projects(prefix: str = "") -> tuple:
    """List the projects whose key starts with `prefix`."""
    return f"2 projects starting {prefix!r}", table(["key"], [["INS"], ["RAD"]], truncated=False)


@tool
def echo(text: str) -> str:
    """Say it back."""
    return text


@tool
def broken() -> str:
    """Always fails."""
    raise RuntimeError("Jira returned 401")


@pytest.fixture
def serve():
    servers = []

    def start(tools, ready_error=None):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(tools, ready_error))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    yield start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def rpc(base, method, params=None, id=1, headers=None):
    body = {"jsonrpc": "2.0", "method": method}
    if id is not None:
        body["id"] = id
    if params is not None:
        body["params"] = params
    request = urllib.request.Request(base + "/mcp", data=json.dumps(body).encode(), method="POST", headers={
        "content-type": "application/json", "accept": "application/json, text/event-stream", **(headers or {})})
    with urllib.request.urlopen(request, timeout=5) as response:
        raw = response.read()
        return response.status, response.headers.get("mcp-session-id"), json.loads(raw) if raw else None


def test_initialize_answers_the_handshake_with_a_session(serve):
    status, session, reply = rpc(serve([echo]), "initialize",
                                 {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}})
    assert status == 200 and session
    assert reply["id"] == 1
    assert reply["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in reply["result"]["capabilities"]


def test_a_notification_gets_no_reply(serve):
    status, _, reply = rpc(serve([echo]), "notifications/initialized", {}, id=None)
    assert (status, reply) == (202, None)


def test_tools_are_listed_with_their_inputs(serve):
    _, _, reply = rpc(serve([list_projects, echo]), "tools/list", {})
    listed = {t["name"]: t for t in reply["result"]["tools"]}
    assert set(listed) == {"list_projects", "echo"}
    assert "starts with" in listed["list_projects"]["description"]
    assert listed["echo"]["inputSchema"]["properties"]["text"]["type"] == "string"
    assert listed["echo"]["inputSchema"]["required"] == ["text"]


def test_a_tools_rows_come_back_as_structured_content(serve):
    _, _, reply = rpc(serve([list_projects]), "tools/call", {"name": "list_projects", "arguments": {"prefix": "I"}})
    result = reply["result"]
    assert result["isError"] is False
    assert result["content"] == [{"type": "text", "text": "2 projects starting 'I'"}]
    assert result["structuredContent"] == {"table": {"columns": ["key"], "rows": [["INS"], ["RAD"]], "truncated": False}}


def test_a_tool_without_rows_sends_text_only(serve):
    _, _, reply = rpc(serve([echo]), "tools/call", {"name": "echo", "arguments": {"text": "hi"}})
    assert reply["result"]["content"][0]["text"] == "hi"
    assert "structuredContent" not in reply["result"]


def test_a_tool_that_fails_is_a_tool_error_not_a_crash(serve):
    _, _, reply = rpc(serve([broken]), "tools/call", {"name": "broken", "arguments": {}})
    assert reply["result"]["isError"] is True
    assert "Jira returned 401" in reply["result"]["content"][0]["text"]


def test_bad_arguments_are_a_tool_error(serve):
    _, _, reply = rpc(serve([echo]), "tools/call", {"name": "echo", "arguments": {}})
    assert reply["result"]["isError"] is True


def test_unknown_tools_and_methods_are_protocol_errors(serve):
    base = serve([echo])
    assert rpc(base, "tools/call", {"name": "nope", "arguments": {}})[2]["error"]["code"] == -32602
    assert rpc(base, "resources/list", {})[2]["error"]["code"] == -32601


def test_health_says_whether_the_agent_can_work(serve):
    with urllib.request.urlopen(serve([echo]) + "/health", timeout=5) as response:
        assert json.loads(response.read()) == {"ok": True}
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(serve([], "JIRA_API_TOKEN is not set") + "/health", timeout=5)
    assert refused.value.code == 503
    assert "JIRA_API_TOKEN" in json.loads(refused.value.read())["error"]


def test_a_get_on_the_mcp_path_is_not_allowed(serve):
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(serve([echo]) + "/mcp", timeout=5)
    assert refused.value.code == 405



IDS = {"x-request-id": "slack-1a2b3c4d5e6f", "x-run-id": "2026-10-02T15:44:25-3f9a1c"}


def lines(capsys):
    captured = capsys.readouterr()
    return captured.out.splitlines() + captured.err.splitlines()


def test_a_good_call_logs_one_info_line_with_the_ids_and_no_arguments(serve, capsys):
    rpc(serve([echo]), "tools/call", {"name": "echo", "arguments": {"text": "my secret question"}}, headers=IDS)
    (line,) = [l for l in lines(capsys) if "[mcp] call" in l]
    assert re.match(r"^\d{4}-\d\d-\d\dT[\d:.]+Z INFO \[mcp\] call tool=echo request=slack-1a2b3c4d5e6f "
                    r"run=2026-10-02T15:44:25-3f9a1c ms=\d+ ok=true$", line)
    assert "secret question" not in line


def test_a_failed_call_logs_the_upstream_message_redacted(serve, capsys):
    @tool
    def leaky() -> str:
        """Fails with a token in the message."""
        raise RuntimeError("Jira returned 401: bad token=abc123 for Bearer xyz789")

    rpc(serve([leaky]), "tools/call", {"name": "leaky", "arguments": {}}, headers=IDS)
    (line,) = [l for l in lines(capsys) if "[mcp] call" in l]
    assert " WARN [mcp] call tool=leaky request=slack-1a2b3c4d5e6f run=2026-10-02T15:44:25-3f9a1c " in line
    assert "ok=false" in line and "RuntimeError: Jira returned 401" in line
    assert "abc123" not in line and "xyz789" not in line


def test_missing_ids_are_logged_as_a_dash(serve, capsys):
    rpc(serve([echo]), "tools/call", {"name": "echo", "arguments": {"text": "hi"}})
    (line,) = [l for l in lines(capsys) if "[mcp] call" in l]
    assert "request=- run=- " in line


def test_protocol_errors_are_logged_as_warnings(serve, capsys):
    base = serve([echo])
    rpc(base, "tools/call", {"name": "nope", "arguments": {}}, headers=IDS)
    rpc(base, "resources/list", {}, headers=IDS)
    request = urllib.request.Request(base + "/mcp", data=b"{not json", method="POST")
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(request, timeout=5)
    out = lines(capsys)
    assert any("WARN [mcp] unknown tool" in l and "tool=nope" in l and "request=slack-1a2b3c4d5e6f" in l for l in out)
    assert any("WARN [mcp] unsupported method" in l and "method=resources/list" in l for l in out)
    assert any("WARN [mcp] bad request" in l and "not JSON" in l for l in out)


def test_a_failed_calls_reply_is_redacted_like_its_log_line(serve):
    # The reply is what the router hands its model, and the model can quote it.
    @tool
    def leaky() -> str:
        """Fails with a token in the message."""
        raise RuntimeError("Jira returned 401: bad token=abc123 for Bearer xyz789")

    _, _, reply = rpc(serve([leaky]), "tools/call", {"name": "leaky", "arguments": {}})
    text = reply["result"]["content"][0]["text"]
    assert reply["result"]["isError"] is True and "Jira returned 401" in text
    assert "abc123" not in text and "xyz789" not in text


@pytest.mark.parametrize("params", [[1], {"name": {"x": 1}}, {"name": ["echo"]}],
                         ids=["params-a-list", "name-an-object", "name-a-list"])
def test_a_malformed_call_is_a_protocol_error_not_a_dropped_connection(serve, params):
    status, _, reply = rpc(serve([echo]), "tools/call", params)
    assert status == 200 and reply["error"]["code"] == -32602


@pytest.mark.parametrize("length", ["2000000", "-1", "lots"], ids=["too-big", "negative", "not-a-number"])
def test_a_bad_content_length_is_refused_without_reading(serve, length):
    # Claimed, not sent: a server that read it would wait for bytes that never come.
    import socket

    host, port = serve([echo]).removeprefix("http://").split(":")
    with socket.create_connection((host, int(port)), timeout=5) as sock:
        sock.sendall(f"POST /mcp HTTP/1.1\r\nHost: x\r\nContent-Length: {length}\r\n\r\n".encode())
        status = sock.recv(64).split(b" ")[1]
    assert status in (b"400", b"413")
