"""A tool reads the access note sent with its own call; /check answers a call that carries one."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from langchain_core.tools import tool

from agent_kit import auth
from agent_kit.mcp_server import build_handler
from tests.test_mcp_server import rpc, serve  # noqa: F401 - the fixture


@tool
def whose() -> str:
    """Says which note and pass this call carries."""
    return f"{auth.call_note()} / {auth.access_token()}"


@pytest.fixture
def checker():
    servers = []

    def start(check=None, access_check=None):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), build_handler([], None, "acme", check=check, access_check=access_check))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    yield start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def post(base, body, headers=None):
    request = urllib.request.Request(base + "/check", data=json.dumps(body).encode(), method="POST",
                                     headers={"content-type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_a_tool_reads_the_note_sent_with_its_own_call(serve):  # noqa: F811
    base = serve([whose])
    _, _, reply = rpc(base, "tools/call", {"name": "whose", "arguments": {}},
                      headers={"authorization": "Bearer at-1", "x-agent-access": "note-1"})
    assert reply["result"]["content"][0]["text"] == "note-1 / at-1"
    _, _, reply = rpc(base, "tools/call", {"name": "whose", "arguments": {}}, headers={"authorization": "Bearer at-2"})
    assert reply["result"]["content"][0]["text"] == "None / at-2"  # nothing is kept between calls


def test_check_with_a_note_runs_the_access_check_inside_the_call(checker, capsys):
    base = checker(access_check=lambda: {"state": "ok", "detail": f"read with {auth.access_token()} under {auth.call_note()}"})
    status, answer = post(base, {}, {"authorization": "Bearer pass-123", "x-agent-access": "note-456"})
    assert (status, answer) == (200, {"state": "ok", "detail": "read with [redacted] under [redacted]"})
    out = capsys.readouterr()
    assert "pass-123" not in out.out + out.err and "note-456" not in out.out + out.err
    assert "state=ok" in out.out and "values=note" in out.out


def test_an_agent_that_checks_only_data_access_has_no_check_without_a_note(checker):
    assert post(checker(access_check=lambda: {"state": "ok", "detail": ""}), {}, {})[0] == 404


def test_a_note_to_an_agent_without_an_access_check_is_not_found_and_its_login_check_stands(checker):
    base = checker(check=lambda values: {"state": "ok", "detail": "fine"})
    assert post(base, {}, {"x-agent-access": "note-1"})[0] == 404
    assert post(base, {}, {}) == (200, {"state": "ok", "detail": "fine"})


def test_an_access_check_that_breaks_is_unreachable_never_refused(checker):
    def broken():
        raise RuntimeError("boom")
    status, answer = post(checker(access_check=broken), {}, {"x-agent-access": "note-1"})
    assert status == 200 and answer["state"] == "unreachable"
