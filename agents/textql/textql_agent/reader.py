"""TextQL's Ana, read over her v2 API: one question in, her answer and charts out.

Copied from rytangle's router/app/tools/textql.py (TextQL 1.0.0 stays built
into the router for servers not on the catalog yet). Ana plans, queries and
writes behind ONE request; charts are read afterwards from the chat, where each
is an HTML page made by pyecharts whose settings sit in it as pure JSON.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx

from textql_agent.log import log, redact

TEXTQL_TIMEOUT_SECONDS = 300.0
CHART_DOWNLOAD_TIMEOUT_SECONDS = 30.0
MAX_CHART_HTML_BYTES = 2_000_000
MAX_CHARTS = 4

_OPTION_DECLARATION = re.compile(r"(?:var|let|const)\s+option_\w+\s*=\s*")


class TextQLError(RuntimeError):
    """TextQL did not return a usable answer."""


@dataclass(frozen=True)
class TextQLAnswer:
    text: str
    charts: tuple[dict, ...] = ()


def connector_ids(text: str | None) -> tuple[int, ...]:
    """TEXTQL_CONNECTOR_IDS, "70, 630": the numbers, and nothing else."""
    return tuple(int(part) for part in (text or "").split(",") if part.strip().isdigit())


def _dicts(value: object) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def chart_option(html: str) -> dict | None:
    match = _OPTION_DECLARATION.search(html)
    if not match:
        return None
    try:
        option, _ = json.JSONDecoder().raw_decode(html[match.end():])
    except ValueError:
        return None
    return option if isinstance(option, dict) else None


def chart_title(option: dict, fallback: str) -> str:
    title = option.get("title")
    if isinstance(title, list):
        title = title[0] if title else {}
    text = title.get("text") if isinstance(title, dict) else None
    return str(text).strip() if text and str(text).strip() else fallback


class TextQLReader:
    def __init__(
        self,
        api_key: str,
        client: httpx.Client,
        base_url: str = "https://app.textql.com",
        connector_ids: tuple[int, ...] = (),
    ) -> None:
        self.api_key = api_key
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.connector_ids = connector_ids

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}

    def ask_detailed(self, question: str) -> TextQLAnswer:
        # Ana's tools are off unless a request turns them on; web search stays off
        # so an answer comes from the data and not from the internet.
        tools: dict = {"sql_enabled": True, "python_enabled": True}
        if self.connector_ids:
            tools["connector_ids"] = list(self.connector_ids)
        try:
            response = self.client.post(
                f"{self.base_url}/v2/chats", json={"question": question, "tools": tools}, headers=self._headers()
            )
        except httpx.HTTPError as error:
            raise TextQLError(f"TextQL unreachable at {self.base_url}: {type(error).__name__}: {error}") from error
        if response.status_code != 200:
            raise TextQLError(f"TextQL returned {response.status_code}: {response.text[:200]}")
        try:
            payload = response.json()
        except ValueError as error:
            raise TextQLError(f"TextQL response was not JSON: {response.text[:200]}") from error
        answer = str(payload.get("response") or "").strip()
        if not answer:
            raise TextQLError("TextQL returned no answer")
        chat_id = str(payload.get("chat_id") or "")
        return TextQLAnswer(text=answer, charts=self.charts(chat_id) if chat_id else ())

    def charts(self, chat_id: str) -> tuple[dict, ...]:
        """Every chart Ana drew in this chat. Best effort: the answer is paid for."""
        try:
            response = self.client.get(f"{self.base_url}/v2/chats/{chat_id}", headers=self._headers())
            response.raise_for_status()
            chat = response.json()
        except (httpx.HTTPError, ValueError) as error:
            log("WARN", "textql", "chart skipped", reason="could not read chat", chat=chat_id, error=redact(error))
            return ()
        if not isinstance(chat, dict):
            log("WARN", "textql", "chart skipped", reason="chat is not an object", chat=chat_id)
            return ()
        assets = [
            asset
            for message in _dicts(chat.get("messages"))
            for asset in _dicts(message.get("assets"))
        ] or _dicts(chat.get("assets"))
        found: list[dict] = []
        for asset in assets:
            if len(found) >= MAX_CHARTS:
                break
            name = str(asset.get("name") or "")
            url = str(asset.get("url") or "")
            if asset.get("type") != "chart" or not name.endswith(".html") or not url:
                continue
            try:
                # No headers: the link is signed, and the key must not travel to it.
                page = self.client.get(url, timeout=CHART_DOWNLOAD_TIMEOUT_SECONDS)
                page.raise_for_status()
            except httpx.HTTPError as error:
                log("WARN", "textql", "chart skipped", chart=name, reason="download failed", error=redact(error))
                continue
            if len(page.content) > MAX_CHART_HTML_BYTES:
                log("WARN", "textql", "chart skipped", chart=name, reason="too large", bytes=len(page.content))
                continue
            option = chart_option(page.text)
            if option is None:
                log("WARN", "textql", "chart skipped", chart=name, reason="no chart settings found")
                continue
            found.append({"title": chart_title(option, name), "option": option})
        return tuple(found)
