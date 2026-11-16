"""The station reads a line without MCP can still make.

On calls the station's MCP server serves `subwave_now_playing`,
`subwave_station_state` and `subwave_schedule` through the allowlist. The
text line carries no MCP
by design — and on 2026-08-27 that meant a caller asking for tracks "similar
to my current queue" was answered from a guess: the DJ had no way to LOOK,
missed the mark, spent half the call's action budget undoing the miss, and
finished by inventing a station rule ("it only holds one track at a time")
to explain a duplicate it could not see. It even reached for
subwave_station_state and was told "no such tool".

These wrappers serve the same reads over the station REST client the line
already holds, under the same names, so every prompt rule that names them
holds on both mouths. Built only where MCP is absent: always on the chat,
and on a call ONLY when the MCP warm-up decisively failed (call/session.py
skips the dead toolset and builds these instead) — never beside a live MCP
session, which would put two tools on one name.

`request_status_tool` at the bottom is NOT one of those twins. It is the
only server of `subwave_request_status` on every line (registry: LOCAL),
built by `build_library_tools` beside the request tool — see its docstring.
It lives here because it is a station read in the same honest-miss voice.
"""

from __future__ import annotations

from datetime import datetime

from brain.briefing import _listener_count
from ..actions import CallActions
from station import StationClient

from .music import _when_it_plays, read_receipt
from .registry import library_search_needs_mcp
from .rows import _fmt_track, _has_words, _txt

# The station numbers its days the way a browser does: 0 is Sunday. (Read
# against the operator's own station for api/guide.py, 2026-09-02 — the grid
# is "0".."6", each day twenty-four show ids.)
_DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
         "Saturday")
_DAY_NUMBERS = frozenset("0123456")


def _station_now(schedule: dict) -> tuple[datetime, str]:
    """The time ON THE STATION'S CLOCK, and the zone it was read in.

    The grid is the station's own week, so "tonight" is the station's night,
    not this container's — /schedule publishes its timezone for exactly
    that. An unknown or missing zone (or a host with no tz database) falls
    back to this machine's local time and says so by returning no zone.
    """
    zone = _txt((schedule or {}).get("timezone"), 64)
    if zone:
        try:
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo(zone)), zone
        except Exception:                                      # noqa: BLE001
            pass
    return datetime.now().astimezone(), ""


def _cell_show(cell) -> str:
    """The show id one grid cell names, whatever the cell's shape."""
    if isinstance(cell, str):
        return cell.strip()
    if isinstance(cell, dict):
        for key in ("showId", "show", "id"):
            if isinstance(cell.get(key), str):
                return cell[key].strip()
    return ""


def _week(schedule: dict) -> dict[int, list[str]]:
    """day number (0 = Sunday) -> twenty-four show ids, "" for an empty hour.

    Only the shape a released station sends: days keyed by number, one cell
    an hour. A grid in any other shape reads as NO grid, which the tool
    answers honestly — a mis-parsed hour would be a slot the DJ then quotes.
    """
    grid = (schedule or {}).get("schedule")
    week: dict[int, list[str]] = {}
    for key, day in (grid.items() if isinstance(grid, dict) else ()):
        number = str(key).strip()
        if number in _DAY_NUMBERS and isinstance(day, list):
            cells = [_cell_show(c) for c in day[:24]]
            week[int(number)] = cells + [""] * (24 - len(cells))
    return week


def _labels(schedule: dict) -> dict[str, str]:
    """show id -> "The Overlook (with Wade)", from the schedule's own rows."""
    djs = {str(p.get("id")): _txt(p.get("name"))
           for p in ((schedule or {}).get("personas") or [])
           if isinstance(p, dict) and p.get("id")}
    out = {}
    for s in ((schedule or {}).get("shows") or []):
        if isinstance(s, dict) and s.get("id") and s.get("name"):
            dj = djs.get(str(s.get("personaId") or ""), "")
            out[str(s["id"])] = _txt(s["name"]) + (f" (with {dj})" if dj else "")
    return out


def _runs(cells: list[str]) -> list[tuple[int, int, str]]:
    """Twenty-four hourly cells as (start, end, show id) runs, gaps dropped."""
    runs: list[tuple[int, int, str]] = []
    for hour, sid in enumerate(cells):
        if runs and runs[-1][2] == sid and runs[-1][1] == hour:
            runs[-1] = (runs[-1][0], hour + 1, sid)
        else:
            runs.append((hour, hour + 1, sid))
    return [r for r in runs if r[2]]


def _day_line(day: int, cells: list[str], labels: dict[str, str]) -> str:
    slots = [f"{a:02d}:00-{b % 24:02d}:00 {labels.get(sid, sid)}"
             for a, b, sid in _runs(cells)]
    return f"{_DAYS[day]}: " + ("; ".join(slots) if slots
                                else "nothing scheduled")


