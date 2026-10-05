# Copied from rytangle router/app/tools/jira.py (Jira 1.x stays built into the
# router until every server runs the catalog). Only the two helper imports differ.
"""Read-only Jira Cloud access.

Hand-rolled thin tools over the REST API, for the same reason the GitHub tools
are: the packaged toolkits target write operations. Nothing here writes.

Three mechanics, all verified by execution against a live Jira Cloud instance
rather than read from a changelog:

- `GET /rest/api/3/search` is **gone** -- it answers 410 with "The requested API
  has been removed". Search must POST to `/rest/api/3/search/jql`.
- That endpoint **refuses unbounded JQL** with 400 ("Unbounded JQL queries are
  not allowed here"). Every query this module sends therefore carries a
  restriction; a blank search means recent issues, not everything.
- Its pagination is token-based (`nextPageToken` / `isLast`) and reports **no
  total**, so a match count cannot be shown -- only whether more remain.

Descriptions and comments come back as ADF (Atlassian Document Format), a nested
node tree rather than text, so `flatten_adf` is the counterpart of the Google
Docs flattener.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from agent_kit.cache import DiskCache
from agent_kit.rows import no_rows, table

API_PATH = "/rest/api/3"
ATLASSIAN_HOST_SUFFIX = ".atlassian.net"

# A restriction the endpoint accepts, used when the caller gives no search term.
RECENT_WINDOW_JQL = "updated >= -30d"

SEARCH_FIELDS = ("summary", "status", "issuetype", "assignee", "updated", "project")
ISSUE_FIELDS = (
    "summary",
    "status",
    "issuetype",
    "assignee",
    "reporter",
    "created",
    "updated",
    "project",
    "priority",
    "description",
)

PAGE_SIZE = 50
MAX_ITEMS = 100
MAX_COMMENTS = 20

# ADF inline nodes whose readable text lives in attrs rather than a text node.
ATTR_TEXT_KEYS = ("text", "url", "shortName")


class JiraError(RuntimeError):
    """A Jira request was refused or returned unusable data."""


def normalise_domain(raw: str) -> str:
    """Accept `kaliper`, `kaliper.atlassian.net`, or a pasted URL."""
    domain = raw.strip().removeprefix("https://").removeprefix("http://").rstrip("/")
    domain = domain.split("/")[0]
    if "." not in domain:
        domain += ATLASSIAN_HOST_SUFFIX
    return domain


def basic_auth_header(email: str, token: str) -> str:
    """Jira Cloud takes the API token as the password half of Basic auth."""
    encoded = base64.b64encode(f"{email}:{token}".encode()).decode()
    return f"Basic {encoded}"


def escape_jql_value(value: str) -> str:
    """Escape a string for a JQL literal.

    The model chooses the search term, so an unescaped quote would end the
    literal early and the remainder would be parsed as JQL syntax.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def flatten_adf(node: object) -> str:
    """ADF node tree to plain text.

    Unknown node types recurse into their content rather than being dropped: ADF
    gains node types over time, and swallowing one would make a ticket read as
    empty when it is not.
    """
    if not isinstance(node, dict):
        return ""
    return _flatten_node(node).strip()


def _flatten_children(node: dict, separator: str = "") -> str:
    content = node.get("content")
    if not isinstance(content, list):
        return ""
    return separator.join(_flatten_node(child) for child in content if isinstance(child, dict))


