# Copied from rytangle router/app/tools/zoho_cliq.py (Cliq 1.x stays built into
# the router until every server runs the catalog). Only the helper import differs.
"""Read-only Zoho Cliq access.

The second chat corpus, and the first agent here whose credential expires: Zoho
issues an access token good for an hour, minted from a long-lived refresh token.
The refreshing token provider follows the same `TokenProvider` shape `gdocs`
already uses for Google service accounts, so no new machinery was needed.

Everything below was verified by execution against a live workspace rather than
read from documentation, and four of the findings changed the design:

- **Two API versions are both required.** Search lives on v3
  (`GET /api/v3/messages/search`); reading messages lives on v2
  (`GET /api/v2/chats/{chat_id}/messages`). v3 documents no GET for channel
  messages. A search hit's `parent_chat_id` is exactly the `chat_id` the v2 read
  wants, so the two halves join without a lookup.
- **There is no thread-read endpoint.** Four plausible paths were tried live and
  all returned 404 or 400, so no `read_cliq_thread` tool exists. What compensates
  is that search reaches *inside* threads: hits come back with the thread's own
  title in `chat_title`, and the hit carries the message text, so thread content
  arrives through search rather than a second call. This is a different model from
  Slack, where search returns a pointer and the thread must then be opened.
- **`thread_message_id` arrives already percent-encoded** -- a `%20` standing for
  a space in the raw id. It is carried through untouched; re-encoding would
  corrupt it.
- **Mentions are self-contained.** Each message carries its own `mentions` map
  from id to a record holding `dname` (`@rytangle`), so rendering needs no
  directory call -- unlike Slack, which needs a workspace-wide user list.

Reads are also budgeted. Cliq allows 15 message reads per minute and then **locks
the account out for ten minutes**, where Slack merely throttles. An agent looping
over channels could trip that and break every later question, so the reader
refuses past its budget and says so instead.
"""

from __future__ import annotations

import re
import time as _time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from agent_kit.rows import no_rows, table
AUTH_SCHEME = "Zoho-oauthtoken"
SCOPES = ("ZohoCliq.Channels.READ", "ZohoCliq.Chats.READ", "ZohoCliq.Messages.READ")

# The documented limit is 15 reads/minute followed by a ten-minute lockout.
# Deliberately well under it: the cost of being wrong is asymmetric.
READ_BUDGET = 8
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_CHANNELS = 200
MAX_SEARCH_HITS = 20
TOKEN_SAFETY_MARGIN = 60.0

_MENTION = re.compile(r"\{@([A-Za-z0-9\-_]+)\}")

# Verified live: Zoho serialises a missing chat id as the literal STRING "null",
# not as JSON null, so a truthiness check on the raw value passes it through and
# the channel is offered to the agent as readable. Every read then fails.
ABSENT_VALUES = frozenset({"", "null", "none", "nil"})


def present(value: object) -> str:
    """A field's value, or empty when it is absent in any of Zoho's spellings."""
    text = str(value if value is not None else "").strip()
    return "" if text.lower() in ABSENT_VALUES else text


class CliqError(RuntimeError):
    """A Cliq call was refused, budgeted out, or returned unusable data."""


@dataclass(frozen=True)
class CliqChannel:
    name: str
    chat_id: str
    unique_name: str
    description: str

    @property
    def readable(self) -> bool:
        """Reads are addressed by chat_id, which can come back null."""
        return bool(self.chat_id)


@dataclass(frozen=True)
class CliqHit:
    channel_title: str
    chat_id: str
    thread_title: str
    thread_id: str
    sender: str
    when: str
    text: str
    permalink: str
    truncated: bool = False


def format_when(value: object) -> str:
    """Reads return epoch milliseconds; search hits return ISO strings."""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC).strftime("%Y-%m-%d %H:%M")
        except (OverflowError, OSError, ValueError):
            return str(value)
    text = str(value or "")
    return text[:16].replace("T", " ") if "T" in text else text


