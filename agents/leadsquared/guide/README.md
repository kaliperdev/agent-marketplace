# LeadSquared MCP draft

> **Draft only — blocked pending an authenticated probe.** This is a review reference, not a tested setup guide or a publishable agent. There is no LeadSquared test account for this work. No screenshots or authenticated tool inventory have been captured. Every item in the screenshot checklist remains unchecked.

The structured draft is in [`draft/spec.json`](../draft/spec.json). This guide records what LeadSquared publishes, where the read-only boundary must be enforced, and the evidence required before any agent can be considered for publication.

## Published endpoint and authentication

LeadSquared's help center publishes this vendor-hosted MCP endpoint:

```text
https://mcp-server.pulseai-in21.leadsquared.com/mcp/v1
```

The same article shows an HTTP MCP connection and describes OAuth 2.1 with PKCE. The documented connection uses a browser login and permission grant. The article says the connector respects the signed-in user's LeadSquared role, but that alone does not make the connector read-only. LeadSquared's article describes create, update, and delete actions alongside retrieval actions. See [Connect LeadSquared CRM to Claude](https://help.leadsquared.com/connect-leadsquared-crm-to-claude/).

The endpoint hostname contains `pulseai-in21`. The help article publishes this exact URL but does not establish whether it serves every tenant region. LeadSquared's regional API-host documentation concerns its REST APIs; it does not prove the MCP endpoint's regional behavior. Confirm tenant compatibility during the authorized probe. See [API Host](https://apidocs.leadsquared.com/api-host/).

Do not put LeadSquared REST access keys or secret keys into this MCP configuration. The REST API documentation describes those credentials separately from this connector's OAuth flow. See [Authentication](https://apidocs.leadsquared.com/authentication/).

## Read-only boundary

Read-only is a requirement for the eventual agent's tool allowlist, not a capability established by OAuth or by the endpoint's existence. The vendor documentation says the connector includes write operations. The signed-in user's role is an additional permission boundary, not evidence that every exposed tool is safe to include.

The current draft therefore has an empty tool allowlist. Do not add tool identifiers based on names in the help article, REST API documentation, or guesses. After an authenticated probe, record exact MCP identifiers and schemas from the server's complete `tools/list` response. Review each candidate against its exact description and schema plus first-party behavior documentation. Include only tools proven to retrieve data without changing CRM state. Exclude writes and anything ambiguous. If the effects of a tool cannot be established without invoking it, reject it rather than probing it.

## Exact probe gate

Run the following sequence only with an authorized non-production tenant, a least-privilege user, and synthetic records approved for read testing:

1. Connect to the exact endpoint above with an MCP-compatible HTTP client. Follow the OAuth challenge and metadata advertised by the server; do not guess metadata URLs. Complete the browser PKCE flow. Capture the observed issuer, requested and granted scopes, redirect behavior, client registration behavior, and disconnect path. Redact credentials, tokens, and personal data.
2. After authentication, capture the MCP `initialize` response. Then call `tools/list`, following every `nextCursor` until no further page remains. Save the complete redacted response with the endpoint, client, tenant region, and timestamp. **Do not call a tool during inventory.**
3. Review every tool individually. Preserve its exact server-provided identifier, title, description, input schema, output schema if present, and annotations. Map the behavior to first-party documentation or another direct behavior contract. Tool names and read-only annotations alone are insufficient proof.
4. Allowlist only tools whose behavior is demonstrably retrieval-only. Reject create, update, delete, merge, task/activity mutation, configuration changes, and any unclear or mixed behavior. If a tool's behavior cannot be established without calling a suspected write, reject it.
5. Use one approved retrieval tool against a known synthetic record. Save the redacted request and response and confirm the record and related state remain unchanged.
6. Disconnect from the integration settings and confirm access is revoked or requires authentication again. Record the observed result.

**Pass only when all six steps succeed.** A missing account, incomplete listing, unclear consent, unreviewed schema, ambiguous tool behavior, unsafe smoke test, or unobserved revocation is a stop condition. Until then, keep the allowlist empty and the draft blocked. The machine-readable criteria are in [`draft/spec.json`](../draft/spec.json).

## Screenshot checklist

These are captures to collect from the authorized probe. No screenshot files are included in this draft. Keep all boxes unchecked until the corresponding state is observed; redact tokens, email addresses, lead details, and other personal data.

- [ ] `01-endpoint-entry.png` — the connector setup screen showing the exact published endpoint.
- [ ] `02-oauth-consent-redacted.png` — the real OAuth login/consent screen, with requested permissions visible and all account identifiers and secrets redacted.
- [ ] `03-connected-state.png` — the connected status for the authorized test tenant after the OAuth flow.
- [ ] `04-tools-list-redacted.png` — the authenticated server tool inventory, or a client view of it; retain the complete raw `tools/list` JSON separately because a screenshot may truncate schemas or pagination.
- [ ] `05-read-only-smoke-redacted.png` — one approved retrieval from a synthetic record, with a matching state check showing no CRM change.
- [ ] `06-disconnect-state.png` — the post-disconnect state or re-authentication requirement.

Do not mark the guide verified based on vendor screenshots or documentation alone. Capture the actual client and tenant used for the probe, and keep the raw, redacted protocol evidence with the review materials.

## Current evidence and blockers

- **Verified from first-party documentation:** a vendor-hosted MCP URL is published; LeadSquared describes OAuth 2.1 with PKCE and an HTTP client connection; the connector includes read and write actions; the connector says it respects the signed-in user's role.
- **Not verified:** this endpoint's compatibility with the intended tenant/region; OAuth metadata, scopes, and exact consent; the authenticated tool identifiers and schemas; which tools are strictly read-only; the successful result of a safe read; disconnect/revocation behavior.
- **Blocker:** no LeadSquared test account is available, so the authenticated probe cannot be run.
- **Publication status:** blocked. No `catalog-1.0.0.json` is present or implied by this draft.
