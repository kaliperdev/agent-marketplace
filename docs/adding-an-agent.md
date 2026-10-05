# Adding an agent

An agent is added without touching the platform (rytangle). You write the agent
(unless the vendor runs its own MCP server) and its catalog entry, then publish.

## 1. Choose its kind

| Kind | Use it when | What you write |
|---|---|---|
| `mcp` | the router's model should drive the agent's tools (search, read) | a folder in `agents/` built with `agents/kit` |
| `remote` | the agent answers whole questions itself (like TextQL) | a folder in `agents/` serving `POST /ask` |
| `mcp` with `service.url` | the vendor already runs an MCP server (Linear, Fireflies, …) | only the entry: no folder, no package |

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

## An MCP server the vendor runs

No code and no package: the entry points at the vendor's server.

1. **List what it offers:** from rytangle's `router/`,

       uv run python -m app.mcp_probe https://mcp.linear.app/mcp/readonly --ask-key --full

   (`--ask-key` asks for the key without showing it.) A server that answers "needs
   signing in" takes no key: it needs Part C's sign-in.
2. **Choose reading tools only** for `router.agent.tools`, by the vendor's own
   names. The probe shows the vendor's read-only marks, but they are hints: read
   each description. If the vendor has a read-only address (Linear's
   `/mcp/readonly`), use it. Copy each description into `router.tool_descriptions`.
3. **The entry:**
   - `"kind": "mcp"`, `"service": {"url": "https://…", "timeout_seconds": 60}`
     (optionally `"max_result_chars"`, 1,000–200,000; default 60,000);
   - `"needs_router": "0.5.0"`;
   - `connection.method` `"form"`, one `password` field for the key, and
     `"send": {"bearer": "<FIELD>"}` (sent as `Authorization: Bearer <key>`) or
     `{"header": "<Name>", "field": "<FIELD>"}`; a key that goes with a user
     name (an Atlassian personal API token, with its owner's email) also takes
     a text or email field, and
     `{"basic": ["<USER FIELD>", "<SECRET FIELD>"]}` (sent as
     `Authorization: Basic …`);
   - `router.source_config` `{}`; not `passthrough`.
4. **Publish** the entry only (`deploy/publish-to-hosted.sh`): there is no package.

On a client's page, Save asks the vendor's server whether it takes the key and
that it still offers every tool the entry names; a refusal leaves the agent off
with the reason. The router hands the model safe tool names (letters, digits,
`_`, `-`; at most 64) and calls the vendor by its own; two tools that would get
the same safe name are refused by the catalog. Charts and counts do not work for
a vendor's tools (they return text), and "searched, found nothing" is not noticed.

## An agent that signs in ("Sign in with…")

`connection.method` `"signin"`, `connection.provider` (the button says "Sign in
with <provider>"), `"needs_router": "0.6.0"`, no `covers`, and:

    "signin": {
      "client": "automatic",          // the vendor registers this server itself
      "scopes": ["read"]              // optional: else the vendor's 401 decides, else none
    }

or, where the vendor needs the client's own app (Asana, Google):

    "signin": {
      "client": "own-app",
      "client_id_field": "ASANA_CLIENT_ID",          // a text field of the form
      "client_secret_field": "ASANA_CLIENT_SECRET"   // a password field (optional)
    }

- **A vendor's MCP server** (`service.url`): where to sign in is found from it.
  Otherwise (an agent we wrote) add `authorization_url` and `token_url`.
- `authorize_params` adds parameters the vendor wants (Google:
  `{"access_type": "offline", "prompt": "consent"}` to get a refresh token).
- `"resource": false` stops the `resource` parameter, for a vendor that rejects it.
- **Read-only:** a vendor without read-only scopes (Asana, Notion) can be asked
  anything its token allows; only `router.agent.tools` keeps the agent to reading.
- **An agent we wrote** reads the token with `agent_kit.auth.access_token()`
  inside a tool, per call; it gets no refresh token and no app secret.
- **Writing the entry:** list the tools after signing in:
  `uv run python -m app.mcp_probe <url> --signin --full` (from rytangle's `router/`).
- **A new version that connects another way** (another `service.url`,
  `connection.method`, `connection.signin` or `connection.send`) is switched off
  when a server updates to it, and its old sign-in is forgotten: the person signs
  in (or saves the form) again. Keep these the same unless the change needs it.

## 4. Publish

    agents/publish-image.sh <id> <version>      # the package, for both chips
    deploy/publish-to-hosted.sh agents/<id>/catalog-<version>.json

Published versions are locked: change anything, raise the version.
