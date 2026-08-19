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
        from .record import with_args

        outputs, failures = {}, {}
        for out in (getattr(ev, "function_call_outputs", None) or []):
            call_id = getattr(out, "call_id", None)
            outputs[call_id] = getattr(out, "output", "")
            # The SDK marks a tool that raised. The record has carried a
            # `failed` flag since 0.10.104 and the chat line has always set it;
            # the phone never did, so a call that talked its way around three
            # refusals read back as a call where the DJ simply chatted — and
            # the panel counts a conversation as needing attention by its
            # problems, which a refusal is not.
            failures[call_id] = bool(getattr(out, "is_error", False))
        for call in (getattr(ev, "function_calls", None) or []):
            name = getattr(call, "name", "?")
            call_id = getattr(call, "call_id", None)
            result = str(outputs.get(call_id, ""))
            log.info("tool: %s -> %s", name, result[:120].replace("\n", " "))
            if record:
                record.tool(name, with_args(getattr(call, "arguments", None),
                                            result),
                            failed=failures.get(call_id, False))

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


def attach_turn_commit(ctx: JobContext, session: AgentSession,
                       pacing=None) -> None:
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

    `pacing` is the call's HeardMeter, and this is where its wait starts on
    a held bar. The hold claims the user turn, and the SDK pins `user_state`
    to "speaking" while a claim is active — so the `user_state_changed`
    transition the meter normally listens for never fires, and all four of
    the operator's real PTT calls on 2026-08-18 wrote `replyGap n=0` while
    the fake-mic tap-to-latch calls measured fine. The commit below is the
    exact moment the caller said "your turn", which is the honest start of
    the wait — stamped only when the commit actually lands, because a
    release with nothing said gets no reply to measure.
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
            if pacing is not None:
                try:
                    pacing.caller_stopped()
                except Exception:                             # noqa: BLE001
                    pass   # a measurement must never cost the turn
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


# Split to call/clocks.py at 0.10.146 (the length ceiling), and re-exported
# rather than repointed: `lifecycle.attach_idle_watch` is a name the session
# and a dozen tests already reach for, and moving a module is not a reason to
# make every caller move too. Same call the rows.py split made.
from .clocks import (  # noqa: F401,E402
    _say_something,
    attach_idle_watch,
    attach_time_limit,
    attach_working_line,
)


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
