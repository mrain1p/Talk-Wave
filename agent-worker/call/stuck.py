"""Whether the caller has had to ask the same thing again, and one word in its ear.

The failure, from the operator's own chat on 2026-08-20: they asked what was
playing, and whether it had lyrics, **seven times in 157 seconds**. Every
answer was the same answer, and every answer was wrong. The record is
`chat-16e0dffa11e4`.

    caller: "what song is it"
    DJ:     "That's an instrumental playing right now"
    caller: "it does have lyrics can you search the web"
    DJ:     "I promise you, my ears aren't playing tricks on me"
    caller: "what about the current song"
    DJ:     "I just double-checked the feed directly, and it's confirmed"
    caller: "what song is this"
    caller: "what song is this"
    caller: "does Gala have lyrics?"
    caller: "what song are you playing right now and does it have lyrics?"

The track was "GALA" by XG. It has words. The caller could hear them.

**The DJ was not being stubborn — it was being obedient.** `/lyrics/current`
404s on this station, the read came back empty, and the tool reported that as
"an instrumental, or the station has none indexed" (fixed alongside this, in
`call/tools/music.py`). The conduct then did exactly what it is built to do:
relay the receipt, never invent. So the honesty machinery took a broken
endpoint and turned it into confident, repeated contradiction of the only
participant who actually knew.

Two things were missing, and neither is a wording problem:

  1. **Nothing counts.** No part of this codebase notices that a caller has
     asked the same thing three times. The DJ had no signal that its answer
     was not landing, so it had no reason to try a different route — and there
     WAS one: `subwave_now_playing` was available all call and never called
     once, while `subwave_current_lyrics` was called eleven times.
  2. **Nothing yields.** No rule anywhere treats a caller's contradiction as
     evidence. For anything the caller can directly perceive — what they are
     hearing right now — they are the better witness, and the prompt has no
     way to say so without also licensing the DJ to invent.

Shaped like `call/door.py` rather than `call/promise_guard.py`, and for the
same reason: the repair has to land BEFORE the words do. A nudge that fires
after the DJ has repeated itself can only add a fourth paragraph to a caller
already drowning in them. So it goes through `on_user_turn_completed`, the
SDK's own hook for editing the context before the model answers, and it costs
nothing on every turn where the caller was heard the first time.

**NOT YET MEASURED.** `door.py` earned its numbers and this has none, and that
file's own closing warning applies here: mechanism over prose is a hypothesis
to test per rule, not a principle to apply on faith. The measurement that
would settle it is a `scripted_call` set where the caller asks the same
question three times against a tool that returns nothing useful, graded on
whether the third answer differs from the first — with and without this hint.
Until that runs, this is an argued fix, not a proven one.
"""

from __future__ import annotations

import re

# Words that carry no signal about WHAT was asked. Kept deliberately short:
# every word dropped here is signal removed from an already-short utterance,
# and caller turns on this line run four or five words ("what song is this").
_NOISE = frozenset("""
a an the is it its this that these those there here am are was were be been
do does did doing done can could will would shall should may might must
i you he she we they me him her us them my your our their
of to in on at for with about from by as and or but if so then than
please thanks thank hey hi hello ok okay just really very
some any something anything more else
""".split())
# `some` and its neighbours earn their place: without them "play some jazz"
# and "play some rock" share two content words out of three and read as the
# same ask, which is the opposite of true — they are the caller asking for
# something different in the same sentence frame.

_WORD = re.compile(r"[a-z0-9']+")

# How much two asks must overlap to count as the same ask, as a fraction of
# the SHORTER one. Overlap rather than Jaccard because a caller who has been
# ignored gets more explicit, not less: the seventh ask on the 2026-08-20 chat
# was "what song are you playing right now and does it have lyrics?" against a
# first ask of "what song is it". Those are the same question asked twice, and
# Jaccard scores them 0.29 — the measure punished the caller for spelling it
# out.
SAME_ASK = 0.6

# ...but overlap alone calls any two utterances sharing one word the same ask
# ("what song is this" / "queue that song" both reduce to {song}), so a real
# repeat has to share at least two content words. This is what keeps "play
# some jazz" and "play some rock" apart — they share only "play".
SHARED_WORDS = 2