def _flatten_node(node: dict) -> str:
    kind = node.get("type")

    if kind == "text":
        return str(node.get("text", ""))
    if kind == "hardBreak":
        return "\n"
    if kind == "rule":
        return "\n---\n"

    if kind in ("doc", "blockquote", "panel"):
        return _flatten_children(node, "\n\n")
    if kind in ("paragraph", "heading"):
        return _flatten_children(node)
    if kind == "codeBlock":
        return _flatten_children(node)
    if kind in ("bulletList", "orderedList"):
        return _flatten_children(node, "\n")
    if kind == "listItem":
        return f"- {_flatten_children(node).strip()}"
    if kind == "table":
        return _flatten_children(node, "\n")
    if kind == "tableRow":
        return "\t".join(
            _flatten_node(cell).strip()
            for cell in node.get("content", [])
            if isinstance(cell, dict)
        )
    if kind in ("tableCell", "tableHeader"):
        return _flatten_children(node, " ")

    attrs = node.get("attrs")
    if isinstance(attrs, dict) and not node.get("content"):
        for key in ATTR_TEXT_KEYS:
            value = attrs.get(key)
            if value:
                return str(value)

    # Unknown node: keep whatever text is inside it.
    return _flatten_children(node, "\n")


@dataclass(frozen=True)
class IssueSummary:
    key: str
    summary: str
    status: str
    issue_type: str
    assignee: str
    updated: str
    project: str


@dataclass(frozen=True)
class IssueListing:
    items: list[IssueSummary]
    truncated: bool


def _name(field: object, default: str) -> str:
    if isinstance(field, dict):
        return str(field.get("name") or field.get("displayName") or field.get("key") or default)
    return default


def _summarise(raw: dict) -> IssueSummary:
    fields = raw.get("fields") or {}
    return IssueSummary(
        key=str(raw.get("key", "")),
        summary=str(fields.get("summary") or ""),
        status=_name(fields.get("status"), "unknown"),
        issue_type=_name(fields.get("issuetype"), "unknown"),
        assignee=_name(fields.get("assignee"), "unassigned"),
        updated=str(fields.get("updated") or ""),
        project=_name(fields.get("project"), ""),
    )


def render_issue(issue: dict, comments: dict) -> str:
    """One ticket as plain text: header fields, description, then comments.

    Comments are included because that is where the decision usually lives; a
    ticket rendered without them reads complete while omitting the answer.
    """
    fields = issue.get("fields") or {}
    summary = _summarise(issue)
    lines = [
        f"{summary.key}  {summary.summary}",
        f"project: {summary.project}   type: {summary.issue_type}   "
        f"status: {summary.status}   assignee: {summary.assignee}",
        f"reporter: {_name(fields.get('reporter'), 'unknown')}   "
        f"created: {str(fields.get('created') or '')[:10]}   "
        f"updated: {str(fields.get('updated') or '')[:10]}",
        "",
    ]

    body = flatten_adf(fields.get("description"))
    lines.append(body if body else "(no description)")

    entries = [c for c in (comments.get("comments") or []) if isinstance(c, dict)]
    if entries:
        lines.append("")
        lines.append(f"--- comments ({len(entries)}) ---")
        for comment in entries[:MAX_COMMENTS]:
            author = _name(comment.get("author"), "unknown")
            when = str(comment.get("created") or "")[:10]
            lines.append(f"[{author}, {when}]")
            lines.append(flatten_adf(comment.get("body")))
    return "\n".join(lines)


