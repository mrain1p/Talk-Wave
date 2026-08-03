"""
Token mint + widget host.

The browser must never see LIVEKIT_API_SECRET, so joining is brokered here:
the widget POSTs to /token, this signs a short-lived join grant scoped to one
freshly-named room, and returns it along with the public LiveKit URL.

Also serves web-widget/ so the call page and embed.js have a real origin.

Run: python token_server.py   (or via run-local.ps1)
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
import uuid
from pathlib import Path

import httpx
from aiohttp import web
from dotenv import load_dotenv
from livekit import api

import admin_auth
import secrets_store
import settings as settings_store
import station as station_mod
from prompts import _demojibake
from station import StationClient
from station_config import StationConfig

# Reported on /health so a deployed instance can say what it is. Keep in
# step with the git tag (v0.9.0 -> "0.9.0") when cutting a release.
APP_VERSION = "0.9.16"

load_dotenv(Path(__file__).parent.parent / ".env")

import log_setup

log_setup.setup("token-server")
log = logging.getLogger("callin.token")

WIDGET_DIR = Path(
    os.environ.get("WIDGET_DIR", Path(__file__).parent.parent / "web-widget")
)
PORT = int(os.environ.get("TOKEN_SERVER_PORT", "8100"))

# Persona avatars live under /api, same as everything else — the `avatar`
# field in GET /dj is "/persona-avatar/{id}", relative to the API base.

LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "")
# What the *browser* connects to — not the internal docker hostname.
LIVEKIT_PUBLIC_URL = os.environ.get("LIVEKIT_PUBLIC_URL", "ws://localhost:7880")

# Which origins may embed / call the token endpoint. "*" is fine for local dev;
# set CALLIN_ALLOWED_ORIGINS to a comma-separated list before exposing this.
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("CALLIN_ALLOWED_ORIGINS", "*").split(",") if o.strip()
]


def _cors(request: web.Request, resp: web.StreamResponse) -> web.StreamResponse:
    origin = request.headers.get("Origin", "")
    if "*" in ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin or "*"
    elif origin in ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return resp


async def handle_options(request: web.Request) -> web.Response:
    return _cors(request, web.Response(status=204))


# --- usage controls -------------------------------------------------------
# Minting a token starts a call, and a call costs LLM + TTS + STT on every
# turn. Without a ceiling, a single open tab (or a bored visitor) can loop
# this endpoint and run up real money. Limits are generous by default: the
# point is to stop runaway use, not to ration callers.
_recent_mints: list[float] = []          # timestamps, global
_caller_last: dict[str, float] = {}      # caller key -> last mint
_live_calls: dict[str, float] = {}       # room -> started at

_CALL_ASSUMED_MAX = 1800.0               # forget a room after 30 min


def _caller_key(request: web.Request) -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote or "unknown"


def _check_usage(request: web.Request, cfg: dict) -> str | None:
    """Returns a caller-facing reason to refuse, or None to allow.

    Wording is deliberately in-world — someone pressing Call shouldn't be told
    about rate limits.
    """
    import time as _time

    now = _time.time()

    # Expire anything stale before deciding.
    cutoff = now - 3600
    _recent_mints[:] = [t for t in _recent_mints if t > cutoff]
    for room, started in list(_live_calls.items()):
        if now - started > _CALL_ASSUMED_MAX:
            _live_calls.pop(room, None)
    # A cooldown older than the longest configurable wait can never matter
    # again; without this the per-caller map grows by one entry per IP forever.
    for key, at in list(_caller_last.items()):
        if now - at > 3600:
            _caller_last.pop(key, None)

    concurrent = int(cfg.get("max_concurrent_calls") or 0)
    if concurrent > 0 and len(_live_calls) >= concurrent:
        return "All the lines are busy right now — give it a minute and try again."

    per_hour = int(cfg.get("calls_per_hour") or 0)
    if per_hour > 0 and len(_recent_mints) >= per_hour:
        return "The phones have been busy this hour. Try again a little later."

    cooldown = int(cfg.get("caller_cooldown_secs") or 0)
    if cooldown > 0:
        last = _caller_last.get(_caller_key(request))
        if last and (now - last) < cooldown:
            wait = int(cooldown - (now - last))
            return f"You've only just hung up — give it {wait}s before calling back."

    return None


async def handle_call_ended(request: web.Request) -> web.Response:
    """The widget reports a hangup so a finished call stops counting against
    the concurrency limit immediately, rather than aging out."""
    try:
        body = await request.json()
        _live_calls.pop(str(body.get("room", "")), None)
    except Exception:
        pass
    return _cors(request, web.json_response({"ok": True}))


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
    if probe and not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))

    cfg = settings_store.load()
    if not probe:
        refusal = _check_usage(request, cfg)
        if refusal:
            log.info("call refused by usage controls: %s", refusal)
            return _cors(request, web.json_response({"error": refusal, "busy": True}, status=429))

    # One room per call keeps callers from ever landing in each other's audio.
    room = f"{'probe' if probe else 'callin'}-{uuid.uuid4().hex[:12]}"
    identity = f"caller-{uuid.uuid4().hex[:8]}"

    token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name("caller")
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
        _live_calls[room] = now

    log.info(
        "minted %s token for room=%s identity=%s (%d live, %d this hour)",
        "probe" if probe else "call",
        room, identity, len(_live_calls), len(_recent_mints),
    )
    return _cors(
        request,
        web.json_response({"token": token, "url": LIVEKIT_PUBLIC_URL, "room": room}),
    )


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response(
        {"ok": True, "version": APP_VERSION, "livekit": LIVEKIT_PUBLIC_URL}
    )


# One /live answer serves every open page for a few seconds. Each build of
# this payload fans out into 4-6 station reads, and the widget polls every
# 20s — without a cache, a dashboard left open works the station ~15x harder
# than it needs to. Settings saves clear it so changes still show immediately.
_live_cache: dict = {"at": 0.0, "data": None}
# 30s: comfortably above the widget's 20s poll, so an open page costs the
# station roughly one sweep per 40s instead of one per poll. Now-playing on
# the card may lag by up to ~30s, which is fine for a status line.
_LIVE_TTL = 30.0


def _secure_origin() -> str:
    """Where the HTTPS front door lives, if one is configured. wss signalling
    and the https widget share an origin by design (the Caddyfile routes
    both), so the wss public URL doubles as the pointer."""
    if LIVEKIT_PUBLIC_URL.startswith("wss://"):
        return "https://" + LIVEKIT_PUBLIC_URL[len("wss://"):].split("/", 1)[0]
    return ""


async def handle_live(request: web.Request) -> web.Response:
    """Who's on air, proxied so the widget doesn't depend on the station
    sending CORS headers to whatever origin the widget is embedded on."""
    import time as _time

    if _live_cache["data"] is not None and _time.time() - _live_cache["at"] < _LIVE_TTL:
        return _cors(request, web.json_response(_live_cache["data"]))

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
        payload = (
                {
                    "reachable": reachable,
                    "onAir": on_air,
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
                        "url": settings_store.station_base_url().rsplit("/api", 1)[0]
                               + "/stream.mp3",
                        "tuneIn": bool(cfg.get("tune_in_on_call")),
                        "volume": int(cfg.get("tune_in_volume") or 0),
                    },
                    # So the card can show elapsed/remaining and warn before
                    # the graceful cutoff rather than surprising the caller.
                    "limits": {
                        "maxCallSeconds": int(cfg.get("max_call_seconds") or 0),
                        "idlePromptSecs": int(cfg.get("idle_prompt_secs") or 0),
                    },
                    "sounds": {
                        "enabled": bool(cfg.get("call_sounds")),
                        "ring": cfg.get("sound_ring") or "",
                        "pickup": cfg.get("sound_pickup") or "",
                        "hangup": cfg.get("sound_hangup") or "",
                        "volume": int(cfg.get("call_volume") or 100),
                    },
                    "name": persona["name"],
                    "tagline": persona["tagline"],
                    "personaId": persona["id"],
                    # Served through our own origin so the widget works when
                    # embedded on an https page and off-LAN.
                    "avatar": f"/avatar/{persona['id']}" if persona["id"] else None,
                    "show": _demojibake(show.get("name", "")) or None,
                    "track": (
                        f"{track.get('title')} — {track.get('artist')}"
                        if track.get("title")
                        else None
                    ),
                }
            )
        if reachable:
            _live_cache["data"] = payload
            _live_cache["at"] = _time.time()
        return _cors(request, web.json_response(payload))
    finally:
        await station.aclose()


async def handle_avatar(request: web.Request) -> web.StreamResponse:
    """Proxy the station's persona avatar (it lives off /api, at
    /persona-avatar/{id}) so the widget never has to reach the station
    directly from the browser."""
    persona_id = request.match_info["persona_id"]
    root = settings_store.station_base_url()
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{root}/persona-avatar/{persona_id}")
            r.raise_for_status()
            return web.Response(
                body=r.content,
                content_type=r.headers.get("content-type", "image/png").split(";")[0],
                headers={"Cache-Control": "public, max-age=300"},
            )
    except Exception as e:
        log.info("avatar fetch failed for %s: %s", persona_id, e)
        raise web.HTTPNotFound()


async def handle_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WIDGET_DIR / "index.html")


# --- settings -------------------------------------------------------------
# The settings surface is deliberately NOT part of the embeddable widget: the
# compact/iframe view never renders it, and writes can be locked down with
# CALLIN_ADMIN_KEY once this is reachable beyond localhost.

ADMIN_KEY = os.environ.get("CALLIN_ADMIN_KEY", "")

# --- panel password + brute-force lockout ---------------------------------
# 5 wrong passwords from one address -> 5-minute cooldown; a second full
# round of failures bans the address until the app restarts. In-memory on
# purpose: `docker restart` (or any redeploy) is the un-ban, and setting
# CALLIN_ADMIN_KEY in the environment is the break-glass password.
_AUTH_MAX_FAILS = 5
_AUTH_COOLDOWN_SECS = 300.0
_AUTH_STRIKES_TO_BAN = 2
_auth_state: dict[str, dict] = {}   # ip -> {fails, strikes, cooldown_until, banned}


def _auth_configured() -> bool:
    return bool(ADMIN_KEY) or admin_auth.is_set()


def _auth_gate(ip: str) -> str | None:
    """A reason this address may not even ATTEMPT a password, or None."""
    st = _auth_state.get(ip)
    if not st:
        return None
    if st.get("banned"):
        return ("too many failed attempts — this address is blocked until "
                "the app restarts")
    wait = st.get("cooldown_until", 0) - time.time()
    if wait > 0:
        return f"too many failed attempts — try again in {int(wait) + 1}s"
    return None


def _auth_fail(ip: str) -> str:
    """Record a wrong password; returns the caller-facing message."""
    st = _auth_state.setdefault(ip, {"fails": 0, "strikes": 0})
    st["fails"] += 1
    if st["fails"] >= _AUTH_MAX_FAILS:
        st["fails"] = 0
        st["strikes"] = st.get("strikes", 0) + 1
        if st["strikes"] >= _AUTH_STRIKES_TO_BAN:
            st["banned"] = True
            log.warning("auth: %s banned until restart after repeated failures", ip)
            return ("too many failed attempts — this address is blocked until "
                    "the app restarts")
        st["cooldown_until"] = time.time() + _AUTH_COOLDOWN_SECS
        log.warning("auth: %s in cooldown after %d failures", ip, _AUTH_MAX_FAILS)
        return f"too many failed attempts — try again in {int(_AUTH_COOLDOWN_SECS)}s"
    left = _AUTH_MAX_FAILS - st["fails"]
    return f"wrong password ({left} tr{'y' if left == 1 else 'ies'} left before a cooldown)"


def _auth_clear(ip: str) -> None:
    _auth_state.pop(ip, None)


def _key_valid(key: str) -> bool:
    if not key:
        return False
    if ADMIN_KEY and hmac.compare_digest(key, ADMIN_KEY):
        return True
    return admin_auth.verify(key)


def _check_admin(request: web.Request) -> bool:
    """Password check with lockout, once a password is configured. Stores a
    caller-facing reason on the request for the shared 401 payload."""
    ip = _caller_key(request)
    gate = _auth_gate(ip)
    if gate:
        request["auth_error"] = gate
        request["auth_required"] = True
        return False
    key = request.headers.get("X-Admin-Key", "")
    if _key_valid(key):
        _auth_clear(ip)
        return True
    # An absent key is a login prompt, not a brute-force attempt — only a
    # WRONG key counts toward the lockout.
    request["auth_error"] = _auth_fail(ip) if key else "password required"
    request["auth_required"] = True
    return False


def _write_allowed(request: web.Request) -> bool:
    """Gate for the panel: anything that reads config, changes state or
    costs money.

    With a password (or CALLIN_ADMIN_KEY) configured, the password decides —
    with per-IP lockout. Without one (first-run), refuse cross-site browser
    calls: any random web page you visit can POST to localhost, and browsers
    always attach an Origin header to cross-origin requests — so a foreign
    origin is the tell. Same-origin pages and plain tools like curl (no
    Origin) stay allowed.
    """
    if _auth_configured():
        return _check_admin(request)

    origin = request.headers.get("Origin", "")
    if not origin:
        return True
    from urllib.parse import urlparse

    parsed = urlparse(origin)
    if parsed.netloc == request.host:
        return True
    if parsed.hostname in ("localhost", "127.0.0.1", "::1"):
        return True
    log.warning("blocked cross-origin write from %s", origin)
    request["auth_error"] = "cross-origin request blocked"
    return False


async def handle_get_settings(request: web.Request) -> web.Response:
    # Config is operator-only once a password exists; before one is set
    # (first-run) it stays open so the panel can render and nudge.
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))
    # `secrets` is status only — set/unset, source, masked tail. Key material
    # never travels back to the browser.
    return _cors(
        request,
        web.json_response(
            {
                "resolved": settings_store.load(),
                "overrides": settings_store.stored_only(),
                "secrets": secrets_store.status(),
                # Field metadata (labels, help, grouping, dependencies) so the
                # page doesn't keep its own parallel copy of the schema.
                "schema": settings_store.schema_payload(),
                "authConfigured": _auth_configured(),
            }
        ),
    )


async def handle_set_password(request: web.Request) -> web.Response:
    """Set or change the panel password. First-run set is open (same-origin
    only); changing requires the current password, with the same lockout as
    every other attempt."""
    try:
        body = await request.json()
    except Exception:
        return _cors(request, web.json_response({"error": "invalid JSON"}, status=400))

    new = str((body or {}).get("new") or "")
    if len(new) < 8:
        return _cors(request, web.json_response(
            {"error": "use at least 8 characters"}, status=400))

    if _auth_configured():
        ip = _caller_key(request)
        gate = _auth_gate(ip)
        if gate:
            return _cors(request, web.json_response(
                {"error": gate, "authRequired": True}, status=401))
        current = str((body or {}).get("current")
                      or request.headers.get("X-Admin-Key", ""))
        if not _key_valid(current):
            err = _auth_fail(ip) if current else "current password required"
            return _cors(request, web.json_response(
                {"error": err, "authRequired": True}, status=401))
        _auth_clear(ip)
    elif not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed"}, status=401))

    admin_auth.set_password(new)
    return _cors(request, web.json_response({"ok": True}))


async def handle_post_secrets(request: web.Request) -> web.Response:
    """Set or clear API keys. Blank values mean 'unchanged' — the panel shows
    masked placeholders, so an untouched field must not wipe a working key."""
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))
    try:
        body = await request.json()
    except Exception:
        return _cors(request, web.json_response({"error": "invalid JSON"}, status=400))

    values = body.get("set") or {}
    clear = body.get("clear") or []
    if not isinstance(values, dict) or not isinstance(clear, list):
        return _cors(request, web.json_response({"error": "bad shape"}, status=400))

    status = secrets_store.save(values, clear)
    _options_cache["data"] = None  # admin creds may change voice mirroring
    # Admin credentials usually arrive through this endpoint, and webhook
    # registration needs them — try again now rather than waiting for a
    # restart.
    if not _hook_state.get("registered"):
        _hook_state.pop("gave_up", None)   # new credentials earn a fresh attempt
        _hook_state.pop("attempts", None)
        asyncio.create_task(register_station_webhook())
    return _cors(request, web.json_response({"secrets": status}))


async def handle_post_settings(request: web.Request) -> web.Response:
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
        return _cors(request, web.json_response({"error": "expected an object"}, status=400))

    resolved = settings_store.save(patch)
    _live_cache["data"] = None
    # Changing the station URL (or the TTS URL) changes what the options
    # payload should contain — without this, re-homing the sidecar left the
    # panel showing the previous station's personas for up to the cache TTL.
    _options_cache["data"] = None
    return _cors(request, web.json_response({"resolved": resolved}))


async def _tts_voices(base_url: str) -> list[str]:
    """Ask the configured TTS server what voices it actually has. For an
    OpenAI-compatible endpoint that's /v1/audio/voices; the public OpenAI API
    has no such route, so fall back to the known stock list."""
    if not base_url:
        return settings_store.OPENAI_VOICES
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=6.0) as c:
            r = await c.get("/v1/audio/voices")
            r.raise_for_status()
            data = r.json()
            voices = [v.get("id") for v in data.get("data", []) if v.get("id")]
            if voices:
                return sorted(voices)
    except Exception as e:
        log.info("voice list unavailable from %s (%s) — using stock list", base_url, e)
    return settings_store.OPENAI_VOICES


async def _openai_models(api_key: str, base_url: str = "") -> list[str]:
    if not api_key:
        return []
    root = (base_url or "https://api.openai.com").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{root}/v1/models", headers={"Authorization": f"Bearer {api_key}"})
            r.raise_for_status()
            ids = [m["id"] for m in r.json().get("data", []) if m.get("id")]
            # Chat-capable families only; the raw list includes embeddings,
            # audio, moderation and image models.
            return sorted(
                i for i in ids
                if i.startswith(("gpt-", "o1", "o3", "o4", "chatgpt"))
                and not any(x in i for x in ("audio", "realtime", "transcribe", "tts", "image"))
            )
    except Exception as e:
        log.info("openai model list unavailable (%s)", e)
        return []


async def _google_models(api_key: str) -> list[str]:
    if not api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": api_key, "pageSize": 200},
            )
            r.raise_for_status()
            # generateContent alone isn't enough — the catalogue includes image,
            # speech, robotics and research models that can't hold a voice
            # conversation. Keep text chat families only.
            skip = (
                "image", "tts", "audio", "robotics", "lyria", "veo", "imagen",
                "banana", "computer-use", "deep-research", "embedding", "aqa",
                "learnlm", "guard",
            )
            out = []
            for m in r.json().get("models", []):
                name = (m.get("name") or "").removeprefix("models/")
                if not name or not name.startswith(("gemini", "gemma")):
                    continue
                if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                    continue
                if any(s in name for s in skip):
                    continue
                out.append(name)
            return sorted(set(out))
    except Exception as e:
        log.info("google model list unavailable (%s)", e)
        return []


async def _anthropic_models(api_key: str) -> list[str]:
    if not api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                params={"limit": 100},
            )
            r.raise_for_status()
            return sorted(m["id"] for m in r.json().get("data", []) if m.get("id"))
    except Exception as e:
        log.info("anthropic model list unavailable (%s)", e)
        return []


async def _openrouter_models(api_key: str = "") -> list[str]:
    """OpenRouter's catalogue is public, so this works before a key is set.
    Trimmed to tool-capable chat models — a voice DJ that can't call tools is
    useless here, and the raw list is ~340 entries deep."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get("https://openrouter.ai/api/v1/models")
            r.raise_for_status()
            out = []
            for m in r.json().get("data", []):
                mid = m.get("id")
                if not mid:
                    continue
                params = m.get("supported_parameters") or []
                if params and "tools" not in params:
                    continue
                out.append(mid)
            return sorted(out)
    except Exception as e:
        log.info("openrouter model list unavailable (%s)", e)
        return []


