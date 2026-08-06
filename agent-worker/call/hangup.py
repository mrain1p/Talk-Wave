"""Ending a call for real.

Its own module because three separate things end calls — the DJ wrapping up,
the idle watcher giving up, and the hard time limit — and they must all end it
the same way, including waiting for the goodbye to actually be heard.
"""

from __future__ import annotations

import asyncio
import logging
import time

from livekit.agents import JobContext

log = logging.getLogger("callin.agent")

# How long to wait for a sign-off to BEGIN before giving up on it, and how
# long to let it run once it has. The first number is the one that matters:
# the model emits the tool call and the sign-off in the same turn, so at the
# moment we start waiting the agent is still thinking, not speaking.
SPEECH_START_GRACE = 8.0
SPEECH_MAX = 25.0
# The state has to stay quiet this long to count as finished. An agent between
# two sentences reads as not-speaking for a moment, and a single sample would
# take that for the end of the goodbye.
QUIET_CONFIRM = 0.8
# A beat after the last word, so the line does not close on the final syllable.
FINAL_BEAT = 0.8


async def await_sign_off(session, what: str = "sign-off") -> None:
    """Block until the DJ has finished speaking, or until it is clearly not
    going to.

    This exists because the line was closing on the first word of the goodbye,
    every single time the DJ ended a call itself. From the transcripts of
    2026-08-06 the DJ's last turn was "Fair", "Right" and "I'll" — three calls,
    three one-word sign-offs, each followed immediately by the room being
    deleted.

    The old code slept one second and then broke out of its poll the moment
    the agent was NOT speaking, which reads as "the goodbye has finished". At
    that point in the turn it means the opposite: the tool call and the
    sign-off come from the same model turn, so a second later the agent is
    still THINKING — it has not started talking yet. Breaking there hung up
    1.8 seconds after the tool fired, mid-first-word.

    So it is two phases. Wait for speech to start; then wait for it to stop
    and stay stopped. A model that says nothing at all costs the grace period
    and no more.
    """
    started = False
    deadline = time.time() + SPEECH_START_GRACE
    while time.time() < deadline:
        if getattr(session, "agent_state", None) == "speaking":
            started = True
            break
        await asyncio.sleep(0.2)

    if not started:
        log.info("no %s was spoken within %.0fs — closing the line", what,
                 SPEECH_START_GRACE)
        return

    deadline = time.time() + SPEECH_MAX
    quiet_since = None
    while time.time() < deadline:
        if getattr(session, "agent_state", None) == "speaking":
            quiet_since = None
        elif quiet_since is None:
            quiet_since = time.time()
        elif time.time() - quiet_since >= QUIET_CONFIRM:
            break
        await asyncio.sleep(0.2)
    else:
        log.warning("%s ran past %.0fs — closing the line anyway", what,
                    SPEECH_MAX)

    await asyncio.sleep(FINAL_BEAT)


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
