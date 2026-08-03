"""
Panel password, stored as a salted PBKDF2 hash — never plaintext.

Scope: this guards the settings panel, keys and test endpoints only. The
call card, embed widget, /live and /token stay public by design (usage
limits and the tool allowlist are their protection).

Recovery, in order of preference:
  1. Set CALLIN_ADMIN_KEY in the environment — it is always accepted
     alongside the stored password (break-glass override).
  2. Delete data/admin-auth.json and restart — back to first-run open mode.
IP bans from failed attempts live in memory: restarting the app clears them.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
from pathlib import Path

log = logging.getLogger("callin.auth")

AUTH_PATH = Path(
    os.environ.get("ADMIN_AUTH_PATH", Path(__file__).parent.parent / "data" / "admin-auth.json")
)

_ITERATIONS = 240_000
_lock = threading.Lock()


def _read() -> dict:
    try:
        with open(AUTH_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def is_set() -> bool:
    d = _read()
    return bool(d.get("hash") and d.get("salt"))


def verify(password: str) -> bool:
    d = _read()
    if not (password and d.get("hash") and d.get("salt")):
        return False
    try:
        calc = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(d["salt"]),
            int(d.get("iterations", _ITERATIONS)),
        )
        return hmac.compare_digest(calc.hex(), d["hash"])
    except (ValueError, TypeError):
        return False


def set_password(password: str) -> None:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    with _lock:
        AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = AUTH_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {"hash": digest.hex(), "salt": salt.hex(), "iterations": _ITERATIONS}, f
            )
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(AUTH_PATH)
    log.info("panel password updated")