class JiraReader:
    def __init__(
        self,
        domain: str,
        email: str,
        token: str,
        client: httpx.Client,
        cache: DiskCache,
        max_items: int = MAX_ITEMS,
    ) -> None:
        # Kept apart from `base` so a browse URL can be built from it. The API
        # path is not where a person opens an issue.
        self.host = f"https://{normalise_domain(domain)}"
        self.base = f"{self.host}{API_PATH}"
        self.client = client
        self.cache = cache
        self.max_items = max_items
        self._headers = {
            "authorization": basic_auth_header(email, token),
            "accept": "application/json",
            "content-type": "application/json",
        }

    def _get(self, path: str, params: dict[str, str]) -> str:
        response = self.client.get(f"{self.base}{path}", params=params, headers=self._headers)
        if response.status_code != 200:
            raise JiraError(f"GET {path} returned {response.status_code}: {response.text[:200]}")
        return response.text

    def _cached_get(self, path: str, params: dict[str, str], key: str) -> dict:
        cached = self.cache.get(key)
        if cached is None:
            cached = self._get(path, params)
            self.cache.put(key, cached)
        return json.loads(cached)

    def build_jql(
        self,
        query: str = "",
        project: str = "",
        status: str = "",
        assignee_ids: Sequence[str] = (),
        reporter_ids: Sequence[str] = (),
        updated_since: str = "",
        created_since: str = "",
    ) -> str:
        """Always bounded: the endpoint rejects an unrestricted query with 400.

        `status` is a real JQL clause rather than a word folded into the text
        search. Without it there was no way to express "the open ones" at all:
        "open" went into `query` and became `text ~ "open"`, a full-text search
        for that word in the ticket body, which has nothing to do with status.
        Measured on a live run -- asked to summarise open tickets, the agent
        searched the word "open", found 2 of the 14 that were actually To Do,
        and reported two. It was being blamed for a sentence it had no grammar
        for.
        """
        clauses: list[str] = []
        if query.strip():
            clauses.append(f'text ~ "{escape_jql_value(query.strip())}"')
        if project.strip():
            # QUOTED. This Jira has a project whose key is MAX, and MAX is a
            # reserved JQL word: unquoted it is a syntax error, so the whole
            # agent reported itself unreachable over a project that exists and
            # is perfectly readable.
            clauses.append(f'project = "{escape_jql_value(project.strip())}"')
        if status.strip():
            # Comma-separated, because "open" in most workflows means more than
            # one name -- To Do AND In Progress AND Internal Review.
            names = ",".join(
                f'"{escape_jql_value(name.strip())}"'
                for name in status.split(",")
                if name.strip()
            )
            clauses.append(f"status IN ({names})")
        # Account IDs, never names: see find_people. Who HOLDS a ticket and who
        # MADE it are different questions, asked in the same breath often enough
        # that conflating them produces a wrong answer with an honest-sounding
        # caveat attached.
        if assignee_ids:
            ids = ",".join(f'"{escape_jql_value(str(one))}"' for one in assignee_ids)
            clauses.append(f"assignee IN ({ids})")
        if reporter_ids:
            ids = ",".join(f'"{escape_jql_value(str(one))}"' for one in reporter_ids)
            clauses.append(f"reporter IN ({ids})")
        # The grammar for "last week". Without these there is no way to express a
        # window at all, so an agent asked for "open tickets from last week"
        # either fetches everything and filters the dates by eye -- measured: 86
        # rows read to report 8 -- or asks which week was meant and searches
        # nothing. `updated` is when a ticket last MOVED, `created` when it was
        # MADE; "created last week" means created_since.
        if updated_since.strip():
            clauses.append(f'updated >= "{escape_jql_value(updated_since.strip())}"')
        if created_since.strip():
            clauses.append(f'created >= "{escape_jql_value(created_since.strip())}"')
        if not clauses:
            clauses.append(RECENT_WINDOW_JQL)
        return " AND ".join(clauses) + " ORDER BY updated DESC"

    def statuses(self) -> list[str]:
        """The status names this Jira actually uses.

        Asked for only when a search was refused, so the happy path pays nothing
        for it.
        """
        body = json.loads(self._get("/status", {}))
        names: list[str] = []
        for entry in body if isinstance(body, list) else []:
            name = str((entry or {}).get("name") or "")
            if name and name not in names:
                names.append(name)
        return names

    def find_people(self, name: str) -> list[tuple[str, str]]:
        """(account id, display name) for people whose name matches.

        Jira Cloud will not match an assignee by display name at all -- JQL
        wants an account id, and a name is a 400 rather than an empty result.
        So a person has to be looked up before they can be searched for, and a
        name matching nobody must say so: an empty result reads as "that person
        has no tickets", which is a different and wrong answer.
        """
        body = json.loads(self._get("/user/search", {"query": name.strip()}))
        return [
            (str(person.get("accountId") or ""), str(person.get("displayName") or ""))
            for person in (body if isinstance(body, list) else [])
            if isinstance(person, dict) and person.get("accountId")
        ]

    def search(
        self,
        query: str = "",
        project: str = "",
        status: str = "",
        assignee_ids: Sequence[str] = (),
        reporter_ids: Sequence[str] = (),
        updated_since: str = "",
        created_since: str = "",
    ) -> IssueListing:
        jql = self.build_jql(query, project, status, assignee_ids, reporter_ids,
                             updated_since, created_since)
        items: list[IssueSummary] = []
        truncated = False
        page_token: str | None = None

        while True:
            payload: dict[str, object] = {
                "jql": jql,
                "maxResults": PAGE_SIZE,
                "fields": list(SEARCH_FIELDS),
            }
            if page_token:
                payload["nextPageToken"] = page_token

            response = self.client.post(
                f"{self.base}/search/jql", json=payload, headers=self._headers
            )
            if response.status_code != 200:
                raise JiraError(
                    f"search returned {response.status_code}: {response.text[:200]}"
                )
            body = response.json()
            items.extend(_summarise(raw) for raw in body.get("issues", []) if isinstance(raw, dict))

            page_token = body.get("nextPageToken")
            if len(items) >= self.max_items:
                truncated = len(items) > self.max_items or bool(page_token)
                del items[self.max_items :]
                break
            # The response reports isLast rather than a total.
            if body.get("isLast") or not page_token:
                break

        return IssueListing(items=items, truncated=truncated)

    def updated(self, key: str) -> str:
        """The ticket's revision stamp, used as its content cache key."""
        body = json.loads(self._get(f"/issue/{key}", {"fields": "updated"}))
        stamp = (body.get("fields") or {}).get("updated")
        if not stamp:
            raise JiraError(f"issue {key} returned no updated timestamp")
        return str(stamp)

    def read_issue(self, key: str, version: str) -> str:
        issue = self._cached_get(
            f"/issue/{key}", {"fields": ",".join(ISSUE_FIELDS)}, f"jira:issue:{key}#{version}"
        )
        comments = self._cached_get(
            f"/issue/{key}/comment",
            {"maxResults": str(MAX_COMMENTS), "orderBy": "created"},
            f"jira:comments:{key}#{version}",
        )
        return render_issue(issue, comments)

    def projects(self) -> list[tuple[str, str]]:
        body = json.loads(self._get("/project/search", {"maxResults": "100"}))
        return [
            (str(p.get("key", "")), str(p.get("name", "")))
            for p in body.get("values", [])
            if isinstance(p, dict)
        ]


