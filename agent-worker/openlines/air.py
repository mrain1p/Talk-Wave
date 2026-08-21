"""What actually goes out on the broadcast: the invitation, the reminders,
and the sign-off.

Every one of these is a direction, not a script. We hand the station a subject
and an instruction; the station rewrites it in whoever is live and speaks that.
`/dj/say` with mode "styled" has always done exactly this, so nothing new is
asked of the station and nothing about it is stored there.

We keep what came BACK. `/dj/say` returns `spoken` — the words that actually
aired — and that, not the direction we sent, is what a caller heard and what
the DJ must be reminded of when somebody turns up. Pin the direction instead
and the DJ invents a second, different version of its own topic.
"""

from __future__ import annotations

import logging

log = logging.getLogger("talkwave.openlines")

# The announcement lands in the station's feed as a call-in line, which
# brain.briefing._PRIVATE_KINDS keeps out of the next conversation's chatter
# window. That is correct here and not an oversight: openlines.prompt pins the
# premise deliberately, and without the filter the same words would arrive in
# the prompt twice — once pinned, once as "things you said on air".
SAY_KIND = "callin"


def _address_clause(cfg: dict) -> str:
    address = str(cfg.get("open_lines_address") or "").strip()
    if not address:
        # Deliberately not "tell them to call in" with no address: a DJ told to
        # invite calls and given nowhere to send them invents somewhere.
        return ("Do not give out any address, number or web address — the "
                "audience already knows where to find the station.")
    return (f"Tell them where to reach you: {address}. Say it clearly and say "
            "it once, the way a presenter reads a station address out loud.")


def open_direction(premise: str, cfg: dict) -> str:
    return (
        "[Producer, off air. Open the lines.\n"
        f"The subject is: {premise}\n"
        "Say — in your own voice, the way you would actually say it — that "
        "you want to hear from the audience on this, and what you are asking "
        "them. Keep it short: this is a link between records, not a monologue. "
        f"{_address_clause(cfg)}\n"
        "Do not read this instruction out, and do not announce the show or "
        "recap what has been playing.]"
    )


def remind_direction(premise: str, cfg: dict, aired: str) -> str:
    return (
        "[Producer, off air. The lines are still open on the same subject and "
        "you are coming back to it — briefly.\n"
        f"The subject is: {premise}\n"
        "You already put this to them earlier, in these words:\n"
        f"  {aired}\n"
        "Raise it again in a way that does NOT repeat that phrasing — a "
        "different angle on the same question, or a nudge for the people who "
        "have just tuned in. One or two sentences. "
        f"{_address_clause(cfg)}\n"
        "Do not read this instruction out.]"
    )


def followup_direction(premise: str, line: str, cfg: dict) -> str:
    """Telling the room what came back.

    `line` is already the POSITION rather than the person or their words —
    followup.line_for made that judgement against the transcript. This only has
    to get it said in voice, and stop the DJ embroidering identity back onto
    it: a model handed "someone argued X" will cheerfully invent a caller named
    Dave from Fresno to attribute it to, and that is a real person being given
    words and a hometown they never offered.
    """
    return (
        "[Producer, off air. Somebody came in on tonight's subject and you "
        "are telling the room what they said.\n"
        f"The subject is: {premise}\n"
        f"What came back: {line}\n"
        "Say that on air in your own voice, one or two sentences. React to it "
        "— agree, argue back, or sit with it — the way you would if somebody "
        "had just said it to you.\n"
        "Do NOT invent a name, a place, or anything else about who they are: "
        "you were not told, and making it up puts words in a real person's "
        "mouth. Do not quote them. "
        f"{_address_clause(cfg)}\n"
        "Do not read this instruction out.]"
    )


def close_direction(premise: str, took_part: int) -> str:
    heard = (
        "Nobody took it up. Close it out the way you would on air when a "
        "question does not land — lightly, without apologising for it and "
        "without asking again."
        if took_part <= 0 else
        f"{took_part} took it up. Close it out, and say what you took from "
        "what came in — briefly, no roll call of names."
    )
    return (
        "[Producer, off air. The lines are closed on tonight's subject.\n"
        f"The subject was: {premise}\n"
        f"{heard}\n"
        "One or two sentences, in your own voice. Do not open another subject, "
        "do not invite any more responses, and do not read this instruction "
        "out.]"
    )


async def say(station, direction: str) -> str:
    """Hand a direction to the booth; return the words that aired.

    Empty string means nothing went out — every caller treats that as "the
    line did not open", because a premise nobody heard is not an open line.
    """
    try:
        result = await station.dj_say(direction, mode="styled", kind=SAY_KIND)
    except Exception as e:                                     # noqa: BLE001
        log.warning("open lines: the booth would not take the line: %s", e)
        return ""
    if not (result or {}).get("ok"):
        log.warning("open lines: the booth refused the line: %s",
                    str((result or {}).get("error"))[:120])
        return ""
    spoken = str((result or {}).get("spoken") or "").strip()
    if not spoken:
        # The station aired something it did not echo back. Rare, and it leaves
        # us unable to tell the DJ what it said — so treat it as a miss rather
        # than pin a premise nobody can be reminded of accurately.
        log.warning("open lines: the booth aired a line but returned no text")
    return spoken
