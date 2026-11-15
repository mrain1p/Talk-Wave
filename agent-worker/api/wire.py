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
from aiohttp.web_log import AccessLogger

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


# WHAT A PAGE ON ANOTHER ORIGIN CAN ACTUALLY DO, said plainly because the
# allowlist above reads as if it granted more (review, 2026-11-15).
#
# The two values below are the whole grant: a preflighted request may carry
# `Content-Type` and be a POST. Neither X-Call-Key nor X-Admin-Key is on the
# list, so a browser will not let a cross-origin page send a guest code or an
# admin password to this service AT ALL — no keyed call, no panel request, no
# GET with a custom header. What an allowed origin's own script gets is:
#
#   * a "simple" GET with no custom headers and a readable answer — in
#     practice `GET /live`, which is what embed.js asks to name who is on air
#     before the frame is opened;
#   * an unkeyed JSON POST, i.e. /token on a line that is open to anyone.
#
# Everything else an embed does — typing the code, signing in, the call
# itself — happens INSIDE the iframe, which is this service's own origin and
# never touches CORS. That is the supported shape: an embed is a frame onto
# `/` plus one simple GET, not a cross-origin API client. Adding a key header
# here would turn every allowed origin into somewhere the admin password may
# be typed, so the values are pinned (TestCrossOriginUseIsAFrameAndOneGet):
# changing them is a decision, not a tidy-up.
CORS_ALLOW_HEADERS = "Content-Type"
CORS_ALLOW_METHODS = "POST, OPTIONS"


def _cors(request: web.Request, resp: web.StreamResponse) -> web.StreamResponse:
    origin = request.headers.get("Origin", "")
    allowed = allowed_origins()
    if "*" in allowed:
        resp.headers["Access-Control-Allow-Origin"] = origin or "*"
    elif origin in allowed:
        resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Access-Control-Allow-Headers"] = CORS_ALLOW_HEADERS
    resp.headers["Access-Control-Allow-Methods"] = CORS_ALLOW_METHODS
    return resp


def refused(request: web.Request, message: str = "not allowed") -> web.Response:
    """The admin refusal card, in one place.

    It was written out at thirty-six call sites plus four private helpers
    (`_refuse` three times and `_unauthorised` once, all byte-identical), and
    the copies had already come apart: five of them dropped `authRequired`,
    including one branch of the change-password handler whose OTHER branch
    sets it. Nobody could see the difference, which is the second half of the
    finding — `authRequired` is emitted by every one of these and read by
    nothing: not the widget, not the panel, not a test. It is kept because
    removing a field from a response is the operator's call, not a cleanup's,
    and because after this there is one line to delete instead of thirty-six.

    `auth_error` and `auth_required` are put on the request by the gates in
    api/auth.py, which are the only things that know WHY the door said no.
    """
    return _cors(request, web.json_response(
        {"error": request.get("auth_error") or message,
         "authRequired": bool(request.get("auth_required"))},
        status=401,
    ))


async def handle_options(request: web.Request) -> web.Response:
    return _cors(request, web.Response(status=204))


def is_local_address(peer: str | None) -> bool | None:
    """Loopback or private: True. A public address: False. Not an address at
    all: None.

    The one spelling of "is this peer on our side of the router". The mint
    log's same-network label (api/tokens._network_of) had its own, by string
    prefix, and the prefixes missed things `ipaddress` knows — an IPv6 unique
    local address outside `fd…`, IPv4 link-local — so the two could describe
    one caller two ways (review, 2026-11-15).

    100.64.0.0/10 (carrier-grade NAT, and what Tailscale hands out) is
    deliberately NOT local here: `ipaddress` does not call it private, and
    this answer feeds a trust decision below, where "shared by strangers" is
    the honest reading of that range.
    """
    import ipaddress

    if not peer:
        return None
    try:
        addr = ipaddress.ip_address(peer.strip().strip("[]"))
    except ValueError:
        return None
    return bool(addr.is_loopback or addr.is_private)


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
        return bool(is_local_address(peer))
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
        # Every hop trusted falls through to the socket, which is what the
        # paragraph above promises. It used to return hops[0] instead — the
        # leftmost entry, the one the CLIENT wrote — so anyone who filled the
        # header with trusted-looking addresses got back exactly the free
        # rotation the rightmost rule exists to deny them.
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
    _hint_an_unnamed_proxy(request)
    return (request.remote or "unknown")


# Said once per process, then never again: this fires on a request, and a line
# a stranger can make us print on demand is a way to fill the log.
_proxy_hint_given = False


def _hint_an_unnamed_proxy(request: web.Request) -> None:
    """Warn that the lockout is keyed on a proxy's address, the first time it
    visibly is.

    The default trust is right and stays as it is — believing a forwarded
    address from any private peer would let a LAN client pick its own lockout
    bucket. But the cost of the safe default was silent: behind the bundled
    Caddy with CALLIN_TRUSTED_PROXIES blank, every caller is the proxy as far
    as the lockout can tell, so one person's wrong codes lock out everybody,
    the operator included, and nothing anywhere said why (review,
    2026-11-15). A private or loopback peer that SENDS X-Forwarded-For is
    what a reverse proxy looks like, so that is the moment to say it — naming
    the peer, because that address is the value to set.

    A list that is set but does not cover the peer gets the same line: a
    container that came back on a new address leaves yesterday's value naming
    nobody, which is the blank case again with a setting that looks done.
    """
    global _proxy_hint_given
    if _proxy_hint_given:
        return
    if not request.headers.get("X-Forwarded-For"):
        return
    peer = request.remote
    if not is_local_address(peer):
        return
    _proxy_hint_given = True
    log.warning(
        "a request arrived from %s carrying X-Forwarded-For, and "
        "CALLIN_TRUSTED_PROXIES %s. If %s is your reverse proxy (the bundled "
        "Caddy is one), every caller behind it shares ONE lockout counter: a "
        "few wrong codes from anyone lock out everyone, the panel included. "
        "Set CALLIN_TRUSTED_PROXIES=%s on the web service and recreate it. "
        "If nothing of yours forwards requests from that address, ignore "
        "this — the header is not believed.",
        peer,
        (f"is {_TRUSTED_PROXIES_RAW!r}, which does not cover it"
         if _TRUSTED_PROXIES_RAW else "is not set"),
        peer, peer)


class _SeenThroughTheProxy:
    """A request whose `remote` is the caller rather than the proxy, for the
    access log's `%a` and nothing else."""

    __slots__ = ("_request", "remote")

    def __init__(self, request, remote: str) -> None:
        self._request = request
        self.remote = remote

    def __getattr__(self, name: str):
        return getattr(self._request, name)


class ClientAccessLogger(AccessLogger):
    """The access log, naming the caller instead of the proxy in front of them.

    aiohttp's `%a` is the socket peer, so behind a reverse proxy every line
    began with the proxy's own address and the log could not say who a
    request came from — while the lockout warnings beside it ("auth: 203.0…
    banned") named somebody else entirely. This shows the address the
    lockout keys on (_auth_key): the forwarded caller when
    CALLIN_TRUSTED_PROXIES vouches for the peer, the socket otherwise. A
    header from an unnamed peer is not believed here either, or a client
    could write itself into the log as anyone (review, 2026-11-15).
    """

    def log(self, request, response, time: float) -> None:
        try:
            client = _auth_key(request)
            if client and client != request.remote:
                request = _SeenThroughTheProxy(request, client)
        except Exception:                                     # noqa: BLE001
            pass                    # an access line is never worth a 500
        super().log(request, response, time)
