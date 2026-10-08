import json

from conftest import FIX

from marketplace import cli


def run(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def seed(capsys):
    return run(capsys, "seed", "--registry", str(FIX / "agents.json"), "--extras", str(FIX / "extras.json"), "--by", "sahil")


def test_seed_publishes_every_agent_once_and_is_safe_to_rerun(catalog_env, capsys):
    code, out, _ = seed(capsys)
    assert code == 0 and out.count(": published") == 8
    code, out, _ = seed(capsys)
    assert code == 0 and out.count(": unchanged") == 8


def test_list_shows_every_agent(catalog_env, capsys):
    seed(capsys)
    code, out, _ = run(capsys, "list")
    assert code == 0
    assert len(out.strip().splitlines()) == 8
    assert "jira" in out


def test_export_edit_import_publishes_a_new_version(catalog_env, capsys, tmp_path):
    seed(capsys)
    _, out, _ = run(capsys, "export", "jira")
    entry = json.loads(out)
    entry["display"]["summary"] = "Reads Jira tickets."
    entry["version"] = "1.0.1"
    edited = tmp_path / "jira.json"
    edited.write_text(json.dumps(entry), encoding="utf-8")
    code, out, _ = run(capsys, "import", str(edited), "--by", "sahil")
    assert code == 0 and "jira 1.0.1: published" in out
    _, out, _ = run(capsys, "history", "jira")
    assert [line.split()[0] for line in out.strip().splitlines()] == ["1.0.0", "1.0.1"]


def test_importing_an_edit_without_a_new_version_is_refused(catalog_env, capsys, tmp_path):
    seed(capsys)
    _, out, _ = run(capsys, "export", "jira")
    entry = json.loads(out)
    entry["display"]["summary"] = "Reads Jira tickets."
    edited = tmp_path / "jira.json"
    edited.write_text(json.dumps(entry), encoding="utf-8")
    code, _, err = run(capsys, "import", str(edited))
    assert code == 2
    assert "bump the version" in err


def test_export_router_rebuilds_the_original_agents_json(catalog_env, capsys):
    seed(capsys)
    code, out, _ = run(capsys, "export-router")
    assert code == 0
    assert json.loads(out) == json.loads((FIX / "agents.json").read_text(encoding="utf-8"))


def test_exporting_an_unknown_agent_says_so(catalog_env, capsys):
    code, _, err = run(capsys, "export", "ghost")
    assert code == 1 and "ghost" in err


def test_importing_a_file_that_is_not_json_is_refused(catalog_env, capsys, tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    code, _, err = run(capsys, "import", str(broken))
    assert code == 2 and "broken.json" in err


def test_export_router_is_stable_and_in_the_routers_own_key_order(catalog_env, capsys):
    seed(capsys)
    _, first, _ = run(capsys, "export-router")
    _, second, _ = run(capsys, "export-router")
    assert first == second
    data = json.loads(first)
    assert all(list(a)[:5] == ["name", "source", "tools", "description", "owns"] for a in data["agents"])
    assert all(list(s)[:2] == ["tools_factory", "answerer_factory"] for s in data["sources"].values())


def test_importing_a_file_that_is_not_an_object_says_so(catalog_env, capsys, tmp_path):
    odd = tmp_path / "list.json"
    odd.write_text("[1, 2]", encoding="utf-8")
    code, _, err = run(capsys, "import", str(odd))
    assert code == 2 and "JSON object" in err


def test_seed_files_in_the_wrong_shape_are_refused_cleanly(catalog_env, capsys, tmp_path):
    bad = tmp_path / "agents.json"
    bad.write_text('{"agents": "nope"}', encoding="utf-8")
    code, _, err = run(capsys, "seed", "--registry", str(bad), "--extras", str(FIX / "extras.json"))
    assert code == 2 and "refused" in err


def test_a_catalog_without_tables_points_to_init_db(catalog_env, capsys):
    catalog_env.execute("drop table if exists agent_versions, agents cascade")
    code, _, err = run(capsys, "list")
    assert code == 3 and "init-db" in err


def test_reseeding_after_a_version_bump_skips_that_agent_and_finishes(catalog_env, capsys, tmp_path):
    seed(capsys)
    _, out, _ = run(capsys, "export", "jira")
    entry = json.loads(out)
    entry["version"] = "1.0.1"
    entry["display"]["summary"] = "Reads Jira tickets."
    newer = tmp_path / "jira.json"
    newer.write_text(json.dumps(entry), encoding="utf-8")
    run(capsys, "import", str(newer))
    code, out, _ = seed(capsys)
    assert code == 0
    assert out.count(": unchanged") == 7
    assert "jira 1.0.0: skipped, the catalog already has 1.0.1" in out


def trail(conn):
    return conn.execute("select agent_id, version, outcome, by from publish_attempts order by id").fetchall()


def test_the_cli_leaves_a_durable_trail_of_every_import_attempt(catalog_env, capsys, tmp_path):
    seed(capsys)
    assert [r[2] for r in trail(catalog_env)] == ["published"] * 8
    _, out, _ = run(capsys, "export", "jira")
    entry = json.loads(out)
    entry["display"]["summary"] = "Reads Jira tickets."
    edited = tmp_path / "jira.json"
    edited.write_text(json.dumps(entry), encoding="utf-8")
    assert run(capsys, "import", str(edited), "--by", "ann")[0] == 2
    entry["version"] = "1.0.1"
    edited.write_text(json.dumps(entry), encoding="utf-8")
    assert run(capsys, "import", str(edited), "--by", "ann")[0] == 0
    entry["version"] = "1.0.0"
    edited.write_text(json.dumps(entry), encoding="utf-8")
    assert run(capsys, "import", str(edited), "--by", "ann")[0] == 2
    assert trail(catalog_env)[8:] == [
        ("jira", "1.0.0", "refused", "ann"),
        ("jira", "1.0.1", "published", "ann"),
        ("jira", "1.0.0", "older", "ann"),
    ]
