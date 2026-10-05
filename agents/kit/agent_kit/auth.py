"""The access token the router sent with this call, for an agent that signs in.

The client's settings service runs "Sign in with…" and keeps the tokens; the
router sends a fresh access token with every MCP request (Authorization:
Bearer). A tool reads it here, for the call it is serving, so the agent never
holds a refresh token or the app's secret, and a revoked sign-in stops working
on the next call.
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator

_TOKEN: contextvars.ContextVar[str] = contextvars.ContextVar("agent_kit_access_token", default="")


class NotSignedIn(RuntimeError):
    """This call came with no token."""


def access_token() -> str:
    """The token sent with the call being served."""
    token = _TOKEN.get()
    if not token:
        raise NotSignedIn("this call came without a sign-in token: is the agent signed in on the catalog page?")
    return token


@contextlib.contextmanager
def serving(authorization: str | None) -> Iterator[None]:
    """For the length of one call: the bearer token in its Authorization header."""
    value = (authorization or "").strip()
    token = value[len("bearer "):].strip() if value.lower().startswith("bearer ") else ""
    reset = _TOKEN.set(token)
    try:
        yield
    finally:
        _TOKEN.reset(reset)
