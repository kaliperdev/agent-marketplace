"""The access token (and access note) the router sent with this call, for an agent that signs in.

The client's settings service runs "Sign in with…" and keeps the tokens; the
router sends a fresh access token with every MCP request (Authorization:
Bearer). A tool reads it here, for the call it is serving, so the agent never
holds a refresh token or the app's secret, and a revoked sign-in stops working
on the next call.

A call may also carry an access note (the x-agent-access header): a signed
statement the router makes about what this call may read. A tool reads it with
call_note(), for the length of one call, like the token.
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator

NOTE_HEADER = "x-agent-access"

_TOKEN: contextvars.ContextVar[str] = contextvars.ContextVar("agent_kit_access_token", default="")
_NOTE: contextvars.ContextVar[str | None] = contextvars.ContextVar("agent_kit_access_note", default=None)


class NotSignedIn(RuntimeError):
    """This call came with no token."""


def access_token() -> str:
    """The token sent with the call being served."""
    token = _TOKEN.get()
    if not token:
        raise NotSignedIn("this call came without a sign-in token: is the agent signed in on the catalog page?")
    return token


def call_note() -> str | None:
    """The raw access note sent with the call being served; None when it came without one."""
    return _NOTE.get()


@contextlib.contextmanager
def serving(authorization: str | None, note: str | None = None) -> Iterator[None]:
    """For the length of one call: the bearer token in its Authorization header
    and the access note in its x-agent-access header."""
    value = (authorization or "").strip()
    token = value[len("bearer "):].strip() if value.lower().startswith("bearer ") else ""
    reset = _TOKEN.set(token)
    reset_note = _NOTE.set(note or None)
    try:
        yield
    finally:
        _NOTE.reset(reset_note)
        _TOKEN.reset(reset)
