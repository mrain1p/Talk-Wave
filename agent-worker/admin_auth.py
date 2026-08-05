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
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError):
        # Deliberately NOT the same answer as "no file". See unreadable().
        return {}


def unreadable() -> str | None:
    """Why the password store cannot be read, or None if it can.

    'No file' and 'a file we cannot read' both used to come back as an empty
    dict, and an empty dict means "no password has been set". That is a gate
    falling open on a configuration error: is_set() goes False,
    _auth_configured() goes False, and the panel drops into first-run mode —
    unauthenticated — while a perfectly good password sits on disk.

    The way to produce one is not exotic. Run the container as a non-root user
    against a data/ whose files root wrote, and every store in it becomes
    unreadable at once. The phone already fails closed here; the panel did not.

    So the two cases are told apart, and a store that exists but will not open
    is treated as "a password IS configured, and nothing can satisfy it" —
    CALLIN_ADMIN_KEY is the way back in, which is what it is for.
    """
    # One read, not two. is_set() and guest_is_set() both call this and then
    # _read(), and both run on every gated request — so the file was being
    # opened twice per check for an answer the first open already had.
    try:
        with open(AUTH_PATH, encoding="utf-8") as f:
            json.load(f)
    except FileNotFoundError:
        return None
    except PermissionError:
        return (f"{AUTH_PATH} exists but cannot be read — check the file's "
                f"owner and mode. Set CALLIN_ADMIN_KEY to get back in.")
    except json.JSONDecodeError:
        return (f"{AUTH_PATH} is not valid JSON, so no password can be "
                f"verified. Set CALLIN_ADMIN_KEY to get back in.")
    except OSError as e:
        return f"{AUTH_PATH} cannot be read ({e}). Set CALLIN_ADMIN_KEY to get back in."
    return None


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
    """True when a password is configured — including when the store exists
    but will not open. An unreadable store is not an absent one, and guessing
    'absent' is the guess that unlocks the panel."""
    if unreadable():
        return True
    return bool(_slot(_read(), "admin").get("hash"))


def guest_is_set() -> bool:
    """Same rule as is_set(), and for a sharper reason.

    `front_access: auto` — the default — is "open until a guest code exists",
    so this answering False is what holds the phone open. An unreadable store
    answering False would swing the line open to anyone the moment a file
    permission went wrong, which is the door-fell-open failure the explicit
    modes were written to avoid. Unreadable means "assume a code is set";
    verify_guest then cannot match it, so the line refuses instead.
    """
    if unreadable():
        return True
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
        # A store we could not read reads as {}, so writing here replaces it —
        # including any guest code in it. That is the CALLIN_ADMIN_KEY recovery
        # path doing its job (nothing else can reach this while the store is
        # unreadable, since verify() cannot succeed), but it is a real loss and
        # it should not happen quietly.
        why = unreadable()
        if why:
            log.warning("replacing an unreadable password store — %s", why)
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
