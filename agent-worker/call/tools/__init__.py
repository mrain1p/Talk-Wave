"""The tools the caller's DJ can reach.

`registry` describes the whole station surface once — which tools exist, what
unlocks each, and whether MCP or one of our own wrappers serves it. The other
modules implement the wrappers, grouped by what they touch:

    music       the library and the queue
    broadcast   anything that makes the on-air DJ produce sound
    control     the call itself

Adding a tool is one entry in `registry.TOOLS` plus one function here.
"""

from .broadcast import build_on_air_tools
from .control import build_call_control_tools
from .music import build_library_tools
from .registry import (
    catalogue,
    effective_tools,
    library_search_needs_mcp,
    mcp_allowlist,
)

__all__ = [
    "build_call_control_tools",
    "build_library_tools",
    "build_on_air_tools",
    "catalogue",
    "effective_tools",
    "library_search_needs_mcp",
    "mcp_allowlist",
]
