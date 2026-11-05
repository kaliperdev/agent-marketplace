# Self-hosted work MCPs

This is a shortlist of useful MCPs that customers may run inside their own network, cloud account, or developer workstation. These are not direct marketplace catalogs: onboarding requires customer infrastructure, private connectivity, or a customer-created endpoint.

## Hosting classifications

| Classification | Meaning | Marketplace treatment |
| --- | --- | --- |
| Direct vendor-hosted | The original vendor owns a fixed remote MCP endpoint and its sign-in flow. | Eligible for a catalog after verification. |
| Customer-hosted | The customer deploys or enables the server in their own account or network. | Document only; consider a managed relay later. |
| Gateway-hosted | A third party brokers access to many vendors. | Future managed-relay candidate, not a direct vendor catalog. |
| Local-only | The server runs on a workstation or private bridge. | Advanced private-bridge option only. |

## Private databases

| MCP | Use in a workspace | Hosting and onboarding | Security notes | Kaliper path |
| --- | --- | --- | --- | --- |
| PostgreSQL | Search product, finance, or operational data with natural language. | Customer deploys an MCP server beside the database and supplies a private connection. | Read-only database role, schema allowlist, row limits, query timeouts, and query audit logs. Never paste a connection string into Slack. | Outbound-only bridge or customer relay. |
| MySQL | Query legacy business applications and reporting databases. | Customer-hosted server inside the same private network as MySQL. | Read-only user, database allowlist, masked sensitive columns, and statement timeouts. | Outbound-only bridge. |
| SQLite | Explore local exports, research data, and small operational snapshots. | Local-only file access on a trusted workstation or private runner. | File allowlist, no arbitrary filesystem access, and read-only file mounts. | Private bridge only. |
| Redis | Inspect queues, cache state, and lightweight operational records. | Customer-hosted server with private network access. | Read-only commands, key-prefix allowlist, value-size limits, and no flush or delete commands. | Outbound-only bridge. |

## Internal knowledge and files

| MCP | Use in a workspace | Hosting and onboarding | Security notes | Kaliper path |
| --- | --- | --- | --- | --- |
| Filesystem | Search private documents and generated reports. | Local-only or customer-hosted beside an approved document mount. | Explicit directory allowlist, path traversal protection, read-only default, and file-size limits. | Private bridge. |
| Private wiki or document store | Answer questions over internal policies, runbooks, and product knowledge. | Customer deploys the server or exposes an internal HTTP endpoint. | Workspace ACL propagation, document-level citations, retention controls, and indexing boundaries. | Outbound-only bridge or managed relay with private networking. |
| Internal REST/OpenAPI MCP | Make approved internal services available through a consistent tool interface. | Customer runs the adapter in its own network. | Endpoint allowlist, OAuth service identity, rate limits, schema validation, and confirmation for mutations. | Managed relay candidate. |

## Engineering operations

| MCP | Use in a workspace | Hosting and onboarding | Security notes | Kaliper path |
| --- | --- | --- | --- | --- |
| Git | Search private repositories, branches, commits, and issues. | Local or customer-hosted server with repository credentials. | Read-only by default, repository allowlist, secret redaction, and explicit confirmation for pushes or merges. | Private bridge or relay. |
| GitHub Enterprise | Search internal code, issues, pull requests, and documentation. | Customer-hosted or private-enterprise endpoint. | Enterprise SSO, least-privilege app scopes, repository allowlist, and write confirmation. | Outbound-only bridge. |
| Jira/Data Center | Search private tickets, sprints, and project metadata. | Customer deploys the server inside the enterprise network. | Project and issue-level permissions, token vaulting, audit logs, and confirmation for transitions or edits. | Managed relay candidate. |

## Browser and QA automation

| MCP | Use in a workspace | Hosting and onboarding | Security notes | Kaliper path |
| --- | --- | --- | --- | --- |
| Playwright | Run approved QA flows and inspect web applications. | Customer-hosted browser runner or isolated sandbox. | Domain allowlist, isolated browser profiles, no production credentials by default, and confirmation for submissions. | Managed sandbox or private bridge. |
| Chrome DevTools | Inspect a local or private web app during debugging. | Local runtime attached to a developer browser. | Never expose a personal browser session, cookies, or arbitrary navigation to a shared workspace. | Private bridge only. |

## Private analytics and operations

| MCP | Use in a workspace | Hosting and onboarding | Security notes | Kaliper path |
| --- | --- | --- | --- | --- |
| Databricks workspace MCP | Query private lakehouse data and operational analytics. | Customer creates the MCP server inside a Databricks workspace. | Read-only SQL warehouse, Unity Catalog permissions, query budgets, and workspace-specific endpoint configuration. | Managed relay candidate; not a fixed global catalog URL. |
| Snowflake account MCP | Query private warehouse data and governed metrics. | Customer creates the server and OAuth integration in its Snowflake account. | Read-only role, database/schema allowlist, masking policies, and query timeouts. | Managed relay candidate; account URL is tenant-specific. |
| Sentry | Investigate private errors, traces, and releases. | Customer-hosted or private organization connector. | Project allowlist, PII filtering, read-only default, and incident mutation confirmation. | Outbound-only bridge. |
| Grafana | Query dashboards, logs, and operational metrics. | Customer-hosted in the observability network. | Folder and datasource allowlists, read-only tokens, and no alert mutation by default. | Outbound-only bridge. |
| Prometheus | Query private time-series metrics. | Customer-hosted beside the Prometheus endpoint. | Read-only API, query-range limits, tenant isolation, and no remote-write access. | Outbound-only bridge. |
| Internal incident system | Search incidents, ownership, runbooks, and status. | Customer adapter inside the private network. | Least-privilege OAuth, audit logs, and confirmation for paging or status changes. | Managed relay candidate. |

## Recommended onboarding tiers

1. **Direct vendor-hosted:** one-click OAuth and fixed endpoint; best marketplace experience.
2. **Kaliper-managed relay:** secure web setup for one customer secret or OAuth flow, tenant-scoped endpoint, allowlists, audit logs, and write confirmations.
3. **Private bridge:** outbound-only connector for local or private-network MCPs; advanced option for enterprise customers.

## Deferred from direct catalog publication

- Twilio account-execution tools: the verified Twilio MCP is currently documentation-only and does not execute API calls.
- Databricks and Snowflake: endpoints are customer/workspace-specific, so they do not fit the fixed global catalog URL.
- Playwright and Chrome DevTools: local or customer-run runtimes require a private bridge or managed sandbox.
- Generic PostgreSQL, MySQL, Redis, filesystem, and internal API MCPs: useful, but customer-hosted rather than vendor-hosted.
- Gateway services such as Zapier, Pipedream, Composio, Graphlit, and Knit: reserve for a future managed-relay product.
