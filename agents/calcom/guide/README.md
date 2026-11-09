# Cal.com MCP setup draft

**Draft only.** [Cal.com's official scheduling-agent page](https://site.cal.com/scheduling-for-agents) describes a hosted OAuth connection at `https://mcp.cal.com/mcp`, but the public page does not establish automatic client registration for every MCP client. Kaliper's sign-in must be tested before this becomes a catalog guide.

## Proposed customer path

1. Sign in to the Cal.com account that owns the event types and bookings to expose.
2. In Kaliper, choose **Sign in with Cal.com** and review the Cal.com authorization request.
3. Use **Test connection** to list event types and check availability for a known event type.
4. Choose **Save and turn on** after confirming the intended calendar is visible.

`create_booking` and `cancel_booking` change calendar records. The final guide must explain those tools and confirm that Rytangle asks for approval before either action. Capture the actual OAuth consent and test screens during a fresh-account run; no verified public setup-screen image was found.
