# AWS MCP setup draft

**Draft only.** [AWS's hosted MCP setup](https://docs.aws.amazon.com/agent-toolkit/latest/userguide/getting-started-aws-mcp-server.html) uses regional endpoints and an AWS identity. Its [OAuth requirements](https://docs.aws.amazon.com/signin/latest/userguide/aws-mcp-server.html) limit dynamic client registration to approved agents and redirect URIs. PR #21's `signin.client: automatic` has not been verified for Kaliper; do not present this as a working one-click connection.

## Proposed customer path after client approval

1. Choose the AWS Region that hosts the intended MCP endpoint. This catalog currently fixes `us-east-1`.
2. Grant the connecting AWS identity only the IAM permissions needed for the read tools. Interactive OAuth also needs `signin:AuthorizeOAuth2Access` and `signin:CreateOAuth2Token`.
3. In Kaliper, choose **Sign in with AWS**, review the requested access, and complete AWS authorization.
4. Use **Test connection** to list regions or read known documentation, then **Save and turn on**.

**Screenshots needed:** AWS identity authorization, regional endpoint choice, and Kaliper test result from an approved client. No verified public step image was found. A second person must complete the flow on a fresh account before `connection.guide` can be added.
