# Onboarding private and customer-hosted MCPs

This is a product design for the next connection type, not a catalog of servers that Kaliper operates today. A customer should not need to expose an internal MCP endpoint to the public internet or paste source credentials into Slack.

## Choose the least demanding connection

1. **Vendor-hosted, fixed URL:** use the existing catalog and vendor OAuth. Show the account, permissions, and a connection test before enabling tools.
2. **Vendor-managed, account URL:** use a per-connection URL from the vendor console, with an allowlist of vendor domains. Snowflake and Databricks fit here. The customer does not deploy an MCP server. The current catalog's single `service.url` cannot represent these safely; do not publish a fabricated shared URL.
3. **Customer-hosted, reachable over HTTPS:** accept a specific endpoint only after domain ownership or administrator approval, TLS validation, and a `tools/list` probe. Store its credential in Kaliper's encrypted connection store, never in Slack. This is suitable only when the customer deliberately exposes a controlled endpoint.
4. **Private-network or local-only:** install a signed, managed Kaliper bridge inside the customer network. It opens an outbound authenticated connection to Kaliper and proxies an allowlisted local MCP server. No inbound firewall rule, public hostname, or port forwarding is required.

The bridge is a **future Kaliper product**, not a vendor-hosted MCP or an existing marketplace entry. A gateway run by another company is a separate hosting model and should not be relabelled vendor-hosted.

## Which sources can be added

"Add" means a supported Kaliper connection, not necessarily a direct marketplace catalog. No customer-hosted source is ready to publish in the current fixed-URL catalog. Start with the paths below; keep the rest in the guide until the matching connection type exists.

