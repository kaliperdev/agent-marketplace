# Copied from rytangle router/app/tools/gdocs.py (Google Docs 1.x stays built into
# the router until every server runs the catalog). Only the helper imports differ.
"""Read-only Google Docs access via a service account.

The service account acts as itself: material must be SHARED WITH its
client_email exactly as it would be shared with a person -- a single folder is
enough, since access is inherited by everything inside it. There is no `sub`
impersonation claim and no domain-wide delegation.

Discovery lives in app.tools.drive; this module only reads content. There is no
allowlist of document ids: the boundary is Google's own ACL, so a document that
was never shared with the service account returns 403 and says so.

Token minting sits behind a TokenProvider callable so tests never touch
google-auth or the network.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence

import httpx
from langchain_core.tools import BaseTool, tool

from agent_kit.cache import DiskCache
from gdocs_agent.drive import DOC_MIME, DriveIndex, DriveItem, DriveListing
from agent_kit.rows import no_rows, table

DOCS_API = "https://docs.googleapis.com/v1/documents"
DOCS_SCOPE = "https://www.googleapis.com/auth/documents.readonly"

TokenProvider = Callable[[], str]

# Files this agent can see but cannot open. Named rather than hidden, so the
# agent can report "there is a spreadsheet I cannot read" instead of reporting
# that the material does not exist.
UNREADABLE_KINDS: dict[str, str] = {
    "application/vnd.google-apps.spreadsheet": "spreadsheet",
    "application/vnd.google-apps.presentation": "presentation",
    "application/vnd.google-apps.form": "form",
    "application/vnd.google-apps.drawing": "drawing",
    "application/pdf": "pdf",
    # Uploaded Office files. Named explicitly because the generic fallback used to
    # render a .docx as "document", which reads as a Google Doc that failed to open.
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx file",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx file",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx file",
    "application/msword": "doc file",
}


class DocsError(RuntimeError):
    """A Google Docs request was refused or returned unusable data."""


def flatten_document(payload: dict) -> str:
    """Google Docs JSON to plain text: title, paragraphs, and table cells."""
    parts: list[str] = []
    title = payload.get("title")
    if title:
        parts.append(f"{title}\n")
    for element in payload.get("body", {}).get("content", []):
        parts.extend(_flatten_element(element))
    return "".join(parts)


def _flatten_element(element: dict) -> list[str]:
    parts: list[str] = []
    paragraph = element.get("paragraph")
    if paragraph:
        for run in paragraph.get("elements", []):
            content = run.get("textRun", {}).get("content")
            if content:
                parts.append(content)
    table = element.get("table")
    if table:
        for row in table.get("tableRows", []):
            for cell in row.get("tableCells", []):
                for child in cell.get("content", []):
                    parts.extend(_flatten_element(child))
    return parts


def service_account_token_provider(sa_json_path: str, scopes: Sequence[str]) -> TokenProvider:
    """Mint access tokens from a service-account key file for the given scopes."""

    def provide() -> str:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        with open(sa_json_path, encoding="utf-8") as handle:
            info = json.load(handle)
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=list(scopes)
        )
        credentials.refresh(Request())
        return str(credentials.token)

    return provide


class DocsReader:
    def __init__(
        self,
        client: httpx.Client,
        token_provider: TokenProvider,
        cache: DiskCache,
    ) -> None:
        self.client = client
        self.token_provider = token_provider
        self.cache = cache

    def read_doc(self, doc_id: str, version: str) -> str:
        """Read one document. `version` is its Drive modifiedTime.

        The revision is part of the cache key because DiskCache never expires:
        keyed on the URL alone, an edited document would serve its old text for
        as long as the cache directory survives.
        """
        url = f"{DOCS_API}/{doc_id}"
        body = self.cache.get(f"{url}#{version}")
        if body is None:
            response = self.client.get(
                url, headers={"authorization": f"Bearer {self.token_provider()}"}
            )
            if response.status_code != 200:
                raise DocsError(
                    f"GET document {doc_id} returned {response.status_code}: {response.text[:200]}"
                )
            body = response.text
            self.cache.put(f"{url}#{version}", body)
        return flatten_document(json.loads(body))


def _describe_unreadable(items: Sequence[DriveItem]) -> str:
    counts: dict[str, int] = {}
    for item in items:
        label = UNREADABLE_KINDS.get(item.mime_type, item.mime_type.split("/")[-1])
        counts[label] = counts.get(label, 0) + 1
    parts = [
        f"{count} {label}{'s' if count != 1 else ''}"
        for label, count in sorted(counts.items())
    ]
    total = sum(counts.values())
    return f"{total} other file{'s' if total != 1 else ''} here cannot be read: {', '.join(parts)}"


def _format_listing(listing: DriveListing, empty_message: str, max_items: int) -> str:
    """Folders first, then docs, each sorted by name.

    Sorted because Drive's ordering is not guaranteed stable, and an unstable
    tool result makes an unstable prompt, which makes an unstable measurement.
    """
    folders = sorted((i for i in listing.items if i.is_folder), key=lambda i: i.name)
    docs = sorted((i for i in listing.items if i.is_doc), key=lambda i: i.name)
    others = [i for i in listing.items if not i.is_folder and not i.is_doc]

    # Built from the id already in hand, so neither costs a request. A folder and
    # a document open at DIFFERENT addresses: pointing a reader at a document URL
    # for a folder gives them a dead page.
    lines = [
        f"FOLDER\t{i.id}\t{i.name}\thttps://drive.google.com/drive/folders/{i.id}"
        for i in folders
    ]
    lines += [
        f"DOC\t{i.id}\t{i.name}\t(modified {i.modified[:10]})"
        f"\thttps://docs.google.com/document/d/{i.id}"
        for i in docs
    ]
    if others:
        lines.append(_describe_unreadable(others))
    if not lines:
        return empty_message
    if listing.truncated:
        lines.append(f"(listing capped at {max_items} items; there are more)")
    return "\n".join(lines)


BROWSE_COLUMNS = ["name", "id", "kind", "modified"]
DOC_COLUMNS = ["title", "id", "modified"]


def item_kind(item: DriveItem) -> str:
    """What a Drive entry is, in the same words the listing already prints."""
    if item.is_folder:
        return "folder"
    if item.mime_type == DOC_MIME:
        return "doc"
    return UNREADABLE_KINDS.get(item.mime_type, item.mime_type.split("/")[-1])


def browse_rows(listing: DriveListing) -> list[list]:
    return [
        [item.name, item.id, item_kind(item), str(item.modified or "")[:10]]
        for item in listing.items
    ]


def make_docs_tools(index: DriveIndex, reader: DocsReader) -> list[BaseTool]:
    @tool(response_format="content_and_artifact")
    def browse_drive(folder_id: str = "") -> tuple:
        """List what is in Drive. Call with no folder_id to see what is shared with
        you, then call it again with a folder's id to see inside that folder.
        Documents nested in subfolders are only visible by descending into them,
        so prefer search_docs when you know what you are looking for."""
        if folder_id:
            listing = index.children(folder_id)
            empty = f"empty: folder {folder_id} contains nothing"
        else:
            listing = index.shared_roots()
            empty = "empty: nothing is shared with this account"
        return (
            _format_listing(listing, empty, index.max_items),
            table(BROWSE_COLUMNS, browse_rows(listing), truncated=listing.truncated)
            if listing.items else no_rows(),
        )

    @tool(response_format="content_and_artifact")
    def search_docs(query: str) -> tuple:
        """Find Google Docs whose title or body contains a word or phrase. This
        searches every document you can reach, however deeply nested, so it is the
        fastest way to locate material. Returns ids to pass to read_google_doc."""
        listing = index.search_docs(query)
        if not listing.items:
            # Never a bare "no matches". A search that found nothing and a
            # search that asked the wrong thing read identically, and the second
            # is how "there is nothing" gets reported for material sitting right
            # there. jira already names what it ran; these four did not.
            return (
                f"no matches for: {query!r} across the documents shared with this "
                "account",
                no_rows(),
            )
        # No `kind` column here: this tool filters to documents, so it would be
        # the same word on every row.
        rows = [
            [item.name, item.id, str(item.modified or "")[:10]]
            for item in listing.items
        ]
        return (
            _format_listing(listing, "no matches", index.max_items),
            table(DOC_COLUMNS, rows, truncated=listing.truncated),
        )

    @tool
    def read_google_doc(doc_id: str) -> str:
        """Read one Google Doc's full text by the id shown by search_docs or
        browse_drive."""
        version = index.modified_time(doc_id)
        return reader.read_doc(doc_id, version)

    return [browse_drive, search_docs, read_google_doc]
