"""An agent that signs in reads the token the router sent with this call."""

from langchain_core.tools import tool

from agent_kit import auth
from tests.test_mcp_server import rpc, serve  # noqa: F401 - the fixture


@tool
def whoami() -> str:
    """Says which token this call carries."""
    return f"called with {auth.access_token()}"


def test_a_tool_reads_the_token_sent_with_its_own_call(serve):  # noqa: F811
    base = serve([whoami])
    _, _, reply = rpc(base, "tools/call", {"name": "whoami", "arguments": {}},
                      headers={"authorization": "Bearer at-123"})
    assert reply["result"]["content"][0]["text"] == "called with at-123"
    # The next call without one has none: nothing is kept between calls.
    _, _, reply = rpc(base, "tools/call", {"name": "whoami", "arguments": {}})
    assert reply["result"]["isError"] is True
    assert "without a sign-in token" in reply["result"]["content"][0]["text"]
