"""Ending a call for real.

Its own module because three separate things end calls — the DJ wrapping up,
the idle watcher giving up, and the hard time limit — and they must all end it
the same way.
"""

from __future__ import annotations

import logging

from livekit.agents import JobContext

log = logging.getLogger("callin.agent")


async def end_call(ctx: JobContext, reason: str) -> None:
    """Actually hang up.

    ctx.shutdown() alone only ends the AGENT's job — the caller would stay
    connected to a DJ-less room, mic hot and timer running, looking "on the
    line" forever. Deleting the room disconnects everyone; the widget hears it
    as a normal remote hangup.
    """
    log.info("ending call (%s)", reason)
    try:
        from livekit import api as lk_api

        await ctx.api.room.delete_room(lk_api.DeleteRoomRequest(room=ctx.room.name))
    except Exception as e:
        log.warning("room delete failed (%s) — agent will still leave", e)
    ctx.shutdown(reason=reason)
