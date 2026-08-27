"""One model call that labels a DJ line's speech act — the classifier pilot.

Move 2 of the conversation-engine convergence (MASTER-PLAN "NORTH STAR";
the operator opened beta for exactly this). The promise lexicons are the
pilot target because they carry the richest failure history: four incidents
in two weeks, each a phrasing the patterns could not reach — a gerund in a
question, a present-progressive with a search in front of it — and every
one of them fixed by growing a regex that the next phrasing will dodge.
The deeper hole is structural: every lexicon in this codebase is English,
and the DJ is deliberately not. A caller's "eso es todo, adiós" is
invisible to patterns; a model reading the line is not.

This module deliberately answers ONE question — what kind of move was that
line? — and nothing else. The decision about what to DO with the label
(nudge, stay silent, record) stays in promises.unbacked_semantic, which is
the same tree the lexicons drive, so the two arms of the pilot differ only
in who reads the sentence. The structural facts (a tool ran, the ledger
moved, a result was refused, an ask is open) are never the model's to
judge — they are known, and they stay in code.

Degrade path, always: any failure here — timeout, garbage, no model —
returns "" and the caller falls back to the lexicons. The pilot must be
strictly additive; a flaky classifier that could silence the guard would
be worse than no classifier at all. CLASSIFY=off is the control arm, in
the product and the harness alike.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("callin.agent")

# The labels, and the whole vocabulary of the answer. Anything else the
# model says is treated as a failure and degrades to the lexicons.
LABELS = ("deliverable", "look", "done", "question", "none")

# How long the label may take. The classify runs while the line it judges
# is still being spoken to the caller, so this is not on anybody's critical
# path — but a hung call must degrade, not dangle.
TIMEOUT_SECS = 2.5

PROMPT = """\
You label ONE line a radio DJ just said to a caller, in any language.
Answer with exactly one word from this list:

deliverable - the line promises or announces that the DJ is putting music
  in the queue or on air ("I'm queueing them both now", "I'll get it into
  the rotation", "lo pongo en la cola ahora")
look - the line promises to go look, check, search or dig ("let me have a
  dig through the racks", "hold on, I'll check")
done - the line states an action has already happened ("that's queued",
  "it's lined up for you", "ya está en la cola")
question - the line's main move is asking the caller something or seeking
  their consent ("shall I queue that one up for you?")
none - anything else: chat, an answer, a description, a goodbye

The line:
<<<{text}>>>

One word only."""


def enabled() -> bool:
    """The pilot lever. CLASSIFY=off runs the lexicons alone — the control
    arm in the drill, and the kill switch in production."""
    return os.environ.get("CLASSIFY", "on").strip().lower() != "off"


def parse_label(raw) -> str:
    """The model's answer as a label, or "" for anything that is not one.

    Strict on purpose: a labeler that answers in a sentence has not answered,
    and "" is the degrade signal the callers already handle.
    """
    word = str(raw or "").strip().strip(".\"'`").lower()
    return word if word in LABELS else ""


async def speech_act(text: str, llm_call) -> str:
    """Label one DJ line via `llm_call`, or "" when anything goes wrong.

    `llm_call` is an async callable(prompt) -> str. It is a parameter rather
    than a client built here so the suite can fake it (no network in tests,
    ever), the harness can pass its own model, and the product can pass the
    session's — one labeler, three mouths, no drift.
    """
    import asyncio

    line = str(text or "").strip()
    if not line or llm_call is None:
        return ""
    try:
        raw = await asyncio.wait_for(
            llm_call(PROMPT.format(text=line[:400])), TIMEOUT_SECS)
    except Exception as e:                                     # noqa: BLE001
        log.debug("speech-act classify failed (degrading to lexicons): %s", e)
        return ""
    return parse_label(raw)


def llm_call_from(llm):
    """An `llm_call` built from a livekit llm plugin instance.

    The session's own model, reached the way the latency probes reach it:
    a fresh single-turn context, streamed, first-to-last delta collected.
    Kept here so promise_guard stays wiring and the harness can reuse it.
    """
    if llm is None:
        return None

    async def _call(prompt: str) -> str:
        from livekit.agents import llm as lk_llm

        ctx = lk_llm.ChatContext.empty()
        ctx.add_message(role="user", content=prompt)
        out = []
        stream = llm.chat(chat_ctx=ctx)
        async with stream:
            async for chunk in stream:
                d = getattr(chunk, "delta", None)
                if d is not None and getattr(d, "content", None):
                    out.append(d.content)
        return "".join(out)

    return _call
