"""After the call: reading the conversation back, and the back-to-air mention.

Split out of lifecycle.py, which was over the length ceiling and had exactly
this seam recorded against it: everything here runs AFTER the call, only reads
the session, and nothing during-call reads it back.

`transcript` is the one honest reading of the session's history — the
call record and the on-air mention both go through it, so they cannot
disagree about what a caller turn is.
"""

from __future__ import annotations

import asyncio
import logging

from livekit.agents import AgentSession

from station import StationClient

log = logging.getLogger("callin.agent")


def is_prime(role: str, text: str) -> bool:
    """A user turn the caller never said: a bracketed situation note.

    The greeting's opening prime started this (see greeting.CALL_OPENING_PRIME
    — Gemini demands a user turn in front of a leading function call), and the
    late request-match note joined it. They sit in the history as `user`
    messages because that is the only shape providers accept, but the caller
    neither said nor heard them — counting one as a caller turn made every
    later caller line in the written transcript inherit the NEXT line's
    timestamp, and inflated callerTurns by one (which gates the back-to-air
    mention). One rule for all of them: a user turn that is nothing but
    bracketed text is ours, not theirs. An STT never produces brackets.
    """
    text = text.strip()
    return role == "user" and text.startswith("[") and text.endswith("]")


def transcript(session: AgentSession, limit: int = 24) -> list[tuple[str, str]]:
    """Flatten the call into (role, text) pairs, whatever shape the SDK's
    chat items happen to take. Primes are dropped — see is_prime."""
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
        if text and not is_prime(role, text):
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

    turns = transcript(session)
    caller_turns = sum(1 for role, _ in turns if role == "user")
    need = int(cfg.get("callback_min_turns", 2))
    if caller_turns < need:
        # The threshold in the line, or the log reads as a bug on any box
        # where the setting isn't the default — it did, on a 6-turn box.
        log.info("skipping on-air handoff — only %d caller turn(s), needs %d",
                 caller_turns, need)
        return

    max_words = int(cfg.get("callback_max_words", 30))
    extra = str(cfg.get("callback_instructions") or "").strip()

    convo = "\n".join(
        f"{'Caller' if role == 'user' else 'You'}: {text}" for role, text in turns
    )

    # WHO the DJ is, not just what it is called.
    #
    # This prompt used to carry the persona's NAME and nothing else — no card,
    # no conduct, no house style — while the call it is summarising was run by
    # the whole brain. So the one line every LISTENER hears about a call was
    # written by a DJ with no character, and it is the only place the station's
    # audience meets this feature at all. The card is already in hand here; it
    # costs one read of a dict and buys the line the same voice the caller just
    # heard. (The full brain is deliberately not assembled: this runs during
    # shutdown, and a station snapshot there would put a network round-trip in
    # front of the worker letting go.)
    from brain.briefing import CARD_BUDGET, clip

    card = clip(persona.get("soul", ""), CARD_BUDGET)
    ask = (
        f"You are {persona.get('name', 'the DJ')}. The call just ended. Write ONE "
        f"line to say on air about it, under {max_words} words, in your own voice.\n\n"
        + (f"Who you are:\n{card}\n\n" if card else "")
        + "Mention it the way a DJ passes over something between tracks — light, "
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

        from .providers import stream_reply

        async def _compose() -> str:
            # Borrows the live session's llm — close only the stream, never
            # the model. stream_reply does exactly that.
            out, _ = await stream_reply(model, ctx)
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
