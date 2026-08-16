"""The station's pushes, received and verified.

Split from api/hooks.py at 0.10.89 along the seam its SPLITTING entry
recorded at 0.10.69: registration talks TO the station and never reads a
push; this side listens FROM it and never registers. The shared identity,
secret store and state live here — the receiver is the side that must keep
working when registration is standing down — and hooks.py imports them,
one-way.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import time
from collections import deque
from pathlib import Path

from aiohttp import web

from api.auth import _write_allowed
from api.live_cache import _LIVE_BUST_FLOOR, _live_cache
from api.wire import _cors

log = logging.getLogger("callin.token")

from collections import deque

# Our row's identity on the station, and the only thing that makes
# registration idempotent. Sending no id means the station mints a fresh one
# every time, so the same Talk Wave moving to a new LAN address left its old
# row behind and added a second — and the station caps the list (16 in
# SUB/WAVE 1.6.0), after which registration fails for good with nothing
# obvious to point at. An env var because one station can serve two of these,
# and two rows claiming one id is a collision the station resolves by dropping
# one of them silently.
HOOK_ID = (os.environ.get("CALLIN_HOOK_ID") or "").strip() or "talk_wave"

# The Authorization header our row registers with. The station does not SIGN
# its hooks, but it does echo a per-hook header verbatim on every push — which
# turns a receiver that anyone on the LAN could otherwise drive into one only
# the station can. Minted once and kept on disk, because the station redacts
# the stored header on read: after registration, our copy is the only copy
# there is to compare an arriving push against.
def _secret_path() -> Path:
    return Path(os.environ.get("CALLIN_HOOK_SECRET_PATH")
                or Path(__file__).parent.parent.parent / "data" / "hook-secret.json")


def _air_path() -> Path:
    """Where the last VERIFIED voice push is written for the worker's on-air
    guard. Derived from the secret path's directory so a deployment (or a
    test) that redirects one redirects the other. call/air.py carries a twin
    of this derivation — duplicated so the worker does not import the HTTP
    surface for one path string; TestThePushFileHasOneAddress pins them
    together."""
    return Path(os.environ.get("CALLIN_HOOK_AIR_PATH")
                or _secret_path().with_name("hook-air.json"))


def _load_hook_secret() -> str:
    try:
        d = json.loads(_secret_path().read_text())
        return str(d.get("authHeader") or "")
    except Exception:                                         # noqa: BLE001
        return ""


def _store_hook_secret(value: str) -> None:
    try:
        path = _secret_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"authHeader": value}))
    except Exception as e:                                    # noqa: BLE001
        # The receiver checks against this file, so an unwritable file means
        # verification quietly stays OFF — the old, open behaviour — while the
        # station dutifully sends a header nothing looks at. Loud for that
        # reason: nothing else would ever mention it.
        log.warning("could not persist the webhook secret (%s) — push "
                    "verification stays off until it can be written", e)


def _mint_hook_secret() -> str:
    return "Bearer " + secrets.token_urlsafe(24)


# The pushes the on-air card reacts to. Intersected with the station's own
# advertised vocabulary before it is sent rather than assumed: the station
# validates this list against an enum and refuses the WHOLE registration over
# one name it doesn't know, so a station that renames or retires an event
# would otherwise take our webhooks down with it. The voice.* lifecycle
# (SUB/WAVE 1.8, our own issue #1382) rides that same intersection — a
# pre-1.8 station simply never grants them and the guard keeps estimating.
WANTED_EVENTS = ("track.play", "dj.say", "dj.link", "request.received",
                 "voice.queued", "voice.start", "voice.end")

# Which arriving events are worth a cache bust, derived from what we asked for
# rather than listed again — the two drifting apart is how you get a
# subscription whose events change nothing on the card.
_BUSTING_PREFIXES = frozenset(e.split(".")[0] for e in WANTED_EVENTS)

_hook_events: deque = deque(maxlen=50)
_hook_state: dict = {
    "registered": False,
    "url": "",
    "id": HOOK_ID,
    # Which station the registration is true OF. Empty until one accepts us.
    "station": "",
    "events": [],
    # Pushes actually received. `registered` only ever meant the station
    # accepted a row; this is the number that says packets are arriving.
    "received": 0,
    "detail": "not attempted",
}



def _epoch(value) -> float:
    """A station timestamp as seconds since the epoch, or 0.0 if unusable.

    Deliberately generous about the shape — epoch seconds, epoch millis, or an
    ISO string — because the field is read from another project's payload and
    a stricter reader would fail closed on a format change, silently, back to
    the arrival time it was added to replace.
    """
    if value in (None, ""):
        return 0.0
    try:
        n = float(value)
        # Anything past ~2001 in millis is far beyond a plausible epoch-seconds
        # date, so the magnitude tells the two apart without a format flag.
        return n / 1000.0 if n > 1e11 else n
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime

        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _remember_air(event: str, body: dict) -> None:
    """Write the one entry the worker's on-air guard reads.

    Extracted from the handler at 0.10.108 so the air file's CONTENTS can
    be tested directly — the ducking bug lived in a field this never
    recorded, and nothing could have caught that through the HTTP layer.
    """
    entry = None
    now = time.time()
    if event in ("dj.say", "dj.link"):
        entry = {"at": now, "event": event,
                 "text": str(body.get("text") or "")[:2000]}
    # How far the LISTENER is behind the live edge. The station measures
    # it and puts it on voice.start for exactly this reason (its #1114):
    # every voice.* timestamp is stamped at the encoder, and the person on
    # the phone hears it this many seconds later. Dropping it was the
    # ducking bug — the hold ended when the station stopped talking, not
    # when the CALLER stopped hearing it, so the call DJ came back over
    # the top every single time.
    try:
        buf = max(0.0, min(30.0, float(body.get("streamBufferSeconds") or 0)))
    except (TypeError, ValueError):
        buf = 0.0

    # WHEN THE WORDS ACTUALLY HIT THE LIVE EDGE, from the mixer's own clock.
    # SUB/WAVE #1390 moved every speech signal off the handoff instant and onto
    # air time for exactly this reason, and says what a consumer should do with
    # it: "one syncing to what listeners hear uses airedAt + streamBufferSeconds".
    # We were stamping our own arrival time instead — the handoff, plus the
    # network, plus our queue, measured on a different box's clock.
    #
    # Kept BESIDE `at` rather than replacing it: `at` is compared against our
    # own send times elsewhere (see _stale_end), and mixing two machines'
    # clocks in that comparison would trade a small error for a confusing one.
    # An unmeasured air time is ABSENT rather than zeroed upstream, so a
    # missing value here correctly means "fall back to when it reached us".
    aired = _epoch(body.get("airedAt"))

    if event == "voice.queued":
        try:
            lead = max(0.0, float(body.get("estimatedAirInMs") or 0) / 1000.0)
        except (TypeError, ValueError):
            lead = 0.0
        entry = {"at": now, "event": event, "v": 2, "phase": "queued",
                 "voiceId": str(body.get("voiceId") or "")[:64],
                 "text": str(body.get("text") or "")[:2000],
                 "durMs": int(body.get("durationMs") or 0),
                 "bufSecs": buf, "airedAt": aired,
                 "airAt": now + lead}
    elif event == "voice.start":
        entry = {"at": now, "event": event, "v": 2, "phase": "speaking",
                 "voiceId": str(body.get("voiceId") or "")[:64],
                 "text": str(body.get("text") or "")[:2000],
                 "bufSecs": buf, "airedAt": aired,
                 "durMs": int(body.get("durationMs") or 0)}
    elif event == "voice.end":
        # No text on purpose: a skewed old worker reads at+text, and an
        # empty text makes it fall to the short fallback hold rather
        # than sizing a fresh hold from words that just FINISHED.
        entry = {"at": now, "event": event, "v": 2, "phase": "clear",
                 "voiceId": str(body.get("voiceId") or "")[:64],
                 "bufSecs": buf, "airedAt": aired,
                 "text": ""}
    if entry is None:
        return
    try:
        path = _air_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        prev = {}
        try:
            prev = json.loads(path.read_text())
        except Exception:                                 # noqa: BLE001
            prev = {}

        # The legacy dj.say/dj.link is the SAME utterance as the voice.*
        # lifecycle, stamped at handoff instead of at air — the station emits
        # both, and this file holds one entry, so the v1 event was overwriting
        # a v2 one about a second later and taking the duration and the stream
        # buffer with it. Measured on air 2026-08-13: voice.queued at +74.68s
        # carrying durMs=17827 and bufSecs=22, dj.say at +75.88s carrying
        # neither, after which the guard sized the hold from a word count with
        # no buffer and handed the caller back at the exact moment the DJ
        # became audible to them. A station fluent in the lifecycle does not
        # need us to read its handoff events at all.
        # CARRY THE BUFFER FORWARD. Only some events report it — voice.end
        # carries 0 — and the guard primes the caller's lag from whatever push
        # happens to be newest when a call starts. So a call beginning after a
        # `clear` believed the caller was 2 seconds behind rather than 22, and
        # sized the hold for its own announcement from the wrong number.
        # Measured on air 2026-08-16: the hold opened with callerLag=2.0 while
        # the same station had been reporting 22 all evening, and the DJ came
        # back 3 seconds before the caller had finished hearing the shoutout.
        # The buffer belongs to the station's Icecast config, so the last real
        # reading is a far better guess than the fallback.
        # AND CARRY THE UTTERANCE'S OWN NUMBERS ONTO ITS `clear`. voice.end
        # says the station stopped talking at the LIVE EDGE; the caller is
        # still hearing it for another `bufSecs`. It ships with no durationMs
        # and no airedAt, so the moment it lands the guard loses every number
        # it needs to work out when the caller actually stops hearing it —
        # 22 seconds before that matters.
        #
        # Heard on air 2026-08-16 (room fc4bb17f63de): station spoke 20.9-31.7,
        # the caller heard it 42.9-53.7, and the hold closed at 53.1 on a
        # word-count estimate because the floor had nothing to measure. The
        # back-from-air line went out over the station's last words.
        #
        # Paired by voiceId, which is what upstream #1390 minted it for.
        if (entry.get("phase") == "clear" and entry.get("voiceId")
                and entry.get("voiceId") == prev.get("voiceId")):
            for field in ("durMs", "airedAt"):
                if not entry.get(field) and prev.get(field):
                    entry[field] = prev[field]
        if not entry.get("bufSecs"):
            try:
                carried = float(prev.get("bufSecs") or 0)
            except (TypeError, ValueError):
                carried = 0.0
            if carried > 0:
                entry["bufSecs"] = carried
        demote = int(entry.get("v") or 0) < 2 and _lifecycle_is_live(prev, now)
        _keep(entry, prev, path, demote=demote)
    except Exception as e:                                # noqa: BLE001
        log.debug("could not write the on-air push file: %s", e)


# How long a v2 entry proves the station is speaking the voice lifecycle. Long
# enough to cover a whole utterance plus the queue wait; short enough that a
# station downgraded mid-run falls back to its handoff events within a track.
LIFECYCLE_TRUST_SECS = 180.0

# How many pushes the file remembers. The guard reads only the newest, but
# nothing could ever answer "what did the station actually do, and when" — the
# ducking has been diagnosed twice now by watching this file at 200ms for five
# minutes, which is not a thing an operator can be asked to do.
AIR_HISTORY = 24


def _lifecycle_is_live(prev: dict, now: float) -> bool:
    """Has the station spoken v2 voice.* recently enough to be trusted?"""
    if not isinstance(prev, dict):
        return False
    for e in list(prev.get("recent") or [])[-AIR_HISTORY:]:
        if int((e or {}).get("v") or 0) >= 2 and \
                now - float((e or {}).get("at") or 0) < LIFECYCLE_TRUST_SECS:
            return True
    return int(prev.get("v") or 0) >= 2 and \
        now - float(prev.get("at") or 0) < LIFECYCLE_TRUST_SECS


def _keep(entry: dict, prev: dict, path: Path, demote: bool = False) -> None:
    """Write the authoritative entry, and append this push to the history.

    `demote` records the push in `recent` — it happened, and a timeline that
    hides events is worse than none — without letting it become what the guard
    reads. The top level keeps the shape it has always had, so a worker on an
    older image reads `at`/`text`/`phase` and never looks at `recent`: this
    stays safe across the version skew a two-container deploy can leave.
    """
    recent = list((prev or {}).get("recent") or [])
    row = {k: entry.get(k) for k in
           ("at", "event", "v", "phase", "voiceId", "durMs", "bufSecs",
            "airAt")}
    if demote:
        row["ignored"] = "the station is speaking the voice lifecycle"
    recent.append(row)
    out = dict(prev if demote else entry)
    out.pop("recent", None)
    out["recent"] = recent[-AIR_HISTORY:]
    path.write_text(json.dumps(out))


async def handle_station_hook(request: web.Request) -> web.Response:
    """Receiver for the station's pushes.

    Once registration has stored a secret, a push must carry it back in the
    Authorization header or it is turned away — the station echoes a per-hook
    header verbatim, and ours is minted at registration. Until then (no
    credentials, an old station, a first boot) the endpoint is open, so
    payloads stay untrusted data either way: store, bust caches, never act
    directly on their contents."""
    expected = _load_hook_secret()
    if expected:
        got = str(request.headers.get("Authorization") or "")
        if not hmac.compare_digest(got, expected):
            # Counted separately from `received`, so the test fire can tell
            # "nothing arrived" apart from "arrived and was turned away".
            _hook_state["rejected"] = _hook_state.get("rejected", 0) + 1
            log.warning("station webhook rejected — Authorization header "
                        "missing or wrong")
            return web.json_response({"error": "bad authorization"}, status=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    event = str(body.get("event") or body.get("type") or "?")[:80]
    # Summarised, not stored whole. This endpoint cannot be authenticated (the
    # station does not sign its hooks), so `body` is arbitrary and unbounded up
    # to aiohttp's 1MB limit — and the deque holds fifty of them, in a process
    # already running near the SDK's own memory warning line. It is only ever
    # read back as a diagnostic list, so a trimmed rendering is all it was
    # worth keeping.
    _hook_events.append({
        "at": time.time(),
        "event": event,
        "data": {str(k)[:40]: str(v)[:120] for k, v in list(body.items())[:12]}
        if isinstance(body, dict) else {},
    })
    # Saturates at the deque's length, so it cannot be counted from the list.
    _hook_state["received"] = _hook_state.get("received", 0) + 1
    # `rejected` is a RUN — refusals since the last push that got in — not a
    # lifetime total. That is what lets hooks._mis_keyed notice a key that
    # breaks after a period of working, instead of going blind for good the
    # moment one push lands.
    _hook_state.pop("rejected", None)
    log.info("station webhook: %s", event)

    # The worker's on-air guard anchors its hold on this file. VERIFIED
    # pushes only: an open receiver writing this would let anyone on the LAN
    # gag the call DJ at will, which is exactly the "never act on their
    # contents" rule above.
    #
    # Two generations share the file. A 1.8 station sends the voice.*
    # lifecycle (queued: the voice is COMING, with a lead estimate; start:
    # it is audible, measured; end: it stopped) — entries carry "v": 2 and a
    # "phase", and the guard holds on them exactly. dj.say/dj.link keep the
    # legacy shape for older stations, where the push lands at the HANDOFF
    # instant, seconds before the guard's 4s log poll would notice (0.10.69).
    # Every entry keeps "at" and "text" so a version-skewed worker reading
    # the other generation's entry still errs toward holding, never toward
    # talking over the air.
    if expected and isinstance(body, dict):
        _remember_air(event, body)

    # Anything that changes what the card shows invalidates the cache — but not
    # more often than the cache would have expired anyway.
    #
    # This endpoint cannot be authenticated (the station doesn't sign hooks),
    # and every bust makes the next /live fan out into four to six station
    # reads. Left ungoverned, anyone who can reach this can make every open
    # widget hammer the station on every poll. The floor means a flood of
    # hooks costs the station no more than the normal 30-second refresh.
    if event.split(".")[0] in _BUSTING_PREFIXES:
        if time.time() - _live_cache["at"] >= _LIVE_BUST_FLOOR:
            _live_cache["data"] = None
    return web.json_response({"ok": True})


def _unauthorised(request: web.Request) -> web.Response:
    return _cors(request, web.json_response(
        {"error": request.get("auth_error") or "not allowed",
         "authRequired": bool(request.get("auth_required"))},
        status=401,
    ))


async def handle_hooks_recent(request: web.Request) -> web.Response:
    # Operator debugging surface — same gate as the rest of the panel.
    if not _write_allowed(request):
        return _unauthorised(request)
    return _cors(request, web.json_response(
        {"registered": _hook_state, "events": list(_hook_events)[-15:]}
    ))

