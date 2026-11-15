"""The station's pictures, fetched on the caller's behalf.

The persona avatar and the album cover are proxied rather than linked: the
caller's browser may not be able to reach the station at all, and behind TLS a
plain-http image is blocked as mixed content. Both routes are public — the
widget shows them on every caller's page — which is why this module also keeps
count of what the station said no to.

Moved out of api/live.py with the miss table below (review, 2026-11-15):
live.py builds the card's payload and is on the size ledger; fetching an
image is a different job.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import quote

import httpx
from aiohttp import web

import settings as settings_store
from log_setup import describe

log = logging.getLogger("callin.token")

# WHAT A MISS COSTS, AND WHO PAYS. Every request here used to become a station
# request with an eight-second budget, and an answer of "no such picture" was
# never remembered — so one client asking for /cover/<random> in a loop was a
# flood aimed at the station, relayed by us, for as long as it cared to run
# (review, 2026-11-15). Hits are left exactly as they were: the browser caches
# those, and the station is glad to serve them. Misses get two limits.
#
#   * A miss is remembered for _MISS_TTL, so the same wrong id costs the
#     station one request per half-minute instead of one per ask. A station
#     that timed out counts too — it is the last thing to hammer.
#   * The table is bounded, and FULL MEANS STOP rather than "forget the
#     oldest". Random ids are all different, so remembering them one by one
#     slows nothing down; what bounds the flood is that once _MISS_MAX
#     distinct misses are standing, an id nobody has seen is refused here
#     without the station being asked, until some of them age out. That is
#     more wrong answers than any real page produces in half a minute.
#
# A picture the station HAS served recently is exempt from that stop, so the
# card on a real caller's page keeps its art while somebody else is guessing.
_MISS_TTL = 30.0
_MISS_MAX = 128
_KNOWN_TTL = 600.0
_KNOWN_MAX = 256
_misses: dict[str, float] = {}      # station url -> monotonic forget-at
_known: dict[str, float] = {}       # station url -> monotonic forget-at
_said_full_at = 0.0


def _forget_expired(table: dict[str, float], now: float) -> None:
    for url in [u for u, until in table.items() if until <= now]:
        del table[url]


def _skip_the_station(url: str, now: float) -> bool:
    """Whether to answer 404 here, without asking the station at all."""
    global _said_full_at
    if _misses.get(url, 0.0) > now:
        return True
    if _known.get(url, 0.0) > now:
        return False
    if len(_misses) < _MISS_MAX:
        return False
    _forget_expired(_misses, now)
    if len(_misses) < _MISS_MAX:
        return False
    if now - _said_full_at > _MISS_TTL:
        _said_full_at = now
        log.warning("%d different pictures the station does not have were "
                    "asked for inside %ds — refusing unknown ones without "
                    "asking the station until that eases", _MISS_MAX,
                    int(_MISS_TTL))
    return True


def _note_miss(url: str, now: float) -> None:
    if url not in _misses and len(_misses) >= _MISS_MAX:
        _forget_expired(_misses, now)
        if len(_misses) >= _MISS_MAX:
            return                  # full of live misses: already stopping
    _misses[url] = now + _MISS_TTL


def _note_hit(url: str, now: float) -> None:
    _misses.pop(url, None)
    if url not in _known and len(_known) >= _KNOWN_MAX:
        _forget_expired(_known, now)
        while len(_known) >= _KNOWN_MAX:
            del _known[next(iter(_known))]      # oldest first
    _known[url] = now + _KNOWN_TTL


async def _station_image(what: str, ident: str, path: str, fallback_type: str,
                         max_age: int) -> web.Response:
    # Quoted: the id arrives from the browser, and an unescaped path segment
    # can walk the request somewhere other than the image endpoint.
    url = f"{settings_store.station_base_url()}/{path}/{quote(ident, safe='')}"
    if _skip_the_station(url, time.monotonic()):
        raise web.HTTPNotFound()
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(url)
            r.raise_for_status()
        answer = web.Response(
            body=r.content,
            content_type=r.headers.get("content-type", fallback_type).split(";")[0],
            headers={"Cache-Control": f"public, max-age={max_age}"},
        )
    except Exception as e:
        _note_miss(url, time.monotonic())
        log.info("%s fetch failed for %s: %s", what, ident, describe(e))
        raise web.HTTPNotFound()
    _note_hit(url, time.monotonic())
    return answer


async def handle_avatar(request: web.Request) -> web.StreamResponse:
    """Proxy the station's persona avatar (it lives off /api, at
    /persona-avatar/{id}) so the widget never has to reach the station
    directly from the browser."""
    return await _station_image(
        "avatar", request.match_info["persona_id"], "persona-avatar",
        "image/png", 300)


async def handle_cover(request: web.Request) -> web.StreamResponse:
    """Proxy the station's album art (/cover/{id}) for the same reason the
    avatar is proxied. A day, not five minutes: the station itself calls a
    song's art immutable-at-the-edge."""
    return await _station_image(
        "cover", request.match_info["track_id"], "cover", "image/jpeg", 86400)
