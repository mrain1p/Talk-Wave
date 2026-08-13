"""The DJ said it was about to do something. Make sure it does.

Measured on 2026-08-13, driving the real brain through the triage sweep: when
the DJ SPEAKS before acting, it usually does not act at all. Across four runs
on two models, of 33 turns that opened with "let me have a look" / "I'm
pulling that up now" / "hold on", **30 emitted no tool call**. The caller hears
the DJ promise to go and look, and then nothing happens — which is exactly the
shape of the calls the operator reported.

The cause is our own prompt. `conduct.running_the_call` says:

    Say what you're doing BEFORE you go quiet to do it ("let me have a dig"),
    so a pause sounds like a DJ working, not a dead line.

That rule exists for a good reason — silence on a phone line reads as a
dropped call — but on these models narration and tool-calling compete for the
same turn, and narration wins. The rule is not wrong; it just needs the second
half, which is this module.

The TEXT line has had this guard since 0.10.65 (`chat/session.py`, added from
an operator report the same week: "sent down to the booth" ended the turn and
nothing was ever sent). The voice line — the primary surface — never got it.
This is that fix, in the shape a LiveKit AgentSession allows: a call can't
re-run its turn loop the way the hand-rolled chat loop can, so instead the
model is given one more turn with instructions to make the call and say
nothing new.
"""

from __future__ import annotations

import logging
import re

from livekit.agents import AgentSession

log = logging.getLogger("callin.agent")

# The openers the conduct asks for by name, plus the plain futures they come
# out as. Deliberately the same list the chat loop matches on, plus the three
# phrasings the sweep caught that it missed ("pulling up", "have a look",
# "dig out") — matching OUR OWN instruction rather than guessing at the model
# is what keeps this from firing on ordinary conversation.
PROMISES_ACTION = re.compile(
    r"\b(let me|lemme|i'?ll\b|i am going to|i'?m going to|i'?m gonna|"
    r"hold on|hang on|one sec|one moment|give me a|on it\b|"
    r"checking|looking|digging|sending|queueing|queuing|getting that|"
    r"pulling up|have a look|dig out|dig through)\b",
    re.IGNORECASE)

_NUDGE = (
    "[You just told the caller you were about to do something, and no tool "
    "ran. If it needs one of your tools, call it NOW. Do not say another word "
    "to them first — they have already heard that line, and repeating it "
    "reads as a stutter. If nothing actually needed doing, stay silent.]"
)


def attach_promise_guard(session: AgentSession, record=None) -> None:
    """One extra turn when the DJ promises an action and calls nothing.

    Fires at most once per caller turn, and only when THAT turn ran no tools —
    a DJ that said "let me look" and looked is behaving correctly and must not
    be interrupted. The state is per-turn rather than per-call because a long
    call legitimately contains many promises.
    """
    # Reset by the caller speaking; set by any tool running; consumed by the
    # nudge. Three flags rather than one because the events can arrive in
    # either order and a missed reset would silence the guard for the rest of
    # the call.
    state = {"tools_ran": False, "nudged": False}

    def _on_caller(ev) -> None:
        if getattr(ev, "is_final", True):
            state["tools_ran"] = False
            state["nudged"] = False

    session.on("user_input_transcribed", _on_caller)

    def _on_tools(ev) -> None:
        state["tools_ran"] = True

    session.on("function_tools_executed", _on_tools)

    def _on_said(ev) -> None:
        item = getattr(ev, "item", None)
        if getattr(item, "role", None) != "assistant":
            return
        text = str(getattr(item, "text_content", "") or "").strip()
        if not text or state["tools_ran"] or state["nudged"]:
            return
        if not PROMISES_ACTION.search(text):
            return
        state["nudged"] = True
        log.info("promise with no tool call — nudging: %s", text[:80])
        if record:
            record.problem(
                "The DJ told the caller it was about to do something and ran "
                "no tool. It was given one more turn to actually make the "
                "call; if the next line still promises without a receipt, the "
                "model is narrating actions instead of taking them — check "
                "the LLM setting against one with proven tool routing."
            )

        async def _push() -> None:
            try:
                await session.generate_reply(user_input=_NUDGE)
            except Exception as e:                             # noqa: BLE001
                log.debug("promise nudge failed (harmless): %s", e)

        # Spawned rather than awaited: this runs inside an event callback, and
        # generate_reply here would deadlock the session it is called from.
        from .background import spawn

        spawn(_push())

    session.on("conversation_item_added", _on_said)
