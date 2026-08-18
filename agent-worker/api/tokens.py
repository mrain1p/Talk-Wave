"""Minting a join token, and the ceilings on doing so.

The browser must never see LIVEKIT_API_SECRET, so joining is brokered here:
the widget POSTs to /token, this signs a short-lived join grant scoped to one
freshly-named room, and returns it along with the public LiveKit URL.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
import uuid

from aiohttp import web
from livekit import api

import settings as settings_store
import station_prefetch
from api.auth import _guest_ok, _write_allowed, caller_tier
from api.env import LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_PUBLIC_URL
from api.wire import _caller_key, _cors

log = logging.getLogger("callin.token")

# In-flight snapshot prefetches, held by strong reference: a bare
# create_task can be garbage-collected mid-fetch, which would quietly turn
# the worker's head start off with nothing logged.
_prefetch_tasks: set = set()


def _prefetch_station(cfg: dict, tier: str) -> None:
    """Start the worker's station reads NOW — see station_prefetch.py.

    The worker re-reads the station the moment this room dispatches, a second
    or two from here; fetching once now means its ringing finds the answers
    already on disk. Fire-and-forget: the mint answers at full speed whether
    or not the station does. Resolved for THIS caller's tier so the snapshot
    carries skills exactly when the worker will ask for them.
    """
    resolved = settings_store.permissions_for(cfg, tier)
    task = asyncio.get_running_loop().create_task(
        station_prefetch.capture(with_skills=bool(resolved.get("allow_skills"))))
    _prefetch_tasks.add(task)
    task.add_done_callback(_prefetch_tasks.discard)


# --- usage controls -------------------------------------------------------
# Minting a token starts a call, and a call costs LLM + TTS + STT on every
# turn. Without a ceiling, a single open tab (or a bored visitor) can loop
# this endpoint and run up real money. Limits are generous by default: the
# point is to stop runaway use, not to ration callers.
_recent_mints: list[float] = []          # timestamps, global
_caller_last: dict[str, float] = {}      # caller key -> last mint

# A minted room is `<prefix>-<tier?>-<12 hex>`; feedback for anything else was
# never a real call, so it is rejected before the 10s / 20-scan retry loop
# rather than tying a request open on a random string (0.10.57 review). The
# tier segment may carry the on-air letter behind it (`callin-gl-…`).
_ROOM_SHAPE = re.compile(r"^(callin|vm|probe)-([a-z]l?-)?[0-9a-f]{12}$")
# And a ceiling on how many feedback waiters may be parked at once, so a flood
# of well-formed-but-nonexistent rooms can't hold a pile of requests open.
_feedback_waiters = asyncio.Semaphore(16)

# room -> what we knew about the caller when the token was minted. The worker
# writes the call record and never sees any of this, so it is merged in when
# /calls is served.
#
# Deliberately in memory only and never written to disk: it is enough to
# answer "why did that call fail" while the process is up, without the call
# archive quietly becoming a log of who rang and from where. A restart loses
# it, which is the right trade for a diagnostic.
_mint_info: dict[str, dict] = {}
_MINT_INFO_KEEP = 60


def _describe_client(ua: str) -> str:
    """Browser and OS, roughly. Enough to tell a Safari problem from a Firefox
    one without pulling in a user-agent library."""
    ua = ua or ""
    browser = next((n for n in ("Edg", "OPR", "Chrome", "Firefox", "Safari")
                    if n in ua), "")
    browser = {"Edg": "Edge", "OPR": "Opera"}.get(browser, browser)
    if browser == "Safari" and "Chrome" in ua:
        browser = "Chrome"
    os_name = next((n for n in ("iPhone", "iPad", "Android", "Mac OS X",
                                "Windows NT", "Linux", "CrOS") if n in ua), "")
    os_name = {"Windows NT": "Windows", "Mac OS X": "macOS",
               "CrOS": "ChromeOS"}.get(os_name, os_name)
    return " on ".join(p for p in (browser, os_name) if p) or "unknown client"


def _network_of(ip: str) -> str:
    """Whether the caller was on this network or came in from outside.

    Worth surfacing because it is the first question when a call connects and
    then hears nothing: an off-LAN caller with no media path looks identical
    to a silent one from inside the booth.
    """
    ip = (ip or "").strip()
    if not ip or ip == "unknown":
        return "unknown"
    if ip.startswith(("10.", "192.168.", "127.", "::1", "fd", "fe80")):
        return "same network"
    if ip.startswith("172."):
        try:
            return "same network" if 16 <= int(ip.split(".")[1]) <= 31 else "off-network"
        except (ValueError, IndexError):
            return "unknown"
    return "off-network"
_live_calls: dict[str, float] = {}       # room -> started at

_CALL_ASSUMED_MAX = 1800.0               # forget a room after 30 min

# How long a minted join token stays valid. Long enough to cover a slow page
# and a retry, short enough that a captured token is not a reusable line.
TOKEN_TTL = datetime.timedelta(minutes=2)


def _refusal_is_line_state(refusal: str) -> bool:
    """Which refusals the answering machine answers THROUGH. Matched on the
    caller-facing wording because that is the one string both sides share —
    brittle-looking, but pinned by tests, and it keeps _check_usage returning
    exactly what a caller is told.

    Busy only. Paused used to be here too, until the operator drew the
    hierarchy out loud: the kill switch is the LINE, and the two transmission
    modes hang off it — paused means the booth answers nothing, the machine
    included. A paused line that still took messages made the dashboard's
    one big switch a lie."""
    return "tied up" in refusal


def _check_usage(request: web.Request, cfg: dict) -> str | None:
    """Returns a caller-facing reason to refuse, or None to allow.

    Wording is deliberately in-world — someone pressing Call shouldn't be told
    about rate limits. These read as the station being busy, because from the
    caller's side that's exactly what it is.
    """
    import time as _time

    now = _time.time()

    # Expire anything stale before deciding. The mint log keeps a full day so
    # the daily ceiling has something to count; the hourly figure is a slice
    # of the same list.
    _recent_mints[:] = [t for t in _recent_mints if t > now - 86400]
    this_hour = sum(1 for t in _recent_mints if t > now - 3600)
    for room, started in list(_live_calls.items()):
        if now - started > _CALL_ASSUMED_MAX:
            _live_calls.pop(room, None)
    # A cooldown older than the longest configurable wait can never matter
    # again; without this the per-caller map grows by one entry per IP forever.
    for key, at in list(_caller_last.items()):
        if now - at > 3600:
            _caller_last.pop(key, None)

    if cfg.get("calls_paused"):
        return "The booth isn't taking calls at the moment — the line's closed for now."

    concurrent = int(cfg.get("max_concurrent_calls") or 0)
    if concurrent > 0 and len(_live_calls) >= concurrent:
        return "The booth line is tied up with another caller. Give it a minute and try again."

    per_day = int(cfg.get("calls_per_day") or 0)
    if per_day > 0 and len(_recent_mints) >= per_day:
        return ("The booth has had a lot of attention today and the phone's been "
                "unplugged for the night. Try again tomorrow.")

    per_hour = int(cfg.get("calls_per_hour") or 0)
    if per_hour > 0 and this_hour >= per_hour:
        return "The switchboard has been lit up this hour. Try the booth again a little later."

    cooldown = int(cfg.get("caller_cooldown_secs") or 0)
    if cooldown > 0:
        last = _caller_last.get(_caller_key(request))
        if last and (now - last) < cooldown:
            wait = int(cooldown - (now - last))
            return f"You've only just hung up — give it {wait}s before ringing back."

    return None


def on_air_call_live() -> bool:
    """Whether any minted, unfinished call chose the on-air door — read off
    the lettered room names this process minted itself. The panel's dump
    asks this before arming a marker, so a dump pressed on a quiet line can
    never behead the next caller's first turn."""
    import time as _time

    now = _time.time()
    return any(settings_store.on_air_from_room(room)
               and now - started < _CALL_ASSUMED_MAX
               for room, started in _live_calls.items())


