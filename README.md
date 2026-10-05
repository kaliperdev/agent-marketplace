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
entry that kept its version number is refused, and the database itself refuses
edits to a published version (a trigger), so a database client cannot change one
either. `catalog history jira` lists the
versions.

## The router's file

The database is the master copy. `uv run catalog export-router` prints
`router/config/agents.json` rebuilt from it.

## Agent services (`agents/`)

Agents that run as their own service on a client's server, one package each.
The client's settings service starts them; they are not part of any compose file.

| Folder | Kind | What |
|---|---|---|
| `agents/kit` | — | shared: serves an agent's LangChain tools over MCP (`POST /mcp`, `GET /health`), plus the rows/cache/settings helpers |
| `agents/jira`, `github`, `slack`, `cliq`, `workdrive`, `gdocs` | `mcp` | the router's own tool code, copied (only helper imports differ; Cliq's read limit is per minute) |
| `agents/textql` | `remote` | TextQL's Ana behind `POST /ask` |

Each folder holds its catalog entry (`catalog-<version>.json`), so the package
and the entry it serves travel together, and a test checks the server offers
exactly the tools its entry names.

Build a package (laptop; no registry yet) and publish its entry:

    docker build -f agents/jira/Dockerfile -t kaliper/agent-jira:2.0.0 agents
    docker build -t kaliper/agent-textql:2.0.0 agents/textql    # TextQL: its own folder
    uv run catalog import agents/jira/catalog-2.0.0.json

A service gets only the settings its entry names (form fields, and what its
sign-in `covers`), from the client's saved form or that server's `.env`; a
`file` setting arrives as the file's contents. The built-in 1.x versions stay in
the router until every server runs the catalog.

`needs_router` in an entry is checked: a server whose router is older refuses it.

## Tests

    docker compose up -d catalog-db
    uv run pytest
    for a in kit jira github slack cliq workdrive gdocs textql; do (cd agents/$a && uv run pytest -q); done
