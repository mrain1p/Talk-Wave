"""The station player's listener actions: the heart and the request box.

The player is a LISTENER surface, and the station already answers listeners
without credentials — POST /like and POST /request are public on the station
by design, rate-limited per IP, refusing in plain words ("Requests are
temporarily closed."). These proxies exist because the caller's browser often
cannot reach the station at all — LAN deployments, and mixed content behind
TLS — the same reason /avatar and /cover are proxied. They add no station
capability its own listener page does not already hand out; station.py stays
read-only and the MCP allowlist still owns everything the DJ does on a call.

The caller's address rides X-Forwarded-For so a station that trusts its proxy
chain keeps per-listener like identity and rate limits. A station that
ignores the header sees this sidecar as one listener, which only ever
TIGHTENS its limits — never loosens them.
"""

from __future__ import annotations

import logging

import httpx
from aiohttp import web

import settings as settings_store
from api.auth import _guest_ok
from api.wire import _cors
from log_setup import describe

log = logging.getLogger("callin.token")


def _door(request: web.Request) -> web.Response | None:
    """The player's own door: the feature must be ON, and the caller must be
    through the same gate as the phone — an open line answers anyone, a
    code-gated line wants the code the widget already sends."""
    if not settings_store.load().get("swipe_player"):
        return _cors(request, web.json_response(
            {"error": "the player is not enabled on this line"}, status=404))
    if not _guest_ok(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "a caller code is needed"},
            status=401))
    return None


async def _relay(request: web.Request, method: str, path: str,
                 payload: dict | None = None) -> web.Response:
    """Pass the station's own answer through, status and body alike — its
    refusals are written for listeners and are better UX than anything this
    sidecar could invent about a station it cannot see into."""
    root = settings_store.station_base_url()
    fwd = request.headers.get("X-Forwarded-For") or (request.remote or "")
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.request(
                method, f"{root}{path}", json=payload,
                headers={"X-Forwarded-For": fwd} if fwd else {})
        try:
            body = r.json()
        except Exception:
            body = {"error": "the station gave no usable answer"}
        return _cors(request, web.json_response(body, status=r.status_code))
    except Exception as e:
        log.info("player relay %s %s failed: %s", method, path, describe(e))
        return _cors(request, web.json_response(
            {"error": "the station could not be reached"}, status=502))


async def handle_player_like_status(request: web.Request) -> web.Response:
    refuse = _door(request)
    if refuse:
        return refuse
    return await _relay(request, "GET", "/like")


async def handle_player_like(request: web.Request) -> web.Response:
    refuse = _door(request)
    if refuse:
        return refuse
    try:
        body = await request.json()
    except Exception:
        body = {}
    # songId is the station's stale-tap guard: the client says which record
    # it thinks it is liking, and a track change between paint and press is
    # answered with 409 rather than the wrong song getting the heart.
    song = body.get("songId") if isinstance(body, dict) else None
    return await _relay(request, "POST", "/like",
                        {"songId": song} if song else {})


async def handle_player_request(request: web.Request) -> web.Response:
    refuse = _door(request)
    if refuse:
        return refuse
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = str((body or {}).get("text") or "").strip()[:200]
    name = str((body or {}).get("name") or "").strip()[:60]
    if not text:
        return _cors(request, web.json_response(
            {"success": False, "message": "say what you'd like to hear"},
            status=400))
    return await _relay(request, "POST", "/request",
                        {"text": text, "name": name})
