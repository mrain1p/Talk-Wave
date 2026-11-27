"""The station player's listener actions: the heart, the request box, and
the lyrics read.

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


def _queue_moving() -> None:
    """The player just asked the station to change its queue: open /live's
    quick window so the card's burst of polls sees the change land (see
    api/live_cache.expect_queue_change)."""
    import time

    from api.live_cache import expect_queue_change

    expect_queue_change(time.time())


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
        # A request that went out and whose answer did not come back in time
        # has almost certainly been taken (station.py _sent_but_unconfirmed,
        # the rule the DJ's own tools follow). Measured 2026-11-27: the
        # station stalled for tens of seconds while its music server was
        # rescanning a library import, two player requests timed out here,
        # and the listener was told "could not be reached" about requests
        # the station had queued — and would have sent them again.
        if (method == "POST" and path == "/request"
                and isinstance(e, httpx.ReadTimeout)):
            return _cors(request, web.json_response(
                {"success": True, "unconfirmed": True,
                 "message": "Sent — the station is slow to confirm it"},
                status=202))
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
    resp = await _relay(request, "POST", "/like",
                        {"songId": song} if song else {})
    # A landed heart is a station action like any other — the Requests tab
    # is the receipt printer, and a press that leaves no receipt reads as
    # a press that did nothing (operator, 2026-09-01). Title only: a track
    # name is station data, never caller words.
    if resp.status < 400:
        from api.auth import caller_tier
        from call import daylog

        daylog.note("like",
                    str((body or {}).get("title") or "")[:80],
                    tier=caller_tier(request))
    return resp


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
    if ok:
        from call import daylog

        daylog.note("unlike",
                    str((body or {}).get("title") or "")[:80],
                    tier=request.get("player_tier", ""))
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
        # `note`, not `said`: the strip reads one key for the server's own
        # one-line status, and this branch was the only place that answered
        # in the other one — so a command that merely ran long was flashed as
        # the generic sticky refusal, "the booth would not take that"
        # (2026-09-17). The wording below is the receipt's, not the DJ's,
        # which is the same rule the block under this one states.
        return _cors(request, web.json_response(
            {"chat": chat.id, "note": "Still digging — give it another go "
             "in a moment?", "fallback": "timeout", "actions": []}))
    actions = [{"icon": e.get("icon"), "label": e.get("label"),
                "detail": e.get("detail")}
               for e in events if e.get("type") == "action"]
    # MECHANICAL, not conversational (operator, 2026-09-01): the command
    # line answers in receipts, never in the DJ's voice — no questions, no
    # explanations. A command that landed no action is UNRESOLVED whatever
    # the brain said about it, and it degrades to the station's own
    # listener request box, which resolves best-effort the way the
    # request tool always has. The spoken reply is deliberately not
    # returned; it lives in the chat record for diagnostics.
    if not actions:
        # Handed to the station's own listener request box, and reported
        # as a CARD either way (the operator's shape, 2026-09-01): an
        # accepted hand-off is a request receipt like any other and lands
        # in the day-log so the Requests tab keeps it; a refusal carries
        # the station's own sentence, which is written for a listener and
        # better than anything invented here.
        import json as _json

        relay = await _relay(request, "POST", "/request",
                             {"text": text[:200]})
        body_out = {}
        try:
            body_out = _json.loads(relay.body.decode("utf-8"))
        except Exception:                                      # noqa: BLE001
            body_out = {}
        took = relay.status < 400 and body_out.get("success") is not False
        said = str(body_out.get("message") or body_out.get("error") or "")
        if took:
            _queue_moving()
            from call import daylog

            daylog.note("request", text[:80],
                        tier=request.get("player_tier", ""))
            return _cors(request, web.json_response(
                {"chat": chat.id, "fallback": "request",
                 "actions": [{"icon": "🎵",
                              "label": "Handed to the request line",
                              "detail": text[:80]}]}))
        return _cors(request, web.json_response(
            {"chat": chat.id, "fallback": "request", "refused": True,
             "actions": [],
             "note": said[:160] or "the booth would not take that"}))
    _queue_moving()
    return _cors(request, web.json_response(
        {"chat": chat.id, "actions": actions}))


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
    # The Requests tab is the booth's receipt printer, so a request from
    # the player belongs in it — but WITHOUT the words. This box is open
    # to any caller the phone's door lets in, and their sentence is
    # exactly the caller content the 48h log has never carried. The
    # operator sees what they typed in the row's own flash instead.
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
    resp = await _relay(request, "POST", "/request",
                        {"text": text, "name": name})
    if resp.status < 400:
        from api.auth import caller_tier
        from call import daylog

        daylog.note("request", "from the player",
                    tier=caller_tier(request))
        _queue_moving()
    return resp


# How long one lyrics answer is held. The station only ever answers for the
# record on air, and a track.play hook clears it (api/hook_receiver), so this
# is the ceiling on a stale answer when no hook arrives. A lookup the station
# says is still running (`pending`, LRCLIB) is held for less: the widget asks
# again every few seconds while it waits, and each of those must reach it.
_LYRICS_TTL = 20.0
_LYRICS_PENDING_TTL = 3.0


def _lyrics_shape(raw: dict, buffer_secs) -> dict:
    """The station's /lyrics/current, narrowed to what the card draws.

    `lines` keep the station's own `startMs`, which is already measured from
    when the record started PLAYING (it shifts for the dead-air trim). The
    station's per-track correction rides as `offsetMs` — its own player adds
    it to the elapsed time before finding the line. `heardDelayMs` is the
    stream buffer: the card's clock counts from when the station started the
    record, a listener hears it that much later, and the highlighted line has
    to be the one they are hearing.
    """
    lines = []
    for ln in (raw.get("lines") or [])[:400]:
        if not isinstance(ln, dict):
            continue
        text = str(ln.get("text") or "").strip()[:300]
        if not text:
            continue
        start = ln.get("startMs")
        lines.append({"t": int(start) if isinstance(start, (int, float)) else None,
                      "text": text})
    try:
        offset = int(raw.get("offsetMs") or 0)
    except (TypeError, ValueError):
        offset = 0
    try:
        heard = int(float(buffer_secs or 0) * 1000)
    except (TypeError, ValueError):
        heard = 0
    return {
        "songId": raw.get("songId") or None,
        "synced": bool(raw.get("synced")) and any(ln["t"] is not None for ln in lines),
        "offsetMs": max(-10000, min(10000, offset)),
        "heardDelayMs": max(0, min(60000, heard)),
        "pending": bool(raw.get("pending")),
        "lines": lines,
    }


async def handle_player_lyrics(request: web.Request) -> web.Response:
    """GET /player/lyrics — the words of the record on air, for the player's
    Lyrics tab.

    The station has had lyrics on by default since 1.28 (#2082) and shows them
    in its own player; this is the same read for a listener on this card, who
    often cannot reach the station at all (the reason /cover is proxied too).
    The tab offers itself only when there are words, so a station with the
    feature off, an instrumental, or a read that failed all answer the same
    way: `lines: []`, and nothing is drawn. Held for seconds and cleared on a
    track change, so an open tab costs the station one read per record.
    """
    refuse = _door(request)
    if refuse:
        return refuse
    if not settings_store.load().get("player_lyrics", True):
        return _cors(request, web.json_response(
            {"error": "this line does not offer that"}, status=404))
    import time

    from api.live_cache import _lyrics_cache
    from station import StationClient

    now = time.time()
    held = _lyrics_cache.get("data")
    if held is not None:
        ttl = _LYRICS_PENDING_TTL if held.get("pending") else _LYRICS_TTL
        if now - _lyrics_cache["at"] < ttl:
            return _cors(request, web.json_response(held))
    st = StationClient()
    try:
        raw = await st._lyrics_read()
        try:
            np = await st.now_playing()
            buffer_secs = (np.get("stream") or {}).get("bufferSeconds")
        except Exception:                                     # noqa: BLE001
            buffer_secs = 0
    except Exception as e:                                    # noqa: BLE001
        # Not an error the listener can do anything about: no tab, quietly.
        # But NOT "this record has no words" either: measured 2026-11-27,
        # the station's own lyrics read waits on its music server, which
        # was taking 8-49s while a library import kept it rescanning. Marked
        # `pending`, so it is held for seconds and the widget asks again,
        # rather than cached as empty and never asked again for the record.
        log.info("player lyrics unavailable (%s)", describe(e))
        raw, buffer_secs = {"pending": True}, 0
    finally:
        await st.aclose()
    data = _lyrics_shape(raw if isinstance(raw, dict) else {}, buffer_secs)
    _lyrics_cache["data"] = data
    _lyrics_cache["at"] = now
    return _cors(request, web.json_response(data))
