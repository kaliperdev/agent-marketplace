"""Logins, read the way the router reads them: blank, or a placeholder still
containing "replace", counts as not set."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

PLACEHOLDER_MARKER = "replace"


def credential(env: Mapping[str, str], key: str) -> str | None:
    value = (env.get(key) or "").strip()
    if not value or PLACEHOLDER_MARKER in value.lower():
        return None
    return value


def missing(env: Mapping[str, str], keys: Sequence[str]) -> str | None:
    """What /health reports when the agent cannot work: names, never values."""
    absent = [key for key in keys if credential(env, key) is None]
    if not absent:
        return None
    return f"{', '.join(absent)} {'is' if len(absent) == 1 else 'are'} not set"
