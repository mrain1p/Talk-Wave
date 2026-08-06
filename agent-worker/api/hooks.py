"""What the station pushes at us, and keeping it warm enough to answer.

The station can push track.play / dj.say / dj.link / request.received to a URL
instead of us polling for them. Receiving them lets the on-air card refresh the
moment something changes and gives later features a real event source.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx
from aiohttp import web

import settings as settings_store
from api.auth import _write_allowed
from api.env import PORT
from api.live_cache import _LIVE_BUST_FLOOR, _live_cache
from api.wire import _cors
from station import StationClient

log = logging.getLogger("callin.token")


from collections import deque

_hook_events: deque = deque(maxlen=50)
_hook_state: dict = {"registered": False, "url": "", "detail": "not attempted"}


def _lan_ip() -> str:
    """The address the NAS can reach this box on — NOT localhost."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


async def handle_station_hook(request: web.Request) -> web.Response:
    """Receiver for the station's pushes. No auth — the station doesn't sign
    hooks — so treat payloads as untrusted data: store, bust caches, never act
    directly on their contents."""
    import time as _time

    try:
        body = await request.json()
    except Exception:
        body = {}
    event = str(body.get("event") or body.get("type") or "?")[:80]
    # Summarised, not stored whole. This endpoint cannot be authenticated (the
    # station does not sign its hooks), so `body` is arbitrary and unbounded up
    # to aiohttp's 1MB limit — and the deque holds fifty of them, in a process
    # already running near the SDK's own memory warning line. It is only ever
    # read back as a diagnostic list, so a trimmed rendering is all it was
    # worth keeping.
    _hook_events.append({
        "at": _time.time(),
        "event": event,
        "data": {str(k)[:40]: str(v)[:120] for k, v in list(body.items())[:12]}
        if isinstance(body, dict) else {},
    })
    log.info("station webhook: %s", event)

    # Anything that changes what the card shows invalidates the cache — but not
    # more often than the cache would have expired anyway.
    #
    # This endpoint cannot be authenticated (the station doesn't sign hooks),
    # and every bust makes the next /live fan out into four to six station
    # reads. Left ungoverned, anyone who can reach this can make every open
    # widget hammer the station on every poll. The floor means a flood of
    # hooks costs the station no more than the normal 30-second refresh.
    if event.split(".")[0] in ("track", "dj", "request"):
        if _time.time() - _live_cache["at"] >= _LIVE_BUST_FLOOR:
            _live_cache["data"] = None
    return web.json_response({"ok": True})


async def handle_hooks_recent(request: web.Request) -> web.Response:
    # Operator debugging surface — same gate as the rest of the panel.
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))
    return _cors(request, web.json_response(
        {"registered": _hook_state, "events": list(_hook_events)[-15:]}
    ))


async def register_station_webhook() -> None:
    """Register our receiver with the station, once, idempotently. The write
    schema is undocumented, so try the two plausible shapes and verify by
    reading the list back."""
    from station_config import admin_credentials

    user, password = admin_credentials()
    if not (user and password):
        _hook_state["detail"] = "no admin credentials"
        return

    url = os.environ.get("CALLIN_HOOK_URL") or f"http://{_lan_ip()}:{PORT}/hooks/station"
    _hook_state["url"] = url
    events = ["track.play", "dj.say", "dj.link", "request.received"]
    base = settings_store.station_base_url()
    auth = httpx.BasicAuth(user, password)

    try:
        async with httpx.AsyncClient(base_url=base, timeout=10.0, auth=auth) as c:
            current = (await c.get("/webhooks")).json()
            hooks = current.get("webhooks") or []
            if any(h.get("url") == url for h in hooks if isinstance(h, dict)):
                _hook_state.update(registered=True, detail="already registered")
                return

            for shape in (
                {"url": url, "events": events},
                {"webhooks": hooks + [{"url": url, "events": events}]},
            ):
                r = await c.post("/webhooks", json=shape)
                if r.status_code >= 400:
                    continue
                check = (await c.get("/webhooks")).json()
                if any(h.get("url") == url
                       for h in (check.get("webhooks") or []) if isinstance(h, dict)):
                    _hook_state.update(registered=True, detail="registered")
                    log.info("station webhook registered -> %s", url)
                    return

            # A schema rejection is permanent for this station version —
            # retrying every warm tick just rate-limits us against the
            # station. Stand down until a restart or a credentials change.
            _hook_state["gave_up"] = True
            _hook_state["detail"] = "station did not accept either registration shape"
            log.warning("webhook registration failed: %s", _hook_state["detail"])
    except Exception as e:
        _hook_state["detail"] = str(e)[:120]
        log.warning("webhook registration failed: %s", e)
        # Every attempt carries the admin credentials; unbounded retries have
        # been observed tripping a station's LOGIN rate limiter and locking
        # the operator out of their own admin UI. Stand down after a few
        # failures; a restart or a credentials change re-arms it.
        _hook_state["attempts"] = _hook_state.get("attempts", 0) + 1
        if _hook_state["attempts"] >= 5:
            _hook_state["gave_up"] = True
            log.warning(
                "webhook registration standing down after %d failed attempts",
                _hook_state["attempts"],
            )


async def keep_station_warm(app: web.Application) -> None:
    """Poll /dj on a timer so a caller never pays for a cold read.

    The station caches persona state lazily: warm it answers in ~15ms, but the
    first read after a quiet spell was measured at 19.5 seconds. That delay
    would land squarely between pressing Call and the DJ speaking. Touching it
    periodically keeps it hot for whoever calls next.
    """
    interval = float(os.environ.get("STATION_WARM_INTERVAL", "45"))

    async def loop() -> None:
        while True:
            try:
                station = StationClient(timeout=25.0)
                try:
                    t0 = asyncio.get_running_loop().time()
                    dj = await station.live_dj()
                    took = asyncio.get_running_loop().time() - t0
                    # An empty read is a FAILED ping — logging "now warm" for
                    # it made an unreachable station look healthy in the logs.
                    if dj and took > 2:
                        log.info("station /dj was cold (%.1fs) — now warm", took)
                    elif not dj:
                        log.debug("warm ping got no answer from the station")
                finally:
                    await station.aclose()
                # Registration was previously attempted exactly once at
                # startup — a station that was down at that moment, or admin
                # credentials added later, left it unregistered until a
                # restart. Piggyback on the warm tick until it sticks.
                if not _hook_state.get("registered") and not _hook_state.get("gave_up"):
                    await register_station_webhook()
            except Exception as e:
                log.debug("warm ping failed: %s", e)
            await asyncio.sleep(interval)

    task = asyncio.create_task(loop())
    app["warm_task"] = task
    app["hook_task"] = asyncio.create_task(register_station_webhook())
    try:
        yield
    finally:
        task.cancel()
