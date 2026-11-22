# Railway MCP setup draft

**Draft only.** [Railway's official MCP guide](https://docs.railway.com/ai/mcp-server) describes direct OAuth at `https://mcp.railway.com`. Its OAuth platform supports consent and scopes, but the public MCP guide does not guarantee dynamic registration for every client. Test Kaliper's automatic sign-in before publishing a customer guide.

## Proposed customer path

1. Sign in to the Railway user that can see the intended workspace and project.
2. In Kaliper, choose **Sign in with Railway**. In Railway consent, select only the needed workspaces and projects.
3. Use **Test connection** to list projects and find a known service or deployment.
4. Choose **Save and turn on**.

`redeploy` changes a running service and must require confirmation. Verify the least-privilege [Railway scopes](https://docs.railway.com/integrations/oauth/scopes-and-user-consent), actual consent labels, and confirmation behavior. Capture the OAuth and test screens during a fresh-account run; no verified public setup-screen image was found.
