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

First run (2026-08-31, REPEATS=5): landed-request doubled 2/5 -> 4/5 and
door-twice held 5/5, but let-go and thank-you each gave a round back —
"breathe out" read as "keep the line warm" on turns the caller had already
closed. The hint's last sentence is that run's fix: a caller signalling
done still gets end_call.

Second run (2026-08-31, same set, REPEATS=5, fixed hint): thank-you
recovered 4/5 but the end_call sentence leaked the OTHER way — landed-
request 3/5 and door-twice 4/5 both to an over-eager end_call. Across the
four contested rows: off 15/20, first hint 15/20, this hint 14/20 — no arm
dominates, every gap one round wide. VERDICT: the mechanism trades faults
instead of removing them on this model, so the switch STAYS OFF (the
classifier's gate rule: not equal-or-better everywhere means not on), and
CLOSING keeps its prose. The machinery stays for the next model change,
like the classifier before it.
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
    "reopening the order window is the one move that flattens an ending. "
    "And if their words already SAY they are done — a thank-you with "
    "nothing new in it, a that's-everything — the crest IS the goodbye "
    "turn: sign off in your own voice and call end_call; breathing out is "
    "not holding the line open.]"
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
