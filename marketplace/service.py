"""The global catalog's read-only web service.

Client servers pull the catalog from here; they never connect to the catalog
database. Read-only by construction: there is no handler for anything but GET.

  GET /health
  GET /v1/catalog                          every agent's latest entry, in full
  GET /v1/catalog/page                     the same, in the shape the catalog page draws
  GET /v1/agents/<id>/versions/<version>   one version of one agent, in full
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import store
from .entry import FORMAT
from .views import page_view

Latest = Callable[[], list[dict]]
One = Callable[[str, str], "dict | None"]


def build_handler(latest: Latest, one: One) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # one line per request is noise here
            pass

        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            parts = path.split("/")
            try:
                if path == "/health":
                    self._send(200, {"ok": True})
                elif path == "/v1/catalog":
                    self._send(200, {"format": FORMAT, "agents": latest()})
                elif path == "/v1/catalog/page":
                    self._send(200, {"agents": [page_view(e) for e in latest()]})
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
                print(f"[catalog] {path} failed: {type(err).__name__}: {err}", file=sys.stderr, flush=True)
                self._send(500, {"error": "the catalog is unavailable right now"})

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

    server = ThreadingHTTPServer((args.host, args.port), build_handler(latest, one))
    print(f"catalog service listening on {args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
