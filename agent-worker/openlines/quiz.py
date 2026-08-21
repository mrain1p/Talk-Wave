"""A quiz the DJ can actually mark.

A quiz cannot be a free-text premise, and the operator's own station proved it.
Given the subject "Quiz question" — a LABEL, not a subject — the invitation
aired as *"the suits want me to push something called a 'Quiz question'"*, and
when a caller took it up the DJ invented a question on the spot with no answer
in mind, then told them they had "whiffed" on it. (2026-08-21, room at 21:31.)

Every part of that is the same failure: nothing decided the question, so
nothing could judge the answer.

So a quiz is its own kind of premise. The question AND its answer are settled
BEFORE anything airs, both are pinned to the record, and the block hands the
DJ the answer — which is what makes "right or wrong" a real judgement rather
than an improvisation.

## Why it asks about ITSELF

The subject is deliberately the DJ's own world: the show, the persona, the
records it has just played, what it said on air twenty minutes ago. Not
general music trivia. Two reasons, and the second is the one that bites:

- the DJ genuinely KNOWS these answers, so it cannot be wrong about its own
  show the way it can be wrong about a chart position in 1974;
- and a listener who has had the show on all evening can actually win, which
  is the only thing that makes a quiz worth calling about.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger("talkwave.openlines")

QUESTION_BUDGET = 200
ANSWER_BUDGET = 160

ASK = (
    "[Not a caller. This is the station's producer, off air.\n"
    "Set ONE quiz question for the audience, and give me its answer.\n"
    "It must be about something YOU actually know and a listener could have "
    "heard: this show, yourself, the records you have played tonight, "
    "something you said on air earlier. NOT general music trivia, chart "
    "positions or release years — you would be guessing, and so would they.\n"
    "It has to be answerable by somebody who has had the show on this evening, "
    "and it must have ONE clear answer you can mark right or wrong. Not a "
    "matter of opinion.\n"
    "Reply as JSON and nothing else, exactly:\n"
    '{"question": "...", "answer": "..."}\n'
    "The question is one sentence, as you would ask it on air. The answer is "
    "short — the fact itself, plus anything else you would accept as correct.]"
)


def _clip(text: str, budget: int) -> str:
    return " ".join(str(text or "").split())[:budget].strip()


def _parse(raw: str) -> dict:
    """Pull {question, answer} out of whatever came back.

    Models fence JSON, prefix it with "Sure —", or answer in prose. A quiz
    that cannot be parsed is simply not offered; it never half-airs.
    """
    text = str(raw or "").strip()
    if "```" in text:
        parts = text.split("```")
        text = max(parts, key=len)
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    question = _clip(data.get("question"), QUESTION_BUDGET)
    answer = _clip(data.get("answer"), ANSWER_BUDGET)
    if not question or not answer:
        return {}
    return {"question": question, "answer": answer}


async def invent(cfg: dict, station, persona: dict) -> dict:
    """A question and its answer, in the DJ's own head. `{}` if it could not.

    Same brain and the same full prompt a reply uses, for the same reason
    `premise.invent` does: a question written against half the persona is a
    question about somebody else's show.
    """
    from livekit.agents import llm as lk_llm

    from brain.assemble import build_system_prompt
    from call.providers import build_llm

    prompt = await build_system_prompt(station, persona, cfg=cfg, mode="chat")
    ctx = lk_llm.ChatContext.empty()
    ctx.add_message(role="system", content=prompt)
    ctx.add_message(role="user", content=ASK)

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
        # Nothing has aired, so the director simply tries again next pass.
        log.info("could not set a quiz: %s", e)
        return {}
    finally:
        try:
            await model.aclose()
        except Exception:                                      # noqa: BLE001
            pass

    quiz = _parse(out)
    if not quiz:
        log.info("quiz came back unparseable: %s", out[:160])
    return quiz


def conduct(question: str, answer: str) -> str:
    """What the DJ is told about running the quiz, once somebody is through.

    The answer is handed over EXPLICITLY. Without it the DJ marks against a
    question it half-remembers, which is how a caller who answered correctly
    gets told they whiffed.
    """
    return (
        "\nYou are running a quiz, and this is the question you put out:\n"
        f"  {question}\n"
        "The answer, which only you know:\n"
        f"  {answer}\n"
        "How to run it:\n"
        "- Ask them the question as you asked it on air, then let them answer.\n"
        "- Mark it yourself against the answer above and say plainly whether "
        "they got it. Close is close — accept an answer that means the same "
        "thing in different words, and do not fail somebody on spelling or on "
        "leaving off a middle name.\n"
        "- If they are wrong, tell them so warmly and give them the answer "
        "rather than leaving them hanging.\n"
        "- An answer is NOT a request. Somebody saying a song title is telling "
        "you their answer, not asking you to play it — do not queue anything "
        "unless they actually ask for it.\n"
        "- Do not invent a different question, and do not change the answer to "
        "fit what they said."
    )
