"""Who may configure this, and who may call it.

Two gates, deliberately separate. ADMIN opens the settings, the keys and the
test endpoints; GUEST opens the phone and nothing else. Both count failures
per address, in their own buckets, so a caller fumbling the door code cannot
lock the operator out of the panel.

The settings surface is deliberately NOT part of the embeddable widget: the
compact/iframe view never renders it, and writes can be locked down with
CALLIN_ADMIN_KEY once this is reachable beyond localhost.
"""

from __future__ import annotations

import hmac
import logging
import os
import time

from aiohttp import web

import admin_auth
from api import wire
from api.live_cache import _live_cache
from api.wire import _auth_key, _caller_key, _cors  # noqa: F401

log = logging.getLogger("callin.token")

ADMIN_KEY = os.environ.get("CALLIN_ADMIN_KEY", "")


# --- panel password + brute-force lockout ---------------------------------
# 5 wrong passwords from one address -> 5-minute cooldown; a second full
# round of failures bans the address until the app restarts. In-memory on
# purpose: `docker restart` (or any redeploy) is the un-ban, and setting
# CALLIN_ADMIN_KEY in the environment is the break-glass password.
_AUTH_MAX_FAILS = 5
_AUTH_COOLDOWN_SECS = 300.0
_AUTH_STRIKES_TO_BAN = 2
_auth_state: dict[str, dict] = {}   # ip -> {fails, strikes, cooldown_until, banned}


def _auth_configured() -> bool:
    return bool(ADMIN_KEY) or admin_auth.is_set()


def _auth_gate(ip: str) -> str | None:
    """A reason this address may not even ATTEMPT a password, or None."""
    st = _auth_state.get(ip)
    if not st:
        return None
    if st.get("banned"):
        return ("too many failed attempts — this address is blocked until "
                "the app restarts")
    wait = st.get("cooldown_until", 0) - time.time()
    if wait > 0:
        return f"too many failed attempts — try again in {int(wait) + 1}s"
    return None


def _auth_fail(ip: str, noun: str = "password") -> str:
    """Record a wrong password; returns the caller-facing message. `noun` so a
    caller who mistypes the door code isn't told about a "password" they were
    never given."""
    st = _auth_state.setdefault(ip, {"fails": 0, "strikes": 0})
    st["fails"] += 1
    if st["fails"] >= _AUTH_MAX_FAILS:
        st["fails"] = 0
        st["strikes"] = st.get("strikes", 0) + 1
        if st["strikes"] >= _AUTH_STRIKES_TO_BAN:
            st["banned"] = True
            log.warning("auth: %s banned until restart after repeated failures", ip)
            return ("too many failed attempts — this address is blocked until "
                    "the app restarts")
        st["cooldown_until"] = time.time() + _AUTH_COOLDOWN_SECS
        log.warning("auth: %s in cooldown after %d failures", ip, _AUTH_MAX_FAILS)
        return f"too many failed attempts — try again in {int(_AUTH_COOLDOWN_SECS)}s"
    left = _AUTH_MAX_FAILS - st["fails"]
    return f"wrong {noun} ({left} tr{'y' if left == 1 else 'ies'} left before a cooldown)"


def _auth_clear(ip: str) -> None:
    _auth_state.pop(ip, None)


def _key_valid(key: str) -> bool:
    if not key:
        return False
    if ADMIN_KEY and hmac.compare_digest(key, ADMIN_KEY):
        return True
    return admin_auth.verify(key)


def _check_admin(request: web.Request) -> bool:
    """Password check with lockout, once a password is configured. Stores a
    caller-facing reason on the request for the shared 401 payload."""
    # The lockout bucket must be unspoofable — see _auth_key. _caller_key
    # (walkable X-Forwarded-For) is fine for pacing, not for a throttle.
    ip = _auth_key(request)
    gate = _auth_gate(ip)
    if gate:
        request["auth_error"] = gate
        request["auth_required"] = True
        return False
    key = request.headers.get("X-Admin-Key", "")
    if _key_valid(key):
        _auth_clear(ip)
        return True
    # A store that exists but will not open makes every password wrong, and
    # "wrong password" sends the operator hunting for the wrong thing. Say what
    # is actually broken — this is reachable only by someone already at the
    # login prompt, and it names a file rather than revealing anything.
    broken = admin_auth.unreadable()
    if broken:
        log.error("password store unreadable: %s", broken)
        request["auth_error"] = broken
        request["auth_required"] = True
        return False
    # An absent key is a login prompt, not a brute-force attempt — only a
    # WRONG key counts toward the lockout.
    request["auth_error"] = _auth_fail(ip) if key else "password required"
    request["auth_required"] = True
    return False