async def handle_call_ended(request: web.Request) -> web.Response:
    """The widget reports a hangup so a finished call stops counting against
    the concurrency limit immediately, rather than aging out."""
    try:
        body = await request.json()
        _live_calls.pop(str(body.get("room", "")), None)
    except Exception:
        pass
    return _cors(request, web.json_response({"ok": True}))


async def handle_call_feedback(request: web.Request) -> web.Response:
    """The caller's thumbs up or down, stored against that call's transcript.

    Unauthenticated on purpose, and it is worth saying why: the person with an
    opinion about the call is the anonymous stranger who was just on it, and
    there is no credential they could hold. What stops it being an open write
    is the shape — the only thing it can do is set one of two words on a
    record that already exists, keyed by a room id the writer had to have been
    given, and rooms are minted per call and deleted at the end of one.

    The retry is not politeness. The worker writes the record in its shutdown
    callback, in the OTHER container, and the widget shows these buttons the
    moment the line drops — so a caller with a fast finger can beat the file
    into existence. Waiting a few seconds costs one idle request and is the
    difference between the feature working and it working most of the time.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    room = str((body or {}).get("room") or "")
    rating = str((body or {}).get("rating") or "")
    if rating not in ("up", "down") or not _ROOM_SHAPE.match(room):
        return _cors(request, web.json_response(
            {"error": "room and rating (up|down) are required"}, status=400))

    from call import record as call_record

    if _feedback_waiters.locked():
        # Every slot is parked; rather than open a 21st 10-second wait, tell
        # the caller to try once more. A real caller's record lands in seconds.
        return _cors(request, web.json_response({"ok": False, "busy": True}))
    async with _feedback_waiters:
        for attempt in range(20):        # ~10s, then give up quietly
            if call_record.rate(room, rating):
                return _cors(request, web.json_response({"ok": True}))
            if attempt < 19:
                await asyncio.sleep(0.5)
    # Recording may simply be switched off, which is not an error worth
    # showing a caller who has just been thanked for answering.
    log.info("no call record to attach a rating to (room=%s)", room)
    return _cors(request, web.json_response({"ok": False, "stored": False}))


async def handle_token(request: web.Request) -> web.Response:
    if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        return _cors(
            request,
            web.json_response({"error": "LiveKit credentials not configured"}, status=500),
        )

    # The pipeline check's media-path stage mints a real token and connects a
    # real room from the browser — the only way to prove the WebRTC leg the
    # server cannot see (e.g. LiveKit in docker advertising its container IP).
    # Probe rooms are skipped by the worker and bypass the usage limits, so
    # minting one is gated the same as every other test endpoint.
    try:
        body = await request.json() if request.can_read_body else {}
    except Exception:
        body = {}
    probe = bool(isinstance(body, dict) and body.get("probe"))
    voicemail = bool(isinstance(body, dict) and body.get("voicemail")) and not probe
    on_air = (bool(isinstance(body, dict) and body.get("onAir"))
              and not probe and not voicemail)
    if probe and not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))

    cfg = settings_store.load()
    if not probe:
        # The door code, if there is one, is checked before the usage limits:
        # a stranger who can't get in shouldn't be told how busy the line is.
        if not _guest_ok(request):
            return _cors(request, web.json_response(
                {"error": request.get("auth_error") or "password required",
                 "guestRequired": True},
                status=401,
            ))
        refusal = _check_usage(request, cfg)
        if voicemail:
            # The answering machine exists FOR the refusals a live call
            # meets: lines-busy is exactly when it should pick up, so that
            # one does not close it. Paused DOES — the kill switch closes
            # the whole line, machine included (see _refusal_is_line_state).
            # The per-caller cooldown and the hour/day ceilings still hold —
            # a message costs STT, and a robot redialling the machine is the
            # same robot the ceilings exist for.
            policy = settings_store.voicemail_policy(cfg)
            if policy == "never":
                return _cors(request, web.json_response(
                    {"error": "The booth doesn't take messages."}, status=403))
            # Who may use the machine, as a tier. The order is the caller
            # ladder; "off" grants nobody, and an unknown value fails closed.
            if not settings_store.tier_reaches(
                    cfg.get("allow_voicemail"), caller_tier(request)):
                return _cors(request, web.json_response(
                    {"error": "The booth doesn't take messages on this "
                              "line."}, status=403))
            if refusal and not _refusal_is_line_state(refusal):
                log.info("voicemail refused by usage controls: %s", refusal)
                return _cors(request, web.json_response(
                    {"error": refusal, "busy": True}, status=429))
        elif refusal:
            log.info("call refused by usage controls: %s", refusal)
            return _cors(request, web.json_response({"error": refusal, "busy": True}, status=429))
        if on_air and not settings_store.tier_reaches(
                cfg.get("allow_on_air"), caller_tier(request)):
            # Same ladder as the machine's gate: "off" grants nobody and an
            # unknown value fails closed. The widget only offers the toggle
            # when /live says the door exists, so a refusal here is a
            # hand-built client or a stale tab — either way it is told
            # plainly rather than being put on air anyway.
            return _cors(request, web.json_response(
                {"error": "This line can't put callers on the air."},
                status=403))
        if on_air and not cfg.get("on_air_calls_enabled", True):
            # The dashboard's quick kill for the phone-in door, narrower
            # than the tier row: the operator stopped live calls going out
            # without closing the feature. Busy-shaped — a stale tab gets
            # the engaged tone, not an error code.
            return _cors(request, web.json_response(
                {"error": "The booth isn't putting callers on the air "
                          "right now — call in off the air instead.",
                 "busy": True}, status=429))
        if on_air and on_air_call_live():
            # ONE phone-in at a time. The station has a single voice queue,
            # and the first deployed test proved what two live calls do to
            # it: their clips and brackets interleave into one broadcast —
            # a dead call's sign-off landed inside a real call's segment.
            # In-world and busy-shaped, so the widget's engaged-tone path
            # handles it like any other full line.
            return _cors(request, web.json_response(
                {"error": "Someone's already live on the air — give it a "
                          "minute, or call in off the air.",
                 "busy": True}, status=429))
        if not voicemail and (settings_store.voicemail_policy(cfg) == "always"
                              or not cfg.get("live_calls_enabled", True)):
            # A voicemail-only line: the widget offers Leave a message and a
            # hand-built client asking for a live call gets the same answer.
            return _cors(request, web.json_response(
                {"error": "The booth is taking messages tonight, not live "
                          "calls — leave one and it gets passed on.",
                 "busy": True}, status=429))

    # One room per call keeps callers from ever landing in each other's audio.
    #
    # The caller's tier rides in the NAME rather than in participant metadata,
    # for two reasons. The name is inside the signed grant, so a caller cannot
    # raise their own tier without a token nobody minted them — metadata a
    # participant sets is theirs to choose. And the worker has the room name
    # the instant the job starts, where a participant's metadata is only there
    # once they have joined, which is after CallSession has already built its
    # tool list.
    #
    # The last 12 characters stay hex: call/record.py finds a transcript by
    # matching on that suffix, and the widget posts a rating against it.
    tier = "admin" if probe else caller_tier(request)
    # The on-air letter rides behind the tier, inside the signed name, for
    # the same reason the tier itself does: a caller cannot flip it.
    seg = tier[0] + ("l" if on_air else "")
    room = (f"probe-{uuid.uuid4().hex[:12]}" if probe
            else f"vm-{tier[0]}-{uuid.uuid4().hex[:12]}" if voicemail
            else f"callin-{seg}-{uuid.uuid4().hex[:12]}")
    identity = f"caller-{uuid.uuid4().hex[:8]}"

    token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name("caller")
        # The SDK default is six hours, which is six hours in which a minted
        # token can be replayed to open a fresh call — a new agent job, a new
        # LLM+TTS bill — without passing the door code or the usage limits
        # again, because those are only checked HERE. The widget joins within
        # a second or two of asking; two minutes is already generous.
        .with_ttl(TOKEN_TTL)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )

    import time as _time

    if not probe:
        now = _time.time()
        _recent_mints.append(now)
        _caller_last[_caller_key(request)] = now
        if not voicemail:
            _live_calls[room] = now
        ip = _caller_key(request)
        _mint_info[room] = {
            "client": _describe_client(request.headers.get("User-Agent", "")),
            "network": _network_of(ip),
            "ip": ip,
            # Which permissions this caller was actually given. Without it,
            # "why did the DJ refuse that?" has no answer in the transcript —
            # the settings say the permission is on, and the tier that decided
            # otherwise is not written down anywhere else.
            "tier": tier,
        }
        for stale in list(_mint_info)[:-_MINT_INFO_KEEP]:
            _mint_info.pop(stale, None)
        if not voicemail:
            # A live call is about to dispatch; the machine reads nothing at
            # pickup, so only calls earn the head start.
            _prefetch_station(cfg, tier)

    log.info(
        "minted %s token for room=%s identity=%s (%d live, %d this hour)",
        "probe" if probe else "call",
        room, identity, len(_live_calls), len(_recent_mints),
    )
    return _cors(
        request,
        web.json_response({"token": token, "url": LIVEKIT_PUBLIC_URL, "room": room}),
    )
