# Copied from rytangle router/app/tools/github.py (GitHub 1.x stays built into
# the router until every server runs the catalog). Only the two helper imports differ.
"""Read-only GitHub access.

Hand-rolled rather than langchain_community's GitHubToolkit, which targets write
operations (issues, pull requests, comments) and expects GitHub App credentials.
Nothing here writes.

Rate limits on the live token: core 5000/hour, search 30/minute, code search
10/minute. Code search is the binding constraint, so the tool descriptions steer
the model toward tree listing plus file reads, and every response is cached.

Every cached response is keyed by the commit it came from. This is not an
optimisation, it is a correctness requirement found the hard way: the key used to
be the request URL, the tree URL contained the literal string "HEAD", and
DiskCache has no expiry -- so once the agent read a repository it answered from
that snapshot for ever. Asked whether a file committed a minute earlier existed,
it said no. The revision lookup itself is the one request that must never be
cached, or the whole scheme pins itself to the first commit it ever saw.
"""

from __future__ import annotations

import base64
import json

import time
from collections.abc import Callable

import httpx
from langchain_core.tools import BaseTool, tool

from agent_kit.cache import DiskCache
from agent_kit.rows import no_rows, table

GITHUB_API = "https://api.github.com"
MAX_FILE_CHARS = 40_000
MAX_ISSUE_BODY_CHARS = 2_000


class GitHubError(RuntimeError):
    """A GitHub request did not return usable data."""


# How long one commit is read before HEAD is looked up again.
REVISION_TTL_SECONDS = 60.0


