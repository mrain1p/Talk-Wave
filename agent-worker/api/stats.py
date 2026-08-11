"""The panel's activity data: the concurrent-listener series, sampled.

The rest of the ACTIVITY strip — doors, ratings, time-to-first-word — is
derived client-side from /calls, because the records already carry their
timestamps, kinds, problems and ratings. The listener curve is the one series
nothing stores: the station answers "how many right now" and forgets, so this
module asks on a timer and keeps the answers. Without it the chart has no
honest data source at all, and the spec is explicit that the frame renders
empty rather than faking one.

The buffer survives restarts through a small JSON file beside the other
data/ state (LISTENERS_PATH overrides, same convention as CALLS_PATH), and
prunes itself to the 30 days the month view can show. An unreachable station
is recorded as a GAP — no sample — never as a zero: "nobody listening" and
"nobody answered" are different facts, and the chart must not flatter an
outage into a quiet night.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from aiohttp import web

from api.auth import _write_allowed
from api.wire import _cors
from station import StationClient, describe

log = logging.getLogger("callin.stats")

LISTENERS_PATH = Path(
    os.environ.get("LISTENERS_PATH",
                   Path(__file__).parent.parent.parent / "data" / "listeners.json")
)
KEEP_SECS = 30 * 24 * 3600          # the month view is the deepest anyone can look

_samples: list[dict] = []           # [{"t": epoch seconds, "n": listeners}]
_loaded = False


def _listener_count(np: dict) -> int | None:
    # The station's two live shapes, same read the briefing and /test/station
    # use: {"listeners": {"current": N}} or {"context": {"listeners":
    # {"count": N}}}. Anything else is "the station didn't say".
    for probe in (
        lambda: (np.get("listeners") or {}).get("current"),
        lambda: ((np.get("context") or {}).get("listeners") or {}).get("count"),
    ):
        try:
            n = probe()
        except AttributeError:
            n = None
        if isinstance(n, (int, float)):
            return int(n)
    return None


def _load() -> None:
    global _samples, _loaded
    if _loaded:
        return
    _loaded = True
    try:
        d = json.loads(LISTENERS_PATH.read_text(encoding="utf-8"))
        rows = d.get("samples") if isinstance(d, dict) else None
        _samples = [r for r in (rows or [])
                    if isinstance(r, dict) and isinstance(r.get("t"), (int, float))]
    except FileNotFoundError:
        _samples = []
    except Exception as e:
        # A corrupt buffer costs history, not the feature.
        log.warning("listener buffer unreadable (%s) — starting fresh", e)
        _samples = []


def _prune(now: float) -> None:
    cutoff = now - KEEP_SECS
    while _samples and _samples[0]["t"] < cutoff:
        _samples.pop(0)


def _save() -> None:
    try:
        LISTENERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = LISTENERS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"samples": _samples}), encoding="utf-8")
        os.replace(tmp, LISTENERS_PATH)
    except Exception as e:
        log.debug("could not persist listener buffer: %s", e)


def record_sample(n: int | None, now: float | None = None) -> None:
    """Append one reading. None (station didn't say) records nothing —
    the chart shows a gap for an outage, not a flattering zero."""
    _load()
    now = time.time() if now is None else now
    if n is not None:
        _samples.append({"t": int(now), "n": int(n)})
    _prune(now)
    if n is not None:
        _save()


async def sample_listeners(app: web.Application) -> None:
    """cleanup_ctx: poll the station's listener count on a timer.

    5 minutes by default — the DAY view buckets hourly, so a dozen samples
    per bucket is plenty, and a month of them is ~8600 rows of two ints.
    """
    interval = float(os.environ.get("LISTENER_SAMPLE_INTERVAL", "300"))
    # <= 0 disables the sampler outright. The test suite sets 0: the loop
    # polls the station the moment the app starts, and a test that builds the
    # real app must not reach for a network the house rules forbid.
    if interval <= 0:
        yield
        return

    async def loop() -> None:
        while True:
            try:
                station = StationClient(timeout=10.0)
                try:
                    np = await station.now_playing()
                finally:
                    await station.aclose()
                record_sample(_listener_count(np or {}))
            except Exception as e:
                log.debug("listener sample failed: %s", describe(e))
            await asyncio.sleep(interval)

    task = asyncio.create_task(loop())
    app["listener_sampler"] = task
    try:
        yield
    finally:
        task.cancel()


async def handle_stats_listeners(request: web.Request) -> web.Response:
    """The sampled series, for the panel's CONCURRENT LISTENERS chart.

    Admin-gated like /calls and /logs: it is operator telemetry, not part of
    the caller-facing surface.
    """
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))
    _load()
    _prune(time.time())
    return _cors(request, web.json_response({
        "samples": _samples,
        "intervalSecs": float(os.environ.get("LISTENER_SAMPLE_INTERVAL", "300")),
    }))
