"""Read-only Freshdesk agent service, staged pending an authenticated probe."""

from __future__ import annotations

import os
from collections.abc import Mapping

from langchain_core.tools import StructuredTool

from agent_kit.mcp_server import serve
from agent_kit.settings import credential, missing
from freshdesk_agent.client import FreshdeskMCP, endpoint

KEYS = ("FRESHDESK_SUBDOMAIN", "FRESHDESK_API_KEY")
# Names are from Freshdesk's published tool table; live schemas still need a probe.
READ_TOOLS = {
    "fetch_ticket": ("fetchTicket", "Read one support ticket."),
    "fetch_tickets": ("fetchTickets", "List support tickets."),
    "search_tickets": ("fetchSearchTickets", "Search support tickets."),
    "fetch_ticket_conversations": ("fetchTicketConversations", "Read a ticket's conversation history."),
    "fetch_contact": ("fetchContact", "Read one contact."),
    "fetch_contacts": ("fetchContacts", "List contacts."),
    "search_contacts": ("fetchContactAutocomplete", "Search contacts by name."),
    "fetch_company": ("fetchCompany", "Read one company."),
    "search_companies": ("fetchCompanyAutocomplete", "Search companies by name."),
    "fetch_search_companies": ("fetchSearchCompanies", "Search companies using their fields."),
}


def build_tools(env: Mapping[str, str], client=None, resolver=None) -> tuple[list, str | None]:
    problem = missing(env, KEYS)
    if problem:
        return [], problem
    subdomain = credential(env, "FRESHDESK_SUBDOMAIN")
    endpoint(subdomain)
    kwargs = {"client": client} if client is not None else {}
    if resolver is not None:
        kwargs["resolver"] = resolver
    remote = FreshdeskMCP(subdomain, credential(env, "FRESHDESK_API_KEY"), **kwargs)
    tools = []
    for name, (vendor_name, description) in READ_TOOLS.items():
        def make_invoke(remote_name: str):
            def invoke(arguments: dict) -> str:
                return remote.call(remote_name, arguments)
            return invoke

        # A nested arguments object is temporary until the live schemas are verified.
        tools.append(StructuredTool.from_function(
            func=make_invoke(vendor_name), name=name, description=description,
        ))
    return tools, None


def main() -> None:
    tools, problem = build_tools(os.environ)
    serve(tools, int(os.environ.get("PORT") or 8080), problem, name="freshdesk")


if __name__ == "__main__":
    main()
