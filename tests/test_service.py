import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from marketplace.service import build_handler


@pytest.fixture
def serve():
    servers = []

    def start(latest, one=lambda agent_id, version: None):
        server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(latest, one))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_health(serve):
    assert get(serve(lambda: []) + "/health") == (200, {"ok": True})


def test_the_catalog_serves_full_entries_with_their_format(serve, entries):
    code, body = get(serve(lambda: entries) + "/v1/catalog")
    assert code == 200 and body["format"] == 1
    assert [e["id"] for e in body["agents"]] == [e["id"] for e in entries]
    assert "router" in body["agents"][0]


def test_the_page_view_is_what_the_catalog_page_reads(serve, entries):
    code, body = get(serve(lambda: entries) + "/v1/catalog/page")
    assert code == 200
    first = body["agents"][0]
    assert "capabilities" in first and "router" not in first


def test_one_version_of_one_agent(serve, entries):
    def one(agent_id, version):
        return next((e for e in entries if e["id"] == agent_id and e["version"] == version), None)

    base = serve(lambda: entries, one)
    code, body = get(base + "/v1/agents/jira/versions/1.0.0")
    assert code == 200 and body["id"] == "jira"
    code, body = get(base + "/v1/agents/jira/versions/9.9.9")
    assert code == 404 and "9.9.9" in body["error"]


def test_an_unknown_address_is_404(serve):
    code, body = get(serve(lambda: []) + "/v1/nothing")
    assert code == 404


def test_a_broken_database_is_a_500_without_its_details(serve):
    def boom():
        raise RuntimeError("password authentication failed for user catalog at catalog-db:5432")

    code, body = get(serve(boom) + "/v1/catalog")
    assert code == 500
    assert "password" not in json.dumps(body) and "catalog-db" not in json.dumps(body)
