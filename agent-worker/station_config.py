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
  - the TTS engine + registered voice ids   via tts_adapter.available_voices,
                                            whose path the adapter names

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

from log_setup import describe

log = logging.getLogger("callin.station_config")

FALLBACK_VOICES_PATH = Path(__file__).parent / "persona-voices.json"

# The last persona->voice map that mirrored from the station, on disk so it
# survives across the per-call job processes and covers a station that times
# out mid-mirror. Sibling of station.py's last-persona cache, same reason.
import json as _json
import os as _os
import time as _time

_VOICE_CACHE = Path(
    _os.environ.get("LAST_VOICES_PATH",
                    Path(__file__).parent.parent / "data" / "last-voices.json")
)
_VOICE_TTL = 1800.0     # half an hour — voices change far less often than shows


# What the last mirror actually said, so an unchanged one can stay quiet.
# "mirroring 18 persona voices from station settings" is worth saying when the
# answer CHANGES; every settings read repeating it identically (twice in the
# same second, in the operator's log viewer) is noise standing where an event
# should be.
_LAST_MIRRORED: dict = {}


def _note_mirrored(mapped: dict) -> None:
    global _LAST_MIRRORED
    if mapped == _LAST_MIRRORED:
        log.debug("persona voices unchanged (%d mirrored)", len(mapped))
        return
    if _LAST_MIRRORED:
        log.info("mirroring %d persona voices from station settings "
                 "(changed from %d)", len(mapped), len(_LAST_MIRRORED))
    else:
        log.info("mirroring %d persona voices from station settings", len(mapped))
    _LAST_MIRRORED = dict(mapped)


def _remember_voices(mapped: dict) -> None:
    try:
        _VOICE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _VOICE_CACHE.write_text(_json.dumps({"voices": mapped, "at": _time.time()}))
    except Exception as e:                                    # noqa: BLE001
        log.debug("could not remember mirrored voices (harmless): %s", e)


def _recall_voices() -> dict | None:
    try:
        d = _json.loads(_VOICE_CACHE.read_text())
        if _time.time() - float(d.get("at") or 0) < _VOICE_TTL:
            return d.get("voices") or None
    except Exception:                                         # noqa: BLE001
        pass
    return None

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
                    if (isinstance(pid, str) and pid.startswith("p_")
                            and voice and pid not in found):
                        found[pid] = voice
                walk(item)
        elif isinstance(node, dict):
            for key, value in node.items():
                # The settings payload carries a `defaults` subtree with the
                # FACTORY voices (kokoro ids like bm_daniel). Walking into it
                # let them clobber the operator's real choices — observed as
                # Brock mirroring bm_daniel instead of his configured
                # -Brock1, which the local TTS then 400s on.
                if str(key).lower() == "defaults":
                    continue
                if key.startswith("p_") and isinstance(value, (dict, str)):
                    voice = value if isinstance(value, str) else _find_voice(value)
                    if voice and key not in found:
                        found[key] = voice
                walk(value)

    walk(settings)
    return found


def _persona_skills_from(settings: dict, persona_id: str) -> list[str] | None:
    """Which segment slugs a persona is assigned, or None for "all of them".

    The station stores this as `personas[].skills`. **Absent and null both mean
    "runs everything"** — its seeded roster carries no `skills` key at all until
    the operator saves personas once, and every reader on its side treats falsy
    as unrestricted. A strict `is None` check here would read a fresh station's
    roster as "this DJ runs nothing".
    """
    if not persona_id:
        return None
    for persona in (settings.get("personas") or []):
        if not isinstance(persona, dict) or persona.get("id") != persona_id:
            continue
        assigned = persona.get("skills")
        if not isinstance(assigned, list):
            return None
        return [str(s) for s in assigned if isinstance(s, str)]
    return None


def runnable_skills(catalogue: list[dict],
                    assigned: list[str] | None) -> list[dict]:
    """The segments this DJ may actually run, out of the station's catalogue.

    The catalogue is every skill the station HAS. Three things in it are not
    things this caller should be offered, and until 0.10.132 the call line
    honoured none of them — it put the whole catalogue in the prompt and let
    `POST /dj/skill` run whatever came back, which is an operator override that
    fires a skill *even when it is switched off*:

      * `enabled: false` — the operator turned it off. A caller could still run
        it, which is the operator's own switch being ignored on their own
        station.
      * `ready: false` — it needs an API key that isn't set. Offering it buys
        one confident "let me get the news" and then a failure.
      * assigned to other personas — SUB/WAVE lets a skill belong to some DJs
        and not others, so a caller could make tonight's host run a segment
        that belongs to someone else's show.

    `cronOnly` is checked too, and deliberately in advance: upstream #1379 added
    it to withhold a clock-pinned skill from the station's own random picks, but
    `skillCatalog()` does not publish the field, so nothing here can see it yet.
    Reading it now means the day the station starts sending it, a segment
    written for 7:10am stops being firable by a caller at one in the afternoon —
    with no change on this side.
    """
    allowed = None if assigned is None else {str(s) for s in assigned}
    out: list[dict] = []
    for skill in (catalogue or []):
        if not isinstance(skill, dict):
            continue
        if skill.get("enabled") is False or skill.get("ready") is False:
            continue
        if skill.get("cronOnly") is True:
            continue
        name = str(skill.get("name") or skill.get("kind") or "")
        if allowed is not None and name and name not in allowed:
            continue
        out.append(skill)
    return out


