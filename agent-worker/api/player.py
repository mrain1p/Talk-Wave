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
from api.wire import _caller_key, _cors
from log_setup import describe

log = logging.getLogger("callin.token")


def _door(request: web.Request,
          switches: tuple[str, ...] = ("swipe_player",)) -> web.Response | None:
    """The listener surfaces' door: at least one offering feature must be ON,
    and the caller must be through the same gate as the phone — an open line
    answers anyone, a code-gated line wants the code the widget already sends.

    `switches` names which settings offer the endpoint. The request box is
    the player's own; the heart is ALSO on the call card (show_track_like),
    so the like endpoints stay open for a card whose operator never switched
    the player on."""
    cfg = settings_store.load()
    if not any(cfg.get(s) for s in switches):
        return _cors(request, web.json_response(
            {"error": "this line does not offer that"}, status=404))
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
    # The address WE honestly observed, not the header the caller sent. The
    # station applies a per-IP throttle to these listener actions; forwarding
    # a raw X-Forwarded-For let a caller spoof the IP it counts against, on a
    # station that trusts its proxy chain — the deployment this feature is
    # for. _caller_key walks the chain rightmost-untrusted and only believes
    # a forwarded address from a proxy CALLIN_TRUSTED_PROXIES names, the same
    # value the sidecar's own cooldown trusts (security sitting, 2026-08-28).
    fwd = _caller_key(request)
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
    refuse = _door(request, ("swipe_player", "show_track_like"))
    if refuse:
        return refuse
    return await _relay(request, "GET", "/like")


async def handle_player_like(request: web.Request) -> web.Response:
    refuse = _door(request, ("swipe_player", "show_track_like"))
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


def _abilities(cfg: dict, tier: str) -> dict:
    """What one caller's key unlocks on the player — pure, so the tier
    truth-table is unit-testable without a web request.

    Two gates multiply on purpose: the phone-page SWITCH says whether the
    furniture is on the sheet at all (the operator's view decision), and the
    permission matrix says which TIER may use it (the access decision) —
    the operator's own framing, 2026-09-01."""
    reaches = settings_store.tier_reaches
    return {
        "tier": tier,
        "skip": bool(cfg.get("player_skip_button"))
        and reaches(cfg.get("allow_skip_track"), tier),
        "unlike": bool(cfg.get("show_track_like", True))
        and reaches(cfg.get("allow_unfavorite"), tier),
        "command": bool(cfg.get("player_operator_mode"))
        and reaches(cfg.get("allow_player_commands"), tier),
    }


async def handle_player_abilities(request: web.Request) -> web.Response:
    """GET /player/abilities — the server's answer to which operator-side
    controls THIS caller's key earns. The widget paints from this rather
    than guessing from the tier, because only the server has seen the
    password (the segment button's own rule)."""
    refuse = _door(request)
    if refuse:
        return refuse
    from api.auth import caller_tier

    cfg = settings_store.load()
    return _cors(request, web.json_response(
        _abilities(cfg, caller_tier(request))))


def _tier_refusal(request: web.Request, cfg: dict,
                  ability: str) -> web.Response | None:
    """403 in honest words when the key does not clear the permission —
    after _door, so the caller is already through the phone's own gate."""
    from api.auth import caller_tier

    tier = caller_tier(request)
    if _abilities(cfg, tier).get(ability):
        request["player_tier"] = tier
        return None
    return _cors(request, web.json_response(
        {"error": "your access level does not include that"}, status=403))


async def handle_player_skip(request: web.Request) -> web.Response:
    """POST /player/skip — the operator's skip, from the sheet.

    Station-wide by nature (its own API calls skip an operator override
    and offers no listener-facing equivalent), which is why this rides the
    ADMIN client behind the permission matrix instead of the public
    listener surface every other /player write uses."""
    refuse = _door(request)
    if refuse:
        return refuse
    cfg = settings_store.load()
    refuse = _tier_refusal(request, cfg, "skip")
    if refuse:
        return refuse
    from call import daylog
    from station import StationClient

    station = StationClient()
    try:
        res = await station.skip_track()
    finally:
        try:
            await station.aclose()
        except Exception:                                      # noqa: BLE001
            pass
    if res.get("ok"):
        daylog.note("skip", "from the player",
                    tier=request.get("player_tier", ""))
        return _cors(request, web.json_response(res))
    return _cors(request, web.json_response(res, status=502))


