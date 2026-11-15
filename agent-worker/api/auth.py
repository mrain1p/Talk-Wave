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
from api.wire import _auth_key, _cors, refused  # noqa: F401

log = logging.getLogger("callin.token")

ADMIN_KEY = os.environ.get("CALLIN_ADMIN_KEY", "")


# --- panel password + brute-force lockout ---------------------------------
# 5 wrong passwords from one address -> 5-minute cooldown; a second full
# round of failures bans the address until the app restarts. In-memory on
# purpose: `docker restart` (or any redeploy) is the un-ban, and setting
# CALLIN_ADMIN_KEY in the environment is the break-glass password — the one
# credential that is compared BEFORE this gate, so it works from a locked-out
# address too (see _break_glass).
_AUTH_MAX_FAILS = 5
_AUTH_COOLDOWN_SECS = 300.0
_AUTH_STRIKES_TO_BAN = 2
# The table used to keep one entry per failing address for the life of the
# process: nothing but a success ever removed one, so a scan from many
# addresses grew it without bound, and a strike earned in March still counted
# toward a ban in June (review, 2026-11-15). Two limits, neither of which
# makes guessing cheaper inside a window:
#
#   * a record nobody has failed against for a day is forgotten. Measured from
#     the LAST failure, so a slow steady guesser never ages out, and every
#     rule above applies unchanged to anything that happens within the day.
#     A BAN is exempt — "until the app restarts" is the documented promise.
#   * the table holds at most _AUTH_TABLE_MAX addresses. When it is full the
#     stale records go first, then the one that failed longest ago, a banned
#     one only when nothing else is left. Reaching that takes thousands of
#     distinct addresses, and an attacker with those was never slowed by a
#     per-address counter in the first place.
_AUTH_FORGET_SECS = 24 * 3600.0
_AUTH_TABLE_MAX = 4096
# ip -> {fails, strikes, cooldown_until, banned, last}
_auth_state: dict[str, dict] = {}


def _auth_configured() -> bool:
    return bool(ADMIN_KEY) or admin_auth.is_set()


def _auth_stale(st: dict, now: float) -> bool:
    """Whether a lockout record has aged out. Never true of a ban, and never
    of a record with no timestamp — unknown age is not old age."""
    if st.get("banned"):
        return False
    last = st.get("last")
    return last is not None and now - last > _AUTH_FORGET_SECS


def _auth_make_room(now: float) -> None:
    """Keep the table under its ceiling before a NEW address is recorded."""
    if len(_auth_state) < _AUTH_TABLE_MAX:
        return
    for key in [k for k, st in _auth_state.items() if _auth_stale(st, now)]:
        del _auth_state[key]
    while len(_auth_state) >= _AUTH_TABLE_MAX:
        oldest = min(_auth_state, key=lambda k: (
            bool(_auth_state[k].get("banned")),
            _auth_state[k].get("last") or 0.0))
        del _auth_state[oldest]


def _auth_gate(ip: str) -> str | None:
    """A reason this address may not even ATTEMPT a password, or None."""
    st = _auth_state.get(ip)
    if not st:
        return None
    if _auth_stale(st, time.time()):
        _auth_state.pop(ip, None)
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
    now = time.time()
    st = _auth_state.get(ip)
    if st is not None and _auth_stale(st, now):
        st = None                       # aged out: this failure starts afresh
    if st is None:
        _auth_state.pop(ip, None)
        _auth_make_room(now)
        st = _auth_state[ip] = {"fails": 0, "strikes": 0}
    st["last"] = now
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