async def _ollama_models(base_url: str) -> list[str]:
    """Whatever is actually pulled on that Ollama box. Far more useful than a
    hardcoded list — it's where the station's own DJ model shows up."""
    root = (base_url or settings_store.PROVIDER_BASE_URLS["ollama"]).rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.get(f"{root}/api/tags")
            r.raise_for_status()
            return sorted(m["name"] for m in r.json().get("models", []) if m.get("name"))
    except Exception as e:
        log.info("ollama model list unavailable at %s (%s)", root, e)
        return []


# Building the options payload means round-tripping the station, the TTS
# server and Ollama — a couple of seconds even in parallel. Cache it briefly so
# reopening the panel is instant; any explicit override or ?fresh=1 bypasses.
_OPTIONS_TTL = 60.0
_options_cache: dict = {"at": 0.0, "data": None}


async def handle_settings_options(request: web.Request) -> web.Response:
    """Everything the settings UI needs to populate its dropdowns, read live
    rather than hardcoded in the page."""
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))
    import time as _time
    secrets_store.apply_to_env()

    overridden = any(
        request.query.get(k)
        for k in ("tts_base_url", "llm_base_url", "station_base_url", "fresh")
    )
    if not overridden and _options_cache["data"] is not None:
        if _time.time() - _options_cache["at"] < _OPTIONS_TTL:
            return _cors(request, web.json_response(_options_cache["data"]))

    cfg = settings_store.load()
    # Let the panel preview a URL it hasn't saved yet.
    for key in ("tts_base_url", "llm_base_url", "station_base_url"):
        if request.query.get(key):
            cfg[key] = request.query[key]

    station = StationClient(base_url=cfg.get("station_base_url"))
    station_cfg = StationConfig(base_url=cfg.get("station_base_url"))
    try:
        # These four hit four different hosts; serially they add up to several
        # seconds of staring at an empty settings panel.
        # Model lists are discovered from each provider rather than hardcoded —
        # a baked-in list goes stale and produces 404s on retired models.
        (
            personas_raw, voice_source, voices,
            ollama, openai_m, openrouter_m, google_m, anthropic_m, station_llm,
        ) = await asyncio.gather(
            station.personas(),
            station_cfg.voice_source(),
            _tts_voices(cfg.get("tts_base_url", "")),
            _ollama_models(cfg.get("llm_base_url", "")),
            _openai_models(secrets_store.get("openai_api_key")),
            _openrouter_models(),
            _google_models(secrets_store.get("google_api_key")),
            _anthropic_models(secrets_store.get("anthropic_api_key")),
            station_cfg.llm_config(),
        )
    finally:
        await station.aclose()
        await station_cfg.aclose()

    personas = [{"id": p.get("id"), "name": p.get("name")} for p in personas_raw if p.get("id")]

    adapters = sorted(
        p.name for p in (Path(__file__).parent / "tts-adapters").glob("*.json")
        if not p.name.startswith("_")
    )

    # Discovered lists win; the curated ones are only a fallback for when a key
    # isn't set yet (or the provider's listing endpoint is unreachable).
    models = dict(settings_store.MODEL_CHOICES)
    models["ollama"] = ollama or models["ollama"]
    models["openai"] = openai_m or models["openai"]
    models["openrouter"] = openrouter_m or models["openrouter"]
    models["google"] = google_m or models["google"]
    models["anthropic"] = anthropic_m or models["anthropic"]

    discovered = {
        "openai": bool(openai_m),
        "openrouter": bool(openrouter_m),
        "google": bool(google_m),
        "anthropic": bool(anthropic_m),
        "ollama": bool(ollama),
    }

    payload = {
        "llmProviders": list(models),
        "llmModels": models,
        "providerBaseUrls": settings_store.PROVIDER_BASE_URLS,
        "ttsBaseUrls": settings_store.TTS_BASE_URLS,
        "sttProviders": list(settings_store.STT_MODEL_CHOICES),
        "sttModels": settings_store.STT_MODEL_CHOICES,
        "ttsModes": ["cloud", "local"],
        "ttsAdapters": adapters,
        "voices": voices,
        "personas": personas,
        "voiceSource": voice_source,
        "modelsDiscovered": discovered,
        "stationLlm": station_llm,
    }

    if not overridden:
        _options_cache["at"] = _time.time()
        _options_cache["data"] = payload

    return _cors(request, web.json_response(payload))


