"""The tools the caller's DJ can reach.

`registry` describes the whole station surface once — which tools exist, what
unlocks each, and whether MCP or one of our own wrappers serves it. The other
modules implement the wrappers, grouped by what they touch:

    music       name search, requests and the queue
    discovery   the other ways into the library: sound, neighbours, filters
    curation    a record's standing rather than its turn: likes, never-play
    broadcast   anything that makes the on-air DJ produce sound
    control     the call itself
    late_match  what a request does after its tool has already answered
    rows        shaping one station listing row into something a DJ can read

Adding a tool is one entry in `registry.TOOLS` plus one function here.
"""

from .broadcast import build_on_air_tools
from .control import build_call_control_tools
from .curation import build_curation_tools
from .discovery import build_discovery_tools
from .music import build_library_tools
from .registry import (
    catalogue,
    effective_tools,
    library_search_needs_mcp,
    mcp_allowlist,
)

__all__ = [
    "build_call_control_tools",
    "build_curation_tools",
    "build_discovery_tools",
    "build_library_tools",
    "build_on_air_tools",
    "catalogue",
    "effective_tools",
    "library_search_needs_mcp",
    "mcp_allowlist",
]