def resolve_mentions(text: str, mentions: dict | None) -> str:
    """Rewrite `{@id}` into the display name the message itself carries."""
    def replace(match: re.Match[str]) -> str:
        record = (mentions or {}).get(match.group(1)) or {}
        name = record.get("dname") or record.get("name") or match.group(1)
        return name if str(name).startswith("@") else f"@{name}"

    return _MENTION.sub(replace, text)


def message_text(message: dict) -> str:
    """Cliq wraps bodies as {"text": ...} rather than a bare string."""
    content = message.get("content")
    raw = content.get("text") if isinstance(content, dict) else content
    text = str(raw or "").strip()
    if not text:
        # A file share or system event would otherwise render as a blank line the
        # model reads as an empty channel.
        return f"(no text: {message.get('type') or 'unknown'} message)"
    return resolve_mentions(text, message.get("mentions"))


def render_messages(messages: Sequence[dict]) -> str:
    if not messages:
        return "no messages"
    lines: list[str] = []
    for message in messages:
        sender = message.get("sender")
        who = sender.get("name") if isinstance(sender, dict) else sender
        lines.append(f"[{who or 'unknown'}  {format_when(message.get('time'))}]")
        lines.append(message_text(message))
        lines.append("")
    return "\n".join(lines).strip()


class CliqReader:
    def __init__(
        self,
        dc: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        client: httpx.Client,
        read_budget: int = READ_BUDGET,
        clock: Callable[[], float] = _time.monotonic,
    ) -> None:
        self.dc = dc.strip().lstrip(".") or "com"
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.client = client
        self.read_budget = read_budget
        # Reads in the last minute, which is the window Cliq itself counts. Counted
        # for the life of the reader instead, and a reader outlives a run, Cliq
        # answered nothing after 8 reads until a restart.
        self._clock = clock
        self._reads: deque[float] = deque()
        self._token: str | None = None
        self._token_expiry = 0.0
        self._by_name: dict[str, str] | None = None

    @property
    def api_base(self) -> str:
        return f"https://cliq.zoho.{self.dc}/api"

    @property
    def accounts_base(self) -> str:
        return f"https://accounts.zoho.{self.dc}"

    # --------------------------------------------------------------------- auth

    def _access_token(self) -> str:
        if self._token and _time.monotonic() < self._token_expiry:
            return self._token

        response = self.client.post(
            f"{self.accounts_base}/oauth/v2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
            },
        )
        body = {}
        try:
            body = response.json()
        except ValueError:
            pass
        token = body.get("access_token")
        if not token:
            raise CliqError(
                f"could not refresh the access token: {body.get('error') or response.text[:200]}"
            )
        self._token = str(token)
        # Expire early so a call never starts with a token about to lapse.
        self._token_expiry = _time.monotonic() + max(
            float(body.get("expires_in") or 3600) - TOKEN_SAFETY_MARGIN, 0.0
        )
        return self._token

    def _get(self, path: str, params: dict[str, str]) -> dict:
        response = self.client.get(
            f"{self.api_base}{path}",
            params=params,
            headers={"authorization": f"{AUTH_SCHEME} {self._access_token()}"},
        )
        if response.status_code != 200:
            raise CliqError(f"GET {path} returned {response.status_code}: {response.text[:200]}")
        body = response.json()
        if isinstance(body, dict) and body.get("error"):
            error = body["error"]
            detail = error.get("message") if isinstance(error, dict) else error
            raise CliqError(f"GET {path} failed: {detail}")
        return body

    # ----------------------------------------------------------------- channels

    def chat_id_for(self, channel_name: str) -> str:
        """Resolve a channel name to the chat_id reads need, memoised for the run.

        A non-threaded search hit reports its channel by NAME and offers only a
        `channel_unique_id`, which the v2 read endpoint does not accept -- so
        without this the agent cannot follow up on a truncated hit.
        """
        if self._by_name is None:
            self._by_name = {c.name: c.chat_id for c in self.channels() if c.readable}
        return self._by_name.get(channel_name, "")

    def channels(self) -> list[CliqChannel]:
        found: list[CliqChannel] = []
        token = ""
        while True:
            params = {"limit": "50"}
            if token:
                params["next_token"] = token
            body = self._get("/v2/channels", params)
            for raw in body.get("channels") or []:
                found.append(
                    CliqChannel(
                        name=present(raw.get("name")),
                        chat_id=present(raw.get("chat_id")),
                        unique_name=present(raw.get("unique_name")),
                        description=present(raw.get("description")),
                    )
                )
            token = str(body.get("next_token") or "")
            if not body.get("has_more") or not token or len(found) >= MAX_CHANNELS:
                break
        # Sorted so the prompt is stable across runs.
        return sorted(found[:MAX_CHANNELS], key=lambda c: c.name)

    # ------------------------------------------------------------------ reading

    def messages(self, chat_id: str, limit: int = DEFAULT_LIMIT) -> list[dict]:
        now = self._clock()
        while self._reads and now - self._reads[0] >= 60:
            self._reads.popleft()
        if len(self._reads) >= self.read_budget:
            raise CliqError(
                f"read budget of {self.read_budget} per minute reached; Cliq locks the "
                "account out for ten minutes past its limit, so no further reads were made. "
                "Try again shortly."
            )
        self._reads.append(now)
        body = self._get(
            f"/v2/chats/{chat_id}/messages", {"limit": str(min(limit, MAX_LIMIT))}
        )
        return body.get("data") or []

    def search(self, query: str) -> list[CliqHit]:
        """Searching does not consume the read budget: it is a different endpoint
        without the lockout, and starving it would remove the agent's discovery."""
        body = self._get("/v3/messages/search", {"query": query})
        hits: list[CliqHit] = []
        for raw in (body.get("data") or [])[:MAX_SEARCH_HITS]:
            sender = raw.get("sender")
            details = ((raw.get("parent_chat_information") or {}).get("chat_details")) or {}
            text = message_text(raw)

            # Two shapes, verified live. A hit INSIDE a thread carries parent_* fields
            # and puts the thread's own title in chat_title. A hit outside one carries
            # no parent_* fields at all and puts the CHANNEL name in chat_title.
            # Reading chat_title as a thread title mislabels every plain message.
            parent_title = present(raw.get("parent_chat_title"))
            if parent_title:
                channel_title = parent_title
                thread_title = present(raw.get("chat_title"))
                chat_id = present(raw.get("parent_chat_id"))
            else:
                channel_title = present(raw.get("chat_title"))
                thread_title = ""
                chat_id = self.chat_id_for(channel_title)

            hits.append(
                CliqHit(
                    channel_title=channel_title,
                    chat_id=chat_id,
                    thread_title=thread_title,
                    # Left exactly as given: it is already percent-encoded.
                    thread_id=present(raw.get("thread_message_id")),
                    sender=str(sender.get("name") if isinstance(sender, dict) else sender or ""),
                    when=format_when(raw.get("time")),
                    text=text,
                    permalink=str(details.get("channel_permalink") or ""),
                    truncated=text.rstrip().endswith("..."),
                )
            )
        return hits


