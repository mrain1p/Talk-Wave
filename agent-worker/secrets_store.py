"""
API keys entered from the settings page.

Kept deliberately separate from settings.py. Ordinary settings round-trip
freely between the server and the browser; secrets must never make that return
trip. The rules this module enforces:

  * Stored in their own file (data/secrets.json), chmod 0600 where the OS
    honours it, so settings.json stays safe to copy, diff or paste.
  * Never returned in plaintext. `status()` reports only whether a key is set
    and its last four characters, which is enough to tell two keys apart
    without disclosing either.
  * Blank on save means "leave unchanged", NOT "clear" — the UI shows masked
    placeholders, so an untouched field arrives empty and must not wipe a
    working key. Clearing is an explicit, separate action.
  * Never logged. Only field names are ever written to the log.

Precedence matches settings: a key stored here wins over the same key in .env,
and clearing it falls back to .env.
"""

from __future__ import annotations

import json
import logging
import os
import stat
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
    "deepgram_api_key": "DEEPGRAM_API_KEY",
    "google_api_key": "GOOGLE_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "tts_api_key": "TTS_API_KEY",
    "subwave_admin_user": "SUBWAVE_ADMIN_USER",
    "subwave_admin_pass": "SUBWAVE_ADMIN_PASS",
}

# Shown in the UI so it's obvious what each key unlocks.
SECRET_LABELS: dict[str, str] = {
    "openai_api_key": "OpenAI (LLM + cloud TTS)",
    "openrouter_api_key": "OpenRouter (many models, one key)",
    "deepgram_api_key": "Deepgram (speech-to-text)",
    "google_api_key": "Google / Gemini",
    "anthropic_api_key": "Anthropic",
    "tts_api_key": "TTS server (if separate from OpenAI)",
    "subwave_admin_user": "Station admin user",
    "subwave_admin_pass": "Station admin password",
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
    SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SECRETS_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    try:
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass  # best effort; Windows ACLs don't map cleanly
    tmp.replace(SECRETS_PATH)


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
    masked tail. Never the value itself."""
    stored = _read()
    out = {}
    for field, env_var in SECRET_FIELDS.items():
        from_store = bool(stored.get(field))
        value = str(stored.get(field) or os.environ.get(env_var, ""))
        out[field] = {
            "label": SECRET_LABELS.get(field, field),
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
