"""The two times the booth types without being typed to.

Both are the same move — one short in-persona line, no tools, sent as if the
DJ had just written it — and both exist because a text line that answers with
silence reads as broken: at the open, before the caller has said anything, and
again when the caller goes quiet mid-conversation.

Split out of session.py at 0.10.119 (the file crossed the ceiling): the reply
path is a tool loop with rounds, caps and a ledger, while this is a single
un-tooled pass. Different jobs that only shared a class.
"""

from __future__ import annotations

import time

from station import StationClient

# The booth's opening line when nothing is configured — filled with the DJ's
# name so a fresh chat is greeted by whoever is actually on air.
DEFAULT_CHAT_GREETING = "You're through to the booth — {dj} here. What's on your mind?"


def fill_greeting(text: str, persona: dict) -> str:
    """{station} {dj} {show} in the canned greeting, same tokens the card and
    the voicemail greeting fill."""
    return (text.replace("{station}", persona.get("station") or "the station")
                .replace("{dj}", persona.get("name") or "the DJ")
                .replace("{show}", persona.get("show") or "")).strip()


async def _one_line(cfg: dict, station, persona, instruction: str,
                    history=(), on_delta=None) -> str:
    """One un-tooled pass through the same brain a reply uses.

    The prompt is the full chat conduct, not a trimmed one: a greeting written
    against half the persona is a greeting in somebody else's voice.
    """
    from livekit.agents import llm as lk_llm

    from brain.assemble import build_system_prompt
    from call.providers import build_llm

    prompt = await build_system_prompt(station, persona, cfg=cfg, mode="chat")
    ctx = lk_llm.ChatContext.empty()
    ctx.add_message(role="system", content=prompt)
    for who, said in history:
        ctx.add_message(role="user" if who == "caller" else "assistant",
                        content=said)
    ctx.add_message(role="user", content=instruction)

    model = build_llm(cfg)
    out = ""
    try:
        stream = model.chat(chat_ctx=ctx)
        async for chunk in stream:
            delta = getattr(chunk, "delta", None)
            if delta and delta.content:
                out += delta.content
                if on_delta:
                    on_delta(delta.content)
        await stream.aclose()
    finally:
        try:
            await model.aclose()
        except Exception:                                      # noqa: BLE001
            pass
    return out.strip()


async def fresh_greeting(chat, cfg, station, persona) -> str:
    """One short in-persona opener, written at open. The voicemail fresh
    greeting, at chat size."""
    return await _one_line(cfg, station, persona, (
        "[The caller just opened the text line and has not typed yet. "
        "Greet them first — one short, warm line in your own voice, no "
        "question needed, just open the door.]"))


async def greet(chat, cfg: dict, on_event) -> None:
    """The booth speaks first when a fresh chat opens — a text line that
    answers with silence until the caller types reads as broken. "canned"
    formats the operator's line (no model cost); "fresh" writes one in
    persona. Either way it lands as the DJ's opening turn, streamed like any
    reply so the widget renders it identically.
    """
    mode = str(cfg.get("chat_greeting_mode") or "off")
    if mode == "off":
        return

    import secrets_store
    secrets_store.apply_to_env()

    station = StationClient()
    try:
        persona = await station.resolve_live_persona()
        chat.persona_name = persona.get("name") or chat.persona_name
        chat.persona_id = persona.get("id") or chat.persona_id
        if mode == "fresh":
            text = await fresh_greeting(chat, cfg, station, persona)
        else:
            text = fill_greeting(
                str(cfg.get("chat_greeting") or "").strip()
                or DEFAULT_CHAT_GREETING, persona)
        text = (text or "").strip()
        if not text:
            return
        on_event({"type": "delta", "text": text})
        chat.remember("dj", text)
        chat.last_active = time.time()
        on_event({"type": "done", "text": text, "dj": chat.persona_name})
    finally:
        await station.aclose()


async def nudge(chat, cfg: dict, on_event) -> None:
    """The caller has gone quiet with the ball in their court. ONE short,
    in-persona line to keep the line breathing — a text chat that sits silent
    after its own last message feels dead and turn-based (operator's ask).
    Explicitly NOT "are you still there?": this is a conversation, not a roll
    call. No tools; fired once per silence by the WS idle timer."""
    import secrets_store
    secrets_store.apply_to_env()

    station = StationClient()
    try:
        persona = await station.resolve_live_persona()
        chat.persona_name = persona.get("name") or chat.persona_name
        chat.persona_id = persona.get("id") or chat.persona_id

        # The nudge needs the conversation it is nudging — an opener does not.
        out = await _one_line(cfg, station, persona, (
            "[The caller has gone quiet since your last message — they "
            "haven't typed for a little while. Type ONE short, warm line in "
            "your own voice to keep the conversation breathing: pick the "
            "thread back up, or lightly offer a next thing. NOT 'are you "
            "still there?' — this is a chat, not a roll call — and no "
            "question they must answer to stay. A light nudge, then leave "
            "it with them.]"),
            history=chat.turns[-12:],
            on_delta=lambda t: on_event({"type": "delta", "text": t}))
        if out:
            chat.remember("dj", out)
            chat.last_active = time.time()
        on_event({"type": "done", "text": out, "dj": chat.persona_name})
    finally:
        await station.aclose()
