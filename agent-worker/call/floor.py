"""Who has the floor — so two of the DJ's own turns never start at once.

Ten things can make the DJ speak and they were added one incident at a time,
each checking whatever the incident was about. Reading them against each other
(docs/the-call.md has the table) shows most of that is already covered:

  * the greeting is a one-shot at pickup, before anything else exists;
  * the late request match and the idle ladder both wait for
    `agent_state == "listening"`, which is false while anything is generating;
  * the hand-over line IS the air, and interrupts on purpose.

That leaves THREE that can start a turn with another already in flight: the
promise nudge, the come-back after a link, and the time-limit sign-off. None of
them checks the others, and all three fire on their own clocks — the come-back
when the air clears, the nudge a second after a line commits, the sign-off when
the call runs out. The overlap is narrow but it is real, and the two the caller
would notice are the nudge landing on top of the come-back.

Deliberately a lock and nothing more. It cannot decide who SHOULD speak — that
would be the director this stream has twice declined to build on the evidence —
it only stops two turns being generated at the same moment. Every holder here
already tolerates being late: the nudge is a repair, the come-back is a
courtesy, and the sign-off ends the call whenever it gets through.

The count is kept because a silent fix is one nobody can tell is load-bearing.
If a month of calls records no collisions, this can go.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

log = logging.getLogger("callin.agent")

# How long a holder may wait for the floor before giving up on it. The point is
# to avoid two voices, not to queue a third — a repair that arrives half a
# minute late is worse than one that never arrives, because the conversation
# has moved on.
MAX_WAIT_SECS = 8.0


def attach_floor_watch(session, floor: "Floor") -> None:
    """Tell the floor when the caller speaks, so it can drop stale turns. The
    final/non-empty caller-line unwrap lives once in watch.on_caller_line."""
    from . import watch

    watch.on_caller_line(session, lambda _t: floor.caller_spoke())


class Floor:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.holder = ""
        # Times a turn wanted to start while another was already generating.
        # Read by the record; nothing branches on it.
        self.collisions = 0
        self.given_up = 0
        # Turns dropped because the caller spoke while they queued. Waiting
        # made these turns LATE, and a late turn is not merely delayed — it is
        # answering something nobody said any more. Room 113774ecedfa: the
        # caller said "No, I don't want anything else" and the DJ replied
        # "That's locked in!", which had been true of a different moment.
        self.stale = 0
        self.last_caller_at = 0.0

    def caller_spoke(self) -> None:
        """The caller said something. Same clock as `take`."""
        self.last_caller_at = time.monotonic()

    @contextlib.asynccontextmanager
    async def take(self, who: str):
        """Hold the floor for one generated turn.

        Yields True when it is yours and False when the wait ran out — a caller
        that gets False should do nothing rather than speak over whoever has
        it, which is the whole point.
        """
        if self._lock.locked():
            self.collisions += 1
            log.info("%s wants the floor while %s has it", who,
                     self.holder or "another turn")
        started = time.monotonic()
        try:
            await asyncio.wait_for(self._lock.acquire(), MAX_WAIT_SECS)
        except asyncio.TimeoutError:
            self.given_up += 1
            log.warning("%s gave up waiting %.0fs for the floor (%s had it) — "
                        "staying quiet rather than talking over it",
                        who, time.monotonic() - started, self.holder or "?")
            yield False
            return
        # THE CONVERSATION MOVED ON WHILE WE QUEUED. Every holder here already
        # tolerates being late, but "late" was doing two jobs: a repair that
        # arrives a beat after the line it repairs is fine, and one that
        # arrives after the CALLER has spoken again is answering a moment that
        # has gone. Dropping it is the same judgement MAX_WAIT_SECS already
        # makes, using the better signal — the caller, rather than a clock.
        #
        # This cannot catch a turn that was already generating when the caller
        # spoke; that is barge-in, and interruptions handle it.
        if self.last_caller_at > started:
            self.stale += 1
            self._lock.release()
            log.info("%s is stale — the caller spoke while it waited, so it "
                     "would answer a moment that has passed", who)
            yield False
            return
        self.holder = who
        try:
            yield True
        finally:
            self.holder = ""
            self._lock.release()