SEARCH_COLUMNS = ["channel", "thread", "sender", "when", "text",
                  "text_truncated", "permalink"]
MESSAGE_COLUMNS = ["sender", "when", "text"]
CHANNEL_COLUMNS = ["name", "chat_id", "readable", "description"]


def make_cliq_tools(reader: CliqReader) -> list:
    from langchain_core.tools import tool

    def reporting(work) -> tuple:
        """Return a Cliq failure as text with no rows: a raised error ends the
        agent's turn, and the model needs to read the reason and change course.

        Declaring the two-part return format makes the pair mandatory on every
        path out of a tool, including the ones that failed.
        """
        try:
            return work()
        except CliqError as error:
            return f"cliq error: {error}", no_rows()

    @tool(response_format="content_and_artifact")
    def search_cliq(query: str) -> tuple:
        """Find Zoho Cliq messages matching a word or phrase. Searches inside threads
        as well as channel timelines. Each hit names its channel and chat id. Zoho
        truncates long matches at about 100 characters and such hits are marked
        [truncated] — when the answer needs the rest, call read_cliq_chat with the
        chat id shown beside the hit."""

        def run() -> tuple:
            hits = reader.search(query)
            if not hits:
                return (
                    f"no matches for: {query!r} across this account's Cliq chats",
                    no_rows(),
                )
            lines = []
            for hit in hits:
                where = hit.channel_title or "(unknown channel)"
                if hit.thread_title and hit.thread_title != hit.channel_title:
                    where += f" › thread: {hit.thread_title}"
                text = hit.text.replace("\n", " ")[:400]
                if hit.truncated:
                    # Zoho cuts search text at ~100 characters. Saying so, with the
                    # chat id, is what lets the agent recover the full message
                    # instead of reporting that it is unavailable.
                    text += f"  [truncated by Zoho — read_cliq_chat('{hit.chat_id}') for the full message]"
                # In the TEXT as well as the rows: rows go to ToolMessage.artifact,
                # which no prompt reaches, so a link left only there is invisible
                # to the agent that has to cite it.
                lines.append(
                    f"{where}\t{hit.chat_id}\t[{hit.sender}  {hit.when}]\t{text}"
                    + (f"\t{hit.permalink}" if hit.permalink else "")
                )
            rows = [
                [hit.channel_title, hit.thread_title, hit.sender, hit.when,
                 hit.text, hit.truncated, hit.permalink]
                for hit in hits
            ]
            return "\n".join(lines), table(
                SEARCH_COLUMNS, rows, truncated=len(hits) >= MAX_SEARCH_HITS)

        return reporting(run)

    @tool(response_format="content_and_artifact")
    def read_cliq_chat(chat_id: str, limit: int = DEFAULT_LIMIT) -> tuple:
        """Read recent messages from one Cliq channel by its chat id, as shown by
        list_cliq_channels or search_cliq. This returns the channel's main timeline;
        messages inside threads are reachable only through search_cliq. Use sparingly:
        reads are strictly limited."""

        def run() -> tuple:
            messages = reader.messages(chat_id, limit)
            if not messages:
                return f"no messages in chat {chat_id}", no_rows()
            # Read the same way render_messages reads them: these arrive as raw
            # dicts from Zoho, not as a typed message.
            rows = [
                [
                    (m.get("sender") or {}).get("name")
                    if isinstance(m.get("sender"), dict)
                    else (m.get("sender") or "unknown"),
                    format_when(m.get("time")),
                    message_text(m),
                ]
                for m in messages
            ]
            return render_messages(messages), table(
                MESSAGE_COLUMNS, rows,
                truncated=len(messages) >= min(limit, MAX_LIMIT))

        return reporting(run)

    @tool(response_format="content_and_artifact")
    def list_cliq_channels() -> tuple:
        """List the Zoho Cliq channels this account belongs to, as
        'name<tab>chat_id' lines. The chat id is what read_cliq_chat needs."""

        def run() -> tuple:
            channels = reader.channels()
            if not channels:
                return "this account belongs to no Cliq channels", no_rows()
            lines = [
                f"{c.name}\t{c.chat_id}" + (f"\t{c.description}" if c.description else "")
                for c in channels
                if c.readable
            ]
            blocked = [c.name for c in channels if not c.readable]
            if blocked:
                # Named rather than hidden: the channel exists, it just has no
                # chat_id for the read endpoint to address.
                lines.append(f"({len(blocked)} cannot be read, no chat id: {', '.join(blocked)})")
            rows = [
                [c.name, c.chat_id, c.readable, c.description] for c in channels
            ]
            return "\n".join(lines), table(
                CHANNEL_COLUMNS, rows, truncated=len(channels) >= MAX_CHANNELS)

        return reporting(run)

    return [search_cliq, read_cliq_chat, list_cliq_channels]
