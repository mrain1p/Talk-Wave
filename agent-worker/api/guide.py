"""The programme guide: the station's week, shaped for the schedule card.

The widget's third card (operator, 2026-09-02) shows today's shows hour by
hour and then every show with its DJ, tagline and times — the shape of the
operator's own guide page, in the card's clothes. The station's `/schedule`
is public and listener-facing (show definitions, the 7×24 grid, a persona
index, the station's timezone), but the browser must never reach the station
itself: on most deployments it is on a LAN or plain http, which is why the
avatar and the cover go through this server too. So this reads it once,
caches it for five minutes — a schedule changes weekly, not by the poll —
and hands the card a shape it can paint without knowing what the station's
grid looks like.

The grid's exact shape is the one thing here written DEFENSIVELY rather than
from a captured payload: the station was off the LAN when this was built,
and the API document shows the day keys and an array per day but not what
the array holds. `_hours` accepts the three shapes a schedule grid comes in —
one entry per hour, a list of ranges, or an hour-keyed map — and normalises
all of them to twenty-four slots of show id or None. A shape it cannot read
degrades to an empty day, never to a broken card.

Gated on the operator's `show_guide` switch: with the card off there is
nothing to read, and the answer is a plain 404 rather than an empty guide
that looks like a station with no schedule.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import quote

from aiohttp import web

import settings as settings_store
from api.wire import _cors
from log_setup import describe
from station import StationClient

log = logging.getLogger("callin.token")

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
GUIDE_TTL = 300
_guide_cache: dict = {"at": 0.0, "data": None}


def _text(v, limit: int = 240) -> str:
    return str(v or "").strip()[:limit]


def _show_id(cell) -> str | None:
    """The show a grid cell names, whatever the cell's shape."""
    if cell is None or cell is False:
        return None
    if isinstance(cell, str):
        return cell.strip() or None
    if isinstance(cell, dict):
        for key in ("showId", "show", "id"):
            v = cell.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, dict) and isinstance(v.get("id"), str):
                return v["id"].strip() or None
    return None


def _hours(day) -> list:
    """Twenty-four slots of show id (or None) from whatever a day holds."""
    slots: list = [None] * 24
    if isinstance(day, dict):
        # Hour-keyed: {"0": "late-shift", ..., "23": ...}
        for k, v in day.items():
            try:
                h = int(k)
            except (TypeError, ValueError):
                continue
            if 0 <= h < 24:
                slots[h] = _show_id(v)
        return slots
    if not isinstance(day, list):
        return slots
    ranged = [c for c in day if isinstance(c, dict)
              and any(k in c for k in ("start", "from", "startHour", "hour"))]
    if ranged and len(ranged) == len(day):
        # A list of ranges: {"showId": ..., "start": 18, "end": 19}
        for c in day:
            start = c.get("start", c.get("from", c.get("startHour", c.get("hour"))))
            end = c.get("end", c.get("to", c.get("endHour")))
            try:
                start = int(start)
                end = int(end) if end is not None else start + 1
            except (TypeError, ValueError):
                continue
            sid = _show_id(c)
            for h in range(max(0, start), min(24, max(end, start + 1))):
                slots[h] = sid
        return slots
    # One entry per hour, the common case.
    for h, c in enumerate(day[:24]):
        slots[h] = _show_id(c)
    return slots


def _day_key(name) -> str | None:
    k = str(name or "").strip().lower()[:3]
    return k if k in DAYS else None


def shape(raw: dict) -> dict:
    """The station's /schedule, normalised for the card."""
    raw = raw if isinstance(raw, dict) else {}

    def rows(key: str) -> list:
        v = raw.get(key)
        return v if isinstance(v, list) else []

    personas = []
    seen = set()
    for p in rows("personas"):
        if not isinstance(p, dict):
            continue
        pid = _text(p.get("id"), 80)
        if not pid or pid in seen:
            continue
        seen.add(pid)
        personas.append({
            "id": pid,
            "name": _text(p.get("name"), 80) or pid,
            "tagline": _text(p.get("tagline"), 160),
            # Through this server's own proxy, never the station's path:
            # the browser cannot reach the station on most deployments.
            "avatar": f"/avatar/{quote(pid, safe='')}" if p.get("avatar") else "",
            "soul": _text(p.get("soul"), 600),
        })
    shows = []
    seen_shows = set()
    for s in rows("shows"):
        if not isinstance(s, dict):
            continue
        sid = _text(s.get("id"), 80)
        if not sid or sid in seen_shows:
            continue
        seen_shows.add(sid)
        raw_guests = s.get("guestPersonaIds")
        guests = [_text(g, 80) for g in (raw_guests if isinstance(raw_guests, list) else [])
                  if isinstance(g, str) and g.strip()]
        shows.append({
            "id": sid,
            "name": _text(s.get("name"), 120) or sid,
            "topic": _text(s.get("topic"), 160),
            "mood": _text(s.get("mood"), 80),
            "personaId": _text(s.get("personaId"), 80),
            "guestPersonaIds": guests,
            "description": _text(s.get("description") or s.get("blurb")
                                 or s.get("summary"), 600),
        })
    grid = {d: [None] * 24 for d in DAYS}
    sched = raw.get("schedule")
    if isinstance(sched, dict):
        for name, day in sched.items():
            key = _day_key(name)
            if key:
                grid[key] = _hours(day)
    return {
        "timezone": _text(raw.get("timezone"), 64),
        "personas": personas,
        "shows": shows,
        "grid": grid,
        "soulsPublished": bool(raw.get("soulsPublished")),
    }


async def handle_guide(request: web.Request) -> web.Response:
    """GET /guide — the week, cached; 404 while the card is switched off."""
    if not settings_store.load().get("show_guide"):
        raise web.HTTPNotFound()
    now = time.time()
    if _guide_cache["data"] is not None and now - _guide_cache["at"] < GUIDE_TTL:
        return _cors(request, web.json_response(_guide_cache["data"]))
    station = StationClient()
    try:
        raw = await station.schedule()
    except Exception as e:                                     # noqa: BLE001
        log.info("guide: schedule read failed: %s", describe(e))
        raw = {}
    finally:
        await station.aclose()
    data = shape(raw)
    # A station that answered nothing is not cached: the next open asks
    # again, rather than showing an empty week for five minutes.
    if data["shows"]:
        _guide_cache["data"] = data
        _guide_cache["at"] = now
    return _cors(request, web.json_response(data))
