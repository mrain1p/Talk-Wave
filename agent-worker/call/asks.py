"""What the caller asked for, and whether anything ever happened about it.

The gap this fills, named in the master plan and left open through three
reviews: **nothing pairs an ask to an outcome.** A record holds turns, tools and
problems. It does not hold "the caller asked for a shoutout and no shoutout was
ever sent", so the one failure the call-orchestration stream was created to
investigate — a request that gets dropped when something else interrupts, an
ask never returned to — is invisible in the archive. Not rare. Invisible.

That is why the director question keeps being answered by argument rather than
by evidence: there is no evidence either way. This is the evidence, and it is
deliberately only that. It detects and it records. Nothing branches on it, the
DJ is never told, and no turn is generated because of it. If the archive fills
up with dropped asks then a director has a case; if it does not, then the
turn-by-turn shape is fine and the stream can stop asking.

**Scoped to asks that need a TOOL.** "What's playing?" is answered in words and
leaves no receipt, so counting it would report a dropped ask on every call that
went well. What is counted is the shape that owes an action — play this, put
that on air, change the show — because that is the shape where silence is a
failure the caller can feel.

Refusals count as answered NOTHING on purpose, and that is not a bug: a caller
who asked for a shoutout on a line with announcements switched off got no
shoutout, and the operator reading the record should see that the ask existed.
The line says what happened, not whose fault it was.
"""

from __future__ import annotations

import re
import time

# A caller line that asks for something a TOOL would have to do. Written from
# the request vocabulary the tools already accept rather than invented: these
# are the phrasings in the scenario sets and in the archive.
#
# **Replayed over the archive 2026-08-14**, which is the first time this had
# been run against real callers rather than scenario text, and it was deaf
# where it mattered most: five of thirteen tool-shaped asks across 44 records.
# The misses were not exotic phrasings — they were the two most ordinary ways
# anybody asks a radio station for anything. "Got any Zeppelin?" (four lines,
# three separate calls) and "Can you put Wade on the radio?" (two) went
# unseen, while the shapes it did catch were all skips and DJ changes.
#
# That direction of failure is the dangerous one HERE, because of what this
# module is for: the plan says that if the archive does NOT fill up with
# dropped asks then the turn-by-turn shape is fine and the director question
# is closed. A detector that cannot hear a request would produce exactly that
# verdict by being deaf, and the stream would settle its central question on
# an instrument that was not listening.
ASKS_FOR_ACTION = re.compile(
    r"\b("
    r"play (?:me|us|something|that|it|this|some)|can you play|could you play|"
    # "put Wade on the radio" — the takeover ask, and the old `put (?:on|it
    # on|...)` list could not see a NAME in the middle of it.
    r"put (?:\w+ ){0,3}on\b|stick (?:on|that on)|"
    r"queue|request|line up|dig out|"
    # "Got any Zeppelin?" / "Do you have any Zeppelin?" — the plainest request
    # there is, and the most common caller line in the archive after hello.
    r"(?:got|have) any(?! idea| clue| thoughts)|"
    # "Give me something acoustic, surprise me." Guarded off the two ways this
    # phrasing means nothing at all.
    r"give (?:me|us) (?:something|some|a)(?! second| minute| moment| sec\b)|"
    r"surprise me|"
    # "Hey, can you tell me a story on air?" — the segment ask. Anchored to a
    # request verb rather than to "on air" alone, which would fire on "how long
    # have you been on air?" — ordinary chatter that owes nobody an action.
    r"(?:tell|do|run|give) (?:me|us|a|an|the)[^.?!]{0,40}?\bon (?:the )?air|"
    r"shout ?out|dedicate|dedication|say (?:hi|hello) to|send.*to the booth|"
    r"skip (?:this|it|that|the)|next (?:song|track|one)|"
    r"change the (?:dj|show)|switch (?:the )?(?:dj|show|to)|put someone else on|"
    r"never play|take (?:this|that) off|ban (?:this|that)|"
    r"heart (?:this|that)|like (?:this|that)"
    r")\b",
    re.IGNORECASE)

# Known and deliberately not guessed at: an ask that names a station SKILL
# ("can you do an escalation with the execs?") is invisible here, because the
# skill names come from the station and a regex holding a copy of them would
# be one more list to drift. If the archive shows this often, the shape to
# reach for is the live skill list, not more prose in this pattern.


class Asks:
    """One call's asks, and whether an action landed after each.

    "After" is the whole test, and it is why this holds timestamps rather than
    a count: an action that landed BEFORE the ask answered a different ask.
    """

    def __init__(self) -> None:
        # (when, what the caller said)
        self.asked: list[tuple[float, str]] = []

    def heard(self, text: str, at: float | None = None) -> None:
        if ASKS_FOR_ACTION.search(str(text or "")):
            self.asked.append((at or time.time(), str(text)[:200]))

    def settled(self, acted_at: list[float]) -> bool:
        """Positive evidence that what the caller asked for has landed.

        Deliberately NOT `not unanswered(...)`. The detector is lexical and
        therefore deaf in places — bare "Play <title>" was invisible to it
        until 2026-08-22 — and an empty `asked` list means "heard nothing",
        which is not the same as "nothing was owed". Reading it as the latter
        would silence the promise guard on every request it could not hear,
        turning a noisy false positive into a quiet false negative, which is
        the worse of the two: you can see a DJ answering its own question, but
        you cannot see a request that vanished.

        So: an obligation is settled only when an ask was HEARD and an action
        landed after it. No ask heard -> no information -> caller decides
        conservatively, which for the guard means behaving exactly as before.
        """
        return bool(self.asked) and not self.unanswered(acted_at)

    def unanswered(self, acted_at: list[float]) -> list[str]:
        """The asks with no action recorded after them.

        `acted_at` is when the ledger noted each successful action. Only the
        LAST ask needs care: a caller who asks twice and gets one action has
        had one answered, and which one is not knowable from here — so an
        action answers every ask before it. That is generous by construction,
        which is the right direction for a detector nobody has calibrated yet.
        """
        if not self.asked:
            return []
        latest = max(acted_at) if acted_at else 0.0
        return [what for when, what in self.asked if when > latest]


def attach_ask_watch(session, asks: Asks) -> None:
    """Listen to the caller, and only to the caller."""

    def _on_caller(ev) -> None:
        if not getattr(ev, "is_final", True):
            return
        text = str(getattr(ev, "transcript", "") or "").strip()
        if text:
            asks.heard(text)

    session.on("user_input_transcribed", _on_caller)