def _break_glass(key: str) -> bool:
    """Whether this is the ENVIRONMENT break-glass key, and nothing else.

    The one credential that gets past a lockout. admin_auth.py, the comment
    above and docs/security.md all promised "CALLIN_ADMIN_KEY is always
    accepted", and every gate returned the lockout reason before the key was
    even looked at — so the operator who had set a recovery key was refused
    with it from the very address that needed recovering (review, 2026-11-15).

    Only the environment key: a STORED password stays behind the lockout, or
    the lockout would protect nothing. Constant-time, and on bytes — str
    compare_digest raises on a non-ASCII character, which a header can carry.
    Using it does not lift the lockout for anything else: a ban stands until
    the restart it was promised to last for.
    """
    if not (key and ADMIN_KEY):
        return False
    return hmac.compare_digest(key.encode("utf-8", "surrogatepass"),
                               ADMIN_KEY.encode("utf-8", "surrogatepass"))


def _key_valid(key: str) -> bool:
    if not key:
        return False
    if _break_glass(key):
        return True
    # Remembered briefly once verified — see the cache in admin_auth.py. The
    # break-glass key never reaches it.
    return admin_auth.verify(key)


def _check_admin(request: web.Request) -> bool:
    """Password check with lockout, once a password is configured. Stores a
    caller-facing reason on the request for the shared 401 payload."""
    # The lockout bucket must be unspoofable — see _auth_key. _caller_key
    # (walkable X-Forwarded-For) is fine for pacing, not for a throttle.
    ip = _auth_key(request)
    key = request.headers.get("X-Admin-Key", "")
    gate = _auth_gate(ip)
    # The break-glass key is looked at BEFORE the lockout says no — see
    # _break_glass. Everything else stops here, as it always did.
    if gate and not _break_glass(key):
        request["auth_error"] = gate
        request["auth_required"] = True
        return False
    if _key_valid(key):
        if not gate:
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

    # First-run lockdown (0.10.78, operator's ask): until an admin password
    # exists, NO door opens — not calls, not chat, not voicemail — in every
    # front_access mode, including an explicit "open". A line whose panel
    # anyone can claim must not also be a line anyone can ring: the same
    # stranger who could call could first walk into /settings, set the
    # password and own the deployment. This is the one check /token, /chat
    # and /guest-login all pass through, so the refusal cannot drift.
    if not _auth_configured():
        return ("the booth isn't set up yet — calls open once the operator "
                "sets the admin password")

    mode = str(settings_store.load().get("front_access") or "auto").lower()
    if mode == "open":
        return None
    # The historical rule, kept as the default so an upgrade cannot silently
    # close a line that was working. Here the password decides the policy;
    # in the explicit modes below the policy decides, and a missing password
    # is a misconfiguration rather than an invitation.
    if mode == "auto" and not admin_auth.guest_is_set():
        return None

    bucket = "guest:" + ip
    gate = _auth_gate(bucket)
    # "CALLIN_ADMIN_KEY is always accepted" covers this door too: the
    # break-glass key rings the phone from a locked-out address. A guest code
    # or a stored admin password does not (review, 2026-11-15).
    if gate and not _break_glass(key):
        return gate
    if key:
        # Admin is accepted in guest mode so an operator carries one password;
        # in admin mode only the admin password opens the phone.
        ok = _key_valid(key) if mode == "admin" else (
            admin_auth.verify_guest(key) or _key_valid(key))
        if ok:
            if not gate:
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
    # THE DOOR AND THE TIER ARE TWO QUESTIONS. `front_access` answers who may
    # ring at all; the guest code answers who this caller IS once they are in.
    # 0.10.66 fused them — on an open line the code stopped elevating — and the
    # cost only showed up in the permission matrix: two of the three tiers
    # could be live at once and never all three, so an operator running an open
    # line could not give code-holders anything extra, and one running a gated
    # line could not describe a stranger who by then could not exist anyway
    # (operator, 2026-08-15: "it shouldn't be that if i have a guest role i
    # can't have permissions for anyone or vice versa").
    #
    # So: a stored guest code elevates whoever types it, in every mode that
    # lets anyone below admin in at all. Admin-only is the exception and stays
    # one — nobody arrives under admin there, so a guest tier reached by side
    # door would be a tier the door does not admit.
    #
    # The guest pathway is switched off by not having a code, which is the rule
    # this had before 0.10.66 and the one the panel now states. The rule itself
    # lives one function down, in tier_for_key, because the text line has to
    # ask the same question of a key that arrives in a WebSocket frame instead
    # of a header — and a second copy of it is how the two drifted apart.
    for header in ("X-Call-Key", "X-Admin-Key"):
        key = request.headers.get(header, "")
        if not key:
            continue
        tier = tier_for_key(key)
        if tier != "open":
            return tier
    return "open"