class GitHubReader:
    def __init__(
        self,
        repo: str,
        client: httpx.Client,
        cache: DiskCache,
        token: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.repo = repo
        self.client = client
        self.cache = cache
        self.token = token
        self._revision: str | None = None
        self._clock = clock
        self._resolved_at = 0.0

    @property
    def revision(self) -> str:
        """The commit reads come from, held in memory for a minute.

        Memoised rather than disk-cached: one live request a minute at most
        against a 5000/hour core limit, and holding one commit for that long means
        a question cannot see a half-updated repository if someone pushes
        mid-question. Held for good, a reader that lives for days (a router
        process, an agent service) never saw another commit.
        """
        if self._revision is None or self._clock() - self._resolved_at >= REVISION_TTL_SECONDS:
            payload = json.loads(self._fetch(f"/repos/{self.repo}/commits/HEAD"))
            sha = payload.get("sha")
            if not sha:
                raise GitHubError(f"no sha in the HEAD commit response for {self.repo}")
            self._revision = str(sha)
            self._resolved_at = self._clock()
        return self._revision

    def _fetch(self, path: str, params: dict[str, str] | None = None) -> str:
        """One live request. No caching -- see _get for the cached path."""
        headers = {"accept": "application/vnd.github+json"}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        response = self.client.get(f"{GITHUB_API}{path}", params=params or {}, headers=headers)
        if response.status_code != 200:
            raise GitHubError(f"GET {path} returned {response.status_code}: {response.text[:200]}")
        return response.text

    def _get(self, path: str, params: dict[str, str] | None = None) -> str:
        request = self.client.build_request(
            "GET", f"{GITHUB_API}{path}", params=params or {}
        )
        # The revision is prefixed rather than left to the URL: /search/code takes no
        # ref parameter, so its URL is identical at every commit and only an explicit
        # prefix makes its key move.
        key = f"{self.revision}#{request.url}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        body = self._fetch(path, params)
        self.cache.put(key, body)
        return body

    def list_tree(self) -> list[str]:
        payload = json.loads(
            self._get(f"/repos/{self.repo}/git/trees/{self.revision}", {"recursive": "1"})
        )
        return [entry["path"] for entry in payload.get("tree", []) if entry.get("type") == "blob"]

    def read_file(self, path: str) -> str:
        payload = json.loads(
            self._get(f"/repos/{self.repo}/contents/{path}", {"ref": self.revision})
        )
        if payload.get("encoding") != "base64":
            raise GitHubError(f"{path}: unexpected encoding {payload.get('encoding')!r}")
        text = base64.b64decode(payload["content"]).decode("utf-8", errors="replace")
        return text[:MAX_FILE_CHARS]

    def search_code(self, query: str) -> list[str]:
        payload = json.loads(self._get("/search/code", {"q": f"{query} repo:{self.repo}"}))
        return [item["path"] for item in payload.get("items", [])]

    def list_issues(self, limit: int = 20) -> list[dict]:
        payload = json.loads(
            self._get(f"/repos/{self.repo}/issues", {"state": "all", "per_page": str(limit)})
        )
        return [
            {
                "number": issue["number"],
                "title": issue["title"],
                "state": issue["state"],
                "body": (issue.get("body") or "")[:MAX_ISSUE_BODY_CHARS],
                # All already in the response, and none of them reach the text
                # half. `kind` is what makes "pull requests merged per week"
                # answerable at all: /issues returns both, and nothing else in
                # the payload tells them apart.
                "kind": "pr" if issue.get("pull_request") else "issue",
                "author": ((issue.get("user") or {}).get("login") or ""),
                "created": str(issue.get("created_at") or "")[:10],
                "closed": str(issue.get("closed_at") or "")[:10],
                "labels": ", ".join(
                    label.get("name", "")
                    for label in (issue.get("labels") or [])
                    if isinstance(label, dict)
                ),
            }
            for issue in payload
        ]


def _from_repo(reader: "GitHubReader", body: str) -> str:
    """Stamp every read with the repository it came from.

    The prompt already tells the agent which repository it owns, but a prompt is
    an instruction and this is evidence: the model sees the name in the same place
    it sees the file list. Measured need -- asked about
    kaliperdev/onboarding-verify-throwaway, the agent read the configured repo and
    summarised it as though it were the one named, because nothing it saw ever
    said otherwise.
    """
    return f"Repository: {reader.repo}\n\n{body}"


FILE_COLUMNS = ["path", "directory", "extension"]
ISSUE_COLUMNS = ["number", "kind", "state", "title", "author", "created",
                 "closed", "labels"]


def file_rows(paths: list[str]) -> list[list]:
    """A path split into the parts somebody groups by.

    Directory and extension are derived rather than asked for, because "files by
    type" is otherwise unchartable.
    """
    rows = []
    for path in paths:
        head, _, tail = path.rpartition("/")
        _, dot, suffix = tail.rpartition(".")
        rows.append([path, head, suffix.lower() if dot else ""])
    return rows


def make_github_tools(reader: GitHubReader) -> list[BaseTool]:
    @tool(response_format="content_and_artifact")
    def list_repo_files() -> tuple:
        """List every file path in the repository. Call this first to orient yourself."""
        paths = reader.list_tree()
        # The text is byte-for-byte what it was, empty repository included.
        return (
            _from_repo(reader, "\n".join(paths)),
            table(FILE_COLUMNS, file_rows(paths), truncated=False) if paths else no_rows(),
        )

    @tool
    def read_repo_file(path: str) -> str:
        """Read one file from the repository by its exact path, as listed by list_repo_files."""
        return _from_repo(reader, reader.read_file(path))

    @tool(response_format="content_and_artifact")
    def search_repo_code(query: str) -> tuple:
        """Find files containing a keyword. Prefer read_repo_file when you already
        know the path: this search is rate-limited to 10 calls per minute."""
        paths = reader.search_code(query)
        if not paths:
            # Names the repository as well as the query: this agent owns exactly
            # one, and "no matches" alone has been read as "the code is not
            # there" when it meant "not in the repo I am configured for".
            return f"no matches for: {query!r} in {reader.repo}", no_rows()
        # The blob URL is built, not fetched, and pinned to the revision this
        # reader already resolved -- so the link opens the exact commit that was
        # read rather than whatever HEAD happens to be later.
        base = f"https://github.com/{reader.repo}/blob/{reader.revision}"
        listed = "\n".join(f"{path}\t{base}/{path}" for path in paths)
        return listed, table(FILE_COLUMNS, file_rows(paths), truncated=False)

    @tool(response_format="content_and_artifact")
    def list_repo_issues() -> tuple:
        """List recent issues and pull requests with their titles and bodies."""
        issues = reader.list_issues()
        text = "\n\n".join(
            f"#{issue['number']} [{issue['state']}] {issue['title']}"
            f"  https://github.com/{reader.repo}/issues/{issue['number']}"
            f"\n{issue['body']}"
            for issue in issues
        )
        if not issues:
            return text, no_rows()
        rows = [
            [issue["number"], issue["kind"], issue["state"], issue["title"],
             issue["author"], issue["created"], issue["closed"], issue["labels"]]
            for issue in issues
        ]
        # `body` is deliberately not a column: up to 2,000 characters each, and it
        # would be most of the payload while charting nothing.
        return text, table(ISSUE_COLUMNS, rows, truncated=len(issues) >= 20)

    return [list_repo_files, read_repo_file, search_repo_code, list_repo_issues]
