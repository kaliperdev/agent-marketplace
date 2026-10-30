"""Setup guide screenshots: what they are and how big, read from their own bytes.

A guide step may show a screenshot (connection.guide.steps[].image). The file
sits beside its entry (agents/<id>/guide/<file>), and the catalog keeps it by
its SHA-256, so a published version's pictures can never change underneath it.
Only WebP and PNG are taken: both are drawn by every browser, and neither can
carry script the way an SVG can.
"""

from __future__ import annotations

import hashlib
import struct

MAX_IMAGE_BYTES = 512 * 1024


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def media_type(data: bytes) -> str | None:
    """image/webp or image/png, from the file's first bytes; None for anything else."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def dimensions(data: bytes) -> tuple[int, int] | None:
    """(width, height) from the header, or None when it cannot be read."""
    kind = media_type(data)
    try:
        if kind == "image/png" and data[12:16] == b"IHDR":
            return struct.unpack(">II", data[16:24])
        if kind == "image/webp":
            chunk = data[12:16]
            if chunk == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
                width, height = struct.unpack("<HH", data[26:30])
                return width & 0x3FFF, height & 0x3FFF
            if chunk == b"VP8L" and data[20] == 0x2F:
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if chunk == b"VP8X":
                return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
    except (IndexError, struct.error):
        return None
    return None


def problem(data: bytes, width: int | None = None, height: int | None = None) -> str | None:
    """Why these bytes cannot be a guide screenshot, or None when they can."""
    if len(data) > MAX_IMAGE_BYTES:
        return f"is {len(data) // 1024} KB; a screenshot may be at most {MAX_IMAGE_BYTES // 1024} KB (WebP keeps it small)"
    if media_type(data) is None:
        return "is not a WebP or PNG image"
    size = dimensions(data)
    if size is None:
        return "has an image header that cannot be read"
    if width is not None and (width, height) != size:
        return f"is {size[0]}×{size[1]}, not the {width}×{height} its entry says"
    return None
