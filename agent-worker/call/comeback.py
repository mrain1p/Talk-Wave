"""Coming back to the caller after the broadcast has had its turn.

Split from air.py at 0.10.125 so the guard's watch loop could stop AWAITING
this. That mattered more than it sounds: the loop was blocked for however long
the come-back took, so it could not see the station start speaking again, and
the only defence was a blanket two-second pad on the end of every hold
(SETTLE_SECS) whether or not there was anything to ride out.

Now the come-back is a task the loop can cancel. A banter break — several
utterances a second or two apart — cancels it mid-sentence and the hold simply
continues, with no second hand-over line, because the caller was never told the
hold was over. That covers a gap of ANY length rather than only one shorter
than the pad, and a break that really has finished costs nothing.
"""

from __future__ import annotations

import asyncio
import logging

from livekit.agents import AgentSession

log = logging.getLogger("callin.agent")


def attach_air_watch(session, guard) -> None:
    """Remember the DJ's last line, so the come-back knows what not to repeat.

    Same shape as door.attach_door_watch and for the same reason: the only
    place that knows what was actually said is the event, and by the time the
    come-back runs the turn is long gone.
    """

    def _on_said(ev) -> None:
        item = getattr(ev, "item", None)
        if getattr(item, "role", None) != "assistant":
            return
        text = str(getattr(item, "text_content", "") or "").strip()
        if text:
            guard.last_dj_line = text

    session.on("conversation_item_added", _on_said)


async def come_back(guard, session: AgentSession) -> None:
    """Say something on the way back from the broadcast.

    The hand-over line told the caller to hold; nothing told them the hold was
    over. So the DJ went quiet mid-conversation, came back, and then waited for
    the caller to speak first — from the caller's end that is indistinguishable
    from the line having dropped, and it is the point at which they hang up.
    Observed on the calls of 2026-08-06, where the silences a caller could not
    account for are the whole story.

    `generate_reply` rather than a canned line, because the useful version
    picks the thread back up ("right, I'm back — you were saying about the
    rock") and only the model knows what was being said. The canned line is the
    fallback: coming back saying SOMETHING beats coming back silently, which is
    the failure being fixed.
    """
    aired = (guard.aired_text or "").strip()
    guard.aired_text = ""
    nod = (
        f" What went out on air was: \"{aired[:200]}\" — a passing nod to "
        "it is fine, but don't read it back to them."
    ) if aired else ""
    # And what the DJ told them on the way OUT, which is the half that was
    # missing. "Don't recap" cannot be obeyed by a model that has not been
    # told what would count as a recap: on 2026-08-16 the DJ said "I just sent
    # that shoutout for Marcus and the Fleetwood Mac track is lined up" as it
    # stepped away, then came back and said the same two things again. The
    # operator's steer is that referring to what just aired is GOOD continuity
    # and only the verbatim repeat is wrong, so this names the sentence to
    # avoid rather than forbidding the subject.
    before = (getattr(guard, "last_dj_line", "") or "").strip()
    if before:
        nod += (
            f" Before you stepped away you told them: \"{before[:200]}\". "
            "They have heard that — carry on from it, don't say it again.")
    # One of the three turns that can start while another is generating — the
    # promise nudge is the one it would collide with, and both are about a
    # caller who has been left waiting. See call/floor.py.
    floor = getattr(guard, "floor", None)
    if floor is not None:
        async with floor.take("the back-from-air line") as mine:
            if not mine:
                return
            await _say_it(guard, session, nod)
        return
    await _say_it(guard, session, nod)


async def _say_it(guard, session: AgentSession, nod: str) -> None:
    try:
        await session.generate_reply(instructions=(
            "You just stepped away to let something go out on air, and "
            "you're back on the call now. Say so in one short line — "
            "\"alright, I'm back\" — and pick the conversation up where "
            "you left it, in your own voice. Don't apologise at length, "
            "don't recap, and don't start a new topic." + nod
        ))
    except asyncio.CancelledError:
        # The station started talking again while we were coming back. The
        # caller is still on hold and still knows it, so this simply stops —
        # no second hand-over line, no apology for a return that never landed.
        raise
    except Exception as e:                                     # noqa: BLE001
        log.debug("could not generate the back-from-air line: %s", e)
        try:
            session.say(
                "Alright, I'm back — where were we?",
                allow_interruptions=True,
                add_to_chat_ctx=False,
            )
        except Exception:                                      # noqa: BLE001
            pass
