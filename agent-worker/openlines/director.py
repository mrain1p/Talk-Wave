"""Opens a line, keeps it going, and closes it — the only thing that decides
when anything airs.

Runs in the web container, beside the other cleanup_ctx loops (the station
warmer, the listener sampler, the hush janitor). The worker never opens or
closes a line; it only reads the record, so a call in progress can never race
the operator's Close button.

Nothing here fires unless the operator turned the feature on, and the default
cadence is manual — `open_lines_every_minutes` is 0 out of the box, so the
only thing that reaches a station's listeners is a button somebody pressed.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from openlines import air, state
from openlines import premise as premise_mod

log = logging.getLogger("talkwave.openlines")

TICK_SECONDS = float(os.environ.get("OPEN_LINES_TICK", "60"))


def _cfg() -> dict:
    import settings as settings_store

    return settings_store.permissions_for(settings_store.load(), "admin")


def _when(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def persona_allowed(cfg: dict, persona: dict) -> bool:
    """The operator's DJ allowlist, by name. Blank = whoever is on air."""
    raw = str(cfg.get("open_lines_personas") or "").strip()
    if not raw:
        return True
    wanted = {n.strip().casefold() for n in raw.split(",") if n.strip()}
    return str(persona.get("name") or "").strip().casefold() in wanted


def listeners_ok(cfg: dict, now_playing: dict) -> tuple[bool, int | None]:
    """Nobody listening, nothing airs. Checked at open and before each
    reminder — never mid-window, because a topic that vanished when somebody
    closed a tab would strand whoever was already typing."""
    from api.stats import _listener_count

    floor = int(cfg.get("open_lines_min_listeners") or 0)
    if floor <= 0:
        return True, None
    count = _listener_count(now_playing or {})
    if count is None:
        # The station did not say. Treat silence as "not proven empty": a
        # station that never reports listeners would otherwise switch the
        # whole feature off with nothing on screen to explain why.
        return True, None
    return count >= floor, count


def _arrivals_since(opened_at: str) -> int:
    """How many conversations came through while the line stood.

    Read from the transcripts already on disk rather than counted live: the
    worker and the web container would otherwise both be incrementing one
    file. Deliberately not "how many were about the topic" — nothing can know
    that — so the sign-off only ever distinguishes none from some.
    """
    start = _when(opened_at)
    if not start:
        return 0
    try:
        from call import record as record_mod
    except ImportError:
        return 0
    seen = 0
    for item in record_mod.recent(60):
        began = _when(item.get("startedAt"))
        if began and began >= start:
            seen += 1
    return seen


async def _live_context(station):
    """Who is on air and what show — the pair an open line belongs to."""
    persona = await station.resolve_live_persona()
    now_playing = await station.now_playing()
    show = await station.active_show(now_playing, None)
    return persona, now_playing, str((show or {}).get("name") or "")


async def open_now(reason: str = "operator", cfg: dict | None = None) -> dict:
    """Put a subject up and open the line.

    Returns a small verdict the panel renders as-is. Every refusal names the
    gate that stopped it: "nothing happened" is the one answer an operator
    cannot act on.
    """
    from station import StationClient

    cfg = cfg or _cfg()
    if not cfg.get("open_lines_enabled"):
        return {"ok": False, "why": "Open Lines is switched off."}

    import secrets_store

    secrets_store.apply_to_env()
    station = StationClient()
    try:
        persona, now_playing, show_name = await _live_context(station)
        if not persona_allowed(cfg, persona):
            who = persona.get("name") or "This DJ"
            return {"ok": False, "why": f"{who} is not on the Open Lines list."}
        ok, count = listeners_ok(cfg, now_playing)
        if not ok:
            floor = cfg.get("open_lines_min_listeners")
            return {"ok": False,
                    "why": f"Only {count} listening — the floor is {floor}."}

        source = str(cfg.get("open_lines_source") or "dj")
        if source == "pool":
            last = int(state.read_raw().get("pool_index", -1))
            text, index = premise_mod.from_pool(cfg, last)
            if not text:
                return {"ok": False, "why": "Your topic list is empty."}
        else:
            text, index = await premise_mod.invent(cfg, station, persona), -1
            if not text:
                return {"ok": False,
                        "why": "The DJ could not come up with one — check the "
                               "model is reachable."}

        spoken = await air.say(station, air.open_direction(text, cfg))
        if not spoken:
            return {"ok": False,
                    "why": "The booth would not take the line — check the "
                           "station admin credentials."}

        record = state.build(
            premise=text, spoken=spoken, persona=persona, show_name=show_name,
            minutes=int(cfg.get("open_lines_minutes") or 60), source=source,
            reminder_minutes=int(cfg.get("open_lines_reminder_minutes") or 0),
            reminder_max=int(cfg.get("open_lines_reminder_max") or 0))
        record["pool_index"] = index
        record["opened_by"] = reason
        state.write(record)
        log.info("open lines: %s opened a line — %s", persona.get("name"), text)
        return {"ok": True, "premise": text, "spoken": spoken,
                "expires_at": record["expires_at"]}
    finally:
        await station.aclose()


