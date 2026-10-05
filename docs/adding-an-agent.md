# Adding an agent

An agent is added without touching the platform (rytangle). You write the agent
(unless the vendor runs its own MCP server) and its catalog entry, then publish.

## 1. Choose its kind

| Kind | Use it when | What you write |
|---|---|---|
| `mcp` | the router's model should drive the agent's tools (search, read) | a folder in `agents/` built with `agents/kit` |
| `remote` | the agent answers whole questions itself (like TextQL) | a folder in `agents/` serving `POST /ask` |

## 2. Write the agent (kind `mcp`)

Copy the shape of `agents/jira`: `<name>_agent/server.py` builds LangChain tools
and calls `agent_kit.mcp_server.serve(...)`; the Dockerfile builds from
`agents/` so the kit is included.

- **Logins** come from the environment, named as the entry's form fields (and
  what its sign-in covers). A `file` field arrives as the file's contents.
- **Rows for charts and counts:** return `(text, agent_kit.rows.table(columns, rows, truncated=...))`
  from a tool with `response_format="content_and_artifact"`.
- **An empty search:** return `agent_kit.rows.nothing_found(what, where)`. The
  router listens for those words to say what was searched and to look once more.
- **/health** answers 503 until every login it needs is set (`agent_kit.settings.missing`).

## 3. Write its entry: `agents/<id>/catalog-<version>.json`

Start from an existing entry. What the platform reads from it:

| Key | Meaning |
|---|---|
| `service.image`, `service.port` | the package and the port it serves on |
| `service.timeout_seconds` | one tool call's wait (default 60) |
| `service.memory_mb` | 64–2048; default: the server's `AGENT_MEMORY_LIMIT_MB` (256) |
| `service.start_seconds` | 5–300 to answer `/health` after starting; default 20 |
| `router.agent.description` | what the planner reads when choosing agents |
| `router.agent.owns` | the agent's boundary: what it owns and what it does not |
| `router.agent.time_limit_seconds` | 30–300 for a whole question; default 120 |
| `router.agent.holds_documents` | `true` if a question naming a file without saying where may be here |
| `router.agent.first_for` | the questions to ask it first, e.g. "meetings and what was said in them" |
| `router.tool_descriptions` | what the model reads about each tool |
| `connection.fields` | the form: `text`, `email`, `password`, `select`, `file` (with `accept`, e.g. `.json,.pem`) |
| `display.logo.image` | `logos/<name>.svg`, or the logo itself as `data:image/svg+xml;base64,…` (at most 64 KB) |

A `{setting}` in the description, `owns` or a tool description is filled from
the value saved for that field on each server (`GITHUB_REPO` → `{github_repo}`).
Never put a secret field in a placeholder: secrets are never filled in.

Set `needs_router` to the router version that has everything the entry uses
(0.4.0 for any key in the table above that is new in 0.4.0).

## 4. Publish

    agents/publish-image.sh <id> <version>      # the package, for both chips
    deploy/publish-to-hosted.sh agents/<id>/catalog-<version>.json

Published versions are locked: change anything, raise the version.
