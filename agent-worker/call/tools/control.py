"""Letting the DJ close the call, the way a presenter does."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from livekit.agents import JobContext

from ..background import spawn
# Imported under a different name on purpose: the tool below is itself called
# end_call, and the closure shadowed this import — so the DJ's sign-off raised
# TypeError inside a background task and the line never actually closed. The
# call then ran on until the idle watcher gave up, which looks like the DJ
# saying goodbye and then refusing to hang up.
from ..hangup import end_call as hang_up

log = logging.getLogger("callin.agent")


def _clock_start() -> float:
    return time.time()


def build_call_control_tools(
    ctx: JobContext,
    get_session: Callable[[], object],
    started_at: float,
    min_call_secs: float = 60.0,
) -> list:
    """Lets the DJ hang up, the way a presenter closes a call.

    Until now a finished conversation just sat there: the caller had said
    goodbye, the DJ had said goodbye, and the line stayed open until the idle
    watcher nudged twice or the hard limit hit. A real DJ says "anything else
    before I let you go?" and then ends it.

    `get_session` is a callable rather than the session itself because tools
    are built before the AgentSession exists — the tool needs to read it at
    call time, not at build time.

    Two guards, because a model that decides to hang up early is worse than
    one that lingers:
      * nothing can end a call before `min_call_secs`, whatever the model
        thinks — it defaults to a minute and the operator can change it;
      * the goodbye is allowed to finish playing before the room closes.

    Setting the floor to 0 removes the first guard entirely. That is a real
    choice rather than a hidden one: the DJ deciding a call is over after two
    words has happened, and a caller hung up on mid-sentence has no way to
    tell that from the line dropping.
    """
    from livekit.agents import llm as lk_llm

    import time as _t

    MIN_CALL_SECS = max(0.0, float(min_call_secs or 0))
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
            # The old wording here ("see what they actually want") was read as
            # an instruction to go find something else to talk about, so a
            # caller who said goodbye at forty seconds got a brand new line of
            # questioning immediately after the DJ's own sign-off. Say what is
            # actually true: the goodbye stands, only the timing is refused.
            left = max(1, int(MIN_CALL_SECS - elapsed))
            return (
                f"Not yet — the line can't close for another {left}s. This is a "
                "timing rule, not a disagreement about the goodbye: if they're "
                "done, they're done. Stay in the moment you were both already "
                "in, one warm half-line, and let it breathe. Do NOT open a new "
                "subject, ask what else they need, or start questioning someone "
                "who has just said goodbye. Try again when they speak next."
            )
        if ending["done"]:
            return "Already wrapping up — just finish your sign-off."
        ending["done"] = True

        async def _close() -> None:
            session = get_session()
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
            await hang_up(ctx, f"the DJ wrapped up the call ({reason or 'done'})")

        spawn(_close())
        # The sign-off is spoken in the SAME turn as this call, so by the time
        # anyone reads this the caller has already been said goodbye to. Asking
        # for one here produced a second, different farewell every time — the
        # caller heard two. There is nothing left to say; if the model insists
        # on speaking, cap it at a couple of words.
        return (
            "The line is closing. Your goodbye has already been said and is "
            "playing now — it is the last thing this caller hears. Do not say "
            "it again and do not add a farewell. At most, two or three words "
            "(\"night, now.\"). Anything longer and they hear two goodbyes."
        )

    return [end_call]


