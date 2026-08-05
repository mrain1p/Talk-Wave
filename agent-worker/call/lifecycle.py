"""What happens to a call while it is running, and just after it ends.

Each function here attaches one behaviour to a live session: keeping a caller
out of dead air when a provider fails, logging what was heard, checking on a
caller who has gone quiet, enforcing the time limit, opening the call, and
handing a line back to the broadcast once it is over.

Separate functions rather than methods, so each reads as the one concern it
is. These used to be interleaved closures inside a single 334-line function,
which meant touching any of them meant reading all of it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from livekit.agents import AgentSession, JobContext

from station import StationClient

from .background import spawn
from .hangup import end_call

log = logging.getLogger("callin.agent")

# The user turn the call opens with, so the DJ has something to answer.
#
# Named rather than inline because two places have to agree about it: the
# greeting sends it, and _transcript drops it. It is not something the caller
# said — it describes the situation to the model — and anything that treats it
# as a caller turn gets the transcript wrong. Bracketed, so the speech filter
# strips it if it ever reaches the voice.
CALL_OPENING_PRIME = (
    "[Call connected. The caller is on the line and has not spoken yet — "
    "you speak first.]"
)


async def cancel(task: asyncio.Task) -> None:
    task.cancel()


def attach_error_recovery(session: AgentSession, record=None) -> None:
    """When a provider gives up after all its retries (observed: Gemini
    flash-lite 503ing under load), the caller must never get dead air. The DJ
    can't THINK without the LLM, but it can still SPEAK — say() drives the TTS
    directly, no model involved."""
    last_sorry = {"t": 0.0}

    def _on_session_error(ev) -> None:
        err = getattr(ev, "error", None)
        log.warning("session error (source=%s): %s", getattr(ev, "source", "?"), err)
        if record:
            record.problem(f"{type(err).__name__ if err else 'error'}: {err}")
        if getattr(err, "recoverable", False):
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


def attach_idle_watch(
    ctx: JobContext, session: AgentSession, cfg: dict, air=None
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
    """
    idle_secs = int(cfg.get("idle_prompt_secs") or 0)
    if idle_secs <= 0:
        return
    max_nudges = int(cfg.get("idle_max_nudges") or 0)
    state = {"last_words": time.time(), "nudges": 0, "asked": False}

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
            # Thinking time, not dead air: give a caller who was just asked
            # something three times as long before checking on them.
            wait_for = idle_secs * 3 if state["asked"] else idle_secs
            if time.time() - state["last_words"] < wait_for:
                continue
            if state["nudges"] >= max_nudges:
                continue
            state["nudges"] += 1
            state["last_words"] = time.time()
            first = state["nudges"] == 1
            log.info("no words from the caller for %ss — check-in %d/%d",
                     idle_secs, state["nudges"], max_nudges)
            if first and max_nudges > 1:
                try:
                    await session.generate_reply(instructions=(
                        "The caller has gone quiet. Check they're still there — "
                        "one short line in your own voice, warm, no more than a "
                        "few words. Don't repeat yourself or start a new topic."
                    ))
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    log.warning("idle check-in failed: %s", e)
            else:
                # Final strike: whatever happens, the line closes. A goodbye
                # that fails to generate must not leave the caller holding a
                # dead line forever.
                try:
                    await session.generate_reply(instructions=(
                        "Still nothing from the caller. Say a brief goodbye in "
                        "character — you're letting them go and getting back to "
                        "the broadcast. One line, then stop."
                    ))
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    log.warning("idle goodbye failed: %s", e)
                    try:
                        await session.say(
                            "I'll let you get back to it — call in any time."
                        )
                    except Exception:
                        pass
                await asyncio.sleep(6)  # let the goodbye actually play
                await end_call(ctx, "caller went quiet")
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
            await session.generate_reply(
                instructions=(
                    "You're out of time. Thank the caller warmly, in one short "
                    "line, and say goodbye. Do not ask a question."
                )
            )
            await asyncio.sleep(6)  # let the sign-off actually play
        except asyncio.CancelledError:
            return
        except Exception as e:
            # The session may already be closing; end the call regardless
            # rather than leaving an unhandled task exception behind.
            log.warning("sign-off before the time limit failed: %s", e)

        await end_call(ctx, "call time limit reached")

    task = asyncio.create_task(_end_when_over_time())
    ctx.add_shutdown_callback(lambda: cancel(task))


