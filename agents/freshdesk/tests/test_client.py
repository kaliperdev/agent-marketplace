import json
import socket

import httpx
import pytest

from freshdesk_agent.client import FreshdeskError, FreshdeskMCP, endpoint
from freshdesk_agent.server import READ_TOOLS, build_tools


def public_dns(host, port, **kwargs):
    assert host == "demo.freshdesk.com"
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]


def private_dns(host, port, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]


@pytest.mark.parametrize("subdomain", ["https://demo.freshdesk.com", "demo.freshdesk.com", "demo/evil", "localhost", "-demo", "demo-", "a..b", "www"])
def test_rejects_non_label_destinations(subdomain):
    with pytest.raises(ValueError):
        endpoint(subdomain)


def test_api_key_is_raw_and_only_reads_are_exposed():
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["authorization"] == "test-key"
        assert str(request.url) == "https://demo.freshdesk.com/mcp"
        body = json.loads(request.content)
        if body["method"] == "tools/call":
            assert body["params"]["name"] == "fetchTicket"
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "ticket"}]}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tools, problem = build_tools({"FRESHDESK_SUBDOMAIN": "demo", "FRESHDESK_API_KEY": "test-key"}, client=client, resolver=public_dns)
    assert problem is None
    assert {tool.name for tool in tools} == set(READ_TOOLS)
    assert all("create" not in tool.name and "update" not in tool.name and "delete" not in tool.name for tool in tools)
    assert next(tool for tool in tools if tool.name == "fetch_ticket").invoke({"arguments": {"ticket_id": 1}}) == "ticket"
    assert len(seen) == 3


def test_rejects_private_dns_before_sending_key():
    sent = []
    client = httpx.Client(transport=httpx.MockTransport(lambda request: sent.append(request)))
    remote = FreshdeskMCP("demo", "test-key", client=client, resolver=private_dns)
    with pytest.raises(FreshdeskError, match="public addresses"):
        remote.connect()
    assert not sent


def test_refuses_redirect_and_does_not_forward_key():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://evil.example/mcp"})

    remote = FreshdeskMCP("demo", "test-key", client=httpx.Client(transport=httpx.MockTransport(handler)), resolver=public_dns)
    with pytest.raises(FreshdeskError, match="redirected"):
        remote.connect()
    assert seen == ["https://demo.freshdesk.com/mcp"]


def test_rejects_header_injection():
    with pytest.raises(ValueError):
        FreshdeskMCP("demo", "key\r\nAuthorization: other")
