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

Read against the operator's own station (2026-09-02): the grid is keyed by
day NUMBER, "0" to "6" with 0 the Sunday (the browser's getDay order), each
day twenty-four show ids; a show's `name` carries its tagline after a middle
dot ("THE PIAZZA · Golden-Era Pop"), its `topic` is a paragraph — the show's
description, not a label — and `moods` is a list beside the single `mood`.
Persona `soul` is the DJ's own description, published when the operator
says so. `_hours` still accepts the other two shapes a grid could come in
(ranges, an hour-keyed map) and day NAMES as well as numbers, so a station
on another build degrades to an empty day, never to a broken card.

Gated on the operator's `show_guide` switch: with the card off there is
nothing to read, and the answer is a plain 404 rather than an empty guide
that looks like a station with no schedule.
"""

from __future__ import annotations

import asyncio
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
# The station numbers its days the way a browser does: 0 is Sunday.
_DAY_NUMBERS = {"0": "sun", "1": "mon", "2": "tue", "3": "wed", "4": "thu",
                "5": "fri", "6": "sat", "7": "sun"}
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
    k = str(name or "").strip().lower()
    if k in _DAY_NUMBERS:
        return _DAY_NUMBERS[k]
    return k[:3] if k[:3] in DAYS else None


def _persona(p) -> dict | None:
    """One persona row for the card, or None for a row that is not one."""
    if not isinstance(p, dict):
        return None
    pid = _text(p.get("id"), 80)
    if not pid:
        return None
    return {
        "id": pid,
        "name": _text(p.get("name"), 80) or pid,
        "tagline": _text(p.get("tagline"), 160),
        # Through this server's own proxy, never the station's path:
        # the browser cannot reach the station on most deployments.
        "avatar": f"/avatar/{quote(pid, safe='')}" if p.get("avatar") else "",
        # The DJ's own description — the operator's souls run to two
        # thousand characters and they are the point of the card.
        "soul": _text(p.get("soul"), 2500),
    }


def _show(s) -> dict | None:
    """One show row for the card, or None for a row that is not one."""
    if not isinstance(s, dict):
        return None
    sid = _text(s.get("id"), 80)
    if not sid:
        return None
    raw_guests = s.get("guestPersonaIds")
    guests = [_text(g, 80) for g in (raw_guests if isinstance(raw_guests, list) else [])
              if isinstance(g, str) and g.strip()]
    # "THE PIAZZA · Golden-Era Pop": the name is the title and its
    # tagline in one string; the card wants them apart.
    full = _text(s.get("name"), 160) or sid
    title, _, tagline = full.partition(" · ")
    raw_moods = s.get("moods")
    moods = [_text(m, 40) for m in (raw_moods if isinstance(raw_moods, list) else [])
             if isinstance(m, str) and m.strip()]
    if not moods and _text(s.get("mood"), 40):
        moods = [_text(s.get("mood"), 40)]
    return {
        "id": sid,
        "name": full,
        "title": title.strip() or full,
        "tagline": tagline.strip(),
        "moods": moods,
        "personaId": _text(s.get("personaId"), 80),
        "guestPersonaIds": guests,
        # The show's description IS its topic on this station (a
        # paragraph, the DJ's Show Card); other builds may name it.
        "description": _text(s.get("description") or s.get("blurb")
                             or s.get("summary") or s.get("topic"), 1500),
    }


def _grid(sched) -> dict:
    """Seven named days of twenty-four slots, from whatever the station keyed."""
    grid = {d: [None] * 24 for d in DAYS}
    if isinstance(sched, dict):
        for name, day in sched.items():
            key = _day_key(name)
            if key:
                grid[key] = _hours(day)
    return grid


def _unique(rows, make) -> list:
    """Shaped rows, first of each id kept, junk dropped."""
    out, seen = [], set()
    for r in (rows if isinstance(rows, list) else []):
        row = make(r)
        if row and row["id"] not in seen:
            seen.add(row["id"])
            out.append(row)
    return out


def _on_air(now) -> dict:
    """The show on air as it is RUNNING, from /now-playing: its id and this
    episode's angle — "Tonight's angle" on the operator's guide — which the
    schedule (the show as configured) never carries."""
    ctx = (now or {}).get("context") if isinstance(now, dict) else None
    active = (ctx or {}).get("activeShow") if isinstance(ctx, dict) else None
    if not isinstance(active, dict):
        return {}
    sid = _text(active.get("id"), 80)
    if not sid:
        return {}
    return {"id": sid, "angle": _text(active.get("episodeAngle"), 600)}


def shape(raw: dict, now: dict | None = None) -> dict:
    """The station's /schedule, normalised for the card; `now` is the
    station's /now-playing, for the angle of the show on air."""
    raw = raw if isinstance(raw, dict) else {}
    return {
        "timezone": _text(raw.get("timezone"), 64),
        "personas": _unique(raw.get("personas"), _persona),
        "shows": _unique(raw.get("shows"), _show),
        "grid": _grid(raw.get("schedule")),
        "soulsPublished": bool(raw.get("soulsPublished")),
        "onAir": _on_air(now),
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
        raw, playing = await asyncio.gather(station.schedule(), station.now_playing())
    except Exception as e:                                     # noqa: BLE001
        log.info("guide: station read failed: %s", describe(e))
        raw, playing = {}, {}
    finally:
        await station.aclose()
    data = shape(raw, playing)
    # A station that answered nothing is not cached: the next open asks
    # again, rather than showing an empty week for five minutes.
    if data["shows"]:
        _guide_cache["data"] = data
        _guide_cache["at"] = now
    return _cors(request, web.json_response(data))
