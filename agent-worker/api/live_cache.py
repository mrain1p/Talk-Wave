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
