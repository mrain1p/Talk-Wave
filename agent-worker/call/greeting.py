"""Picking up: the first thing the caller hears.

Split out of lifecycle.py at 0.10.106. lifecycle.py is behaviours attached to a
session for the life of a call; this is the one-shot at the start of it, and it
grew a second job — waiting for the on-air DJ to stop talking before the
greeting goes out, which until now it did not do at all.

Three ways a pickup can produce silence, and all three are handled here
because all three have happened on real calls:

  * the model raises, and the caller hears nothing until they give up
  * the SDK swallows a "recoverable" LLM error, so generate_reply returns with
    no exception AND no reply (three Gemini 504s, 43 seconds of dead air,
    2026-08-11)
  * the on-air DJ is mid-link, and the greeting used to simply talk over it
"""

from __future__ import annotations

import logging

from livekit.agents import AgentSession

log = logging.getLogger("callin.agent")


# The user turn the call opens with, so the DJ has something to answer.
#
# It is not something the caller said — it describes the situation to the
# model — and anything that treats it as a caller turn gets the transcript
# wrong; handoff.is_prime is what drops it (and every bracketed note like it)
# on the way to the written record. Bracketed, so the speech filter strips it
# if it ever reaches the voice.
CALL_OPENING_PRIME = (
    "[Call connected. The caller is on the line and has not spoken yet — "
    "you speak first.]"
)


# How long a brand-new caller may be held before the greeting, when the on-air
# DJ is mid-link. Shorter than OnAirGuard.MAX_HOLD (45s): that budget is for a
# hold MID-conversation. It was 12 — chosen when pickup had no hold UI, where
# silence right after the ring read as a failed call. Two things made 12 wrong:
# the widget now says "you're on hold, the DJ is on the station mic" from the
# moment the gate closes, so the wait explains itself; and the hold runs on
# CALLER time — a caller joining mid-link has the stream buffer (~22s on the
# operator's station) plus the link's tail still to hear, so 12s guaranteed the
# greeting landed on top of it. Room callin-o-643dc6d2993e (2026-08-18): hold
# opened at pickup, ran 34s, greeting went out 28s before the caller's copy of
# the link finished.
GREET_HOLD_SECS = 30.0

# How long the generated greeting gets to reach the caller's ear before the
# canned pickup takes over. The 2026-08-11 call is the sizing incident: three
# "recoverable" Gemini 504s in a row, 43 seconds of dead air after the pickup
# click, and the caller said "Hello" into silence — the record-check fallback
# below only ran after generate_reply finally returned. Ten seconds covers a
# slow-but-alive turn (cloud TTFT is budgeted 10s/attempt in llm_pace, typical
# draws are 1.5-6.5s, plus TTS first-audio ~1-2.6s) while beating that failure
# by half a minute. Counted from generate_reply, so an on-air GREET_HOLD wait
# never eats the budget.
GREET_RACE_SECS = 10.0

# One canned pickup, used by every fallback: the race timeout, the sync
# failure, and the swallowed-recoverable case that produced no audio.
CANNED_PICKUP = ("Hey — you're through to the booth. Bear with me a second, "
                 "the line's a bit rough tonight. What can I do for you?")


