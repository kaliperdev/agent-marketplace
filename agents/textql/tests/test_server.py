import json
import re
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from textql_agent.reader import TextQLAnswer, TextQLError
from textql_agent.server import build_handler


@pytest.fixture
def serve():
    servers = []

    def start(ask):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(ask))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    yield start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def call(url, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method="GET" if body is None else "POST",
                                     headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_ask_answers_in_the_shape_the_router_reads(serve):
    chart = {"title": "T", "option": {"series": []}}
    base = serve(lambda q: TextQLAnswer(text=f"answer to {q}", charts=(chart,)))
    assert call(base + "/ask", {"question": "rides?"}) == (200, {
        "answer": "answer to rides?", "costUsd": 0.0, "tokensIn": 0, "tokensOut": 0, "charts": [chart],
    })


def test_a_textql_failure_is_a_502_with_its_message(serve):
    def ask(q):
        raise TextQLError("TextQL returned 401: bad key")

    code, body = call(serve(ask) + "/ask", {"question": "q"})
    assert code == 502 and "401" in body["error"]


def test_health_says_whether_the_key_is_set(serve):
    assert call(serve(lambda q: None) + "/health") == (200, {"ok": True})
    code, body = call(serve(None) + "/health")
    assert code == 503 and "TEXTQL_API_KEY" in body["error"]


def test_a_question_that_is_not_text_is_a_400(serve):
    assert call(serve(lambda q: None) + "/ask", {"question": 3})[0] == 400


IDS = {"x-request-id": "slack-1a2b3c4d5e6f", "x-run-id": "2026-10-02T15:44:25-3f9a1c"}


def ask_with_ids(url, question="how many?"):
    request = urllib.request.Request(url + "/ask", data=json.dumps({"question": question}).encode(), method="POST",
                                     headers={"content-type": "application/json", **IDS})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as err:
        return err.code


def log_lines(capsys):
    captured = capsys.readouterr()
    return (captured.out + captured.err).splitlines()


def test_a_good_ask_logs_one_info_line_with_ids_and_chart_count(serve, capsys):
    answer = TextQLAnswer(text="42", charts=({"title": "t", "option": {}},))
    assert ask_with_ids(serve(lambda q: answer)) == 200
    (line,) = [l for l in log_lines(capsys) if "[textql] ask" in l]
    assert re.match(r"^\d{4}-\d\d-\d\dT[\d:.]+Z INFO \[textql\] ask request=slack-1a2b3c4d5e6f "
                    r"run=2026-10-02T15:44:25-3f9a1c ms=\d+ charts=1$", line)
    assert "how many" not in line


def test_a_textql_failure_logs_a_warning_with_the_redacted_reason(serve, capsys):
    def ask(question):
        raise TextQLError("TextQL returned 401: bad token=abc123")

    assert ask_with_ids(serve(ask)) == 502
    (line,) = [l for l in log_lines(capsys) if "[textql] ask failed" in l]
    assert " WARN [textql] ask failed request=slack-1a2b3c4d5e6f run=2026-10-02T15:44:25-3f9a1c ms=" in line
    assert "TextQL returned 401" in line and "abc123" not in line


def test_an_unexpected_failure_logs_an_error_and_missing_ids_are_dashes(serve, capsys):
    def ask(question):
        raise ValueError("boom sk-abcdefghijklmnop")

    request = urllib.request.Request(serve(ask) + "/ask", data=b'{"question": "q"}', method="POST")
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(request, timeout=5)
    (line,) = [l for l in log_lines(capsys) if "[textql] ask failed" in l]
    assert " ERROR [textql] ask failed request=- run=- " in line
    assert "ValueError: boom" in line and "sk-abcdefghijklmnop" not in line


@pytest.mark.parametrize("error", [TextQLError("TextQL returned 401: bad token=abc123"),
                                   ValueError("boom token=abc123")], ids=["textql", "unexpected"])
def test_a_failures_reply_is_redacted_like_its_log_line(serve, error):
    def ask(question):
        raise error

    code, body = call(serve(ask) + "/ask", {"question": "q"})
    assert code in (500, 502) and "abc123" not in body["error"]


@pytest.mark.parametrize("length", ["2000000", "-1", "lots"], ids=["too-big", "negative", "not-a-number"])
def test_a_bad_content_length_is_refused_without_reading(serve, length):
    import socket

    host, port = serve(lambda q: None).removeprefix("http://").split(":")
    with socket.create_connection((host, int(port)), timeout=5) as sock:
        sock.sendall(f"POST /ask HTTP/1.1\r\nHost: x\r\nContent-Length: {length}\r\n\r\n".encode())
        status = sock.recv(64).split(b" ")[1]
    assert status in (b"400", b"413")
