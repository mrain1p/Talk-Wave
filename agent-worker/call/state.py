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
                 asks=None) -> None:
        self.door = door
        self.stuck = stuck
        self.withheld = withheld
        self.arc = arc
        # Held for the same reason the others are — one object to hand around
        # — but consumed by the promise guard's wiring, not by the reply path.
        self.asks = asks

    def dj_said(self, text: str) -> None:
        """Fan one DJ line to every guard that watches how lines end."""
        if self.door is not None:
            self.door.dj_said(text)
        if self.arc is not None:
            self.arc.dj_said(text)

    def hints_for(self, caller_text: str) -> list:
        """The reply path's notes for this turn, in the standing order.

        Each entry is (log_line, note); every note goes in front of the model
        as a system message and every log line says which guard stepped in —
        the same pairs, in the same order, that on_user_turn_completed used
        to assemble by hand.
        """
        out = []
        if self.stuck is not None:
            note = self.stuck.hint_for(caller_text)
            if note:
                out.append(("the caller has asked this before — steering "
                            "this turn", note))
        if self.withheld is not None:
            note = self.withheld.hint_for(caller_text)
            if note:
                out.append(("the caller asked for a withheld capability — "
                            "carding and steering this turn", note))
        if self.door is not None:
            note = self.door.hint_for(caller_text)
            if note:
                out.append(("the last line held the door open — steering "
                            "this one", note))
        if self.arc is not None:
            note = self.arc.hint_for(caller_text)
            if note:
                out.append(("both sides have said goodbye — steering this "
                            "turn toward end_call", note))
        return out


def attach_state_watch(session, state: ConversationState) -> None:
    """One watcher on the DJ's lines, where there used to be one per guard.

    Same shape as the watchers it replaces (door.attach_door_watch,
    arc.attach_arc_watch), for the same reason: the event is the only place
    that knows what actually went out.
    """

    def _on_said(ev) -> None:
        item = getattr(ev, "item", None)
        if getattr(item, "role", None) != "assistant":
            return
        text = str(getattr(item, "text_content", "") or "").strip()
        if text:
            state.dj_said(text)

    session.on("conversation_item_added", _on_said)
