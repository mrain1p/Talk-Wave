"""The proof a hangup has to show before a call's slot is freed.

`POST /call-ended` frees a concurrency slot by room id, and the room id is
handed to the caller — their own widget is given it at the mint. So the id
alone cannot be the proof: a caller who freed their own slot while still
connected walked straight back past the one-live-phone-in check and the
concurrency ceiling, and the operator's dump button reported no phone-in
for a call that was live (found 2026-09-17). The browser now shows the
per-room `release` minted with its token.

The WORKER cannot show that one. It runs in the other container, it has
never seen the mint, and with a panel password set it holds no admin
credential either — so the beacon it sends at hangup, the one that exists
precisely because a crashed tab never sends its own, would be refused and
every dead session would sit on a slot for the full 30-minute age-out.

What the two processes DO share, by construction, is the LiveKit API
secret: the web half mints join tokens with it and the worker connects
with it, and neither half of this product runs without it. So the worker's
proof is an HMAC of the room name under that secret. Nothing new to
configure, nothing extra to leak into a browser, and not a value any
caller can compute — they are given a room id and a token, never the
secret that signs them.

Both halves derive it HERE rather than each spelling the rule out: two
copies of one fact is the drift surface this repo's own binding principle
says not to create.
"""

from __future__ import annotations

import hashlib
import hmac
import os


def worker_release(room: str) -> str:
    """The worker's proof for one room, or "" when it cannot be made.

    Empty when there is no room or no LiveKit secret in the environment —
    and an empty value must never be treated as a match, which is why every
    caller checks both sides are non-empty before comparing.
    """
    secret = os.environ.get("LIVEKIT_API_SECRET", "")
    if not (secret and room):
        return ""
    return hmac.new(secret.encode("utf-8"), room.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def matches(room: str, shown: str) -> bool:
    """Whether `shown` is the worker's proof for `room`. Constant-time."""
    wanted = worker_release(room)
    if not (wanted and shown):
        return False
    return hmac.compare_digest(wanted, shown)
