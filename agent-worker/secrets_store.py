"""
API keys entered from the settings page.

Kept deliberately separate from settings.py. Ordinary settings round-trip
freely between the server and the browser; secrets must never make that return
trip. The rules this module enforces:

  * Stored in their own file (data/secrets.json), chmod 0600 where the OS
    honours it, so settings.json stays safe to copy, diff or paste.
  * Never returned in plaintext. `status()` reports only whether a key is set
    and a fixed-width mask — no real characters and no length, so the display
    can't disclose either.
  * Blank on save means "leave unchanged", NOT "clear" — the UI shows masked
    placeholders, so an untouched field arrives empty and must not wipe a
    working key. Clearing is an explicit, separate action.
  * Never logged. Only field names are ever written to the log.

Precedence matches settings: a key stored here wins over the same key in .env,
and clearing it falls back to .env.
"""

from __future__ import annotations

from jsonstore import write_atomic

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("callin.secrets")

SECRETS_PATH = Path(
    os.environ.get("SECRETS_PATH", Path(__file__).parent.parent / "data" / "secrets.json")
)

# settings field -> environment variable the SDKs actually read
SECRET_FIELDS: dict[str, str] = {
    "openai_api_key": "OPENAI_API_KEY",
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "google_api_key": "GOOGLE_API_KEY",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "requesty_api_key": "REQUESTY_API_KEY",
    "gateway_api_key": "AI_GATEWAY_API_KEY",
    "deepgram_api_key": "DEEPGRAM_API_KEY",
    "tts_api_key": "TTS_API_KEY",
    "elevenlabs_api_key": "ELEVENLABS_API_KEY",
    "fish_api_key": "FISH_API_KEY",
    "subwave_admin_user": "SUBWAVE_ADMIN_USER",
    "subwave_admin_pass": "SUBWAVE_ADMIN_PASS",
}

# Shown in the UI so it's obvious what each key unlocks.
SECRET_LABELS: dict[str, str] = {
    "openai_api_key": "OpenAI",
    "openrouter_api_key": "OpenRouter",
    "anthropic_api_key": "Anthropic",
    "google_api_key": "Google / Gemini",
    "deepseek_api_key": "DeepSeek",
    "requesty_api_key": "Requesty",
    "gateway_api_key": "Vercel AI Gateway",
    "deepgram_api_key": "Deepgram",
    "tts_api_key": "TTS server",
    "elevenlabs_api_key": "ElevenLabs",
    "fish_api_key": "Fish Audio",
    "subwave_admin_user": "Station admin user",
    "subwave_admin_pass": "Station admin password",
}

# Which settings section each key is entered in. There is no "Connections"
# section any more: a key was being asked for on one screen and used on
# another, so choosing a provider meant scrolling away from the choice, adding
# a key to a list of eight, and scrolling back to find out whether the provider
# had appeared. Each key now sits under the thing it unlocks.
#
# One home each, even where a key does two jobs — OpenAI is LLM, cloud TTS and
# cloud STT off the same string, and three input boxes writing one value is
# worse than one box and a sentence saying where it also lands.
SECRET_GROUPS: dict[str, str] = {
    "openai_api_key": "brains",
    "openrouter_api_key": "brains",
    "anthropic_api_key": "brains",
    "google_api_key": "brains",
    "deepseek_api_key": "brains",
    "requesty_api_key": "brains",
    "gateway_api_key": "brains",
    "deepgram_api_key": "ears",
    "tts_api_key": "voice",
    "elevenlabs_api_key": "voice",
    "fish_api_key": "voice",
    "subwave_admin_user": "station",
    "subwave_admin_pass": "station",
}

# One line under each key saying what it buys, on the same row as the box. A
# key list with nothing but vendor names asks the operator to already know
# which of eight vendors this deployment is using.
SECRET_HELP: dict[str, str] = {
    "openai_api_key": "Also powers cloud TTS and OpenAI speech-to-text — one key, three legs of the call.",
    "openrouter_api_key": "One key, ~340 models including free tiers. Its catalogue is public, so the list fills before the key does.",
    "anthropic_api_key": "Claude models.",
    "google_api_key": "Gemini models, and Google speech-to-text.",
    "deepseek_api_key": "DeepSeek chat and reasoner.",
    "requesty_api_key": "Aggregator — many vendors behind one key. Models are read live from your account.",
    "gateway_api_key": "Vercel's aggregator. Models are read live from your account.",
    "deepgram_api_key": "The fastest speech-to-text on a call, and the only one that gives word-by-word captions.",
    "tts_api_key": "Optional, and usually for a self-hosted or local speech server: only if that "
                   "endpoint wants a bearer token of its own. Blank falls back to the OpenAI key "
                   "on an OpenAI host. Test with voice below — the sample plays through this key.",
    "elevenlabs_api_key": "Sent as xi-api-key by the ElevenLabs adapter. Pick that adapter under Backend below.",
    "fish_api_key": "Used by the Fish Audio adapter — the station's third cloud voice, shared here.",
    "subwave_admin_user": "The station's own login — see what it unlocks above.",
    "subwave_admin_pass": "The station's own login — see what it unlocks above.",
}

