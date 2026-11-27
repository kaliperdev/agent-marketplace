"""A call's own access: the router's signed note says which login's pass, which project and which
datasets this one call may read, and the reader checks it before it reads anything.

The note (header x-agent-access) is `<body>.<stamp>`: body is the base64url (no `=`) of the payload's
JSON ({"v": 1, "agent", "login", "project", "datasets": ["project.dataset", ...], "at": unix seconds}),
stamp the base64url of HMAC-SHA256 over the body with ACCESS_NOTE_KEY, the key this bot's router also
holds. A note is good for 60 seconds either side of `at`. The Google pass comes in the call's
Authorization header.

A call with a note reads with that pass, project and dataset list only: never merged with the reader's
own, and a note that cannot be trusted is refused, never answered with the reader's own login. A call
without a note is not touched here. No text in this file can hold the note, the pass or the key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from agent_kit import auth, login
from bigquery_reader_agent.bigquery import BigQuery, BigQueryError, DatasetRef, parse_datasets
from bigquery_reader_agent.tablemap import MapCaches
from bigquery_reader_agent.tools import Scope

NOTE_KEY_SETTING = "ACCESS_NOTE_KEY"
MAX_AGE_SECONDS = 60
MAX_DATASETS = 100


class AccessRefused(Exception):
    """A call's access cannot be used. The text never holds the note or the pass."""


NO_KEY = f"This reader was started without {NOTE_KEY_SETTING}, so it cannot check a data access note."
NOT_SIGNED = "This call's data access note is not signed by this bot's router."
NOT_FRESH = f"This call's data access note is not fresh: a note is good for {MAX_AGE_SECONDS} seconds."
NOT_READABLE = "This call's data access note is not readable."
NO_PASS = "This call's data access note came without its Google pass."


@dataclass(frozen=True)
class Note:
    agent: str
    login: str
    project: str
    datasets: tuple[DatasetRef, ...]

    @property
    def key(self) -> tuple[str, str, tuple[str, ...]]:
        """What one access is: its login, project and datasets (their order does not matter)."""
        return (self.login, self.project, tuple(sorted(str(d) for d in self.datasets)))


def _text(payload: dict, name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise AccessRefused(NOT_READABLE)
    return value


def read_note(raw: str, secret: str | None, now: float) -> Note:
    """The note's contents, once its stamp and age are good. Checked in this order: a key to check
    with, the stamp (before anything is decoded), the payload, the age."""
    if not secret:
        raise AccessRefused(NO_KEY)
    body, dot, stamp = raw.partition(".")
    if not dot or not body or not stamp or "." in stamp:
        raise AccessRefused(NOT_READABLE)
    digest = hmac.new(secret.encode(), body.encode("utf-8"), hashlib.sha256).digest()
    expected = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    if not hmac.compare_digest(expected.encode(), stamp.encode("utf-8")):
        raise AccessRefused(NOT_SIGNED)
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError:
        raise AccessRefused(NOT_READABLE) from None
    if not isinstance(payload, dict) or type(payload.get("v")) is not int or payload["v"] != 1:
        raise AccessRefused(NOT_READABLE)
    agent, login, project = _text(payload, "agent"), _text(payload, "login"), _text(payload, "project")
    listed, at = payload.get("datasets"), payload.get("at")
    if (not isinstance(listed, list) or not 1 <= len(listed) <= MAX_DATASETS
            or not all(isinstance(d, str) and d and not any(c.isspace() or c == "," for c in d) for d in listed)
            or isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at)):
        raise AccessRefused(NOT_READABLE)
    try:
        datasets = parse_datasets(" ".join(listed), project)
    except ValueError:
        raise AccessRefused(NOT_READABLE) from None
    if not all(d.project and d.dataset for d in datasets):
        raise AccessRefused(NOT_READABLE)
    if abs(now - at) > MAX_AGE_SECONDS:
        raise AccessRefused(NOT_FRESH)
    return Note(agent, login, project, datasets)


def call_access(secret: str | None, client: httpx.Client, now: float) -> tuple[Note, BigQuery] | None:
    """None for a call without a note; else its note and a BigQuery in the note's project that reads
    with the call's pass. The pass is held as a value: build_map describes tables on worker threads,
    which do not see this call's context."""
    raw = auth.call_note()
    if raw is None:
        return None
    note = read_note(raw, secret, now)
    try:
        token = auth.access_token()
    except auth.NotSignedIn:
        raise AccessRefused(NO_PASS) from None
    return note, BigQuery(client, lambda: token, note.project)


def per_call(secret: str | None, client: httpx.Client, maps: MapCaches,
             clock: Callable[[], float] = time.time) -> Callable[[], Scope | None]:
    """What make_bq_tools asks at the start of each tool: this call's scope, or None to read as the reader."""
    def scope() -> Scope | None:
        access = call_access(secret, client, clock())
        if access is None:
            return None
        note, bq = access
        return Scope(bq, note.datasets, maps.cache(note.key), from_note=True)

    return scope


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def access_check(secret: str | None, client: httpx.Client,
                 clock: Callable[[], float] = time.time) -> Callable[[], dict]:
    """The answer to a /check that carries a note (Test access): each of the note's datasets listed with
    the call's pass (tables.list is free). Never the reader's own login; a note that cannot be trusted is
    refused. The answer holds dataset names and BigQuery's own words, never the note, the pass or the key."""
    def check() -> dict:
        try:
            access = call_access(secret, client, clock())
        except AccessRefused as error:
            return login.result(login.REFUSED, str(error))
        if access is None:
            return login.result(login.REFUSED, "This check came without a data access note.")
        note, bq = access
        failed, readable, unreachable = [], [], False
        for dataset in note.datasets:
            name = dataset.dataset if dataset.project == note.project else str(dataset)
            try:
                readable.append(f"{name} ({_plural(len(bq.tables(dataset)), 'table')})")
            except BigQueryError as error:
                failed.append(f"{name}: could not be listed. {str(error).rstrip('.')}.")
                if str(error).startswith("BigQuery could not be reached"):
                    unreachable = True
                    break  # one answer is enough: do not wait out a timeout for each of up to 100 datasets
        can = ", ".join(readable[:-1]) + (" and " if len(readable) > 1 else "") + "".join(readable[-1:])
        detail = " ".join([*failed, f"Can read {can}."] if readable else failed)
        if not failed:
            return login.result(login.OK, detail)
        return login.result(login.UNREACHABLE if unreachable else login.REFUSED, detail)

    return check
