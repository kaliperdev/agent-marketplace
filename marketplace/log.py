"""One log line per event, the same shape in every service:

  2026-10-02T15:44:25.123Z INFO [mcp] call tool=echo request=slack-1a2b ms=12 ok=true

INFO goes to stdout, WARN and ERROR to stderr. Text from an upstream service
goes through redact() first: it can carry a token or a signed link.
"""

from __future__ import annotations

import re
import sys
import threading
from datetime import datetime, timezone

LEVELS = ("INFO", "WARN", "ERROR")
MAX_TEXT = 300

# Order matters: the labelled forms run before the bare token shapes.
_PATTERNS = [
    (re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*)(?:bearer\s+|basic\s+)?[^\s,;\"']+"), r"\1[redacted]"),
    (re.compile(r"(?i)\b(bearer\s+)[^\s,;\"']+"), r"\1[redacted]"),
    # A link's query string can hold a signature under any name: drop all of it.
    (re.compile(r"(https?://[^\s?#\"'<>]+)\?[^\s\"'<>]*"), r"\1?[redacted]"),
    (re.compile(r"(?i)([\w-]*(?:token|key|secret|signature|password))(=|\"?\s*:\s*\"?)[^&\s\"',;<>]+"),
     r"\1\2[redacted]"),
    (re.compile(r"xox[bapsr]-[\w-]+"), "[redacted]"),
    (re.compile(r"gh[pousr]_\w+"), "[redacted]"),
    (re.compile(r"sk-[\w-]{8,}"), "[redacted]"),
    (re.compile(r"1000\.[0-9a-fA-F]{32}\.[0-9a-fA-F]{32}"), "[redacted]"),
    (re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+"), "[redacted]"),
]


def redact(text: object, limit: int = MAX_TEXT) -> str:
    """`text` with token shapes hidden, cut to `limit` characters."""
    out = str(text)
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out if len(out) <= limit else out[:limit] + "…"


def _value(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if not text:
        return '""'
    if any(c.isspace() or c in '"=' for c in text):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")
        return f'"{escaped}"'
    return text


_WRITING = threading.Lock()


def log(level: str, component: str, event: str, **fields: object) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    line = f"{stamp} {level} [{component}] {event}"
    for key, value in fields.items():
        line += f" {key}={_value(value)}"
    stream = sys.stdout if level == "INFO" else sys.stderr
    # One write per line, under a lock: print() writes the text and the "\n"
    # separately, and with unbuffered output (PYTHONUNBUFFERED=1) a line from
    # another thread could land between them -- two tool calls logged in the
    # same millisecond came out as one line.
    with _WRITING:
        stream.write(line + "\n")
        stream.flush()
