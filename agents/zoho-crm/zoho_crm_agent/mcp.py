"""Minimal streamable-HTTP MCP client for the Zoho CRM Data Insights host."""

from __future__ import annotations

import json
import socket
from collections.abc import Callable
from typing import Any

import httpx

from .security import Resolver, UnsafeDestination, require_public_url, validate_mcp_url

PROTOCOL_VERSION = "2025-06-18"
TokenProvider = Callable[[], str]


class MCPError(RuntimeError):
    """A remote MCP request failed without exposing its secret URL."""


class ZohoCRMDataInsightsClient:
    """MCP transport whose URL is constrained to a Zoho Data Insights server."""

    def __init__(
        self,
        url: str,
        token: TokenProvider,
        client: httpx.Client,
        resolver: Resolver = socket.getaddrinfo,
    ) -> None:
        self._url = validate_mcp_url(url)
        self._token = token
        self._client = client
        self._resolver = resolver
        self._session_id: str | None = None
        self._ready = False
        self._tool_names: set[str] | None = None

    def __repr__(self) -> str:
        return "ZohoCRMDataInsightsClient(url=<redacted>)"

    def _headers(self) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "mcp-protocol-version": PROTOCOL_VERSION,
        }
        access_token = self._token()
        if access_token:
            headers["authorization"] = f"Bearer {access_token}"
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        return headers

    @staticmethod
    def _parse(body: str) -> dict[str, Any]:
        text = body.strip()
        if not text:
            return {}
        if text.startswith("{"):
            payload = json.loads(text)
        else:
            payload = None
            for line in text.splitlines():
                if line.startswith("data:"):
                    content = line[len("data:") :].strip()
                    if content:
                        payload = json.loads(content)
                        break
        if not isinstance(payload, dict):
            raise MCPError("Zoho CRM returned no JSON-RPC message")
        return payload

    def _send(self, method: str, params: dict | None = None, notify: bool = False) -> dict:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notify:
            message["id"] = 1
        try:
            require_public_url(self._url, self._resolver)
            response = self._client.post(
                self._url,
                json=message,
                headers=self._headers(),
                follow_redirects=False,
            )
        except (httpx.HTTPError, UnsafeDestination):
            raise MCPError("Zoho CRM Data Insights request failed safely; URL redacted") from None

        if response.is_redirect or response.is_informational:
            raise MCPError("Zoho CRM Data Insights redirect refused; URL redacted")
        if response.status_code >= 400:
            raise MCPError(f"Zoho CRM Data Insights returned HTTP {response.status_code}; URL redacted")

        session = response.headers.get("mcp-session-id")
        if session:
            self._session_id = session
        if notify:
            return {}
        try:
            payload = self._parse(response.text)
        except (ValueError, UnicodeDecodeError):
            raise MCPError("Zoho CRM Data Insights response was not valid MCP JSON") from None
        if payload.get("error"):
            raise MCPError("Zoho CRM Data Insights returned a JSON-RPC error")
        result = payload.get("result") or {}
        if not isinstance(result, dict):
            raise MCPError("Zoho CRM Data Insights returned an invalid MCP result")
        return result

    def connect(self) -> dict:
        if self._ready:
            return {}
        result = self._send(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "zoho-crm-data-insights-draft", "version": "0.0.0"},
            },
        )
        self._send("notifications/initialized", {}, notify=True)
        self._ready = True
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        self.connect()
        tools = self._send("tools/list", {}).get("tools") or []
        if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
            raise MCPError("Zoho CRM Data Insights returned an invalid tool list")
        self._tool_names = {
            tool["name"] for tool in tools if isinstance(tool.get("name"), str)
        }
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool only after its name was returned by this Data Insights host."""
        if self._tool_names is None:
            self.list_tools()
        if name not in (self._tool_names or set()):
            raise MCPError("requested name was not returned by the Data Insights tool list")
        result = self._send("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise MCPError("Zoho CRM Data Insights tool call failed")
        return result
