"""When the show currently on air gives way to the next one.

The station publishes its week as an hour grid, not as start/end times:
`/schedule` carries `schedule` as a map of day -> 24 show ids, plus the
station's own `timezone`. So the end of the current programme is found by
walking forward from this hour until the id changes.

Day indexing is Sunday-first. That is not a guess — SUB/WAVE's own
`controller/src/schemas/schedule.ts` says so:

    // 0 (Sunday) .. 6 (Saturday), matching JS Date.getDay(); 24 hours per day.

Python's `weekday()` is Monday-first, so every read here converts.

Why an open line cares: `state.is_live` already kills a line when the show
changes underneath it, so a 60-minute line opened 20 minutes before a
changeover was already going to end early — it just claimed otherwise to
everyone looking at it. Bounding the recorded expiry makes the panel's
countdown, the DJ's sense of how long it stands, and the sign-off all agree
with what was always going to happen.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("talkwave.openlines")

# A week. Past this the grid is repeating itself and something is wrong with
# the read, so we stop rather than loop.
MAX_HOURS = 24 * 7

# Below this, bounding to the programme is worse than not bounding: a line cut
# to the last two minutes of a show airs its invitation and its sign-off back
# to back. The show change ends it anyway, through state.is_live.
MIN_USEFUL_MINUTES = 5


def _zone(schedule: dict):
    name = str((schedule or {}).get("timezone") or "").strip()
    if not name:
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:                                          # noqa: BLE001
        # An unknown zone name must not stop a line opening; the duration
        # setting simply stays in charge.
        log.info("unknown station timezone %r, falling back to UTC", name)
        return timezone.utc


def _grid(schedule: dict) -> dict:
    grid = (schedule or {}).get("schedule")
    return grid if isinstance(grid, dict) else {}


def _slot(grid: dict, day_sun_first: int, hour: int) -> str:
    row = grid.get(str(day_sun_first))
    if not isinstance(row, list) or len(row) <= hour:
        return ""
    return str(row[hour] or "")


def item_end(schedule: dict, show_id: str, now: datetime | None = None) -> datetime | None:
    """When the run of hours holding `show_id` ends, or None.

    None means "the schedule cannot answer" — no grid, an overridden schedule,
    or this show not being where the clock says it is. Every caller treats that
    as "no bound", never as "ends now": a station whose schedule we cannot read
    must still be able to open a line.
    """
    grid = _grid(schedule)
    if not grid or not show_id:
        return None
    # A manual override means the grid is not what is actually on air.
    if (schedule or {}).get("override"):
        return None

    tz = _zone(schedule)
    local = (now or datetime.now(timezone.utc)).astimezone(tz)
    day = (local.weekday() + 1) % 7          # Monday-first -> Sunday-first

    if _slot(grid, day, local.hour) != str(show_id):
        # The clock and the grid disagree — a live override, a station mid-edit,
        # or a show that is on air outside its slot. Do not bound on a guess.
        return None

    # Walk to the first hour that is somebody else's.
    cursor = local.replace(minute=0, second=0, microsecond=0)
    for _ in range(MAX_HOURS):
        cursor += timedelta(hours=1)
        c_local = cursor.astimezone(tz)
        c_day = (c_local.weekday() + 1) % 7
        if _slot(grid, c_day, c_local.hour) != str(show_id):
            return cursor.astimezone(timezone.utc)
    return None


def bounded_minutes(schedule: dict, show_id: str, wanted: int,
                    now: datetime | None = None) -> tuple[int, bool]:
    """How long a line may really stand: `wanted`, or less if the programme
    ends first. Returns the minutes and whether the show was the reason.

    Never bounds below MIN_USEFUL_MINUTES. Cutting a line to the last minute
    of a programme would air an invitation and a sign-off back to back, which
    is worse than not bounding at all — so in the tail of a show the wanted
    duration is kept and `is_live` ends it at the changeover, exactly as it
    did before any of this existed.
    """
    wanted = max(1, int(wanted or 1))
    end = item_end(schedule, show_id, now)
    if not end:
        return wanted, False
    start = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    left = int((end - start).total_seconds() // 60)
    if left < MIN_USEFUL_MINUTES or left >= wanted:
        return wanted, False
    return left, True