# Nothing round-trips in the clear — not even the admin username, and not the
# last few characters of a key. The UI gets "set or not" and where it came
# from, which is all it needs to make a decision.
VISIBLE_FIELDS: set[str] = set()

# Fixed-width mask: constant regardless of the real length, so the display
# doesn't leak how long the secret is either.
MASK = "••••••••••••"

_lock = threading.Lock()


def _read() -> dict:
    try:
        with open(SECRETS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        log.warning("secrets file unreadable (%s) — falling back to environment", e)
        return {}


def _write(data: dict) -> None:
    # Owner-only: an API key is the one thing here that must never be
    # world-readable on the shared volume.
    write_atomic(SECRETS_PATH, data, file_mode=0o600, indent=2, sort_keys=True)


def get(field: str) -> str:
    """Resolved value: stored wins, else environment."""
    stored = _read().get(field)
    if stored:
        return str(stored)
    env_var = SECRET_FIELDS.get(field)
    return os.environ.get(env_var, "") if env_var else ""


def _mask(value: str) -> str:
    """No real characters, and no length information."""
    return MASK if value else ""


def status() -> dict:
    """What the UI is allowed to know: set-or-not, where it came from, and a
    fixed mask. Never the value, never its length."""
    stored = _read()
    out = {}
    for field, env_var in SECRET_FIELDS.items():
        from_store = bool(stored.get(field))
        value = str(stored.get(field) or os.environ.get(env_var, ""))
        out[field] = {
            "label": SECRET_LABELS.get(field, field),
            # Which settings section renders the box. The panel groups on this
            # rather than on a list of its own, so a key added here appears
            # under the right heading without a second edit in the browser.
            "group": SECRET_GROUPS.get(field, "brains"),
            "help": SECRET_HELP.get(field, ""),
            "set": bool(value),
            "source": "settings" if from_store else ("env" if value else "unset"),
            "hint": _mask(value),
            "visible": False,
        }
    return out


def save(values: dict, clear: list[str] | None = None) -> dict:
    """Blank values are ignored — see the module docstring. Clearing is only
    ever done through the explicit `clear` list."""
    with _lock:
        data = _read()

        for field in clear or []:
            if field in SECRET_FIELDS:
                data.pop(field, None)

        touched = []
        for field, value in (values or {}).items():
            if field not in SECRET_FIELDS:
                continue
            if value is None or str(value).strip() == "":
                continue  # untouched masked field
            data[field] = str(value).strip()
            touched.append(field)

        _write(data)

    # Field names only — never values.
    if touched:
        log.info("secrets updated: %s", ", ".join(sorted(touched)))
    if clear:
        log.info("secrets cleared: %s", ", ".join(sorted(clear)))

    apply_to_env()
    return status()


# What the environment held before we first overrode a given variable, so that
# clearing a key actually reverts to .env instead of leaving the old value
# resident until restart. Recorded lazily rather than snapshotted at import,
# because load_dotenv() runs after this module is imported.
_overridden: dict[str, str | None] = {}


def apply_to_env() -> None:
    """Push stored keys into the process environment so provider SDKs and the
    TTS adapter pick them up. Called at worker start, before every call, and
    before each test run.

    Also undoes itself: a key that is no longer stored has its original
    environment value put back (or removed), so Clear takes effect immediately.
    """
    stored = _read()
    for field, env_var in SECRET_FIELDS.items():
        value = stored.get(field)

        if value:
            if env_var not in _overridden:
                _overridden[env_var] = os.environ.get(env_var)
            os.environ[env_var] = str(value)
        elif env_var in _overridden:
            original = _overridden.pop(env_var)
            if original is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = original
