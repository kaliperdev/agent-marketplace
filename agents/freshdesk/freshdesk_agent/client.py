"""Restricted client for Freshdesk's account-hosted MCP endpoint.

Freshdesk's API key is sent as the raw Authorization value, not Bearer.
The account subdomain is the only user-controlled part of the destination.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from collections.abc import Callable
from typing import Any

import httpx

PROTOCOL_VERSION = "2025-06-18"
_SUBDOMAIN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_RESERVED = {"www", "api", "support", "localhost"}


class FreshdeskError(RuntimeError):
    """A restricted MCP call could not complete."""


def endpoint(subdomain: str) -> str:
    label = subdomain.strip().lower()
    if not _SUBDOMAIN.fullmatch(label) or label in _RESERVED:
        raise ValueError("enter one Freshdesk subdomain label, without a URL or dots")
    return f"https://{label}.freshdesk.com/mcp"


def require_public_dns(host: str, resolver: Callable = socket.getaddrinfo) -> None:
    """Refuse a hostname that resolves to any non-public address."""
    try:
        addresses = resolver(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise FreshdeskError("Freshdesk host could not be resolved") from exc
    if not addresses or any(
        not ipaddress.ip_address(item[4][0]).is_global for item in addresses
    ):
        raise FreshdeskError("Freshdesk host did not resolve only to public addresses")


def _payload(body: str) -> dict[str, Any]:
    text = body.strip()
    if text.startswith("{"):
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    for line in text.splitlines():
        if line.startswith("data:"):
            value = json.loads(line[5:].strip())
            if isinstance(value, dict):
                return value
    raise FreshdeskError("Freshdesk returned no MCP response")


class FreshdeskMCP:
    def __init__(
        self,
        subdomain: str,
        api_key: str,
        client: httpx.Client | None = None,
        resolver: Callable = socket.getaddrinfo,
    ) -> None:
        self.url = endpoint(subdomain)
        self.host = self.url.split("/")[2]
        if not api_key or "\n" in api_key or "\r" in api_key:
            raise ValueError("a Freshdesk API key is required")
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=30, follow_redirects=False, trust_env=False)
        self._resolver = resolver
        self._session_id: str | None = None
        self._ready = False

    def _request(self, method: str, params: dict[str, Any] | None, notification: bool = False) -> dict:
        require_public_dns(self.host, self._resolver)
        headers = {
            "Authorization": self._api_key,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notification:
            message["id"] = 1
        response = self._client.post(self.url, json=message, headers=headers, follow_redirects=False)
        if 300 <= response.status_code < 400:
            raise FreshdeskError("Freshdesk redirected the MCP request; connection refused")
        if response.status_code == 401:
            raise FreshdeskError("Freshdesk rejected the API key")
        if response.status_code >= 400:
            raise FreshdeskError(f"Freshdesk MCP returned HTTP {response.status_code}")
        if notification:
            return {}
        if session := response.headers.get("mcp-session-id"):
            self._session_id = session
        payload = _payload(response.text)
        if payload.get("error"):
            raise FreshdeskError("Freshdesk MCP reported a protocol error")
        return payload.get("result") or {}

    def connect(self) -> None:
        if self._ready:
            return
        self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "kaliper-freshdesk", "version": "0.1"},
        })
        self._request("notifications/initialized", {}, notification=True)
        self._ready = True

    def list_tools(self) -> list[dict]:
        self.connect()
        return list(self._request("tools/list", {}).get("tools") or [])

    def call(self, name: str, arguments: dict) -> str:
        self.connect()
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise FreshdeskError("Freshdesk MCP tool reported an error")
        return "\n".join(
            part.get("text", "") for part in result.get("content", [])
            if isinstance(part, dict) and part.get("type") == "text"
        )
