"""What the DJ knows, and how the DJ behaves.

Split because the two change for completely different reasons: the facts move
when the station's API moves, the conduct moves when a call goes wrong. Before
this, a bad call and a new station field were edits to the same 500-line file.

    briefing.py   facts — now playing, recent, queue, booth chatter, segments
    conduct.py    rules — momentum, triage, closing, tool etiquette, safety
    assemble.py   joins them into the per-call system prompt

`assemble.build_system_prompt` is the only thing callers need.
"""

from __future__ import annotations

from brain.assemble import build_system_prompt

__all__ = ["build_system_prompt"]
