import copy

import pytest

from marketplace import store


def _jira(entries):
    return copy.deepcopy(next(e for e in entries if e["id"] == "jira"))


def test_a_new_agent_is_published_and_listed(conn, entries):
    assert store.publish(conn, _jira(entries), "sahil") == "published"
    assert [e["id"] for e in store.latest_entries(conn)] == ["jira"]


def test_publishing_the_same_entry_again_changes_nothing(conn, entries):
    store.publish(conn, _jira(entries), "sahil")
    assert store.publish(conn, _jira(entries), "sahil") == "unchanged"
    assert len(store.history(conn, "jira")) == 1


def test_a_changed_entry_that_kept_its_version_is_refused(conn, entries):
    store.publish(conn, _jira(entries), "sahil")
    edited = _jira(entries)
    edited["display"]["summary"] = "Something else"
    with pytest.raises(store.CatalogError, match="bump the version"):
        store.publish(conn, edited, "sahil")
    assert store.get_entry(conn, "jira")["display"]["summary"] == _jira(entries)["display"]["summary"]


def test_a_new_version_becomes_the_latest_and_the_old_one_is_kept(conn, entries):
    store.publish(conn, _jira(entries), "sahil")
    newer = _jira(entries)
    newer["version"] = "1.1.0"
    newer["display"]["summary"] = "Reads Jira."
    assert store.publish(conn, newer, "sahil") == "published"
    assert store.get_entry(conn, "jira")["version"] == "1.1.0"
    assert store.get_entry(conn, "jira", "1.0.0")["display"]["summary"] == _jira(entries)["display"]["summary"]
    assert [row[0] for row in store.history(conn, "jira")] == ["1.0.0", "1.1.0"]


def test_an_older_version_is_refused(conn, entries):
    newer = _jira(entries)
    newer["version"] = "1.1.0"
    store.publish(conn, newer, "sahil")
    with pytest.raises(store.CatalogError, match="older"):
        store.publish(conn, _jira(entries), "sahil")


def test_versions_are_ordered_as_numbers(conn, entries):
    for version in ["1.9.0", "1.10.0"]:
        entry = _jira(entries)
        entry["version"] = version
        entry["display"]["summary"] = f"version {version}"
        store.publish(conn, entry, "sahil")
    assert store.get_entry(conn, "jira")["version"] == "1.10.0"


def test_an_invalid_entry_is_refused_and_nothing_is_written(conn, entries):
    bad = _jira(entries)
    bad["connection"]["fields"].pop()
    with pytest.raises(store.CatalogError, match="JIRA_API_TOKEN"):
        store.publish(conn, bad, "sahil")
    assert store.latest_entries(conn) == []


def test_the_catalog_keeps_the_order_agents_were_first_published(conn, entries):
    for entry in entries:
        store.publish(conn, entry, "sahil")
    assert [e["id"] for e in store.latest_entries(conn)] == [e["id"] for e in entries]


def test_an_unknown_agent_has_no_entry_and_no_history(conn):
    assert store.get_entry(conn, "ghost") is None
    assert store.history(conn, "ghost") == []


def test_a_version_written_differently_cannot_republish_the_same_number(conn, entries):
    store.publish(conn, _jira(entries), "sahil")
    sneaky = _jira(entries)
    sneaky["version"] = "1.0.00"
    sneaky["display"]["summary"] = "Something else"
    with pytest.raises(store.CatalogError):
        store.publish(conn, sneaky, "sahil")
    assert [row[0] for row in store.history(conn, "jira")] == ["1.0.0"]


def test_entries_come_back_in_the_canonical_key_order(conn, entries):
    store.publish(conn, _jira(entries), "sahil")
    entry = store.get_entry(conn, "jira")
    assert list(entry) == ["format", "id", "version", "kind", "needs_router", "publisher", "display", "connection", "router"]
    assert list(entry["router"]["agent"])[:5] == ["name", "source", "tools", "description", "owns"]
    assert list(store.latest_entries(conn)[0]) == list(entry)
