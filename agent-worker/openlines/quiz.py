"""A quiz the DJ can actually mark, about facts that are actually true.

Two failures on the operator's station built this, and they are different.

**Nothing decided the question.** Given the subject "Quiz question" — a LABEL,
not a subject — the invitation aired as *"the suits want me to push something
called a 'Quiz question'"*, and when a caller took it up the DJ invented a
question on the spot with no answer in mind, read their answer as a song
request, queued a track at them and told them they had whiffed. So: the
question AND its answer are settled before anything airs, both are pinned to
the record, and the block hands the DJ the answer.

**And then the answer was false.** Told to ask about its own show, the DJ asked
what drink somebody at the bar had ordered earlier and answered "a plain
bagel". It had said neither. A DJ has no reliable memory of its own broadcast —
invited to remember one, it invents one — and a quiz whose answer is wrong is
worse than no quiz at all.

So the question is grounded in a list of facts we build ourselves from the
snapshot, and the answer is CHECKED back against that list before anything
airs. A quiz we cannot verify is not offered.
"""

from __future__ import annotations

import json
import logging
import re

log = logging.getLogger("talkwave.openlines")

QUESTION_BUDGET = 200
ANSWER_BUDGET = 160

ASK = (
    "[Not a caller. This is the station's producer, off air.\n"
    "Set ONE quiz question for the audience, and give me its answer.\n"
    "The answer MUST come from the facts below. They are the only things that "
    "are actually true and checkable:\n"
    "{facts}\n"
    "Do not ask about anything else — not chart positions, not release years, "
    "and nothing you would be REMEMBERING rather than reading above. You do "
    "not reliably remember your own broadcast: what you actually said is in "
    "that list, and if it is not in the list then you did not say it. A quiz "
    "whose answer is wrong is worse than no quiz at all.\n"
    "Good shapes: who recorded a track you played, which track came before "
    "another, what is playing right now, something you said on air earlier, "
    "what this show is about.\n"
    "It must have ONE clear answer you can mark right or wrong, and somebody "
    "who has had the show on this evening must be able to get it.\n"
    "Reply as JSON and nothing else, exactly:\n"
    '{{"question": "...", "answer": "..."}}\n'
    "The question is one sentence, as you would ask it on air. The answer is "
    "short — the fact itself and nothing more.]"
)


def facts_from(snapshot: dict, show: dict, persona: dict) -> list[str]:
    """The things we can prove, and the only things a question may be about.

    "What did I say earlier?" IS a fair question — we just have to supply the
    answer rather than trusting the DJ to remember it. The station records its
    own on-air speech in the session feed (the same source
    `brain.briefing._fmt_booth` reads), and the Show Card carries what the
    programme is about. Both are checkable, so both are in here; the DJ's
    unaided memory of the evening is not.
    """
    facts = []

    # What it actually said on air, from the station's own record of it.
    from brain.briefing import _is_spoken, clip, demojibake

    messages = (snapshot.get("session") or {}).get("messages") or []
    for message in messages[-14:]:
        text = " ".join(str(message.get("text") or "").split())
        kind = str(message.get("kind") or "").lower()
        # Bookkeeping is not speech, and a line about an earlier CALL is not
        # something the audience heard as part of the show.
        if not text or not _is_spoken(message) or kind in {"callin", "caller"}:
            continue
        facts.append("you said on air: " + clip(demojibake(text), 220))

    topic = " ".join(str(show.get("topic") or "").split())
    if topic:
        facts.append("what this show is about: " + clip(demojibake(topic), 300))

    played = (snapshot.get("state") or {}).get("history") or []
    for track in played[:8]:
        title = " ".join(str(track.get("title") or "").split())
        artist = " ".join(str(track.get("artist") or "").split())
        if title and artist:
            facts.append('played tonight: "%s" by %s' % (title, artist))
        elif title:
            facts.append('played tonight: "%s"' % title)
    now = (snapshot.get("now_playing") or {}).get("nowPlaying") or {}
    title = " ".join(str(now.get("title") or "").split())
    if title:
        artist = " ".join(str(now.get("artist") or "").split())
        facts.append('playing right now: "%s"%s'
                     % (title, (" by " + artist) if artist else ""))
    if show.get("name"):
        facts.append("the show on air is called %s" % show["name"])
    if persona.get("name"):
        facts.append("the DJ on air is %s" % persona["name"])
    return facts


def _clip(text: str, budget: int) -> str:
    return " ".join(str(text or "").split())[:budget].strip()


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9']+", str(text or "").lower())
            if len(w) > 2}


def answer_is_grounded(answer: str, facts: list) -> bool:
    """Is this answer actually IN what we handed over?

    Deliberately loose: "Rod Stewart" against 'played tonight: "Hot Legs" by
    Rod Stewart' has to pass, and so does "Hot Legs". Every meaningful word of
    the answer must appear somewhere in the facts — which "a plain bagel"
    could never have done, because no fact mentioned a bagel.

    Loose in the safe direction. It cannot catch an answer that is wrong but
    made of the right words; it does catch one invented out of nothing, which
    is the failure that actually happened.
    """
    wanted = _words(answer)
    if not wanted:
        return False
    known = set()
    for fact in facts or []:
        known |= _words(fact)
    return wanted.issubset(known)


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


async def invent(cfg: dict, station, persona: dict, snapshot: dict,
                 show: dict) -> dict:
    """A question and its answer, grounded in tonight's facts. `{}` if not.

    Same brain and the same full prompt a reply uses, for the same reason
    `premise.invent` does: a question written against half the persona is a
    question about somebody else's show.
    """
    from livekit.agents import llm as lk_llm

    from brain.assemble import build_system_prompt
    from call.providers import build_llm

    facts = facts_from(snapshot or {}, show or {}, persona or {})
    if len(facts) < 2:
        # Nothing verifiable to ask about — a station that has just come up,
        # or a snapshot that came back empty.
        log.info("no facts to set a quiz from")
        return {}

    prompt = await build_system_prompt(station, persona, cfg=cfg, mode="chat")
    ctx = lk_llm.ChatContext.empty()
    ctx.add_message(role="system", content=prompt)
    ctx.add_message(role="user", content=ASK.format(
        facts="\n".join("  - " + f for f in facts)))

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
        return {}
    if not answer_is_grounded(quiz["answer"], facts):
        # The bagel gate. Airing this would put a false answer on the
        # broadcast and mark a correct caller wrong.
        log.info("quiz answer is not in tonight's facts, dropping it: %r",
                 quiz["answer"])
        return {}
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