async def _sign_off(cfg: dict, record: dict) -> None:
    """The closing line, aired exactly once. `signed_off` is the latch —
    without it a restart would air the sign-off all over again."""
    from station import StationClient

    import secrets_store

    secrets_store.apply_to_env()
    station = StationClient()
    try:
        took = _arrivals_since(str(record.get("opened_at")))
        spoken = await air.say(station, air.close_direction(
            str(record.get("premise") or ""), took))
        record["signed_off"] = True
        record["closed"] = True
        record["sign_off_spoken"] = spoken
        record.setdefault("closed_reason", "expired")
        state.write(record)
        log.info("open lines: closed (%s arrivals while it stood)", took)
    finally:
        await station.aclose()


async def _remind(cfg: dict, record: dict) -> None:
    from station import StationClient

    import secrets_store

    secrets_store.apply_to_env()
    station = StationClient()
    try:
        _persona, now_playing, _show = await _live_context(station)
        ok, _count = listeners_ok(cfg, now_playing)
        if ok:
            await air.say(station, air.remind_direction(
                str(record.get("premise") or ""), cfg,
                str(record.get("spoken") or "")))
        # The slot is spent either way. Banking skipped reminders through a
        # quiet hour would spend them all at once the moment somebody tunes in.
        state.write(state.note_reminder(
            record, int(cfg.get("open_lines_reminder_minutes") or 0)))
    finally:
        await station.aclose()


async def tick(cfg: dict | None = None) -> None:
    """One pass: close what is over, remind what is due, open what is owed."""
    cfg = cfg or _cfg()
    if not cfg.get("open_lines_enabled"):
        return
    record = state.read_raw()

    if record and not state.is_live(record) and not record.get("signed_off"):
        await _sign_off(cfg, record)
        return

    if state.is_live(record) and record.get("next_reminder_at"):
        due = _when(record["next_reminder_at"])
        if due and datetime.now(timezone.utc) >= due:
            await _remind(cfg, record)
        return

    every = int(cfg.get("open_lines_every_minutes") or 0)
    if every <= 0 or state.is_live(record):
        return
    last = _when(record.get("closed_at") or record.get("opened_at"))
    if last and (datetime.now(timezone.utc) - last).total_seconds() < every * 60:
        return
    await open_now(reason="schedule", cfg=cfg)


async def run(app) -> None:
    """cleanup_ctx: the director's own loop.

    <= 0 disables it outright, which is what the suite sets — a test that
    builds the real app must not reach for a network, and this loop would
    otherwise poll the station the moment it starts.
    """
    if TICK_SECONDS <= 0:
        yield
        return

    async def loop() -> None:
        while True:
            try:
                await tick()
            except Exception as e:                             # noqa: BLE001
                log.debug("open lines tick failed: %s", e)
            await asyncio.sleep(TICK_SECONDS)

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:                                      # noqa: BLE001
            pass