def tier_for_key(key: str) -> str:
    """Which caller one typed code makes its holder: open, guest or admin.

    The resolution lives HERE, on its own, because two doors ask it and they
    must not answer differently. The text line had grown its own copy
    (api/chat._tier_for — the WS key arrives in-band, not in a header) that
    handed 'guest' to any valid code with none of the guest_door rule on it,
    so the same code-holder was 'open' on the phone and 'guest' in a text and
    the permission matrix meant two things at once (invariants 7 and 10,
    docs/architecture.md).
    """
    import settings as settings_store

    if not key:
        return "open"
    if _key_valid(key):
        return "admin"
    cfg = settings_store.load()
    # Whether the guest tier is reachable is ONE rule, and it lives in
    # settings_store.guest_door_open — consolidated at Batch 2 from the two
    # spellings (here and the call card) the old comment warned would drift.
    guest_door = settings_store.guest_door_open(
        cfg.get("front_access"), admin_auth.guest_is_set(),
        bool(cfg.get("guest_tier", True)))
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
    # Name the way out. The bare "cross-origin request blocked" cost the
    # operator two separate evenings (the guest code, then the station
    # address) before anyone connected it to the hostname in their address
    # bar — the refusal is deliberate, but a dead end with no exit sign is
    # not (0.10.81).
    request["auth_error"] = (
        "setup from " + origin + " is blocked until a password exists — "
        "before one does, the panel only trusts a literal address (a NAME "
        "can be pointed at this box by someone else; an IP cannot). Open "
        "the panel once at the box's own IP and set the admin password "
        "there, or put this origin in CALLIN_PANEL_ORIGINS."
    )
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
            return refused(request)
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
        # _auth_key, not _caller_key — this is a password attempt, so it is a
        # security control and cannot ride a key the client writes. It rode one
        # until 0.97.25: measured against the deployment, eight wrong passwords
        # with a rotating X-Forwarded-For all came back "4 tries left" while the
        # same eight from one address tripped the cooldown at five. The throttle
        # in front of the admin password was decorative for anyone the peer-trust
        # rule accepts, which by default is the whole LAN.
        ip = _auth_key(request)
        current = str((body or {}).get("current")
                      or request.headers.get("X-Admin-Key", ""))
        gate = _auth_gate(ip)
        # Same order as _check_admin: the break-glass key is the way to set a
        # new password from an address that has locked itself out, which is
        # the recovery it exists for (review, 2026-11-15).
        if gate and not _break_glass(current):
            return _cors(request, web.json_response(
                {"error": gate, "authRequired": True}, status=401))
        if not _key_valid(current):
            err = _auth_fail(ip) if current else "current password required"
            return _cors(request, web.json_response(
                {"error": err, "authRequired": True}, status=401))
        if not gate:
            _auth_clear(ip)
    elif not _write_allowed(request):
        return refused(request)

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
    # code is — including which bucket a failure counts against. _guest_ok
    # hands _guest_check an _auth_key; this handed it a _caller_key, so the
    # same wrong code was throttled at one door and unlimited at the other.
    reason = _guest_check(
        str((body or {}).get("password") or ""), _auth_key(request)
    )
    if reason is None:
        return _cors(request, web.json_response({"ok": True}))
    return _cors(request, web.json_response(
        {"error": reason, "guestRequired": True}, status=401))
