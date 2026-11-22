from __future__ import annotations

import httpx
import pytest

from zoho_crm_agent.mcp import MCPError, ZohoCRMDataInsightsClient
from zoho_crm_agent.server import LIVE_PROBE_GATE, build_client, startup_problem

MCP_URL = f"https://zoho-crm-data-insights-60065097786.zohomcp.in/mcp/{'c' * 32}/message"
ENV = {
    "ZOHO_CRM_MCP_URL": MCP_URL,
    "ZOHO_CRM_CLIENT_ID": "client-id",
    "ZOHO_CRM_REFRESH_TOKEN": "refresh-secret",
}


def public_dns(*_args, **_kwargs):
    return [(2, 1, 6, "", ("8.8.8.8", 443))]


def test_transport_uses_oauth_and_refuses_redirects_without_leaking_url_token():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://127.0.0.1/redirect"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = ZohoCRMDataInsightsClient(MCP_URL, lambda: "access-value", http_client, public_dns)
        assert "<redacted>" in repr(client)
        with pytest.raises(MCPError, match="redirect refused") as error:
            client.connect()

    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer access-value"
    assert "c" * 32 not in str(error.value)


def test_url_token_never_appears_in_transport_error():
    def handler(_request):
        raise httpx.ConnectError("fake connection failure")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = ZohoCRMDataInsightsClient(MCP_URL, lambda: "access-value", http_client, public_dns)
        with pytest.raises(MCPError) as error:
            client.connect()
    assert "c" * 32 not in str(error.value)
    assert "fake connection failure" not in str(error.value)


def test_transport_refuses_private_dns_before_sending_request():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    def private_dns(*_args, **_kwargs):
        return [(2, 1, 6, "", ("10.0.0.5", 443))]

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = ZohoCRMDataInsightsClient(MCP_URL, lambda: "access-value", http_client, private_dns)
        with pytest.raises(MCPError, match="request failed safely"):
            client.connect()

    assert requests == []


def test_builder_rejects_data_operations_url_before_any_network_call():
    invalid = dict(ENV)
    invalid["ZOHO_CRM_MCP_URL"] = MCP_URL.replace("data-insights", "data-operations")
    client, problem = build_client(invalid, resolver=public_dns)
    assert client is None
    assert problem == "ZOHO_CRM_MCP_URL must be a Zoho CRM Data Insights URL"


def test_valid_connection_stays_gated_until_live_probe():
    assert startup_problem(ENV) == LIVE_PROBE_GATE


def test_missing_auth_inputs_are_reported_without_constructing_a_client():
    client, problem = build_client({"ZOHO_CRM_MCP_URL": MCP_URL}, resolver=public_dns)
    assert client is None
    assert problem == "ZOHO_CRM_CLIENT_ID, ZOHO_CRM_REFRESH_TOKEN are not set"
