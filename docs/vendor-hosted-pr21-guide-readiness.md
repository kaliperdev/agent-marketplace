# PR #21 connection guide drafts

The nine PR #21 agents have step-by-step **drafts** in their own `agents/<id>/guide/` folders. These are research notes, not checked catalog-page guides. The repository's [connection guide standard](connection-guides.md) requires a fresh-account walkthrough, a second-person check, and screenshots from that run before `connection.guide.checked` can truthfully be set. Do not publish a draft as a tested connection flow.

| Agent | Draft | Focused public setup image | Gate before catalog-page guide |
|---|---|---|---|
| AWS | [Steps](../agents/aws/guide/README.md) | None verified | Kaliper client and redirect URI must be approved for AWS OAuth; regional endpoint and IAM access must be tested. |
| Box | [Steps](../agents/box/guide/README.md) | Admin Console MCP filter | Replace automatic sign-in with an approved OAuth app or own-app setup; correct tool names. |
| Cal.com | [Steps](../agents/calcom/guide/README.md) | None verified | Test Kaliper OAuth registration and confirm booking changes require approval. |
| ClickHouse Cloud | [Steps](../agents/clickhouse/guide/README.md) | Connect menu and MCP switch | Test admin enablement, OAuth, and a read tool on a fresh service; correct the catalog tool list. |
| Dropbox | [Steps](../agents/dropbox/guide/README.md) | None verified | Confirm Kaliper is a trusted client or register and distribute an app with a narrow scope set. |
| MongoDB Atlas | [Steps](../agents/mongodb-atlas/guide/README.md) | None verified | Build the admin-provisioned service-account flow or obtain supported-client registration; correct tool names. |
| Railway | [Steps](../agents/railway/guide/README.md) | None verified | Test Kaliper OAuth registration, workspace selection, and confirmation for redeploy. |
| Stripe | [Steps](../agents/stripe/guide/README.md) | None verified | Test sandbox consent, narrow permissions, and confirmation for refunds. |
| Zoom Meetings | [Steps](../agents/zoom/guide/README.md) | None verified | Correct the endpoint, use manual OAuth app setup, and correct tool names. |

The Box and ClickHouse images were taken from official vendor documentation. They are focused references only; they were not captured during a Kaliper connection. Public setup-screen images were not verified for the other seven. No placeholder or unrelated marketing screenshot was added.

## Completion sequence

1. Fix the catalog endpoint, connection method, and tool names wherever the table identifies a blocker.
2. Run each vendor connection on a fresh test account, including a harmless read tool and any write confirmation.
3. Capture each actual setup step at the same 1440 × 900 browser size, crop to the control, hide identifiers, and save a step-named WebP under that agent's `guide/` folder.
4. Have a second person repeat the guide. Add the `connection.guide` object, set `checked` to that real date, and run `uv run catalog guide-images` for its hashes and sizes.
5. Validate the catalog and check the rendered guide at phone width and in both themes.
