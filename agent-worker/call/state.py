"""One call's conversation state, held in one place.

Move 1 of the conversation-engine convergence (MASTER-PLAN "NORTH STAR",
agreed 2026-08-27): the per-call guards — door, stuck, withheld, arc — grew
one at a time, each bolted onto the same two seams (the DJ's own lines on
the way out, the caller's turn on the way in), and by the eighth the reply
path was consulting four objects in a hand-maintained order and the DJ-line
event had three separate watchers doing the same read. This object is that
order, written down once.

Deliberately a HOLDER, not a merger: each guard keeps its own file, its own
tests and its own reasoning — door.py's argument for staying tiny still
holds for every one of them. What moves here is only what was duplicated
around them: the build, the fan-out of the DJ's lines, and the reply-path
consultation with its order and its log lines. Zero behavior change, by
construction and by the suite.

The order below is the order the reply path has always used — stuck, then
withheld, then door, then arc — and it is load-bearing: the stuck note must
land before the door note so a repeated ask is answered before the line's
ending is judged, and the arc speaks last because "the call is over"
outranks how any single line ended.
"""

from __future__ import annotations


class ConversationState:
    """The guards one call runs, their order, and their two seams."""

    def __init__(self, door=None, stuck=None, withheld=None, arc=None,
                 asks=None, actions=None, landed=None) -> None:
        self.door = door
        self.stuck = stuck
        self.withheld = withheld
        self.arc = arc
        # The post-landing wind-down (call/landed.py) — None unless the
        # operator's closing_nudge switch is on; the closing scenario set is
        # what earns it a default.
        self.landed = landed
        # Held for the same reason the others are — one object to hand around
        # — but consumed by the promise guard's wiring, not by the reply path.
        self.asks = asks
        # The ledger, for the open-ask comeback's acted_at — and the comeback
        # itself, the director's second slice (see asks.OpenAskComeback).
        self.actions = actions
        self.ask_back = None
        if asks is not None:
            from . import asks as asks_mod
            if hasattr(asks_mod, "OpenAskComeback"):
                self.ask_back = asks_mod.OpenAskComeback(asks)

    def dj_said(self, text: str) -> None:
        """Fan one DJ line to every guard that watches how lines end."""
        if self.door is not None:
            self.door.dj_said(text)
        if self.arc is not None:
            self.arc.dj_said(text)

    def hints_for(self, caller_text: str) -> list:
        """The reply path's notes for this turn, in the standing order.

        Each entry is (kind, log_line, note): `kind` is a stable per-guard
        slug ("stuck", "withheld", "door", "arc", "open_ask") a consumer can
        branch on, `log_line` is the human message saying which guard stepped
        in, and `note` — always the LAST element — is the system message that
        goes in front of the model. The kind exists because the text line
        needs to tell the stuck hit from the others for its Needs-attention
        record, and reading that off the prose log line coupled chat to
        state.py by a substring nobody had marked load-bearing (cloud review,
        2026-08-28).
        """
        out = []
        if self.stuck is not None:
            note = self.stuck.hint_for(caller_text)
            if note:
                out.append(("stuck", "the caller has asked this before — "
                            "steering this turn", note))
        if self.withheld is not None:
            note = self.withheld.hint_for(caller_text)
            if note:
                out.append(("withheld", "the caller asked for a withheld "
                            "capability — carding and steering this turn",
                            note))
        if self.door is not None:
            note = self.door.hint_for(caller_text)
            if note:
                out.append(("door", "the last line held the door open — "
                            "steering this one", note))
        if self.arc is not None:
            note = self.arc.hint_for(caller_text)
            if note:
                out.append(("arc", "both sides have said goodbye — steering "
                            "this turn toward end_call", note))
        # Last, and never over a finished call: the open-ask comeback, so
        # what the caller came for survives holds, segments and tangents
        # without them having to ask twice.
        if (self.ask_back is not None
                and not (self.arc is not None and self.arc.ending)):
            note = self.ask_back.hint_for(
                caller_text,
                getattr(self.actions, "taken_at", None) or [])
            if note:
                out.append(("open_ask", "the caller's ask is still open — "
                            "steering back to it", note))
        # The wind-down after a landed request — but never while anything
        # else is steering: an open ask outranks a crest, and a call already
        # ending needs no help down. See call/landed.py for why this is a
        # mechanism and not four paragraphs of prose.
        if (self.landed is not None and not out
                and not (self.arc is not None and self.arc.ending)):
            note = self.landed.hint_for(caller_text)
            if note:
                out.append(("landed", "the request landed — steering this "
                            "turn toward the wind-down", note))
        return out


def attach_state_watch(session, state: ConversationState) -> None:
    """One watcher on the DJ's lines, fanning to every guard through
    ConversationState.dj_said. The event unwrap lives once in watch.on_dj_line
    now; this is the wiring that used to be a copy per guard."""
    from . import watch

    watch.on_dj_line(session, state.dj_said)