async def handle_player_unlike(request: web.Request) -> web.Response:
    """POST /player/unlike {songId} — take the operator's heart back off a
    record. The station keeps that as an admin write (DELETE
    /likes/song/:id/operator), so like the skip it rides the admin client
    behind the matrix — the public heart stays exactly as it was."""
    refuse = _door(request, ("swipe_player", "show_track_like"))
    if refuse:
        return refuse
    cfg = settings_store.load()
    refuse = _tier_refusal(request, cfg, "unlike")
    if refuse:
        return refuse
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001
        body = {}
    song = str((body or {}).get("songId") or "")
    if not song:
        # The same stale-tap honesty as the like: without the id this could
        # un-heart whatever record slid under the finger.
        return _cors(request, web.json_response(
            {"error": "songId is needed — which record?"}, status=400))
    from station import StationClient

    station = StationClient()
    try:
        res = await station.unlike_track(song)
    finally:
        try:
            await station.aclose()
        except Exception:                                      # noqa: BLE001
            pass
    ok = not (isinstance(res, dict) and res.get("error"))
    return _cors(request, web.json_response(
        res if isinstance(res, dict) else {"ok": ok},
        status=200 if ok else 502))


async def handle_player_command(request: web.Request) -> web.Response:
    """POST /player/command {text, chat?} — the request line's operator
    mode: one typed instruction through the SAME brain and tool surface as
    the text line, no back-and-forth (operator's ask, 2026-09-01). The
    turn runs on a real ChatSession from the shared shelf — resumed by the
    id the widget hands back, swept by the chat clocks, written to the
    records like any text exchange — so operator commands cost nothing new
    in machinery and show up in diagnostics like everything else."""
    import asyncio

    refuse = _door(request)
    if refuse:
        return refuse
    cfg = settings_store.load()
    refuse = _tier_refusal(request, cfg, "command")
    if refuse:
        return refuse
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001
        body = {}
    text = str((body or {}).get("text") or "").strip()
    if not text:
        return _cors(request, web.json_response(
            {"error": "say what you want done"}, status=400))
    from chat.session import SHELF

    SHELF.sweep(cfg)
    chat = SHELF.get_or_open(str((body or {}).get("chat") or ""),
                             request.get("player_tier", "admin"), cfg)
    if chat is None:
        return _cors(request, web.json_response(
            {"error": "the booth's lines are all tied up — a moment"},
            status=429))
    events: list[dict] = []
    try:
        timeout = float(cfg.get("chat_reply_timeout_secs") or 0) or 90.0
        await asyncio.wait_for(chat.ask(text, events.append), timeout)
    except asyncio.TimeoutError:
        return _cors(request, web.json_response(
            {"chat": chat.id, "said": "Still digging — give it another go "
             "in a moment?", "actions": []}))
    actions = [{"icon": e.get("icon"), "label": e.get("label"),
                "detail": e.get("detail")}
               for e in events if e.get("type") == "action"]
    said = next((str(e.get("text") or "") for e in reversed(events)
                 if e.get("type") == "done"), "")
    return _cors(request, web.json_response(
        {"chat": chat.id, "said": said, "actions": actions}))


async def handle_player_booth_log(request: web.Request) -> web.Response:
    """GET /player/booth-log — the Booth tab's feed: the station day-log,
    the same 48h no-caller-content record the DJ's own booth_log tool
    reads, tier-attributed. One source of truth; operator commands land in
    it through the ledger like every other action."""
    refuse = _door(request)
    if refuse:
        return refuse
    cfg = settings_store.load()
    refuse = _tier_refusal(request, cfg, "command")
    if refuse:
        return refuse
    from call import daylog
    from call.actions import CallActions

    # The tab is the booth's receipt printer, so each entry wears the same
    # icon and label the caller's action cards do — mapped on read from the
    # one table, never stored (the log would drift the moment a label
    # changed).
    entries = []
    for e in daylog.recent(30):
        icon, label = CallActions.LABELS.get(
            str(e.get("kind") or ""), ("✅", str(e.get("kind") or "")))
        entries.append({**e, "icon": icon, "label": label})
    return _cors(request, web.json_response({"entries": entries}))


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
