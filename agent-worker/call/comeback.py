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
    The event unwrap lives once in watch.on_dj_line now; this keeps only what it
    does with the line — by the time the come-back runs the turn is long gone."""
    from . import watch

    def _remember(text: str) -> None:
        guard.last_dj_line = text

    watch.on_dj_line(session, _remember)


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
    # The director's second slice (see asks.OpenAskComeback): a hold that
    # cut into an open task must return TO the task, not just to the room.
    # Skipped when the goodbyes are done — the arc's branch below owns that.
    arc_now = getattr(guard, "arc", None)
    asks = getattr(guard, "asks", None)
    if asks is not None and not (arc_now is not None and arc_now.ending):
        acted_at = getattr(getattr(guard, "call_actions", None),
                           "taken_at", None) or []
        open_asks = asks.unanswered(list(acted_at))
        if open_asks:
            nod += (
                f" And before the break they asked for: "
                f"\"{str(open_asks[-1])[:120]}\" — it has not happened yet. "
                "Pick that task back up in the same breath as your return, "
                "without making them ask again.")
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
    # A hold that interrupted an ENDED conversation must not restart it: on
    # the 2026-08-25 harness call the caller had signed off, the announcement
    # aired, and this instruction's "pick the conversation up" produced
    # "Alright, I'm back" to a caller who was already gone — the call ran a
    # minute past its end. The arc (call/arc.py) is the one thing that knows,
    # and it rides the guard the same way last_dj_line does.
    arc = getattr(guard, "arc", None)
    if arc is not None and arc.ending:
        instructions = (
            "You stepped away to let something go out on air, and the "
            "caller had already said their goodbye before it. Do not "
            "restart the conversation and do not say you're back: one "
            "short, warm sign-off — thank them for calling — and use the "
            "end_call tool in this same turn." + nod
        )
    else:
        instructions = (
            "You just stepped away to let something go out on air, and "
            "you're back on the call now. Say so in one short line — "
            "\"alright, I'm back\" — and pick the conversation up where "
            "you left it, in your own voice. Don't apologise at length, "
            "don't recap, and don't start a new topic." + nod
        )
    try:
        await session.generate_reply(instructions=instructions)
    except asyncio.CancelledError:
        # The station started talking again while we were coming back. The
        # caller is still on hold and still knows it, so this simply stops —
        # no second hand-over line, no apology for a return that never landed.
        raise
    except Exception as e:                                     # noqa: BLE001
        log.debug("could not generate the back-from-air line: %s", e)
        try:
            session.say(
                "That's gone out — thanks for calling, take care now."
                if arc is not None and arc.ending else
                "Alright, I'm back — where were we?",
                allow_interruptions=True,
                add_to_chat_ctx=False,
            )
        except Exception:                                      # noqa: BLE001
            pass
