# Freshdesk connection guide (draft)

**Status:** Unchecked. The account flow, MCP tool schemas, and screenshots still need a fresh Growth-or-higher test account and a second-person walkthrough. No catalog should be published from this draft.

## What you need

- A Freshdesk account whose plan includes MCP access and an administrator able to enable MCP.
- The one-label subdomain of that account, such as `demo` for `https://demo.freshdesk.com`.
- A Freshdesk API key with access limited to the support data intended for Kaliper. Enter the key in Kaliper's web form; never paste it into Slack.

## Draft steps

1. In [Freshdesk's MCP setup instructions](https://support.freshdesk.com/support/solutions/articles/50000012670-model-context-protocol-mcp-integration-in-freshdesk), check the account plan and enable MCP with an administrator. Capture `01-enable-mcp.webp` showing the control.
2. In the Freshdesk profile, locate the API key using the [vendor setup instructions](https://support.freshdesk.com/support/solutions/articles/50000012670-model-context-protocol-mcp-integration-in-freshdesk). Capture `02-api-key-location.webp` with the secret completely masked. Record the exact menu labels during the test run.
3. In Kaliper's Freshdesk connection form, enter the account subdomain and API key. Capture `03-kaliper-form.webp` with the key masked and account identifiers removed.
4. Select **Test connection**. Confirm that a harmless ticket read succeeds and that the tool list matches the catalog allowlist. Capture `04-test-connection.webp`.
5. Select **Save and turn on**. Capture `05-save.webp`.

The connector sends the key as a raw `Authorization` header to `https://<subdomain>.freshdesk.com/mcp`. It refuses redirects and private-network DNS results. Only ticket, contact, company, and conversation reads are exposed. Actual remote tool schemas and required field names remain unverified.

## Review gates

- [ ] Complete all steps on a fresh test account and record the exact vendor labels.
- [ ] Compare the authenticated `tools/list` schemas with the connector mapping.
- [ ] Capture focused, masked WebP screenshots with an orange outline around the relevant control.
- [ ] Have a second person follow the guide and perform a harmless read.
- [ ] Add `connection.guide` and a checked date only after that walkthrough.

Source: [Freshdesk official MCP guide](https://support.freshdesk.com/support/solutions/articles/50000012670-model-context-protocol-mcp-integration-in-freshdesk).
Logo source: [Freshdesk icon hosted by Freshworks](https://www.freshworks.com/beta/icons/template-2026/freshdesk-logo-icon.svg).
