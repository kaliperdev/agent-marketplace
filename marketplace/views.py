"""The two shapes the catalog is read in, besides the entry itself.

page_view: what the catalog page draws (the shape rytangle's catalog/public
           already reads, so the page needs no change to read the service).
router_registry: router/config/agents.json, rebuilt from entries; with the
           database as the master copy, this is how the router's file is made.
"""

from __future__ import annotations

from .entry import required_credentials


def _fill(text: str) -> str:
    # The router fills this from GITHUB_REPO when it runs; a catalog has no runtime.
    return str(text).replace("{github_repo}", "the configured repository")


def page_view(entry: dict) -> dict:
    display, router = entry["display"], entry["router"]
    agent = router["agent"]
    return {
        "id": entry["id"],
        "name": display["name"],
        "publisher": entry["publisher"],
        "version": entry["version"],
        "needsRouter": entry["needs_router"],
        "summary": display["summary"],
        "categories": display["categories"],
        "logo": display["logo"],
        "kind": "answers" if agent.get("passthrough") else "tools",
        "routerDescription": _fill(agent["description"]),
        "capabilities": [
            {"name": tool, "description": _fill(router["tool_descriptions"][tool])} for tool in agent["tools"]
        ],
        "credentials": required_credentials(entry),
        "connection": entry["connection"],
        "examples": display["examples"],
    }


def router_registry(entries: list[dict]) -> dict:
    sources: dict = {}
    agents: list = []
    tools: dict = {}
    for entry in entries:
        if entry.get("kind", "builtin") != "builtin":
            # agents.json can only hold code that is inside the router.
            continue
        router = entry["router"]
        source = router["source"]
        if source in sources and sources[source] != router["source_config"]:
            raise ValueError(f"agents disagree about how source {source!r} is configured ({entry['id']})")
        sources[source] = router["source_config"]
        agents.append(router["agent"])
        for tool in router["agent"]["tools"]:
            tools[tool] = {"description": router["tool_descriptions"][tool]}
    return {"sources": sources, "agents": agents, "tools": tools}