# Jira's wording when a JQL field is given a value it does not have.
_MISSING_VALUE = re.compile(r"does not exist for the field ['\"]?(\w+)", re.IGNORECASE)


def unknown_statuses(reader: "JiraReader", status: str) -> list[str]:
    """The names in `status` that this Jira does not have.

    Case-insensitive: this instance carries both "Blocked" and "BLOCKED", and
    four spellings of Client Review, so exact matching would call a real status
    imaginary.
    """
    known = {name.lower() for name in reader.statuses()}
    return [
        name.strip()
        for name in status.split(",")
        if name.strip() and name.strip().lower() not in known
    ]


def unknown_value(message: str) -> str:
    """Which field was given a value this Jira does not have, if any."""
    found = _MISSING_VALUE.search(message)
    return found.group(1).lower() if found else ""


# Field names a model reaches for when it assumes this tool speaks JQL. Paired
# with an operator, they are the signature of a filter attempt rather than a
# phrase anyone would search for as text.
_JIRA_FIELDS = (
    "status|statuscategory|assignee|reporter|creator|project|priority|type|issuetype|"
    "created|updated|resolved|due|duedate|resolution|labels?|sprint|epic|fixversion|"
    "component|summary|description|text|key|parent|watcher"
)
FILTER_SYNTAX = re.compile(
    rf"\b(?:{_JIRA_FIELDS})\s*(?::|=|!=|~|>=|<=|>|<|\bis\b|\bin\b|\bwas\b)",
    re.I,
)
# JQL structure that carries no field name of its own.
JQL_GRAMMAR = re.compile(r"\bORDER\s+BY\b|\bcurrentUser\s*\(|\bEMPTY\b", re.I)


