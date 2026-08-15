"""The two behaviours that end a call on a clock.

Split from lifecycle.py at 0.10.146, when it crossed the length ceiling. The
seam is the one this whole stream is about: everything left in lifecycle.py
OBSERVES a call — it logs what was heard, stamps the first word, flushes a
card, records why the session closed — and none of it ever speaks. These two
own a clock, and when the clock runs out they GENERATE A TURN and can end the
line.

That distinction is the reason both take the on-air guard. A turn generated on
a timer is indifferent to what the station is doing, and the time-limit
sign-off used to go out on top of a live link for exactly that reason. Anything
added here speaks; add it to the table in docs/the-call.md and give it the
guard.
"""

from __future__ import annotations

import asyncio
import logging
import time

from livekit.agents import AgentSession, JobContext

from .hangup import await_sign_off, end_call

log = logging.getLogger("callin.agent")


def _cancel(task: asyncio.Task):
    """lifecycle.cancel, imported lazily to keep the two modules one-way."""
    from .lifecycle import cancel

    return cancel(task)


async def _say_something(
    session: AgentSession, instructions: str, fallback: str, what: str = "line"
) -> None:
    """Generate a line in character, and if that fails, say the plain one.

    The in-character line is worth generating: a canned string in the middle
    of a persona call is jarring, and the model is the only thing that knows
    what was being talked about. But the whole failure this exists for is the
    DJ saying NOTHING — an idle goodbye that dies in the provider left a
    caller listening to a line that then just closed. Something plain and
    audible beats something perfect that never arrived.
    """
    try:
        await session.generate_reply(instructions=instructions)
        return
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning("%s failed (%s) — falling back to a canned line", what, e)
    try:
        await session.say(fallback)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning("canned %s failed too — the caller heard nothing: %s",
                    what, e)


