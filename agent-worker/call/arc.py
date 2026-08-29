"""Whether this call is already over, and the word that stops it restarting.

The two failures this owns, both recorded on one live harness call
(2026-08-25, against the deployed stack): the DJ said goodbye TWICE — a full
farewell, then a second full farewell on the next turn — and when an on-air
hold interrupted the ENDED conversation, the come-back line ("alright, I'm
back — pick the conversation up where you left it") was composed for a
caller who had already signed off. The call ran about a minute past its
natural end before the DJ finally ended it.

Both are the same across-turn fact the turn loop cannot see: THE CALL IS
OVER. Every turn is judged alone, so a second farewell reads as a fresh
line and the come-back instruction reads as continuity. This module owns
that one fact — the first slice of the orchestration stream's director, and
deliberately no bigger than the fact itself. door.py's argument holds here
too: a tiny object that answers one question beats a general
conversation-state machine nobody can verify.

conduct.CLOSING already says all of this in prose and scores 1/4 on the
scenario it is most about (three rewrites failed — HANDOVER-intent D.7).
Same trade as the door: the prompt pays on every turn of every call forever
and still loses the across-turn case, because no sentence in a system
prompt can remember what happened two turns ago. This pays only when a call
actually ends, which is once.

Not wired into the text line yet, on purpose rather than by omission: chat
has neither an end_call tool nor a come-back path, so there is no mechanism
for the arc to serve there — a hint pushing a tool the surface does not
have would be the fork conduct_chat.py warns about. When chat grows a close
of its own, this object is the one it should reuse.

The detectors are door.py's own SIGNALS_DONE, reused rather than a sixth
regex family (HANDOVER-intent §1: five mechanisms already answer semantic
questions with lexical patterns, and each was found deaf or over-eager
alone). The arc never acts on one match: everything it does is gated on
BOTH sides having said goodbye, which is what keeps a mid-call "that's it
for the news" from ending anybody's call.
"""

from __future__ import annotations

from . import door as door_mod

# The word in the DJ's ear when it starts a turn on a call that is over.
# Same delivery as the door hint — a system message on the reply path — and
# the same honesty rule: it names what to do (end_call), not just what to
# avoid, because "don't say goodbye again" with no exit leaves the model
# holding a turn it cannot fill.
FAREWELL_HINT = (
    "[Note to you, not from the caller: the goodbyes have been said on this "
    "call — yours and theirs. Do not perform another farewell. One short "
    "word at most if their line needs one, and use the end_call tool in "
    "this same turn. The call is over.]"
)


class CallArc:
    """One call's memory that both sides have said goodbye.

    Two flags, one question. `caller_done` follows the caller's turns and a
    substantive turn CLEARS it — people say "that's everything" and then
    remember the thing they actually rang about, and a call that has
    reopened is a call, not a fault. `dj_farewell` follows the DJ's own
    lines, judged on the tail the way the door judges its question: a
    farewell is how a line ends, not a word it contains.
    """

    def __init__(self) -> None:
        self.caller_done = False
        self.dj_farewell = False
        # Read by the record, like door.corrections: a call that had to be
        # told the goodbyes were done is a different report from one that
        # ended cleanly, and neither shows up anywhere without this.
        self.corrections = 0

    @property
    def ending(self) -> bool:
        return self.caller_done and self.dj_farewell

    def dj_said(self, text: str) -> None:
        tail = str(text or "").strip()[-80:]
        if door_mod.signals_done(tail):
            self.dj_farewell = True

    def hint_for(self, caller_text: str) -> str:
        """Judge the caller's turn, and return the note for this one or "".

        Order matters: the turn is judged BEFORE the hint is decided, so a
        caller who reopens the call gets a normal turn and a caller whose
        line is another goodbye gets the DJ told to finish it.
        """
        t = str(caller_text or "").strip()
        if door_mod.signals_done(t):
            self.caller_done = True
        elif t:
            # A real turn reopens the call — both flags, because the DJ's
            # next line is conversation again, not a resumed farewell.
            self.caller_done = False
            self.dj_farewell = False
            return ""
        if self.ending:
            self.corrections += 1
            return FAREWELL_HINT
        return ""


