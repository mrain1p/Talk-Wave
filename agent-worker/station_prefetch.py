"""The station snapshot, fetched while the caller's browser is still joining.

The worker re-reads the station at the top of every call — six concurrent
reads, measured at ~2.5s of ringing on a healthy deployment (2026-08-18) and
far worse on a congested one. But the moment a call is coming is known
EARLIER, in the other process: the token server mints the room a second or
two before LiveKit dispatches the job here. This file is that head start
crossing the web→worker seam, over the same shared-data/ file pattern the
mixer verdict uses: the mint writes one JSON, the worker reads it back, and
the ringing stops waiting on reads whose answers already exist.

Freshness is the whole contract. A stale snapshot is the WRONG DJ answering
the phone, so recall() refuses anything older than MAX_AGE_SECS outright and
the worker simply does its own reads, exactly as before this existed. Same
posture as every station read: a miss here may never cost a call anything
but the seconds it already cost.

No polling and no daemon, on purpose: nothing in this module runs unless a
call is actually being minted, so a quiet line costs zero.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("callin.prefetch")

PATH = Path(
    os.environ.get("STATION_PREFETCH_PATH",
                   Path(__file__).parent.parent / "data" / "station-prefetch.json")
)

# Mint -> dispatch -> prepare is a second or two; anything much older than
# that was fetched for some OTHER call and has to be treated as unknown.
MAX_AGE_SECS = 10.0


async def capture(with_skills: bool) -> None:
    """Fetch one snapshot and store it. Called by the token server per mint.

    Builds its own short-lived clients rather than borrowing anything from the
    serving process: the mint response must not wait on the station, so this
    runs fire-and-forget and cleans up after itself.
    """
    from station import StationClient
    from station_config import StationConfig

    station, station_cfg = StationClient(), StationConfig()
    try:
        snapshot, station_settings = await asyncio.gather(
            station.snapshot(with_skills=with_skills),
            station_cfg.settings(),
        )
        store(snapshot, station_settings, with_skills=with_skills)
    except Exception as e:                                       # noqa: BLE001
        log.info("snapshot prefetch skipped (%s) — the worker reads instead", e)
    finally:
        await station.aclose()
        await station_cfg.aclose()


def store(snapshot: dict, station_settings: dict, *, with_skills: bool) -> None:
    """Write the head start down where the worker can find it."""
    payload = {
        "t": time.time(),
        "withSkills": bool(with_skills),
        "snapshot": snapshot,
        "stationSettings": station_settings or {},
    }
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        # Owner-only, like every other file in data/: the station settings
        # payload can carry persona config an operator would not publish.
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(PATH)
    except Exception as e:                                       # noqa: BLE001
        log.info("could not store the snapshot prefetch: %s", e)


def recall(*, with_skills: bool) -> tuple[dict, dict] | None:
    """(snapshot, station_settings) from a fresh mint, or None to read live.

    Refuses three things, all towards the same end — the worker must never
    answer off worse data than its own read would get:

      * anything older than MAX_AGE_SECS (fetched for some other call)
      * a skills mismatch (the prompt would gain or lose segments the
        operator's settings decided otherwise about, mid-ring)
      * a snapshot with neither a DJ nor a roster (the station was down at
        mint time; two seconds later it deserves the retry the worker's own
        read effectively is)
    """
    try:
        d = json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        return None
    if not isinstance(d, dict):
        return None
    if time.time() - float(d.get("t") or 0) > MAX_AGE_SECS:
        return None
    if bool(d.get("withSkills")) != bool(with_skills):
        return None
    snap = d.get("snapshot")
    if not isinstance(snap, dict) or not (snap.get("dj") or snap.get("personas")):
        return None
    station_settings = d.get("stationSettings")
    if not isinstance(station_settings, dict):
        station_settings = {}
    return snap, station_settings
