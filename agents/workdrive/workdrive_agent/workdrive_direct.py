# Copied from rytangle router/app/tools/workdrive_direct.py (WorkDrive 1.x stays built into the
# router until every server runs the catalog). Unchanged.
"""WorkDrive's own REST API, for what the MCP tool list cannot do.

── why this does not go through MCP ──

The MCP connection exposes 33 tools and not one of them returns a file's
contents, so for a while the honest answer to "what does this document say" was
that WorkDrive could not answer it. That was wrong, and the mistake is worth
recording because it cost a wrong ask to the client.

The file metadata carries a `download_url` on `download-accl.zoho.com`, and that
host refuses this token with `INVALID_OAUTHSCOPE`. The conclusion drawn from
that refusal -- "content is unreachable" -- was drawn from ONE host. The token
response names its own `api_domain`, `https://www.zohoapis.com`, and that host
serves the same download happily:

    GET {api_domain}/workdrive/api/v1/download/<file_id>
    Authorization: Zoho-oauthtoken <access token>
    -> 200, content-disposition: attachment; filename="PCR 859_Unit Test.xlsx"

The scope was never missing: the access token carries `WorkDrive.files.READ`,
verified by reading the `scope` field the token endpoint returns.

── the boundary question this raises ──

The client granted `WorkDrive.files.READ` while exposing no content tool, so
their two signals disagree about where the limit is meant to be. Until they say
which they intend, this is behind `WORKDRIVE_DIRECT_API`: reading a document is
within the scope they granted, and one env var turns it off if they say the tool
list is the boundary.

── formats ──

Turning bytes into text is document_text.py's job, not this module's. Zoho
exports its native documents to Office formats -- a Zoho Sheet downloads as
.xlsx -- so nothing here is Zoho-specific either.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from .document_text import UnreadableDocument, extract
from .mcp import TokenProvider

# The path is the same on every data centre; the HOST comes from the token
# response, so it is passed in rather than written here.
DOWNLOAD_PATH = "/workdrive/api/v1/download/{file_id}"

# Folders and files somebody has SHARED with this account. There is no MCP tool
# for this -- checked against `tools/list`, all 39 of them -- and it is the only
# way to see a shared folder at all: it belongs to no team folder and is not in
# My Folders, so every listing the agent had answered "nothing here" while a
# whole project tree sat behind this one path.
INCOMING_PATH = "/workdrive/api/v1/users/{zuid}/incomingfiles"

# One document's worth of text. Past this the model's recall degrades and the
# cost stops buying anything, so it is truncated with the cut declared -- an
# answer built from a silently shortened source is the failure this avoids.
MAX_CHARS = 200_000

# A ceiling on what is pulled over the wire at all. A 200MB video would
# otherwise be downloaded in full to discover it has no text.
MAX_BYTES = 25 * 1024 * 1024


# How long a content download may take.
#
# Separate from the shared client's timeout, which is sized for metadata calls
# that answer in under a second. A Zoho Writer file is not stored as .docx: the
# download EXPORTS it while the request is open, so the wait tracks the
# conversion rather than the file's size.
#
# Measured live 2026-09-22, reading a 1,028 character document four times: 9.7,
# 16.3, 14.5 and 15.5 seconds. At the shared 30 second ceiling a read that
# normally takes 15 has only to be twice as slow to fail, and one did -- the
# agent then answered from the next file it could read, with a correct citation,
# to a question it had not answered.
#
# 60 and not more. This was first set to 120 and that was a mistake worth
# recording: a slow read no longer fails, it WAITS, and a fanned-out run opening
# several documents across several agents multiplies the wait. A live run that
# had taken 73 seconds was still going after ten minutes and had to be killed.
# 60 leaves roughly four times the measured worst case while capping what a
# single stuck read can cost the person waiting in the channel.
#
# incoming_shares below deliberately does NOT get this: it is a listing, and a
# listing that hangs for two minutes is a bug rather than a slow export.
DOWNLOAD_TIMEOUT_SECONDS = 60.0


class WorkDriveDirectError(RuntimeError):
    """A file could not be downloaded, or holds no text this can read."""


@dataclass(frozen=True)
class FileText:
    name: str
    text: str
    truncated: bool
    """The file's first table, for formats that have one. None for prose.

    `more_tables` is the ROWS' own truncation flag and is deliberately not
    `truncated` above: cutting the text at a character limit drops no rows, and
    a workbook's second sheet is not shown however short the file is.
    """
    columns: list[str] | None = None
    rows: list[list[str]] | None = None
    more_tables: bool = False


@dataclass(frozen=True)
class Shared:
    """A folder or file shared with this account."""

    id: str
    name: str
    kind: str
    owner: str


class WorkDriveDirect:
    """WorkDrive over its own REST API rather than through MCP."""

    def __init__(
        self,
        api_domain: Callable[[], str],
        token: TokenProvider,
        client: httpx.Client,
        max_chars: int = MAX_CHARS,
    ) -> None:
        # A callable, not a string: the domain comes from the token response, so
        # it is not known until a token has been minted, and minting one when the
        # agent is WIRED would spend a request per process start whether or not
        # any question ever reaches this agent.
        self.api_domain = api_domain
        self.token = token
        self.client = client
        self.max_chars = max_chars

    def incoming_shares(self, zuid: str) -> list[Shared]:
        """What has been shared WITH this account, which nothing else can see.

        A shared folder is not a team folder and not in My Folders, so the two
        listing tools both answer "nothing here" -- correctly, and uselessly. On
        this deployment that hid a folder holding the client's SOPs, quotations
        and delivery records behind an empty-looking WorkDrive.

        The zuid is passed in rather than looked up here: the MCP reader already
        knows it, and a second identity lookup down a different transport is a
        second thing that can disagree.
        """
        url = self.api_domain().rstrip("/") + INCOMING_PATH.format(zuid=zuid)
        try:
            response = self.client.get(
                url,
                headers={"Authorization": f"Zoho-oauthtoken {self.token()}"},
                follow_redirects=True,
            )
        except httpx.HTTPError as error:
            raise WorkDriveDirectError(f"could not reach WorkDrive: {error}") from None
        if response.status_code >= 400:
            raise WorkDriveDirectError(
                f"WorkDrive answered {response.status_code} listing shared items: "
                f"{response.text[:200]}"
            )
        try:
            rows = (response.json() or {}).get("data") or []
        except ValueError:
            raise WorkDriveDirectError("WorkDrive's reply was not JSON") from None

        out: list[Shared] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            attrs = row.get("attributes") or {}
            out.append(Shared(
                id=str(row.get("id") or ""),
                name=str(attrs.get("name") or attrs.get("display_attr_name") or "(unnamed)"),
                # `type` is "folder" for a folder and the app name for a file.
                kind=str(attrs.get("type") or "file"),
                owner=str(attrs.get("created_by") or attrs.get("owner") or ""),
            ))
        return out

    def read(self, file_id: str, name: str = "") -> FileText:
        url = self.api_domain().rstrip("/") + DOWNLOAD_PATH.format(file_id=file_id)
        try:
            response = self.client.get(
                url,
                # Zoho's own scheme, not Bearer. Bearer is accepted by the MCP
                # server and refused here, which reads as an auth failure rather
                # than a wrong header name.
                headers={"Authorization": f"Zoho-oauthtoken {self.token()}"},
                follow_redirects=True,
                # Per-request, so the shared client keeps its short timeout for
                # every listing and metadata call that also goes through it.
                timeout=DOWNLOAD_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as error:
            raise WorkDriveDirectError(f"could not reach WorkDrive: {error}") from None

        if response.status_code == 401:
            raise WorkDriveDirectError(
                "WorkDrive refused the token for file content. The connection needs "
                "WorkDrive.files.READ, and the request must go to the api_domain the "
                "token names -- download-accl.zoho.com refuses it."
            )
        if response.status_code == 404:
            raise WorkDriveDirectError(f"no file with id {file_id}")
        if response.status_code >= 400:
            raise WorkDriveDirectError(
                f"WorkDrive answered {response.status_code}: {response.text[:200]}"
            )

        data = response.content
        if len(data) > MAX_BYTES:
            raise WorkDriveDirectError(
                f"file is {len(data) // (1024 * 1024)}MB, past the {MAX_BYTES // (1024 * 1024)}MB "
                "limit for reading contents"
            )

        # The filename Zoho sends back, which carries the export format -- a Zoho
        # Sheet arrives as .xlsx and the listing's own `type` says `zohosheet`.
        disposition = response.headers.get("content-disposition", "")
        served = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", disposition)
        filename = (served.group(1) if served else "") or name or file_id

        # Format handling lives in document_text.py: it is the part that grows
        # with what people keep in a store, and none of it is about WorkDrive.
        try:
            got = extract(data, filename)
        except UnreadableDocument as error:
            raise WorkDriveDirectError(
                f"{error}. The file exists and can be opened in WorkDrive."
            ) from None

        table = {"columns": got.columns, "rows": got.rows,
                 "more_tables": got.more_tables}
        if len(got.text) > self.max_chars:
            return FileText(name=filename, text=got.text[: self.max_chars],
                            truncated=True, **table)
        return FileText(name=filename, text=got.text, truncated=False, **table)