# --- test endpoints -------------------------------------------------------
# These exercise the same code paths a real call uses, so a green result here
# means the call will work rather than "the URL responded".

TEST_LINE = "You're on the air. What are we playing tonight?"


async def handle_test_tts(request: web.Request) -> web.Response:
    """Synthesize one line and report whether the backend can keep up with a
    live call. The realtime factor is the number that matters: above 1.0 the
    buffer starves and playback gaps."""
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))
    secrets_store.apply_to_env()

    body = await request.json() if request.can_read_body else {}
    cfg = settings_store.load()
    cfg.update({k: v for k, v in body.items() if v not in (None, "")})

    import base64 as _b64
    import time as _time

    from tts_adapter import AdapterTTS

    os.environ["TTS_MODE"] = str(cfg.get("tts_mode", "cloud"))
    adapter_path = cfg.get("tts_adapter") or None
    if adapter_path and not os.path.isabs(adapter_path):
        candidate = Path(__file__).parent / "tts-adapters" / adapter_path
        adapter_path = str(candidate) if candidate.exists() else None

    voice = cfg.get("tts_voice") or ""
    if not voice:
        sc = StationConfig()
        try:
            st = StationClient()
            try:
                persona = await st.resolve_live_persona()
            finally:
                await st.aclose()
            voice = await sc.voice_for(persona["id"])
        finally:
            await sc.aclose()

    tts = None
    try:
        tts = AdapterTTS(
            voice=voice,
            base_url=cfg.get("tts_base_url") or "",
            api_key=os.environ.get("TTS_API_KEY", ""),
            adapter_path=adapter_path,
            model=cfg.get("tts_model") or "",
        )
        t0 = _time.perf_counter()
        first = None
        pcm = bytearray()
        stream = tts.synthesize(body.get("text") or TEST_LINE)
        async for ev in stream:
            if first is None:
                first = _time.perf_counter() - t0
            pcm.extend(ev.frame.data.tobytes())
        await stream.aclose()

        wall = _time.perf_counter() - t0
        audio_sec = len(pcm) / 2 / tts.sample_rate if tts.sample_rate else 0
        return _cors(
            request,
            web.json_response(
                {
                    "ok": True,
                    "voice": voice,
                    "firstAudioMs": round((first or 0) * 1000),
                    "wallMs": round(wall * 1000),
                    "audioSec": round(audio_sec, 2),
                    "realtimeFactor": round(wall / audio_sec, 2) if audio_sec else None,
                    "sampleRate": tts.sample_rate,
                    "pcmBase64": _b64.b64encode(bytes(pcm)).decode(),
                }
            ),
        )
    except Exception as e:
        msg = str(e)
        # By far the most common failure: a voice id from one backend sent to
        # the other (stock cloud names vs the local sample registry).
        if "400" in msg and voice:
            msg += (
                f"\n\nThe backend rejected voice '{voice}'. Cloud voices "
                f"(alloy, onyx, …) and local sample ids (-Trevor2, -Delia1, …) "
                f"are not interchangeable — reload the voice list after "
                f"switching backend."
            )
        return _cors(request, web.json_response({"ok": False, "voice": voice, "error": msg}))
    finally:
        if tts is not None:
            await tts.aclose()


