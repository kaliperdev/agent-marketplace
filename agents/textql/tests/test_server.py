import json
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
