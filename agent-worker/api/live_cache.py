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

_live_cache: dict = {"at": 0.0, "data": None}
# 30s: comfortably above the widget's 20s poll, so an open page costs the
# station roughly one sweep per 40s instead of one per poll. Now-playing on
# the card may lag by up to ~30s, which is fine for a status line.
_LIVE_TTL = 30.0
# The most often an unauthenticated station webhook may force a fresh sweep.
# Operator actions (a settings save, a sound upload) still clear it outright —
# those are already behind the password.
_LIVE_BUST_FLOOR = 5.0


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
