"""What the call card shows: who is on air, and what the widget may offer.

The answer is cached — see live_cache.py, which is also where every module
that stales it reaches. This one builds it.
"""

from __future__ import annotations

import logging
import re
import time

import httpx
from aiohttp import web

import admin_auth
import settings as settings_store
import station as station_mod
import tune_in
import voice_effects
from api.auth import _write_allowed, caller_tier
from api.env import LIVEKIT_PUBLIC_URL
from api.live_cache import _LIVE_TTL, _live_cache
# Re-exported: the look half moved to api/look.py at 0.10.131 and
# `from api.live import look_payload` is what every caller already
# says. A split should not break a caller that was right.
from api.look import (  # noqa: F401
    _STATION_TOKENS,
    call_button_label,
    card_identity,
    corner_controls,
    look_payload,
    station_palette,
)
from api.sounds import _resolved_sound
from api.stats import _listener_count
from api.wire import _cors
from brain.briefing import demojibake
from log_setup import describe
from station import StationClient
from version import APP_VERSION

log = logging.getLogger("callin.token")


def _num(v) -> float | None:
    """A number, or nothing. The station's now-playing block is assembled from
    tag data and a mixer, so a field that is usually an int is sometimes a
    string and occasionally absent — and `0` has to survive, which is why this
    is not a truthiness check."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n == n and n not in (float("inf"), float("-inf")) else None


# When this process started. The container-skew notice needs it: a call
# RECORD carries the version of the worker that answered it, which is evidence
# about the past, not about what is running now. A record written before this
# process booted says nothing about the worker it shares an image with — and
# saying it anyway is how an operator who has just pulled gets told their
# containers disagree by a transcript from before the upgrade (2026-08-16).
_STARTED_AT = time.time()


async def handle_health(request: web.Request) -> web.Response:
    from api.tokens import on_air_call_live

    return web.json_response(
        {"ok": True, "version": APP_VERSION, "livekit": LIVEKIT_PUBLIC_URL,
         "since": _STARTED_AT,
         # Whether a caller is live on the station's air right now — the
         # panel's dump control reads its state from here.
         "onAirLive": on_air_call_live()}
    )


# Whether the live-relay transport is actually there, cached briefly: /live
# is polled by every open widget, and the probe is a real TCP connect with a
# 2s timeout — unreachable (the normal case: no shared docker network) would
# otherwise cost every poll two seconds. The widget only offers the
# Live-on-air toggle when this says yes, so a dead mixer means no toggle
# rather than a caller choosing a door that dies mid-call.
_onair_probe = {"at": 0.0, "ok": False}
_ONAIR_PROBE_TTL = 30.0


async def _on_air_door(cfg: dict) -> dict:
    """The two on-air doors, answered separately: a live CALL needs the
    mixer's telnet (the relay pushes real clips), but an on-air VOICEMAIL
    does not — the studio's dj-reads backend airs over the plain admin API
    when the mixer is missing. Each door also rides its own dashboard quick
    kill, so the operator can stop one without touching the other."""
    tier = settings_store.normalise_tier(cfg.get("allow_on_air"))
    enabled = tier != settings_store.TIER_OFF
    reachable = False
    if enabled and cfg.get("on_air_calls_enabled", True):
        import asyncio

        from onair import chunks, transport

        now = time.time()
        if now - _onair_probe["at"] > _ONAIR_PROBE_TTL:
            # The WORKER's verdict first — it is the process that pushes,
            # and this process's own reachability says nothing about it
            # (separate containers, separate networks). Written at worker
            # prewarm and at every relay arm; only when the worker has not
            # spoken recently does this process probe for itself.
            verdict = chunks.mixer_verdict()
            if verdict is not None:
                _onair_probe["ok"] = bool(verdict["ok"])
            else:
                _onair_probe["ok"] = bool(
                    transport.air_base_url(cfg)
                    and await asyncio.to_thread(transport.mixer_reachable, cfg))
            _onair_probe["at"] = now
        reachable = _onair_probe["ok"]
    calls = (enabled and bool(cfg.get("on_air_calls_enabled", True))
             and reachable)
    voicemail = enabled and bool(cfg.get("on_air_voicemail_enabled", True))
    return {
        # Kept for older widgets: any door at all.
        "offered": calls or voicemail,
        "calls": calls,
        "voicemail": voicemail,
        # The tier the row is set to, so the card can explain a lock rather
        # than silently hiding the switch from a caller one code short.
        "tier": tier,
        # Live relay or tape — the stage message tells the caller which
        # promise they are accepting, because "live on air" and "airs after
        # you hang up" are different consents to give.
        "mode": ("after" if str(cfg.get("on_air_call_mode") or "live")
                 == "after" else "live"),
        # The quick kill's own state, so the panel can tell "the operator
        # closed this door" apart from "the mixer is unreachable" — calls
        # being false means either, and only one of them deserves a wiring
        # warning (the operator's ask, 2026-08-18).
        "enabled": bool(cfg.get("on_air_calls_enabled", True)),
    }


def _secure_origin() -> str:
    """Where the HTTPS front door lives, if one is configured. wss signalling
    and the https widget share an origin by design (the Caddyfile routes
    both), so the wss public URL doubles as the pointer."""
    if LIVEKIT_PUBLIC_URL.startswith("wss://"):
        return "https://" + LIVEKIT_PUBLIC_URL[len("wss://"):].split("/", 1)[0]
    return ""


async def handle_live_preview(request: web.Request) -> web.Response:
    """What the card would look like with these settings, without saving them.

    Admin only, and deliberately so: it takes an arbitrary settings patch and
    tells you what it resolves to. Nothing is written and no cache is stalled
    — the values are merged over the stored config in memory and thrown away.
    """
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))
    try:
        patch = await request.json()
    except Exception:
        return _cors(request, web.json_response({"error": "invalid JSON"}, status=400))
    if not isinstance(patch, dict):
        return _cors(request, web.json_response(
            {"error": "expected an object"}, status=400))

    cfg = dict(settings_store.load())
    # Only keys the settings store actually knows. An unknown key here would
    # be quietly previewed and then quietly dropped by save(), which is a
    # preview of something that can never be saved.
    cfg.update({k: v for k, v in patch.items() if k in settings_store.FIELDS})
    return _cors(request, web.json_response(
        look_payload(cfg, str(patch.get("_personaName") or ""))))


def _for_this_caller(request: web.Request, payload: dict) -> dict:
    """The one part of /live that cannot be shared between callers.

    Everything else here is the same for everybody and is cached for thirty
    seconds across the lot — but what a caller may ASK for now depends on what
    they typed to get in, and the "What can I ask?" list is the widget's
    promise about what will actually work. A cached list would promise an
    open caller the permissions an operator gave themselves, and the DJ would
    then refuse — which is the failure this list exists to prevent.

    Cheap: no station reads, just the door check again against the settings
    already in hand.
    """
    tier = caller_tier(request)
    out = dict(payload)
    # WHICH DOORS THIS CALLER CAN ACTUALLY OPEN, alongside the shared
    # payload's door states. The card used to offer every open door to every
    # caller and let the mint refuse: an operator signed out on their own
    # phone armed ON AIR, pressed Call in live, and got "This line can't put
    # callers on the air" with no path to the code — several times in one
    # evening (2026-08-18, rooms callin-o-*). A door the tier doesn't open
    # is not offered; the sign-in chip is the way to a bigger tier.
    # Copy-on-write on the nested dict: `out` is a shallow copy of a payload
    # cached across every caller, and writing into the shared onAirCalls
    # would leak one caller's verdict to everybody for thirty seconds.
    cfg_now = settings_store.load()
    doors = dict(out.get("onAirCalls") or {})
    doors["mine"] = settings_store.tier_reaches(
        cfg_now.get("allow_on_air"), tier)
    out["onAirCalls"] = doors
    out["voicemailMine"] = settings_store.tier_reaches(
        cfg_now.get("allow_voicemail"), tier)
    out["chatMine"] = settings_store.tier_reaches(
        cfg_now.get("allow_chat"), tier)
    if payload.get("canAsk") is None:
        return out                          # the help button is switched off
    out["canAsk"] = {
        k: settings_store.permission_reaches(v, tier)
        for k, v in (payload.get("askTiers") or {}).items()
    }
    out["callerTier"] = tier
    # Is there a tier ABOVE this caller that a code could actually reach? The
    # sign-in chip is pointless otherwise, so it only shows when signing in
    # would change something. The guest half of the offer exists only while
    # the line is code-gated: since 0.10.66 Guest code and Anyone are one
    # choice apiece, so on an open line the code does not elevate and
    # offering it would be a lie. The admin password is a door in every mode.
    import admin_auth

    # First-run: until an admin password (or the env break-glass) exists, the
    # CALL page offers to set one — same predicate as the panel's own nudge,
    # same trust model (an unconfigured box belongs to whoever reaches it
    # first), and per-request rather than cached so it vanishes the moment
    # the store holds a hash.
    from api.auth import _auth_configured
    out["needsSetup"] = not _auth_configured()

    admin_set = admin_auth.is_set()
    guest_set = admin_auth.guest_is_set()
    mode = str(settings_store.load().get("front_access") or "auto").lower()
    # Same rule as auth.caller_tier, and it has to stay the same rule — a
    # fourth spelling of it is how the card and the panel disagreed by
    # accident before. Admin-only has no guest; a code-gated door IS the guest
    # tier; an open line elevates only while `guest_tier` is on.
    guest_door = guest_set and (
        mode == "guest" or (mode != "admin"
                            and bool(settings_store.load().get("guest_tier", True))))
    out["signinAvailable"] = (
        (tier == "open" and (guest_door or admin_set))
        or (tier == "guest" and admin_set)
    )
    # THE SETTINGS GEAR IS FOR THE OPERATOR, and until 0.10.145 it was offered
    # to whoever loaded the page. Nothing leaked — every endpoint behind
    # /settings checks admin auth for itself, and the panel shows a locked gate
    # to anyone else — but a guest was being shown a door with their name not
    # on it, next to a sign-in chip and a sign-out lock, which is three
    # controls telling one person three different stories about who they are
    # (operator-reported).
    #
    # Per request rather than in the cached payload for the same reason as
    # signinAvailable: it depends on the X-Call-Key this caller sent, and the
    # rest of /live is shared across every caller for thirty seconds. The
    # operator's own switch still applies on top — this only ever SUBTRACTS.
    out["isAdmin"] = tier == "admin"
    # …and the GATE, which is not quite the same fact. Until an admin password
    # exists nobody can be admin, and hiding the gear then would leave a
    # first-run operator with no way to the panel from the card at all. Same
    # trust model as the setup nudge two lines up: an unconfigured box belongs
    # to whoever reaches it first, and it stops being one the moment a hash is
    # stored.
    out["canOpenSettings"] = out["isAdmin"] or out["needsSetup"]
    return out


async def handle_live(request: web.Request) -> web.Response:
    """Who's on air, proxied so the widget doesn't depend on the station
    sending CORS headers to whatever origin the widget is embedded on."""
    import time as _time

    if _live_cache["data"] is not None and _time.time() - _live_cache["at"] < _LIVE_TTL:
        return _cors(request, web.json_response(
            _for_this_caller(request, _live_cache["data"])))

    station = StationClient()
    try:
        health = await station.health()
        persona = await station.resolve_live_persona()
        now = await station.now_playing()
        show = await station.active_show(now)
        track = now.get("nowPlaying") or {}

        # "On air" needs to be a real, distinguishable state. Previously the
        # card just showed whatever came back, so a station that answered but
        # had nobody on air looked identical to one that was live.
        reachable = bool(health) or bool(now) or bool(persona.get("id"))
        on_air = reachable and bool(persona.get("id")) and persona["id"] != "default"

        cfg = settings_store.load()
        sound_pack = cfg.get("sound_pack") or "classic"
        # Resolved whenever the station will say, not only when the operator
        # chose station colours: the card's theme cycle offers the palette as
        # a VIEWER choice now, and a cycle entry that only exists on some
        # deployments has to know which ones. (It used to be gated on the
        # operator's colour setting to save a station read; the cycle is why
        # it no longer can be.)
        palette = None
        try:
            palette = station_palette(await station.themes())
        except Exception as e:
            # The card still has to paint. Falling back to the neutral
            # base is the honest answer for a station that will not say
            # what colour it is.
            log.info("station palette unavailable (%s)", describe(e))
        # Cached inside tune_in, so a station that publishes a mount list is
        # only asked for it every few minutes rather than on every /live.
        stream_url, stream_alternates = await tune_in.resolve(
            cfg, settings_store.station_base_url()
        )
        # The player's UP NEXT panel: the station's own queue snapshot, read
        # only when the player is switched on — the phone card shows none of
        # it, and a /state read per rebuild is not free on a rate-limited
        # station.
        up_next = []
        booth_line = None
        if cfg.get("swipe_player"):
            snap = await station.state()
            for item in (snap.get("upcoming") or [])[:6]:
                if item.get("title"):
                    up_next.append({
                        "title": item.get("title"),
                        "artist": item.get("artist") or None,
                        "requestedBy": item.get("requestedBy") or None,
                    })
            # IN THE BOOTH is the DJ's commentary on the RECORD — "this
            # track matches the rainy, late-night mood" — which the feed
            # carries as role dj + kind pick (operator's correction, checked
            # against their live station's own /session: the last-anything
            # rule was surfacing request banter instead). The newest dj turn
            # of any kind stands in when no pick has words yet.
            feed = await station.session_feed()
            msgs = feed.get("messages") or []

            def _booth(m):
                text = str(m.get("text") or "").strip()
                if not text:
                    return None
                return {"text": demojibake(text)[:280],
                        "kind": m.get("kind") or None}

            for m in reversed(msgs):
                if m.get("role") == "dj" and m.get("kind") == "pick":
                    booth_line = _booth(m)
                    if booth_line:
                        break
            if not booth_line:
                for m in reversed(msgs):
                    if m.get("role") == "dj":
                        booth_line = _booth(m)
                        if booth_line:
                            break
        # The header's weather readout, from the context the station already
        # sends with /now-playing — same source its own player reads.
        wx = (now.get("context") or {}).get("weather") or {}
        weather_line = (
            f"{wx['condition']} {wx['temp']}°{wx.get('tempUnit') or ''}"
            if wx.get("condition") not in (None, "", "unknown")
            and wx.get("temp") is not None else None)
        payload = (
                {
                    "reachable": reachable,
                    "onAir": on_air,
                    # Whether the caller must enter a code before the line
                    # opens. The card asks for it up front rather than letting
                    # them press Call and be refused.
                    # Derived from the policy, not from whether a password
                    # exists — the widget must gate on the same rule the
                    # server enforces, or the button lies.
                    "guestRequired": (
                        admin_auth.guest_is_set()
                        if str(cfg.get("front_access") or "auto").lower() == "auto"
                        else str(cfg.get("front_access")).lower() != "open"),
                    # The operator has closed the line; the card says so
                    # instead of offering a button that can't work.
                    "callsPaused": bool(cfg.get("calls_paused")),
                    # The answering-machine policy, so the card can offer
                    # "Leave a message" exactly where it paints a refusal.
                    "voicemailWhen": settings_store.voicemail_policy(cfg),
                    # Which door "Leave a message" opens: the classic machine
                    # (a LiveKit vm- room) or the soundbite studio (browser
                    # recording + review). The widget branches on this alone.
                    "voicemailFlow": str(cfg.get("voicemail_flow") or "machine"),
                    # The phone-in door: whether a Live-on-air toggle is worth
                    # offering at all (setting on AND mixer reachable), and at
                    # what tier. The mint still enforces the tier for real.
                    "onAirCalls": await _on_air_door(cfg),
                    # The widget's expiry maths stayed in minutes; only the SETTING
                    # moved to hours, so the wire stays compatible both ways.
                    "guestSessionMinutes":
                        int(cfg.get("guest_session_hours") or 0) * 60,
                    # True when several station reads in a row have failed —
                    # the card can say "station struggling" instead of the
                    # prompt just silently thinning.
                    "degraded": station_mod.degraded(),
                    # The TLS front door, derived from LIVEKIT_PUBLIC_URL.
                    # When this page is on plain http (where browsers refuse
                    # the microphone), the widget links the caller here
                    # instead of failing cryptically.
                    "secureOrigin": _secure_origin(),
                    # The station's own audio stream, so the caller can be
                    # counted as a listener while on the line.
                    "stream": {
                        # Derived only when nothing is configured, and that is
                        # correct on a plain-http LAN deployment alone: behind
                        # TLS the browser blocks an http stream as mixed
                        # content, silently, and the call runs with no station
                        # behind it. `alternates` are the station's other
                        # published mounts, for the widget to fall back to.
                        "url": stream_url,
                        "alternates": stream_alternates,
                        "tuneIn": bool(cfg.get("tune_in_on_call")),
                        "volume": (int(cfg.get("tune_in_volume") or 0)
                                   if cfg.get("tune_in_audible", True) else 0),
                    },
                    # Whether the card OFFERS the swipe-up station player.
                    # Only the operator's switch travels — the stream itself
                    # is the block above, and the widget also requires a
                    # resolved URL before it shows the gesture, so a switch
                    # flipped on with no reachable stream offers nothing.
                    "swipePlayer": bool(cfg.get("swipe_player")),
                    # How many are tuned in, from the same /now-playing
                    # context the station's own player reads (the listener
                    # sampler parses the identical shapes). None when the
                    # station won't say or the operator switched the row off
                    # — the widget only paints a number it was given.
                    "listeners": (
                        _listener_count(now)
                        if cfg.get("show_listener_count", True) else None),
                    # Whether the card offers the track heart — the same
                    # public /like any listener page sends, relayed by
                    # /player/like so LAN and mixed-content deployments work.
                    "cardLike": bool(cfg.get("show_track_like", True)),
                    # Which face the page opens on; the widget still requires
                    # the player to actually be offered before honouring it.
                    "playerStart": bool(cfg.get("start_on_player")),
                    # How loud the player stays under the studio. 0 is real
                    # (silent but running), so no `or` shorthand here.
                    "playerDuck": max(0, min(100, int(
                        cfg.get("vm_player_duck")
                        if cfg.get("vm_player_duck") is not None else 10))),
                    # The player's queue panel, booth line and header weather.
                    "upNext": up_next,
                    "booth": booth_line,
                    "weather": weather_line,
                    # Everything that is a look rather than a fact — the theme,
                    # the corner controls, which lines of the who's-on-air
                    # block each surface paints, the photo's shape, the Call
                    # button's label. All of it via look_payload, because the
                    # panel's live preview resolves the very same settings
                    # through the very same function; a preview that disagrees
                    # with the card is worse than no preview.
                    #
                    # Both surfaces' answers go out because /live is cached
                    # across every caller and cannot know which one is asking;
                    # the widget picks by whether it is in a frame.
                    **look_payload(cfg, persona.get("name")),
                    # This persona's own colour, when the operator gave it
                    # one — a per-DJ costume outranks the shared pick, the
                    # same override direction the greeting overrides take.
                    **({"voiceEffect": fx} if (fx := voice_effects.effect_for(
                        persona.get("id") or "")) else {}),
                    # The on-air show's own palette, already translated into
                    # this widget's token names, when "the station's own
                    # colours" is the choice. Null every other time, including
                    # when the station could not be asked. Not in look_payload:
                    # it comes from the STATION, not from settings.
                    "stationTheme": palette,
                    # What a caller may actually ask for. Sent only when the
                    # operator has switched the help button on, and only as
                    # the permissions themselves — the wording lives in the
                    # widget, so the panel and the card cannot drift.
                    # Every permission the shared ASKS list gates on. A gate
                    # missing from here reads as `undefined` in the widget and
                    # the example is filtered out — so a caller is never told
                    # about something the operator switched on. That is how
                    # skip and the programme beat stayed invisible on the card
                    # after they shipped as permissions.
                    # Two shapes on purpose. `askTiers` is the raw setting per
                    # permission and is what gets cached; `canAsk` is that
                    # collapsed for the caller actually asking, and is
                    # rewritten on every request by _for_this_caller. The
                    # widget only ever reads canAsk.
                    "askTiers": (
                        {k: cfg.get(k) for k in settings_store.TIERED_PERMISSIONS}
                        if cfg.get("show_caller_help") else None
                    ),
                    "canAsk": (
                        {} if cfg.get("show_caller_help") else None
                    ),
                    # So the card can show elapsed/remaining and warn before
                    # the graceful cutoff rather than surprising the caller.
                    "limits": {
                        "maxCallSeconds": int(cfg.get("max_call_seconds") or 0),
                        "idlePromptSecs": int(cfg.get("idle_prompt_secs") or 0),
                        # The machine's own clock — a voicemail's timer must
                        # count against this, not the live call's: "/ 10:00"
                        # on a 30-second machine read as the ceiling being
                        # ignored.
                        "voicemailMaxSeconds":
                            max(5, int(cfg.get("voicemail_max_seconds") or 30)),
                    },
                    # Per sound: what the operator configured, else whatever
                    # the chosen pack bundles, else "" — which the widget
                    # reads as "synthesize it", the behaviour it has always
                    # had when nothing is set.
                    "sounds": {
                        "enabled": bool(cfg.get("call_sounds")),
                        # Whether the ring yields (soft fade) at pickup —
                        # the widget's engine reads it as cutRing:false to
                        # keep the old let-it-finish behaviour.
                        "cutRing": bool(cfg.get("ring_cut_at_pickup", True)),
                        "pack": sound_pack,
                        "ring": _resolved_sound(cfg, sound_pack, "ring"),
                        "pickup": _resolved_sound(cfg, sound_pack, "pickup"),
                        "hold": _resolved_sound(cfg, sound_pack, "hold"),
                        "hangup": _resolved_sound(cfg, sound_pack, "hangup"),
                        "failed": _resolved_sound(cfg, sound_pack, "failed"),
                        "volume": int(cfg.get("call_volume") or 100),
                    },
                    "name": persona["name"],
                    "tagline": persona["tagline"],
                    "personaId": persona["id"],
                    # Served through our own origin so the widget works when
                    # embedded on an https page and off-LAN.
                    "avatar": f"/avatar/{persona['id']}" if persona["id"] else None,
                    "show": demojibake(show.get("name", "")) or None,
                    "track": (
                        f"{track.get('title')} — {track.get('artist')}"
                        if track.get("title")
                        else None
                    ),
                    # The record as STRUCTURE, for the station player's sheet —
                    # the flat `track` string above stays for the who-row. All
                    # of it is already in the station's /now-playing answer
                    # (the same analysis strip its own player renders: genre ·
                    # BPM · key · mood); this only forwards it. Art goes
                    # through our own /cover proxy for the same reason the
                    # avatar does — the station may be unreachable or plain
                    # http from the caller's browser.
                    "nowPlaying": {
                        "title": track.get("title") or None,
                        "artist": track.get("artist") or None,
                        "album": track.get("album") or None,
                        "year": track.get("year"),
                        "genres": [g for g in (track.get("genres") or []) if g][:4],
                        "bpm": _num(track.get("bpm")),
                        "key": track.get("musicalKey") or None,
                        "moods": [m for m in (track.get("moods") or []) if m][:3],
                        "art": (f"/cover/{track['subsonic_id']}"
                                if track.get("subsonic_id") else None),
                    },
                    # When the record started and how long it runs, so the
                    # now-playing rail can show elapsed and a progress
                    # hairline. Sent as the START INSTANT rather than as an
                    # elapsed figure on purpose: /live is cached across every
                    # caller for a few seconds, so an elapsed number baked in
                    # here would be stale by up to that much and would jump
                    # backwards on the next poll. From a fixed start the
                    # widget counts locally and the poll only ever corrects
                    # which record it is counting.
                    "trackStartedAt": _num(track.get("timestamp")),
                    "trackSeconds": _num(track.get("duration")),
                }
            )
        if reachable:
            _live_cache["data"] = payload
            _live_cache["at"] = _time.time()
        return _cors(request, web.json_response(_for_this_caller(request, payload)))
    finally:
        await station.aclose()


async def handle_avatar(request: web.Request) -> web.StreamResponse:
    """Proxy the station's persona avatar (it lives off /api, at
    /persona-avatar/{id}) so the widget never has to reach the station
    directly from the browser."""
    # Quoted: this arrives from the browser, and an unescaped path segment can
    # walk the request somewhere other than the avatar endpoint.
    from urllib.parse import quote

    persona_id = request.match_info["persona_id"]
    root = settings_store.station_base_url()
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{root}/persona-avatar/{quote(persona_id, safe='')}")
            r.raise_for_status()
            return web.Response(
                body=r.content,
                content_type=r.headers.get("content-type", "image/png").split(";")[0],
                headers={"Cache-Control": "public, max-age=300"},
            )
    except Exception as e:
        log.info("avatar fetch failed for %s: %s", persona_id, describe(e))
        raise web.HTTPNotFound()


async def handle_cover(request: web.Request) -> web.StreamResponse:
    """Proxy the station's album art (/cover/{id}) for the same reason the
    avatar is proxied: the caller's browser may not be able to reach the
    station at all, and behind TLS a plain-http image is blocked as mixed
    content. A day, not five minutes: the station itself calls a song's art
    immutable-at-the-edge."""
    from urllib.parse import quote

    track_id = request.match_info["track_id"]
    root = settings_store.station_base_url()
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{root}/cover/{quote(track_id, safe='')}")
            r.raise_for_status()
            return web.Response(
                body=r.content,
                content_type=r.headers.get("content-type", "image/jpeg").split(";")[0],
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except Exception as e:
        log.info("cover fetch failed for %s: %s", track_id, describe(e))
        raise web.HTTPNotFound()