async def handle_test_llm(request: web.Request) -> web.Response:
    """One short completion, with a tool offered. Reports whether the model
    actually emits a tool call — plenty of local models answer fluently but
    never call tools, which shows up as a DJ that never submits a request."""
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))
    secrets_store.apply_to_env()

    body = await request.json() if request.can_read_body else {}
    cfg = settings_store.load()
    cfg.update({k: v for k, v in body.items() if v not in (None, "")})

    import time as _time

    from livekit.agents import llm as lk_llm

    from main import build_llm

    try:
        model = build_llm(cfg)

        @lk_llm.function_tool
        async def request_song(song: str) -> str:
            """Put a song into the station's request queue."""
            return "queued"

        ctx = lk_llm.ChatContext.empty()
        ctx.add_message(
            role="user",
            content="Please play Dreams by Fleetwood Mac. Use your tool to request it.",
        )

        t0 = _time.perf_counter()
        text, tool_calls, first = "", [], None
        stream = model.chat(chat_ctx=ctx, tools=[request_song])
        async for chunk in stream:
            if first is None:
                first = _time.perf_counter() - t0
            delta = getattr(chunk, "delta", None)
            if delta is None:
                continue
            if getattr(delta, "content", None):
                text += delta.content
            if getattr(delta, "tool_calls", None):
                tool_calls.extend(delta.tool_calls)
        await stream.aclose()

        return _cors(
            request,
            web.json_response(
                {
                    "ok": True,
                    "provider": cfg.get("llm_provider"),
                    "model": cfg.get("llm_model"),
                    "firstTokenMs": round((first or 0) * 1000),
                    "totalMs": round((_time.perf_counter() - t0) * 1000),
                    "toolCalling": bool(tool_calls),
                    "reply": (text or "").strip()[:200],
                }
            ),
        )
    except Exception as e:
        return _cors(request, web.json_response({"ok": False, "error": str(e)}))


