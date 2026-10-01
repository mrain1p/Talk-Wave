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

It also answers what the caller said that the SDK threw away. A turn that ends
while the DJ is saying a line nothing may cut — the hand-over to air, the
quiet-caller check-in — is dropped by the agents SDK outright: no reply, and
never added to the conversation. Room 7fb56fb676bb, 2026-10-01: "Can you hear
me?" landed during the hand-over, the hold ran thirty seconds, and the DJ came
back with "you were telling me about your song" — picking up a conversation
that never happened, because the one that did was gone.
"""

from __future__ import annotations

import asyncio
import logging
import time

from livekit.agents import AgentSession

from . import background

log = logging.getLogger("callin.agent")

#: What the agents SDK logs as it throws a caller's turn away. Matched exactly,
#: and pinned against the installed SDK by the tests, so an upgrade that
#: rewords it fails the build instead of quietly dropping callers' words again.
SDK_DROPPED_TURN = ("skipping reply to user input, current speech generation "
                    "cannot be interrupted")


def attach_air_watch(session, guard) -> None:
    """Remember the DJ's last line, so the come-back knows what not to repeat,
    and catch the caller's words the SDK drops, so the come-back can answer
    them. The event unwrap lives once in watch.on_dj_line now; this keeps only
    what it does with the line — by the time the come-back runs the turn is
    long gone."""
    from . import watch

    def _remember(text: str) -> None:
        guard.last_dj_line = text

    watch.on_dj_line(session, _remember)
    catch_dropped_turns(session, guard)


class _DroppedTurns(logging.Handler):
    """The SDK's dropped-turn warning, caught for one call and handed on."""

    def __init__(self, loop, on_drop) -> None:
        super().__init__(logging.WARNING)
        self._loop = loop
        self._on_drop = on_drop

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.getMessage() != SDK_DROPPED_TURN:
                return
            text = str(getattr(record, "user_input", "") or "").strip()
            if text:
                self._loop.call_soon_threadsafe(self._on_drop, text)
        except Exception:                                      # noqa: BLE001
            pass    # a handler that raises takes the SDK's own log call with it


