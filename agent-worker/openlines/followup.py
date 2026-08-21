"""Telling the audience what came back.

Without this the loop is open at one end: the DJ puts a subject up on the
broadcast, somebody answers it in a private conversation, and nobody listening
ever hears that it happened. No listener has any reason to think the question
was real, and nobody else joins in.

A follow-up fires when a conversation has ENDED, never while one is running.
A record on disk is a finished conversation, which makes the trigger free —
and a DJ narrating a live chat would be reporting on somebody who is still
typing.

## What is aired, and what is not

The model is asked for the POSITION, not the person and not the words. A
contribution to an open topic is offered to the show, but it is offered to a
DJ, not to a microphone, and the difference matters:

- never a name, a handle, or anything that identifies who said it
- never a quotation — the DJ says what was argued, in its own voice
- nothing personal that happened to come up on the way past
- and if the conversation was not actually about the subject, nothing airs

That last one is the same judgement `premise.invent` makes: the model answers
with the line or with NOTHING, and NOTHING is a perfectly good answer. Most
conversations while a line is open will be requests, and a DJ reporting "and
someone asked for a Beatles record" as though it were a contribution is worse
than silence.
"""

from __future__ import annotations

import logging

log = logging.getLogger("talkwave.openlines")

# Per line, not per hour. Three is enough for a topic to feel alive on air and
# few enough that a busy evening cannot turn the broadcast into a read-out of
# its own text line. Deliberately not a setting: the toggle is the decision,
# and a cap an operator can raise is a cap that ends up raised.
MAX_PER_LINE = 3

# Long enough to have said something, short enough to skip a hello and a
# goodbye. Two turns each way.
MIN_TURNS = 4

FOLLOW_UP = (
    "[Not a caller. This is the station's producer, off air.\n"
    "You put this subject to your audience: {premise}\n\n"
    "Somebody just came in on it. Here is what passed between you:\n"
    "{transcript}\n\n"
    "If they genuinely engaged with the subject, give me ONE sentence for air "
    "saying what came back — the POSITION they took, not who they are and not "
    "their words. No name, no handle, no quoting, nothing personal that came "
    "up on the way past. Say it as something to tell the room, so the audience "
    "knows the question was real and somebody answered it.\n"
    "If they did NOT engage with it — a request, a question, a hello, anything "
    "else — reply with exactly: NOTHING\n"
    "Reply with the sentence alone, or NOTHING. No preamble, no quotation "
    "marks.]"
)


def _transcript(record: dict, budget: int = 2000) -> str:
    """The conversation as two speakers, oldest first, trimmed to a budget."""
    lines = []
    for turn in record.get("turns") or []:
        who = "Them" if str(turn.get("who")) == "caller" else "You"
        text = " ".join(str(turn.get("text") or "").split())
        if text:
            lines.append(f"{who}: {text}")
    joined = "\n".join(lines)
    return joined[-budget:] if len(joined) > budget else joined


def candidates(record: dict, since: str, already: list) -> list[dict]:
    """Finished conversations that started after the line went up and have not
    been reported yet."""
    from openlines.director import _when

    try:
        from call import record as record_mod
    except ImportError:
        return []

    start = _when(since)
    if not start:
        return []
    seen = {str(i) for i in (already or [])}
    out = []
    for item in record_mod.recent(40):
        rid = str(item.get("id") or "")
        if not rid or rid in seen:
            continue
        began = _when(item.get("startedAt"))
        if not began or began < start:
            continue
        if len(item.get("turns") or []) < MIN_TURNS:
            continue
        out.append(item)
    # Oldest first: if two arrived between ticks, the room hears them in the
    # order they happened.
    out.reverse()
    return out


async def line_for(cfg: dict, station, persona: dict, premise: str,
                   record: dict) -> str:
    """One airable sentence about what came back, or "" for nothing to say."""
    from livekit.agents import llm as lk_llm

    from brain.assemble import build_system_prompt
    from call.providers import build_llm
    from openlines.premise import _clean

    transcript = _transcript(record)
    if not transcript:
        return ""

    prompt = await build_system_prompt(station, persona, cfg=cfg, mode="chat")
    ctx = lk_llm.ChatContext.empty()
    ctx.add_message(role="system", content=prompt)
    ctx.add_message(role="user", content=FOLLOW_UP.format(
        premise=premise, transcript=transcript))

    model = build_llm(cfg)
    out = ""
    try:
        stream = model.chat(chat_ctx=ctx)
        async for chunk in stream:
            delta = getattr(chunk, "delta", None)
            if delta and delta.content:
                out += delta.content
        await stream.aclose()
    except Exception as e:                                     # noqa: BLE001
        # Nothing has aired, so this is not worth waking anyone for. The
        # conversation is marked seen by the caller either way — retrying a
        # model that is down, once a minute, forever, is how a quiet failure
        # becomes an expensive one.
        log.info("could not write a follow-up: %s", e)
        return ""
    finally:
        try:
            await model.aclose()
        except Exception:                                      # noqa: BLE001
            pass

    line = _clean(out)
    # The model's own way of saying there is nothing here. Checked loosely
    # because a model that is told to reply NOTHING sometimes replies
    # "NOTHING." or "nothing to report".
    if not line or line.upper().startswith("NOTHING"):
        return ""
    return line