def looks_like_a_filter(query: str) -> bool:
    """Whether a query was probably meant as JQL rather than as text.

    Consulted ONLY when a search returned nothing, so a phrase that genuinely
    matches issue text is never second-guessed for containing a colon.
    """
    return bool(FILTER_SYNTAX.search(query) or JQL_GRAMMAR.search(query))


NOT_JQL_HELP = (
    "No issues matched, and {query!r} looks like a field filter rather than a "
    "phrase. This tool searches the TEXT of issues only: it does not accept JQL "
    "or field:value syntax, so {query!r} was matched literally against issue text "
    "and found nothing.\n"
    "To filter by workflow status, use the `status` argument -- not the query. "
    "To search text, pass plain words. To limit to one project, use the project "
    "argument with its key. With no arguments at all this returns recent issues, "
    "each with its status."
)


def _asked(
    query: str,
    project: str,
    status: str,
    people: Sequence[tuple[str, str]] = (),
    authors: Sequence[tuple[str, str]] = (),
    updated_since: str = "",
    created_since: str = "",
) -> str:
    """The search, in the words it was actually run with.

    Printed beside an empty result, and it has to name EVERY filter applied. A
    failed lookup and an honest empty result are otherwise indistinguishable, so
    "no matches" gets read as "there is nothing" when it meant "that is not what
    was asked" -- and a date window is the filter most likely to be the reason.
    """
    parts = []
    if query.strip():
        parts.append(f"text matching {query.strip()!r}")
    if project.strip():
        parts.append(f"project {project.strip()}")
    if status.strip():
        parts.append(f"status {status.strip()}")
    if people:
        parts.append("assigned to " + ", ".join(name for _, name in people))
    if authors:
        parts.append("created by " + ", ".join(name for _, name in authors))
    if updated_since.strip():
        parts.append(f"updated since {updated_since.strip()}")
    if created_since.strip():
        parts.append(f"created since {created_since.strip()}")
    return "; ".join(parts) if parts else "issues updated in the last 30 days"


def is_bad_query(message: str) -> bool:
    """Whether Jira refused the QUERY, as opposed to refusing us.

    A 400 is the agent's own sentence coming back wrong, and it can rephrase.
    A 401 or a 5xx is not something it can rephrase its way out of, and
    answering one with "no matches" would read as an empty Jira.
    """
    return "returned 400" in message


def jira_reason(message: str) -> str:
    """Jira's own explanation, without the HTTP wrapping around it."""
    found = re.search(r'"errorMessages":\s*\[\s*"([^"]+)"', message)
    return found.group(1) if found else message[:200]


def _format_listing(listing: IssueListing, max_items: int, host: str = "") -> str:
    if not listing.items:
        return "no matches"
    # The browse URL is BUILT, not fetched: the key is already in hand and the
    # host is on the reader, so this costs no request. An issue key alone is not
    # a link -- a reader has to know the Jira domain to open it, and the agent
    # has no way to cite one.
    lines = [
        f"{i.key}\t[{i.status}]\t{i.summary}\t({i.issue_type}, {i.assignee}, "
        f"updated {i.updated[:10]})"
        + (f"\t{host}/browse/{i.key}" if host and i.key else "")
        for i in listing.items
    ]
    if listing.truncated:
        # Never silent: a capped list otherwise reads as the complete set.
        lines.append(f"(capped at {max_items} issues; there are more)")
    return "\n".join(lines)


DATASET_COLUMNS = ["key", "summary", "status", "issue_type", "assignee",
                   "updated", "project"]


def rows_from_listing(listing: IssueListing) -> tuple[list[str], list[list]]:
    """One row per issue, for a page to chart or tabulate.

    `updated` is cut to the date: the time is noise on an axis and unusable as a
    category, and "issues per day" is a question somebody actually asks.
    """
    rows = [
        [issue.key, issue.summary, issue.status, issue.issue_type,
         issue.assignee, issue.updated[:10], issue.project]
        for issue in listing.items
    ]
    return list(DATASET_COLUMNS), rows