def _guest_check(key: str, ip: str) -> str | None:
    """The front door for CALLING, as opposed to configuring. Returns a
    caller-facing reason to refuse, or None to allow.

    Governed by `front_access`, not by whether a password happens to exist.
    Inferring the policy from "is a guest code set" meant the answer changed
    as a side effect of setting or clearing one, which is not something an
    operator should have to reason about.

        open   anyone who can load the page
        guest  the guest code, or the admin password
        admin  the admin password only

    Kept separate from the panel gate on purpose: the guest code buys you the
    phone, never the controls. Failed attempts are counted under their own key,
    so a caller fumbling the code can't lock the operator out of the panel.
    """
    import settings as settings_store

    mode = str(settings_store.load().get("front_access") or "auto").lower()
    if mode == "open":
        return None
    # The historical rule, kept as the default so an upgrade cannot silently
    # close a line that was working. Here the password decides the policy;
    # in the explicit modes below the policy decides, and a missing password
    # is a misconfiguration rather than an invitation.
    if mode == "auto" and not admin_auth.guest_is_set():
        return None

    # Nothing to check against. Refusing is the safe reading of "a password is
    # required": an unconfigured gate must not silently become an open door.
    if mode == "admin" and not _auth_configured():
        return "the booth line isn't taking calls yet"
    if mode == "guest" and not (admin_auth.guest_is_set() or _auth_configured()):
        return "the booth line isn't taking calls yet"

    bucket = "guest:" + ip
    gate = _auth_gate(bucket)
    if gate:
        return gate
    if key:
        # Admin is accepted in guest mode so an operator carries one password;
        # in admin mode only the admin password opens the phone.
        ok = _key_valid(key) if mode == "admin" else (
            admin_auth.verify_guest(key) or _key_valid(key))
        if ok:
            _auth_clear(bucket)
            return None
    return _auth_fail(bucket, "code") if key else "code required"


def _guest_ok(request: web.Request) -> bool:
    reason = _guest_check(
        request.headers.get("X-Call-Key", ""), _auth_key(request)
    )
    if reason:
        request["auth_error"] = reason
    return reason is None


def caller_tier(request: web.Request) -> str:
    """How much this caller typed to get in: open, guest or admin.

    Decided here and nowhere else, because here is the only place that has
    seen the password. Call it only after _guest_ok has said yes — this
    answers "which caller is this", not "may they call at all", and on its own
    it would happily report `open` for someone who was refused at the door.

    Deliberately generous about which header carries it: the phone sends
    X-Call-Key, but the operator's own browser holds the admin password under
    a different name, and an operator ringing their own booth from the panel's
    preview should not come through as a stranger.
    """
    import settings as settings_store

    # Whether a GUEST caller can exist is the door's answer, not the code's.
    # Since 0.10.66 Guest code and Anyone are one choice apiece (the
    # operator's ask): on an open line the code no longer elevates, so
    # turning the guest pathway off does not require closing the line or
    # deleting the stored code. The admin password is a door in every mode.
    mode = str(settings_store.load().get("front_access") or "auto").lower()
    guest_door = mode == "guest" or (mode == "auto" and admin_auth.guest_is_set())
    for header in ("X-Call-Key", "X-Admin-Key"):
        key = request.headers.get(header, "")
        if not key:
            continue
        if _key_valid(key):
            return "admin"
        if guest_door and admin_auth.verify_guest(key):
            return "guest"
    return "open"


def _is_literal_address(host: str | None) -> bool:
    """True for an IP address, false for a DNS name.

    The distinction matters because only a NAME can be pointed at this box by
    someone else — which is what DNS rebinding does.
    """
    import ipaddress

    if not host:
        return False
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return host in ("localhost",)


