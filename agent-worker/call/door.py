"""Whether the DJ just showed the caller the door, and one word in its ear.

The failure, in the operator's own words: a caller who asked for one song was
shown the door three times on the way out. `conduct.CLOSING` has four
paragraphs against it and they do not hold — measured 2026-08-14 with
SCENARIO_SET=closing, one round in three, after two rounds of rewording the
section itself.

It is real on live calls, not a harness artefact. Eight of the 162 DJ lines in
the archive end by asking whether the caller wants more, and not one of those
callers had said they were finished:

    caller: "I'll wait while it goes out."
    DJ:     "...Anything else you want to dig up while we're waiting?"
    caller: "He has had a really rough week."
    DJ:     "...Anything else you'd like me to add for him?"

One call does it three times while the caller is talking about a friend having
a rough week. **The harm is the repetition**, not one polite question, and that
is what this fixes.

Why it is not shaped like `call/promise_guard.py`, which is the other guard of
this kind: that one's repair is SILENT — call the tool, say nothing — and it can
therefore run after the utterance has committed. A door question cannot be
unsaid. Firing after the fact could only add a second line, which is worse, and
stripping the question before the audio would leave the line ending flat, which
is the OTHER failure in the same section of the conduct.

So the correction lands on the NEXT turn, through `on_user_turn_completed` —
the SDK's own hook for editing the context before the model answers. It cannot
fix the line that just went out, and it does not try to: it stops the second and
third, which is the part a caller actually feels.

It costs nothing on every turn where the DJ behaved, which standing prose in the
prompt cannot say. That is the whole argument for moving a rule out of the
prompt and into a mechanism: the prompt pays on every turn of every call
forever, and this pays only when it is needed.

**Measured, against its own absence** — `SCENARIO_SET=closing`,
`SCENARIO='door is not held'`, four rounds each, one variable:

    DOOR=off   1/4     the guard disabled
    DOOR=on    3/3     the guard on, and it fired on every judged round

Same scenario, same model, same prompt. The trigger is a scripted DJ line
(`@dj …`) rather than one the model has to happen to produce: the first attempt
at this measurement scored 3/4 with the guard never firing once, because the DJ
did not hold the door open in any of those four rounds, and a result that
depends on the fault occurring by luck is not a result. Without the correction
the DJ also reached for `end_call` — it did not merely hold the door open, it
showed the caller out.

**The prose it replaces was measured and KEPT**, which is not what was expected.
`conduct.CLOSING_DOOR` was split out so it could be dropped by name, and the
closing set run with the guard on in both arms:

    with the prose      1/3 first occurrence,  3/3 repetition   (30,438 chars)
    without the prose   0/3 first occurrence,  3/3 repetition   (29,355 chars)

So the guard fully carries the REPETITION on its own — that is the harm the
operator reported and the archive shows — but the paragraphs still buy the
first occurrence, and 1,110 characters is what they cost. The mechanism did not
make the prose free; it made the prose measurable, and the measurement said
keep it. "Mechanism over prose" is a hypothesis to test per rule, not a
principle to apply on faith.
"""

from __future__ import annotations

import re

# A turn that ENDS by asking whether they want more. Anchored to the tail
# because the same words mid-line are ordinary conversation — "anything else on
# your mind while that spins up" in the middle of a sentence is not the failure;
# ending on it is.
#
# Broad on purpose. The first fix removed the literal "anything else?" from the
# prompt and the model came back with "Anything else you're looking to hear…"
# and "you want me to spin up something else, or are you good for now?". The
# failure is the SHAPE of the ending, not one wording of it, so matching one
# wording just teaches the next rewrite to dodge the check.
HOLDS_THE_DOOR = re.compile(
    r"(anything else|something else (?:you|i can)|anything more|"
    r"you all set|are you (?:good|all set|sorted)|what else can i|"
    r"else you'?re (?:looking|after)|anything you need)"
    r"[^.!?]*\?\s*$",
    re.IGNORECASE)

# The caller saying they are finished. When they have, the question is not the
# door being held open — it is the LAST thing you say in a call, which the
# conduct explicitly allows once, at the end.
SIGNALS_DONE = re.compile(
    r"\b(that'?s (?:it|all|everything|the lot)|nothing else|no,? that'?s|"
    r"i'?m (?:good|done|all set)|all good|"
    r"bye\b|goodbye|see you|catch you|take care|"
    r"cheers,? (?:thanks|mate)|thanks,? bye)\b",
    re.IGNORECASE)

HINT = (
    "[Note to you, not from the caller: your last line ended by asking whether "
    "they wanted anything else, and they had not said they were finished — so "
    "it read as showing them the door after they had just asked you for "
    "something. Do not end this turn that way again. Say what you have to say "
    "and leave something REAL in the air — what is coming up, the record you "
    "would put on next, something in what they just told you — or simply let "
    "the full stop land. Keep your own voice; just don't ask them to order "
    "again.]"
)


def holds_the_door(text: str) -> bool:
    """Did this line end by asking the caller whether they want more?"""
    # Only the tail is examined: a line can be long, and what makes it the
    # door-holding move is how it FINISHES.
    return bool(HOLDS_THE_DOOR.search(str(text or "").strip()[-160:]))


def signals_done(text: str) -> bool:
    return bool(SIGNALS_DONE.search(str(text or "")))


class Door:
    """One call's memory of whether the last DJ line held the door open.

    Deliberately tiny and deliberately not a general "conversation state"
    object. It answers one question, for one turn, and forgets — a call
    legitimately contains many endings and each is judged on its own.
    """

    def __init__(self) -> None:
        self.held = False
        # How many times it has been corrected on this call. Read by the record
        # rather than by the logic: a call that needed telling three times is a
        # different report from one that needed telling once, and neither shows
        # up anywhere today.
        self.corrections = 0

    def dj_said(self, text: str) -> None:
        self.held = holds_the_door(text)

    def hint_for(self, caller_text: str) -> str:
        """The note to put in front of the model, or "" for nothing.

        Consumed either way: the question was asked and answered, and holding
        the flag would correct a turn that is two turns downstream of it.
        """
        held, self.held = self.held, False
        if not held or signals_done(caller_text):
            return ""
        self.corrections += 1
        return HINT


def attach_door_watch(session, door: Door) -> None:
    """Watch what the DJ says so the next turn knows how the last one ended."""

    def _on_said(ev) -> None:
        item = getattr(ev, "item", None)
        if getattr(item, "role", None) != "assistant":
            return
        text = str(getattr(item, "text_content", "") or "").strip()
        if text:
            door.dj_said(text)

    session.on("conversation_item_added", _on_said)