async def handle_prompt_preview(request: web.Request) -> web.Response:
    """The exact system prompt the next caller's DJ will be given.

    Assembled the same way the worker does it, so what you read here is what
    the model actually gets — including the live station context and any house
    style you've set.
    """
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))

    import prompts as prompts_mod
    from main import effective_tools

    cfg = settings_store.load()
    station = StationClient()
    try:
        snap = await station.snapshot()
        override = str(cfg.get("persona_override") or "").strip()
        roster = {p.get("id"): p for p in snap["personas"]}
        persona = roster.get(override) or station.persona_from(snap["dj"], snap["personas"])
        text = await prompts_mod.build_system_prompt(station, persona, snapshot=snap)
    finally:
        await station.aclose()

    et = effective_tools(cfg)
    return _cors(
        request,
        web.json_response(
            {
                "prompt": text,
                "persona": persona.get("name"),
                "chars": len(text),
                "approxTokens": len(text) // 4,
                # MCP allowlist + local wrappers — what the model actually sees.
                "tools": et["mcp"] + et["local"],
            }
        ),
    )


async def handle_speed_test(request: web.Request) -> web.Response:
    """Time every stage a single conversational turn passes through, then add
    them up.

    Individual stages can each look acceptable while the sum is not: a caller
    experiences STT + LLM + TTS end to end, and that total is what decides
    whether the DJ feels like a person or a kiosk. This measures the real code
    paths, not a synthetic benchmark.
    """
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))

    import time as _time

    secrets_store.apply_to_env()
    body = await request.json() if request.can_read_body else {}
    cfg = settings_store.load()
    cfg.update({k: v for k, v in (body or {}).items() if v not in (None, "")})

    stages: list[dict] = []

    def record(name, ms, note="", counts=True):
        stages.append({"name": name, "ms": round(ms), "note": note, "counts": counts})

    # --- station snapshot (once per call, before the greeting) ---
    st = StationClient()
    try:
        t0 = _time.perf_counter()
        snap = await st.snapshot()
        record("Station snapshot", (_time.perf_counter() - t0) * 1000,
               "per call, before the DJ picks up", counts=False)

        t0 = _time.perf_counter()
        persona = st.persona_from(snap["dj"], snap["personas"])
        import prompts as prompts_mod

        prompt = await prompts_mod.build_system_prompt(st, persona, snapshot=snap)
        record("Prompt assembly", (_time.perf_counter() - t0) * 1000,
               f"{len(prompt)} chars (~{len(prompt)//4} tokens, paid every turn)",
               counts=False)
    finally:
        await st.aclose()

    # --- STT ---
    # For the local engine this is MEASURED, not estimated: the TTS stage
    # below produces real speech audio, and we transcribe that. The record is
    # inserted here (index) so the display keeps call order even though the
    # measurement happens after TTS.
    stt_ms = 0.0
    stt_index = len(stages)
    stt_provider_name, stt_model_name = "", ""
    try:
        from main import build_stt, effective_stt

        stt_provider_name, stt_model_name, _ = effective_stt(cfg)
        build_stt(cfg)
        if stt_provider_name != "local":
            stt_ms = 400.0
            record("Speech-to-text", stt_ms,
                   f"{stt_provider_name} — ESTIMATE, network round trip not measured")
    except Exception as e:
        record("Speech-to-text", 0, f"failed: {e}"[:110])
        stt_provider_name = ""

    # --- LLM to first token ---
    llm_ms = 0.0
    try:
        from livekit.agents import llm as lk_llm

        from main import build_llm

        model_obj = build_llm(cfg)
        ctx = lk_llm.ChatContext.empty()
        ctx.add_message(role="user", content="Say one short sentence about the weather.")

        t0 = _time.perf_counter()
        first = None
        stream = model_obj.chat(chat_ctx=ctx)
        async for chunk in stream:
            if first is None:
                delta = getattr(chunk, "delta", None)
                if delta and getattr(delta, "content", None):
                    first = (_time.perf_counter() - t0) * 1000
        await stream.aclose()
        llm_ms = first or (_time.perf_counter() - t0) * 1000
        record("LLM first token", llm_ms, f"{cfg.get('llm_provider')} / {cfg.get('llm_model')}")
    except Exception as e:
        record("LLM first token", 0, f"failed: {e}"[:110])

    # --- TTS to first audio ---
    tts_ms = 0.0
    rtf = None
    try:
        from tts_adapter import AdapterTTS

        os.environ["TTS_MODE"] = str(cfg.get("tts_mode", "cloud"))
        adapter_path = cfg.get("tts_adapter") or None
        if adapter_path and not os.path.isabs(adapter_path):
            cand = Path(__file__).parent / "tts-adapters" / adapter_path
            adapter_path = str(cand) if cand.exists() else None

        voice = cfg.get("tts_voice") or ""
        if not voice:
            sc = StationConfig()
            try:
                st2 = StationClient()
                try:
                    pv = await st2.resolve_live_persona()
                finally:
                    await st2.aclose()
                voice = await sc.voice_for(pv["id"])
            finally:
                await sc.aclose()

        tts = AdapterTTS(voice=voice, base_url=cfg.get("tts_base_url") or "",
                         api_key=os.environ.get("TTS_API_KEY", ""),
                         adapter_path=adapter_path, model=cfg.get("tts_model") or "")
        pcm = bytearray()
        tts_rate = 24000
        try:
            t0 = _time.perf_counter()
            first = None
            samples = 0
            stream = tts.synthesize("Evening. You're through to the booth, what can I do for you?")
            async for ev in stream:
                if first is None:
                    first = (_time.perf_counter() - t0) * 1000
                samples += ev.frame.samples_per_channel
                pcm.extend(ev.frame.data.tobytes())
            await stream.aclose()
            tts_rate = tts.sample_rate or 24000
            wall = (_time.perf_counter() - t0)
            audio_s = samples / tts.sample_rate if tts.sample_rate else 0
            rtf = round(wall / audio_s, 2) if audio_s else None
            tts_ms = first or 0
            record("TTS first audio", tts_ms,
                   f"{voice} · {rtf}x realtime" + ("  ⚠ slower than playback" if rtf and rtf >= 1 else ""))
        finally:
            await tts.aclose()
    except Exception as e:
        pcm = bytearray()
        record("TTS first audio", 0, f"failed: {e}"[:110])

    # --- local STT, measured on the real speech TTS just produced ---
    if stt_provider_name == "local" and pcm:
        try:
            from livekit import rtc as lk_rtc

            from local_stt import LocalWhisperSTT

            stt_obj = LocalWhisperSTT(model=stt_model_name or "base.en")
            t0 = _time.perf_counter()
            await stt_obj.prewarm()
            load_ms = (_time.perf_counter() - t0) * 1000

            n = len(pcm) // 2
            clip = lk_rtc.AudioFrame(
                data=bytes(pcm), sample_rate=tts_rate,
                num_channels=1, samples_per_channel=n,
            )
            t0 = _time.perf_counter()
            ev = await stt_obj._recognize_impl(clip)
            stt_ms = (_time.perf_counter() - t0) * 1000
            heard = (ev.alternatives[0].text if ev.alternatives else "").strip()
            audio_len = n / tts_rate
            factor = stt_ms / 1000 / audio_len if audio_len else 0
            entry = {
                "name": "Speech-to-text", "ms": round(stt_ms),
                "note": f"{stt_model_name} · measured on {audio_len:.1f}s of real speech "
                        f"({factor:.2f}x realtime) · heard: “{heard[:60]}”",
                "counts": True,
            }
            stages.insert(stt_index, entry)
            if load_ms > 800:
                stages.insert(stt_index + 1, {
                    "name": "STT model load", "ms": round(load_ms),
                    "note": "one-off per process, prewarmed in the worker",
                    "counts": False,
                })
        except Exception as e:
            stages.insert(stt_index, {
                "name": "Speech-to-text", "ms": 0,
                "note": f"measurement failed: {e}"[:110], "counts": True,
            })

    turn_ms = stt_ms + llm_ms + tts_ms
    return _cors(
        request,
        web.json_response(
            {
                "ok": True,
                "stages": stages,
                "turnMs": round(turn_ms),
                "realtimeFactor": rtf,
                "verdict": (
                    "Feels immediate." if turn_ms < 1200 else
                    "Natural enough." if turn_ms < 2000 else
                    "Noticeable pause between turns." if turn_ms < 3500 else
                    "Long enough that callers will talk over it."
                ),
            }
        ),
    )


