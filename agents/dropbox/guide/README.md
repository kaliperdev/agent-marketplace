# Dropbox MCP setup draft

**Draft only.** The [official Dropbox MCP connection guide](https://help.dropbox.com/integrations/connect-dropbox-mcp-server) supports automatic registration for its listed trusted clients. It says other clients must register a Dropbox app with a redirect URI and credentials. Kaliper is not identified as a trusted client in that guide, so PR #21's automatic sign-in is unverified.

## Proposed customer path after Kaliper client support is confirmed

1. Ask the Dropbox team admin whether third-party app connections are allowed.
2. Connect with an account that can see the intended files. Select the narrowest metadata and content read permissions that support the catalog's search and download tools.
3. In Kaliper, choose **Sign in with Dropbox** and review the requested permissions.
4. Use **Test connection** to search for a known file, then **Save and turn on**.

If Kaliper needs its own Dropbox app, register and distribute that app first or build an explicit own-app flow. Do not ask users to paste its secret into Slack. Capture the actual Dropbox app, consent, and Kaliper test screens on a fresh account before adding `connection.guide`; no verified public step image was found.