def attach_idle_watch(
    ctx: JobContext, session: AgentSession, cfg: dict, air=None, heard=None,
    actions=None,
) -> None:
    """A caller who goes quiet gets checked on in character, then let go.

    Dead air on a phone call is worse than a graceful goodbye, and an abandoned
    tab would otherwise hold a line open until the hard time limit.

    Silence means NO DISCERNIBLE LANGUAGE, deliberately not "no sound": the
    SDK's away-state rides the VAD, and background noise — a TV, the station
    bleeding in, room hiss — kept resetting it, so the check-in never fired in
    any real room. Only a transcript with actual words counts as the caller
    being present; the clock starts each time the DJ finishes talking (a
    caller quietly listening isn't idle).

    `heard` is the call's own "has anything ever arrived from this caller"
    counter, and it separates two situations this used to treat as one. A
    caller who has been talking and then stops is thinking, or distracted, and
    deserves the patient ladder. A caller who has NEVER been heard is a broken
    media path, a blocked microphone, or a wrong device — the commonest
    outcome on this deployment by some distance — and the patient ladder is
    exactly wrong for them: on 2026-08-06 that call ran 133 seconds, produced
    one "Still with me?", and the caller hung up before the goodbye ever came.
    Nothing the DJ said named the problem, and nothing arrived in time to be
    heard.
    """
    idle_secs = int(cfg.get("idle_prompt_secs") or 0)
    if idle_secs <= 0:
        return
    max_nudges = int(cfg.get("idle_max_nudges") or 0)
    state = {"last_words": time.time(), "nudges": 0, "asked": False}

    def never_heard() -> bool:
        return heard is not None and not heard.get("n")

    def _on_transcript(ev) -> None:
        text = str(getattr(ev, "transcript", "") or "")
        if text.strip():
            state["last_words"] = time.time()
            state["nudges"] = 0
            state["asked"] = False

    session.on("user_input_transcribed", _on_transcript)

    def _note_question(ev) -> None:
        """Did the DJ just ask the caller something?

        Observed on a real call: the DJ offered a choice of two versions of a
        track and then asked "Still with me?" twice while the caller was
        deciding. A caller weighing up an answer is not an absent caller, so a
        question buys them considerably longer before anyone checks on them.
        """
        item = getattr(ev, "item", None)
        if getattr(item, "role", None) != "assistant":
            return
        text = str(getattr(item, "text_content", "") or "").strip()
        if text:
            state["asked"] = text.endswith("?")

    session.on("conversation_item_added", _note_question)

    async def _idle_watch() -> None:
        while True:
            await asyncio.sleep(1.0)
            # The clock only runs while the DJ is actually LISTENING. Pinning
            # it during speaking/thinking means the count always starts fresh
            # the moment the DJ stops talking — a long monologue can never
            # expire the timer mid-sentence, which used to fire a check-in on
            # the heels of the DJ's own turn.
            if getattr(session, "agent_state", None) != "listening":
                state["last_words"] = time.time()
                continue
            # Nor while the DJ is deliberately holding for the broadcast. The
            # session still reads as "listening" during a hold — it is waiting
            # for clear air, not for the caller — so without this the clock ran
            # and the DJ asked "still there?" for a silence it was causing
            # itself. Seen on a real call: held 10:29:47-10:30:15, check-in
            # fired at 10:30:11, and the caller had done nothing wrong.
            if air is not None and getattr(air, "on_air", False):
                state["last_words"] = time.time()
                continue
            # Nor while the DJ is mid-task ON THE CALLER'S BEHALF — a search
            # running, a request still resolving in the background. The caller
            # is waiting on US; asking whether THEY are still there is exactly
            # backwards, and it happened on a real call (a Zeppelin request
            # that took a while to resolve, then "are you still there?").
            if actions is not None and actions.is_working():
                state["last_words"] = time.time()
                continue
            # Thinking time, not dead air: give a caller who was just asked
            # something three times as long before checking on them.
            #
            # Except when nothing has ever arrived from them. The greeting
            # ends in a question on every call, so `asked` is true from the
            # first second, and tripling the window is what made the first
            # check-in land 63 seconds into a call whose caller was never
            # audible. There is no answer coming to a question they cannot
            # hear being asked.
            dead_line = never_heard()
            wait_for = (
                idle_secs if dead_line or not state["asked"] else idle_secs * 3
            )
            if time.time() - state["last_words"] < wait_for:
                continue
            if state["nudges"] >= max_nudges:
                continue
            state["nudges"] += 1
            state["last_words"] = time.time()
            first = state["nudges"] == 1
            log.info("no words from the caller for %ss — check-in %d/%d%s",
                     wait_for, state["nudges"], max_nudges,
                     " (nothing ever heard)" if dead_line else "")
            if first and max_nudges > 1:
                # Two different situations, and the caller can only act on one
                # of them. "Still there?" to somebody whose microphone is off
                # is a question they can watch arrive and cannot answer.
                instructions = (
                    # On a push-to-talk line, silence usually means the caller
                    # has not pressed the bar — telling them to "check the
                    # microphone" they deliberately have closed reads as the
                    # DJ not knowing how its own phone works. The worker can't
                    # know which surface the caller is on, so the bar is
                    # mentioned when EITHER surface has it switched on.
                    (
                        "Nothing at all has come through from the caller since "
                        "they connected. This line uses push to talk: remind "
                        "them, in one short line in your own voice, to press "
                        "the talk bar while they speak. Don't ask a question "
                        "they'd have to speak to answer."
                    )
                    if (cfg.get("show_push_to_talk")
                        or cfg.get("embed_push_to_talk"))
                    else
                    "Nothing at all has come through from the caller since "
                    "they connected — most likely their microphone is blocked "
                    "or picking up nothing. Say so plainly, in your own voice, "
                    "in one short line, and tell them to check it. Don't ask a "
                    "question they'd have to speak to answer."
                ) if dead_line else (
                    "The caller has gone quiet. Check they're still there — "
                    "one short line in your own voice, warm, no more than a "
                    "few words. Don't repeat yourself or start a new topic."
                )
                await _say_something(
                    session, instructions,
                    "I'm not hearing anything your end — worth checking your "
                    "microphone." if dead_line else "Still with me?",
                    what="idle check-in",
                )
            else:
                # Final strike: whatever happens, the line closes, and the
                # caller hears WHY. A goodbye that fails to generate must not
                # leave them holding a dead line, and one that never mentions
                # the silence reads as being hung up on for no reason.
                await _say_something(
                    session,
                    (
                        "You never heard anything from this caller at all. "
                        "Sign off warmly in one line — say you can't hear them, "
                        "that it's probably their microphone, and to try again. "
                        "Then stop."
                    ) if dead_line else (
                        "Still nothing from the caller. Say a brief goodbye in "
                        "character — you're letting them go and getting back to "
                        "the broadcast. One line, then stop."
                    ),
                    (
                        "I still can't hear you, so I'll let you go — check "
                        "your microphone and call back any time."
                    ) if dead_line else (
                        "I'll let you get back to it — call in any time."
                    ),
                    what="idle goodbye",
                )
                # Was a flat six seconds, which is a guess that clips a long
                # goodbye and leaves dead air after a short one. Same wait the
                # DJ's own hang-up uses now.
                await await_sign_off(session, "the idle goodbye")
                await end_call(ctx, "caller could not be heard" if dead_line
                               else "caller went quiet")
                return

    task = asyncio.create_task(_idle_watch())
    ctx.add_shutdown_callback(lambda: _cancel(task))


