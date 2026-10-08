# Copied from rytangle router/app/tools/mcp.py (WorkDrive 1.x stays built into the
# router until every server runs the catalog). Unchanged: the client of Zoho's own MCP server.
"""A minimal MCP client, speaking streamable HTTP.

The transport half of an MCP integration, kept apart from any one server's
meaning the same way `drive.py` is kept apart from `gdocs.py`. Nothing here
knows what WorkDrive is.

Only the three calls this project needs are implemented -- `initialize`,
`tools/list` and `tools/call`. MCP also defines resources, prompts and
subscriptions; a client that implemented them would be code nothing runs.

Verified by execution against Zoho's server (`zoho-mcp 1.0.0`, protocol
2025-06-18) rather than read from the specification, and three of the findings
changed the design:

- **A session id comes back on the initialize RESPONSE header**, `mcp-session-id`,
  and every later request must echo it. Without it the server answers the first
  call and refuses the second, which reads like an intermittent fault rather than
  a missing header.
- **Replies arrive as server-sent events, not JSON**, despite a JSON request:
  the body is `data: {...}` lines. Parsing it as JSON fails on a response that is
  perfectly well formed.
- **`notifications/initialized` must be sent and must carry no id.** It is a
  notification, so a JSON-RPC id makes it a request the server has no reply for.

The access token is fetched through a callable rather than held, so the caller
owns refreshing and this class never sees a credential it might log.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "rytangle-router"

TokenProvider = Callable[[], str]


class MCPError(RuntimeError):
    """An MCP request did not return usable content."""


class MCPClient:
    """One MCP server, over streamable HTTP.

    The session is established lazily on the first call and reused, so a run that
    asks three questions of one server pays for one handshake.
    """

    def __init__(
        self,
        url: str,
        token: TokenProvider,
        client: httpx.Client,
        client_name: str = CLIENT_NAME,
    ) -> None:
        self.url = url
        self.token = token
        self.client = client
        self.client_name = client_name
        self._session_id: str | None = None
        self._ready = False
        # Whether this token has ever been accepted. A 401 before and a 401 after
        # mean different things, and saying so is the difference between checking
        # one call's permissions and re-authorising a working connection.
        self._accepted = False

    # ── the wire ─────────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
            # Both, because a server may answer either way and this one answers
            # with events even though the request is JSON.
            "accept": "application/json, text/event-stream",
            "mcp-protocol-version": PROTOCOL_VERSION,
        }
        # A server on this machine's private network takes no token, and
        # "Bearer " with nothing after it is a header HTTP refuses to send.
        token = self.token()
        if token:
            headers["authorization"] = f"Bearer {token}"
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        return headers

    @staticmethod
    def _parse(body: str) -> dict:
        """One JSON-RPC message, from either a JSON body or an SSE stream."""
        text = body.strip()
        if not text:
            return {}
        if text.startswith("{"):
            return json.loads(text)
        for line in text.splitlines():
            if line.startswith("data:"):
                payload = line[len("data:"):].strip()
                if payload:
                    return json.loads(payload)
        raise MCPError(f"no JSON-RPC message in the response: {text[:200]}")

    def _send(self, method: str, params: dict | None, notify: bool = False) -> dict:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notify:
            # Any non-zero id will do; one request is in flight at a time.
            message["id"] = 1

        response = self.client.post(self.url, json=message, headers=self._headers())

        session = response.headers.get("mcp-session-id")
        if session:
            self._session_id = session

        if response.status_code == 401:
            raise MCPError(self._unauthorised(method))
        self._accepted = True
        if response.status_code >= 400:
            raise MCPError(
                f"{method} returned {response.status_code}: {response.text[:200]}"
            )
        if notify:
            return {}

        payload = self._parse(response.text)
        if payload.get("error"):
            error = payload["error"]
            raise MCPError(
                f"{method} failed: {error.get('message', error)} "
                f"(code {error.get('code', '?')})"
            )
        return payload.get("result") or {}

    def _unauthorised(self, method: str) -> str:
        """What a 401 actually means, which depends on what came before it.

        Observed against Zoho's server: on one session, with one token,
        `getUserInfo` and `Get_All_Teams_Of_User` both returned data and
        `Get_My_Folder_Id` then returned 401. The credential was never in
        question -- that call was refused on its own merits.

        Reporting every 401 as an expired refresh token sends whoever reads it to
        re-authorise a connection that works, and the real cause -- a scope the
        grant does not include, or an id belonging to somebody else -- goes
        unlooked-at because the message named something else.
        """
        if self._accepted:
            return (
                f"{method} was refused (401) though this connection is "
                "authenticated -- earlier calls on this session succeeded. The "
                "token is fine; this particular request is not permitted. Most "
                "likely the grant lacks the scope this API needs, or an id in the "
                "request belongs to an account this one cannot act for."
            )
        return (
            "the MCP server rejected the access token (401), and no call on this "
            "connection has ever been accepted. The refresh token may have been "
            "revoked, or the connection may need authorising again."
        )

    # ── the session ──────────────────────────────────────────────────────────

    def connect(self) -> dict:
        """Handshake once, and remember that it happened."""
        if self._ready:
            return {}
        result = self._send(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": self.client_name, "version": "1.0"},
            },
        )
        # A notification, so no id -- see the module docstring.
        self._send("notifications/initialized", {}, notify=True)
        self._ready = True
        return result

    # ── the two calls anything needs ─────────────────────────────────────────

    def list_tools(self) -> list[dict]:
        self.connect()
        return list(self._send("tools/list", {}).get("tools") or [])

    def call(self, name: str, arguments: dict) -> str:
        """One tool call, as the text the server returned.

        A tool-level failure comes back as `isError` with the reason in the
        content rather than as a JSON-RPC error, so it is raised here: the caller
        decides whether the agent sees a message or an exception.
        """
        return self.call_detailed(name, arguments)[0]

    def call_detailed(self, name: str, arguments: dict) -> tuple[str, dict]:
        """One tool call: its text, and its structured result (`structuredContent`,
        an empty dict when the server sent none). Kaliper's agent servers put a
        tool's rows there as {"table": {...}}."""
        self.connect()
        result = self._send("tools/call", {"name": name, "arguments": arguments})
        text = "".join(
            block.get("text", "")
            for block in result.get("content") or []
            if block.get("type") == "text"
        )
        if result.get("isError"):
            raise MCPError(f"{name}: {text[:300] or 'the server reported an error'}")
        structured = result.get("structuredContent")
        return text, structured if isinstance(structured, dict) else {}


