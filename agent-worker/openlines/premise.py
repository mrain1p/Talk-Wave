"""Where a topic comes from: the operator's list, or the DJ's own head.

Both return a SUBJECT, never a script. The words that go out are written by
the station in the live persona's voice (see `air.announce`) — this module
decides what the DJ is talking about and nothing else. That split is the whole
design: a premise generator that also writes dialogue produces a DJ who sounds
like a different DJ every time it opens a line.
"""

from __future__ import annotations

import logging

log = logging.getLogger("talkwave.openlines")

# A premise is a note to a presenter, not a paragraph. Long ones survive the
# trip to the station and come back as a monologue.
PREMISE_BUDGET = 220

INVENT = (
    "[Not a caller. This is the station's producer, off air.\n"
    "Give me ONE subject to put to the audience tonight — something you would "
    "genuinely want to hear people's answers to, drawn from tonight's show: "
    "the music you have played, the show's own theme, something you actually "
    "have an opinion about.\n"
    "Rules: one sentence. It must be answerable by someone who just tuned in, "
    "with no context beyond what you would say on air. Prefer something real "
    "over something invented — a record, an opinion, a memory it would prompt "
    "— because people will engage with it seriously and invented detail falls "
    "apart when they do.\n"
    "Reply with the subject alone. No preamble, no quotation marks, no "
    "greeting, do not write the announcement itself.]"
)


def _clean(text: str) -> str:
    """One line, no wrapping quotes, no lead-in. Models like to answer a
    request for a subject with 'Sure — how about:'."""
    line = " ".join(str(text or "").split())
    for opener in ("Sure,", "Sure —", "Here's", "Here is", "How about"):
        if line.lower().startswith(opener.lower()):
            _, _, rest = line.partition(":")
            line = (rest or line).strip()
            break
    return line.strip().strip('"').strip("'")[:PREMISE_BUDGET].strip()


def clean(text: str) -> str:
    """A subject typed by the operator, tidied the same way an invented one
    is — one line, no wrapping quotes, budgeted."""
    return _clean(text)


def take_by_id(premise_id: str) -> dict:
    """One named subject off the shelf, marked used. For the dashboard's
    dropdown, where the operator picked THIS one rather than asking for
    whatever was least recently used."""
    from openlines import premises

    item = premises.take_one(str(premise_id))
    if not item:
        return {}
    return {**item, "text": _clean(item.get("text"))}


def take_from_shelf(persona_id: str) -> dict:
    """The next subject off the operator's shelf for THIS DJ, marked used.

    Assignment is per premise, not per list: an argument that suits one persona
    is wrong in another's mouth, and a single shared pool made the DJ allowlist
    do a job it could not do. Unassigned entries are available to everyone.
    """
    from openlines import premises

    item = premises.take_next(persona_id)
    if not item:
        return {}
    return {**item, "text": _clean(item.get("text"))}


async def invent(cfg: dict, station, persona: dict) -> str:
    """One subject, in the DJ's own head, from the station context it already
    has. Same brain and the same full prompt a reply uses — a premise written
    against half the persona is a premise in somebody else's voice.
    """
    from livekit.agents import llm as lk_llm

    from brain.assemble import build_system_prompt
    from call.providers import build_llm, stream_reply

    prompt = await build_system_prompt(station, persona, cfg=cfg, mode="chat")
    ctx = lk_llm.ChatContext.empty()
    ctx.add_message(role="system", content=prompt)
    ctx.add_message(role="user", content=INVENT)

    model = build_llm(cfg)
    out = ""
    try:
        out, _ = await stream_reply(model, ctx)
    except Exception as e:                                     # noqa: BLE001
        # A premise we could not write is not an error worth waking anyone
        # for: nothing has aired yet, and the director simply tries again on
        # its next pass.
        log.info("could not invent a premise: %s", e)
        return ""
    finally:
        try:
            await model.aclose()
        except Exception:                                      # noqa: BLE001
            pass
    return _clean(out)