async def handle_test_env(request: web.Request) -> web.Response:
    """The links nothing else covers: is LiveKit up, is an agent worker
    actually registered to answer, and can the configured STT be built at all.

    STT is the least-testable leg — it needs live audio to exercise properly —
    so this at least catches a missing key or a bad provider/model combination
    before a caller discovers it."""
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))

    secrets_store.apply_to_env()
    body = await request.json() if request.can_read_body else {}
    cfg = settings_store.load()
    cfg.update({k: v for k, v in (body or {}).items() if v not in (None, "")})

    result: dict = {"ok": True}

    # --- LiveKit server + a registered worker ---
    lk_url = os.environ.get("LIVEKIT_URL", "ws://localhost:7880")
    http_url = lk_url.replace("wss://", "https://").replace("ws://", "http://").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(http_url + "/")
            result["livekit"] = {"ok": r.status_code < 500, "url": lk_url,
                                 "detail": f"HTTP {r.status_code}"}
    except Exception as e:
        result["livekit"] = {"ok": False, "url": lk_url, "detail": str(e)[:120]}
        result["ok"] = False

    # A registered worker is what actually answers the call.
    try:
        lkapi = api.LiveKitAPI(
            url=http_url,
            api_key=LIVEKIT_API_KEY,
            api_secret=LIVEKIT_API_SECRET,
        )
        try:
            await lkapi.room.list_rooms(api.ListRoomsRequest())
            result["livekitAuth"] = {"ok": True, "detail": "API credentials accepted"}
        finally:
            await lkapi.aclose()
    except Exception as e:
        result["livekitAuth"] = {"ok": False, "detail": str(e)[:120]}
        result["ok"] = False

    # --- STT constructible with the configured provider/key ---
    try:
        from main import build_stt, effective_stt

        provider, model, note = effective_stt(cfg)
        build_stt(cfg)  # surfaces missing credentials
        # Buildable with a valid model = the call will work. A note means the
        # selection didn't survive intact (missing key, or a model belonging to
        # another provider) and was corrected — worth surfacing, but not fatal.
        result["stt"] = {
            "ok": True,
            "provider": provider,
            "model": model,
            "note": note,
            "detail": f"{provider} · {model or 'default'}"
            + (f" — {note}" if note else ""),
        }
    except Exception as e:
        result["stt"] = {"ok": False, "provider": cfg.get("stt_provider"),
                         "detail": str(e)[:160]}
        result["ok"] = False

    # --- station admin credentials ---
    # Several tools are admin-gated (library search, announcements) and the
    # back-to-air handoff posts to /dj/say, which 401s without them. Probing
    # /listeners is a cheap, side-effect-free way to prove the credentials work.
    from station_config import has_admin

    if not has_admin():
        result["admin"] = {
            "ok": False,
            "detail": "not set — library search, on-air announcements and the "
                      "back-to-air handoff will all be refused",
        }
    else:
        base = settings_store.station_base_url()
        try:
            from station_config import admin_credentials

            user, password = admin_credentials()
            async with httpx.AsyncClient(timeout=6.0) as c:
                r = await c.get(f"{base}/listeners", auth=httpx.BasicAuth(user, password))
            if r.status_code == 401:
                result["admin"] = {"ok": False, "detail": "station rejected these credentials"}
            else:
                r.raise_for_status()
                result["admin"] = {"ok": True, "detail": "accepted by the station"}
        except Exception as e:
            result["admin"] = {"ok": False, "detail": str(e)[:120]}

    # --- listeners: the station refuses song requests when nobody is tuned in ---
    try:
        st = StationClient()
        try:
            np = await st.now_playing()
        finally:
            await st.aclose()
        count = ((np.get("listeners") or {}).get("current"))
        if count is None:
            count = ((np.get("context") or {}).get("listeners") or {}).get("count")
        result["listeners"] = {
            "count": count,
            "requestsOpen": bool(count),
            "detail": (f"{count} tuned in — requests are open" if count else
                       "nobody tuned in — the station will refuse song requests "
                       "until someone is listening"),
        }
    except Exception as e:
        result["listeners"] = {"count": None, "requestsOpen": False, "detail": str(e)[:120]}

    # --- keys the current configuration depends on ---
    need = []
    llm_p = str(cfg.get("llm_provider", "")).lower()
    if llm_p in ("openai", "google", "anthropic") and not secrets_store.get(f"{llm_p}_api_key"):
        need.append(f"{llm_p} (LLM)")
    if str(cfg.get("tts_mode")) == "cloud" and not (
        secrets_store.get("tts_api_key") or secrets_store.get("openai_api_key")
    ):
        need.append("cloud TTS")
    result["keys"] = {"ok": not need, "missing": need}
    result["webhook"] = dict(_hook_state)
    if need:
        result["ok"] = False

    return _cors(request, web.json_response(result))


