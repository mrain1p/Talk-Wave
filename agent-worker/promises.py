"""What the DJ just said about an action, and whether it owes a receipt.

Two shapes of the same failure, and both end with the caller believing something happened
that did not:

**The promise.** "Let me have a dig" and then nothing. Measured 2026-08-13 across four runs
on two models: of 33 turns that opened with a promise, 30 emitted no tool call. The cause is
our own conduct rule — say what you're doing before you go quiet to do it — which is right
about dead air and wrong about these models, where narration and tool-calling compete for one
turn and narration wins.

**The claim.** "I've got that queued up for you" and then nothing. This one is worse. A
promise with no tool behind it is a dead line; a CLAIM with no tool behind it is a lie the
caller has no way to catch, and on the on-air tools it is a lie about what the whole station
is doing. Observed 2026-08-14 (record ...084215): the caller asked to change the DJ, the
model pinned THE OVERLOOK, the caller said "to duke" — and the DJ answered "I've got that
queued up for you, Duke's show is on its way" with no tool call at all. Cliff stayed on air,
the caller was told Duke was coming, and nothing anywhere disagreed. It read as a *matching*
bug and was not: `_match_show("duke")` resolves Duke Sterling to The Alibi Room correctly.
The guard simply did not recognise a finished-tense sentence as something owing a receipt.

Both surfaces match against OUR OWN words rather than guessing at the model: the promise
openers are the ones `conduct.running_the_call` asks for by name, and the completion
vocabulary is the vocabulary the tools themselves hand back ("it's pinned", "added to the
queue", "on its way"). That is what keeps this from firing on ordinary conversation.

**One copy, because two drifted.** The phone and the text line each carried their own
regex, and by 0.10.137 the phone's had gained four phrasings the chat's never got — so the
same sentence was guarded on one surface and waved through on the other. The patterns live
here now and both import them.
"""

from __future__ import annotations

import re

# Future tense: the DJ says it is ABOUT to act.
PROMISES_ACTION = re.compile(
    r"\b(let me|lemme|i'?ll\b|i am going to|i'?m going to|i'?m gonna|"
    r"hold on|hang on|one sec|one moment|give me a|on it\b|"
    r"checking|looking|digging|sending|queueing|queuing|getting that|"
    r"pulling up|have a look|dig out|dig through)\b",
    re.IGNORECASE)

# Finished tense: the DJ says it HAS acted. Deliberately two-part — a completion marker AND
# a station action, within one sentence of each other. Either half alone is ordinary talk
# ("got it", "that track is coming up next" read off the schedule); together they are a
# receipt. Measured against all 155 DJ lines in the live archive on 2026-08-14: three lines
# match, two of them had genuinely run a tool, and the one that had not is the Duke call
# above. No other line in the corpus fires.
_DONE = (r"(?:i'?ve (?:got|just)|i have got|got it|that'?s (?:done|sorted|set)|"
         r"all set|sorted|done|just (?:pinned|queued|sent|put|switched|handed)|"
         r"is made|is set|consider it|you'?re all set|i'?ve gone ahead)")
_ACT = (r"(?:queued|queue|lined up|line-?up|pinned|on its way|on the way|"
        r"taking over|take over|takes over|coming up next|switch(?:ed)?|"
        r"handed over|handing (?:it |the )?over|added|sent (?:down|it|that)|"
        r"put (?:it |that |them )?on|set up|cued|on air|on the air|"
        r"in the queue|to the booth)")
CLAIMS_DONE = re.compile(
    rf"\b{_DONE}\b[^.!?\n]{{0,80}}\b{_ACT}\b|"
    rf"\b{_ACT}\b[^.!?\n]{{0,60}}\b{_DONE}\b",
    re.IGNORECASE)


def unbacked(text: str) -> str:
    """Classify a line the DJ said with no tool call behind it.

    Returns "promise", "claim", or "" for anything that owes nothing. A line carrying both
    ("got it, I'll queue that up") is a PROMISE: the softer nudge still gets the tool called,
    and telling a model it claimed something it only offered to do invites an apology the
    caller does not need.
    """
    text = str(text or "")
    if PROMISES_ACTION.search(text):
        return "promise"
    if CLAIMS_DONE.search(text):
        return "claim"
    return ""