def catch_dropped_turns(session, guard) -> None:
    """Keep the words the SDK drops, and answer them when nothing is in the way.

    The SDK says which turn it dropped only in its own log line, so that is
    what is listened for — scoped to this call, and let go when it closes.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return          # not on a call's event loop, so nothing to answer on
    guard.dropped_words = []

    def _dropped(text: str) -> None:
        guard.dropped_words.append(text)
        guard.dropped_at = time.monotonic()
        log.warning("the caller spoke during a line that could not be cut and "
                    "the SDK dropped it — answering once it is done: %s",
                    text[:120])
        if getattr(guard, "air_log", None):
            guard.air_log.note("the caller's words were dropped mid-line")
        task = getattr(guard, "_answering", None)
        if task is None or task.done():
            guard._answering = background.spawn(answer_dropped(guard, session))

    sdk = logging.getLogger("livekit.agents")
    handler = _DroppedTurns(loop, _dropped)
    sdk.addHandler(handler)
    session.on("close", lambda *_: sdk.removeHandler(handler))


async def answer_dropped(guard, session) -> None:
    """Answer the dropped words once the line and any hold are over.

    The line that refused the turn plays out first — the SDK would not cut it,
    and talking over it is the one thing this must not do. Then any hold is
    waited out. A return from air already under way answers the words itself
    (see _say_it); if the station cuts that return off, they go back on the
    list and the next clear answers them here — a resumed break spawns no
    second return.
    """
    speech = getattr(session, "current_speech", None)
    if speech is not None:
        try:
            await speech.wait_for_playout()
        except Exception:                                      # noqa: BLE001
            pass
    for _ in range(4):
        await guard.wait_until_clear()
        back = getattr(guard, "_comeback", None)
        if back is not None and not back.done():
            await asyncio.wait({back})
            continue
        words = take_dropped(guard)
        if words:
            await _answer(guard, session, words)
        return


def take_dropped(guard) -> str:
    """The dropped words, joined, with the list emptied — or "" if the caller
    has spoken since: that newer turn reached the DJ the ordinary way and got
    its own answer, and replying to the older one would answer a moment that
    has passed (the floor's own rule, call/floor.py)."""
    words = list(getattr(guard, "dropped_words", None) or [])
    if not words:
        return ""
    guard.dropped_words = []
    floor = getattr(guard, "floor", None)
    if floor is not None and floor.last_caller_at > getattr(
            guard, "dropped_at", 0.0):
        return ""
    return " ".join(words)


def _put_back(guard, words: str, in_context: bool = False) -> None:
    """Return words that were taken but not answered. `in_context` when a
    reply had already started: the SDK adds user_input to the conversation as
    the reply begins, so the next attempt must not add it a second time."""
    if words and isinstance(getattr(guard, "dropped_words", None), list):
        guard.dropped_words.insert(0, words)
        if in_context:
            guard.dropped_in_context = True


def _as_their_message(guard, words: str) -> dict:
    """The words as the caller's own message — unless a cut-off reply already
    put them in the conversation."""
    if getattr(guard, "dropped_in_context", False):
        guard.dropped_in_context = False
        return {}
    return {"user_input": words}


async def _answer(guard, session, words: str) -> None:
    reply = {"instructions": (
        "The caller spoke while you were saying a line that could not be "
        "cut, and their words have only just reached you — they are the "
        "caller's last message. Answer them now, in your own voice, without "
        "making them repeat themselves.")}
    reply.update(_as_their_message(guard, words))
    floor = getattr(guard, "floor", None)
    try:
        if floor is None:
            await session.generate_reply(**reply)
            return
        async with floor.take("the caller's dropped words") as mine:
            if mine:
                await session.generate_reply(**reply)
    except Exception as e:                                     # noqa: BLE001
        log.warning("could not answer the caller's dropped words: %s", e)


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
    # What the caller said that the SDK dropped during the hand-over — the
    # return is the moment to answer it, and only the model can.
    words = take_dropped(guard)
    # One of the three turns that can start while another is generating — the
    # promise nudge is the one it would collide with, and both are about a
    # caller who has been left waiting. See call/floor.py.
    floor = getattr(guard, "floor", None)
    if floor is not None:
        async with floor.take("the back-from-air line") as mine:
            if not mine:
                _put_back(guard, words)
                return
            await _say_it(guard, session, nod, words)
        return
    await _say_it(guard, session, nod, words)


async def _say_it(guard, session: AgentSession, nod: str,
                  words: str = "") -> None:
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
    reply = {"instructions": instructions}
    if words:
        # As the caller's own message, so the conversation holds what they
        # really said — the drop left it out of the context entirely.
        reply.update(_as_their_message(guard, words))
        reply["instructions"] += (
            " While you were away the caller said something that has not been "
            "answered — it is their last message. Answer it in the same "
            "breath as your return, without making them repeat it.")
    handle = None
    try:
        # KEEP THE HANDLE, don't just await the call. generate_reply returns a
        # SpeechHandle whose __await__ is a SHIELDED wait, so cancelling this
        # task ends our WAIT and not the playback — the come-back line carried
        # on out of the caller's speaker straight over the station's next
        # utterance, which is the one thing the cancel exists to prevent.
        handle = session.generate_reply(**reply)
        await handle
    except asyncio.CancelledError:
        # Cut off before it could answer them: the words wait for the next
        # clear (answer_dropped), since a resumed break has no second return.
        _put_back(guard, words, in_context=handle is not None)
        # The station started talking again while we were coming back. The
        # caller is still on hold and still knows it, so this simply stops —
        # no second hand-over line, no apology for a return that never landed.
        # force=True: the line is not the caller's to interrupt, so the handle
        # would refuse a polite one. getattr, because the test fakes (and any
        # older SDK) hand back a bare coroutine with nothing to cut.
        cut = getattr(handle, "interrupt", None)
        if cut is not None:
            try:
                cut(force=True)
            except Exception:                                  # noqa: BLE001
                pass
        raise
    except Exception as e:                                     # noqa: BLE001
        log.debug("could not generate the back-from-air line: %s", e)
        _put_back(guard, words, in_context=handle is not None)
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
