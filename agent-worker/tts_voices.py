"""
The call TTS's adapter config and voice discovery: which file describes a
backend (resolve_adapter, load_adapter), which key it is sent (adapter_api_key,
adapter_headers), and which voices it can actually speak (parse_voice_list,
available_voices, pick_speakable_voice).

Split from tts_adapter.py along the seam its ledger entry named: discovery
never reads the synthesis class, and the class needs only the config helpers
back. tts_adapter re-exports every name here, so a caller still imports from
one place. An empty voice list means "could not find out", never "the backend
has no voices"; callers must not collapse the two.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx

from log_setup import describe

log = logging.getLogger("callin.agent")

ADAPTER_DIR = Path(__file__).parent / "tts-adapters"


def _default_adapter_path(mode: str = "") -> Path:
    """The adapter to use when none was named.

    `mode` is passed in now rather than read back out of os.environ. Four
    different places used to write os.environ["TTS_MODE"] purely so this line
    could read it — a setting laundered through process-global state with no
    owner, and in the token server that state is shared by every concurrent
    request, so two operators testing different backends raced each other.
    The environment remains the fallback for a worker that has not been told.
    """
    explicit = os.environ.get("TTS_ADAPTER_CONFIG")
    if explicit:
        return Path(explicit)
    # Default matches settings.py ("cloud"); these disagreed previously, so a
    # caller that hadn't set TTS_MODE got the local adapter while the rest of
    # the app assumed cloud.
    mode = (mode or os.environ.get("TTS_MODE", "cloud")).lower()
    return ADAPTER_DIR / ("local-vibevoice.json" if mode == "local" else "openai-cloud.json")


def _is_openai_host(base_url: str) -> bool:
    """Is this URL actually OpenAI's own API, rather than something whose
    hostname merely contains that string?"""
    from urllib.parse import urlparse

    host = (urlparse(str(base_url or "")).hostname or "").lower()
    return host == "api.openai.com" or host.endswith(".api.openai.com")


def adapter_api_key(adapter: dict, base_url: str = "", allow_stored: bool = True) -> str:
    """The key this backend wants, from the environment.

    Most adapters describe an OpenAI-shaped endpoint and take TTS_API_KEY (or
    the OpenAI key, on an OpenAI host — the README promises one key covers
    cloud TTS). A vendor with a key of its own says so with `auth.key_env`,
    which is what lets ElevenLabs sit beside the generic adapters instead of
    needing the operator to paste the same key into TTS_API_KEY and lose the
    ability to use both.
    """
    if not allow_stored:
        return ""
    auth = adapter.get("auth") or {}
    named = str(auth.get("key_env") or "").strip()
    if named:
        return os.environ.get(named, "")
    key = os.environ.get("TTS_API_KEY", "")
    if not key and _is_openai_host(base_url):
        key = os.environ.get("OPENAI_API_KEY", "")
    return key


def adapter_headers(adapter: dict, api_key: str) -> dict:
    auth = adapter.get("auth", {"type": "none"})
    kind = auth.get("type", "none")
    if not api_key:
        return {}
    if kind == "bearer":
        return {"Authorization": f"Bearer {api_key}"}
    if kind == "header":
        return {auth.get("header_name", "X-API-Key"): api_key}
    return {}


def parse_voice_list(data: object, prefer: str = "") -> list[str]:
    """Voice ids out of whatever shape the backend answered with.

    `prefer` names the field that IS the id when a backend's catalogue carries
    both an id and a display name. ElevenLabs is the case: its entries are
    `{voice_id, name, ...}`, only voice_id is addressable, and the default
    order below would pick `name` and hand the caller a list of labels that
    every synthesis request then 404s on.

    There is no standard here, and at least four shapes are in the wild:
    OpenAI's {"data": [{"id": ...}]}, a bare ["name", ...], {"voices": [...]}
    with either dicts or strings inside, and a mapping of id -> details.

    Reading only the first is worse than it sounds. An empty list means "could
    not find out" everywhere in this file, so a backend that answers its voice
    list perfectly well in the wrong shape does not read as "unknown voices" —
    it silently disables pick_speakable_voice, the panel's dropdown falls back
    to stock OpenAI names, and the station's voice goes to a backend that
    never had it. Tolerating the shapes costs nothing and means most new
    backends need no adapter entry at all.
    """
    if isinstance(data, dict):
        for key in ("data", "voices", "results", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            # A mapping of id -> details. Every value being a dict is what
            # distinguishes it from an error envelope like {"detail": "..."},
            # which would otherwise offer "detail" as a voice.
            if data and all(isinstance(v, dict) for v in data.values()):
                data = list(data.keys())

    if not isinstance(data, list):
        return []

    found: set[str] = set()
    for item in data:
        if isinstance(item, str):
            if item.strip():
                found.add(item.strip())
        elif isinstance(item, dict):
            for key in ([prefer] if prefer else []) + ["id", "name", "voice", "voice_id"]:
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    found.add(value.strip())
                    break
    return sorted(found)


async def available_voices(
    base_url: str,
    timeout: float = 6.0,
    adapter_path: str | Path | None = None,
    mode: str = "",
    allow_stored: bool = True,
) -> list[str]:
    """What the TTS backend at `base_url` says it can actually speak in.

    An empty list means "could not find out" and never "has none" — the caller
    must treat those differently, because refusing to speak on a failed lookup
    would turn a slow TTS server into a silent call.

    Lives here rather than in token_server because the WORKER needs it too:
    the panel showing a voice list the worker never consults is how a call
    ends up trying a voice the backend does not have.

    The path comes from the adapter, because discovery is as backend-specific
    as synthesis and this file only ever described the second half. A backend
    that serves its list at /voices rather than /v1/audio/voices looked
    identical to one that was down.
    """
    # A backend whose voices are a fixed set it does not serve a list of —
    # Gemini's thirty prebuilt voices; its /voices is the Live catalogue, which
    # omits Puck and Kore entirely — names them in its adapter. The list is the
    # answer, in the adapter's own order: its first entry is the voice
    # pick_speakable_voice falls back to.
    try:
        named = load_adapter(adapter_path, mode=mode).get("voices")
    except Exception:                                         # noqa: BLE001
        named = None    # reported below, where the lookup reads it again
    if isinstance(named, list) and named:
        return [str(v).strip() for v in named if str(v).strip()]
    if not base_url:
        return []
    # A trailing slash here produced `http://host:8001//v1/audio/voices`, which
    # some servers route and some 404 — so whether the panel could list voices
    # at all depended on a character nobody could see. AdapterTTS already
    # strips it; this was the one path that didn't.
    base_url = base_url.rstrip("/")
    path = "/v1/audio/voices"
    headers: dict = {}
    prefer = ""
    try:
        adapter = load_adapter(adapter_path, mode=mode)
        path = str(adapter.get("voices_path") or path)
        prefer = str(adapter.get("voices_id_field") or "")
        # Authenticated, because some catalogues are. ElevenLabs answers
        # /v1/voices with a 401 and no body without xi-api-key, and an empty
        # list here means "could not find out" — so the panel would have shown
        # eleven stock OpenAI voice names for a backend that has none of them,
        # and the first call would have failed on a voice that never existed.
        # …but only to a host the operator has SAVED. `base_url` reaches here
        # from a ?tts_base_url= the panel is previewing, and this lookup used
        # to hand that stranger the stored TTS/ElevenLabs key — invariant 4,
        # the same rule the Test button already obeyed. Caller decides.
        headers = adapter_headers(
            adapter, adapter_api_key(adapter, base_url, allow_stored=allow_stored))
    except Exception as e:                                    # noqa: BLE001
        # An unreadable adapter is the caller's problem to report, not a
        # reason to skip the lookup with the default path.
        log.info("adapter unreadable for voice discovery (%s)", describe(e))
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as c:
            r = await c.get(path, headers=headers)
            r.raise_for_status()
            return parse_voice_list(r.json(), prefer=prefer)
    except Exception as e:                                    # noqa: BLE001
        log.info("voice list unavailable from %s%s (%s)", base_url, path, e)
        return []


def pick_speakable_voice(wanted: str, available: list[str]) -> tuple[str, str]:
    """(voice to use, why it changed). An empty reason means it did not.

    The station tells us which voice each DJ uses ON AIR, and mirroring that is
    right — the call-in DJ should sound like the one broadcasting. But the
    station's voice belongs to the station's TTS, and this service may be
    pointed at a different one. Rosie's station voice is an ElevenLabs id;
    against local VibeVoice every request 400s, so the DJ generated a perfectly
    good greeting and the caller heard silence for the whole call. Even the
    dead-air fallback was mute, because it speaks through the same backend.

    A voice the backend does not have is therefore not a reason to say nothing.
    It is a reason to say it in a different voice and to write down why.
    """
    wanted = str(wanted or "").strip()
    if not available:
        return wanted, ""            # lookup failed — not evidence of anything
    if wanted and wanted in available:
        return wanted, ""
    fallback = available[0]
    if not wanted:
        return fallback, ""          # nothing asked for; nothing surprising
    return fallback, (
        f"The station uses voice {wanted!r} for this DJ, and the TTS backend "
        f"does not have it — speaking as {fallback!r} instead. Every line would "
        f"otherwise have failed and the caller would have heard nothing. Set "
        f"Voice under Models & voice to choose deliberately, or point this at "
        f"the TTS server the station itself uses."
    )


def resolve_adapter(value: str | None) -> str | None:
    """The adapter file a setting names, constrained to ADAPTER_DIR.

    `tts_adapter` reaches this from saved settings *and* from the body of
    /test/tts and /test/speed, which is a request. The resolution used to be
    the same three lines copied into three modules, and all three read "join
    it to ADAPTER_DIR unless it is absolute" — so an absolute path went
    straight to open(), and a relative one with ../ in it walked out of the
    directory before the exists() check ever looked. A request could name any
    file on the disk and learn whether it existed and whether it parsed as
    JSON, which in first-run mode needs no password at all.

    Same shape as _safe_sound_name: one flat directory, a known extension,
    nothing that can point elsewhere. The panel only ever offers a filename
    out of ADAPTER_DIR.glob("*.json"), so nothing legitimate is lost.

    The one exception is TTS_ADAPTER_CONFIG. That is set at deploy time by
    whoever runs the container, not by a request, and pointing it at a mounted
    file outside the image is a supported thing to do — so an absolute path is
    honoured when it is *exactly* that value and never otherwise.
    """
    name = str(value or "").strip()
    if not name:
        return None

    from_env = str(os.environ.get("TTS_ADAPTER_CONFIG") or "").strip()
    if from_env and name == from_env:
        return name

    if name != Path(name).name or not name.lower().endswith(".json"):
        log.warning(
            "ignoring tts adapter %r — it must be a .json filename in %s, "
            "with no path in it", name, ADAPTER_DIR,
        )
        return None
    candidate = ADAPTER_DIR / name
    try:
        if candidate.resolve().parent != ADAPTER_DIR.resolve():
            return None
    except OSError:
        return None
    return str(candidate) if candidate.is_file() else None


def load_adapter(path: str | Path | None = None, mode: str = "") -> dict:
    p = Path(path) if path else _default_adapter_path(mode)
    with open(p, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # The one key with no sensible default: without a path to POST audio to,
    # the backend cannot be reached. Fail here, naming the file, rather than a
    # KeyError deep in the first synthesis (the report-only type check is blind
    # across this untyped-dict seam, so this is the guard that catches it).
    if "endpoint_path" not in cfg or not str(cfg["endpoint_path"]).strip():
        raise ValueError(
            f"TTS adapter {p} is missing 'endpoint_path' — the URL path audio "
            "requests are POSTed to. Add it to the adapter JSON.")
    cfg.setdefault("method", "POST")
    cfg.setdefault("static_fields", {})
    cfg.setdefault("auth", {"type": "none"})
    cfg.setdefault("response", {"type": "raw_audio"})
    cfg.setdefault("audio", {"encoding": "pcm", "sample_rate": 24000, "num_channels": 1})
    cfg.setdefault("voices_path", "/v1/audio/voices")
    return cfg