def _now_and_next(cells: list[str], labels: dict[str, str], hour: int) -> str:
    """What the grid has in this hour, and the next change today — the two
    lines "who's on after this?" is actually asking for."""
    runs = _runs(cells)
    bits = [f"On the grid now: {labels.get(sid, sid)}, until {b % 24:02d}:00."
            for a, b, sid in runs if a <= hour < b]
    bits += [f"Next on the grid today: {labels.get(sid, sid)} at {a:02d}:00."
             for a, b, sid in runs if a > hour][:1]
    return " ".join(bits)


def _which_day(word: str, today: int) -> int | None:
    """"today", "tomorrow", "friday", "fri" -> a day number; else None."""
    w = str(word or "").strip().casefold()
    if w in ("today", "tonight", "now"):
        return today
    if w == "tomorrow":
        return (today + 1) % 7
    for n, name in enumerate(_DAYS):
        if len(w) >= 3 and name.casefold().startswith(w):
            return n
    return None


_GRID_CAVEAT = (
    "This is the GRID — what is SCHEDULED. A takeover or a steer can put "
    "something else on air, so what is actually on NOW is the show in your "
    "briefing, never this. Quote times from these lines only: a show with "
    "no slot here has no time you can name, and you do not invent one.")


def running_order(schedule: dict, when: datetime, zone: str = "",
                  day: str = "", show: str = "") -> str:
    """The schedule read, as the DJ hears it. Pure: the clock is passed in."""
    week, labels = _week(schedule), _labels(schedule)
    if not week:
        return (
            "The station's schedule came back without an hour-by-hour grid "
            "— the read failed, or this station publishes none. You have "
            "the show NAMES from your briefing and nothing about times: say "
            "you can't see the running order from here, and do not guess a "
            "slot.")
    today = (when.weekday() + 1) % 7
    clock = (f"Station time{' (' + zone + ')' if zone else ''}: it is "
             f"{_DAYS[today]}, {when:%H:%M}.")
    if str(show or "").strip():
        hits = [f"{_DAYS[d]} {a:02d}:00-{b % 24:02d}:00"
                for d in sorted(week) for a, b, sid in _runs(week[d])
                if _has_words(labels.get(sid, sid), show)]
        if not hits:
            return (f"{clock} Nothing on the grid answers to \"{show}\" — "
                    "it has no slot this week, or the name is off. Shows "
                    "that DO have slots: "
                    + ", ".join(sorted({labels.get(s, s) for d in week
                                        for s in week[d] if s})[:12])
                    + ". Do not invent a time for it.")
        return (f"{clock} \"{show}\" is on the grid at: "
                + "; ".join(hits[:14]) + ". " + _GRID_CAVEAT)
    wanted = _which_day(day, today)
    if str(day or "").strip() and wanted is None:
        return (f"{clock} \"{day}\" is not a day this can read — pass "
                "today, tomorrow, or a weekday name.")
    days = [wanted] if wanted is not None else [today, (today + 1) % 7]
    lines = [_day_line(d, week.get(d) or [""] * 24, labels) for d in days]
    if today in days:
        lines.insert(0, _now_and_next(week.get(today) or [""] * 24, labels,
                                      when.hour))
    return "\n".join(filter(None, [clock, *lines, _GRID_CAVEAT]))


