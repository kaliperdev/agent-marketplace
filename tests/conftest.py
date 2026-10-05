import json
import os
from pathlib import Path

import pytest

from marketplace.seed import entries_from_files

FIX = Path(__file__).parent / "fixtures"
TEST_URL = os.environ.get(
    "CATALOG_TEST_DATABASE_URL", "postgresql://catalog:catalog@localhost:55433/catalog_test"
)


def require_test_database(url: str) -> str:
    """The tests drop tables, so they may only ever touch a database named *_test."""
    name = url.rsplit("/", 1)[-1].split("?", 1)[0]
    if not name.endswith("_test"):
        raise RuntimeError(f"refusing to run tests against database {name!r}: its name must end in _test")
    return url


@pytest.fixture
def registry():
    return json.loads((FIX / "agents.json").read_text(encoding="utf-8"))


@pytest.fixture
def extras():
    return json.loads((FIX / "extras.json").read_text(encoding="utf-8"))


@pytest.fixture
def entries(registry, extras):
    return entries_from_files(registry, extras, needs_router="0.1.0")


@pytest.fixture
def conn():
    """A clean catalog in the TEST database: tables dropped and recreated."""
    from marketplace import store

    url = require_test_database(TEST_URL)  # outside the try: refuse loudly, never skip quietly
    try:
        connection = store.connect(url)
    except Exception as err:  # psycopg.OperationalError when the container is down
        pytest.skip(f"catalog test database not reachable ({err}); run: docker compose up -d catalog-db")
    connection.execute("drop table if exists publish_attempts, agent_versions, agents cascade")
    store.apply_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def catalog_env(conn, monkeypatch):
    """Point the catalog command at the clean test database."""
    monkeypatch.setenv("CATALOG_DATABASE_URL", TEST_URL)
    return conn
