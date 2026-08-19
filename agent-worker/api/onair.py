"""The mixer's fetch leg for live-call clips.

The relay (worker process) pushes `voice_queue.push <url>` at the mixer; the
mixer then curls the clip from HERE (web process) — the two containers share
`data/onair/`, which is the whole hand-off. Public by design, like /vm-air:
the mixer is curl on another network, so the token IS the credential
(onair/chunks.py owns those rules — unguessable filename, short TTL).

Same HEAD/GET split the studio route learned the hard way: the mixer probes
with a HEAD before it downloads, and a probe that burned the single-use URL
left the real GET a 404 six milliseconds later. HEAD peeks; only the GET
spends. Served from memory and deleted before the response goes out — the
fetch is the moment a caller's turn leaves the disk.
"""

from __future__ import annotations

import asyncio
import logging
import os

from aiohttp import web

import settings as settings_store
from api.auth import _write_allowed
from api.wire import _cors
from onair import chunks, hush

log = logging.getLogger("callin.onair")


async def hush_janitor(app: web.Application) -> None:
    """cleanup_ctx: the ONE restorer of the station's Voice switch.

    Workers only ever quiet the station (onair/hush.py); putting it back
    belongs to exactly one process so per-call job processes can never race
    each other over the restore. Each tick reconciles: while any call marker
    is fresh the switch stays down (finishing an assert the worker could not
    confirm), and once none are, the switch goes back — unless the operator
    already flipped it themselves, which the tick respects and stands down
    from. The first tick after a boot is the crash recovery: a stack that
    died mid-call restores the moment it is back on its feet. Runs even when
    the setting is off, because the setting being turned off mid-call must
    not orphan a quieted station — the tick is a no-op unless a flip is
    actually on record."""
    interval = float(os.environ.get("HUSH_JANITOR_INTERVAL", "7"))

    async def loop() -> None:
        while True:
            await hush.janitor_tick(settings_store.load())
            await asyncio.sleep(interval)

    task = asyncio.create_task(loop())
    app["hush_task"] = task
    try:
        yield
    finally:
        task.cancel()


async def handle_on_air_dump(request: web.Request) -> web.Response:
    """The operator's dump button: the turn still in hand on a live phone-in
    dies before it airs, and the segment signs off. Admin, and armed ONLY
    while an on-air call is actually live — the marker crosses to the worker
    through the shared store, and a marker with nothing to kill would sit
    waiting for the next caller instead."""
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed"}, status=401))
    from api.tokens import on_air_call_live

    if not on_air_call_live():
        return _cors(request, web.json_response(
            {"ok": False, "live": False,
             "note": "no phone-in is on the air right now"}))
    chunks.request_dump()
    log.info("operator dump: marker armed for the live on-air call")
    return _cors(request, web.json_response({"ok": True, "live": True}))


async def handle_on_air_clip(request: web.Request) -> web.StreamResponse:
    token = request.match_info.get("token", "")
    if request.method == "HEAD":
        path = chunks.path_for(token)
        if not path:
            raise web.HTTPNotFound()
        return web.Response(headers={
            "Cache-Control": "no-store", "Content-Type": "audio/wav",
            "Content-Length": str(path.stat().st_size)})
    path = chunks.path_for(token)
    if not path:
        raise web.HTTPNotFound()
    data = path.read_bytes()
    chunks.discard(token)
    return web.Response(body=data, headers={
        "Cache-Control": "no-store", "Content-Type": "audio/wav"})
