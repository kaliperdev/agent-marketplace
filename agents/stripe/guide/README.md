# Stripe MCP setup draft

**Draft only.** [Stripe's official MCP guide](https://docs.stripe.com/mcp) describes OAuth and agent API keys for its hosted server. This catalog uses OAuth. A real Kaliper sign-in and permission review are still required before a checked `connection.guide` can be published.

## Proposed customer path

1. Ask a Stripe admin to enable MCP for the intended environment. Start with a sandbox.
2. In Kaliper, choose **Sign in with Stripe** and select only the intended account, environment, and permissions in Stripe consent.
3. Use **Test connection** to read a known test payment or invoice.
4. Choose **Save and turn on** only after checking that the account and environment are correct.

`create_refund` moves money and must require explicit confirmation. The guide must show the actual account/environment selector and permission prompt from a fresh-account run, plus the Kaliper test result. No verified public step-specific setup image was found.
