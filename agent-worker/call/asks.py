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
#
# **Replayed again 2026-08-23**, 97 records, after "Play diciembre first" went
# unheard on a live call (the pattern wanted a pronoun after "play"). That
# replay found the deafness was general — 29 caller lines the pattern could
# not hear, and none of them exotic: "spin me a mix all 90s rock", "cue the
# word album" (STT for queue), "cancel that track", "Can I get Rosie?", "have
# you got the lyrics for this song?", "Create me a mix from the artist mina",
# "What Eminem albums do you have?", "remove all the eminiem songs from the
# queu?" (STT again). Every branch below exists because a real caller said it;
# the replay script keeps LOST at zero, so nothing the old pattern heard has
# gone unheard. Known and accepted misses: bare follow-up fragments ("Italian
# songs", "a mix") that only context can read, and informational questions
# ("what song is this?") that are answered in words and out of scope here.
ASKS_FOR_ACTION = re.compile(
    r"\b("
    # Bare imperative "play <anything>": "Play diciembre first", "play the
    # marshal mathers lp", "Let her play Chinese music". The exclusions are the
    # figurative particles ("play along") and "play next/here/there", which
    # appear in chatter about the programme rather than in requests. \S+ not
    # \S: a single-char match strands the closing \b inside the next word.
    r"play (?!next\b|here\b|there\b|along\b|around\b|nice\b|fair\b|out\b"
    r"|off\b|with\b)\S+|"
    r"spin (?:me|us|something|some|a|that|it|this)|"
    # "i want to hear wade" / "how about some jazz" — the request phrased as
    # appetite rather than imperative.
    r"(?:want|wanna|like|love|d love) to hear|hear (?:some|a little|a bit of)|"
    r"let'?s hear|"
    r"(?:how|what) about (?:some|a little|a bit of)|"
    r"(?:can|could) (?:i|we|you) get\b|"
    # The mix asks, and the album-shelf ask ("What Eminem albums do you
    # have?") — both answered by tools, both deaf spots on the 08-23 replay.
    r"(?:create|make|build) (?:me |us )?a (?:mix|playlist)|mix of\b|a mix from|"
    r"(?:what|which) [^.?!]{0,24}?albums?\b|albums? (?:do|have) you\b|"
    # Lyrics asks route to a tool, and the failure mode when the tool is
    # missing is the DJ inventing an answer — the eleven-times call.
    r"(?:the|have|any) lyrics|lyrics (?:for|to|of)|"
    r"give (?:this|that) (?:track|song|tune|one|a spin)|"
    r"search\b|look (?:\w+ )?up\b|look for|"
    # Queue edits. "cue" and "queu" are what STT actually makes of "queue".
    r"cancel\b|cue\b|remove (?:all |the |that |this |it |every)|"
    r"recommend|"
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
    r"skip (?:this|it|that|the|current|next|everything|song|track)|"
    r"next (?:song|track|one)|"
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
        until the 2026-08-23 replay, and follow-up fragments ("Italian
        songs") still are — and an empty `asked` list means "heard nothing",
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


#: The word in the DJ's ear when a caller's ask has outlived the turn it
#: arrived in. Same delivery and same honesty rule as the door and arc
#: hints: it names what to DO — finish the task or say where it stands —
#: because "you left something hanging" with no direction is just nagging.
COMEBACK_HINT = (
    "[Note to you, not from the caller: they asked for \"{want}\" and it "
    "has not happened yet. Come back to it NOW — do the task, or say "
    "honestly where it stands — without making them ask again.]"
)


class OpenAskComeback:
    """The director's second slice: the ask that survives an interruption.

    The arc taught the turn loop one across-turn fact — the call is over.
    This is the next one: THE CALLER IS STILL OWED SOMETHING. Today an open
    ask survives in the ledger, but nothing drives the DJ back to it after
    a hold, a segment, or a tangent; on the flow set's scenario the caller
    has to re-ask, and on real calls they do too. This fires one steer when
    an ask has outlived the caller turn it arrived in with no action landed
    — once per ask, never while the goodbyes are done, and it stands down
    the moment an action answers.

    Turn-counted, not clock-timed, on purpose: the drill's turns take
    however long the model takes, so a wall-clock threshold would make the
    measurement flaky while changing nothing for real calls.
    """

    def __init__(self, asks: Asks) -> None:
        self.asks = asks
        self._turns_open = 0
        self._hinted: set[str] = set()
        # Read by the record, like door.corrections and the arc's: how often
        # the DJ had to be steered back to what the caller came for.
        self.corrections = 0

    def hint_for(self, caller_text: str, acted_at) -> str:
        open_asks = self.asks.unanswered(list(acted_at or []))
        if not open_asks:
            self._turns_open = 0
            return ""
        self._turns_open += 1
        want = str(open_asks[-1])[:120]
        # Turn one is the ask itself — the model is presumably acting on it
        # right now, and steering it there would be noise. Turn two with
        # nothing landed is the caller waiting.
        if self._turns_open < 2 or want in self._hinted:
            return ""
        self._hinted.add(want)
        self.corrections += 1
        return COMEBACK_HINT.format(want=want)


def attach_ask_watch(session, asks: Asks) -> None:
    """Listen to the caller, and only to the caller. The final/non-empty
    caller-line unwrap lives once in watch.on_caller_line now."""
    from . import watch

    watch.on_caller_line(session, asks.heard)
