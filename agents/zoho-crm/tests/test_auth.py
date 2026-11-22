from __future__ import annotations

import httpx
import pytest

from zoho_crm_agent.auth import ZohoOAuthError, refresh_token_provider

MCP_URL = f"https://zoho-crm-data-insights-60065097786.zohomcp.in/mcp/{'b' * 32}/message"


def public_dns(*_args, **_kwargs):
    return [(2, 1, 6, "", ("8.8.8.8", 443))]


def test_refresh_token_discovery_and_cache_use_guarded_urls_and_bearer_value():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={"token_endpoint": "https://accounts.zoho.in/oauth/v2/token"})
        assert request.url.host == "accounts.zoho.in"
        assert b"refresh_token=refresh-secret" in request.content
        return httpx.Response(200, json={"access_token": "access-value", "expires_in": 3600})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        token = refresh_token_provider(MCP_URL, "client-id", "refresh-secret", client, public_dns)
        assert token() == "access-value"
        assert token() == "access-value"

    assert [request.url.path for request in calls] == [
        "/.well-known/oauth-authorization-server",
        "/oauth/v2/token",
    ]
    assert all("b" * 32 not in str(request.url) for request in calls)


def test_oauth_metadata_redirect_is_not_followed_or_exposed():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://127.0.0.1/steal"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        token = refresh_token_provider(MCP_URL, "client-id", "path-secret", client, public_dns)
        with pytest.raises(ZohoOAuthError) as error:
            token()

    assert len(requests) == 1
    assert "path-secret" not in str(error.value)
    assert "b" * 32 not in str(error.value)


def test_oauth_metadata_cannot_choose_an_arbitrary_token_endpoint():
    def handler(_request):
        return httpx.Response(200, json={"token_endpoint": "https://attacker.example/oauth/v2/token"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        token = refresh_token_provider(MCP_URL, "client-id", "refresh-secret", client, public_dns)
        with pytest.raises(ZohoOAuthError, match="untrusted token endpoint"):
            token()


def test_token_endpoint_redirect_is_not_followed():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={"token_endpoint": "https://accounts.zoho.in/oauth/v2/token"})
        return httpx.Response(307, headers={"location": "https://attacker.example/collect"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        token = refresh_token_provider(MCP_URL, "client-id", "refresh-secret", client, public_dns)
        with pytest.raises(ZohoOAuthError, match="redirects are disabled"):
            token()

    assert len(requests) == 2
    assert requests[-1].url.host == "accounts.zoho.in"
