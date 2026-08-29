"""One home for turning a LiveKit session event into the line it carries.

Split out at Batch 3 (2026-08-29): "a committed DJ line is an assistant item's
stripped text_content" was written four times (door, arc, state, comeback), and
the caller-line unwrap twice (floor, asks) — one fact about the SDK event,
restated in every guard, each an independent place to drift. It lives once here
now; every guard delegates and keeps only what it DOES with the line, which is
the part that actually differs.

The already-consolidated stuck/withheld guards — driven off the reply path's own
caller text via ConversationState.hints_for — are the end-state this converges
toward: a guard contributes behaviour, the plumbing is written once.

Not every session-event handler belongs here, and folding the rest blindly would
break behaviour: the card flush fires on an EMPTY assistant item, the idle clock
resets on PARTIAL transcripts, the promise guard resets on an empty final, and
the think-pace watcher reads metrics not text. Those keep their own handlers —
see docs/adr/review-ledger.md for the deliberate follow-on.
"""

from __future__ import annotations


def dj_line(ev) -> str:
    """The DJ line an event carries: an assistant item's stripped text_content,
    or "" when the event is not a committed assistant line."""
    item = getattr(ev, "item", None)
    if getattr(item, "role", None) != "assistant":
        return ""
    return str(getattr(item, "text_content", "") or "").strip()


def caller_line(ev) -> str:
    """The caller line an event carries: a FINAL transcript, stripped, or "" for
    a partial or empty one."""
    if not getattr(ev, "is_final", True):
        return ""
    return str(getattr(ev, "transcript", "") or "").strip()


def on_dj_line(session, fn) -> None:
    """Call fn(text) for every committed DJ line on this session."""
    def _on(ev) -> None:
        text = dj_line(ev)
        if text:
            fn(text)

    session.on("conversation_item_added", _on)


def on_caller_line(session, fn) -> None:
    """Call fn(text) for every final, non-empty caller line on this session."""
    def _on(ev) -> None:
        text = caller_line(ev)
        if text:
            fn(text)

    session.on("user_input_transcribed", _on)
