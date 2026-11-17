from __future__ import annotations

import socket

import pytest

from zoho_crm_agent.security import (
    UnsafeDestination,
    require_public_dns,
    validate_mcp_url,
    validate_oauth_token_endpoint,
)

TOKEN = "a" * 32
INSIGHTS_URL = f"https://zoho-crm-data-insights-60065097786.zohomcp.in/mcp/{TOKEN}/message"


@pytest.mark.parametrize(
    "url",
    [
        INSIGHTS_URL,
        f"https://zoho-crm-data-insights-42.zohomcp.com/mcp/{TOKEN}/message",
    ],
)
def test_accepts_data_insights_url_shape(url):
    assert validate_mcp_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        f"https://zoho-crm-data-operations-60065097786.zohomcp.in/mcp/{TOKEN}/message",
        f"https://evil.example/mcp/{TOKEN}/message",
        f"https://zoho-crm-data-insights-60065097786.zohomcp.in.evil.example/mcp/{TOKEN}/message",
        f"http://zoho-crm-data-insights-60065097786.zohomcp.in/mcp/{TOKEN}/message",
        f"https://zoho-crm-data-insights-60065097786.zohomcp.in/mcp/{TOKEN}",
        f"https://zoho-crm-data-insights-60065097786.zohomcp.in/mcp/{TOKEN[:-1]}g/message",
        f"https://zoho-crm-data-insights-60065097786.zohomcp.in:444/mcp/{TOKEN}/message",
        f"https://user:pass@zoho-crm-data-insights-60065097786.zohomcp.in/mcp/{TOKEN}/message",
        f"https://zoho-crm-data-insights-60065097786.zohomcp.in/mcp/{TOKEN}/message?next=https://127.0.0.1",
        f"https://zoho-crm-data-insights-60065097786.zohomcp.in/mcp/{TOKEN}/message#fragment",
        f"https://127.0.0.1/mcp/{TOKEN}/message",
        f"https://localhost/mcp/{TOKEN}/message",
    ],
)
def test_rejects_wrong_hosts_paths_and_url_features(url):
    with pytest.raises(UnsafeDestination):
        validate_mcp_url(url)


def test_url_validation_error_never_echoes_the_secret_path():
    secret_url = INSIGHTS_URL + "?debug=1"
    with pytest.raises(UnsafeDestination) as error:
        validate_mcp_url(secret_url)
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://accounts.zoho.in/oauth/v2/token",
        "https://accounts.zoho.eu/oauth/v2/token",
        "https://zoho-crm-data-insights-60065097786.zohomcp.in/oauth/v2/token",
    ],
)
def test_accepts_only_expected_oauth_token_endpoint_shapes(endpoint):
    assert validate_oauth_token_endpoint(endpoint, INSIGHTS_URL) == endpoint


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://accounts.zoho.in/oauth/v2/token",
        "https://evil.example/oauth/v2/token",
        "https://127.0.0.1/oauth/v2/token",
        "https://accounts.zoho.in/other",
        "https://accounts.zoho.in/oauth/v2/token?redirect=https://127.0.0.1",
        "https://user:pass@accounts.zoho.in/oauth/v2/token",
        "https://accounts.zoho.in:444/oauth/v2/token",
    ],
)
def test_rejects_untrusted_oauth_endpoint(endpoint):
    with pytest.raises(UnsafeDestination):
        validate_oauth_token_endpoint(endpoint, INSIGHTS_URL)


def _dns_answer(address: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.9", "169.254.1.2", "::1", "fc00::1"])
def test_rejects_private_dns_answers(address):
    with pytest.raises(UnsafeDestination):
        require_public_dns("zoho-crm-data-insights-60065097786.zohomcp.in", lambda *_args, **_kwargs: _dns_answer(address))


def test_rejects_a_public_name_with_any_private_dns_answer():
    answers = _dns_answer("8.8.8.8") + _dns_answer("192.168.1.9")
    with pytest.raises(UnsafeDestination):
        require_public_dns("zoho-crm-data-insights-60065097786.zohomcp.in", lambda *_args, **_kwargs: answers)


def test_accepts_public_dns_answers():
    require_public_dns(
        "zoho-crm-data-insights-60065097786.zohomcp.in",
        lambda *_args, **_kwargs: _dns_answer("8.8.8.8"),
    )
