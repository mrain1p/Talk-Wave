"""The settings API: read the config, write it, and populate its dropdowns.

`settings` here is the HTTP surface. The store it reads and writes is the
top-level `settings.py`, imported as `settings_store` throughout, and the
precedence it implements (data/settings.json -> environment -> DEFAULTS) is
that module's business rather than this one's.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
from aiohttp import web

import admin_auth
import secrets_store
import settings as settings_store
from api.auth import _auth_configured, _write_allowed
from api.credentials import _is_saved_host
from api.hooks import _hook_state, register_station_webhook
from api.live_cache import _live_cache
from api.wire import _cors
from log_setup import describe
from station import StationClient
from station_config import StationConfig
from tts_adapter import ADAPTER_DIR
from tts_adapter import available_voices as tts_voice_list
from tts_adapter import resolve_adapter

log = logging.getLogger("callin.token")


async def handle_get_settings(request: web.Request) -> web.Response:
    # A browser NAVIGATING here (Accept: text/html) gets the settings PAGE
    # itself — /settings is the panel's one and only address (the old
    # /panel is retired outright, 0.10.8). The page is public markup with
    # its own login, so serving it does not weaken the admin gate below,
    # which still guards everything else this handler returns. The panel's
    # own fetch() calls carry no text/html Accept and never take this branch.
    if "text/html" in (request.headers.get("Accept") or ""):
        from api.widget import _versioned_page

        return web.Response(text=_versioned_page("panel.html"),
                            content_type="text/html")
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
                # What each field would be if cleared — env over defaults, the
                # operator's own choices removed. The panel's blank option
                # describes itself with this; see settings.beneath().
                "beneath": settings_store.beneath(),
                "secrets": secrets_store.status(),
                # Field metadata (labels, help, grouping, dependencies) so the
                # page doesn't keep its own parallel copy of the schema.
                "schema": settings_store.schema_payload(),
                "authConfigured": _auth_configured(),
                "guestConfigured": admin_auth.guest_is_set(),
            }
        ),
    )


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

    # Catch a typo while the operator is still looking at the panel. A real
    # deployment ran for days with "Michael" in the MCP endpoint — autofilled
    # by the browser — which left the DJ with no station tools on every call.
    problem = settings_store.complain(patch)
    if problem:
        return _cors(request, web.json_response({"error": problem}, status=400))

    resolved = settings_store.save(patch)
    _live_cache["data"] = None
    # Changing the station URL (or the TTS URL) changes what the options
    # payload should contain — without this, re-homing the sidecar left the
    # panel showing the previous station's personas for up to the cache TTL.
    _options_cache["data"] = None
    return _cors(request, web.json_response({"resolved": resolved}))


# --- per-DJ voice effects ----------------------------------------------------
# The greeting-overrides pattern on the settings surface: one colour per
# persona, overriding the shared Voice effects pick while that DJ is on air.
# Saved the moment it is picked (like the dashboard controls), because a
# per-DJ costume is a decision about a character, not a draft of a form.

async def handle_voice_effects(request: web.Request) -> web.Response:
    """The whole map — persona id to effect kind. Admin: it is panel
    furniture, and the caller-facing answer already rides /live."""
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))
    import voice_effects

    return _cors(request, web.json_response({"effects": voice_effects.read()}))


async def handle_voice_effect_set(request: web.Request) -> web.Response:
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
    pid = str((body or {}).get("personaId") or "").strip()
    effect = str((body or {}).get("effect") or "").strip()
    if not pid:
        return _cors(request, web.json_response(
            {"error": "personaId is required"}, status=400))
    # Legal kinds are exactly the dropdown's — the same CHOICES list — plus
    # blank for "the shared setting decides". An unknown kind stored here
    # would ride /live into every caller's WebAudio graph as a no-op with a
    # name, which is the quiet kind of wrong.
    legal = {v for v, _ in settings_store.STATIC_CHOICES.get("voice_effect", [])}
    if effect and effect not in legal:
        return _cors(request, web.json_response(
            {"error": f"unknown effect '{effect}'"}, status=400))
    import voice_effects

    voice_effects.set_effect(pid, effect)
    _live_cache["data"] = None
    return _cors(request, web.json_response(
        {"ok": True, "effects": voice_effects.read()}))


async def _tts_voices(base_url: str, cfg: dict | None = None) -> list[str]:
    """Ask the configured TTS server what voices it actually has.

    The lookup itself lives in tts_adapter, because the WORKER consults the
    same list before a call — a panel showing one set of voices while the
    worker believes another is how a call ends up asking for a voice the
    backend does not have. That includes the ADAPTER: discovery is described
    there now, so looking it up without one would put the panel back on a
    different path from the call it is configuring. Here, an empty answer
    falls back to the stock OpenAI names so the dropdown is never blank; the
    worker deliberately does not, because "could not find out" must not read
    as "has none".
    """
    if not base_url:
        return settings_store.OPENAI_VOICES
    cfg = cfg or {}
    found = await tts_voice_list(
        base_url,
        adapter_path=resolve_adapter(cfg.get("tts_adapter")),
        mode=str(cfg.get("tts_mode", "")),
    )
    return found or settings_store.OPENAI_VOICES


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
        log.info("openai model list unavailable (%s)", describe(e))
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
        log.info("google model list unavailable (%s)", describe(e))
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
        log.info("anthropic model list unavailable (%s)", describe(e))
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
        log.info("openrouter model list unavailable (%s)", describe(e))
        return []


async def _protocol_models(provider: str) -> list[str]:
    """The catalogue of an OpenAI-protocol provider — DeepSeek, Requesty, the
    Vercel gateway. One function for all three: they answer the same
    `GET /v1/models` with the same `{data: [{id}]}`, so what differs is the
    address and the key.

    Nothing is filtered out here. On the two aggregators the ids are namespaced
    per vendor and there is no reliable prefix to trim on — a filter written
    against today's naming quietly hides tomorrow's models, and this list is
    the only one the operator gets, because MODEL_CHOICES for these is empty on
    purpose.
    """
    host, _default = settings_store.OPENAI_PROTOCOL_HOSTS[provider]
    api_key = secrets_store.get(settings_store.LLM_PROVIDER_KEY[provider])
    if not api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{host.rstrip('/')}/models",
                            headers={"Authorization": f"Bearer {api_key}"})
            r.raise_for_status()
            return sorted(m["id"] for m in r.json().get("data", []) if m.get("id"))
    except Exception as e:
        log.info("%s model list unavailable (%s)", provider, describe(e))
        return []


def _custom_llm_endpoint(cfg: dict) -> str:
    """The operator's own OpenAI-protocol address, when the model dropdown
    should be read from IT rather than from the provider's official catalogue.

    A beta tester pointed the openai provider at llama-swap (llama.cpp's
    multi-model router) and every model in the dropdown 404'd — "no router for
    requested model" — because the list came from api.openai.com while the
    calls went to their server. build_llm honours llm_base_url for openai,
    deepseek, requesty, gateway and openai-compatible, so for those the
    endpoint's own list is the only one whose names actually route. Empty for
    providers that ignore the field (google, anthropic, openrouter) and for
    ollama, which has its own /api/tags path — and empty when the URL is just
    the provider's official host again.
    """
    p = str(cfg.get("llm_provider", "")).lower()
    base = str(cfg.get("llm_base_url") or "").strip().rstrip("/")
    # locca is the one provider whose blank Endpoint still names a server —
    # the well-known host address build_llm falls back to — so its dropdown
    # can be read from there before the operator has typed anything.
    if p == "locca":
        return base or settings_store.LOCCA_BASE_URL_DEFAULT.rstrip("/")
    if not base:
        return ""
    if p in ("openai-compatible", "openai"):
        return base
    if p in settings_store.OPENAI_PROTOCOL_HOSTS:
        host, _default = settings_store.OPENAI_PROTOCOL_HOSTS[p]
        if base != host.rstrip("/"):
            return base
    return ""


async def _endpoint_models(cfg: dict) -> list[str]:
    """Whatever the operator's own OpenAI-protocol server says it serves.

    Mirrors the station's GET /settings/llm/discover: keyless on purpose — a
    stored provider key must not travel to a custom host just to list models,
    and local servers answer /models unauthenticated anyway. The one
    exception is the openai-compatible provider's own opt-in key, which
    already belongs to that endpoint. Only asked when the list should come
    from the endpoint at all — see _custom_llm_endpoint; an Ollama address
    left in the shared field is not probed as if it were vLLM. No name
    filtering: the ids are whatever the operator loaded into their server.
    """
    import os

    base = _custom_llm_endpoint(cfg)
    if not base:
        return []
    headers = {}
    if (str(cfg.get("llm_provider", "")).lower() == "openai-compatible"
            and os.environ.get("OPENAI_COMPAT_API_KEY")):
        headers["Authorization"] = f"Bearer {os.environ['OPENAI_COMPAT_API_KEY']}"
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.get(f"{base}/models", headers=headers)
            r.raise_for_status()
            return sorted(m["id"] for m in r.json().get("data", []) if m.get("id"))
    except Exception as e:
        log.info("endpoint model list unavailable at %s (%s)", base, describe(e))
        return []


async def _ollama_models(base_url: str) -> list[str]:
    """Whatever is actually pulled on that Ollama box. Far more useful than a
    hardcoded list — it's where the station's own DJ model shows up."""
    root = (base_url or settings_store.provider_base_urls()["ollama"]).rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.get(f"{root}/api/tags")
            r.raise_for_status()
            return sorted(m["name"] for m in r.json().get("models", []) if m.get("name"))
    except Exception as e:
        log.info("ollama model list unavailable at %s (%s)", root, describe(e))
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
    saved_station = settings_store.station_base_url()
    # Let the panel preview a URL it hasn't saved yet.
    for key in ("tts_base_url", "llm_base_url", "station_base_url"):
        if request.query.get(key):
            cfg[key] = request.query[key]

    station = StationClient(base_url=cfg.get("station_base_url"))
    # StationConfig reads admin-only endpoints, so it carries the station
    # password. A previewed URL must not be handed it — see
    # _credentials_travel_to. Without auth it simply reports nothing mirrored,
    # which is the honest answer for a station we cannot log in to.
    station_authed = _is_saved_host(cfg.get("station_base_url"), saved_station)
    station_cfg = StationConfig(
        base_url=cfg.get("station_base_url"), with_auth=station_authed
    )
    try:
        # These four hit four different hosts; serially they add up to several
        # seconds of staring at an empty settings panel.
        # Model lists are discovered from each provider rather than hardcoded —
        # a baked-in list goes stale and produces 404s on retired models.
        (
            personas_raw, voice_source, voices,
            ollama, openai_m, openrouter_m, google_m, anthropic_m, station_llm,
            *protocol_m,
        ) = await asyncio.gather(
            station.personas(),
            station_cfg.voice_source(),
            _tts_voices(cfg.get("tts_base_url", ""), cfg),
            _ollama_models(cfg.get("llm_base_url", "")),
            _openai_models(secrets_store.get("openai_api_key")),
            _openrouter_models(),
            _google_models(secrets_store.get("google_api_key")),
            _anthropic_models(secrets_store.get("anthropic_api_key")),
            station_cfg.llm_config(),
            *(_protocol_models(p) for p in settings_store.OPENAI_PROTOCOL_HOSTS),
            _endpoint_models(cfg),
        )
    finally:
        await station.aclose()
        await station_cfg.aclose()

    personas = [{"id": p.get("id"), "name": p.get("name")} for p in personas_raw if p.get("id")]

    adapters = sorted(
        p.name for p in ADAPTER_DIR.glob("*.json")
        if not p.name.startswith("_")
    )
    # An adapter and an endpoint have to match or the audio comes back at the
    # wrong sample rate and sounds broken while logging nothing — the exact
    # hazard tts_base_url's help warns about. An adapter that knows its own
    # vendor's address says so here, and the panel fills the box in.
    adapter_urls = {}
    for name in adapters:
        try:
            with open(ADAPTER_DIR / name, "r", encoding="utf-8") as f:
                hint = json.load(f).get("base_url")
            if hint:
                adapter_urls[name] = str(hint)
        except Exception as e:                                # noqa: BLE001
            log.info("adapter %s unreadable (%s)", name, describe(e))

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
    # protocol_m is the tail of the gather above: one list per entry in
    # OPENAI_PROTOCOL_HOSTS, then the operator's own custom endpoint.
    endpoint_m = protocol_m[len(settings_store.OPENAI_PROTOCOL_HOSTS)]
    for provider, found in zip(settings_store.OPENAI_PROTOCOL_HOSTS, protocol_m):
        models[provider] = found or models.get(provider, [])
        discovered[provider] = bool(found)
    models.setdefault("openai-compatible", [])
    models.setdefault("locca", [])
    # The endpoint's list WINS for whichever provider is pointed at it: the
    # calls go to that URL, so a dropdown read from the official catalogue
    # offers names the server cannot route (observed with llama-swap: every
    # pick 404'd "no router for requested model"). Mirrors the station's
    # /settings/llm/discover.
    endpoint_provider = ""
    if endpoint_m:
        endpoint_provider = str(cfg.get("llm_provider", "")).lower()
        models[endpoint_provider] = endpoint_m
        discovered[endpoint_provider] = True

    # Only what the operator could actually pick. A provider with no key is not
    # a choice, it is a call that fails at the first turn — and it used to be
    # offered exactly as prominently as the one that works. Whatever is
    # currently configured stays listed even without its key, or the dropdown
    # would show something other than what the next call will use.
    llm_providers = settings_store.providers_with_keys(
        settings_store.LLM_PROVIDER_KEY, cfg.get("llm_provider", ""))
    stt_providers = settings_store.providers_with_keys(
        settings_store.STT_PROVIDER_KEY, cfg.get("stt_provider", ""))

    payload = {
        "llmProviders": llm_providers,
        "llmProviderLabels": settings_store.LLM_PROVIDER_LABELS,
        # Trimmed to the providers above: the page fills the model list from
        # this, and a model list for a provider that cannot be selected is
        # weight on the wire for a dropdown nobody can reach.
        "llmModels": {p: models.get(p, []) for p in llm_providers},
        "providerBaseUrls": settings_store.provider_base_urls(),
        "ttsBaseUrls": settings_store.tts_base_urls(),
        "sttProviders": stt_providers,
        "sttModels": {p: settings_store.STT_MODEL_CHOICES.get(p, [])
                      for p in stt_providers},
        # Which of the five are missing a key, so the panel can say what to add
        # rather than just being shorter than the operator expected.
        "providersNeedingKeys": {
            "llm": [p for p in settings_store.LLM_PROVIDER_KEY
                    if p not in llm_providers],
            "stt": [p for p in settings_store.STT_PROVIDER_KEY
                    if p not in stt_providers],
        },
        # Local first, like the provider lists (0.10.85): the no-account
        # option leads, the hosted one follows.
        "ttsModes": ["local", "cloud"],
        "ttsAdapters": adapters,
        "ttsAdapterBaseUrls": adapter_urls,
        "voices": voices,
        "personas": personas,
        "voiceSource": voice_source,
        "modelsDiscovered": discovered,
        # Which provider's list came from the operator's own endpoint rather
        # than the provider's catalogue, so the panel can say so.
        "modelsFromEndpoint": endpoint_provider,
        "stationLlm": station_llm,
    }

    if not overridden:
        _options_cache["at"] = _time.time()
        _options_cache["data"] = payload

    return _cors(request, web.json_response(payload))
