# Zoho CRM Data Insights connector draft

This directory documents a non-publishable connector scaffold. Zoho describes **Data Insights** as read-only access for COQL queries, module lists, and field schemas. Zoho describes **Data Operations** separately and includes create, update, and delete operations there. This connector accepts only the Data Insights host pattern and does not expose tools until the live-probe gate is complete.

## Connection and secret handling

The proposed connection uses a per-organization Zoho CRM Data Insights MCP URL. Its `/mcp/<secret>/message` path contains a credential: copy it only from the Zoho CRM MCP setup screen, store it in the secret field, and do not paste it into tickets, screenshots, logs, or shell history. The connector refuses other host families, including Data Operations, and refuses redirects.

The OAuth client ID, refresh-token inputs, metadata endpoint, and token-refresh behavior are scaffolding assumptions pending a Zoho test account. No OAuth scope names or remote tool names are asserted in the draft catalog.

## Local checks

From the repository root, run the focused tests with:

```sh
PYTHONPATH=agents/zoho-crm:agents/kit python -m pytest -q -p no:cacheprovider agents/zoho-crm/tests
```

The tests use fake DNS answers and an in-memory HTTP transport. They do not contact Zoho or validate a real OAuth grant.

## Live-probe gate

Before adding any tool to a publishable catalog:

1. Connect a dedicated Zoho CRM test organization to the **Data Insights** server.
2. Probe OAuth metadata, endpoint redirects, token refresh, and MCP protocol negotiation.
3. Capture `tools/list` output and verify each name, description, and input schema from the live server.
4. Confirm every exposed tool is read-only and covers only module discovery, schema inspection, or COQL querying.
5. Confirm the MCP URL host is Data Insights, not Data Operations, and verify the deployment's DNS/egress controls.
6. Replace the draft capability intent with the exact verified tool names and schemas, then run integration checks before publication.

No remote tool names or OAuth scopes should be copied from assumptions. Zoho CRM's REST API documentation describes separate OAuth requirements for COQL and field metadata; those REST scopes do not establish what the hosted MCP server requests.

## Screenshot checklist

Collect these images from the test organization after a live probe. Before saving or sharing any image, redact the full secret MCP path, organization identifiers, user email addresses, access tokens, refresh tokens, and client secrets.

- [ ] Zoho CRM Setup → Developer Hub → MCP for AI Agents, with **Data Insights** visibly selected.
- [ ] The Data Insights connection/setup panel, with the URL host visible and the entire `/mcp/<secret>/message` path obscured.
- [ ] The OAuth consent/approval screen, with account identifiers and any tokens redacted; record the displayed permissions separately without inferring scopes.
- [ ] A successful MCP connection and the live tool list, with tool schemas captured in a separate redacted artifact.
- [ ] A representative read-only module/schema/COQL probe, with CRM record values and personal data redacted.
- [ ] A negative check showing Data Operations is not selected or accepted by this connector; do not include its secret URL.

## Upstream references

- [Zoho CRM MCP overview](https://www.zoho.com/crm/developer/docs/mcp/overview.html)
- [Zoho CRM MCP setup for VS Code](https://www.zoho.com/crm/developer/docs/mcp/setup/vscode.html)
- [Zoho CRM COQL API](https://www.zoho.com/crm/developer/docs/api/v8/Get-Records-through-COQL-Query.html)
- [Zoho CRM module metadata API](https://www.zoho.com/crm/developer/docs/api/v8/module-meta.html)
- [Zoho CRM OAuth scopes](https://www.zoho.com/crm/developer/docs/api/v8/scopes.html)
