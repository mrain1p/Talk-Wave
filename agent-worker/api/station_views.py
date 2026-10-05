"""Two operator reads the station already serves, on the panel's Diagnostics
page (approved in the 2026-09-28 upstream pass): who is connected right now
(/listeners/connections) and which library tracks the station has not tagged
yet (/library/untagged).

Admin-gated here as they are on the station, and neither ever reaches a call:
an IP address and a browser are not a caller's business, and the untagged walk
reads Navidrome album by album — far too slow to run while somebody waits.
Both run only when the operator presses Load.
"""

from __future__ import annotations

from aiohttp import web

from api.auth import _write_allowed
from api.wire import _cors, refused
from station import StationClient

NEEDS_LOGIN = ("needs the station admin login (Access → Station) — the station "
               "only shows this to its own admin")


async def _answer(request: web.Request, read) -> web.Response:
    """Run one station read, and say plainly why it didn't. The caller has
    already passed the panel's gate — each handler checks it itself, where
    TestExposedSurface can see it."""
    from station_config import has_admin

    if not has_admin():
        return _cors(request, web.json_response({"ok": False, "error": NEEDS_LOGIN}))
    station = StationClient()
    try:
        got = await read(station)
    finally:
        await station.aclose()
    if not got or got.get("error"):
        reason = (got or {}).get("error") or "the station did not answer"
        return _cors(request, web.json_response({"ok": False, "error": str(reason)[:200]}))
    return _cors(request, web.json_response({"ok": True, **got}))


async def handle_station_listeners(request: web.Request) -> web.Response:
    """GET /station/listeners — the station's live listener connections."""
    if not _write_allowed(request):
        return refused(request)
    return await _answer(request, lambda st: st.listener_connections())


async def handle_station_untagged(request: web.Request) -> web.Response:
    """GET /station/untagged?cursor= — one page of the untagged walk."""
    if not _write_allowed(request):
        return refused(request)
    cursor = str(request.query.get("cursor") or "")[:200]
    return await _answer(request, lambda st: st.untagged_tracks(cursor=cursor))
