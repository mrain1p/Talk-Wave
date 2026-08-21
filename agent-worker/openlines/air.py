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


def own_address() -> str:
    """Where this deployment actually answers, for when nothing is configured.

    The operator should not have to type an address Talk Wave already knows —
    and an address typed in two places is an address that drifts. Derived from
    LIVEKIT_PUBLIC_URL because that is the one value a working deployment must
    already have right: it is what the caller's browser connects to.

    Empty for a local or unset deployment, because "come and call me at
    localhost" is worse than saying nothing.
    """
    from api.env import LIVEKIT_PUBLIC_URL

    raw = str(LIVEKIT_PUBLIC_URL or "").strip()
    host = raw.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].strip()
    if not host or host in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return ""
    # A bare IP is not something a DJ can read out usefully on air.
    if all(part.isdigit() for part in host.split(".") if part):
        return ""
    return host


def _address_clause(cfg: dict) -> str:
    address = str(cfg.get("open_lines_address") or "").strip() or own_address()
    if not address:
        # Deliberately not "tell them to call in" with no address: a DJ told to
        # invite calls and given nowhere to send them invents somewhere.
        return ("Do not give out any address, number or web address — the "
                "audience already knows where to find the station.")
    return (f"Send them to {address} — that is a WEB address, and it is where "
            f"the audience actually reaches you. Say it exactly as written, "
            f"once. Do not abbreviate it, do not turn it into a phone number, "
            f"and do not replace it with \"give us a call\" or \"get in touch\" "
            f"on its own: an invitation with nowhere to go is not an "
            f"invitation.")


def open_direction(premise: str, cfg: dict, quiz: bool = False) -> str:
    """Open the lines — and say so, in those words or the DJ's own.

    The first version asked the DJ to "say you want to hear from the audience",
    and every take came back as a rhetorical musing: "I'm curious, what song
    does that for you?" — lovely radio, but a listener cannot tell it is an
    invitation to actually get in touch, and no address ever appeared. A
    listener has to be told two things plainly: the lines are OPEN, and where.
    """
    # The address goes FIRST and again LAST. Buried as item three in a list it
    # was simply dropped: the station's own LLM rewrites this in persona, and
    # what it drops is whatever sits in the middle. Measured on air — "the
    # phone lines are OPEN right now… just give us a call" with no address in
    # it anywhere, from a direction that said the address was mandatory.
    address = str(cfg.get("open_lines_address") or "").strip() or own_address()
    where = f" Come and find me at {address}." if address else ""
    if quiz:
        # "I want to hear about <question>" would announce that a question
        # exists rather than asking it.
        model = (f"Quiz time — the lines are open. Here is the question: "
                 f"{premise}{where}")
    else:
        model = f"The lines are open — I want to hear about {premise}.{where}"
    keep = (
        f" You may change every word EXCEPT the address, which must come out "
        f"exactly as “{address}” and must not be turned into a phone "
        f"number or replaced with “give us a call” on its own."
        if address else
        " Do not invent an address, a number or a web address — a DJ given "
        "nowhere to send people makes somewhere up."
    )
    return (
        "[Producer, off air. Here is the line to put out:\n"
        f"  “{model}”\n"
        "Say that on air in your own voice — your phrasing, your rhythm, as "
        "long or as short as you would really make it." + keep
        + (" Keep all of it: that the lines are open, what you are asking, and "
           "where to reach you.\n" if address else
           " Keep both: that the lines are open, and what you are asking.\n")
        +
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


def close_direction(premise: str, took_part: int, reported: list | None = None) -> str:
    """The closing line. `reported` is what the DJ ACTUALLY told the room about
    while the line stood — the follow-ups that aired, in order.

    That argument exists because the sign-off used to invent its own content.
    Told only "3 took it up" and asked to "say what you took from what came
    in", the DJ had no idea what came in and filled the gap: on the operator's
    station, 2026-08-21, it closed a line about first driving songs with "for
    everyone else, it's some obscure ballad or a goddamn polka" — nobody had
    said either, and the DJ had been handed a COUNT and nothing else.

    Great radio, and a caller who rang in could hear their answer described as
    something they never said. So: summarise only what was really reported, and
    with nothing reported, acknowledge that people got in touch without
    characterising them.
    """
    lines = [str(x).strip() for x in (reported or []) if str(x).strip()]
    if took_part <= 0:
        heard = ("Nobody took it up. Close it out the way you would on air when "
                 "a question does not land — lightly, without apologising for "
                 "it and without asking again.")
    elif lines:
        joined = "\n".join("  - " + line for line in lines)
        heard = ("People got in touch, and this is what you told the room about "
                 "while it stood — ALL you know about what came in:\n"
                 f"{joined}\n"
                 "Close it out on that. Do not add anyone else's answer, and do "
                 "not invent what other people said.")
    else:
        # Somebody came through, and the DJ never heard a word of it.
        heard = ("People got in touch, but you do not know what any of them "
                 "said — you were not told. Close it out warmly on the fact "
                 "that they did, and on what YOU think about the subject. Do "
                 "NOT characterise their answers, describe them, or invent "
                 "even one: those are real people and you did not hear them.")
    return (
        "[Producer, off air. The lines are closed on tonight's subject.\n"
        f"The subject was: {premise}\n"
        f"{heard}\n"
        "One or two sentences, in your own voice. Do not open another subject, "
        "do not invite any more responses, and do not read this instruction "
        "out.]"
    )


async def say(station, direction: str) -> tuple[str, bool]:
    """Hand a direction to the booth. Returns (the words that aired, aired).

    THREE outcomes, not two, and conflating the last two is a bug this feature
    had until it was air-tested on a real station:

    - refused, or the request never landed -> ("", False). Nothing went out.
    - aired, and the station echoed the words -> (words, True). The good case.
    - aired, but no words came back -> ("", True). `station.dj_say` answers a
      slow-but-sent request with `{"ok": True, "unconfirmed": True}` and no
      `spoken`, which is right: the station almost certainly said something.

    That last case used to return "" and every caller read it as "nothing
    aired", so a slow station meant listeners heard the DJ open a subject while
    Talk Wave recorded no line at all — the DJ then had no idea it had asked,
    and the operator was told the booth refused. Worse than either honest
    answer. Callers now decide for themselves what an unconfirmed line means.
    """
    try:
        result = await station.dj_say(direction, mode="styled", kind=SAY_KIND)
    except Exception as e:                                     # noqa: BLE001
        log.warning("open lines: the booth would not take the line: %s", e)
        return "", False
    if not (result or {}).get("ok"):
        log.warning("open lines: the booth refused the line: %s",
                    str((result or {}).get("error"))[:120])
        return "", False
    spoken = str((result or {}).get("spoken") or "").strip()
    if not spoken:
        log.warning("open lines: the booth aired a line but returned no text "
                    "— the words are lost, the line is not")
    return spoken, True
