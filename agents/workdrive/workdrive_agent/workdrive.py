# Copied from rytangle router/app/tools/workdrive.py (WorkDrive 1.x stays built into the
# router until every server runs the catalog). Only the two helper imports differ.
"""Read-only Zoho WorkDrive access, through the client's MCP server.

What this agent can and cannot see is the first thing to understand about it,
because the limit is unusual and it is not ours to fix.

The MCP connection exposes thirty-three team, folder and user APIs and **no
file-content API**. (It was twenty-four when this was written; the client has
added to it since, and re-checking the count is one call -- see the note below.)
Its granted scopes are
`WorkDrive.teamfolders.READ`, `.teamfolders.admin.READ`, `.team.READ`,
`.team.admin.READ` and `.users.READ` -- `WorkDrive.files.READ` is absent. So this
agent reads the SHAPE of a WorkDrive: which team folders exist, what is filed in
them, who can reach them, who is on the team. It cannot open a document and read
its text, the way the gdocs agent reads a Google Doc.

That is a property of the client's MCP configuration rather than of WorkDrive,
whose own API does have download endpoints. Adding the file APIs and
`WorkDrive.files.READ` on their side would make document reading possible, and
nothing here would need rewriting -- a `read_workdrive_file` tool would simply
have something to call. The agent's description in config/agents.json states the
limit plainly, because a planner that believes this agent can read documents will
route "what does the spec say" here and get a confident nothing back.

Verified by execution against the live server rather than read from the supplied
documentation, and these findings shaped what is below:

- **The tool list is the client's to change, and they have changed it.** It was
  twenty-four APIs when first surveyed and is thirty-three now, so treat any count
  written down here as of its date and re-read `tools/list` before relying on one.
  What has NOT changed is the thing that matters: no tool among the thirty-three
  returns a file's contents, so the limit above still holds.
- **Nine of those additions are reachable and unused.** `Sub_Folders` and
  `Get_Team_Folder_Folders` descend into a subfolder -- today `list_workdrive_files`
  shows subfolders in its listing and nothing can open one, so a question about
  anything below the top level of a team folder is a dead end. `Get_My_Folder_Id`,
  `My_Folder_Files` and `getFilesInMyFolders` read a person's own My Folder rather
  than a team's. Both are worth wiring up once there is an account with data to
  test against; neither is worth writing blind.
- **There is no team id to configure.** It is discovered -- user, then that
  user's teams -- so a deployment needs only a URL and a refresh token, and the
  same build works for any client.
- **An account with no WorkDrive provisioning answers `{"data":[]}` rather than
  failing.** Empty and unprovisioned are indistinguishable at the wire, so the
  bootstrap says which it suspects instead of reporting an empty WorkDrive.
- **Every argument shape here was checked against the server's own schemas.** All
  seven APIs this file calls take their path arguments nested under
  `path_variables` and their query arguments under `query_params`, with every
  query value a string -- confirmed by reading `tools/list`, not inferred. A flat
  argument is refused with `Mandatory path variable "<name>" is not present in
  tool body`, which names the variable but not the nesting, so the shape is easy
  to get wrong and slow to diagnose.

Response shapes are JSON:API -- `{"data": [{"id", "attributes": {...}}]}` --
confirmed against the live `getUserInfo`. Field NAMES come from the server's own
`fields[files]` enum, which documents `name`, `type`, `parent_id`,
`status_change_time_in_millisecond`, `created_time_in_millisecond`,
`capabilities` and `storage_info`. Times are epoch MILLISECONDS rather than date
strings, which is why `_when` exists: read as a date and truncated, one of them
prints as "1788194886", and a number that looks like data but means nothing is
worse than a blank.

What could NOT be confirmed is how those attributes come back POPULATED, because
the only account available for testing has no WorkDrive data. So every field is
read through a list of candidate names and a missing one degrades to a blank
rather than a KeyError. The first run against a provisioned account is the real
test of this file, and the likeliest thing to need adjusting.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from workdrive_agent.mcp import MCPClient, MCPError
from agent_kit.rows import no_rows, table

# Zoho caps a page at 50 and the cap is theirs, not ours.
PAGE_SIZE = 50
# What one listing may return before it says it was shortened. A folder with
# thousands of files should not become thousands of lines in a prompt.
MAX_ITEMS = 200

TOOL_PREFIX = "ZohoWorkdrive_"

# Spellings the client's server has used for the same API, newest first. They
# renamed every camelCase tool to PascalCase without notice, which turned an
# edit on their side into a total outage on ours; resolving through this means
# the next rename costs nothing. Arguments were identical across the rename --
# verified against `tools/list` schemas, not assumed.
ALIASES: dict[str, tuple[str, ...]] = {
    "getUserInfo": ("Get_User_Info",),
    "Get_User_Info": ("getUserInfo",),
    "Get_All_Teams_Of_User": ("getAllTeamsOfUser",),
    "getFilesInMyFolders": ("My_Folder_Files",),
    "getTeamFolderInfo": ("Get_Team_Folders_Info",),
    "Get_Current_Team_User": ("getCurrentTeamMember",),
    "Get_My_Folder_Id": ("getmyfolderid",),
}


class WorkDriveError(RuntimeError):
    """A WorkDrive request did not return usable data."""


@dataclass(frozen=True)
class Item:
    """A file or a folder, as a listing shows it."""

    id: str
    name: str
    kind: str
    modified: str
    # An openable link to the document, straight from the metadata Zoho already
    # returns. Empty for a folder and for anything the service has no page for,
    # which is why nothing here may depend on it being present.
    permalink: str = ""


@dataclass(frozen=True)
class Person:
    """Someone with a name, and whatever else came back about them."""

    id: str
    name: str
    email: str
    role: str


@dataclass(frozen=True)
class Listing:
    items: list[Item]
    truncated: bool


def _first(attrs: dict, *names: str, default: str = "") -> str:
    """The first of several possible field names that is actually present.

    Zoho spells the same idea differently across resources -- `name` and
    `display_name`, `email_id` and `email`. Reading through a list of candidates
    keeps one renamed field from emptying a whole listing.
    """
    for name in names:
        value = attrs.get(name)
        if value not in (None, ""):
            return str(value)
    return default


def _when(attrs: dict, *names: str) -> str:
    """A date a model can reason about, from whichever shape Zoho used.

    The documented file fields are `status_change_time_in_millisecond` and
    `created_time_in_millisecond` -- epoch MILLISECONDS, not a date string. Taken
    for a date and truncated to ten characters, one reads as "1788194886", which
    is worse than blank: it looks like data and means nothing to the model
    reading it.
    """
    raw = _first(attrs, *names)
    if not raw:
        return ""
    if raw.isdigit():
        from datetime import datetime, timezone

        seconds = int(raw) / (1000 if len(raw) > 11 else 1)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    # Already a date or an ISO timestamp; keep the date half.
    return raw[:10]


def _rows(payload: dict) -> list[dict]:
    """The `data` array of a JSON:API response, whatever it holds."""
    data = payload.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []


class WorkDriveReader:
    def __init__(self, mcp: MCPClient, max_items: int = MAX_ITEMS) -> None:
        self.mcp = mcp
        self.max_items = max_items
        self._names: set[str] | None = None
        self._me_cache: dict | None = None
        self._zuid: str | None = None
        self._team_id: str | None = None
        self._team_member_id: str | None = None
        self._my_folder_id: str | None = None

    # ── one call ─────────────────────────────────────────────────────────────

    def _resolve(self, tool: str) -> str:
        """The name this server actually exposes for `tool`.

        The client's tool list is theirs to change and they change it: 24 tools
        when first surveyed, 33 later, 39 now -- and that last edit RENAMED the
        camelCase forms (`getUserInfo`, `getFilesInMyFolders`) to PascalCase
        while keeping identical arguments. Every call broke at once, mid-day,
        with `Tool not configured in mcp server`, and the agent could answer
        nothing at all.

        So a name is resolved against `tools/list` rather than trusted: the
        alternative is that a rename on their side is an outage on ours, with a
        code change and a deploy to recover from something that costs them a
        checkbox. ALIASES holds the spellings seen for the same API; anything
        already present is used as-is.

        The list is fetched once per process by MCPClient and cached, so this
        costs nothing per call.
        """
        if self._names is None:
            try:
                self._names = {
                    str(t.get("name", "")) for t in self.mcp.list_tools()
                }
            except MCPError:
                # Unreachable list: fall through to the requested name, so the
                # failure is the ORIGINAL one rather than a confusing second.
                self._names = set()

        wanted = f"{TOOL_PREFIX}{tool}"
        if not self._names or wanted in self._names:
            return tool
        for alias in ALIASES.get(tool, ()):
            if f"{TOOL_PREFIX}{alias}" in self._names:
                return alias
        return tool

    def _call(self, tool: str, path: dict | None = None, query: dict | None = None) -> dict:
        tool = self._resolve(tool)
        arguments: dict = {}
        if path:
            arguments["path_variables"] = path
        if query:
            arguments["query_params"] = {k: str(v) for k, v in query.items()}
        try:
            body = self.mcp.call(f"{TOOL_PREFIX}{tool}", arguments)
        except MCPError as error:
            raise WorkDriveError(str(error)) from None
        if not body.strip():
            return {}
        try:
            return json.loads(body)
        except ValueError:
            raise WorkDriveError(f"{tool} returned something that is not JSON: {body[:200]}") from None

    # ── bootstrap: who are we, and which team ────────────────────────────────

    def _me(self) -> dict:
        """This connection's own user record, read once.

        It carries more than an id: `preferred_team_id`, and nested inside
        `preferred_org_info`, `privatespace_id`. Both were being fetched with
        separate calls until a live payload was read properly -- an earlier probe
        printed only the SCALAR attributes, so the nested org objects were never
        opened, and the comment here claimed the field was absent.
        """
        if self._me_cache is None:
            payload = self._call("getUserInfo")
            row = payload.get("data") or {}
            attrs = dict(row.get("attributes") or {})
            attrs["__row_id"] = row.get("id") or ""
            self._me_cache = attrs
        return self._me_cache

    @property
    def zuid(self) -> str:
        if self._zuid is None:
            attrs = self._me()
            found = _first(attrs, "zuid", "zid") or str(attrs.get("__row_id") or "")
            if not found:
                raise WorkDriveError("WorkDrive did not say who this connection belongs to")
            self._zuid = found
        return self._zuid

    @property
    def team_id(self) -> str:
        """The team whose folders this connection reads, discovered not configured.

        A client deployment should need a URL and a refresh token and nothing
        else; asking an administrator to find a team id is asking them to get it
        wrong.
        """
        if self._team_id is not None:
            return self._team_id

        # Already in the user record as `preferred_team_id`. Reading it there
        # costs nothing -- that record is fetched anyway -- and one fewer call is
        # one fewer tool whose name the client can rename underneath us.
        cheap = _first(self._me(), "preferred_team_id", "org_id")
        if cheap:
            self._team_id = cheap
            return cheap

        rows = _rows(self._call("Get_All_Teams_Of_User", {"zuid": self.zuid}))
        if not rows:
            # Empty and unprovisioned look identical here, so say both.
            raise WorkDriveError(
                "this account belongs to no WorkDrive team. Either the authorised "
                "account is not the one holding the team folders, or WorkDrive has "
                "not been set up for it."
            )
        self._team_id = str(rows[0].get("id") or "")
        if not self._team_id:
            raise WorkDriveError("WorkDrive returned a team with no id")
        return self._team_id

    @property
    def team_member_id(self) -> str:
        """This connection's identity WITHIN its team, which is not its zuid.

        A person has one zuid and a separate membership id per team, shaped
        `<team_id>-<member>`. `Get_My_Folder_Id` takes the membership id, so the
        zuid cannot be substituted -- it is accepted and answers for nobody.
        """
        if self._team_member_id is not None:
            return self._team_member_id

        rows = _rows(self._call("Get_Current_Team_User", {"team_id": self.team_id}))
        found = str(rows[0].get("id") or "") if rows else ""
        if not found:
            raise WorkDriveError(
                "WorkDrive did not say who this connection is within its team, so "
                "its My Folders cannot be located"
            )
        self._team_member_id = found
        return found

    @property
    def my_folder_id(self) -> str:
        """This connection's own My Folders, discovered rather than configured.

        One field lookup where the user record carries it, which is the normal
        case: `preferred_org_info.privatespace_id`. That replaces a four-hop
        chain -- user, team, membership, folder.

        The earlier note here said `getUserInfo` carries no `privatespace_id`.
        It does; the probe behind that claim printed only scalar attributes, so
        the nested org objects were never opened. The chain below is kept for an
        account whose record has no org info, and there the id is the ROW id
        rather than an attribute: `Get_My_Folder_Id` answers
        `{"data": {"id": "<folder>", "attributes": {...}}}` where the attributes
        are unread counts and a hold flag, and nothing in them is the folder.
        """
        if self._my_folder_id is not None:
            return self._my_folder_id

        # The user record carries it, nested one level down in the org info,
        # which replaces THREE calls -- teams, then membership, then folder --
        # with a field lookup on a record already in hand.
        me = self._me()
        for key in ("preferred_org_info", "last_viewed_org_info"):
            nested = me.get(key)
            if isinstance(nested, dict):
                found = _first(nested, "privatespace_id", "private_space_id")
                if found:
                    self._my_folder_id = found
                    return found

        # The chain remains for an account whose record carries no org info.
        rows = _rows(self._call("Get_My_Folder_Id", {"team_member_id": self.team_member_id}))
        found = str(rows[0].get("id") or "") if rows else ""
        if not found:
            raise WorkDriveError(
                "this connection reports no My Folders. Either WorkDrive has not "
                "been set up for the authorised account, or the grant is missing "
                "WorkDrive.users.READ."
            )
        self._my_folder_id = found
        return found

    # ── paging ───────────────────────────────────────────────────────────────

    def _paged(self, tool: str, path: dict, query: dict | None = None) -> tuple[list[dict], bool]:
        """Rows up to max_items, and whether more were left behind."""
        collected: list[dict] = []
        offset = 0
        while True:
            page = dict(query or {})
            page["page[limit]"] = PAGE_SIZE
            page["page[offset]"] = offset
            rows = _rows(self._call(tool, path, page))
            collected.extend(rows)
            if len(rows) < PAGE_SIZE:
                return collected, False
            if len(collected) >= self.max_items:
                # Never silent: a shortened list otherwise reads as the whole set.
                del collected[self.max_items :]
                return collected, True
            offset += PAGE_SIZE

    # ── what the tools ask for ───────────────────────────────────────────────

    def team_folders(self) -> Listing:
        rows, truncated = self._paged("Get_All_Team_Folders", {"team_id": self.team_id})
        return Listing(
            items=[
                Item(
                    id=str(row.get("id") or ""),
                    name=_first(row.get("attributes") or {}, "name", "display_name", default="(unnamed)"),
                    kind="team folder",
                    modified=_when(
                        row.get("attributes") or {},
                        "status_change_time_in_millisecond",
                        "modified_time",
                        "created_time_in_millisecond",
                    ),
                )
                for row in rows
            ],
            truncated=truncated,
        )

    def _file_listing(
        self,
        tool: str,
        path: dict,
        kind: str = "",
        extension: str = "",
        newest_first: bool = True,
    ) -> Listing:
        """A file listing, whichever location it came from.

        Team folders and My Folders differ only in the tool name and the id it
        takes -- both accept the same `sort`, `filter[*]` and `page[*]` query
        params (read from the server's own schemas) and both answer in the same
        JSON:API shape, so the row-reading lives here once rather than drifting
        between two copies.
        """
        query: dict = {"sort": "-last_modified" if newest_first else "name"}
        if kind:
            query["filter[type]"] = kind
        if extension:
            query["filter[extension]"] = extension
        rows, truncated = self._paged(tool, path, query)
        items = []
        for row in rows:
            attrs = row.get("attributes") or {}
            items.append(
                Item(
                    id=str(row.get("id") or ""),
                    name=_first(attrs, "name", "display_name", default="(unnamed)"),
                    kind=_first(attrs, "type", "kind", default="file"),
                    # Already in the response and previously discarded. An answer
                    # that cites a document should be able to point at it, and a
                    # reader should be able to check WHICH document was read.
                    permalink=_first(attrs, "permalink", "url_link"),
                    # The names the server documents for this listing, in the
                    # order that answers "when did this last change".
                    modified=_when(
                        attrs,
                        "status_change_time_in_millisecond",
                        "modified_time",
                        "created_time_in_millisecond",
                    ),
                )
            )
        return Listing(items=items, truncated=truncated)

    def folder_contents(
        self, folder_id: str, kind: str = "", extension: str = "", newest_first: bool = True
    ) -> Listing:
        """What is inside ANY folder: a team folder, a folder shared with this
        account, or a subfolder of either.

        `Get_File_List` takes a plain `folder_id` and was verified live against
        all three -- My Folders, a shared root, and a subfolder three levels
        down. That matters because the folder that actually holds this client's
        work is a SHARED one, and the team-folder call cannot address it: every
        listing tool answered "nothing here" while a whole project tree sat one
        call away.

        `Get_Team_Folder_Files` stays as the fallback. It is the call this used
        to make, no team folder exists in this workspace to test the generic one
        against, and quietly dropping a path that worked is how a capability
        disappears for someone else's deployment.
        """
        try:
            return self._file_listing(
                "Get_File_List", {"folder_id": folder_id}, kind, extension, newest_first
            )
        except WorkDriveError:
            return self._file_listing(
                "Get_Team_Folder_Files",
                {"teamfolder_id": folder_id},
                kind,
                extension,
                newest_first,
            )

    def my_files(
        self, kind: str = "", extension: str = "", newest_first: bool = True
    ) -> Listing:
        """The files in this connection's own My Folders.

        Its own method rather than a `folder_contents` call site because it
        needs no id from the caller -- that is the whole point of it, for an
        account with no team folders.
        """
        return self.folder_contents(self.my_folder_id, kind, extension, newest_first)

    def folder_viewers(self, folder_id: str) -> list[Person]:
        rows = _rows(self._call("Get_Team_Folder_Shared_Users", {"teamfolder_id": folder_id}))
        return [_person(row) for row in rows]

    def team_members(self, search: str = "") -> list[Person]:
        query = {"search[all]": search} if search.strip() else None
        rows, _ = self._paged("Get_Team_Users", {"team_id": self.team_id}, query)
        return [_person(row) for row in rows]

    def folder_details(self, folder_id: str) -> dict:
        payload = self._call("getTeamFolderInfo", {"teamfolder_id": folder_id})
        row = payload.get("data")
        if isinstance(row, list):
            row = row[0] if row else {}
        return (row or {}).get("attributes") or {}


def _person(row: dict) -> Person:
    attrs = row.get("attributes") or {}
    return Person(
        id=str(row.get("id") or ""),
        name=_first(attrs, "display_name", "name", "user_name", default="(unnamed)"),
        email=_first(attrs, "email_id", "email"),
        role=_first(attrs, "role", "role_name", "permission", "type"),
    )


# ── the tools the agent holds ────────────────────────────────────────────────


def _render(listing: Listing, max_items: int) -> str:
    if not listing.items:
        return "nothing here"
    lines = [
        f"{item.id}\t[{item.kind}]\t{item.name}"
        + (f"\t(changed {item.modified})" if item.modified else "")
        + (f"\t{item.permalink}" if item.permalink else "")
        for item in listing.items
    ]
    if listing.truncated:
        lines.append(f"(shortened to {max_items} items; there are more)")
    return "\n".join(lines)


def _render_people(people: Sequence[Person], empty: str) -> str:
    if not people:
        return empty
    return "\n".join(
        "\t".join(part for part in (person.name, person.email, person.role) if part)
        for person in people
    )


def make_workdrive_tools(reader: WorkDriveReader, content: object | None = None) -> list:
    """The WorkDrive tools.

    `content` is optional and separate from `reader` because it reaches a
    different place: the reader speaks MCP, and reading a file's bytes does not
    go through MCP at all (see workdrive_direct.py). Absent -- which is what
    WORKDRIVE_DIRECT_API=false produces -- the listing tools work exactly as
    before and no tool claims to read a document.
    """
    from langchain_core.tools import tool

    def reporting(work) -> str:
        """A WorkDrive failure is text, not an exception.

        A raised error ends the agent's turn; the model needs to read what went
        wrong and choose differently -- most usefully when a folder id is wrong
        and listing the folders again would fix it.
        """
        try:
            return work()
        except WorkDriveError as error:
            return f"workdrive error: {error}"

    @tool
    def list_workdrive_folders() -> str:
        """List every Zoho WorkDrive team folder this connection can see, with the
        folder id to pass to the other WorkDrive tools. Call this first: the other
        tools all need a folder id, and the ids are not guessable."""
        return reporting(lambda: _render(reader.team_folders(), reader.max_items))

    @tool
    def list_workdrive_files(folder_id: str, file_type: str = "", extension: str = "") -> str:
        """List the files and subfolders inside ANY WorkDrive folder, newest first,
        by a folder id from list_workdrive_folders, list_workdrive_shared_with_me,
        or from an earlier call to this tool.

        Call it again on a SUBFOLDER's id to go deeper. A folder's real content is
        often several levels down, and stopping at the top level reports a nearly
        empty drive.

        This returns NAMES and dates only. It cannot read what is inside a file --
        this connection has no access to file contents, so a question about what a
        document SAYS cannot be answered from WorkDrive.

        Narrow it with file_type (documents, spreadsheets, presentations, pdf,
        images, folder) or extension (docx,pdf)."""
        return reporting(
            lambda: _render(
                reader.folder_contents(folder_id, kind=file_type, extension=extension),
                reader.max_items,
            )
        )

    @tool
    def list_my_workdrive_files(file_type: str = "", extension: str = "") -> str:
        """List the files in the WorkDrive My Folders of the account this
        connection authenticates as, newest first. Needs no folder id.

        Use this when list_workdrive_folders shows no team folders, or when a
        document is described as the bot's own rather than the team's -- a
        workspace can have every file here and no team folder at all.

        This returns NAMES and dates only. It cannot read what is inside a file --
        this connection has no access to file contents, so a question about what a
        document SAYS cannot be answered from WorkDrive.

        Narrow it with file_type (documents, spreadsheets, presentations, pdf,
        images, folder) or extension (docx,pdf)."""
        return reporting(
            lambda: _render(
                reader.my_files(kind=file_type, extension=extension), reader.max_items
            )
        )

    @tool
    def who_can_access_workdrive_folder(folder_id: str) -> str:
        """List the people and groups a WorkDrive team folder is shared with, by the
        folder id shown by list_workdrive_folders. Use this for questions about who
        can see or reach something."""
        return reporting(
            lambda: _render_people(
                reader.folder_viewers(folder_id), "this folder is shared with nobody"
            )
        )

    @tool
    def list_workdrive_team_members(search: str = "") -> str:
        """List the members of the WorkDrive team, optionally narrowed by a name or
        email fragment. Use this to find out who is in the workspace at all."""
        return reporting(
            lambda: _render_people(reader.team_members(search), "no members matched")
        )

    @tool
    def describe_workdrive_folder(folder_id: str) -> str:
        """Describe one WorkDrive team folder -- its name, description, and how it is
        configured -- by the folder id shown by list_workdrive_folders."""

        def run() -> str:
            attrs = reader.folder_details(folder_id)
            if not attrs:
                return "no such folder, or nothing is recorded about it"
            lines = [
                f"{key}\t{value}"
                for key, value in sorted(attrs.items())
                if not isinstance(value, (dict, list)) and value not in (None, "")
            ]
            return "\n".join(lines) if lines else "no details recorded"

        return reporting(run)

    @tool
    def list_workdrive_shared_with_me() -> str:
        """List the folders and files other people have SHARED with this account,
        with the id to pass to list_workdrive_files. Needs no folder id.

        Call this whenever list_workdrive_folders comes back empty, and before
        concluding that WorkDrive holds nothing: a shared folder belongs to no
        team folder and is not in My Folders, so it appears in neither listing.
        A team's real work often lives entirely here."""

        def run() -> str:
            if content is None:
                return (
                    "workdrive error: shared items cannot be listed for this connection "
                    "(WORKDRIVE_DIRECT_API is off, and the MCP server exposes no tool "
                    "for them). Team folders and My Folders are still readable."
                )
            shared = content.incoming_shares(reader.zuid)  # type: ignore[attr-defined]
            if not shared:
                return "nothing has been shared with this account"
            return "\n".join(
                f"{item.id}\t[{item.kind}]\t{item.name}"
                + (f"\t(shared by {item.owner})" if item.owner else "")
                for item in shared
            )

        try:
            return run()
        except Exception as error:  # noqa: BLE001 - reported to the model as text
            return f"workdrive error: {error}"

    @tool(response_format="content_and_artifact")
    def read_workdrive_file(file_id: str) -> tuple:
        """Read the TEXT INSIDE one WorkDrive file, by the file id shown in a
        listing. Use this to answer what a document actually says.

        Readable: Word, Excel and PowerPoint; Zoho Writer, Sheet and Show, which
        export to those; OpenDocument (.odt .ods .odp); PDFs that have selectable
        text; HTML; RTF; and plain text including CSV, Markdown and JSON.
        A spreadsheet comes back one row per line with cells tab-separated, so a
        heading keeps its value.

        Not readable, and each says which it is rather than coming back empty: a
        SCANNED pdf (pictures of words -- it would need OCR), images, audio,
        video, archives, and the pre-2007 .doc/.xls/.ppt binaries. When a file
        cannot be read, say the file exists and its contents are not readable --
        never infer contents from the filename."""

        def run() -> tuple:
            if content is None:
                return (
                    "workdrive error: reading file contents is turned off for this "
                    "connection (WORKDRIVE_DIRECT_API). File and folder names, dates "
                    "and sharing are still readable.",
                    no_rows(),
                )
            got = content.read(file_id)  # type: ignore[attr-defined]
            head = f"=== {got.name} ==="
            if got.truncated:
                # Declared, never silent: an answer drawn from a shortened source
                # that did not say so is indistinguishable from a complete one.
                head += " (TRUNCATED -- the document continues past what is shown)"
            # The rows a spreadsheet already was. The text half is unchanged --
            # a page charts these, the model still reads the same characters.
            payload = (
                table(got.columns, got.rows, truncated=got.more_tables)
                if got.columns and got.rows
                else no_rows()
            )
            return f"{head}\n{got.text}", payload

        try:
            return run()
        except Exception as error:  # noqa: BLE001 - reported to the model as text
            return f"workdrive error: {error}", no_rows()

    return [
        list_workdrive_folders,
        list_workdrive_files,
        list_my_workdrive_files,
        list_workdrive_shared_with_me,
        read_workdrive_file,
        who_can_access_workdrive_folder,
        list_workdrive_team_members,
        describe_workdrive_folder,
    ]
