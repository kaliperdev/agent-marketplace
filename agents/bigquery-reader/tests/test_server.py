import json
import os
import stat
import tomllib
from pathlib import Path

from bigquery_reader_agent import access
from bigquery_reader_agent import server as server_module
from bigquery_reader_agent.server import build_tools, key_path

KEY = '{"type": "service_account", "client_email": "marketing-agent@example.invalid"}'
ENV = {"GOOGLE_SA_JSON": KEY, "BQ_PROJECT": "rytangle-demo", "BQ_DATASETS": "marketing_test"}

CATALOG = json.loads((Path(__file__).resolve().parents[1] / "catalog-1.1.0.json").read_text(encoding="utf-8"))


def test_the_server_offers_exactly_the_tools_its_catalog_entry_names(tmp_path):
    tools, problem = build_tools(ENV, key_dir=tmp_path)
    assert problem is None
    assert sorted(t.name for t in tools) == sorted(CATALOG["router"]["agent"]["tools"])


def test_the_catalog_describes_each_tool_as_the_tool_describes_itself(tmp_path):
    tools, _ = build_tools(ENV, key_dir=tmp_path)
    described = CATALOG["router"]["tool_descriptions"]
    assert {t.name: " ".join(t.description.split()) for t in tools} == {
        name: " ".join(text.split()) for name, text in described.items()}


def test_the_entry_names_no_write_tool():
    assert "writes" not in CATALOG["router"]["agent"]


def test_the_entry_asks_for_exactly_the_settings_the_server_reads():
    assert [f["key"] for f in CATALOG["connection"]["fields"]] == [
        "GOOGLE_SA_JSON", "BQ_PROJECT", "BQ_DATASETS", "BQ_MAX_GB"]
    assert [f["required"] for f in CATALOG["connection"]["fields"]] == [True, True, True, False]


def test_the_key_can_arrive_as_its_contents_and_is_kept_private(tmp_path):
    path = key_path(KEY, tmp_path)
    assert Path(path).read_text(encoding="utf-8") == KEY
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_the_key_can_still_be_a_path(tmp_path):
    assert key_path("/run/keys/sa.json", tmp_path) == "/run/keys/sa.json"


def test_missing_settings_are_named():
    assert build_tools({}) == ([], "GOOGLE_SA_JSON, BQ_PROJECT, BQ_DATASETS are not set")


def test_a_bad_scan_limit_is_named(tmp_path):
    tools, problem = build_tools({**ENV, "BQ_MAX_GB": "lots"}, key_dir=tmp_path)
    assert tools == [] and "BQ_MAX_GB" in problem


def test_a_blank_dataset_list_is_named(tmp_path):
    tools, problem = build_tools({**ENV, "BQ_DATASETS": " , "}, key_dir=tmp_path)
    assert tools == [] and "BQ_DATASETS" in problem


def test_the_guide_grants_data_viewer_per_dataset_and_only_job_user_on_the_project():
    # The roles, not only our dry-run check, keep the agent inside the listed datasets.
    texts = [CATALOG["connection"]["fields"][0]["help"]] + [
        s["text"] for s in CATALOG["connection"]["guide"]["steps"]]
    joined = " ".join(texts)
    assert "Data Viewer on the whole project" not in joined
    assert any("BigQuery Job User" in t and "project" in t for t in texts)
    assert any("BigQuery Data Viewer" in t and "**Share**" in t and "dataset" in t for t in texts)


def test_the_reader_checks_data_access_but_never_its_own_login(monkeypatch):
    seen = {}
    monkeypatch.setattr(server_module, "serve", lambda tools, port, problem, **kw: seen.update(kw))
    for key, value in {**ENV, "ACCESS_NOTE_KEY": "k"}.items():
        monkeypatch.setenv(key, value)
    server_module.main()
    assert callable(seen["access_check"]) and seen.get("check") is None


def test_the_entry_is_1_1_0_and_matches_the_package():
    assert CATALOG["version"] == "1.1.0" and CATALOG["service"]["image"] == "kaliper/agent-bigquery-reader:1.1.0"
    assert "access_notes" not in CATALOG["service"] and access.NOTE_KEY_SETTING == "ACCESS_NOTE_KEY"
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == CATALOG["version"]
