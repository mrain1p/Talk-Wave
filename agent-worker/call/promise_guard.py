"""The DJ said it was about to do something — or that it already had. Make sure it did.

Measured on 2026-08-13, driving the real brain through the triage sweep: when
the DJ SPEAKS before acting, it usually does not act at all. Across four runs
on two models, of 33 turns that opened with "let me have a look" / "I'm
pulling that up now" / "hold on", **30 emitted no tool call**. The caller hears
the DJ promise to go and look, and then nothing happens — which is exactly the
shape of the calls the operator reported.

Since 0.10.138 this also catches the finished tense — "I've got that queued up for you" with
no tool behind it — which is the same failure told as an accomplished fact. Why that is the
worse half, and the call it was found on, is in `promises.py`.

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

from livekit.agents import AgentSession

from promises import PROBLEMS, unbacked, unbacked_semantic
from spoken_rules import check_after_failure, reads_as_a_refusal

from . import classify

log = logging.getLogger("callin.agent")

_NUDGE = {
    "promise": (
        "[You just told the caller you were about to do something, and no tool "
        "ran. If it needs one of your tools, call it NOW. Do not say another word "
        "to them first — they have already heard that line, and repeating it "
        "reads as a stutter. If nothing actually needed doing, stay silent.]"
    ),
    # Worded apart from the promise on purpose. The caller has ALREADY been told this is
    # done, so the repair is to make it true and say nothing — not to announce it twice.
    # The last sentence matters as much as the first: every tool in this codebase ends by
    # telling the DJ not to claim a failure worked, and a nudge that only said "call it now"
    # would leave a refused action still sitting behind a sentence that said it went through.
    # A THIRD kind, and it exists because the claim nudge made things worse
    # here. Measured 2026-08-15 on the refusals set: the claim nudge opens
    # "no tool ran — so it is NOT done", which is false when a tool ran and was
    # REFUSED, and it goes on to say "call it NOW" — so the DJ sent the same
    # request again, twice, against a tool result that says in as many words
    # "Do NOT send it again". Three turns to reach the honest sentence, two of
    # them forbidden retries the caller sat through.
    #
    # So this one never asks for a tool. There is nothing left to call: the
    # station has answered, the answer was no, and the only thing owed is
    # saying so.
    "refused": (
        "[The tool you just called came back REFUSED — the thing did not happen — and the "
        "line you just said tells the caller it did, or that it is on its way. Do not call "
        "that tool again: you already have the station's answer, and asking twice does not "
        "change it. Say plainly, in your own voice and in the world, that it did not go "
        "through and why, and offer what you CAN do instead. Do not apologise twice or "
        "explain the machinery — one honest sentence and move on.]"
    ),
    "claim": (
        "[You just told the caller that was already done, and no tool ran — so it is NOT "
        "done. If one of your tools does it, call it NOW, and say nothing to them first: "
        "they have already heard you say it, and hearing it again reads as a stutter. If it "
        "comes back refused, or you have no tool for it, tell them plainly that it did not "
        "go through — do not leave them believing it did.]"
    ),
}

# Moved to promises.py at 0.98.16 and imported, for the reason the patterns
# themselves were: the text line needs the identical wording in its own
# record and a second copy is a second thing to drift.
_PROBLEM = PROBLEMS


def attach_promise_guard(session: AgentSession, record=None, actions=None,
                         air=None, floor=None, asks=None) -> None:
    """One extra turn when the DJ promises an action and calls nothing.

    Fires at most once per caller turn. The state is per-turn rather than
    per-call because a long call legitimately contains many promises.

    `actions` is the call's ledger, and it is what tells a READ from an ACTION.
    Without it this guard cleared a claim on any tool at all — so a turn that
    searched the library and then told the caller "That one's in" with nothing
    queued was treated as a DJ behaving correctly. See `promises.unbacked`.
    Optional so the chat line and the tests can attach without one; absent, a
    claim is only ever cleared by an action nobody recorded, which is the safe
    direction (it nudges) rather than the silent one.
    """
    # Reset by the caller speaking; set by any tool running; consumed by the
    # nudge. Separate flags because the events can arrive in either order and a
    # missed reset would silence the guard for the rest of the call.
    state = {"tools_ran": False, "nudged": False, "acted_at": 0,
             "refused": False}

    def _ledger() -> int:
        return int(getattr(actions, "count", 0) or 0)

    def _on_caller(ev) -> None:
        if getattr(ev, "is_final", True):
            state["tools_ran"] = False
            state["nudged"] = False
            state["refused"] = False
            # The ledger is per CALL and this question is per TURN, so what
            # matters is whether it moved since the caller last spoke.
            state["acted_at"] = _ledger()

    session.on("user_input_transcribed", _on_caller)

    def _on_tools(ev) -> None:
        state["tools_ran"] = True
        # Whether any of them came back REFUSED. Without this the guard was
        # silenced by the very call that failed: "I'll get that in the queue
        # for you" is normally settled the moment the DJ reaches for a tool,
        # and the tool it reached for was the one that said no. Measured on the
        # refusals set, both judged rounds, 2026-08-14.
        #
        # `is_error` FIRST, and it is the half that was missing. The SDK marks
        # a tool that raised, `lifecycle._log_tools` has read that flag off
        # this very event since 0.10.146 to mark the record — and this guard,
        # reading the same event two handlers away, went on re-deriving the
        # same fact by pattern-matching the tool's prose. A wrapper whose
        # wording drifted, or one that failed in a way nobody had written a
        # phrase for, was a refusal this could not see: the caller is told it
        # landed, and the one mechanism built to catch exactly that stays
        # quiet. The structured answer was in hand the whole time.
        #
        # The prose match stays underneath rather than being replaced. They
        # catch different things and only one is authoritative about each: a
        # tool can return a perfectly successful call whose CONTENT is the
        # station saying no (a rate limit, a blocklist), which raises nothing
        # and is invisible to `is_error`.
        for out in (getattr(ev, "function_call_outputs", None) or []):
            if out is None:
                continue
            if (bool(getattr(out, "is_error", False))
                    or reads_as_a_refusal(getattr(out, "output", ""))):
                state["refused"] = True
                break

    session.on("function_tools_executed", _on_tools)

    def _on_said(ev) -> None:
        item = getattr(ev, "item", None)
        if getattr(item, "role", None) != "assistant":
            return
        text = str(getattr(item, "text_content", "") or "").strip()
        if not text:
            return
        if state["nudged"]:
            # The guard has spent its one nudge for this caller turn; what is
            # left is GRADING. A line after the refusal nudge that still says
            # it landed is exactly the repeat PROBLEMS["refused"] tells the
            # operator to watch for, and until 0.98.55 it lived only in the
            # transcript — the harness graded this fault on every drill run
            # (spoken_rules.check_after_failure) while live calls never did,
            # so the panel's "needs attention" count could not see it.
            if state["refused"] and record and check_after_failure(text):
                log.info("the claim survived the nudge — recording it: %s",
                         text[:80])
                record.problem(_PROBLEM["claims-again"])
            return
        # Whether the CALLER has an ask outstanding that a tool would have to
        # satisfy. The obligation belongs to their request, not to the DJ's
        # phrasing — see promises.unbacked. Absent (older wiring, the chat
        # line before it grew one), `owed` stays True and the guard behaves
        # exactly as it did.
        owed = True
        if asks is not None:
            owed = not asks.settled(getattr(actions, "taken_at", []) or [])
        # The structural facts, read NOW: they reset when the caller next
        # speaks, and the classifier arm judges the line a moment later.
        facts = dict(tools_ran=state["tools_ran"],
                     acted=_ledger() > state["acted_at"],
                     refused=state["refused"], owed=owed)

        # Spawned rather than awaited: this runs inside an event callback, and
        # generate_reply here would deadlock the session it is called from.
        from .background import spawn

        def _fire(kind: str) -> None:
            state["nudged"] = True
            # The caller is now waiting on US, and the idle clock has to know
            # it. It reads `working_until`, which only covers a tool in
            # flight — and the whole point of this branch is that no tool
            # ran. Without this the DJ says "I'm digging through the crates",
            # digs nothing, and asks "Still with me?" (2026-08-16, twice in
            # one evening).
            if actions is not None:
                actions.promise_made()
            log.info("%s with no tool call — nudging: %s", kind, text[:80])
            if record:
                record.problem(_PROBLEM[kind])
            spawn(_push(kind))

        async def _push(kind: str) -> None:
            # Wait for the DJ's OTHER turns, not just the station's. This is
            # one of only three injectors that can start while another is
            # already generating — see call/floor.py for which and why.
            if floor is None:
                await _generate(kind)
                return
            async with floor.take("the promise nudge") as mine:
                if mine:
                    await _generate(kind)

        async def _generate(kind: str) -> None:
            try:
                # Wait for the broadcast, like every other generated turn.
                # This one did not, and it is the likeliest of the three that
                # skip the hold to be heard: the DJ says "let me have a dig",
                # the station starts a link, and the nudge generates straight
                # over the top of it — the doubled voice the whole on-air guard
                # exists to prevent. `wait_until_clear` gives up at MAX_HOLD, so
                # a stuck gate delays the repair rather than losing it.
                if air is not None:
                    waited = await air.wait_until_clear()
                    if waited > 0.5:
                        log.info("held the %s nudge %.1fs for the on-air DJ",
                                 kind, waited)
                await session.generate_reply(user_input=_NUDGE[kind])
            except Exception as e:                             # noqa: BLE001
                log.debug("promise nudge failed (harmless): %s", e)

        # The classifier pilot (call/classify.py, NORTH STAR move 2): when
        # the lever is on and the session carries a model, the LABEL judges
        # the line and the lexicons become the degrade path. The label call
        # runs while the line is still being spoken to the caller, so nobody
        # waits on it; the verdict tree is unbacked's own, fed the label
        # instead of the patterns, so the two arms differ only in who read
        # the sentence.
        llm_call = (classify.llm_call_from(getattr(session, "llm", None))
                    if classify.enabled() else None)
        if llm_call is None:
            kind = unbacked(text, **facts)
            if kind:
                _fire(kind)
            return

        async def _judge() -> None:
            # THE LABEL VETOES, IT NEVER INITIATES — measured into this shape
            # on 2026-08-28. The first wiring let the label fire nudges the
            # lexicons couldn't hear, and the mimicry set caught what that
            # means under attack: an injected command makes the DJ narrate
            # compliance, the label correctly hears a deliverable promise,
            # and the nudge pushes the DJ to COMPLETE the attacker's ask
            # (1/9 and 5/9 with labels initiating, 4/9 and 8/9 without,
            # n=9 same night same image). The lexicons' deafness was
            # accidentally protective. So the lexicons decide whether a
            # nudge is owed at all, and the label may only stand it down —
            # which keeps the measured precision wins (false nudges halved
            # on every chatter-heavy set) and surrenders the extra catches
            # until nudging can tell a caller's ask from an attacker's.
            kind = unbacked(text, **facts)
            if not kind or state["nudged"]:
                return
            label = await classify.speech_act(text, llm_call)
            if label and not unbacked_semantic(label, **facts):
                log.info("speech-act label %r stood the %s nudge down",
                         label, kind)
                return
            if kind and not state["nudged"]:
                _fire(kind)

        spawn(_judge())

    session.on("conversation_item_added", _on_said)
