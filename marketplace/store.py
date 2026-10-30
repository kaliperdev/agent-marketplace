"""The global catalog database: agents, and every published version of each.

publish() is the only write. It checks the entry first, keeps every old
version, and refuses a changed entry that kept its version number, so what a
client installed as "jira 1.1.0" can never change underneath it.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from . import images as shots
from .entry import guide_images, missing_guide, ordered, parse_version, validate
from .log import log, redact

DEFAULT_URL = "postgresql://catalog:catalog@localhost:55433/catalog"
SCHEMA = Path(__file__).with_name("schema.sql")
_LATEST = " order by major desc, minor desc, patch desc limit 1"


class CatalogError(Exception):
    """An entry the catalog refuses, with a reason a person can act on."""


class OlderVersionError(CatalogError):
    """The catalog already has a newer version of this agent."""


def connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url, autocommit=True)


def apply_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA.read_text(encoding="utf-8"))


def publish(conn: psycopg.Connection, entry: dict, published_by: str, require_guide: bool = False,
            images: Mapping[str, bytes] | None = None) -> str:
    """Publish `entry`, and record the attempt whatever the outcome (publish_attempts).
    Returns and raises exactly what _publish does. `require_guide`: a new version
    asking for a key, token or file must carry its setup guide (`catalog import`;
    seeding the router's own agents does not ask). `images`: the guide's
    screenshots by SHA-256; every one the entry names must be among them."""
    agent_id = str(entry.get("id") or "-") if isinstance(entry, dict) else "-"
    version = str(entry.get("version") or "-") if isinstance(entry, dict) else "-"
    try:
        outcome = _publish(conn, entry, published_by, require_guide, images or {})
    except OlderVersionError as err:
        _record(conn, agent_id, version, "older", str(err), published_by)
        raise
    except CatalogError as err:
        _record(conn, agent_id, version, "refused", str(err), published_by)
        raise
    _record(conn, agent_id, version, outcome, None, published_by)
    return outcome


def _record(conn, agent_id: str, version: str, outcome: str, reason: str | None, by: str) -> None:
    """Best effort: a catalog that cannot write its trail must still answer the publish."""
    try:
        conn.execute(
            "insert into publish_attempts (agent_id, version, outcome, reason, by) values (%s, %s, %s, %s, %s)",
            (agent_id, version, outcome, reason, by),
        )
    except psycopg.Error as err:
        log("WARN", "catalog", "publish attempt not recorded", agent=agent_id, version=version,
            outcome=outcome, error=redact(f"{type(err).__name__}: {err}"))


def _publish(conn: psycopg.Connection, entry: dict, published_by: str, require_guide: bool = False,
             images: Mapping[str, bytes] | None = None) -> str:
    errors = validate(entry)
    if errors:
        raise CatalogError("; ".join(errors))
    agent_id, version = entry["id"], entry["version"]
    pictures = _guide_pictures(entry, images or {})
    with conn.transaction():
        # Kept even when the version is already published ("unchanged"), so a
        # catalog rebuilt from published/ gets its pictures back by importing.
        for sha, (kind, data) in pictures.items():
            conn.execute("insert into guide_images (sha256, media_type, data) values (%s, %s, %s) "
                         "on conflict (sha256) do nothing", (sha, kind, data))
        row = conn.execute(
            "select version, entry from agent_versions where agent_id = %s" + _LATEST, (agent_id,)
        ).fetchone()
        if row is None:
            conn.execute(
                "insert into agents (id, position) "
                "values (%s, (select coalesce(max(position) + 1, 0) from agents)) "
                "on conflict (id) do nothing",
                (agent_id,),
            )
        else:
            latest_version, latest_entry = row
            if version == latest_version:
                if latest_entry == entry:
                    return "unchanged"
                raise CatalogError(
                    f"{agent_id} {version} is already published with different content; bump the version"
                )
            if parse_version(version) < parse_version(latest_version):
                raise OlderVersionError(
                    f"{agent_id} {version} is older than the latest published version, {latest_version}"
                )
        if require_guide and (reason := missing_guide(entry)):
            raise CatalogError(reason)
        major, minor, patch = parse_version(version)
        conn.execute(
            "insert into agent_versions (agent_id, version, major, minor, patch, entry, published_by) "
            "values (%s, %s, %s, %s, %s, %s, %s)",
            (agent_id, version, major, minor, patch, Jsonb(entry), published_by),
        )
    return "published"


def _guide_pictures(entry: dict, images: Mapping[str, bytes]) -> dict[str, tuple[str, bytes]]:
    """The screenshots the entry's guide names, checked: each given, the bytes
    it names by hash, a WebP or PNG of the size it says."""
    found: dict[str, tuple[str, bytes]] = {}
    for image in guide_images(entry):
        sha, name = image["sha256"], image["file"]
        data = images.get(sha)
        if data is None:
            raise CatalogError(f"guide screenshot {name} was not given with the entry (agents/<id>/guide/{name})")
        if shots.sha256(data) != sha:
            raise CatalogError(f"guide screenshot {name} is not the file its entry names: run catalog guide-images")
        if reason := shots.problem(data, image["width"], image["height"]):
            raise CatalogError(f"guide screenshot {name} {reason}")
        found[sha] = (shots.media_type(data), data)  # type: ignore[assignment]
    return found


def guide_image(conn: psycopg.Connection, sha256: str) -> tuple[str, bytes] | None:
    """One guide screenshot by its hash: (media type, bytes)."""
    row = conn.execute("select media_type, data from guide_images where sha256 = %s", (sha256,)).fetchone()
    return (row[0], bytes(row[1])) if row else None


def latest_entries(conn: psycopg.Connection) -> list[dict]:
    rows = conn.execute(
        "select distinct on (v.agent_id) v.entry, a.position "
        "from agent_versions v join agents a on a.id = v.agent_id "
        "order by v.agent_id, v.major desc, v.minor desc, v.patch desc"
    ).fetchall()
    return [ordered(entry) for entry, _ in sorted(rows, key=lambda row: row[1])]


def get_entry(conn: psycopg.Connection, agent_id: str, version: str | None = None) -> dict | None:
    if version is None:
        row = conn.execute(
            "select entry from agent_versions where agent_id = %s" + _LATEST, (agent_id,)
        ).fetchone()
    else:
        row = conn.execute(
            "select entry from agent_versions where agent_id = %s and version = %s", (agent_id, version)
        ).fetchone()
    return ordered(row[0]) if row else None


def versions(conn: psycopg.Connection, agent_id: str) -> list[dict]:
    """Every published version of one agent, oldest first, with the router each needs."""
    rows = conn.execute(
        "select version, entry->>'needs_router' from agent_versions "
        "where agent_id = %s order by major, minor, patch",
        (agent_id,),
    ).fetchall()
    return [{"version": version, "needsRouter": needs} for version, needs in rows]


def all_versions(conn: psycopg.Connection) -> dict[str, list[dict]]:
    """versions() for every agent in one query: agent id -> its versions, oldest first."""
    rows = conn.execute(
        "select agent_id, version, entry->>'needs_router' from agent_versions "
        "order by agent_id, major, minor, patch"
    ).fetchall()
    found: dict[str, list[dict]] = {}
    for agent_id, version, needs in rows:
        found.setdefault(agent_id, []).append({"version": version, "needsRouter": needs})
    return found


def history(conn: psycopg.Connection, agent_id: str) -> list:
    return conn.execute(
        "select version, published_by, published_at from agent_versions "
        "where agent_id = %s order by major, minor, patch",
        (agent_id,),
    ).fetchall()
