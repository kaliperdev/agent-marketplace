# Copied from rytangle router/app/tools/slack.py (Slack 1.x stays built into
# the router until every server runs the catalog). Only the helper import differs.
"""Read-only Slack access.

Two tokens, because Slack splits the capability across them:

- the **bot** token reads channels it has been invited to
  (`users.conversations`, `conversations.history`, `conversations.replies`,
  `users.list`);
- `search.messages` **cannot be called with a bot token at all**, so a **user**
  token with `search:read` provides discovery.

Without the user token this agent would have no search, only enumeration --
the same weakness that made the pinned-document-id Drive design worse than
the one that replaced it.

Three things about this API differ from every other tool module here:

- **Failure arrives as HTTP 200.** Slack signals errors with `{"ok": false,
  "error": "not_in_channel"}` and a 200 status. Checking the status alone --
  correct for GitHub, Drive and Jira -- would read every Slack error as a
  successful empty response, so `_call` checks `ok` first.
- **Membership is the whole access boundary, and only one endpoint asks about
  it directly.** `conversations.list` enumerates the workspace and reports
  membership as a field, which sounds equivalent and is not: measured against the
  live workspace it returned public channels the bot was not in and none of the
  private ones it was, so filtering on `is_member` yielded nothing while the bot
  was answering questions in one of them. `users.conversations` asks which
  conversations the account belongs to, and every row it returns is one.
- **Nothing is cached to disk, deliberately.** Slack is the one corpus here that
  is append-only and has no revision stamp, and the value of a cache depends
  entirely on which rate-limit tier the app lands in -- unmeasured at the time of
  writing. Every read therefore goes live, so a question about what changed today
  is always correct. The one exception is an in-run memo for `users.list`, which
  is not a cache in the staleness sense: it only stops the same call repeating
  inside a single question. Adding a disk cache later is a constructor argument.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from agent_kit.rows import no_rows, table

SLACK_API = "https://slack.com/api"

# The restricted (non-Marketplace) tier caps a page at 15 objects, so a larger
# request would silently return fewer than asked. Clamped rather than trusted.
MAX_PAGE = 15
DEFAULT_HISTORY = 15
MAX_SEARCH_HITS = 20
MAX_THREAD_MESSAGES = 60
MAX_CHANNELS = 200

BOT_SCOPES = ("channels:history", "groups:history", "channels:read", "users:read")
USER_SCOPES = ("search:read",)

_USER_REF = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>")
_CHANNEL_REF = re.compile(r"<#(C[A-Z0-9]+)(?:\|([^>]*))?>")
_BROADCAST_REF = re.compile(r"<!(here|channel|everyone)(?:\|[^>]*)?>")
_LINK_REF = re.compile(r"<(https?://[^|>]+)(?:\|([^>]*))?>")


class SlackError(RuntimeError):
    """A Slack call was refused, rate-limited, or returned unusable data."""


@dataclass(frozen=True)
class Channel:
    id: str
    name: str
    is_member: bool
    is_private: bool
    topic: str


@dataclass(frozen=True)
class SearchHit:
    channel_id: str
    channel_name: str
    user: str
    ts: str
    thread_ts: str
    text: str
    permalink: str


def format_ts(ts: str) -> str:
    """Slack timestamps are epoch seconds with a microsecond suffix."""
    try:
        moment = datetime.fromtimestamp(float(ts), tz=UTC)
    except (TypeError, ValueError):
        return ts
    return moment.strftime("%Y-%m-%d %H:%M")


def resolve_refs(text: str, names: dict[str, str]) -> str:
    """Rewrite Slack's markup into something a model can quote.

    Raw message text carries `<@U04AB12CD>` and `<#C123|general>`. Left alone,
    every answer the agent produces cites opaque ids instead of people.
    """
    text = _USER_REF.sub(lambda m: f"@{names.get(m.group(1), m.group(1))}", text)
    text = _CHANNEL_REF.sub(lambda m: f"#{m.group(2) or m.group(1)}", text)
    text = _BROADCAST_REF.sub(lambda m: f"@{m.group(1)}", text)
    text = _LINK_REF.sub(
        lambda m: m.group(1) if not m.group(2) else f"{m.group(2)} ({m.group(1)})", text
    )
    return text


def render_messages(messages: Sequence[dict], names: dict[str, str]) -> str:
    """One message per block: who, when, what, and whether a thread hangs off it."""
    if not messages:
        return "no messages"

    lines: list[str] = []
    for message in messages:
        author = names.get(message.get("user", ""), message.get("user") or "unknown")
        when = format_ts(str(message.get("ts", "")))
        body = resolve_refs(str(message.get("text") or ""), names).strip()
        if not body:
            # A bare file share or a join event would otherwise render as a blank
            # line the model reads as an empty channel.
            body = f"(no text: {message.get('subtype') or message.get('type') or 'unknown'} event)"
        lines.append(f"[{author}  {when}]")
        lines.append(body)
        replies = int(message.get("reply_count") or 0)
        if replies:
            # The ts doubles as the thread_ts needed to open it, so the agent can
            # act on this line without another lookup.
            lines.append(f"  -> {replies} replies in thread {message.get('ts')}")
        lines.append("")
    return "\n".join(lines).strip()


class SlackReader:
    def __init__(
        self,
        bot_token: str,
        user_token: str,
        client: httpx.Client,
        history_limit: int = DEFAULT_HISTORY,
    ) -> None:
        self.bot_token = bot_token
        self.user_token = user_token
        self.client = client
        self.history_limit = history_limit
        self._names: dict[str, str] | None = None
        self._readable: set[str] | None = None

    # ------------------------------------------------------------------ calling

    def _call(self, method: str, token: str, params: dict[str, str]) -> dict:
        response = self.client.get(
            f"{SLACK_API}/{method}",
            params=params,
            headers={"authorization": f"Bearer {token}"},
        )
        if response.status_code == 429:
            wait = response.headers.get("retry-after", "unknown")
            raise SlackError(f"{method} rate-limited; retry after {wait}s")
        if response.status_code != 200:
            raise SlackError(f"{method} returned {response.status_code}: {response.text[:200]}")

        body = response.json()
        if not body.get("ok"):
            # Slack's own convention: failure with a 200 status.
            raise SlackError(f"{method} failed: {body.get('error', 'unknown error')}")
        return body

    def _paged(
        self,
        method: str,
        token: str,
        params: dict[str, str],
        key: str,
        cap: int,
    ) -> list[dict]:
        items: list[dict] = []
        cursor = ""
        while True:
            page = dict(params)
            if cursor:
                page["cursor"] = cursor
            body = self._call(method, token, page)
            items.extend(body.get(key) or [])
            cursor = (body.get("response_metadata") or {}).get("next_cursor") or ""
            if not cursor or len(items) >= cap:
                break
        return items[:cap]

    # ------------------------------------------------------------------- people

    def _load_names(self) -> dict[str, str]:
        if self._names is None:
            members = self._paged(
                "users.list", self.bot_token, {"limit": "200"}, "members", 2000
            )
            self._names = {
                str(m.get("id")): str(
                    m.get("real_name")
                    or (m.get("profile") or {}).get("display_name")
                    or m.get("name")
                    or m.get("id")
                )
                for m in members
                if m.get("id")
            }
        return self._names

    def readable_channel_ids(self) -> set[str]:
        """Channel ids the bot can actually read, memoised for the run.

        The user token searches every channel the PERSON can see, which is wider
        than the bot's membership: verified live that a hit can land in a channel
        where reading returns channel_not_found.
        """
        if self._readable is None:
            self._readable = {c.id for c in self.channels()}
        return self._readable

    def names(self) -> dict[str, str]:
        """Every known user id mapped to a readable name, fetched once per run."""
        return self._load_names()

    def user_name(self, user_id: str) -> str:
        """An unknown id returns itself: a blank author attributes a quote to nobody."""
        return self._load_names().get(user_id, user_id)

    # ----------------------------------------------------------------- channels

    def channels(self) -> list[Channel]:
        """The channels this bot belongs to -- the only ones it can read.

        users.conversations, NOT conversations.list. The latter enumerates the
        workspace and reports membership as a field on each row; measured against
        the live workspace, it returned five public channels the bot was not in
        and none of the eleven private ones it was, so filtering on `is_member`
        produced an empty list while the bot was actively answering questions in
        one of those private channels.

        This endpoint asks which conversations the account belongs to, which is
        the question the tool has always meant.
        """
        raw = self._paged(
            "users.conversations",
            self.bot_token,
            {
                "types": "public_channel,private_channel",
                "exclude_archived": "true",
                "limit": "200",
            },
            "channels",
            MAX_CHANNELS,
        )
        found = [
            Channel(
                id=str(c.get("id", "")),
                name=str(c.get("name", "")),
                # True by construction: this endpoint returns memberships only and
                # the payload carries no is_member field. Kept on the record
                # because callers read it as "can I read this".
                is_member=True,
                is_private=bool(c.get("is_private")),
                topic=str((c.get("topic") or {}).get("value") or ""),
            )
            for c in raw
        ]
        # Sorted because Slack's order is not guaranteed stable, and an unstable
        # tool result makes an unstable prompt.
        return sorted(found, key=lambda c: c.name)

    # ------------------------------------------------------------------ reading

    def channel_history(self, channel: str, limit: int | None = None) -> list[dict]:
        asked = self.history_limit if limit is None else limit
        return self._call(
            "conversations.history",
            self.bot_token,
            {"channel": channel, "limit": str(min(asked, MAX_PAGE))},
        ).get("messages") or []

    def _replies(self, channel: str, ts: str) -> list[dict]:
        return self._paged(
            "conversations.replies",
            self.bot_token,
            {"channel": channel, "ts": ts, "limit": str(MAX_PAGE)},
            "messages",
            MAX_THREAD_MESSAGES,
        )

    def thread(self, channel: str, thread_ts: str) -> list[dict]:
        """The whole thread, parent first, even when handed a reply's timestamp.

        Verified live: `conversations.replies` given a REPLY's ts returns that one
        message alone, and `search.messages` hits never carry `thread_ts` -- the key
        is absent from the response schema. So a search hit that happens to be a
        reply would otherwise open as a single orphaned message, losing the thread
        that was the only reason to open it. The message itself does carry
        `thread_ts`, so one extra call climbs to the parent.
        """
        messages = self._replies(channel, thread_ts)
        if len(messages) == 1:
            parent = str(messages[0].get("thread_ts") or "")
            if parent and parent != thread_ts:
                return self._replies(channel, parent)
        return messages

    def search(self, query: str) -> list[SearchHit]:
        body = self._call(
            "search.messages",
            self.user_token,
            {"query": query, "count": str(MAX_SEARCH_HITS), "sort": "score"},
        )
        matches = ((body.get("messages") or {}).get("matches")) or []
        hits: list[SearchHit] = []
        for match in matches:
            channel = match.get("channel") or {}
            ts = str(match.get("ts", ""))
            hits.append(
                SearchHit(
                    channel_id=str(channel.get("id", "")),
                    channel_name=str(channel.get("name", "")),
                    user=str(match.get("user") or match.get("username") or ""),
                    ts=ts,
                    # A hit outside a thread is its own thread root, so a hit is
                    # always openable with one call.
                    thread_ts=str(match.get("thread_ts") or ts),
                    text=str(match.get("text") or ""),
                    permalink=str(match.get("permalink") or ""),
                )
            )
        return hits


def message_rows(messages: Sequence[dict], names: dict[str, str]) -> list[list]:
    """Messages as rows: who, when, what, and how many replies hang off it.

    Carries the FULL text, not the 180-character clip the search listing shows.
    That clip exists to keep a prompt small, and this payload never enters one.
    """
    rows = []
    for message in messages:
        author = names.get(message.get("user", ""), message.get("user") or "unknown")
        rows.append([
            author,
            format_ts(str(message.get("ts", ""))),
            resolve_refs(str(message.get("text") or ""), names).strip(),
            int(message.get("reply_count") or 0),
        ])
    return rows


MESSAGE_COLUMNS = ["author", "when", "text", "replies"]
SEARCH_COLUMNS = ["channel", "author", "when", "text", "permalink"]
CHANNEL_COLUMNS = ["name", "id", "visibility", "topic"]


def make_slack_tools(reader: SlackReader) -> list:
    from langchain_core.tools import tool

    def reporting(work: Callable[[], tuple]) -> tuple:
        """Return a Slack failure as text, with no rows.

        A raised error would end the agent's turn; the model needs to read the
        reason -- `not_in_channel`, `ratelimited` -- and try something else.

        Declaring the two-part return format makes the pair mandatory on every
        path out of a tool, including the ones that failed.
        """
        try:
            return work()
        except SlackError as error:
            return f"slack error: {error}", no_rows()

    @tool(response_format="content_and_artifact")
    def search_slack(query: str) -> tuple:
        """Find Slack messages matching a word or phrase, across every channel you
        can see. This is the fastest way to locate a discussion. Each hit reports
        the channel and a thread timestamp to pass to read_slack_thread."""

        def run() -> tuple:
            hits = reader.search(query)
            if not hits:
                return (
                    f"no matches for: {query!r} across the channels this bot can see",
                    no_rows(),
                )
            names = reader.names()
            readable = reader.readable_channel_ids()
            lines = []
            for hit in hits:
                author = names.get(hit.user, hit.user or "unknown")
                text = resolve_refs(hit.text, names).replace("\n", " ")[:180]
                # The matched text is evidence whether or not the thread can be
                # opened, so an unreadable hit is marked, never dropped.
                where = (
                    f"thread {hit.thread_ts}"
                    if hit.channel_id in readable
                    else "cannot open: bot is not in this channel"
                )
                # The permalink goes in the TEXT as well as the rows. Rows reach
                # ToolMessage.artifact, which rows.py says "no prompt ever reaches",
                # so an agent asked to cite a link could not see one.
                lines.append(
                    f"#{hit.channel_name}\t{hit.channel_id}\t{where}\t"
                    f"[{author}  {format_ts(hit.ts)}]\t{text}"
                    + (f"\t{hit.permalink}" if hit.permalink else "")
                )
            rows = [
                [f"#{hit.channel_name}", names.get(hit.user, hit.user or "unknown"),
                 format_ts(hit.ts), resolve_refs(hit.text, names).strip(),
                 hit.permalink]
                for hit in hits
            ]
            return "\n".join(lines), table(
                SEARCH_COLUMNS, rows, truncated=len(hits) >= MAX_SEARCH_HITS)

        return reporting(run)

    @tool(response_format="content_and_artifact")
    def read_slack_thread(channel: str, thread_ts: str) -> tuple:
        """Read a whole thread: its parent message and every reply, in order. Pass the
        channel id and the thread timestamp shown by search_slack or read_slack_channel.
        Threads are where decisions get argued out."""

        def run() -> tuple:
            messages = reader.thread(channel, thread_ts)
            names = reader.names()
            return render_messages(messages, names), table(
                MESSAGE_COLUMNS, message_rows(messages, names), truncated=False)

        return reporting(run)

    @tool(response_format="content_and_artifact")
    def read_slack_channel(channel: str, limit: int = DEFAULT_HISTORY) -> tuple:
        """Read the most recent messages in one channel by its id. Messages that have
        replies are marked with the thread timestamp needed to open them, so follow up
        with read_slack_thread rather than assuming the parent says everything."""

        def run() -> tuple:
            messages = reader.channel_history(channel, limit)
            if not messages:
                # Names the channel. A bare "no messages" reads as "that channel
                # is empty", when a wrong or unreadable id looks exactly the same
                # from here.
                return f"no messages in channel {channel}", no_rows()
            names = reader.names()
            return render_messages(messages, names), table(
                MESSAGE_COLUMNS, message_rows(messages, names),
                # We asked for `limit` and got exactly that many, so there is
                # almost certainly more behind it.
                truncated=len(messages) >= limit)

        return reporting(run)

    @tool(response_format="content_and_artifact")
    def list_slack_channels() -> tuple:
        """List the channels this bot has been invited to -- the only channels it can
        read. Returns 'name<tab>id' lines with topics."""

        def run() -> tuple:
            channels = reader.channels()
            if not channels:
                return "the bot has not been invited to any channel", no_rows()
            text = "\n".join(
                f"#{c.name}\t{c.id}\t{'private' if c.is_private else 'public'}"
                + (f"\t{c.topic}" if c.topic else "")
                for c in channels
            )
            rows = [
                [c.name, c.id, "private" if c.is_private else "public", c.topic]
                for c in channels
            ]
            return text, table(CHANNEL_COLUMNS, rows,
                               truncated=len(channels) >= MAX_CHANNELS)

        return reporting(run)

    return [search_slack, read_slack_thread, read_slack_channel, list_slack_channels]
