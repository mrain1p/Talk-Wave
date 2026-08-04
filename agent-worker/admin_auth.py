"""
Front-door passwords, stored as salted PBKDF2 hashes — never plaintext.

Two independent levels, because they protect different things:

  ADMIN  the settings panel, the keys, the test endpoints. Whoever holds this
         controls the application and can spend your API keys.
  GUEST  the call itself — the Call button, /token, the embed. Optional. Set
         one when the page is reachable from the internet and you want only
         the people you gave the code to able to ring the booth.

Admin implies guest: an operator never needs two passwords to make a call.
The reverse is emphatically not true, which is the whole point of the split —
a guest can use the phone without being handed the controls. They must be
different from each other; set_guest_password refuses a match.

Recovery, in order of preference:
  1. Set CALLIN_ADMIN_KEY in the environment — it is always accepted
     alongside the stored admin password (break-glass override).
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


def _slot(data: dict, scope: str) -> dict:
    """The admin record is the file's top level — that's where it has always
    lived, and existing installs must keep working. Guest lives in a subkey."""
    if scope == "guest":
        sub = data.get("guest")
        return sub if isinstance(sub, dict) else {}
    return data


def _check(record: dict, password: str) -> bool:
    if not (password and record.get("hash") and record.get("salt")):
        return False
    try:
        calc = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(record["salt"]),
            int(record.get("iterations", _ITERATIONS)),
        )
        return hmac.compare_digest(calc.hex(), record["hash"])
    except (ValueError, TypeError):
        return False


def _hash(password: str) -> dict:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return {"hash": digest.hex(), "salt": salt.hex(), "iterations": _ITERATIONS}


def _write(data: dict) -> None:
    AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = AUTH_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(AUTH_PATH)


def is_set() -> bool:
    return bool(_slot(_read(), "admin").get("hash"))


def guest_is_set() -> bool:
    return bool(_slot(_read(), "guest").get("hash"))


def verify(password: str) -> bool:
    return _check(_slot(_read(), "admin"), password)


def verify_guest(password: str) -> bool:
    """Admin opens the guest door too — an operator shouldn't need to carry
    two passwords to place a call."""
    data = _read()
    return _check(_slot(data, "guest"), password) or _check(
        _slot(data, "admin"), password
    )


def set_password(password: str) -> None:
    with _lock:
        data = _read()
        if _check(_slot(data, "guest"), password):
            raise ValueError("the admin password must differ from the guest password")
        data.update(_hash(password))
        _write(data)
    log.info("admin password updated")


def set_guest_password(password: str) -> None:
    """Refuses a guest password that matches the admin one. Sharing the code
    with callers would otherwise hand every one of them the controls — the
    single most likely way to get this wrong."""
    with _lock:
        data = _read()
        if _check(_slot(data, "admin"), password):
            raise ValueError("the guest password must differ from the admin password")
        data["guest"] = _hash(password)
        _write(data)
    log.info("guest password updated")


def clear_guest_password() -> None:
    with _lock:
        data = _read()
        data.pop("guest", None)
        _write(data)
    log.info("guest password cleared — the call line is open again")
