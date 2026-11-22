# MongoDB Atlas MCP setup draft

**Draft only.** [Atlas's managed MCP setup](https://www.mongodb.com/docs/mcp-server/get-started/) requires an admin-provisioned configuration and service-account credentials for the programmatic path. The [remote access models](https://www.mongodb.com/docs/mcp-server/remote-mcp/access-models/) describe user-delegated OAuth through supported clients. PR #21's automatic sign-in is not verified for Kaliper.

## Proposed admin-led path

1. Have an Atlas admin create a Managed MCP configuration for the intended organization and project.
2. Restrict the configuration to the required read tools and databases. Use a service account with the narrowest applicable privileges.
3. Create the client credential once and enter it in a secure Kaliper web form. Do not paste it into Slack.
4. Use **Test connection** to list a known database and collection, then **Save and turn on**.

This flow needs a different connection schema and secret handling than `signin.client: automatic`. No verified public setup-screen image was found. Capture the configuration and test screens during a fresh-account walkthrough before adding `connection.guide`.
