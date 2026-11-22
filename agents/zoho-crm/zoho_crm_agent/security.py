"""URL and destination guards for the Zoho CRM Data Insights endpoint.

The MCP URL contains a bearer-like secret in its path. Keep validation errors
generic and never include the supplied URL in an exception or representation.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Sequence
from urllib.parse import urlsplit


class UnsafeDestination(ValueError):
    """A URL is outside the connector's fixed Zoho destination boundary."""


# These are the regional host suffixes used by Zoho's MCP/API data centres.
# The hostname must also identify the CRM Data Insights server and its numeric
# tenant segment. In particular, this excludes the Data Operations server.
_REGIONS = r"(?:com|eu|in|jp|ca|sa|ae|com\.au|com\.cn)"
_INSIGHTS_HOST = re.compile(
    rf"\Azoho-crm-data-insights-[0-9]+\.zohomcp\.{_REGIONS}\Z", re.IGNORECASE
)
_MCP_PATH = re.compile(r"\A/mcp/[0-9a-fA-F]{32}/message\Z")
_ACCOUNT_HOST = re.compile(
    rf"\Aaccounts\.zoho\.{_REGIONS}\Z|\Aaccounts\.zohocloud\.ca\Z", re.IGNORECASE
)
_TOKEN_PATH = "/oauth/v2/token"

Resolver = Callable[..., Sequence[tuple]]


def validate_mcp_url(value: str) -> str:
    """Accept only a Zoho CRM Data Insights URL with its expected secret path."""
    if not isinstance(value, str) or not value or value.strip() != value:
        raise UnsafeDestination("invalid Zoho CRM Data Insights URL")
    if len(value) > 2048 or any(ord(char) < 0x20 or char.isspace() for char in value):
        raise UnsafeDestination("invalid Zoho CRM Data Insights URL")
    if "?" in value or "#" in value or "\\" in value:
        raise UnsafeDestination("invalid Zoho CRM Data Insights URL")

    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        raise UnsafeDestination("invalid Zoho CRM Data Insights URL") from None

    if (
        parts.scheme.lower() != "https"
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
        or not _INSIGHTS_HOST.fullmatch(host)
        or not _MCP_PATH.fullmatch(parts.path)
    ):
        raise UnsafeDestination("invalid Zoho CRM Data Insights URL")

    # The allowlisted DNS name is the only accepted host form; literal IPs,
    # localhost aliases, custom ports, other Zoho products and arbitrary hosts
    # all fail the same check above.
    authority = parts.netloc.lower()
    if authority not in (host, f"{host}:443"):
        raise UnsafeDestination("invalid Zoho CRM Data Insights URL")
    return value


def validate_oauth_token_endpoint(value: str, mcp_url: str) -> str:
    """Allow the MCP origin or an official Zoho Accounts token endpoint only."""
    mcp_url = validate_mcp_url(mcp_url)
    if not isinstance(value, str) or not value or value.strip() != value:
        raise UnsafeDestination("invalid Zoho OAuth token endpoint")
    if len(value) > 2048 or any(ord(char) < 0x20 or char.isspace() for char in value):
        raise UnsafeDestination("invalid Zoho OAuth token endpoint")
    if "?" in value or "#" in value or "\\" in value:
        raise UnsafeDestination("invalid Zoho OAuth token endpoint")

    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        port = parts.port
        mcp_host = (urlsplit(mcp_url).hostname or "").lower()
    except ValueError:
        raise UnsafeDestination("invalid Zoho OAuth token endpoint") from None

    same_mcp_origin = host == mcp_host and parts.path == _TOKEN_PATH
    official_accounts = bool(_ACCOUNT_HOST.fullmatch(host)) and parts.path == _TOKEN_PATH
    if (
        parts.scheme.lower() != "https"
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
        or not (same_mcp_origin or official_accounts)
        or parts.netloc.lower() not in (host, f"{host}:443")
    ):
        raise UnsafeDestination("invalid Zoho OAuth token endpoint")
    return value


def require_public_dns(host: str, resolver: Resolver = socket.getaddrinfo) -> None:
    """Reject destinations whose DNS answers include a non-global address."""
    try:
        answers = resolver(host, 443, type=socket.SOCK_STREAM)
    except (OSError, ValueError):
        raise UnsafeDestination("Zoho endpoint DNS could not be validated") from None
    if not answers:
        raise UnsafeDestination("Zoho endpoint DNS could not be validated")

    for answer in answers:
        try:
            address = ipaddress.ip_address(answer[4][0])
        except (IndexError, TypeError, ValueError):
            raise UnsafeDestination("Zoho endpoint DNS could not be validated") from None
        if not address.is_global:
            raise UnsafeDestination("Zoho endpoint resolved to a non-public address")


def require_public_url(value: str, resolver: Resolver = socket.getaddrinfo) -> None:
    """Resolve a previously validated URL and reject private destinations."""
    try:
        host = (urlsplit(value).hostname or "").lower()
    except ValueError:
        raise UnsafeDestination("invalid Zoho endpoint") from None
    if not host:
        raise UnsafeDestination("invalid Zoho endpoint")
    require_public_dns(host, resolver)
