# Copied from rytangle router/app/tools/drive.py (Google Docs 1.x stays built into
# the router until every server runs the catalog). Unchanged.
"""Read-only Google Drive discovery: what documents exist, and where.

The Docs API has no list or search endpoint, so discovery is a different API
with a different scope. It stays in its own module: nothing here reads document
content, and nothing here writes.

Two facts, both verified against the live service account rather than taken from
documentation prose:

- `sharedWithMe` returns only the items shared with the account DIRECTLY. A
  folder shared with the account appears; the documents nested inside it do not,
  even though they are readable through inherited permission. Discovery that
  relied on `sharedWithMe` alone would therefore find folders and no documents.
- `fullText contains` DOES reach those nested documents, at any depth, in one
  call, and it works under `drive.metadata.readonly` -- the broader
  `drive.readonly` scope is not required. Search is what makes nesting a
  non-problem; browsing is only for orientation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx

DRIVE_API = "https://www.googleapis.com/drive/v3/files"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.metadata.readonly"

DOC_MIME = "application/vnd.google-apps.document"
FOLDER_MIME = "application/vnd.google-apps.folder"

LIST_FIELDS = "files(id,name,mimeType,modifiedTime),nextPageToken"
PAGE_SIZE = 100
# Raised from 200, which was a round number rather than a measured one. This
# service account can see 329 Google Docs, so every listing question was
# answering from 61% of the corpus -- honestly, because the cap announces itself,
# but partially. 500 clears the current corpus with headroom and stays a ceiling,
# so a much larger Drive still cannot flood the aggregator.
#
# It costs nothing on the common path: a search that matches a handful of
# documents never approaches the cap, and only a "list everything" question pays
# for the difference. Measured on such a question at 200: the agent emitted 9,136
# output tokens and the answer ran to 18,324 characters.
MAX_ITEMS = 500

TokenProvider = Callable[[], str]


class DriveError(RuntimeError):
    """A Drive request was refused or returned unusable data."""


@dataclass(frozen=True)
class DriveItem:
    id: str
    name: str
    mime_type: str
    modified: str

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME

    @property
    def is_doc(self) -> bool:
        return self.mime_type == DOC_MIME


@dataclass(frozen=True)
class DriveListing:
    items: list[DriveItem]
    truncated: bool


def escape_query_value(value: str) -> str:
    """Escape a user string for a Drive query literal.

    The model chooses the search term, so an unescaped apostrophe would end the
    literal early and the rest of the term would be parsed as query syntax.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


class DriveIndex:
    def __init__(
        self,
        client: httpx.Client,
        token_provider: TokenProvider,
        max_items: int = MAX_ITEMS,
    ) -> None:
        self.client = client
        self.token_provider = token_provider
        self.max_items = max_items

    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.token_provider()}"}

    def _list(self, query: str) -> DriveListing:
        items: list[DriveItem] = []
        truncated = False
        page_token: str | None = None

        while True:
            params = {
                "q": query,
                "fields": LIST_FIELDS,
                "pageSize": str(PAGE_SIZE),
                # Sent unconditionally so adding the service account to a Shared
                # Drive later needs no code change.
                "includeItemsFromAllDrives": "true",
                "supportsAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token

            response = self.client.get(DRIVE_API, params=params, headers=self._headers())
            if response.status_code != 200:
                raise DriveError(
                    f"drive query {query!r} returned {response.status_code}: {response.text[:200]}"
                )
            payload = response.json()
            for raw in payload.get("files", []):
                items.append(
                    DriveItem(
                        id=raw["id"],
                        name=raw.get("name", ""),
                        mime_type=raw.get("mimeType", ""),
                        modified=raw.get("modifiedTime", ""),
                    )
                )

            page_token = payload.get("nextPageToken")
            if len(items) >= self.max_items:
                truncated = len(items) > self.max_items or bool(page_token)
                del items[self.max_items :]
                break
            if not page_token:
                break

        return DriveListing(items=items, truncated=truncated)

    def shared_roots(self) -> DriveListing:
        """The items shared with this account directly -- typically folders."""
        return self._list("sharedWithMe and trashed=false")

    def children(self, folder_id: str) -> DriveListing:
        return self._list(f"'{escape_query_value(folder_id)}' in parents and trashed=false")

    def search_docs(self, query: str) -> DriveListing:
        return self._list(
            f"fullText contains '{escape_query_value(query)}' "
            f"and mimeType='{DOC_MIME}' and trashed=false"
        )

    def modified_time(self, file_id: str) -> str:
        """The document's revision stamp, used as its content cache key."""
        response = self.client.get(
            f"{DRIVE_API}/{file_id}",
            params={"fields": "modifiedTime", "supportsAllDrives": "true"},
            headers=self._headers(),
        )
        if response.status_code != 200:
            raise DriveError(
                f"file {file_id} returned {response.status_code}: {response.text[:200]}"
            )
        modified = response.json().get("modifiedTime")
        if not modified:
            raise DriveError(f"file {file_id} returned no modifiedTime")
        return str(modified)