def make_jira_tools(reader: JiraReader) -> Sequence[object]:
    from langchain_core.tools import tool

    @tool(response_format="content_and_artifact")
    def search_jira_issues(
        query: str = "",
        project: str = "",
        status: str = "",
        assignee: str = "",
        reporter: str = "",
        updated_since: str = "",
        created_since: str = "",
    ) -> tuple:
        """Find Jira issues. `query` searches the TEXT of the ticket -- plain words,
        never JQL: a filter like "status:open" goes in as text and matches nothing.
        `status` filters by workflow status; pass the names this Jira uses,
        comma-separated for several, and a name it does not have comes back as the
        list of names it does. To find the open ones, use `status`, not `query`.
        `project` narrows to one project key, e.g. RUUBY.
        `assignee` is who HOLDS a ticket and `reporter` is who CREATED it --
        different questions, so use the one that was asked; both take a person's
        name and are matched against Jira's own user list.
        `updated_since` bounds when a ticket last MOVED and `created_since` when it
        was MADE -- again different, and "created last week" means created_since.
        Both take a date (2026-01-01) or a window (-7d).
        With nothing at all this returns issues updated in the last 30 days.
        Returns issue keys to pass to read_jira_issue."""
        # Resolved before searching, because JQL wants account ids and a display
        # name is a 400. Measured: "chart the jira issues by Aryan" answered "no
        # matches", which reads as "Aryan has no issues" when the query never ran.
        people: list[tuple[str, str]] = []
        authors: list[tuple[str, str]] = []
        for name, role in ((assignee, "assignee"), (reporter, "reporter")):
            if not name.strip():
                continue
            found = reader.find_people(name)
            if not found:
                return (
                    f"No one in this Jira matches {name.strip()!r}. Names are "
                    "matched against Jira's own user list, so try a fuller name, "
                    f"or search without a {role} and read the {role} column.",
                    no_rows(),
                )
            if role == "assignee":
                people = found
            else:
                authors = found

        try:
            listing = reader.search(
                query, project, status,
                [account for account, _ in people],
                [account for account, _ in authors],
                updated_since, created_since,
            )
        except JiraError as error:
            # A value this Jira has never had is a 400, not an empty result, so
            # without this the whole agent reports itself broken -- and says
            # nothing about what should have been asked for instead. Naming the
            # valid values turns a dead end into one more turn.
            unknown = unknown_value(str(error))
            if unknown == "status" and status.strip():
                return (
                    f"This Jira has no status called {status.strip()!r}. "
                    f"It uses: {', '.join(reader.statuses())}.",
                    no_rows(),
                )
            if unknown == "project" and project.strip():
                # Measured: asked for tickets "for revasure", the reply was that
                # no Revasure project could be identified -- while the agent held
                # every project name and printed none of them.
                keys = ", ".join(
                    f"{key} ({name})" for key, name in reader.projects()
                )
                return (
                    f"This Jira has no project called {project.strip()!r}. "
                    f"It has: {keys}.",
                    no_rows(),
                )
            # Any other refused QUERY comes back as Jira's own sentence rather
            # than as a dead agent: a 400 is something the next turn can fix.
            if is_bad_query(str(error)):
                return (
                    f"Jira refused that search: {jira_reason(str(error))} "
                    f"(searched for: {_asked(query, project, status, people, authors, updated_since, created_since)})",
                    no_rows(),
                )
            raise
        # An empty result and a misunderstood query are the same ten characters
        # otherwise. Measured: asked for open tickets, the agent sent
        # "status:open", got "no matches", and reported that no open tickets
        # existed -- while an empty query returns 42 issues, INS-35 among them,
        # marked "To Do". Silence is what made a wrong answer confident.
        if not listing.items and looks_like_a_filter(query):
            return NOT_JQL_HELP.format(query=query), no_rows()
        if not listing.items and status.strip():
            # Jira does not always refuse a status it has never heard of. Verified
            # against this instance: status IN ("Open") returns an empty result,
            # not a 400 -- and "Open" is the first word any model reaches for,
            # while the statuses here are "To Do", "In Progress" and the rest.
            # Checked here rather than before the search so the happy path never
            # pays for the extra request.
            wrong = unknown_statuses(reader, status)
            if wrong:
                listed = ", ".join(repr(name) for name in wrong)
                return (
                    f"This Jira has no status called {listed}. It uses: "
                    f"{', '.join(reader.statuses())}. The open ones are the "
                    "statuses other than Done -- pass them comma-separated.",
                    no_rows(),
                )
        if not listing.items:
            # Say what was actually run. A failed lookup and an honest empty
            # result are otherwise indistinguishable, and "no matches" gets read
            # as "there is nothing" when it meant "that is not what was asked".
            return (
                "no matches for: "
                + _asked(query, project, status, people, authors,
                         updated_since, created_since),
                no_rows(),
            )
        columns, rows = rows_from_listing(listing)
        listed = _format_listing(listing, reader.max_items, reader.host)
        # Named from the rows already in hand -- no extra request, so the happy
        # path pays nothing. Measured: asked for "open jira tickets from last
        # week", the agent ran a date filter with NO status and reported 7 open
        # RAD tickets, three of which it had itself just printed as [Done]. It
        # had no definition of "open" and invented one: everything the search
        # returned. A description cannot be relied on to prevent that; the result
        # saying so cannot be skipped.
        if not status.strip():
            present = sorted({one.status for one in listing.items if one.status})
            if len(present) > 1 or any(one.casefold() == "done" for one in present):
                listed = (
                    "NO STATUS FILTER was applied, so these are every status "
                    f"rather than the open ones. Present here: {', '.join(present)}."
                    " Open means the statuses other than Done: to answer a"
                    " question about open tickets, search again with status set to"
                    " those. Do not describe this set as the open ones.\n"
                ) + listed
        # A name was matched against Jira's user list, which is a guess. Say who
        # it landed on: "Aryan" reaching two people is a different search from
        # the one that was asked for, and silently including both is how a count
        # comes out wrong for a reason nobody can see.
        for label, matched in (("Assigned to", people), ("Created by", authors)):
            if matched:
                listed = f"{label}: " + ", ".join(n for _, n in matched) + "\n" + listed
        return listed, table(columns, rows, truncated=listing.truncated)

    @tool
    def read_jira_issue(key: str) -> str:
        """Read one Jira issue by its key, e.g. RUUBY-12: its fields, its description,
        and its comments. The comments usually carry the decision."""
        try:
            return reader.read_issue(key, reader.updated(key))
        except JiraError as error:
            # A 404 on one key is a NEGATIVE RESULT, not a failure. Measured: the
            # agent guessed "E7" was an issue key, Jira correctly said no such
            # issue, and the reply told the reader "the jira agent could not be
            # reached" -- Jira was reached, and it answered. Anything else (401,
            # 403, 5xx, a timeout) really is a failure and still raises.
            if "returned 404" in str(error):
                return (
                    f"There is no Jira issue with the key {key!r}, or this account "
                    "cannot see it. Use search_jira_issues to find issues by text, "
                    "or list_jira_projects to see which project keys exist."
                )
            raise

    @tool(response_format="content_and_artifact")
    def list_jira_projects() -> tuple:
        """List the Jira projects this account can see, as 'key<tab>name' lines. Use a
        key to narrow search_jira_issues."""
        pairs = reader.projects()
        if not pairs:
            return "no projects visible to this account", no_rows()
        return (
            "\n".join(f"{key}\t{name}" for key, name in pairs),
            table(["key", "name"], [[key, name] for key, name in pairs],
                  truncated=False),
        )

    return [search_jira_issues, read_jira_issue, list_jira_projects]