# The caller telling the DJ it is wrong about something they can perceive.
# Anchored on the contradiction itself rather than on hedges: "actually" alone
# is ordinary speech, and matching it would fire on half of every call.
CONTRADICTS = re.compile(
    r"\b(?:"
    r"it (?:does|do) have|it isn'?t|it is not|it'?s not|"
    r"no it (?:does|doesn'?t|isn'?t|is)|"
    r"yes it (?:does|is)|but it (?:does|is|has)|"
    r"that'?s (?:wrong|not right|not true|incorrect)|"
    r"you'?re wrong|you are wrong|not what i (?:asked|said)|"
    r"i can hear|i'?m listening to|i am listening to|"
    r"there are lyrics|it has (?:words|lyrics|vocals)"
    r")\b",
    re.IGNORECASE)


def content(text: str) -> frozenset:
    """The words of an utterance that say what it was about."""
    words = _WORD.findall(str(text or "").lower())
    return frozenset(w for w in words if w not in _NOISE and len(w) > 1)


def same_ask(a: str, b: str) -> bool:
    """Are these two caller turns asking for the same thing?

    Word overlap against the shorter utterance, floored at two shared words.
    Crude on purpose — the alternative is an embedding call on the caller's
    critical path, which buys accuracy this does not need at a latency cost
    the call cannot afford.
    """
    x, y = content(a), content(b)
    if not x or not y:
        return False
    shared = len(x & y)
    if shared < SHARED_WORDS:
        return False
    return shared / min(len(x), len(y)) >= SAME_ASK


def contradicts(text: str) -> bool:
    """Is the caller telling the DJ it has this wrong?"""
    return bool(CONTRADICTS.search(str(text or "")))


def _ordinal(n: int) -> str:
    """3rd, not 3th. The note is read by a model that is about to write in
    the DJ's voice, and a malformed word in the instruction is a malformed
    word offered back to it."""
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _repeat_hint(times: int) -> str:
    # Second time is a nudge; third is a different instruction, because by
    # then restating has already been tried and has already failed.
    if times <= 2:
        return (
            "[Note to you, not from the caller: they have now asked you this "
            "twice. Your last answer did not land. Do NOT say it again in "
            "different words — take a DIFFERENT route: a different tool, a "
            "different read, or a plain admission of what you cannot find "
            "out. If a tool has been coming back empty, say that out loud "
            "rather than repeating what it told you as though it were "
            "settled.]"
        )
    return (
        f"[Note to you, not from the caller: this is the {_ordinal(times)} time they "
        "have asked you the same thing, which means every answer you have "
        "given has failed. STOP repeating it. Something in your information "
        "is wrong or missing — say so plainly, in your own voice, and name "
        "what you actually cannot determine. If there is a tool you have not "
        "tried, try it now. Do not defend your previous answers and do not "
        "tell them you have double-checked: from where they are sitting you "
        "have been wrong every time so far.]"
    )


_CONTRADICTED = (
    "[Note to you, not from the caller: they have just told you that you have "
    "this wrong. They are the one listening to the record; on anything they "
    "can hear for themselves, they are the better witness and you are working "
    "from whatever the station handed you — which can be missing or stale. Do "
    "NOT insist, do not tell them you have checked and confirmed it, and do "
    "not say your information is definitive. Take their word, say your "
    "information doesn't show it, and go and look again by another route if "
    "you have one.]"
)


class Stuck:
    """One conversation's memory of what the caller has already had to ask.

    Deliberately not a general conversation-state object — it answers one
    question per turn and keeps only what that needs. The history is capped
    because a long call legitimately circles back, and an ask from twenty
    minutes ago is a new ask, not a repeat.
    """

    #: How far back a repeat still counts as a repeat.
    WINDOW = 12

    def __init__(self) -> None:
        self.asks: list[str] = []
        # What the operator reads back afterwards. A conversation where the
        # caller had to ask four times is a different report from one where
        # they asked twice, and neither is visible anywhere today.
        self.repeats = 0
        self.contradictions = 0

    def hint_for(self, caller_text: str) -> str:
        """The note to put in front of the model, or "" for nothing.

        Records the turn as it goes: the check and the remembering are the
        same event, and splitting them is how a caller's line gets counted
        twice or not at all.
        """
        said = str(caller_text or "").strip()
        if not said:
            return ""
        times = 1 + sum(1 for prev in self.asks if same_ask(prev, said))
        self.asks.append(said)
        del self.asks[:-self.WINDOW]

        notes = []
        if times > 1:
            self.repeats += 1
            notes.append(_repeat_hint(times))
        if contradicts(said):
            self.contradictions += 1
            notes.append(_CONTRADICTED)
        return "\n".join(notes)
