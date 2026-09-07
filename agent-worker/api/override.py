"""The dashboard's station-override card: what stands, and the one clear.

A takeover or a genre lock outlives the call that set it — a caller with the
permission can point the station somewhere and hang up. Until this card,
neither panel showed one standing: this dashboard had only the permission
row, and the station's own quick control is still an open PR upstream. The
read is the station's public /schedule (the override plus the shows list to
name it); the clear is the same admin DELETE the DJ's cancel tool uses, so
the operator can lift what a caller set without hunting the transcript.
"""

from __future__ import annotations

import logging

from aiohttp import web

from api.auth import _write_allowed
from api.wire import _cors
from station import StationClient

log = logging.getLogger("callin.override")

# The reserved show id the genre lock pins — the same mirror
# call/tools/broadcast.GENRE_LOCK_SHOW_ID keeps, for the same reason: the
# card names the two differently, because "pinned to The Graveyard Shift"
# and "locked to a genre" are different sentences to an operator.
GENRE_LOCK_SHOW_ID = "genre_lock"


def _refuse(request: web.Request) -> web.Response:
    return _cors(request, web.json_response(
        {"error": request.get("auth_error") or "not allowed",
         "authRequired": bool(request.get("auth_required"))}, status=401))


async def override_payload() -> dict:
    """The pin currently in force, named, or {"active": False}.

    /schedule reports an expired or dangling override as null already, so
    active here means the station will actually honour it. The show name is
    resolved from the same payload's shows list — one read, no second fetch
    to disagree with the first.
    """
    station = StationClient()
    try:
        schedule = await station.schedule()
    finally:
        try:
            await station.aclose()
        except Exception:                                      # noqa: BLE001
            pass
    ov = schedule.get("override") if isinstance(schedule, dict) else None
    if not isinstance(ov, dict):
        return {"active": False}
    # `showId: null` INSIDE an override object is Default programming — the
    # operator pinning the station's own mix over the grid (upstream #1543,
    # schemas/schedule.ts:229: "showId: null means Default programming; an
    # outer scheduleOverride: null means there is no takeover at all"). Read
    # as falsy it came back as "nothing is pinned", which is the one state it
    # is not (upstream pass, 2026-09-07).
    if "showId" not in ov:
        return {"active": False}
    if ov.get("showId") is None:
        return {
            "active": True,
            "kind": "default-programming",
            "showId": None,
            "show": "Default programming",
            "startedAt": ov.get("startedAt"),
            "expiresAt": ov.get("expiresAt"),
        }
    show_id = str(ov.get("showId") or "")
    if not show_id:
        return {"active": False}
    name = ""
    for s in (schedule.get("shows") or []):
        if isinstance(s, dict) and str(s.get("id") or "") == show_id:
            name = str(s.get("name") or "")
            break
    return {
        "active": True,
        "kind": "genre-lock" if show_id == GENRE_LOCK_SHOW_ID else "takeover",
        "showId": show_id,
        "show": name,
        "startedAt": ov.get("startedAt"),
        "expiresAt": ov.get("expiresAt"),
    }


async def handle_override_status(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return _refuse(request)
    try:
        payload = await override_payload()
    except Exception as e:                                     # noqa: BLE001
        # A station that will not answer is not "no override" — the card
        # hides on active:False, and hiding a pin that may still stand
        # because one read timed out would be the dashboard lying.
        log.info("override read failed: %s", e)
        payload = {"active": False, "unreachable": True}
    return _cors(request, web.json_response(payload))


async def handle_override_clear(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return _refuse(request)
    import secrets_store

    secrets_store.apply_to_env()
    station = StationClient()
    try:
        res = await station.clear_pinned_show()
    finally:
        try:
            await station.aclose()
        except Exception:                                      # noqa: BLE001
            pass
    return _cors(request, web.json_response(
        res if isinstance(res, dict) else {"ok": False}))