async def greet(session: AgentSession, cfg: dict, record=None, air=None,
                persona: dict | None = None, show_name: str = "") -> None:
    """Pick up. Both styles stay in persona and carry the show; the toggle is
    only whether the DJ opens with an invitation or lets the caller lead.

    `air` is the overlap guard, and passing it is what stops the greeting
    going out ON TOP of a live link. Every other DJ turn has waited for clear
    air since 0.10.x; the greeting never did, because the watch loop's first
    pass was written to close the gate "without the greeting being cut off".
    That protected the greeting from the guard and left the audience hearing
    two of the same voice — the operator's report, and reproducible by ringing
    in while the station is mid-announcement.
    """
    if air is not None and getattr(air, "enabled", False):
        waited = await air.wait_until_clear(GREET_HOLD_SECS)
        if waited > 0.5:
            log.info("held the greeting %.1fs for the on-air DJ", waited)
        if record is not None and waited >= GREET_HOLD_SECS:
            # Only the timeout is worth a line: it means the greeting went out
            # over the top after all, which is the thing this exists to stop.
            record.problem(
                f"The on-air DJ was still speaking after {waited:.0f}s, so the "
                "greeting went out anyway rather than leave the caller in "
                "silence. If this recurs, the station is talking more than the "
                "call line leaves room for — raise the hold, or have the DJ "
                "hand over before picking up."
            )
    if str(cfg.get("greeting_style") or "inviting").lower() == "in-world":
        default_greeting = (
            "Pick up the call in character, mid-world — you were just on air. If "
            "something notable happened on the broadcast in the last little "
            "while, let it colour how you answer. One short line, the way a real "
            "DJ picks up mid-show. No question, no list of what you can do — "
            "just be there, and let them say why they called."
        )
    else:
        default_greeting = (
            "Pick up the call in character — you were just on air, and if "
            "something notable happened on the broadcast, let it colour the "
            "greeting. One short line, then invite them in with a single open "
            "question in your own voice: what's on their mind, or whether "
            "there's something they'd like to hear. One question, not a menu, "
            "and never a list of what you can do."
        )
    greeting = str(cfg.get("greeting") or "").strip() or default_greeting
    # An open line changes the one question the pickup asks. Appended rather
    # than woven in, so it works the same whether the operator is using the
    # default greeting or one of their own — and adds nothing at all when no
    # line is up, which is what keeps every existing call byte-identical.
    from openlines import prompt as open_lines

    greeting += open_lines.greeting_clause(cfg, persona, show_name)
    try:
        # The greeting is generated before the caller has said anything, and
        # the DJ usually reaches for a tool while writing it ("what's playing
        # right now?"). That leaves a function call as the FIRST turn in the
        # conversation, and Gemini rejects the whole request outright:
        #   "Please ensure that function call turn comes immediately after a
        #    user turn or after a function response turn."  (400, fatal)
        # Reproduced directly against the API: identical history with a user
        # turn in front of it passes. So the call opens with one, describing
        # the situation rather than putting words in the caller's mouth. It is
        # never spoken — bracketed text is stripped on its way to the voice.
        ret = session.generate_reply(
            user_input=CALL_OPENING_PRIME,
            instructions=greeting,
        )
        # THE RACE (0.99.1). Against the real SDK, generate_reply returns a
        # SpeechHandle synchronously and awaiting it waits for FULL playout —
        # so the old `await` meant nothing could interrupt a greeting that
        # was never going to arrive. Now: wait up to GREET_RACE_SECS for the
        # DJ to actually START speaking (the same agent_state signal the
        # first-word stamp listens to); lost the race, the pending handle is
        # killed BEFORE the canned line speaks, so a generation landing a
        # moment later has nothing left to play, and speech scheduling being
        # serial means overlap is structurally impossible. The drill and the
        # test fakes define generate_reply as a coroutine — no handle, no
        # race, awaited exactly as before.
        handle = ret if hasattr(ret, "interrupt") else None
        if handle is None:
            await ret
        else:
            import asyncio

            started = asyncio.get_running_loop().create_future()

            def _on_state(ev) -> None:
                if (getattr(ev, "new_state", None) == "speaking"
                        and not started.done()):
                    started.set_result(None)

            if getattr(session, "agent_state", None) == "speaking":
                started.set_result(None)
            session.on("agent_state_changed", _on_state)
            try:
                try:
                    await asyncio.wait_for(asyncio.shield(started),
                                           GREET_RACE_SECS)
                except asyncio.TimeoutError:
                    pass
                if not started.done():
                    handle.interrupt(force=True)
                    log.warning(
                        "greeting not on air after %.0fs — canned pickup",
                        GREET_RACE_SECS)
                    try:
                        await session.say(CANNED_PICKUP)
                    except Exception:                          # noqa: BLE001
                        pass
                    return
                await handle
            finally:
                session.off("agent_state_changed", _on_state)
    except Exception as e:
        # A model outage at pickup used to mean the caller heard NOTHING until
        # they gave up. A canned line through the TTS keeps the call alive —
        # later turns may succeed once the provider recovers.
        log.warning("greeting failed (%s) — using a canned pickup", e)
        try:
            await session.say(CANNED_PICKUP)
        except Exception:
            pass
        return
    # The exception branch above is only HALF the failure surface: the SDK
    # swallows LLM errors it marks `recoverable` — generate_reply returns
    # with no exception and no reply. Observed live (2026-08-11): three
    # recoverable Gemini 504s in a row, 43 seconds of dead air, and the
    # caller said "Hello" into silence. The record is the ground truth of
    # whether the DJ actually spoke; if it says no, the canned line goes out.
    if record is not None and not record.data.get("firstWordAt") and not any(
        t.get("who") == "dj" for t in record.data.get("turns", ())
    ):
        log.warning("greeting produced no DJ audio — using a canned pickup")
        try:
            await session.say(CANNED_PICKUP)
        except Exception:
            pass
