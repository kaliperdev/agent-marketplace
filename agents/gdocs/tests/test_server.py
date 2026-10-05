import json
import os
import stat
from pathlib import Path

from gdocs_agent.server import build_tools, key_path

CATALOG = json.loads((Path(__file__).resolve().parents[1] / "catalog-2.0.0.json").read_text(encoding="utf-8"))
KEY = '{"type": "service_account", "client_email": "reader@example.invalid"}'


def test_the_server_offers_exactly_the_tools_its_catalog_entry_names(tmp_path):
    tools, problem = build_tools({"GOOGLE_SA_JSON": KEY, "CACHE_DIR": str(tmp_path)}, key_dir=tmp_path)
    assert problem is None
    assert sorted(t.name for t in tools) == sorted(CATALOG["router"]["agent"]["tools"])


def test_the_key_can_arrive_as_its_contents_and_is_kept_private(tmp_path):
    path = key_path(KEY, tmp_path)
    assert Path(path).read_text(encoding="utf-8") == KEY
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_the_key_can_still_be_a_path(tmp_path):
    assert key_path("/run/keys/sa.json", tmp_path) == "/run/keys/sa.json"


def test_without_its_key_it_offers_nothing_and_says_why(tmp_path):
    assert build_tools({}, key_dir=tmp_path) == ([], "GOOGLE_SA_JSON is not set")
