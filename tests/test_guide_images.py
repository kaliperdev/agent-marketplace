"""Setup guide screenshots: what is accepted (marketplace/images.py), how an
entry names one, how `catalog import` publishes them and how the service
serves them."""

import json
import shutil
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import FIX

from marketplace import cli, images, store
from marketplace.entry import guide_images, validate
from marketplace.service import build_handler

ROOT = Path(__file__).resolve().parent.parent
WEBP, PNG = (FIX / "shot.webp").read_bytes(), (FIX / "shot.png").read_bytes()


def test_webp_and_png_are_read_from_their_own_bytes():
    assert images.media_type(WEBP) == "image/webp" and images.dimensions(WEBP) == (320, 200)
    assert images.media_type(PNG) == "image/png" and images.dimensions(PNG) == (320, 200)
    assert images.problem(WEBP) is None and images.problem(PNG, 320, 200) is None


@pytest.mark.parametrize("data, reason", [
    (b"<svg xmlns='http://www.w3.org/2000/svg'><script/></svg>", "not a WebP or PNG"),
    (b"GIF89a" + b"\0" * 20, "not a WebP or PNG"),
    (b"RIFF\0\0\0\0WEBPVP8 ", "cannot be read"),
    (WEBP + b"\0" * images.MAX_IMAGE_BYTES, "at most 512 KB"),
])
def test_anything_else_is_refused(data, reason):
    assert reason in images.problem(data)


def test_a_screenshot_must_be_the_size_its_entry_says():
    assert "not the 640×400" in images.problem(WEBP, 640, 400)


# ── an entry naming a screenshot ──

def _entry_with_shot(**image):
    entry = json.loads((ROOT / "agents" / "gdrive" / "catalog-1.0.2.json").read_text(encoding="utf-8"))
    entry["version"] = "1.0.9"
    entry["connection"]["guide"]["steps"][0]["image"] = {
        "file": "01-project.webp", "sha256": images.sha256(WEBP), "width": 320, "height": 200,
        "alt": "Google Cloud's New Project form, named Rytangle demo", **image}
    return entry


def test_a_step_may_show_a_screenshot():
    assert validate(_entry_with_shot()) == []


@pytest.mark.parametrize("change, message", [
    ({"file": "../../etc/passwd"}, "image.file"),
    ({"file": "shot.svg"}, "image.file"),
    ({"sha256": "abc"}, "image.sha256"),
    ({"width": 10}, "image.width"),
    ({"alt": ""}, "image.alt"),
    ({"caption": "x"}, "unknown key 'caption'"),
])
def test_a_malformed_screenshot_is_refused(change, message):
    assert any(message in m for m in validate(_entry_with_shot(**change))), message


def _agent_folder(tmp_path, entry, shot=WEBP):
    folder = tmp_path / "agents" / "gdrive"
    (folder / "guide").mkdir(parents=True)
    (folder / "guide" / "01-project.webp").write_bytes(shot)
    path = folder / "catalog-1.0.9.json"
    path.write_text(json.dumps(entry), encoding="utf-8")
    return path


def test_guide_images_fills_in_each_screenshots_hash_and_size(tmp_path, capsys):
    path = _agent_folder(tmp_path, _entry_with_shot(sha256="0" * 64, width=999, height=999))
    assert cli.main(["guide-images", str(path)]) == 0
    image = json.loads(path.read_text())["connection"]["guide"]["steps"][0]["image"]
    assert (image["sha256"], image["width"], image["height"]) == (images.sha256(WEBP), 320, 200)
    assert "updated: 01-project.webp" in capsys.readouterr().out
    assert cli.main(["guide-images", str(path)]) == 0
    assert "up to date" in capsys.readouterr().out


def test_guide_images_refuses_a_file_that_is_not_a_screenshot(tmp_path, capsys):
    path = _agent_folder(tmp_path, _entry_with_shot(), shot=b"<svg/>")
    assert cli.main(["guide-images", str(path)]) == 2
    assert "not a WebP or PNG" in capsys.readouterr().err


# ── publishing and serving (database) ──

def test_import_publishes_the_screenshots_beside_the_entry(catalog_env, conn, tmp_path, capsys):
    path = _agent_folder(tmp_path, _entry_with_shot())
    assert cli.main(["import", str(path), "--by", "ann"]) == 0
    assert store.guide_image(conn, images.sha256(WEBP)) == ("image/webp", WEBP)


def test_import_refuses_a_screenshot_that_is_not_the_one_named(catalog_env, conn, tmp_path, capsys):
    path = _agent_folder(tmp_path, _entry_with_shot(), shot=PNG)
    assert cli.main(["import", str(path)]) == 2
    assert "not the file its entry names" in capsys.readouterr().err
    assert store.get_entry(conn, "gdrive", "1.0.9") is None


def test_import_refuses_a_missing_screenshot(catalog_env, tmp_path, capsys):
    path = _agent_folder(tmp_path, _entry_with_shot())
    shutil.rmtree(path.parent / "guide")
    assert cli.main(["import", str(path)]) == 2
    assert "could not read guide screenshot" in capsys.readouterr().err


@pytest.fixture
def image_server():
    found = {images.sha256(WEBP): ("image/webp", WEBP)}
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(
        lambda: [], lambda a, v: None, read_keys=frozenset({"k1"}), image=found.get))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _get(url, key="k1"):
    request = urllib.request.Request(url, headers={"x-catalog-key": key} if key else {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def test_the_service_serves_a_screenshot_by_its_hash_to_be_kept_forever(image_server):
    status, headers, body = _get(f"{image_server}/v1/guide-images/{images.sha256(WEBP)}")
    assert (status, body) == (200, WEBP)
    assert headers["content-type"] == "image/webp"
    assert "immutable" in headers["cache-control"]
    assert headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path, key, status", [
    (f"/v1/guide-images/{'0' * 64}", "k1", 404),
    ("/v1/guide-images/../catalog", "k1", 404),
    (f"/v1/guide-images/{images.sha256(WEBP)}", None, 401),
])
def test_the_service_answers_nothing_else(image_server, path, key, status):
    assert _get(image_server + path, key)[0] == status


def _named_screenshots():
    for path in sorted((ROOT / "agents").glob("*/catalog-*.json")):
        entry = json.loads(path.read_text(encoding="utf-8"))
        for image in guide_images(entry):
            yield pytest.param(path, image, id=f"{path.parent.name}/{path.name}/{image['file']}")


@pytest.mark.parametrize("path, image", list(_named_screenshots()))
def test_every_screenshot_an_entry_names_is_beside_it_and_is_that_file(path, image):
    data = (path.parent / "guide" / image["file"]).read_bytes()
    assert images.sha256(data) == image["sha256"], "run: uv run catalog guide-images " + str(path.relative_to(ROOT))
    assert images.problem(data, image["width"], image["height"]) is None