def _write_allowed(request: web.Request) -> bool:
    """Gate for the panel: anything that reads config, changes state or
    costs money.

    With a password (or CALLIN_ADMIN_KEY) configured, the password decides —
    with per-IP lockout. Without one (first-run), refuse cross-site browser
    calls: any random web page you visit can POST to localhost, and browsers
    always attach an Origin header to cross-origin requests — so a foreign
    origin is the tell. Same-origin pages and plain tools like curl (no
    Origin) stay allowed.
    """
    if _auth_configured():
        return _check_admin(request)

    origin = request.headers.get("Origin", "")
    if not origin:
        return True
    from urllib.parse import urlparse

    parsed = urlparse(origin)
    # Through the module rather than imported by name, so the list this reads
    # is the one wire.py holds now — a test (and a future reload) can change it.
    if origin in wire.PANEL_ORIGINS:
        return True
    if parsed.hostname in ("localhost", "127.0.0.1", "::1"):
        return True
    # Same origin as the page — but only when the address is a literal one.
    #
    # `netloc == request.host` on its own is a DNS-rebinding hole: an attacker
    # points evil.example at this box, serves a page from it, and the browser
    # then sends BOTH Origin: http://evil.example and Host: evil.example, so
    # the two match and a foreign page reads the settings (including which
    # keys are set) and can write them. A name is what makes that possible;
    # an IP or localhost cannot be rebound to something else. Operators who
    # front this with a real hostname and no password can name it in
    # CALLIN_PANEL_ORIGINS, which is checked above — and setting a password
    # bypasses all of this anyway.
    if parsed.netloc == request.host and _is_literal_address(parsed.hostname):
        return True
    log.warning("blocked cross-origin write from %s", origin)
    request["auth_error"] = "cross-origin request blocked"
    return False


async def handle_set_password(request: web.Request) -> web.Response:
    """Set or change a password. First-run set of the ADMIN password is open
    (same-origin only); changing it requires the current one, with the same
    lockout as every other attempt.

    `scope: "guest"` sets the call-line password instead. That one always
    requires admin — handing out a code that opens the settings panel is the
    single most likely way to get this wrong, so the store refuses a guest
    password that matches the admin one.
    """
    try:
        body = await request.json()
    except Exception:
        return _cors(request, web.json_response({"error": "invalid JSON"}, status=400))

    scope = str((body or {}).get("scope") or "admin").lower()
    new = str((body or {}).get("new") or "")

    if scope == "guest":
        if not _write_allowed(request):
            return _cors(request, web.json_response(
                {"error": request.get("auth_error") or "not allowed",
                 "authRequired": bool(request.get("auth_required"))},
                status=401,
            ))
        if not new:                       # blank clears it — the line reopens
            admin_auth.clear_guest_password()
            _live_cache["data"] = None
            return _cors(request, web.json_response({"ok": True, "guestConfigured": False}))
        if len(new) < 6:
            return _cors(request, web.json_response(
                {"error": "use at least 6 characters"}, status=400))
        try:
            admin_auth.set_guest_password(new)
        except ValueError as e:
            return _cors(request, web.json_response({"error": str(e)}, status=400))
        _live_cache["data"] = None
        return _cors(request, web.json_response({"ok": True, "guestConfigured": True}))

    if len(new) < 8:
        return _cors(request, web.json_response(
            {"error": "use at least 8 characters"}, status=400))

    if _auth_configured():
        ip = _caller_key(request)
        gate = _auth_gate(ip)
        if gate:
            return _cors(request, web.json_response(
                {"error": gate, "authRequired": True}, status=401))
        current = str((body or {}).get("current")
                      or request.headers.get("X-Admin-Key", ""))
        if not _key_valid(current):
            err = _auth_fail(ip) if current else "current password required"
            return _cors(request, web.json_response(
                {"error": err, "authRequired": True}, status=401))
        _auth_clear(ip)
    elif not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed"}, status=401))

    try:
        admin_auth.set_password(new)
    except ValueError as e:
        return _cors(request, web.json_response({"error": str(e)}, status=400))
    _live_cache["data"] = None
    return _cors(request, web.json_response({"ok": True}))


async def handle_guest_login(request: web.Request) -> web.Response:
    """Check a caller's door code. Public by necessity — it's the only way in
    — so it carries the same per-address lockout as every other attempt."""
    try:
        body = await request.json()
    except Exception:
        return _cors(request, web.json_response({"error": "invalid JSON"}, status=400))

    # Same check /token runs, so the two can never disagree about what a good
    # code is.
    reason = _guest_check(
        str((body or {}).get("password") or ""), _caller_key(request)
    )
    if reason is None:
        return _cors(request, web.json_response({"ok": True}))
    return _cors(request, web.json_response(
        {"error": reason, "guestRequired": True}, status=401))
