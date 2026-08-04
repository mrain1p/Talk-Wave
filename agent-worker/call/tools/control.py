"""Letting the DJ close the call, the way a presenter does."""

from __future__ import annotations

import asyncio
import logging
import time

from livekit.agents import JobContext

from ..background import spawn
from ..hangup import end_call

log = logging.getLogger("callin.agent")


def _clock_start() -> float:
    return time.time()


def build_call_control_tools(ctx: JobContext, session_ref: dict, started_at: float) -> list:
    """Lets the DJ hang up, the way a presenter closes a call.

    Until now a finished conversation just sat there: the caller had said
    goodbye, the DJ had said goodbye, and the line stayed open until the idle
    watcher nudged twice or the hard limit hit. A real DJ says "anything else
    before I let you go?" and then ends it.

    Two guards, because a model that decides to hang up early is worse than
    one that lingers:
      * nothing can end a call in its first minute, whatever the model thinks;
      * the goodbye is allowed to finish playing before the room closes.
    """
    from livekit.agents import llm as lk_llm

    import time as _t

    MIN_CALL_SECS = 60.0
    ending = {"done": False}

    @lk_llm.function_tool(name="end_call")
    async def end_call(reason: str = "") -> str:
        """Hang up. Use ONLY once the caller has confirmed they're done — you
        asked if there was anything else and they said no, or they said
        goodbye. Say your sign-off in the same turn you call this; the line
        stays open long enough for it to play. Never use this to cut a
        conversation short."""
        elapsed = _t.time() - started_at
        if elapsed < MIN_CALL_SECS:
            return (
                "Too early to hang up — you've barely picked up. Stay with the "
                "caller and see what they actually want."
            )
        if ending["done"]:
            return "Already wrapping up — just finish your sign-off."
        ending["done"] = True

        async def _close() -> None:
            session = session_ref.get("session")
            # Let the sign-off play out. Poll rather than guess a duration: a
            # fixed sleep either clips a warm goodbye or leaves dead air after
            # a curt one.
            deadline = _t.time() + 20.0
            await asyncio.sleep(1.0)
            while _t.time() < deadline:
                if getattr(session, "agent_state", None) != "speaking":
                    break
                await asyncio.sleep(0.5)
            await asyncio.sleep(0.8)      # a beat after the last word
            await end_call(ctx, f"the DJ wrapped up the call ({reason or 'done'})")

        spawn(_close())
        return (
            "Right — say your goodbye now, one line, in character. The line closes "
            "as soon as you've finished speaking."
        )

    return [end_call]