def attach_time_limit(ctx: JobContext, session: AgentSession, cfg: dict,
                      air=None, floor=None) -> None:
    """`max_call_seconds` was a declared setting that nothing enforced. Wind
    the call up in character rather than cutting the audio dead."""
    max_seconds = int(cfg.get("max_call_seconds") or 0)
    if max_seconds <= 0:
        return

    async def _end_when_over_time() -> None:
        try:
            await asyncio.sleep(max_seconds)
        except asyncio.CancelledError:
            # Normal hangup cancelled the timer. Returning here matters: a
            # `finally` that called ctx.shutdown() would fire on EVERY call,
            # re-entering shutdown and logging the wrong reason.
            return

        log.info("call hit the %ss limit — signing off", max_seconds)
        # The third turn that can start while another is generating — see
        # call/floor.py. If it cannot get the floor the call still ends;
        # what is skipped is the spoken sign-off, not the hang-up.
        if floor is not None:
            async with floor.take("the time-limit sign-off") as mine:
                if not mine:
                    await end_call(ctx, "call time limit reached")
                    return
        try:
            # The goodbye waits for the broadcast, like every other generated
            # turn. It did not, and the timer is indifferent to what the
            # station is doing — so on a call that ran its full length while
            # the on-air DJ was mid-link, the sign-off went out on top of it
            # and the last thing the audience heard was two of the same voice.
            # Bounded by MAX_HOLD inside wait_until_clear: the call still ends.
            if air is not None:
                waited = await air.wait_until_clear()
                if waited > 0.5:
                    log.info("held the time-limit sign-off %.1fs for the "
                             "on-air DJ", waited)
            await _say_something(
                session,
                "You're out of time. Thank the caller warmly, in one short "
                "line, and say goodbye. Do not ask a question.",
                "That's my time — thanks for calling in.",
                what="time-limit sign-off",
            )
            await await_sign_off(session, "the time-limit sign-off")
        except asyncio.CancelledError:
            return
        except Exception as e:
            # The session may already be closing; end the call regardless
            # rather than leaving an unhandled task exception behind.
            log.warning("sign-off before the time limit failed: %s", e)

        await end_call(ctx, "call time limit reached")

    task = asyncio.create_task(_end_when_over_time())
    ctx.add_shutdown_callback(lambda: _cancel(task))
