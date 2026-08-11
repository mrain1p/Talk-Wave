"""The HTTP edge: what we send back, and who we believe sent it.

Both answers are policy rather than plumbing. Which origins may talk to this
service decides who can spend the operator's API budget, and which address a
request is attributed to decides whose cooldown and whose lockout counter it
lands on — so neither may be a value the caller chooses.
"""

from __future__ import annotations

import logging
import os

from aiohttp import web

log = logging.getLogger("callin.token")

# Which origins may embed / call the token endpoint. "*" is fine for local dev;
# empty is same-origin only. The widget on this service's own page needs no
# entry here; a value is only needed to embed it on another site.
#
# It used to default to "*" — any page on the internet may mint a call token
# against this service and spend the operator's LLM and TTS budget. That was
# kept through 0.9.76 to avoid silently breaking embeds on deployments that
# never set the variable, and then deliberately changed: pre-1.0, with few
# deployments, is exactly when to take that break rather than ship the
# convenient default into 1.0 and be stuck with it.
#
# A panel setting since 0.10.63 (The call card → Embed on another page), with
# CALLIN_ALLOWED_ORIGINS as its env baseline. Read live on every check, the
# same re-read rule the rest of settings.py follows — the point of the move is
# that allowing the site you just built a snippet for is a save, not a
# container recreate, so caching this at import would undo the feature.
def allowed_origins() -> list[str]:
    import settings as settings_store

    raw = settings_store.load().get("allowed_origins") or ""
    return [o.strip() for o in str(raw).split(",") if o.strip()]

# Origins that may reach the PANEL during first-run — before any password
# exists. Deliberately NOT the same list as above.
#
# CALLIN_ALLOWED_ORIGINS says "this page may embed the widget and mint call
# tokens". That is a caller-facing permission: what it costs you is API budget.
# Reading the settings, seeing which keys are set and choosing the admin
# password is a different thing entirely, and a site you were happy to let
# embed a Call button is not automatically one you would hand the controls to.
#
# Same lesson as 0.9.61, where who may call the booth stopped being inferred
# from whether a guest code happened to exist: a permission that moves as a
# side effect of another setting is one nobody can reason about. So this is its
# own list, and it is empty by default — the literal-address rule in
# _write_allowed covers the ordinary first run (a LAN IP, or localhost), and
# this exists only for an operator who reaches the panel by hostname and has
# not set a password yet. Setting a password makes the whole path moot.
PANEL_ORIGINS = [
    o.strip() for o in os.environ.get("CALLIN_PANEL_ORIGINS", "").split(",") if o.strip()
]


def origin_allowed(request: web.Request) -> bool:
    """Whether this request's Origin may drive a budget-spending surface.

    Same policy as _cors, but usable where CORS can't reach: a WebSocket
    handshake is NOT subject to CORS, so the chat line — which spends LLM
    money per turn — was reachable cross-origin even though /token refuses
    the same foreign page (0.10.57 review). A same-origin request carries no
    Origin header (or one equal to the host), so empty stays same-origin-only.
    """
    origin = request.headers.get("Origin", "")
    allowed = allowed_origins()
    if not origin or "*" in allowed or origin in allowed:
        return True
    # Same-origin: the Origin's host:port matches the request's own.
    host = request.headers.get("Host", "")
    return origin.split("://", 1)[-1] == host


def _cors(request: web.Request, resp: web.StreamResponse) -> web.StreamResponse:
    origin = request.headers.get("Origin", "")
    allowed = allowed_origins()
    if "*" in allowed:
        resp.headers["Access-Control-Allow-Origin"] = origin or "*"
    elif origin in allowed:
        resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return resp


async def handle_options(request: web.Request) -> web.Response:
    return _cors(request, web.Response(status=204))


# Which immediate peers are allowed to speak for someone else — that is, whose
# X-Forwarded-For we believe. Comma-separated addresses or CIDRs.
#
# The default is "any loopback or private address", which is exactly the
# bundled deployment: caddy talks to this container over the docker bridge. A
# request arriving straight off the internet is not on that list, so it cannot
# hand us an address of its choosing.
_TRUSTED_PROXIES_RAW = os.environ.get("CALLIN_TRUSTED_PROXIES", "").strip()


def _peer_is_a_trusted_proxy(peer: str | None) -> bool:
    import ipaddress

    if not peer:
        return False
    try:
        addr = ipaddress.ip_address(peer.strip("[]"))
    except ValueError:
        return False
    if not _TRUSTED_PROXIES_RAW:
        return addr.is_loopback or addr.is_private
    for entry in _TRUSTED_PROXIES_RAW.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            if addr in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def _caller_key(request: web.Request) -> str:
    """Who is calling, as well as we can know it.

    This decides three things that all matter: the per-caller cooldown, which
    bucket a failed password counts against, and which address gets locked
    out. So it must not be a value the caller chooses.

    X-Forwarded-For is a list the CLIENT starts and each proxy appends to, so
    the leftmost entry is whatever the client felt like claiming. Reading it
    let anyone rotate the header and sit at "4 tries left" forever, and let
    anyone drop someone else's address into cooldown by failing on their
    behalf. Two things fix it:

      * only believe the header at all when the connection came from a proxy
        we trust (see _peer_is_a_trusted_proxy) — otherwise the socket's own
        address is the only honest answer;
      * take the RIGHTMOST entry, which is the one that proxy appended and
        therefore actually observed, rather than the leftmost, which is the
        one the client wrote.
    """
    if _peer_is_a_trusted_proxy(request.remote):
        hops = [h.strip() for h in
                request.headers.get("X-Forwarded-For", "").split(",") if h.strip()]
        # Walk back through the trusted ones. Taking the rightmost entry flat
        # is right for a single proxy and wrong the moment there are two: with
        # a CDN in front of the reverse proxy, the entry the proxy appended is
        # the CDN's address, so every caller in the world collapses into one
        # cooldown bucket and one lockout counter. Skipping hops we already
        # trust lands on the first address none of them vouched for, which is
        # the caller. Fails safe either way — if every hop is trusted there is
        # nobody left to blame but the socket.
        for hop in reversed(hops):
            if not _peer_is_a_trusted_proxy(hop):
                return hop
        if hops:
            return hops[0]
    return request.remote or "unknown"


def _auth_key(request: web.Request) -> str:
    """The bucket a wrong password or guest code counts against, and the one a
    lockout applies to. Deliberately NOT _caller_key.

    _caller_key trusts X-Forwarded-For from any private/loopback peer by
    default (the bundled proxy reaches this container over the docker bridge).
    That is fine for the redial cooldown, a pacing knob — but it means a client
    on the LAN, connecting straight to :8100, is treated as a trusted proxy and
    can set its own X-Forwarded-For: rotate the header to sit at "tries left"
    forever, defeating the throttle in front of the 6-char guest code and the
    admin password, or drop a victim's address (the operator's own) into
    cooldown (0.10.58 review). A security control cannot ride a spoofable key.

    So the forwarded caller is believed here ONLY when an explicit
    CALLIN_TRUSTED_PROXIES list vouches for the immediate peer; otherwise the
    socket's own address — which the client cannot choose — is the answer.
    Behind the bundled reverse proxy, set CALLIN_TRUSTED_PROXIES to it to
    restore per-caller precision without reopening the spoof.
    """
    if _TRUSTED_PROXIES_RAW and _peer_is_a_trusted_proxy(request.remote):
        return _caller_key(request)
    return (request.remote or "unknown")
