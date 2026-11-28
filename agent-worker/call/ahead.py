""""Cancel what's ahead of mine" is not "cancel mine" — one word in its ear.

The call that prompted this (2026-11-28 02:44Z, record 20261128-024256): the
caller queued six KPop Demon Hunters tracks, heard they were "about six or
eight minutes" out, and said "All right, cancel anything before them." The
DJ ran subwave_clear_from_queue with the caller's OWN batch label and pulled
all six. The caller tried to object ("I want your eye to…"), heard "That
stuff is out of the queue for you. I'm sorry", and hung up.

The words that carry the meaning are the small ones — before, ahead of, in
front of — and the model read straight past them to "cancel" + "them". The
prompt already says this line cannot jump the queue and must never pull
another caller's records to make room; what it lacked was the reminder at
the moment the caller says it. Same shape as door.py: a note on the NEXT
reply, costing nothing on every turn that doesn't say it.

What the note asks for is the honest answer the tools support: their picks
stay, the line can't reorder or clear the shared queue in their favour, and
the DJ says where theirs sit. A caller who then says "fine, drop mine" has
made a separate, explicit ask, and the removal rules handle that one.
"""

from __future__ import annotations

import re

from spoken_rules import straight

# A removal verb, then — within the same breath — a word that points at what
# is IN FRONT of something. Both halves must be present: "cancel them" alone
# is an ordinary cancel and is not this guard's business.
_CLEAR = (r"(?:cancel|clear|remove|pull|drop|dump|delete|scrap|skip|ditch|"
          r"get rid of|take out|knock out)")
_AHEAD = (r"(?:before|ahead of|in front of|in the way of|"
          r"(?:that'?s|is|are|what'?s|whatever'?s) (?:ahead|in front|first|"
          r"in the way|queued (?:up )?(?:before|ahead)))")
CLEAR_AHEAD = re.compile(
    rf"\b{_CLEAR}\b[^.?!]{{0,40}}?\b{_AHEAD}\b"
    # "everything ahead of mine", "whatever's in front"
    rf"|\b(?:everything|anything|whatever|all|stuff|the rest)\b[^.?!]{{0,12}}?"
    rf"\b(?:ahead|in front)\b[^.?!]{{0,30}}?\b{_CLEAR}\b",
    re.IGNORECASE)

# The same wish phrased as a reorder: "move them up", "bump mine to the
# front", "can they go first". The answer is the same — this line can't
# jump the queue — and so is the risk: the nearest tool that SOUNDS like
# acting on it is a clear-out of their own picks.
JUMP_AHEAD = re.compile(
    r"\b(?:move|bump|push|put|shift|get)\b[^.?!]{0,25}?\b(?:them|those|mine|"
    r"it|that|these|my (?:songs?|tracks?|picks?|stuff|ones))\b[^.?!]{0,15}?"
    r"\b(?:up (?:front|top)|up the queue|to the (?:front|top)|first|"
    r"next)\b",
    re.IGNORECASE)

HINT = (
    "[Note to you, not from the caller: they want what is AHEAD of their "
    "picks out of the way so theirs play sooner — they are NOT asking you "
    "to remove their own picks. Do not pull, cancel or clear anything this "
    "call queued for them. This line can't jump the queue, and the tracks "
    "in front belong to the station's programme or to other listeners, so "
    "nothing gets pulled to make room. Tell them plainly where their picks "
    "sit — what the receipt said, or what the queue shows — and that theirs "
    "play straight after what's already ahead. Only if they then say to "
    "drop their own picks is that a cancel.]"
)


def asks_to_clear_ahead(text: str) -> bool:
    """Does this caller line ask to clear (or jump) what's in front of theirs?"""
    t = straight(text)
    return bool(CLEAR_AHEAD.search(t) or JUMP_AHEAD.search(t))


class Ahead:
    """Stateless apart from its tally: fires on the turn that says it.

    `fired` is read by the record, not the logic — the same convention as
    Door.corrections.
    """

    def __init__(self) -> None:
        self.fired = 0

    def hint_for(self, caller_text: str) -> str:
        if not asks_to_clear_ahead(caller_text):
            return ""
        self.fired += 1
        return HINT
