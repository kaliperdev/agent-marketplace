# Google Workspace unified MCP onboarding

Google Workspace should be presented as one marketplace service with capability toggles, not as separate user-facing Gmail, Drive, Docs, Sheets, Calendar, Slides, Chat, and People agents.

The current catalog format supports one fixed MCP URL per entry. Google publishes separate hosted endpoints per Workspace product, so the unified experience requires a connector layer above the catalog format.

## User flow

1. The user selects **Google Workspace** in the marketplace.
2. Kaliper starts Google OAuth with basic identity scopes.
3. The user selects Workspace capability toggles: Gmail, Calendar, Drive, Docs, Sheets, Slides, Chat, and People.
4. Kaliper requests incremental consent for only the selected scopes.
5. Kaliper stores the encrypted refresh token per Slack workspace and user.
6. The connector enables the selected Google MCP endpoints and exposes only their tools to Slack.

## Hosted endpoints

```text
Gmail:    https://gmailmcp.googleapis.com/mcp/v1
Drive:    https://drivemcp.googleapis.com/mcp/v1
Docs:     https://docsmcp.googleapis.com/mcp/v1
Sheets:   https://sheetsmcp.googleapis.com/mcp/v1
Slides:   https://slidesmcp.googleapis.com/mcp/v1
Calendar: https://calendarmcp.googleapis.com/mcp/v1
Chat:     https://chatmcp.googleapis.com/mcp/v1
People:   https://people.googleapis.com/mcp/v1
```

## Permission defaults

- Read access is the default for every toggle.
- Gmail send, Calendar changes, file uploads, Sheets edits, Slides edits, and Chat messages require separate write enablement.
- Every write requires explicit confirmation in Slack.
- Disabling a toggle removes its tools immediately and revokes the associated scopes when no other enabled capability needs them.

## Implementation boundary

Do not publish a fake single Google Workspace MCP URL. Add a new connector/catalog format only when the OAuth broker and capability routing exist. Existing Google Drive and Google Docs catalogs remain unchanged until the unified connector is available.
