"""The global catalog's read-only web service.

Client servers pull the catalog from here; they never connect to the catalog
database. Read-only by construction: there is no handler for anything but GET.

  GET /health
  GET /v1/catalog                          every agent's latest entry, in full
  GET /v1/catalog/page                     the same, in the shape the catalog page draws,
                                           each with every published version (to go back)
  GET /v1/agents/<id>/versions/<version>   one version of one agent, in full
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import store
from .log import log, redact
from .entry import FORMAT
from .views import page_view

Latest = Callable[[], list[dict]]
One = Callable[[str, str], "dict | None"]
Versions = Callable[[str], list[dict]]
AllVersions = Callable[[], "dict[str, list[dict]]"]


def build_handler(
    latest: Latest,
    one: One,
    versions: Versions = lambda agent_id: [],
    all_versions: AllVersions | None = None,
    access_log: bool = False,
    read_keys: frozenset[str] = frozenset(),
) -> type[BaseHTTPRequestHandler]:
    """`read_keys`: when set, every address but /health needs one of them as
    x-catalog-key. The hosted catalog is reachable from the internet; only
    client servers read it, each with its own key so one can be withdrawn."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # one line per request is noise here
            pass

        def _send(self, code: int, body: dict, quiet: bool = False) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            # Answers that went well are noise; everything else leaves a line.
            if not quiet and (code != 200 or access_log):
                log("INFO" if code < 500 else "ERROR", "catalog", "response",
                    method="GET", path=redact(self.path.split("?", 1)[0], 200), status=code)

        def _key_ok(self) -> bool:
            given = (self.headers.get("x-catalog-key") or "").encode()
            return any(hmac.compare_digest(given, key.encode()) for key in read_keys)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            parts = path.split("/")
            try:
                if path == "/health":
                    self._send(200, {"ok": True})
                elif read_keys and not self._key_ok():
                    self._send(401, {"error": "this catalog needs a read key (x-catalog-key)"})
                elif path == "/v1/catalog":
                    self._send(200, {"format": FORMAT, "agents": latest()})
                elif path == "/v1/catalog/page":
                    entries = latest()
                    if all_versions is None:
                        of = versions
                    else:  # one query for the whole page, not one per agent
                        found = all_versions()
                        of = lambda agent_id: found.get(agent_id, [])  # noqa: E731
                    self._send(200, {"agents": [{**page_view(e), "versions": of(e["id"])} for e in entries]})
                elif len(parts) == 6 and parts[1:3] == ["v1", "agents"] and parts[4] == "versions":
                    entry = one(parts[3], parts[5])
                    if entry is None:
                        self._send(404, {"error": f"no version {parts[5]} of {parts[3]}"})
                    else:
                        self._send(200, entry)
                else:
                    self._send(404, {"error": f"no route {path}"})
            except Exception as err:  # noqa: BLE001 - logged here; the reply stays generic
                # A database error can name the host and the user. That belongs in
                # this service's log, never in a reply to a client server.
                log("ERROR", "catalog", "request failed", method="GET", path=redact(path, 200), status=500,
                    error=f"{type(err).__name__}: {redact(err)}")
                self._send(500, {"error": "the catalog is unavailable right now"}, quiet=True)

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="catalog-service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8095)
    args = parser.parse_args(argv)
    url = os.environ.get("CATALOG_DATABASE_URL", store.DEFAULT_URL)

    def latest() -> list[dict]:
        with store.connect(url) as conn:
            return store.latest_entries(conn)

    def one(agent_id: str, version: str) -> dict | None:
        with store.connect(url) as conn:
            return store.get_entry(conn, agent_id, version)

    def all_versions() -> dict[str, list[dict]]:
        with store.connect(url) as conn:
            return store.all_versions(conn)

    access_log = bool(os.environ.get("CATALOG_ACCESS_LOG"))  # set it to log every 200 too
    # One key per client server, comma-separated, so one can be withdrawn alone.
    read_keys = frozenset(k.strip() for k in os.environ.get("CATALOG_READ_KEYS", "").split(",") if k.strip())
    server = ThreadingHTTPServer(
        (args.host, args.port),
        build_handler(latest, one, all_versions=all_versions, access_log=access_log, read_keys=read_keys),
    )
    log("INFO", "catalog", "listening", host=args.host, port=args.port, read_keys=len(read_keys))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
