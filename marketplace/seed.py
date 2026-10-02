"""The one-time import: today's agents, from the files rytangle keeps them in.

router/config/agents.json says what the router runs; catalog/extras.json says
how each agent is shown and connected. Their per-client `sample` block is left
behind on purpose: the global catalog holds nothing about any one client.
"""

from __future__ import annotations

import copy

from .entry import FORMAT

DISPLAY_KEYS = ("name", "summary", "categories", "logo", "examples")


def entries_from_files(
    registry: dict,
    extras: dict,
    *,
    needs_router: str,
    publisher: str = "Kaliper",
    version: str = "1.0.0",
) -> list[dict]:
    known = [agent["name"] for agent in registry["agents"]]
    unknown = sorted(set(extras) - set(known))
    if unknown:
        raise ValueError(f"extras.json describes {', '.join(repr(u) for u in unknown)}, which agents.json does not run")

    entries = []
    for agent in registry["agents"]:
        extra = extras.get(agent["name"])
        if extra is None:
            raise ValueError(f"extras.json has no display details for agent {agent['name']!r}")
        entry = {
            "format": FORMAT,
            "id": agent["name"],
            "version": version,
            "kind": "builtin",
            "needs_router": needs_router,
            "publisher": publisher,
            "display": {key: extra[key] for key in DISPLAY_KEYS},
            "connection": extra["connection"],
            "router": {
                "source": agent["source"],
                "source_config": registry["sources"][agent["source"]],
                "agent": agent,
                "tool_descriptions": {
                    tool: registry["tools"][tool]["description"] for tool in agent.get("tools", [])
                },
            },
        }
        entries.append(copy.deepcopy(entry))
    return entries
