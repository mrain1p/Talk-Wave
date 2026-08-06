"""What the call card shows: who is on air, and what the widget may offer.

The answer is cached — see live_cache.py, which is also where every module
that stales it reaches. This one builds it.
"""

from __future__ import annotations

import logging

import httpx
from aiohttp import web

import admin_auth
import settings as settings_store
import station as station_mod
import tune_in
from api.env import LIVEKIT_PUBLIC_URL
from api.live_cache import _LIVE_TTL, _live_cache
from api.sounds import _resolved_sound
from api.wire import _cors
from brain.briefing import demojibake
from station import StationClient
from version import APP_VERSION

log = logging.getLogger("callin.token")


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response(
        {"ok": True, "version": APP_VERSION, "livekit": LIVEKIT_PUBLIC_URL}
    )


def _secure_origin() -> str:
    """Where the HTTPS front door lives, if one is configured. wss signalling
    and the https widget share an origin by design (the Caddyfile routes
    both), so the wss public URL doubles as the pointer."""
    if LIVEKIT_PUBLIC_URL.startswith("wss://"):
        return "https://" + LIVEKIT_PUBLIC_URL[len("wss://"):].split("/", 1)[0]
    return ""


def corner_controls(cfg: dict, embed: bool = False) -> dict:
    """Which buttons the call card offers in its top-right corner.

    One decision, made here, for both surfaces. It used to be three unrelated
    mechanisms in the widget — the settings gear hidden by a CSS rule that
    only existed for embeds, the theme toggle by an inline style set in two
    different places, the help button by whether `canAsk` came back — so what
    a caller was offered depended on which surface they happened to be
    looking at, and nobody had decided that. It was just where the rules
    happened to live.

    Still one decision, but now it is asked twice: the standalone page and an
    embed on somebody else's site are different audiences, and answering both
    with one switch made every answer a compromise. `/live` carries both, and
    the widget picks by whether it is in a frame — the server cannot know
    that, and the answer is cached for 30 seconds across every caller anyway.

    The widget may still subtract from this, but only for things this side
    cannot know: a host page that pinned ?theme= has already chosen, and an
    embed never loads the settings panel at all, so a gear there opens
    nothing.
    """
    return {
        "help": bool(cfg.get("embed_caller_help" if embed else "show_caller_help")),
        # Two gates, and both have to pass. The operator can switch the toggle
        # off outright; pinning light or dark also removes it, because there is
        # then nothing to toggle between. "inherit" is not pinned — on the
        # standalone page it behaves as auto.
        "theme": (
            bool(cfg.get("embed_theme_toggle" if embed else "show_theme_toggle"))
            and str(cfg.get("widget_theme") or "auto") not in ("light", "dark")
        ),
        # Never in an embed, and not a setting there: an embed does not load
        # the panel's code, so the gear would open nothing whichever way an
        # operator set it.
        "settings": False if embed else bool(cfg.get("show_settings_gear")),
    }


def card_identity(cfg: dict, embed: bool = False) -> dict:
    """Which lines of the "who is on air" block the card paints.

    An embed sits in a column beside the host page's own now-playing ticker
    and show heading, so a second copy of both is noise — but on the
    standalone page they are the only thing saying who you are about to ring.
    Hence one answer each. The DJ's NAME is not switchable: a call card that
    doesn't say who answers isn't a call card.
    """
    p = "embed_" if embed else "show_"
    return {
        "avatar": bool(cfg.get(p + "dj_avatar")),
        "show": bool(cfg.get(p + "dj_show")),
        "tagline": bool(cfg.get(p + "dj_tagline")),
        "track": bool(cfg.get(p + "now_playing")),
    }


def call_button_label(cfg: dict, persona_name: str = "") -> str:
    """What the Call button says before a call starts.

    Resolved here rather than in the widget so the two surfaces cannot drift,
    and so "use the DJ's name" follows the live roster without the widget
    having to know the rule. Falls back the moment the name is missing —
    "Call " with nothing after it is worse than the generic label.
    """
    if cfg.get("call_button_uses_name") and str(persona_name or "").strip():
        return f"Call {str(persona_name).strip()}"
    return str(cfg.get("call_button_label") or "").strip() or "Call the DJ"


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
        sound_pack = cfg.get("sound_pack") or "classic"
        # Cached inside tune_in, so a station that publishes a mount list is
        # only asked for it every few minutes rather than on every /live.
        stream_url, stream_alternates = await tune_in.resolve(
            cfg, settings_store.station_base_url()
        )
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
                        "volume": int(cfg.get("tune_in_volume") or 0),
                    },
                    # How the card is coloured. An embed's own data-theme
                    # attribute still wins — the host page knows more about
                    # itself than this setting does.
                    "theme": str(cfg.get("widget_theme") or "auto"),
                    # Which controls the card puts in its top-right corner,
                    # and which lines of the who's-on-air block it paints.
                    # Both surfaces are sent because /live is cached across
                    # every caller and cannot know which one is asking; the
                    # widget picks by whether it is in a frame.
                    "controls": corner_controls(cfg),
                    "embedControls": corner_controls(cfg, embed=True),
                    "card": card_identity(cfg),
                    "embedCard": card_identity(cfg, embed=True),
                    "callLabel": call_button_label(cfg, persona.get("name")),
                    # Whether to ask the caller how it went once the line
                    # drops. See api/tokens.handle_call_feedback.
                    "askFeedback": bool(cfg.get("ask_call_feedback")),
                    # What a caller may actually ask for. Sent only when the
                    # operator has switched the help button on, and only as
                    # the permissions themselves — the wording lives in the
                    # widget, so the panel and the card cannot drift.
                    "canAsk": (
                        {k: bool(cfg.get(k)) for k in (
                            "allow_requests", "allow_library_search",
                            "allow_exact_queue", "allow_announcements",
                            "allow_skills", "allow_takeover",
                        )}
                        if cfg.get("show_caller_help") else None
                    ),
                    # So the card can show elapsed/remaining and warn before
                    # the graceful cutoff rather than surprising the caller.
                    "limits": {
                        "maxCallSeconds": int(cfg.get("max_call_seconds") or 0),
                        "idlePromptSecs": int(cfg.get("idle_prompt_secs") or 0),
                    },
                    # Per sound: what the operator configured, else whatever
                    # the chosen pack bundles, else "" — which the widget
                    # reads as "synthesize it", the behaviour it has always
                    # had when nothing is set.
                    "sounds": {
                        "enabled": bool(cfg.get("call_sounds")),
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
        log.info("avatar fetch failed for %s: %s", persona_id, e)
        raise web.HTTPNotFound()
