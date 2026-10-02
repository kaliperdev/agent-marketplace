# agent-marketplace

The GLOBAL side of the agent catalog: the database that is the master copy of
every agent and every version of it, the `catalog` tool, and the read-only
catalog service that client servers pull from. Client servers (rytangle) never
connect to the database itself.

## Run it on this laptop

    docker compose up -d catalog-db catalog-service
    uv run catalog init-db            # once
    curl -s localhost:8095/v1/catalog | head

| What | Where |
|---|---|
| Catalog database | `postgresql://catalog:catalog@localhost:55433/catalog` (laptop-only password) |
| Tests' database | `…/catalog_test`, emptied by every test run |
| Catalog service | `http://localhost:8095` |

## Change an agent

    uv run catalog export jira > jira.json
    # edit jira.json, then raise "version" (1.0.0 -> 1.0.1)
    uv run catalog import jira.json

Every import is checked, and a new version never replaces an old one. A changed
entry that kept its version number is refused. `catalog history jira` lists the
versions.

## The router's file

The database is the master copy. `uv run catalog export-router` prints
`router/config/agents.json` rebuilt from it.

## Tests

    docker compose up -d catalog-db
    uv run pytest
