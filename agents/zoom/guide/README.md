# Zoom Meetings MCP setup draft

**Draft only.** [Zoom's MCP connection guide](https://developers.zoom.us/docs/mcp/servers/connect-to-zoom-mcp-servers/) requires a manually registered OAuth app with client credentials, redirect URI, and meeting scopes. Zoom does not support dynamic client registration for this server. The [Meetings MCP reference](https://developers.zoom.us/docs/mcp/zoom-meetings-mcp-server/) gives `https://mcp.zoom.us/mcp/meeting/streamable`; PR #21 currently uses an older URL.

## Proposed admin-led path

1. Create a Zoom General app for the intended account and add only the meeting scopes required by the selected tools.
2. Register Kaliper's exact OAuth callback URL and securely supply the app's client ID and secret through a web form.
3. In Kaliper, choose **Sign in with Zoom Meetings** and complete Zoom authorization.
4. Use **Test connection** to list a known meeting, then **Save and turn on**.

`create_meeting`, `update_meeting`, and `delete_meeting` change records and need explicit confirmation. The endpoint, tool names, and connection method must be corrected before this flow is offered. No verified public setup-screen image was found; capture the app, OAuth, and test screens on a fresh account before adding `connection.guide`.
