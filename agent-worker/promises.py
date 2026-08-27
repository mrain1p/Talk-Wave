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

# The DJ assuring the caller of an OUTCOME that has not happened. Distinct
# from CLAIMS_DONE, which is past tense ("that's in", "sent it off"): this is
# the confident future — the record will air, the shoutout will go out — and
# it is only ever a fault when the thing it promises has already been refused.
# On every other turn of a call it is the honest receipt, because the DJ really
# does queue records and "it's about six minutes out" is what the caller wants
# to hear. Shared with `spoken_rules.CUE_FRAMING`, which grades the same shape
# from the harness side, so the guard and the grader cannot drift into
# disagreeing about what a claim looks like.
from spoken_rules import CUE_FRAMING as _ASSURED_OUTCOME

# Future tense: the DJ says it is ABOUT to act.
PROMISES_ACTION = re.compile(
    r"\b(let me|lemme|i'?ll\b|i am going to|i'?m going to|i'?m gonna|"
    r"hold on|hang on|one sec|one moment|give me a|on it\b|"
    r"checking|looking|digging|sending|queueing|queuing|getting that|"
    r"pulling up|have a look|dig out|dig through|"
    # The present participle of an action already under way, which reads to a
    # caller exactly like the finished tense and owes the same receipt.
    # "No problem. Taking that off for you now." — said with no tool call at
    # all, on the drill's cancel scenario, 2026-08-14. Narrow on purpose: the
    # object is required, so "taking requests tonight" and "putting on a good
    # show" are untouched. Checked against all 162 DJ lines in the live archive
    # the same day: it matches none of them, so it widens what is caught
    # without widening what is interrupted.
    r"(?:taking|pulling|putting|queuing|cueing) (?:it|that|them|those) "
    r"(?:off|out|back|on|in|up)"
    r")\b",
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

# The DELIVERABLE half of the promise vocabulary — _ACT plus the progressive
# forms a promise naturally wears ("I'm queueing them both now"), which the
# past-tense _ACT words can't reach across a word boundary. A promise that
# names the deliverable is fulfilled only by the action happening; a promise
# to go and look ("let me have a dig") is fulfilled by the dig itself.
PROMISES_DELIVERABLE = re.compile(
    rf"\b(?:queu\w+|(?:into|in) the rotation|{_ACT})\b", re.IGNORECASE)

# What the RECORD says when one of these fires, for the operator reading a bad
# conversation back. Here for the same reason the patterns are — the phone
# wrote these into `problems` from the start and the text line, which declared
# the list and drained it into the record, never appended to it once. Every
# chat ever recorded therefore shipped `problems: []`, and the panel's "needs
# attention" count could not see a text conversation at all. Observed
# 2026-08-20 on a chat that promised a request it never sent, invented a
# library limitation and skipped the caller's own record: clean sheet.
PROBLEMS = {
    "promise": (
        "The DJ told the caller it was about to do something and ran no tool. It was given "
        "one more turn to actually make the call; if the next line still promises without a "
        "receipt, the model is narrating actions instead of taking them — check the LLM "
        "setting against one with proven tool routing."
    ),
    "refused": (
        "A tool came back REFUSED and the DJ told the caller it had happened anyway, or was "
        "on its way. This is the failure the caller cannot catch — they hang up believing a "
        "record is coming that nobody queued. It was given one more turn to own it. Repeats "
        "here mean the honesty rules in the prompt are not reaching this model."
    ),
    # Not a promise-guard kind — nothing here classifies it, and `unbacked`
    # never returns it. It lives in this dict because this is the vocabulary
    # the record and the panel's "needs attention" count already read, and a
    # conversation where the caller had to ask four times is exactly what that
    # count is for. Written by call/stuck.py.
    "stuck": (
        "The caller had to ask the same thing again. Every answer before it failed, and "
        "until 0.98.22 nothing anywhere noticed — so the DJ had no signal to change route "
        "and kept restating. Read the transcript for what it kept saying: if a tool was "
        "coming back empty, the fault is the tool's message, not the model's manners. The "
        "2026-08-20 chat that prompted this asked seven times and was told the wrong thing "
        "every time, because a 404 was being reported as a fact about the record."
    ),
    "claim": (
        "The DJ told the caller something had ALREADY been done and ran no tool, so it had "
        "not been. It was given one more turn to make the claim true. This is the shape that "
        "does not announce itself on the call — the caller is told it worked and hangs up "
        "believing it — so if it repeats, treat the model's tool routing as unfit rather "
        "than as a rough edge."
    ),
    # The repeat the "refused" entry above says to watch for, graded rather
    # than left to whoever reads the transcript. Written when the DJ, GIVEN
    # the extra turn after a refusal, claims the thing landed anyway — judged
    # by spoken_rules.check_after_failure, the same check the drill grades
    # with, so the panel's "needs attention" count and the harness agree
    # about this fault instead of only the harness seeing it.
    "claims-again": (
        "Given the extra turn after a refusal, the DJ STILL told the caller it landed. The "
        "nudge has failed and the honesty rules are not reaching this model — treat its tool "
        "conduct as unfit, not as a rough edge, and check the LLM setting against one with "
        "proven tool routing."
    ),
}


def unbacked(text: str, *, tools_ran: bool = False, acted: bool = False,
             refused: bool = False, owed: bool = True) -> str:
    """Classify a line the DJ said, against what actually ran behind it.

    Returns "promise", "claim", or "" for anything that owes nothing. A line carrying both
    ("got it, I'll queue that up") is a PROMISE: the softer nudge still gets the tool called,
    and telling a model it claimed something it only offered to do invites an apology the
    caller does not need.

    **A read does not settle a claim, and this is the whole reason the two flags are
    separate.** A promise is about dead air — "let me have a dig" is honest the moment the DJ
    reaches for ANY tool, because something is now happening. A claim is about truth: "that
    one's in" is true only if something was PUT in, and a search is not that. Both surfaces
    got this wrong, in opposite directions, and each bug was invisible from the other side:

      * the phone cleared a claim on any tool at all, so a turn that searched and then said
        "That one's in. Got it lined up for you." with nothing queued sailed through. Caught
        2026-08-14 on the drill's `a cancel that comes too late` scenario, one round in three,
        on the model the operator runs. It is the Duke call with a search in front of it, and
        the caller has no way to notice.
      * the text line cleared a claim on nothing at all, so a queue that really HAD run got
        told "no tool ran — so it is NOT done" and spent a turn apologising for a job it had
        done correctly.

    `acted` is the ledger's answer, not a list of tool names kept here: `CallActions.note()`
    fires exactly when a station action SUCCEEDED, which is also the right answer for a tool
    that ran and was refused — a refused queue leaves the claim false, and the nudge for that
    case tells the DJ to own it.
    """
    text = str(text or "")
    # A tool that ran and was REFUSED backs nothing, and this case slipped
    # both other flags. Measured 2026-08-14, refusals set, two judged rounds
    # out of two: `subwave_request_song` came back "The station couldn't take
    # that request", and the DJ answered "it'll head out onto the airwaves just
    # as soon as that track clears" and "I've got it locked in to follow".
    #
    # The first line matched neither pattern — it is an assured FUTURE, not a
    # past-tense "that's done", which is what CLAIMS_DONE was written for. The
    # second matched PROMISES_ACTION and was then cleared by `tools_ran`,
    # because a promise is normally settled the moment the DJ reaches for any
    # tool. That reasoning is right about dead air and wrong here: the tool it
    # reached for is the one that said no, so the very call that should have
    # made the line honest is what silenced the guard.
    #
    # After a refusal both shapes are the same failure and take the CLAIM
    # nudge, not the promise one — "call it NOW" would be telling the DJ to
    # retry a refusal, which every tool result explicitly forbids. The claim
    # nudge is the one that says: own it, tell them it did not go through.
    if refused and not acted and (
            _ASSURED_OUTCOME.search(text)
            or CLAIMS_DONE.search(text)
            or PROMISES_ACTION.search(text)):
        return "refused"
    if PROMISES_ACTION.search(text):
        if tools_ran:
            # A promise is about dead air, and any tool settles THAT half —
            # "let me have a dig" is fulfilled by the dig. But the Casino
            # call (2026-08-26) showed the other half: searches ran, the DJ
            # said "I'm queueing them both for you right now", no queue ever
            # fired, and the caller hung up believing it had. A promise that
            # names the DELIVERABLE is fulfilled only by the action, so reads
            # alone leave it standing while the caller's ask is open. A line
            # that ENDS as a question stays cleared either way: that is
            # confirm mode's own consent shape, and the 2026-08-22 lesson is
            # that nudging a question makes the DJ answer itself.
            if (owed and not acted and not text.rstrip().endswith("?")
                    and PROMISES_DELIVERABLE.search(text)):
                return "promise"
            return ""
        # AN OBLIGATION IS CREATED BY THE CALLER ASKING, NOT BY THE DJ'S
        # VOCABULARY. This pattern reads WORDS to infer a speech act, and
        # words cannot carry that reliably: on 2026-08-22 it fired on
        # "…or were you looking for something else from the list?" — the
        # gerund `looking`, in a question about what the CALLER wanted, in a
        # line whose only other content was asking permission. The nudge told
        # the DJ to act, so it queued the record it had just asked about and
        # answered its own question, which is `confirm_requests` defeated by
        # a guard that could not tell a question from a promise.
        #
        # No pattern list fixes that — "looking" is honestly ambiguous and
        # always will be. So the trigger moves off the prose: `owed` is
        # whether the CALLER has an ask outstanding that a tool would have to
        # satisfy (call/asks.py, which already scopes that correctly and was
        # built as evidence for exactly this question). No ask, nothing owed,
        # nothing to nudge — whatever words the DJ used.
        #
        # Only the PROMISE verdict is gated. A false "that's done" is a lie
        # whether or not anybody asked, and `refused` is structural already.
        return "promise" if owed else ""
    if CLAIMS_DONE.search(text):
        return "" if acted else "claim"
    return ""
