"""
Mirror SUB/WAVE's own DJ + TTS configuration instead of keeping a second copy.

The station is the source of truth for how a persona sounds and how the DJ
behaves. Duplicating that here means two things to keep in sync and a call-in
DJ that drifts from the on-air one. So wherever the station will tell us, we
ask it, and local settings act only as explicit overrides on top.

What we can already read without auth:
  - the live persona and its DJ Card        GET /dj
  - the persona roster and ids              GET /personas
  - the active show and its Show Card       GET /schedule
  - the TTS engine + registered voice ids   GET {TTS}/v1/audio/voices

What needs admin basic auth (SUBWAVE_ADMIN_USER / SUBWAVE_ADMIN_PASS):
  - GET /api/settings   station config: expected to carry the persona->voice
                        mapping and the station's own TTS/LLM choices
  - GET /api/system

Both return 401 without credentials. Until creds are configured this module
degrades to the local `persona-voices.json` fallback and says so in the logs,
rather than guessing at a schema it hasn't seen.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("callin.station_config")

FALLBACK_VOICES_PATH = Path(__file__).parent / "persona-voices.json"

# Key names the station might use for a persona's voice. Checked in order.
_VOICE_KEYS = ("voice", "voiceId", "voice_id", "voiceSample", "ttsVoice", "tts_voice")

# Likewise for the station's own LLM configuration.
_MODEL_KEYS = ("llmModel", "llm_model", "model", "ollamaModel", "chatModel", "djModel")
_LLM_URL_KEYS = ("llmBaseUrl", "llm_base_url", "ollamaUrl", "ollama_url", "llmUrl")


# Subtrees that carry model NAMES which are not the DJ's chat model — the
# embedding/search/tagger configs all have "model" keys, and a blind DFS was
# observed reporting the station's embedding model as its DJ model.
_SKIP_SUBTREES = ("embed", "search", "tagger", "tts")


def _find_first(node: Any, keys: tuple[str, ...]) -> str | None:
    """Depth-first search for the first non-empty string under any of `keys`.
    The station's settings shape isn't documented, so this looks for the value
    rather than assuming a path."""
    if isinstance(node, dict):
        for key in keys:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for name, value in node.items():
            if any(s in str(name).lower() for s in _SKIP_SUBTREES):
                continue
            found = _find_first(value, keys)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_first(item, keys)
            if found:
                return found
    return None


def admin_credentials() -> tuple[str, str]:
    """Read at call time, not import time — these can now be set from the
    settings page while the process is running."""
    import secrets_store

    return secrets_store.get("subwave_admin_user"), secrets_store.get("subwave_admin_pass")


def has_admin() -> bool:
    user, password = admin_credentials()
    return bool(user and password)


def mcp_headers() -> dict[str, str]:
    """Auth for the MCP connection.

    Several station tools are admin-gated — `subwave_search_library` among
    them — and the controller rejects them outright without credentials. The
    MCP transport is plain HTTP, so the credentials go on as a Basic
    Authorization header rather than through httpx's auth handling.
    """
    user, password = admin_credentials()
    if not (user and password):
        return {}
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _fallback_voices() -> dict:
    """persona-voices.json is operator-local (it names their personas and
    voice samples, so it isn't committed). A fresh checkout falls back to the
    example file, and failing that to a stock cloud voice — a missing file
    must never stop a call from connecting."""
    for path in (FALLBACK_VOICES_PATH,
                 FALLBACK_VOICES_PATH.with_name("persona-voices.example.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            log.debug("voice fallback %s unusable: %s", path.name, e)
    return {"default": {"name": "Station DJ", "local_voice": "", "cloud_voice": "alloy"}}


def _find_voice(node: Any) -> str | None:
    if not isinstance(node, dict):
        return None
    for key in _VOICE_KEYS:
        value = node.get(key)
        if isinstance(value, str) and value:
            return value
    # SUB/WAVE nests it one level down: personas[].tts.voice — checking only
    # the top level made mirroring silently find nothing.
    sub = node.get("tts")
    if isinstance(sub, dict):
        for key in _VOICE_KEYS:
            value = sub.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _extract_persona_voices(settings: dict) -> dict[str, str]:
    """Pull a persona_id -> voice map out of the station settings payload
    without assuming exactly where it lives. Handles the two shapes this can
    plausibly take: a list of persona objects, or a dict keyed by persona id.
    """
    found: dict[str, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                if isinstance(item, dict):
                    pid = item.get("id") or item.get("personaId")
                    voice = _find_voice(item)
                    if isinstance(pid, str) and pid.startswith("p_") and voice:
                        found[pid] = voice
                walk(item)
        elif isinstance(node, dict):
            for key, value in node.items():
                if key.startswith("p_") and isinstance(value, (dict, str)):
                    voice = value if isinstance(value, str) else _find_voice(value)
                    if voice:
                        found[key] = voice
                walk(value)

    walk(settings)
    return found


class StationConfig:
    """Reads the station's own config. Every method is best-effort — a call
    must still connect if the station is mid-restart or creds are absent."""

    def __init__(self, base_url: str | None = None, timeout: float = 8.0) -> None:
        import settings as settings_store

        user, password = admin_credentials()
        auth = httpx.BasicAuth(user, password) if user and password else None
        self._client = httpx.AsyncClient(
            base_url=base_url or settings_store.station_base_url(),
            timeout=timeout,
            auth=auth,
        )
        self._cache: dict[str, Any] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str) -> dict:
        if path in self._cache:
            return self._cache[path]
        try:
            r = await self._client.get(path)
            if r.status_code == 401:
                log.info(
                    "%s needs admin credentials — set SUBWAVE_ADMIN_USER/PASS to "
                    "mirror the station's own config instead of the local fallback",
                    path,
                )
                return {}
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.warning("station config read %s failed: %s", path, e)
            return {}
        self._cache[path] = data
        return data

    async def settings(self) -> dict:
        return await self._get("/settings") if has_admin() else {}

    async def persona_voices(self) -> dict[str, str]:
        """persona_id -> voice id, mirrored from the station when possible."""
        station = await self.settings()
        if station:
            mapped = _extract_persona_voices(station)
            if mapped:
                log.info("mirroring %d persona voices from station settings", len(mapped))
                return mapped
            log.info(
                "station settings readable but no persona->voice mapping recognised; "
                "using local persona-voices.json"
            )

        fallback = _fallback_voices()
        import settings as settings_store

        mode = settings_store.tts_mode()
        key = "local_voice" if mode == "local" else "cloud_voice"
        return {
            pid: entry[key]
            for pid, entry in fallback.items()
            if isinstance(entry, dict) and entry.get(key)
        }

    async def llm_config(self) -> dict:
        """What model the station itself runs its DJ on, so the call-in agent
        can default to the same thing rather than diverging.

        Needs admin credentials. Without them we report what we can infer and
        say plainly that it's a guess — the sidecar should not silently pretend
        to match the station when it can't actually read the setting.
        """
        result: dict = {"model": None, "baseUrl": None, "source": "unknown"}

        settings = await self.settings()
        if settings:
            model = _find_first(settings, _MODEL_KEYS)
            base = _find_first(settings, _LLM_URL_KEYS)
            if model:
                result.update({"model": model, "baseUrl": base, "source": "station"})
                log.info("station DJ model: %s", model)
                return result
            result["source"] = "station-unreadable"

        return result

    async def voice_source(self) -> dict:
        """Where persona voices are actually coming from, for the settings UI."""
        mirrored: dict[str, str] = {}
        if has_admin():
            station = await self.settings()
            if station:
                mirrored = _extract_persona_voices(station)

        return {
            "adminConfigured": has_admin(),
            "mirroringStation": bool(mirrored),
            "count": len(mirrored),
        }

    async def voice_for(self, persona_id: str) -> str:
        voices = await self.persona_voices()
        if voices.get(persona_id):
            return voices[persona_id]

        import settings as settings_store

        mode = settings_store.tts_mode()
        fallback = _fallback_voices().get("default", {})
        voice = str(fallback.get("local_voice" if mode == "local" else "cloud_voice") or "")
        if voice:
            return voice

        # Fresh deployment, local TTS, no voice configured anywhere: sending
        # an empty voice just 400s on every call. Ask the TTS server what it
        # actually has and use its first voice — a clean install must speak.
        if mode == "local":
            cfg = settings_store.load()
            base = str(cfg.get("tts_base_url") or "").rstrip("/")
            if base:
                try:
                    # Fresh client: self._client carries the station's Basic
                    # auth, which must not be sent to the TTS host.
                    async with httpx.AsyncClient(timeout=6.0) as c:
                        r = await c.get(f"{base}/v1/audio/voices")
                    r.raise_for_status()
                    ids = sorted(
                        v.get("id") for v in r.json().get("data", []) if v.get("id")
                    )
                    if ids:
                        log.warning(
                            "no persona voice configured — falling back to the "
                            "TTS server's first voice '%s'; set one in the panel "
                            "or add station admin credentials to mirror voices",
                            ids[0],
                        )
                        return ids[0]
                except Exception as e:
                    log.warning("voice-list fallback failed: %s", e)
            return ""

        return "alloy"
