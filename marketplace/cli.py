"""catalog: manage the global agent catalog.

  catalog init-db                          create the tables (safe to re-run)
  catalog seed --registry F --extras F     one-time import from rytangle's files
  catalog list                             every agent and its latest version
  catalog history AGENT                    every version of one agent
  catalog export AGENT [--version V]       one entry as JSON, to edit
  catalog import FILE                      check and publish an edited entry
  catalog export-router                    rebuild router/config/agents.json

The database is the master copy. To change an agent: export it, edit the file,
raise "version", import it. Every import is checked; old versions are kept.
Exit codes: 0 ok, 1 not found, 2 refused or bad input, 3 database unreachable.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

import psycopg

from . import store
from .seed import entries_from_files
from .views import router_registry


def _dump(value) -> None:
    sys.stdout.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def _read_json(path: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise store.CatalogError(f"could not read {path}: {err}") from err


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="catalog", description="Manage the global agent catalog.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="create the tables (safe to re-run)")
    seed = sub.add_parser("seed", help="one-time import from rytangle's agents.json + extras.json")
    seed.add_argument("--registry", required=True)
    seed.add_argument("--extras", required=True)
    seed.add_argument("--needs-router", default="0.1.0")
    seed.add_argument("--by")
    sub.add_parser("list", help="every agent and its latest version")
    history = sub.add_parser("history", help="every version of one agent")
    history.add_argument("agent")
    export = sub.add_parser("export", help="print one entry as JSON")
    export.add_argument("agent")
    export.add_argument("--version")
    imp = sub.add_parser("import", help="check and publish an entry from a file")
    imp.add_argument("file")
    imp.add_argument("--by")
    sub.add_parser("export-router", help="print router/config/agents.json rebuilt from the catalog")
    args = parser.parse_args(argv)
    by = getattr(args, "by", None) or getpass.getuser()
    url = os.environ.get("CATALOG_DATABASE_URL", store.DEFAULT_URL)

    try:
        with store.connect(url) as conn:
            if args.command == "init-db":
                store.apply_schema(conn)
                print("catalog tables ready")
            elif args.command == "seed":
                registry, extras = _read_json(args.registry), _read_json(args.extras)
                try:
                    entries = entries_from_files(registry, extras, needs_router=args.needs_router)
                except (ValueError, KeyError, TypeError, AttributeError) as err:
                    raise store.CatalogError(
                        f"the seed files are not in the expected shape ({type(err).__name__}: {err})"
                    ) from err
                refused = False
                for entry in entries:
                    label = f"{entry['id']} {entry['version']}"
                    try:
                        print(f"{label}: {store.publish(conn, entry, by)}")
                    except store.OlderVersionError:
                        # Seed is a starting point: an agent edited since is kept as edited.
                        latest = store.get_entry(conn, entry["id"])["version"]
                        print(f"{label}: skipped, the catalog already has {latest}")
                    except store.CatalogError as err:
                        print(f"{label}: refused: {err}", file=sys.stderr)
                        refused = True
                if refused:
                    return 2
            elif args.command == "list":
                for entry in store.latest_entries(conn):
                    print(f"{entry['id']:<10} {entry['version']:<8} {entry['display']['name']}")
            elif args.command == "history":
                rows = store.history(conn, args.agent)
                if not rows:
                    print(f"no agent {args.agent!r} in the catalog", file=sys.stderr)
                    return 1
                for version, who, when in rows:
                    print(f"{version:<8} {who:<16} {when:%Y-%m-%d %H:%M}")
            elif args.command == "export":
                entry = store.get_entry(conn, args.agent, args.version)
                if entry is None:
                    which = f" at version {args.version}" if args.version else ""
                    print(f"no agent {args.agent!r}{which} in the catalog", file=sys.stderr)
                    return 1
                _dump(entry)
            elif args.command == "import":
                entry = _read_json(args.file)
                if not isinstance(entry, dict):
                    raise store.CatalogError(f"{args.file} must hold one JSON object (a catalog entry)")
                print(f"{entry.get('id')} {entry.get('version')}: {store.publish(conn, entry, by)}")
            elif args.command == "export-router":
                try:
                    _dump(router_registry(store.latest_entries(conn)))
                except ValueError as err:
                    raise store.CatalogError(str(err)) from err
    except store.CatalogError as err:
        print(f"refused: {err}", file=sys.stderr)
        return 2
    except psycopg.errors.UndefinedTable:
        print("the catalog database has no tables yet: run `catalog init-db`", file=sys.stderr)
        return 3
    except (psycopg.DataError, psycopg.IntegrityError) as err:
        print(f"refused by the database: {err}", file=sys.stderr)
        return 2
    except psycopg.OperationalError as err:
        print(f"could not reach the catalog database (CATALOG_DATABASE_URL): {err}", file=sys.stderr)
        return 3
    except psycopg.Error as err:
        print(f"catalog database error: {type(err).__name__}: {err}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
