"""The TextQL agent service: the contract every remote agent speaks.

  GET  /health                 200 {"ok": true} when it can work
  POST /ask {"question": "..."}  200 {"answer", "costUsd", "tokensIn", "tokensOut", "charts"}
  anything wrong               non-200 {"error": "..."}

Settings come from the environment the client's settings service starts it
with: TEXTQL_API_KEY, TEXTQL_CONNECTOR_IDS, TEXTQL_BASE_URL, PORT.

  python -m textql_agent.server
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from textql_agent.reader import TEXTQL_TIMEOUT_SECONDS, TextQLAnswer, TextQLError, TextQLReader, connector_ids


def build_handler(ask: Callable[[str], TextQLAnswer] | None) -> type[BaseHTTPRequestHandler]:
    """`ask` is None when TEXTQL_API_KEY is missing: the service then says so."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            pass

        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/health":
                self._send(404, {"error": f"no route {self.path}"})
            elif ask is None:
                self._send(503, {"ok": False, "error": "TEXTQL_API_KEY is not set"})
            else:
                self._send(200, {"ok": True})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/ask":
                self._send(404, {"error": f"no route {self.path}"})
                return
            if ask is None:
                self._send(503, {"error": "TEXTQL_API_KEY is not set"})
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
            except ValueError:
                self._send(400, {"error": "body must be JSON"})
                return
            question = body.get("question") if isinstance(body, dict) else None
            if not isinstance(question, str) or not question.strip():
                self._send(400, {"error": "question must be text"})
                return
            try:
                answer = ask(question)
            except TextQLError as error:
                self._send(502, {"error": str(error)})
                return
            except Exception as error:  # noqa: BLE001 - logged; the reply stays short
                print(f"[textql] /ask failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
                self._send(500, {"error": f"{type(error).__name__}: {error}"})
                return
            # TextQL bills in its own credits and its reply carries no price.
            self._send(200, {"answer": answer.text, "costUsd": 0.0, "tokensIn": 0, "tokensOut": 0,
                             "charts": list(answer.charts)})

    return Handler


def main() -> int:
    key = (os.environ.get("TEXTQL_API_KEY") or "").strip()
    ask = None
    if key and "replace" not in key.lower():
        reader = TextQLReader(
            key,
            client=httpx.Client(timeout=TEXTQL_TIMEOUT_SECONDS),
            base_url=os.environ.get("TEXTQL_BASE_URL") or "https://app.textql.com",
            connector_ids=connector_ids(os.environ.get("TEXTQL_CONNECTOR_IDS")),
        )
        ask = reader.ask_detailed
    port = int(os.environ.get("PORT") or 8080)
    print(f"textql agent listening on :{port} ({'ready' if ask else 'no TEXTQL_API_KEY'})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), build_handler(ask)).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
