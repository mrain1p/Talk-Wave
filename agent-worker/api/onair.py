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

import logging

from aiohttp import web

from onair import chunks

log = logging.getLogger("callin.onair")


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