# ── OAuth, as the MCP authorisation spec defines it ──────────────────────────

# An access token lives an hour; refreshing a minute early costs one request a
# run and removes a whole class of "expired between the check and the call".
EXPIRY_MARGIN_SECONDS = 60


def discover_token_endpoint(mcp_url: str, client: httpx.Client) -> str:
    """Where to exchange a refresh token, found rather than configured.

    MCP's authorisation spec puts this at a well-known path on the server's
    origin, so a deployment needs the MCP URL and a refresh token and nothing
    else. Asking a client's administrator to also find a token endpoint is asking
    them to get it wrong, and the value is entirely derivable.
    """
    origin = httpx.URL(mcp_url)
    well_known = str(origin.copy_with(path="/.well-known/oauth-authorization-server", query=None))
    response = client.get(well_known)
    if response.status_code != 200:
        raise MCPError(
            f"could not read the OAuth metadata at {well_known}: "
            f"{response.status_code} {response.text[:150]}"
        )
    endpoint = (response.json() or {}).get("token_endpoint")
    if not endpoint:
        raise MCPError(f"the OAuth metadata at {well_known} names no token_endpoint")
    return str(endpoint)


def refresh_token_provider(
    mcp_url: str,
    client_id: str,
    refresh_token: str,
    client: httpx.Client,
) -> TokenProvider:
    """Access tokens minted from a long-lived refresh token, cached until they expire.

    No client secret: the connection is registered as a public client, which is
    what `token_endpoint_auth_methods_supported: ["none"]` means, so the refresh
    token IS the credential. Verified against the live endpoint -- the refresh
    token is reusable rather than rotated, so a deployment can hold it in .env
    the way the Cliq agent already does.
    """
    import time

    state: dict[str, object] = {"token": "", "expires_at": 0.0, "endpoint": "", "api_domain": ""}

    def provide() -> str:
        now = time.monotonic()
        token = state["token"]
        if token and now < float(state["expires_at"]):
            return str(token)

        if not state["endpoint"]:
            state["endpoint"] = discover_token_endpoint(mcp_url, client)

        response = client.post(
            str(state["endpoint"]),
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
        )
        body: dict = {}
        try:
            body = response.json()
        except ValueError:
            pass
        fresh = body.get("access_token")
        if not fresh:
            raise MCPError(
                "could not refresh the WorkDrive access token: "
                f"{body.get('error') or response.text[:200]}"
            )
        state["token"] = str(fresh)
        state["expires_at"] = now + max(
            0.0, float(body.get("expires_in") or 3600) - EXPIRY_MARGIN_SECONDS
        )
        # Zoho names the host its own APIs answer on. Kept because it is not
        # guessable and not the same host as anything else in play: the file
        # metadata advertises `download-accl.zoho.com`, which refuses this token
        # outright, while the domain named here serves the same download. Reading
        # it from the response is what stops that being rediscovered by hand.
        if body.get("api_domain"):
            state["api_domain"] = str(body["api_domain"])
        return str(fresh)

    # The domain rides on the provider rather than being returned separately:
    # anything holding a token needs the host that token is for, and two values
    # threaded through call sites in parallel is how they drift apart.
    provide.api_domain = lambda: str(state["api_domain"])  # type: ignore[attr-defined]
    return provide