| Source | Classification | Earliest supported path | Why it is not a direct catalog today |
|---|---|---|---|
| Grafana Cloud | Direct vendor-hosted candidate | Verify OAuth, read-only tool subset, and live `tools/list`, then add a fixed-URL catalog. | [Grafana's hosted MCP](https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/cloud-mcp/) uses `https://mcp.grafana.com/mcp`; this is distinct from self-hosted Grafana. It is a candidate, not an integrated catalog. |
| Snowflake-managed MCP | Vendor-managed account URL | Add the per-connection vendor URL wizard first; verify the account URL, OAuth configuration, role grants, and read-only tools. | Each customer has an account-specific URL and security integration; there is no shared MCP URL. |
| Databricks managed MCP | Vendor-managed workspace URL | Add the per-connection vendor URL wizard first; enforce read-only SQL policy and verify tools. | Each customer has a workspace-specific URL and permissions. |
| PostgreSQL | Customer-hosted bridge pilot | Build a reviewed read-only adapter and outbound bridge, then offer a guided connection. | The reference MCP is [archived](https://github.com/modelcontextprotocol/servers-archived/blob/main/src/postgres/README.md) and runs near the database; it is not a vendor-hosted service. |
| Local files and private document stores | Local/private bridge pilot | Use explicit directory allowlists and the official filesystem MCP's read-only mode behind the bridge. | The [filesystem MCP](https://github.com/modelcontextprotocol/servers/blob/main/src/filesystem/README.md) runs on the customer's machine or network. |
| Jira Data Center | Customer-hosted bridge pilot | Add a reviewed read-only adapter after the bridge pilot. | Jira Cloud's existing remote MCP does not serve private Data Center installations. |
| MySQL, SQLite, Redis, GitHub Enterprise, private wikis, internal REST APIs, Prometheus, self-hosted Grafana and Sentry | Customer-hosted later | Assess a separate adapter for each source after the first bridge pilots. | Endpoints, identities, permissions, and network reachability differ per customer. Do not promise one-click onboarding yet. |
| Playwright, Chrome DevTools, arbitrary custom MCP servers | Local-only or unreviewed | Advanced private bridge exploration only. | Browser side effects and unrestricted tool surfaces need a separate policy and sandbox design. |

For Grafana Cloud, grant only the read and query tool areas needed by the workspace; leave write access off. The [hosted setup guide](https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/cloud-mcp/) says the endpoint uses Streamable HTTP and OAuth 2.1. A catalog still needs an authenticated tool probe and Kaliper OAuth compatibility check before it can be published. The self-managed [Grafana MCP](https://grafana.com/docs/grafana/latest/ai-tools/mcp/) remains a bridge candidate.

## Proposed connection wizard

The page should use the same progressive guide layout as the [Google Docs catalog](../agents/gdocs/catalog-2.0.3.json): one action per step, prerequisites, access summary, vendor links, test, and troubleshooting.

| Step | Customer action | Kaliper check |
|---|---|---|
| 1. Choose source | Select a vendor and the data location: vendor cloud, managed account endpoint, reachable customer endpoint, or private network. | Show only supported connection paths. |
| 2. Choose access | Pick a workspace, project, database, or folder and start with read-only tools. | Display the exact tools that will be enabled and the identity they will run as. |
| 3. Connect | Sign in, enter a vendor-issued endpoint, or install the bridge using a short-lived pairing code. | Bind the connection to a Slack workspace and a Kaliper administrator. Pairing code is entered in the web wizard, not Slack. |
| 4. Verify | Approve the vendor consent or local bridge request. | Check TLS, endpoint ownership, authorization, transport, and `tools/list`; compare tool names with the catalog allowlist. |
| 5. Preview | Ask a harmless sample question. | Show source, identity, scopes, tool calls, and an audit entry. |
| 6. Enable | Turn the source on for selected users or channels. | Keep writes disabled until a separate, explicit approval policy is configured. |

For private sources, the bridge installer should be a signed package with one documented command or container deployment. The wizard supplies a short-lived pairing code and an organization ID. The bridge generates its own key pair, stores source credentials on the customer side, and connects outward over TLS. Kaliper stores only the bridge identity and session routing metadata. The bridge must authorize each tool call locally, reject unknown tools, bind every request to a tenant and user, and support revocation, updates, health checks, logs, and clean uninstall. Network failure should pause tools instead of falling back to another endpoint.

Do not implement a generic “run any command” proxy. Each supported source needs a reviewed adapter, default read-only policy, explicit filesystem/database allowlist, and a documented upgrade path. Payments, outbound messages, deployments, and record mutations need separate confirmation in the product.

## Why these sources need different paths

| Source | Why a fixed vendor catalog is insufficient | Easier path |
|---|---|---|
| PostgreSQL and other private databases | A database often sits on a private network. The MCP project's [PostgreSQL reference server](https://github.com/modelcontextprotocol/servers-archived/blob/main/src/postgres/README.md) is archived and local; it is not a production vendor service. | Customer-side reviewed read-only adapter and outbound bridge; dedicated database role restricted to approved schemas. |
| Local files and private knowledge | The official [filesystem server](https://github.com/modelcontextprotocol/servers/blob/main/src/filesystem/README.md) runs locally against allowed directories; it has a read-only mode. | Signed local bridge, explicit folders, no unrestricted home-directory access. |
| Jira Data Center | Atlassian's [remote MCP](https://support.atlassian.com/atlassian-ai-gateway/docs/get-started-with-the-atlassian-remote-mcp-server/) serves Atlassian Cloud. Data Center uses customer-controlled instances and [personal access tokens](https://developer.atlassian.com/server/jira/platform/personal-access-token/). | Customer-side read-only Jira adapter and outbound bridge. The existing Jira Cloud catalog is a different connector. |
| Snowflake | [Snowflake-managed MCP](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-mcp) runs in the customer's account; the URL and OAuth integration are account-specific. | Add vendor-managed account URL onboarding, role grants, and network-policy checks. A bridge is needed only for private connectivity. |
| Databricks | [Managed MCP services](https://docs.databricks.com/aws/en/agents/agent-framework/agent-tool) have workspace-specific URLs and identity. [Databricks SQL MCP](https://docs.databricks.com/aws/en/agents/mcp-tools/databricks-sql) may allow writes unless policy restricts it. | Add vendor-managed workspace URL onboarding and enforce a read-only SQL policy before tools are enabled. |
| Self-hosted Grafana or Sentry | SaaS editions have hosted options, while private deployments require an endpoint on the customer's network. See [Grafana Cloud MCP](https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/cloud-mcp/) and [Sentry MCP](https://github.com/getsentry/sentry-mcp). | Prefer the hosted edition where available; otherwise use a customer-side bridge with read scopes. |

## Implementation sequence

1. Build **per-connection vendor-managed URLs** for Snowflake and Databricks. This removes deployment work for customers already using those services. Constrain hosts to vendor-owned domains and verify the server identity before saving.
2. Pilot the **outbound bridge** with one source: a read-only PostgreSQL adapter. Measure pairing completion, failed connections, update success, and support burden before adding more sources.
3. Add local files and Jira Data Center adapters. Keep browser automation and arbitrary custom MCPs out of the first release because their permissions and side effects are harder to constrain.

No customer-hosted source should appear as a directly vendor-hosted catalog until this connection type and its security checks exist.
