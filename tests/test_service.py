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

    def start(latest, one=lambda agent_id, version: None, versions=lambda agent_id: [], all_versions=None):
        server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(latest, one, versions, all_versions))
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


def test_the_page_view_lists_each_agents_versions(serve, entries):
    jira = next(e for e in entries if e["id"] == "jira")
    base = serve(lambda: [jira], versions=lambda agent_id: [{"version": "1.0.0", "needsRouter": "0.1.0"}])
    _, body = get(base + "/v1/catalog/page")
    assert body["agents"][0]["versions"] == [{"version": "1.0.0", "needsRouter": "0.1.0"}]


def test_the_page_view_asks_for_all_versions_once_per_request(serve, entries):
    calls = {"all": 0, "each": 0}

    def all_versions():
        calls["all"] += 1
        return {e["id"]: [{"version": e["version"], "needsRouter": "0.1.0"}] for e in entries}

    def each(agent_id):
        calls["each"] += 1
        return []

    base = serve(lambda: entries, versions=each, all_versions=all_versions)
    _, body = get(base + "/v1/catalog/page")
    assert calls == {"all": 1, "each": 0}
    assert len(entries) > 1
    assert all(a["versions"] == [{"version": a["version"], "needsRouter": "0.1.0"}] for a in body["agents"])
    get(base + "/v1/catalog/page")
    assert calls["all"] == 2


def test_the_page_view_gives_an_agent_with_no_versions_an_empty_list(serve, entries):
    jira = next(e for e in entries if e["id"] == "jira")
    _, body = get(serve(lambda: [jira], all_versions=lambda: {}) + "/v1/catalog/page")
    assert body["agents"][0]["versions"] == []


def lines(capsys):
    captured = capsys.readouterr()
    return (captured.out + captured.err).splitlines()


def test_a_missing_route_or_version_is_logged_at_info_with_path_and_status(serve, capsys):
    base = serve(lambda: [])
    get(base + "/nope?x=1")
    get(base + "/v1/agents/jira/versions/9.9.9")
    out = lines(capsys)
    assert any(" INFO [catalog] response " in l and "path=/nope" in l and "status=404" in l and "x=1" not in l
               for l in out)
    assert any("path=/v1/agents/jira/versions/9.9.9" in l and "status=404" in l for l in out)


def test_a_failure_is_logged_once_at_error_with_a_redacted_reason(serve, capsys):
    def latest():
        raise RuntimeError("connection to host db user=catalog password=hunter2 failed")

    status, body = get(serve(latest) + "/v1/catalog")
    assert status == 500 and "hunter2" not in json.dumps(body)
    (line,) = lines(capsys)
    assert " ERROR [catalog] request failed " in line and "path=/v1/catalog" in line and "status=500" in line
    assert "RuntimeError" in line and "hunter2" not in line


def test_successful_requests_are_not_logged_unless_asked(serve, capsys):
    get(serve(lambda: []) + "/health")
    assert lines(capsys) == []
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(lambda: [], lambda a, v: None, access_log=True))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        get(f"http://127.0.0.1:{server.server_address[1]}/health")
    finally:
        server.shutdown()
        server.server_close()
    assert any("[catalog] response" in l and "path=/health" in l and "status=200" in l for l in lines(capsys))
