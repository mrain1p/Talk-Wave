"""What happens to a call while it is running.

Each function here attaches one behaviour to a live session: keeping a caller
out of dead air when a provider fails, logging what was heard, checking on a
caller who has gone quiet, enforcing the time limit, and opening the call.
What happens AFTER the call — reading the transcript back, the back-to-air
mention — lives in handoff.py.

Separate functions rather than methods, so each reads as the one concern it
is. These used to be interleaved closures inside a single 334-line function,
which meant touching any of them meant reading all of it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from livekit.agents import AgentSession, APITimeoutError, JobContext

import speech_filter

from .background import spawn
from .hangup import await_sign_off, end_call

log = logging.getLogger("callin.agent")


# Which leg of the call an error came from, in the words the panel shows. The
# raw event renders as its pydantic repr — `type='llm_error' label='...'` — and
# that string was the operator's first sight of a failed call.
_LEGS = {"llm_error": "The model", "stt_error": "Speech-to-text",
         "tts_error": "The voice", "realtime_model_error": "The model"}


def _model_gave_up(err) -> bool:
    """True when the model ran out of time producing its first token.

    Worth telling apart from every other provider failure: the others are
    something going wrong, and this one is the box being asked for more than it
    can do. It is also the only one where the fix is a different model.
    """
    if getattr(err, "type", "") != "llm_error":
        return False
    return isinstance(getattr(err, "error", None), APITimeoutError)


def _in_plain_words(err, think=None) -> str:
    """One line for the call record. Read by an operator, not by a debugger."""
    if _model_gave_up(err):
        budget = float(getattr(think, "budget", 0) or 0)
        return (
            f"The model did not start answering within {budget:.0f}s, so the turn was "
            "thrown away and retried. The caller heard silence for that whole time, "
            "and then the apology line."
        )
    inner = getattr(err, "error", None) or err
    leg = _LEGS.get(getattr(err, "type", ""), "The call")
    return f"{leg} failed: {type(inner).__name__}: {inner}"[:400]


async def cancel(task: asyncio.Task) -> None:
    # Await the cancellation, don't just request it. A task cancelled mid-await
    # (generate_reply, await_sign_off) is otherwise destroyed while pending —
    # "Task was destroyed but it is pending" noise, and its except/finally
    # cleanup may not run before the loop tears down. Swallowing CancelledError
    # here makes shutdown deterministic (0.10.58 review).
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


def attach_error_recovery(session: AgentSession, record=None, think=None) -> None:
    """When a provider gives up after all its retries (observed: Gemini
    flash-lite 503ing under load), the caller must never get dead air. The DJ
    can't THINK without the LLM, but it can still SPEAK — say() drives the TTS
    directly, no model involved."""
    last_sorry = {"t": 0.0}

    recoverable_at: list[float] = []

    def _on_session_error(ev) -> None:
        err = getattr(ev, "error", None)
        log.warning("session error (source=%s): %s", getattr(ev, "source", "?"), err)
        slow = _model_gave_up(err)
        if slow and think is not None:
            think.gave_up()
        if record:
            record.problem(_in_plain_words(err, think))
        if getattr(err, "recoverable", False) and not slow:
            # One recoverable error is the SDK's to absorb. A SECOND inside
            # the window means "recoverable" is not recovering — three
            # recoverable Gemini 504s in a row once left a caller in 43s of
            # dead air with no apology, because this returned every time.
            #
            # A model that ran out of time is exempt from that grace: the
            # caller has ALREADY been silent for the whole budget (30s on a
            # self-hosted box) by the time this fires, so absorbing the first
            # one quietly is how you get a minute of nothing.
            now = time.time()
            recoverable_at.append(now)
            del recoverable_at[: max(0, len(recoverable_at) - 5)]
            if len([t for t in recoverable_at if now - t < 45]) < 2:
                return
        if time.time() - last_sorry["t"] < 20:
            return
        last_sorry["t"] = time.time()

        async def _apologise() -> None:
            try:
                await session.say(
                    "The line's giving me trouble on my end — hang tight a "
                    "second, or try me again in a minute.",
                    # Kept out of the history: this is a canned apology for a
                    # provider failure, not something the DJ decided to say,
                    # and a stray model turn is what makes Gemini reject the
                    # next request that contains a tool call.
                    add_to_chat_ctx=False,
                )
            except Exception:
                pass  # if the voice is what failed, silence is unavoidable

        spawn(_apologise())

    session.on("error", _on_session_error)


def attach_think_pace(session: AgentSession, think) -> None:
    """Measure what the caller waits for the model, on every turn.

    The SDK stamps each assistant message with its own `llm_node_ttft`, which
    is the same number the settings panel benches — so for the first time the
    record and the panel are measuring the same thing and can be held against
    each other. (`metrics_collected` carries it too and is deprecated in this
    SDK; subscribing to it logs a warning on every call.)
    """

    def _on_item(ev) -> None:
        item = getattr(ev, "item", None)
        if getattr(item, "role", None) != "assistant":
            return
        try:
            metrics = getattr(item, "metrics", None) or {}
            think.note(float(metrics.get("llm_node_ttft") or 0))
        except Exception:
            pass  # a measurement must never cost the turn it is measuring

    session.on("conversation_item_added", _on_item)


def attach_first_word(session: AgentSession, record=None) -> None:
    """Stamp the record the first time the DJ's audio STARTS.

    The chart's "time to first word" used to be derived from the first dj
    turn, which commits only after the utterance finishes — so it silently
    measured ring + pickup + the whole greeting (12.5s median on calls whose
    first audio landed in ~4). The speaking transition is when a caller
    actually stops hearing silence."""

    def _on_state(ev) -> None:
        if record is not None and getattr(ev, "new_state", None) == "speaking":
            record.first_word()

    session.on("agent_state_changed", _on_state)


def attach_heard_logging(session: AgentSession, counter: dict, record=None) -> None:
    """Both halves of the conversation, in the log and in the call record.

    `heard:` alone was not enough. It showed the CALLER's side, so a report
    like "he wouldn't hang up" had to be matched against tracebacks to work out
    what the DJ had actually said or tried. `said:` and `tool:` complete it —
    the log now reads as the call.
    """

    def _log_heard(ev) -> None:
        text = str(getattr(ev, "transcript", "") or "").strip()
        if text and getattr(ev, "is_final", True):
            counter["n"] += 1
            log.info("heard: %s", text[:160])
            if record:
                record.turn("caller", text)

    session.on("user_input_transcribed", _log_heard)

    def _log_said(ev) -> None:
        item = getattr(ev, "item", None)
        if getattr(item, "role", None) != "assistant":
            return
        text = str(getattr(item, "text_content", "") or "").strip()
        if text:
            log.info("said: %s", text[:160])
            if record:
                record.turn("dj", text)
                # Kept off the air by the speech filter, but silently — and to
                # the caller a typed tool call looks exactly like the DJ
                # agreeing and then doing nothing. See strip_tool_code.
                if speech_filter.looks_like_tool_code(text):
                    record.problem(
                        "The model typed a tool call instead of making one, so "
                        "nothing ran and the caller's request was dropped (it "
                        "was not spoken). A model-side failure — check the LLM "
                        "setting against one with proven tool routing."
                    )

    session.on("conversation_item_added", _log_said)

    def _log_tools(ev) -> None:
        outputs = {}
        for out in (getattr(ev, "function_call_outputs", None) or []):
            outputs[getattr(out, "call_id", None)] = getattr(out, "output", "")
        for call in (getattr(ev, "function_calls", None) or []):
            name = getattr(call, "name", "?")
            result = str(outputs.get(getattr(call, "call_id", None), ""))
            log.info("tool: %s -> %s", name, result[:120].replace("\n", " "))
            if record:
                record.tool(name, result)

    session.on("function_tools_executed", _log_tools)


def attach_card_flush(session: AgentSession, actions) -> None:
    """The phone's action_cards "after" mode: a tool's receipt card is held
    until the DJ line mentioning it commits — conversation_item_added fires
    only once the utterance finishes, so the words reach the caller's screen
    before the paperwork. Held cards at hang-up drop; the record has them."""

    def _flush(ev) -> None:
        item = getattr(ev, "item", None)
        if getattr(item, "role", None) == "assistant":
            actions.flush_cards()

    session.on("conversation_item_added", _flush)


# What the SDK's close reasons mean in the record. Written out rather than
# passed through raw, because "PARTICIPANT_DISCONNECTED" is the answer to a
# question nobody asked — the operator wants to know whether the caller rang
# off or the line dropped.
_CLOSE_REASONS = {
    "PARTICIPANT_DISCONNECTED": "the caller hung up",
    "USER_INITIATED": "the DJ ended the call",
    "TASK_COMPLETED": "the DJ ended the call",
    "JOB_SHUTDOWN": "the worker shut down mid-call",
    "ERROR": "the session failed",
}


def attach_turn_commit(ctx: JobContext, session: AgentSession) -> None:
    """Push-to-talk's other half: the widget says the turn is OVER.

    Releasing the talk bar mutes the mic, and until now that was all it did
    — the DJ then waited out its endpointing delay (0.3–2.5s here) against
    a line that was already silent, which a beta tester's side-by-side read
    correctly called out: mute and unmute, no commit. The bar release is
    the one moment a caller explicitly says "your turn", so the widget now
    announces it (`talkwave.turn-end`) and this hands it to the session.

    Guarded on the user actually being mid-turn: a release with nothing
    said must not commit an empty turn and make the DJ answer silence —
    normal endpointing already handled anything that finished earlier.
    """

    def _on_data(packet) -> None:
        if getattr(packet, "topic", "") != "talkwave.turn-end":
            return
        try:
            if str(getattr(session, "user_state", "")) != "speaking":
                return
            # skip_reply stays False: committing IS the reply cue. The
            # future is fire-and-forget — the transcript still arrives
            # through the ordinary user_input_transcribed path.
            session.commit_user_turn()
        except RuntimeError:
            pass          # session already draining; nothing to commit
        except Exception as e:                                # noqa: BLE001
            # An old SDK without the API degrades to the old behaviour —
            # endpointing — rather than taking the call down.
            log.info("turn commit unavailable: %s", e)

    ctx.room.on("data_received", _on_data)


def attach_close_reason(session: AgentSession, ended: dict) -> None:
    """Record why the session closed.

    `ctx.shutdown_reason` is empty for the commonest ending of all — the caller
    closing the tab — so every ordinary call wrote an empty `endedBecause` and
    the record could not tell a hang-up from a dropped line. The SDK does know:
    its close event carries a CloseReason, and PARTICIPANT_DISCONNECTED is
    exactly the distinction that was missing.
    """
    def _closed(ev) -> None:
        raw = str(getattr(ev, "reason", "") or "")
        # str() on the enum gives "CloseReason.USER_INITIATED", not the bare
        # value — the assumption that it was a plain str subclass was wrong,
        # and the first real call after 0.9.76 wrote that whole repr into the
        # record instead of "the caller hung up". Take the last segment either
        # way, so it works whichever the SDK hands over.
        name = raw.rsplit(".", 1)[-1].strip().upper()
        ended["reason"] = _CLOSE_REASONS.get(name, raw)

    try:
        session.on("close", _closed)
    except Exception as e:                                    # noqa: BLE001
        # An SDK that renames the event must not take the call with it.
        log.debug("could not watch for the close reason: %s", e)


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
    ctx.add_shutdown_callback(lambda: cancel(task))


def attach_time_limit(ctx: JobContext, session: AgentSession, cfg: dict) -> None:
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
        try:
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
    ctx.add_shutdown_callback(lambda: cancel(task))


async def release_call_slot(room: str) -> None:
    """Tell the token server this call is over so its concurrency slot frees
    immediately. The widget sends the same beacon, but a crashed tab never
    does — and the worker's shutdown always runs, so with the default limit of
    two concurrent calls, this is what stops dead sessions blocking real
    callers for the 30-minute age-out."""
    import httpx

    base = os.environ.get(
        "CALLIN_INTERNAL_URL",
        f"http://localhost:{os.environ.get('TOKEN_SERVER_PORT', '8100')}",
    )
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            await c.post(f"{base}/call-ended", json={"room": room})
    except Exception as e:
        log.debug("slot release beacon failed (harmless, will age out): %s", e)