async def greet(session: AgentSession, cfg: dict) -> None:
    """Pick up. Both styles stay in persona and carry the show; the toggle is
    only whether the DJ opens with an invitation or lets the caller lead."""
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
        await session.generate_reply(
            user_input=CALL_OPENING_PRIME,
            instructions=greeting,
        )
    except Exception as e:
        # A model outage at pickup used to mean the caller heard NOTHING until
        # they gave up. A canned line through the TTS keeps the call alive —
        # later turns may succeed once the provider recovers.
        log.warning("greeting failed (%s) — using a canned pickup", e)
        try:
            await session.say(
                "Hey — you're through to the booth. Bear with me a second, "
                "the line's a bit rough tonight. What can I do for you?"
            )
        except Exception:
            pass


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



def _transcript(session: AgentSession, limit: int = 24) -> list[tuple[str, str]]:
    """Flatten the call into (role, text) pairs, whatever shape the SDK's
    chat items happen to take.

    The opening prime is dropped. It sits in the history as a `user` message
    because that is the only shape Gemini accepts, but the caller never said
    it and never heard it — counting it as something they said made every
    later caller line in the written transcript inherit the NEXT line's
    timestamp, and inflated callerTurns by one (which gates the back-to-air
    mention). Everything downstream of here treats a `user` turn as the caller
    speaking, so this is the place to say it wasn't.
    """
    turns: list[tuple[str, str]] = []
    try:
        items = list(session.history.items)
    except Exception:
        return turns

    for item in items[-limit:]:
        role = getattr(item, "role", None)
        if role not in ("user", "assistant"):
            continue
        content = getattr(item, "content", None)
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(c for c in content if isinstance(c, str))
        else:
            text = getattr(item, "text_content", "") or ""
        text = text.strip()
        if text and text != CALL_OPENING_PRIME:
            turns.append((role, text))
    return turns


async def send_on_air_callback(
    session: AgentSession, station: StationClient, persona: dict, cfg: dict
) -> None:
    """After the call, give the on-air DJ a passing mention of it.

    The point is continuity — a listener hears the same DJ refer to the call
    that just happened. It's deliberately one line: a mention, not a recap, and
    never a transcript. Nothing the caller said is repeated verbatim unless the
    DJ chooses to.
    """
    if not cfg.get("callback_enabled"):
        return

    turns = _transcript(session)
    caller_turns = sum(1 for role, _ in turns if role == "user")
    if caller_turns < int(cfg.get("callback_min_turns", 2)):
        log.info("skipping on-air handoff — only %d caller turn(s)", caller_turns)
        return

    max_words = int(cfg.get("callback_max_words", 30))
    extra = str(cfg.get("callback_instructions") or "").strip()

    convo = "\n".join(
        f"{'Caller' if role == 'user' else 'You'}: {text}" for role, text in turns
    )

    ask = (
        f"You are {persona.get('name', 'the DJ')}. The call just ended. Write ONE "
        f"line to say on air about it, under {max_words} words, in your own voice.\n\n"
        "Mention it the way a DJ passes over something between tracks — light, "
        "in character, moving on. Do not greet the audience, do not read out a "
        "summary, do not quote the caller word for word, and do not use their "
        "personal details beyond a first name. If they asked you something about "
        "yourself worth sharing, you may answer it briefly on air. If nothing "
        "about the call is worth mentioning, reply with exactly: SKIP\n"
    )
    if extra:
        ask += f"\nAlso: {extra}\n"
    ask += f"\nThe call:\n{convo}\n"

    try:
        from livekit.agents import llm as lk_llm

        ctx = lk_llm.ChatContext.empty()
        ctx.add_message(role="user", content=ask)

        # This runs during shutdown, when the session's own LLM may already be
        # tearing down. Fall back to a fresh client rather than losing the
        # handoff — it only gets one attempt per call.
        try:
            model = session.llm
            assert model is not None
        except Exception:
            from .providers import build_llm

            model = build_llm(cfg)

        async def _compose() -> str:
            out = ""
            stream = model.chat(chat_ctx=ctx)
            async for chunk in stream:
                delta = getattr(chunk, "delta", None)
                if delta and getattr(delta, "content", None):
                    out += delta.content
            await stream.aclose()
            return out

        # Capped so a stalled provider can't eat the whole shutdown budget.
        text = await asyncio.wait_for(_compose(), timeout=25.0)
    except asyncio.TimeoutError:
        log.warning("on-air handoff compose timed out — skipping")
        return
    except Exception as e:
        log.warning("could not compose the on-air handoff: %s", e)
        return

    line = text.strip().strip('"')
    if not line or line.upper().startswith("SKIP"):
        log.info("on-air handoff skipped — nothing worth mentioning")
        return

    log.info("handing back to air: %s", line)
    # Fresh client: the session's StationClient may already be closed by an
    # earlier shutdown callback by the time this runs.
    fresh = StationClient()
    try:
        await fresh.dj_say(line, mode="styled", kind="callin")
    finally:
        await fresh.aclose()


