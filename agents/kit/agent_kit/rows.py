"""The rows a tool hands back alongside the text it hands to the model.

A tool declaring `response_format="content_and_artifact"` returns a two-tuple:
element one is the string the model reads -- byte-for-byte what the tool returned
before this existed -- and element two is this payload, which LangChain puts on
`ToolMessage.artifact`, where no prompt ever reaches it.

Every row-emitting tool builds that payload here rather than writing a dict
literal, so twelve producers cannot drift into twelve spellings of the same
shape.

`truncated` is keyword-only and has no default, deliberately. A source that
capped its results and did not say so produces a page presenting a slice as the
whole set -- an authoritative-looking wrong answer, which is worse than no page.
Being wrong toward "there may be more" costs a sentence; being wrong toward
"this is everything" costs a decision. The signature makes silence a TypeError
rather than an oversight.
"""

from __future__ import annotations

from typing import Any, Sequence


def table(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    truncated: bool,
) -> dict:
    """One tool result as a table.

    Copied rather than aliased: a reader that goes on mutating its own list must
    not be able to edit a payload it has already handed over. Normalised to plain
    lists so the fingerprint that deduplicates datasets sees one spelling of a
    row, not a tuple and a list that JSON would have made identical anyway.
    """
    return {
        "columns": [str(column) for column in columns],
        "rows": [list(row) for row in rows],
        "truncated": bool(truncated),
    }


def no_rows() -> dict:
    """The payload for a return path that produced no table.

    Refusals, caught errors and empty results all need one: declaring
    content_and_artifact makes the two-tuple mandatory on every return path, not
    just the happy one.

    A function rather than a shared constant so that one caller mutating what it
    was given cannot alter what every later caller gets.
    """
    return {"columns": [], "rows": [], "truncated": False}


def nothing_found(what: str, where: str = "") -> tuple[str, dict]:
    """What a search tool returns when it found nothing: the text and no rows.

    The words are the router's, not a style choice: "no matches for:" in an
    agent's answer is how the router knows a search came back empty, so it can
    tell the reader what was searched and plan once more (rytangle
    router/app/evaluate.py, BARREN). Say what was searched, as the person
    would recognise it, and where.
    """
    text = f"no matches for: {what!r}" + (f" in {where}" if where else "")
    return text, no_rows()