def build_read_tools(cfg: dict, station: StationClient,
                     actions: CallActions | None = None) -> list:
    """The station reads, locally served. Empty without credentials:
    /state and /now-playing are admin REST here, so an uncredentialed
    wrapper could only ever answer nothing — and a tool that always answers
    nothing teaches the DJ the station is empty."""
    if library_search_needs_mcp():
        return []
    from livekit.agents import llm as lk_llm

    tools = []

    @lk_llm.function_tool(name="subwave_now_playing")
    async def now_playing() -> str:
        """What is on air RIGHT NOW — the current record and how it is
        filed. Read this before any judgement that leans on the current
        track: "more like this", "does this fit what's on", "what's
        playing"."""
        now = await station.now_playing()
        track = None
        for key in ("nowPlaying", "track", "current"):
            candidate = (now or {}).get(key)
            if isinstance(candidate, dict):
                track = candidate
                break
        if track is None and isinstance(now, dict):
            track = now
        if not isinstance(track, dict) or not (
                track.get("title") or track.get("artist")):
            return (
                "Nothing identifiable came back for what's on air — the "
                "read failed or the booth is between records. That is NOT "
                "an answer about the music: don't guess at the current "
                "record, say you can't see the booth from here."
            )
        # The count the MCP tool and the briefing both carry, and this twin
        # dropped (review, 2026-11-15): on the text line "is anyone even
        # listening?" had a live answer on the phone and none here. Same
        # reader as the briefing, so the two cannot disagree about shape.
        listeners = _listener_count(now if isinstance(now, dict) else {})
        crowd = "" if listeners is None else (
            " Nobody else is tuned in right now." if listeners <= 0 else
            f" {listeners} listener{'s' if listeners != 1 else ''} tuned "
            "in right now.")
        return (
            "On air right now: " + _fmt_track(track) + ". This is the "
            "record playing THIS moment — anything queued comes after it."
            + crowd
        )

    tools.append(now_playing)

    @lk_llm.function_tool(name="subwave_station_state")
    async def station_state() -> str:
        """The station's live queue — what is actually waiting to play, in
        order. Read this BEFORE answering anything about the queue, before
        adding to it under a caller's constraint ("no duplicates",
        "something like what's queued"), and whenever a caller says a track
        is or isn't in it — they can see the queue, and guessing against
        someone who can see is how the DJ ends up wrong out loud."""
        state = await station.state()
        upcoming = [t for t in ((state or {}).get("upcoming") or [])
                    if isinstance(t, dict)]
        if not upcoming:
            return (
                "The queue shows nothing waiting — or the read failed, and "
                "the two look the same from here. Say what you can actually "
                "see; don't declare the queue empty as a fact about the "
                "station if the caller says otherwise."
            )
        shown = upcoming[:12]
        lines = [f"{i}. " + _fmt_track(t) for i, t in enumerate(shown, 1)]
        extra = (f"\n…and {len(upcoming) - len(shown)} more behind these"
                 if len(upcoming) > len(shown) else "")
        return (
            "Waiting in the queue, in order:\n" + "\n".join(lines) + extra
            + "\nThis is the station's whole queue — every caller's picks "
            "and the station's own, not just this call's. Read it before "
            "promising positions, and before adding anything a caller asked "
            "to keep duplicate-free."
        )

    tools.append(station_state)

    # The third twin (review, 2026-11-15). The text line never has MCP and
    # an MCP-failed call has lost it, so "what's on tonight", "when is X
    # on", "who's after this" had no read at all — the chat conduct even
    # scripted "I don't have the running order in front of me". /schedule
    # is the same public read the briefing's roster comes from.
    @lk_llm.function_tool(name="subwave_schedule")
    async def schedule(day: str = "", show: str = "") -> str:
        """The station's weekly schedule — which show is on when, and who
        hosts it. With no arguments: today's and tomorrow's running order.
        `day` (today, tomorrow, or a weekday) for another day; `show` (a
        show's name) for every slot it has this week. Read this before
        giving ANY time for a show — "what's on tonight", "when is X on",
        "who's on after this" — rather than answering from memory."""
        raw = await station.schedule()
        when, zone = _station_now(raw)
        return running_order(raw, when, zone, day=day, show=show)

    tools.append(schedule)

    return tools


def request_status_tool(station: StationClient,
                        actions: CallActions | None = None):
    """`subwave_request_status`, the one route to it on every line.

    Built by `build_library_tools` beside the request tool it follows, NOT by
    `build_read_tools` — it was a twin there, served only where MCP was
    absent, and on a normal call the MCP tool stood in its place demanding a
    request id the model is never shown (review, 2026-11-15). The registry
    row is LOCAL now, so this wrapper is the only thing wearing the name.
    """
    from livekit.agents import llm as lk_llm

    @lk_llm.function_tool(name="subwave_request_status")
    async def request_status(requestId: str = "") -> str:
        """Where a song request stands. Call it with NO arguments: it
        checks the LAST request this call put in — you are never shown a
        request id, so there is nothing to pass and nothing to guess.
        (requestId is only for an id a tool result actually handed you.)
        Use when a caller asks whether their request went in, or when it
        will play."""
        rid = (requestId or "").strip() or getattr(
            actions, "last_request_id", "")
        if not rid:
            return (
                "No request id to look up — this call hasn't put one "
                "in, and none was given. If they mean an earlier call's "
                "request, the queue itself is the honest place to look: "
                "subwave_station_state."
            )
        st = await station.request_status(str(rid))
        if not st:
            return (
                "The station couldn't say where that request stands — "
                "the read failed. That is NOT a no: don't tell them it "
                "was lost, say you can't see it from here just now."
            )
        verdict, track, ack, position = read_receipt(st)
        if verdict == "failed":
            return (
                "That request did not go in: "
                f"{ack or 'the station found nothing close enough'}. "
                "It is NOT queued. Say so plainly rather than leaving "
                "them expecting it."
            )
        if verdict == "answered":
            # The booth replied in words and queued nothing — its own
            # resolution, not a pruned request. This branch was missing,
            # so "did my request go in?" was answered "pruned or lost to
            # a restart" about a request the station had just answered.
            return (
                "The booth answered that request in words and queued "
                "NOTHING: there is no track in the queue for it. "
                + (f"The station said: {ack}. " if ack else "")
                + "Relay the reply; do not promise a title or a time, "
                "and do not tell them it was lost."
            )
        if verdict == "standing":
            return (
                "That request matched " + _fmt_track(track) + ", but the "
                "station added no new queue entry — it is already in the "
                "running order, or the booth answered without queueing "
                "it. Do not guess at when it plays."
            )
        if verdict == "queued":
            return (
                "That request is matched: " + _fmt_track(track) + ". "
                "It is queued, not playing yet. "
                + _when_it_plays(position)
            )
        if verdict == "pending":
            return (
                "Still being matched in the booth — not queued yet, and "
                "not lost. Check again in a little while, and don't "
                "promise a title in the meantime."
            )
        return (
            "The station doesn't know that request any more — it was "
            "pruned or lost to a restart. Stop checking it; if the "
            "caller still wants the song, put the request in again."
        )

    return request_status
