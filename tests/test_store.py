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


def _publish_versions(conn, entries, agent_id, versions):
    for version in versions:
        entry = _jira(entries)
        entry["id"] = entry["router"]["agent"]["name"] = agent_id
        entry["version"] = version
        entry["display"]["summary"] = f"{agent_id} {version}"
        store.publish(conn, entry, "sahil")


def test_all_versions_lists_every_agent_in_number_order(conn, entries):
    _publish_versions(conn, entries, "jira", ["1.0.0", "1.9.0", "1.10.0"])
    _publish_versions(conn, entries, "other", ["0.1.0", "0.2.0"])
    assert store.all_versions(conn) == {
        "jira": [
            {"version": "1.0.0", "needsRouter": "0.1.0"},
            {"version": "1.9.0", "needsRouter": "0.1.0"},
            {"version": "1.10.0", "needsRouter": "0.1.0"},
        ],
        "other": [
            {"version": "0.1.0", "needsRouter": "0.1.0"},
            {"version": "0.2.0", "needsRouter": "0.1.0"},
        ],
    }


def test_all_versions_matches_versions_for_each_agent(conn, entries):
    _publish_versions(conn, entries, "jira", ["1.0.0", "1.10.0"])
    _publish_versions(conn, entries, "other", ["2.0.0"])
    assert store.all_versions(conn) == {a: store.versions(conn, a) for a in ["jira", "other"]}


def test_all_versions_of_an_agent_with_one_version(conn, entries):
    store.publish(conn, _jira(entries), "sahil")
    assert store.all_versions(conn) == {"jira": [{"version": "1.0.0", "needsRouter": "0.1.0"}]}


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


def test_every_version_is_listed_with_the_router_it_needs_in_number_order(conn, entries):
    for version, needs in (("1.0.0", "0.1.0"), ("1.9.0", "0.2.0"), ("1.10.0", "0.3.0")):
        entry = _jira(entries)
        entry.update({"version": version, "needs_router": needs})
        store.publish(conn, entry, "sahil")
    assert store.versions(conn, "jira") == [
        {"version": "1.0.0", "needsRouter": "0.1.0"},
        {"version": "1.9.0", "needsRouter": "0.2.0"},
        {"version": "1.10.0", "needsRouter": "0.3.0"},
    ]


def test_a_published_version_cannot_be_edited_in_the_database_directly(conn, entries):
    # Twice, a version was changed in a database client (TablePlus), silently
    # changing what client servers had already copied. Publish a new version instead.
    import psycopg

    store.publish(conn, _jira(entries), "sahil")
    with pytest.raises(psycopg.Error, match="publish a new version"):
        conn.execute("update agent_versions set entry = jsonb_set(entry, '{publisher}', '\"X\"') "
                     "where agent_id = 'jira'")
    assert store.get_entry(conn, "jira")["publisher"] == "Kaliper"


@pytest.mark.parametrize("statement", ["delete from agent_versions where agent_id = 'jira'",
                                       "truncate agent_versions cascade"], ids=["delete", "truncate"])
def test_a_published_version_cannot_be_deleted_and_republished_differently(conn, entries, statement):
    # Deleting the row would let the same version be published again with other
    # content, which client servers that copied it would never notice.
    import psycopg

    store.publish(conn, _jira(entries), "sahil")
    with pytest.raises(psycopg.Error, match="publish a new version"):
        conn.execute(statement)
    conn.rollback()
    assert store.get_entry(conn, "jira")["publisher"] == "Kaliper"


def attempts(conn):
    return conn.execute(
        "select agent_id, version, outcome, reason, by from publish_attempts order by id"
    ).fetchall()


def test_every_publish_attempt_is_recorded_with_its_outcome(conn, entries):
    entry = entries[0]
    newer = {**entry, "version": "1.2.0"}
    older = {**entry, "version": "1.1.0"}
    changed = {**newer, "display": {**newer["display"], "name": "Changed"}}
    invalid = {**entry, "version": "not-a-version"}

    assert store.publish(conn, entry, "ann") == "published"
    assert store.publish(conn, entry, "ann") == "unchanged"
    assert store.publish(conn, newer, "bob") == "published"
    with pytest.raises(store.OlderVersionError):
        store.publish(conn, older, "bob")
    with pytest.raises(store.CatalogError):
        store.publish(conn, changed, "cy")
    with pytest.raises(store.CatalogError):
        store.publish(conn, invalid, "cy")

    rows = attempts(conn)
    assert [(r[0], r[1], r[2], r[4]) for r in rows] == [
        (entry["id"], entry["version"], "published", "ann"),
        (entry["id"], entry["version"], "unchanged", "ann"),
        (entry["id"], "1.2.0", "published", "bob"),
        (entry["id"], "1.1.0", "older", "bob"),
        (entry["id"], "1.2.0", "refused", "cy"),
        (entry["id"], "not-a-version", "refused", "cy"),
    ]
    assert rows[0][3] is None
    assert "older than the latest" in rows[3][3]
    assert "different content" in rows[4][3]
    assert conn.execute("select count(*) from publish_attempts where at is null").fetchone()[0] == 0


def test_an_entry_without_an_id_is_still_recorded(conn):
    with pytest.raises(store.CatalogError):
        store.publish(conn, {"nonsense": True}, "cy")
    assert attempts(conn)[0][2] == "refused"