async def handle_test_admin(request: web.Request) -> web.Response:
    """Prove the station admin credentials work, without running the whole
    pipeline. Accepts draft values in the body so a credential can be tested
    BEFORE saving it; falls back to the stored/env ones. Probes /listeners —
    admin-gated, read-only, side-effect free."""
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))

    body = await request.json() if request.can_read_body else {}
    from station_config import admin_credentials

    user = str((body or {}).get("subwave_admin_user") or "").strip()
    password = str((body or {}).get("subwave_admin_pass") or "").strip()
    draft = bool(user or password)
    if not (user and password):
        stored_user, stored_pass = admin_credentials()
        user = user or stored_user
        password = password or stored_pass
    if not (user and password):
        return _cors(request, web.json_response(
            {"ok": False, "detail": "no credentials to test — fill in both fields"}
        ))

    base = settings_store.station_base_url()
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{base}/listeners", auth=httpx.BasicAuth(user, password))
        if r.status_code == 401:
            detail = "station rejected these credentials"
            ok = False
        elif r.status_code == 429:
            detail = ("station login rate limiter is active — wait 15 minutes "
                      "(or restart the station) and test again; this does not "
                      "mean the credentials are wrong")
            ok = False
        else:
            r.raise_for_status()
            detail = ("accepted by the station"
                      + (" (draft — remember to save)" if draft else ""))
            ok = True
        return _cors(request, web.json_response({"ok": ok, "detail": detail}))
    except Exception as e:
        return _cors(request, web.json_response({"ok": False, "detail": str(e)[:140]}))


async def handle_test_station(request: web.Request) -> web.Response:
    """Station reachable, and how many MCP tools survive the allowlist."""
    # Same gate as every other test endpoint: without an admin key, a foreign
    # origin must not be able to read the station URL and tool list.
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))

    from livekit.agents import mcp as lk_mcp

    from main import effective_tools
    secrets_store.apply_to_env()

    cfg = settings_store.load()
    base = request.query.get("station_base_url") or cfg["station_base_url"]
    mcp_url = request.query.get("station_mcp_url") or (
        cfg.get("station_mcp_url") or f"{str(base).rstrip('/')}/mcp"
    )

    # Connect with the same guarded MCP list a real call uses, and report the
    # local wrappers alongside — the raw MCP view is not what callers get.
    et = effective_tools(cfg)
    allowed = et["mcp"]
    result: dict = {"ok": False, "mcpUrl": mcp_url, "stationUrl": base}

    station = StationClient(base_url=base)
    try:
        health = await station.health()
        result["station"] = bool(health)
        persona = await station.resolve_live_persona()
        result["liveDj"] = persona.get("name")
    finally:
        await station.aclose()

    from station_config import mcp_headers

    headers = mcp_headers()
    result["adminAuth"] = bool(headers)
    server = lk_mcp.MCPServerHTTP(
        url=mcp_url,
        transport_type="streamable_http",
        allowed_tools=allowed,
        headers=headers or None,
        client_session_timeout_seconds=15,
    )
    try:
        await server.initialize()
        tools = await server.list_tools()
        mcp_names = [
            lk_mcp.get_raw_function_info(t).name if lk_mcp.is_raw_function_tool(t) else str(t)
            for t in tools
        ]
        result["tools"] = mcp_names + et["local"]
        result["toolCount"] = len(mcp_names) + len(et["local"])
        result["ok"] = True
    except Exception as e:
        result["error"] = str(e)
    finally:
        try:
            await server.aclose()
        except Exception:
            pass

    return _cors(request, web.json_response(result))


# --- station webhooks ------------------------------------------------------
# The station can push track.play / dj.say / dj.link / request.received to a
# URL instead of us polling for them. Receiving them lets the on-air card
# refresh the moment something changes (cache bust) and gives later features
# (mid-call "your request just landed") a real event source.
from collections import deque

_hook_events: deque = deque(maxlen=50)
_hook_state: dict = {"registered": False, "url": "", "detail": "not attempted"}


