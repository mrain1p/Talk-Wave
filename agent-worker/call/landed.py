"""The wind-down after a landed request — mechanism, not prose.

CLOSING is 4,091 characters of the spoken conduct, and its own measurements
call it the least effective section in the prompt: with all four paragraphs
present the DJ still asked "anything else?" three rounds out of three
(2026-08-14), and two of its four rules scored BETTER ablated. The diagnosis
is the one door.py already wrote down: a rule stated at assembly time is a
long way from the moment it governs. So the steer moves to the moment — when
a station action LANDS and the caller's next words open nothing new, ONE note
tells the DJ this is the call's natural crest.

Ships OFF (`closing_nudge`, no panel row): behaviour-sensitive, and the
closing scenario set is the instrument that decides it — the same bar
single_lookup_tool waits behind. The prose section is untouched until this
holds numbers; if the mechanism wins, CLOSING shrinks to its measured core
in the same release that flips the switch.
"""

from __future__ import annotations

import re

#: Words in the caller's turn that OPEN something rather than close it — a
#: landed request followed by "and one more thing" is mid-call, not a crest.
#: Deliberately broad: a missed wind-down costs nothing (the old behaviour),
#: a wind-down over a fresh ask costs the caller their momentum.
REOPENS = re.compile(
    r"\b(another|also|one more|next one|and can|and could|can you|could you|"
    r"what about|how about|actually|wait|hold on|instead)\b", re.I)

HINT = (
    "[Note to you, not from the caller: their request has just LANDED — the "
    "receipt is in. If their words open nothing new, this is the natural "
    "crest of the call: say what landed in your own voice, leave something "
    "real in the air — what's coming up, where their record sits — and let "
    "the call breathe out. Do NOT ask whether they want anything else; "
    "reopening the order window is the one move that flattens an ending.]"
)


class Landed:
    """Armed by the action ledger moving, fired once on the next caller turn.

    The same shape as Door: one question, one turn, then it forgets. `fired`
    is read by the record, not the logic — a call that crested three times
    is a different report from one that never did.
    """

    def __init__(self, actions) -> None:
        self.actions = actions
        self._seen = int(getattr(actions, "count", 0) or 0)
        self.armed = False
        self.fired = 0

    def hint_for(self, caller_text: str) -> str:
        count = int(getattr(self.actions, "count", 0) or 0)
        if count > self._seen:
            self.armed = True
        self._seen = count
        if not self.armed:
            return ""
        # Consumed either way: the crest was this turn or it wasn't — a held
        # flag would wind down a conversation two asks downstream of it.
        self.armed = False
        if REOPENS.search(str(caller_text or "")):
            return ""
        self.fired += 1
        return HINT
