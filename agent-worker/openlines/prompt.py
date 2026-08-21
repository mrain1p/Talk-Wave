"""The block that tells the DJ what it put up, and how to greet somebody who
turns up while it stands.

ADDITIVE, ALWAYS. Every function here returns `""` when no line is live, and
`brain.assemble` concatenates that — so with the feature off, or between
topics, the assembled prompt is byte-for-byte what it was before Open Lines
existed. `TestOpenLinesIsAdditive` holds that, because "we did not change the
existing path" is a claim worth proving rather than asserting.

The fact and the rule live in one block rather than being split across
briefing and conduct in the house style. That is deliberate: they share one
gate — is a line open — and a gate evaluated in two modules is the exact shape
that shipped two settings unreachable. One block, one condition, one place to
look.
"""

from __future__ import annotations

from datetime import datetime, timezone

from openlines import state


def _minutes_ago(record: dict) -> str:
    opened = record.get("opened_at")
    try:
        dt = datetime.fromisoformat(str(opened))
    except (TypeError, ValueError):
        return "a little while ago"
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    mins = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins == 1:
        return "a minute ago"
    return f"{mins} minutes ago"


# How the arrival reached the booth, and therefore what "ask them" looks like.
_ARRIVAL = {
    "call": ("They are on the phone and you are talking out loud.",
             "ask them"),
    "chat": ("They are typing, on the station's text line.", "ask them"),
}


def greeting_clause(cfg: dict, persona: dict | None = None,
                    show_name: str = "") -> str:
    """What to add to the PICKUP instruction while a line is open. "" if none.

    The block below reaches the DJ's conduct, which governs every turn except
    the one that matters most here: the greeting is generated from its own
    instruction in call/greeting.py, before the caller has said anything, and
    it never saw any of this. So a listener who heard the topic on air, rang
    in, and got "what would you like to hear?" — reported 2026-08-21, with a
    line demonstrably live at the time.

    Deliberately phrased as a REPLACEMENT for the greeting's own open question
    rather than an addition. Both at once is two questions in a breath, and the
    conduct block two inches away says to ask one, lightly.
    """
    if not cfg.get("open_lines_enabled"):
        return ""
    persona = persona or {}
    record = state.current(str(persona.get("id") or ""), show_name)
    if not record:
        return ""
    premise = str(record.get("premise") or "").strip()
    if not premise:
        return ""
    if str(record.get("quiz_answer") or "").strip():
        return (
            " You have a QUIZ running on air right now and they may well be "
            f"ringing to answer it: {premise}. So INSTEAD of your usual "
            "opening question, ask whether they have come to take the quiz or "
            "about something else — one question, lightly, in your own voice, "
            "not both. Do not give them the answer, do not re-read the "
            "question as an announcement, and do not read out any address."
        )

    return (
        " You have a subject up on air right now and they may well be ringing "
        f"about it: {premise}. So INSTEAD of your usual opening question, ask "
        "whether they have come about that or about something else — one "
        "question, lightly, in your own voice, not both. Do not re-read the "
        "subject as an announcement and do not give out any address: they are "
        "already through to you."
    )


def block(cfg: dict, persona: dict | None = None, show_name: str = "",
          mode: str = "call") -> str:
    """The Open Lines block, or `""`.

    `persona` and `show_name` are the live ones: an open line belongs to the DJ
    that opened it and dies when either changes, so a stale record simply reads
    as no line at all.
    """
    if not cfg.get("open_lines_enabled"):
        return ""
    persona = persona or {}
    record = state.current(str(persona.get("id") or ""), show_name)
    if not record:
        return ""
    aired = str(record.get("spoken") or "").strip()
    premise = str(record.get("premise") or "").strip()
    if not aired and not premise:
        return ""

    where, ask = _ARRIVAL.get(mode, _ARRIVAL["call"])
    lines = [
        "",
        "# The subject you put to the audience — the lines are open",
        f"You opened this {_minutes_ago(record)} and it still stands. "
        f"{where}",
    ]
    if aired:
        lines += [
            "",
            "Your own words on air, which is what they may have heard — do not "
            "repeat them, and do not contradict them. Any detail in here is now "
            "something you said, so keep to it:",
            "  " + aired,
        ]
    if premise and premise.lower() not in aired.lower():
        lines.append(f"\nWhat you are actually asking: {premise}")

    # A quiz is marked, not discussed, and the answer lives only here.
    answer = str(record.get("quiz_answer") or "").strip()
    if answer:
        from openlines import quiz as quiz_mod

        lines.append(quiz_mod.conduct(premise, answer))
        lines += [
            "",
            "Whoever turns up:",
            f"- Near the start, {ask} whether they have come to take the "
            "quiz or about something else. One question, lightly.",
            "- If they have not come for the quiz, drop it completely and "
            "deal with them exactly as you would on any other day.",
            "- Do not re-announce the quiz as though they had not heard it, "
            "and do not read out any address. They are already through.",
        ]
        return "\n".join(lines) + "\n"

    lines += [
        "",
        "How to handle whoever turns up:",
        f"- Open by finding out which it is. Near the start, {ask} whether "
        "they have come about this or about something else. One question, "
        "asked lightly, in your own voice — not a menu, and not a form.",
        "- If they are here for it: take them seriously. Get their actual "
        "position, push back where you disagree, and treat it as a "
        "conversation you wanted to have, because you did.",
        "- If they are not: drop it completely and never bring it up again. "
        "A request, a question, a hello — deal with it exactly as you would "
        "on any other day. Someone who wants a record played is not a failed "
        "contribution to your topic.",
        "- Do not re-announce the subject as though they had not heard it, "
        "and do not read out any address. They are already through.",
    ]
    return "\n".join(lines) + "\n"


def voicemail_clause(cfg: dict, persona: dict | None = None,
                     show_name: str = "") -> str:
    """One sentence appended to the machine's greeting while a line stands.

    Voicemail cannot ask which they came for — there is nobody to answer. So
    it names the subject and leaves the choice with the caller, which is the
    honest shape for a one-way door. Empty when no line is open, so the
    staged clip reverts to the greeting it has always been.
    """
    if not cfg.get("open_lines_enabled"):
        return ""
    record = state.current(str((persona or {}).get("id") or ""), show_name)
    if not record:
        return ""
    premise = str(record.get("premise") or "").strip()
    if not premise:
        return ""
    return (f" And if you're calling about tonight's question — {premise} — "
            "leave me your answer and I'll play the best of them.")