class StationConfig:
    """Reads the station's own config. Every method is best-effort — a call
    must still connect if the station is mid-restart or creds are absent."""

    def __init__(
        self, base_url: str | None = None, timeout: float = 4.5,
        with_auth: bool = True,
    ) -> None:
        """`with_auth=False` reads the station without logging in.

        The station password belongs to the station in the saved settings and
        nowhere else. A caller pointing this at some other base_url — which the
        panel does when previewing an unsaved URL — must not have the password
        sent along with it.

        4.5s, down from 8s (2026-08-10), to match StationClient. This read
        (the persona-voices mirror) sits on the call-setup path, and at 8s a
        slow station added most of a second timeout's worth of ringing AFTER
        the snapshot. persona_voices has its own last-known-good disk cache, so
        a miss falls to the right voices fast rather than making the caller
        wait.
        """
        import settings as settings_store

        user, password = admin_credentials() if with_auth else ("", "")
        auth = httpx.BasicAuth(user, password) if user and password else None
        self._client = httpx.AsyncClient(
            base_url=base_url or settings_store.station_base_url(),
            timeout=timeout,
            auth=auth,
        )
        self._cache: dict[str, Any] = {}
        self._authed = bool(auth)

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
            log.warning("station config read %s failed: %s", path, describe(e))
            return {}
        self._cache[path] = data
        return data

    def prime(self, path: str, payload: dict) -> None:
        """Adopt a response fetched by ANOTHER process as this client's cache.

        The mint-time snapshot prefetch (station_prefetch.py) reads /settings
        in the token server a second or two before the worker would read it
        again; priming it here means persona_voices, persona_skills, voice_for
        and speak_clock — which all deliberately ride the one cached /settings
        read — cost the call no network at all.

        Only a non-empty payload, and only when this client is authed: {} is
        what a failed or unauthenticated read looks like, and caching one
        would stop persona_voices falling back to the last-known-good voices
        exactly when that fallback is the point.
        """
        if self._authed and isinstance(payload, dict) and payload:
            self._cache[path] = payload

    async def settings(self) -> dict:
        """Admin-only, so it needs THIS client to be carrying credentials —
        not merely for some to exist in the store."""
        return await self._get("/settings") if self._authed else {}

    async def persona_skills(self, persona_id: str) -> list[str] | None:
        """The segment slugs assigned to one persona, or None for "all".

        Rides the same cached `/settings` read the voice mirror already warms,
        so on a call this costs no network at all. None is also what an
        unreadable station gives back — the safe answer, because it means "do
        not narrow the catalogue" rather than "this DJ runs nothing".
        """
        station = await self.settings()
        if not station:
            return None
        return _persona_skills_from(station, persona_id)

    async def persona_voices(self) -> dict[str, str]:
        """persona_id -> voice id, mirrored from the station when possible."""
        station = await self.settings()
        if station:
            mapped = _extract_persona_voices(station)
            if mapped:
                _note_mirrored(mapped)
                _remember_voices(mapped)
                return mapped
            log.info(
                "station settings readable but no persona->voice mapping recognised; "
                "using local persona-voices.json"
            )
        else:
            # The station read timed out (observed 2026-08-10: /settings
            # ReadTimeout under load). Reuse the voices last mirrored to disk
            # before falling to the generic first voice — a caller heard the
            # wrong DJ's voice (-Brock1 for Cliff) on exactly this path, and a
            # slightly stale voice map beats the wrong voice entirely.
            recalled = _recall_voices()
            if recalled:
                log.warning("station settings unavailable — reusing the last "
                            "mirrored voices (%d)", len(recalled))
                return recalled

        fallback = _fallback_voices()
        import settings as settings_store

        mode = settings_store.tts_mode()
        key = "local_voice" if mode == "local" else "cloud_voice"
        return {
            pid: entry[key]
            for pid, entry in fallback.items()
            if isinstance(entry, dict) and entry.get(key)
        }

    async def speak_clock(self) -> bool:
        """Whether the station's DJ may name the time of day (djSpeakClock,
        SUB/WAVE 1.8). Mirrored so a station that has gone clockless doesn't
        find the call-in DJ is the one voice still announcing the hour.
        Defaults ON like the station's own coercion: absent, non-boolean,
        unreadable or unauthed all mean the switch effectively doesn't exist.
        """
        settings = await self.settings()

        def find(node):
            if isinstance(node, dict):
                if isinstance(node.get("djSpeakClock"), bool):
                    return node["djSpeakClock"]
                for v in node.values():
                    got = find(v)
                    if got is not None:
                        return got
            elif isinstance(node, list):
                for v in node:
                    got = find(v)
                    if got is not None:
                        return got
            return None

        found = find(settings)
        return True if found is None else found

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
                # tts_adapter owns the lookup — this was a second copy of it,
                # hardcoded to /v1/audio/voices and OpenAI's response shape,
                # so a backend that lists its voices anywhere else answered
                # "no voices" here while the panel next door listed them fine.
                # It also uses its own client, which is what keeps the
                # station's Basic auth away from the TTS host.
                from tts_adapter import available_voices, resolve_adapter

                ids = await available_voices(
                    base,
                    adapter_path=resolve_adapter(cfg.get("tts_adapter")),
                    mode=mode,
                )
                if ids:
                    log.warning(
                        "no persona voice configured — falling back to the "
                        "TTS server's first voice '%s'; set one in the panel "
                        "or add station admin credentials to mirror voices",
                        ids[0],
                    )
                    return ids[0]
            return ""

        return "alloy"
