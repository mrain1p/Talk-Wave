"""The operator's two buttons and one status read for Open Lines.

Admin-gated, all three: opening a line writes to the broadcast, and closing
one puts a sign-off on air. Neither is something a guest code should reach.
"""

from __future__ import annotations

import logging

from aiohttp import web

import settings as settings_store
from api.auth import _write_allowed
from api.wire import _cors
from openlines import director, premises, state
from station import StationClient

log = logging.getLogger("callin.openlines")


def _refuse(request: web.Request) -> web.Response:
    return _cors(request, web.json_response(
        {"error": request.get("auth_error") or "not allowed",
         "authRequired": bool(request.get("auth_required"))}, status=401))


def _cfg() -> dict:
    return settings_store.permissions_for(settings_store.load(), "admin")


def status_payload() -> dict:
    """What the panel's card renders.

    `live` is the question the card actually asks, and it is not the same as
    "a record exists": an expired line, or one belonging to a DJ who has since
    gone off air, is on disk but is not open. The card must show what a caller
    would meet, not what was last written.
    """
    cfg = _cfg()
    record = state.read_raw()
    live = state.is_live(record)
    payload = {
        "enabled": bool(cfg.get("open_lines_enabled")),
        "live": live,
        "premise": str(record.get("premise") or ""),
        "spoken": str(record.get("spoken") or ""),
        "persona": str(record.get("persona_name") or ""),
        "openedAt": record.get("opened_at"),
        "expiresAt": record.get("expires_at"),
        "secondsLeft": int(state.seconds_left(record)) if live else 0,
        "remindersSent": int(record.get("reminders_sent") or 0),
        "reminderMax": int(record.get("reminder_max") or 0),
        "source": str(record.get("source") or ""),
        "openedBy": str(record.get("opened_by") or ""),
        "closedReason": str(record.get("closed_reason") or ""),
        "signOff": str(record.get("sign_off_spoken") or ""),
        "cutByShow": bool(record.get("cut_by_show")),
        # So the dashboard can grey "off the shelf" rather than offering a
        # press that can only ever answer "nothing on the shelf".
        "shelfCount": len(premises.read()),
    }
    return payload


async def handle_open_lines_status(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return _refuse(request)
    return _cors(request, web.json_response(status_payload()))


async def handle_open_lines_premises(request: web.Request) -> web.Response:
    """The shelf, plus the roster to aim entries at.

    The roster rides along so the panel can draw the per-premise DJ picker
    from one read — it is the same list the persona allowlist ticks, and two
    fetches for one screen is two ways for it to disagree with itself.
    """
    if not _write_allowed(request):
        return _refuse(request)

    import secrets_store

    secrets_store.apply_to_env()
    station = StationClient()
    try:
        roster = await station.personas()
    except Exception as e:                                     # noqa: BLE001
        roster = []
        log.info("premise shelf could not read the roster: %s", e)
    finally:
        await station.aclose()
    return _cors(request, web.json_response({
        "items": premises.read(),
        "personas": [{"id": str(p.get("id") or ""),
                      "name": str(p.get("name") or "")}
                     for p in roster if p.get("id")],
    }))


async def handle_open_lines_premise_add(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return _refuse(request)
    body = await request.json() if request.can_read_body else {}
    item = premises.add(str(body.get("text") or ""),
                        list(body.get("personas") or []))
    if not item:
        return _cors(request, web.json_response(
            {"ok": False, "why": "A subject needs some words."}))
    return _cors(request, web.json_response({"ok": True, "item": item,
                                             "items": premises.read()}))


async def handle_open_lines_premise_edit(request: web.Request) -> web.Response:
    """Edit or delete one entry. DELETE removes; POST updates text and/or aim."""
    if not _write_allowed(request):
        return _refuse(request)
    pid = request.match_info.get("premise_id", "")
    if request.method == "DELETE":
        return _cors(request, web.json_response(
            {"ok": premises.remove(pid), "items": premises.read()}))
    body = await request.json() if request.can_read_body else {}
    item = premises.update(
        pid,
        text=body.get("text") if "text" in body else None,
        personas=list(body["personas"]) if "personas" in body else None)
    return _cors(request, web.json_response(
        {"ok": bool(item), "item": item, "items": premises.read()}))


async def handle_open_lines_open(request: web.Request) -> web.Response:
    """Put a subject up now, for one full duration.

    Refusals come back with `why` and a 200, not an error status: every one of
    them is a setting the operator can change (switched off, wrong DJ, nobody
    listening, empty list), and a red failure box for "nobody is listening" is
    a bug report waiting to be filed against a working feature.
    """
    if not _write_allowed(request):
        return _refuse(request)
    body = await request.json() if request.can_read_body else {}
    # "dj" or "shelf", for THIS press only. Absent = whatever the settings
    # page says, which is what the section's own button sends.
    source = str(body.get("source") or "").strip() or None
    result = await director.open_now(reason="operator", source=source)
    return _cors(request, web.json_response(
        {**result, "status": status_payload()}))


async def handle_open_lines_close(request: web.Request) -> web.Response:
    """Close the line by hand. The sign-off airs on the director's next tick,
    so the operator's press returns immediately rather than waiting on the
    station's TTS — and a stack restarted in between still airs it exactly
    once, because `signed_off` is the latch, not this request."""
    if not _write_allowed(request):
        return _refuse(request)
    closed = state.close(reason="operator")
    return _cors(request, web.json_response(
        {"ok": bool(closed), "status": status_payload()}))
