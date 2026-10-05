# agent-marketplace

The GLOBAL side of the agent catalog: the database that is the master copy of
every agent and every version of it, the `catalog` tool, and the read-only
catalog service that client servers pull from. Client servers (rytangle) never
connect to the database itself.

## Where it runs

The catalog is hosted on Kaliper prod: `https://catalog.rytangle.com`, read by
every client server with its own key (see `deploy/README.md`). Publish with
`deploy/publish-to-hosted.sh`; `deploy/publish-to-hosted.sh --list` shows what it
holds. Every published version is also a file under `published/`.

`docker compose up -d catalog-db catalog-service` still starts a throwaway
catalog on a laptop (http://localhost:8095, database on 55433) for development;
nothing uses it.

## Tests

They use a database named `catalog_test` (they drop its tables, and refuse any
database not named `*_test`), by default on the laptop's main Postgres, the
rytangle one on port 55432. Create it once:

    docker exec proto_kb_pg sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "create database catalog_test"'
    uv run pytest
    for a in kit jira github slack cliq workdrive gdocs textql; do (cd agents/$a && uv run pytest -q); done

Or point `CATALOG_TEST_DATABASE_URL` at another `*_test` database.

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

How to write one, and every entry key the platform reads: [docs/adding-an-agent.md](docs/adding-an-agent.md).

| Folder | Kind | What |
|---|---|---|
| `agents/kit` | — | shared: serves an agent's LangChain tools over MCP (`POST /mcp`, `GET /health`), plus the rows/cache/settings helpers |
| `agents/jira`, `github`, `slack`, `cliq`, `workdrive`, `gdocs` | `mcp` | the router's own tool code, copied (only helper imports differ; Cliq's read limit is per minute) |
| `agents/textql` | `remote` | TextQL's Ana behind `POST /ask` |

Each folder holds its catalog entry (`catalog-<version>.json`), so the package
and the entry it serves travel together, and a test checks the server offers
exactly the tools its entry names.

Release a version: upload its package, then publish its entry.

    agents/publish-image.sh jira 2.0.0          # both chips (x86 + ARM) -> ghcr.io/kaliperdev/agent-jira:2.0.0
    uv run catalog import agents/jira/catalog-2.0.0.json

`publish-image.sh` refuses a version that does not match the agent's
`pyproject.toml`, or one already uploaded (published versions are locked, as in
the catalog). It needs a one-time `gh auth token | docker login ghcr.io -u
kaliperdev --password-stdin` with the `write:packages` permission. The images
are private: a server downloads them with `AGENT_REGISTRY=ghcr.io/kaliperdev`
and a read-only login in its root `.env` (see the rytangle catalog README). The
catalog service ships the same way: `agents/publish-image.sh catalog-service 0.1.0`.

A service gets only the settings its entry names (form fields, and what its
sign-in `covers`), from what was saved on that server's page (`adopt` copies
existing logins from its `.env` once); a `file` setting arrives as the file's
contents. The built-in 1.x versions stay in
the router until every server runs the catalog.

`needs_router` in an entry is checked: a server whose router is older refuses it.
