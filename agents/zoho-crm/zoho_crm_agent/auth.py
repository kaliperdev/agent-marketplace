"""OAuth refresh scaffolding for Zoho's hosted CRM MCP server."""

from __future__ import annotations

import json
import math
import socket
import time
from collections.abc import Callable

import httpx

from .security import (
    Resolver,
    UnsafeDestination,
    require_public_url,
    validate_mcp_url,
    validate_oauth_token_endpoint,
)

EXPIRY_MARGIN_SECONDS = 60


class ZohoOAuthError(RuntimeError):
    """OAuth failed; messages intentionally omit URLs, tokens, and response bodies."""


def discover_token_endpoint(
    mcp_url: str,
    client: httpx.Client,
    resolver: Resolver = socket.getaddrinfo,
) -> str:
    """Read OAuth metadata from the validated MCP origin without following redirects."""
    mcp_url = validate_mcp_url(mcp_url)
    origin = httpx.URL(mcp_url)
    metadata_url = str(origin.copy_with(path="/.well-known/oauth-authorization-server"))
    try:
        require_public_url(metadata_url, resolver)
        response = client.get(metadata_url, follow_redirects=False)
    except (httpx.HTTPError, UnsafeDestination):
        raise ZohoOAuthError("could not safely read Zoho OAuth metadata") from None

    if response.status_code != 200:
        raise ZohoOAuthError("Zoho OAuth metadata request failed; redirects are disabled")
    try:
        metadata = response.json()
    except (ValueError, json.JSONDecodeError):
        raise ZohoOAuthError("Zoho OAuth metadata was not valid JSON") from None
    endpoint = metadata.get("token_endpoint") if isinstance(metadata, dict) else None
    try:
        return validate_oauth_token_endpoint(endpoint, mcp_url)
    except (UnsafeDestination, TypeError):
        raise ZohoOAuthError("Zoho OAuth metadata named an untrusted token endpoint") from None


def refresh_token_provider(
    mcp_url: str,
    client_id: str,
    refresh_token: str,
    client: httpx.Client,
    resolver: Resolver = socket.getaddrinfo,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[], str]:
    """Return a cached access-token provider using the MCP OAuth metadata endpoint."""
    mcp_url = validate_mcp_url(mcp_url)
    if not client_id or not refresh_token:
        raise ZohoOAuthError("Zoho OAuth client credentials are missing")

    state: dict[str, object] = {"access_token": "", "expires_at": 0.0, "endpoint": ""}

    def provide() -> str:
        now = clock()
        current = state["access_token"]
        if current and now < float(state["expires_at"]):
            return str(current)

        endpoint = str(state["endpoint"])
        if not endpoint:
            endpoint = discover_token_endpoint(mcp_url, client, resolver)
            state["endpoint"] = endpoint
        try:
            require_public_url(endpoint, resolver)
            response = client.post(
                endpoint,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                },
                follow_redirects=False,
            )
        except (httpx.HTTPError, UnsafeDestination):
            raise ZohoOAuthError("Zoho OAuth token refresh failed safely") from None

        if response.status_code != 200:
            raise ZohoOAuthError("Zoho OAuth token refresh failed; redirects are disabled")
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError):
            raise ZohoOAuthError("Zoho OAuth token response was not valid JSON") from None
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise ZohoOAuthError("Zoho OAuth response contained no access token")
        try:
            expires_in = float(body.get("expires_in", 3600))
        except (TypeError, ValueError):
            expires_in = 3600.0
        if not math.isfinite(expires_in) or expires_in <= 0:
            expires_in = 3600.0

        state["access_token"] = token
        state["expires_at"] = now + max(0.0, expires_in - EXPIRY_MARGIN_SECONDS)
        return token

    return provide