def _lan_ip() -> str:
    """The address the NAS can reach this box on — NOT localhost."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


async def handle_station_hook(request: web.Request) -> web.Response:
    """Receiver for the station's pushes. No auth — the station doesn't sign
    hooks — so treat payloads as untrusted data: store, bust caches, never act
    directly on their contents."""
    import time as _time

    try:
        body = await request.json()
    except Exception:
        body = {}
    event = str(body.get("event") or body.get("type") or "?")
    _hook_events.append({"at": _time.time(), "event": event, "data": body})
    log.info("station webhook: %s", event)

    # Anything that changes what the card shows invalidates the cache.
    if event.split(".")[0] in ("track", "dj", "request"):
        _live_cache["data"] = None
    return web.json_response({"ok": True})


async def handle_hooks_recent(request: web.Request) -> web.Response:
    # Operator debugging surface — same gate as the rest of the panel.
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))
    return _cors(request, web.json_response(
        {"registered": _hook_state, "events": list(_hook_events)[-15:]}
    ))


async def register_station_webhook() -> None:
    """Register our receiver with the station, once, idempotently. The write
    schema is undocumented, so try the two plausible shapes and verify by
    reading the list back."""
    from station_config import admin_credentials

    user, password = admin_credentials()
    if not (user and password):
        _hook_state["detail"] = "no admin credentials"
        return

    url = os.environ.get("CALLIN_HOOK_URL") or f"http://{_lan_ip()}:{PORT}/hooks/station"
    _hook_state["url"] = url
    events = ["track.play", "dj.say", "dj.link", "request.received"]
    base = settings_store.station_base_url()
    auth = httpx.BasicAuth(user, password)

    try:
        async with httpx.AsyncClient(base_url=base, timeout=10.0, auth=auth) as c:
            current = (await c.get("/webhooks")).json()
            hooks = current.get("webhooks") or []
            if any(h.get("url") == url for h in hooks if isinstance(h, dict)):
                _hook_state.update(registered=True, detail="already registered")
                return

            for shape in (
                {"url": url, "events": events},
                {"webhooks": hooks + [{"url": url, "events": events}]},
            ):
                r = await c.post("/webhooks", json=shape)
                if r.status_code >= 400:
                    continue
                check = (await c.get("/webhooks")).json()
                if any(h.get("url") == url
                       for h in (check.get("webhooks") or []) if isinstance(h, dict)):
                    _hook_state.update(registered=True, detail="registered")
                    log.info("station webhook registered -> %s", url)
                    return

            # A schema rejection is permanent for this station version —
            # retrying every warm tick just rate-limits us against the
            # station. Stand down until a restart or a credentials change.
            _hook_state["gave_up"] = True
            _hook_state["detail"] = "station did not accept either registration shape"
            log.warning("webhook registration failed: %s", _hook_state["detail"])
    except Exception as e:
        _hook_state["detail"] = str(e)[:120]
        log.warning("webhook registration failed: %s", e)
        # Every attempt carries the admin credentials; unbounded retries have
        # been observed tripping a station's LOGIN rate limiter and locking
        # the operator out of their own admin UI. Stand down after a few
        # failures; a restart or a credentials change re-arms it.
        _hook_state["attempts"] = _hook_state.get("attempts", 0) + 1
        if _hook_state["attempts"] >= 5:
            _hook_state["gave_up"] = True
            log.warning(
                "webhook registration standing down after %d failed attempts",
                _hook_state["attempts"],
            )


async def keep_station_warm(app: web.Application) -> None:
    """Poll /dj on a timer so a caller never pays for a cold read.

    The station caches persona state lazily: warm it answers in ~15ms, but the
    first read after a quiet spell was measured at 19.5 seconds. That delay
    would land squarely between pressing Call and the DJ speaking. Touching it
    periodically keeps it hot for whoever calls next.
    """
    interval = float(os.environ.get("STATION_WARM_INTERVAL", "45"))

    async def loop() -> None:
        while True:
            try:
                station = StationClient(timeout=25.0)
                try:
                    t0 = asyncio.get_running_loop().time()
                    dj = await station.live_dj()
                    took = asyncio.get_running_loop().time() - t0
                    # An empty read is a FAILED ping — logging "now warm" for
                    # it made an unreachable station look healthy in the logs.
                    if dj and took > 2:
                        log.info("station /dj was cold (%.1fs) — now warm", took)
                    elif not dj:
                        log.debug("warm ping got no answer from the station")
                finally:
                    await station.aclose()
                # Registration was previously attempted exactly once at
                # startup — a station that was down at that moment, or admin
                # credentials added later, left it unregistered until a
                # restart. Piggyback on the warm tick until it sticks.
                if not _hook_state.get("registered") and not _hook_state.get("gave_up"):
                    await register_station_webhook()
            except Exception as e:
                log.debug("warm ping failed: %s", e)
            await asyncio.sleep(interval)

    task = asyncio.create_task(loop())
    app["warm_task"] = task
    app["hook_task"] = asyncio.create_task(register_station_webhook())
    try:
        yield
    finally:
        task.cancel()


@web.middleware
async def _no_stale_assets(request: web.Request, handler):
    """Browsers heuristically cache the widget's html/js/css, so after an
    image update people saw the OLD interface until a hard refresh — and
    nothing told them why. no-cache forces revalidation; unchanged files
    still answer 304, so the cost is one conditional request."""
    resp = await handler(request)
    # Path-based: a FileResponse only learns its content type at send time.
    if request.path == "/" or request.path.endswith((".html", ".js", ".css")):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def build_app() -> web.Application:
    app = web.Application(middlewares=[_no_stale_assets])
    app.router.add_post("/token", handle_token)
    app.router.add_post("/call-ended", handle_call_ended)
    app.router.add_post("/hooks/station", handle_station_hook)
    app.router.add_get("/hooks/recent", handle_hooks_recent)
    app.router.add_options("/call-ended", handle_options)
    app.router.add_options("/token", handle_options)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/live", handle_live)
    app.router.add_get("/settings", handle_get_settings)
    app.router.add_post("/settings", handle_post_settings)
    app.router.add_options("/settings", handle_options)
    app.router.add_post("/settings/secrets", handle_post_secrets)
    app.router.add_options("/settings/secrets", handle_options)
    app.router.add_post("/auth/password", handle_set_password)
    app.router.add_options("/auth/password", handle_options)
    app.router.add_get("/settings/options", handle_settings_options)
    app.router.add_get("/avatar/{persona_id}", handle_avatar)
    app.router.add_post("/test/tts", handle_test_tts)
    app.router.add_options("/test/tts", handle_options)
    app.router.add_post("/test/llm", handle_test_llm)
    app.router.add_options("/test/llm", handle_options)
    app.router.add_get("/test/station", handle_test_station)
    app.router.add_post("/test/admin", handle_test_admin)
    app.router.add_options("/test/admin", handle_options)
    app.router.add_get("/prompt", handle_prompt_preview)
    app.router.add_post("/test/env", handle_test_env)
    app.router.add_post("/test/speed", handle_speed_test)
    app.router.add_options("/test/speed", handle_options)
    app.router.add_options("/test/env", handle_options)
    app.router.add_get("/", handle_index)
    app.router.add_static("/", WIDGET_DIR, show_index=False, name="widget")
    app.cleanup_ctx.append(keep_station_warm)
    return app


if __name__ == "__main__":
    log.info("call-in widget + token server on http://localhost:%s", PORT)
    log.info("browser will be told to connect to %s", LIVEKIT_PUBLIC_URL)
    web.run_app(build_app(), port=PORT, print=None)
