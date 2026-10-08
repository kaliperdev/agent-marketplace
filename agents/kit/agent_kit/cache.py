"""Content-addressed cache for HTTP responses.

GitHub code search allows 10 requests per minute, so repeated questions over the
same files must not re-hit the API. Keys are hashed rather than used as paths, so
a key containing '..' or '?' cannot escape the cache root.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


class DiskCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / digest[:2] / f"{digest}.txt"

    def get(self, key: str) -> str | None:
        path = self._path(key)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def put(self, key: str, value: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
