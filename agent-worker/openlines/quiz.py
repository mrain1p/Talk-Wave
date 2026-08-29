"""A quiz the DJ can actually mark, about things that are actually true.

Three failures on the operator's station built this, and they are different.

**Nothing decided the question.** Given the subject "Quiz question" — a LABEL,
not a subject — the invitation aired as *"the suits want me to push something
called a 'Quiz question'"*, and when a caller took it up the DJ invented a
question on the spot with no answer in mind, read their answer as a song
request, queued a track at them and told them they had whiffed. So: the
question AND its answer are settled before anything airs, both are pinned to
the record, and the block hands the DJ the answer.

**Then the answer was false.** Told to ask about its own show, the DJ asked
what drink somebody at the bar had ordered and answered "a plain bagel". It
had said neither. A DJ has no reliable memory of its own broadcast — invited
to remember one, it invents one.

**But restricting it to verifiable facts was too tight.** A quiz can be about
anything that belongs to the show's world. The sin was never general
knowledge, it was invented AUTOBIOGRAPHY — the DJ claiming it said or did
something it did not.

So a question declares which it is:

- "theme" — general knowledge from the show's world. Not checked here, and
  it does not have to be about music: Donovan's Pub has a fire and a dog and
  a county, and a detective show has none of those. Prescribing a music
  thread would flatten every persona into the same show.
- "tonight" — a claim about this broadcast. The answer is CHECKED against
  facts assembled here, and a quiz that fails is dropped rather than aired.

Anything that does not say which is treated as "tonight", so a model that
omits the field gets the careful branch rather than the loose one.
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
    "Tonight's bit is: {bit}\n"
    "\n"
    "Turn that into the ACTUAL thing you will say on air — the specific "
    "question, the specific choice, the specific challenge. Not the name of "
    "the bit: a listener hearing \"{bit}\" and nothing else has been told "
    "there is a game, not invited into one.\n"
    "\n"
    "Make it yours. Pitch it in the world of THIS show — its subject, its "
    "place, its era, whatever the thing behind it is. It does not have to be "
    "about music; it has to be something that could only come from your show.\n"
    "\n"
    "If it is the kind of bit with a right answer, give me that answer too, "
    "and say which sort it is:\n"
    '  kind "theme" — general knowledge. You must be genuinely sure of it; if '
    "you are hazy, pick something else.\n"
    '  kind "tonight" — a claim about THIS broadcast. The answer must come '
    "from the list below, because you do not reliably remember your own "
    "evening: if it is not in the list then it did not happen, however "
    "strongly you feel otherwise.\n"
    "{facts}\n"
    "\n"
    "If the bit has no right answer — an opinion, a story, an argument for you "
    "to judge — leave the answer empty. That is a normal outcome, not a "
    "failure.\n"
    "Reply as JSON and nothing else, exactly:\n"
    '{{"kind": "theme" or "tonight", "question": "...", "answer": "..."}}\n'
    "The question is one sentence, exactly as you would say it on air.]"
)

# What a fresh shelf offers as BITS rather than one-off subjects: a recurring
# thing the DJ resolves into tonight's specific instance before it airs.
# Names only, deliberately. Building logic per format would fix twelve shapes
# and forbid the thirteenth; one resolve step handles any of them, and an
# operator can add "Desert Island Discs" without anybody writing code.
FORMATS = (
    "Settle the Argument — two callers give their sides, you decide who wins",
    "Stump the DJ — callers try to ask you something you cannot answer",
    "Name That Tune — describe a song without naming it, they guess",
    "Finish the Lyric — you give part of a lyric, they finish it",
    "Two Truths and a Lie — the caller gives three, you pick the lie",
    "Would You Rather — two ridiculous choices, they have to pick one",
    "Hot Take Hotline — they give an opinion, you rule on whether it is a "
    "real hot take",
    "Rate My Take — they present an opinion, you rate it out of ten",
    "Ask the DJ — advice, opinions, recommendations, arguments, anything",
    "Guess the Year — you give a clue, they name the year",
    "Local Trivia — the area, its landmarks, its history, its odder corners",
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
    from brain.briefing import is_spoken, clip, demojibake

    import station as station_mod

    messages = (snapshot.get("session") or {}).get("messages") or []
    for message in messages[-14:]:
        text = " ".join(str(message.get("text") or "").split())
        kind = str(message.get("kind") or "").lower()
        # Bookkeeping is not speech, and a line about an earlier CALL is not
        # something a quiz should hang a question on. The kind check has
        # never matched live — the station coerces our kinds to 'dj-speak'
        # (see brain/briefing._PRIVATE_KINDS) — so `said_by_us` is the check
        # that fires.
        if not text or not is_spoken(message) or kind in {"callin", "caller"}:
            continue
        if station_mod.said_by_us(text):
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
    # No question, no bit. No ANSWER is fine — "Ask the DJ" and "Rate My
    # Take" have nothing to mark, and demanding one would only make the
    # model invent something to fill the field.
    if not question:
        return {}
    # Anything that is not explicitly general knowledge is treated as a claim
    # about tonight, and therefore checked. A model that omits the field, or
    # invents a third kind, gets the careful branch rather than the loose one.
    kind = str(data.get("kind") or "").strip().lower()
    return {"question": question, "answer": answer,
            "kind": "theme" if kind == "theme" else "tonight"}


async def resolve(cfg: dict, station, persona: dict, snapshot: dict,
                  show: dict, bit: str = "") -> dict:
    """A question and its answer, grounded in tonight's facts. `{}` if not.

    Same brain and the same full prompt a reply uses, for the same reason
    `premise.invent` does: a question written against half the persona is a
    question about somebody else's show.
    """
    from livekit.agents import llm as lk_llm

    from brain.assemble import build_system_prompt
    from call.providers import build_llm

    facts = facts_from(snapshot or {}, show or {}, persona or {})

    prompt = await build_system_prompt(station, persona, cfg=cfg, mode="chat")
    ctx = lk_llm.ChatContext.empty()
    ctx.add_message(role="system", content=prompt)
    ctx.add_message(role="user", content=ASK.format(
        bit=bit or "a quiz — a question with one right answer",
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

    resolved = _parse(out)
    if not resolved:
        log.info("the bit came back unresolvable: %s", out[:160])
        return {}
    # The bagel gate, and only for questions that CLAIM to be about this
    # broadcast. General knowledge in the show's own subject is fair game and
    # cannot be checked against tonight's facts by definition — the sin was
    # never trivia, it was invented autobiography.
    if (resolved["answer"] and resolved["kind"] == "tonight"
            and not answer_is_grounded(resolved["answer"], facts)):
        log.info("the bit claims to be about tonight but its answer is not "
                 "in the facts, dropping it: %r", resolved["answer"])
        return {}
    return resolved


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
