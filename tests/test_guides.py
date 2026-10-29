"""connection.guide: the steps to get what a connection form asks for
(docs/connection-guides.md), and the rule that a new version asking for a key,
token or file must carry one."""

import copy
import datetime
import json
from pathlib import Path

import pytest

from marketplace.entry import needs_guide, ordered, publish_problems, validate

ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR, PUBLISHED_DIR = ROOT / "agents", ROOT / "published"


def _gdrive():
    return json.loads((AGENTS_DIR / "gdrive" / "catalog-1.0.2.json").read_text(encoding="utf-8"))


def _with_guide(mutate):
    entry = _gdrive()
    mutate(entry["connection"]["guide"])
    return validate(entry)


def test_the_google_drive_guide_is_valid_and_written_in_order():
    entry = _gdrive()
    assert validate(entry) == [] and publish_problems(entry) == []
    assert json.dumps(ordered(entry)) == json.dumps(entry)


def test_a_guide_is_needed_for_a_key_token_or_file_and_for_an_own_app():
    entry = _gdrive()
    assert needs_guide(entry)
    del entry["connection"]["guide"]
    assert validate(entry) == []
    assert any("needs connection.guide" in m for m in publish_problems(entry))
    entry["connection"]["fields"][0]["type"] = "text"
    assert not needs_guide(entry)
    asana = json.loads((AGENTS_DIR / "asana" / "catalog-1.0.0.json").read_text(encoding="utf-8"))
    assert needs_guide(asana)
    notion = json.loads((AGENTS_DIR / "notion" / "catalog-1.0.1.json").read_text(encoding="utf-8"))
    assert not needs_guide(notion)


@pytest.mark.parametrize("mutate, message", [
    (lambda g: g.pop("title"), "guide.title"),
    (lambda g: g.update(summary="x" * 321), "guide.summary"),
    (lambda g: g.update(needs=[]), "guide.needs"),
    (lambda g: g.update(steps=g["steps"][:1]), "2 to 10 steps"),
    (lambda g: g["steps"][0].pop("text"), "step 1 needs its text"),
    (lambda g: g["steps"][0].update(unknown=1), "unknown key 'unknown'"),
    (lambda g: g["steps"][1].update(link={"label": "Open", "url": "http://console.cloud.google.com"}), "step 2: link"),
    (lambda g: g["steps"][1].update(link={"label": "Open", "url": "javascript:alert(1)"}), "step 2: link"),
    (lambda g: g["steps"][2].update(path=[]), "step 3: path"),
    (lambda g: g["steps"][1].update(copy=["a`b"]), "step 2: copy"),
    (lambda g: g["steps"][0].update(text="An `unclosed code span"), "step 1 needs its text"),
    (lambda g: g["trouble"].append({"problem": "No fix"}), "trouble item"),
    (lambda g: g.update(source={"label": "Docs", "url": "https://10.0.0.1/docs"}), "guide.source"),
    (lambda g: g.update(checked="30/10/2026"), "guide.checked"),
    (lambda g: g.update(checked=(datetime.date.today() + datetime.timedelta(days=1)).isoformat()), "guide.checked"),
])
def test_a_malformed_guide_is_refused(mutate, message):
    assert any(message in m for m in _with_guide(mutate)), message


def test_a_guide_is_plain_text_never_markup():
    # The page escapes every string before drawing it; links are https and the
    # vendor's own, so a guide can carry no script or tracking pixel.
    guide = _gdrive()["connection"]["guide"]
    text = json.dumps(guide)
    assert "<" not in text and ">" not in text
    links = [s["link"]["url"] for s in guide["steps"] if "link" in s] + [guide["source"]["url"]]
    assert links and all(url.startswith("https://") for url in links)


def _unpublished():
    for path in sorted(AGENTS_DIR.glob("*/catalog-*.json")):
        if not (PUBLISHED_DIR / path.parent.name / f"{path.name[len('catalog-'):]}").exists():
            yield path


@pytest.mark.parametrize("path", list(_unpublished()), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_version_not_yet_published_can_be_published(path):
    assert publish_problems(json.loads(path.read_text(encoding="utf-8"))) == []
