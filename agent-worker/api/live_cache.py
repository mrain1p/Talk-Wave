"""One /live answer, held for a few seconds, and everything that stales it.

Its own module because it has five writers and only one reader. Each build of
the card payload fans out into four to six station reads and the widget polls
every 20s, so without a cache a dashboard left open works the station ~15x
harder than it needs to. But anything that changes what the card would SAY —
a settings save, a new ring tone, a password that flips `guestRequired` — has
to clear it, or the operator changes something and the page keeps insisting
otherwise for half a minute.

Keeping the dict here rather than in live.py is what lets those writers say so
directly: live.py resolves its sounds through sounds.py, so a cache owned by
live.py would have sounds.py importing back into it.
"""

from __future__ import annotations

_live_cache: dict = {"at": 0.0, "data": None, "quick_until": 0.0}
# 30s: comfortably above the widget's 20s poll, so an open page costs the
# station roughly one sweep per 40s instead of one per poll. Now-playing on
# the card may lag by up to ~30s, which is fine for a status line.
_LIVE_TTL = 30.0
# The most often an unauthenticated station webhook may force a fresh sweep.
# Operator actions (a settings save, a sound upload) still clear it outright —
# those are already behind the password.
_LIVE_BUST_FLOOR = 5.0

# THE QUICK WINDOW (2026-11-27). A request reaches Up next late, measured on
# the operator's stack: the station fires `request.received` the moment it
# ACCEPTS a request, before it resolves which track to queue (routes/
# request.ts, then resolveRequest) — so the bust above landed first, the
# next /live refilled with the OLD queue, and that stale answer was then held
# for the full 30s, with the widget's own poll on top. A queue change our own
# DJ makes (a call, the text line, the player's command row) sends no event
# at all. So whatever is known to be about to change the queue opens a short
# window in which an answer is held for seconds rather than half a minute:
# the card's burst of polls then sees the track land within a few seconds of
# the station queueing it. Bounded both ways — a window, not a mode, and a
# floor of one sweep per _LIVE_QUICK_TTL however many tabs or hooks arrive.
_LIVE_QUICK_TTL = 3.0
_LIVE_QUICK_SECS = 45.0

# The player's lyrics read (api/player.py), held here beside the card's so the
# one place that hears "a new record is on" clears both. One entry: the
# station only ever answers for the track on air.
_lyrics_cache: dict = {"at": 0.0, "data": None}


def live_ttl(now: float) -> float:
    """How long a /live answer is fresh at `now`: seconds inside a quick
    window, the ordinary 30 outside one."""
    return _LIVE_QUICK_TTL if now < _live_cache.get("quick_until", 0.0) else _LIVE_TTL


def expect_queue_change(now: float, secs: float = _LIVE_QUICK_SECS) -> None:
    """Something is about to change the queue: drop the held answer and open
    (or extend) the quick window. Never shortens a window already open."""
    _live_cache["data"] = None
    _live_cache["quick_until"] = max(_live_cache.get("quick_until", 0.0), now + secs)


def build_lock():
    """The one rebuild, so a miss is not N of them.

    Measured on the deployed box (2026-09-17): a warm /live answers in 1.4ms
    and a cold one in 374ms, because the miss path fans out into four to six
    station reads, run one after another. Nothing serialised those misses —
    so when the 30s TTL expires, every tab that polls during that 374ms window
    starts its own full sweep, and the station takes the whole fan-out once per
    tab instead of once. The cache exists precisely to stop that arithmetic;
    this is the same argument one level down.

    Double-checked by the caller: whoever wins the lock builds, and everyone
    behind them re-reads the cache and finds it fresh. A waiter therefore
    waits at most one build — the same wait it would have had on its own, with
    the station spared the rest.

    Made lazily, and remade when the loop changes. An asyncio.Lock binds to
    the first loop that awaits it and raises if a second one tries — which
    never happens in the worker (one loop, whole process life) and happens in
    every test file (asyncio.run makes a fresh loop per test). Holding the
    loop beside the lock is what keeps a module-level lock honest in both.
    """
    global _build_lock, _build_loop
    import asyncio

    loop = asyncio.get_running_loop()
    if _build_lock is None or _build_loop is not loop:
        _build_lock = asyncio.Lock()
        _build_loop = loop
    return _build_lock


_build_lock = None
_build_loop = None
