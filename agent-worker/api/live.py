"""What the call card shows: who is on air, and what the widget may offer.

The answer is cached — see live_cache.py, which is also where every module
that stales it reaches. This one builds it.
"""

from __future__ import annotations

import logging
import re

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
from api.sounds import _resolved_sound
from api.wire import _cors
from brain.briefing import demojibake
from log_setup import describe
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
        #
        # "station" KEEPS the toggle. It used to be pinned too, because the
        # palette's tokens are inline custom properties on :root that outrank
        # every data-theme rule — the toggle flipped the attribute and nothing
        # on screen changed. The operator who chose station colours then
        # reported the toggle "not surfacing" as a bug, which it reads as: the
        # setting that shows the toggle was on, and a different setting was
        # silently vetoing it. The widget now clears the inline tokens when
        # the viewer toggles (shared.js), so the control works instead of
        # being hidden: station colours are the default look, and an explicit
        # viewer choice overrides them.
        "theme": (
            bool(cfg.get("embed_theme_toggle" if embed else "show_theme_toggle"))
            and str(cfg.get("widget_theme") or "auto") not in ("light", "dark")
        ),
        # Never in an embed, and not a setting there: an embed does not load
        # the panel's code, so the gear would open nothing whichever way an
        # operator set it.
        "settings": False if embed else bool(cfg.get("show_settings_gear")),
    }


# The station names its palette one way and this widget names it another, and
# neither is going to change: the station's names are what its own player and
# admin UI are written against, and the widget's are the ones HOST-STYLE-GUIDE
# publishes for host pages to post over `swtv:theme`. So the translation lives
# here, in one direction, once.
#
# Deliberately partial. --ok, --cool and --shadow have no counterpart in the
# station's set, so they keep the widget's own light/dark value and are picked
# up from `mode` below — a green that means "the line is open" is the widget's
# own vocabulary, not the station's, and inventing one from --accent-2 would
# make the state chip stop reporting the transition a caller waits on.
_STATION_TOKENS = {
    "--bg": "--pine",
    "--surface": "--granite",
    "--field": "--granite-hi",
    "--ink": "--alpenglow",
    "--muted": "--sage",
    "--ink-faint": "--sage-dim",
    "--accent": "--coral",
    "--accent-2": "--amber",
    "--soft-border": "--hairline",
    "--line": "--edge",
}

# A colour, and nothing that could be anything else. These values reach a
# browser and are written into inline style, so the widget refuses anything
# suspicious on arrival too — but a station is a trusted source that can still
# be misconfigured, and a token that silently poisons every embed's stylesheet
# is not a failure anyone would trace back to a theme file.
_SAFE_TOKEN = re.compile(r"^[#a-zA-Z0-9(),.%/ _-]{1,120}$")


def station_palette(payload: dict) -> dict | None:
    """The on-air show's palette, in this widget's token names.

    `effective` is the station's own answer to "what should a client paint
    right now" — a show's themeId outranks the station default while it is on
    air — so this follows the programme rather than the settings page, which
    is what "the station's own colours" has to mean for a card sitting next to
    a player that already moved.
    """
    themes = payload.get("themes") or []
    wanted = payload.get("effective") or payload.get("active")
    theme = None
    for t in themes:
        if isinstance(t, dict) and t.get("id") == wanted:
            theme = t
            break
    if theme is None and isinstance(wanted, dict):
        theme = wanted              # some builds send the theme, not its id
    if not isinstance(theme, dict):
        return None

    tokens = {}
    for their, ours in _STATION_TOKENS.items():
        value = str((theme.get("tokens") or {}).get(their) or "").strip()
        if value and _SAFE_TOKEN.match(value):
            tokens[ours] = value
    if not tokens:
        return None
    return {
        "id": theme.get("id") or "",
        "name": theme.get("name") or "",
        # light or dark decides the tokens we did NOT get from the station,
        # and the browser's own form controls and scrollbars.
        "mode": "light" if str(theme.get("mode")).lower() == "light" else "dark",
        "tokens": tokens,
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
    mode = str(cfg.get("call_button_mode") or "default").lower()
    if mode == "name" and str(persona_name or "").strip():
        return f"Call {str(persona_name).strip()}"
    if mode == "custom":
        return str(cfg.get("call_button_label") or "").strip() or "Call the DJ"
    return "Call the DJ"


def look_payload(cfg: dict, persona_name: str = "") -> dict:
    """Everything about the card that is a LOOK rather than a fact.

    Split out so the settings panel's live preview and a real caller's /live
    resolve it through the same code. The panel previews unsaved settings, and
    the alternative was the panel reimplementing corner_controls,
    card_identity and call_button_label in JavaScript — three rules that
    already exist here, in a file whose whole job is being the one place they
    exist. A preview that disagrees with the card is worse than no preview:
    it is confidently wrong about the thing you opened it to check.

    Nothing here reads the station. It is settings in, appearance out.
    """
    return {
        "theme": str(cfg.get("widget_theme") or "auto"),
        "controls": corner_controls(cfg),
        "embedControls": corner_controls(cfg, embed=True),
        "card": card_identity(cfg),
        "embedCard": card_identity(cfg, embed=True),
        "avatarStyle": (
            "square" if str(cfg.get("avatar_style")) == "square" else "round"
        ),
        "speakerDefault": bool(cfg.get("default_to_speaker")),
        # Like controls/card: /live is cached across every caller and cannot
        # know which surface is asking, so both answers travel and the widget
        # picks on `framed`.
        "liveCalls": bool(cfg.get("live_calls_enabled", True)),
        "vmBtn": bool(cfg.get("show_voicemail_button")),
        "embedVmBtn": bool(cfg.get("embed_voicemail_button")),
        # The text line: enabled is the door's state (The Line's pause is
        # applied widget-side like the other modes), the buttons are per
        # surface like the voicemail pair.
        "chatEnabled": bool(cfg.get("chat_enabled")),
        "chatBtn": bool(cfg.get("show_chat_button")),
        "embedChatBtn": bool(cfg.get("embed_chat_button")),
        "ptt": bool(cfg.get("show_push_to_talk")),
        "embedPtt": bool(cfg.get("embed_push_to_talk")),
        # The card's fixed strings, overrides only — the defaults live in
        # the widget, so a blank costs nothing on the wire.
        "wording": {
            k[len("word_"):]: str(cfg.get(k) or "")
            for k in ("word_ringing", "word_answering", "word_online",
                      "word_recording", "word_hangup", "word_vm_button",
                      "word_ptt", "word_closed", "word_message_only")
            if cfg.get(k)
        },
        "voiceEffect": str(cfg.get("voice_effect") or "none"),
        # Not `or 60`: a stored 0 is a real answer (the clean voice), and
        # the old `or 100` silently turned intensity-zero into full blast.
        # Blank only happens through the preview's unsaved patch.
        "voiceEffectLevel": max(0, min(100, int(
            lvl if (lvl := cfg.get("voice_effect_level")) not in ("", None)
            else 60))),
        # In look_payload as well as /live: the panel's preview exists to
        # show what a setting does to the card, and this one can turn the
        # Call button into "Leave a message".
        "voicemailWhen": settings_store.voicemail_policy(cfg),
        "callLabel": call_button_label(cfg, persona_name),
        "askFeedback": bool(cfg.get("ask_call_feedback")),
    }


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
    if payload.get("canAsk") is None:
        return payload                      # the help button is switched off
    tier = caller_tier(request)
    out = dict(payload)
    out["canAsk"] = {
        k: settings_store.permission_reaches(v, tier)
        for k, v in (payload.get("askTiers") or {}).items()
    }
    out["callerTier"] = tier
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
