"""Quiet the station's own DJ while a phone-in is live.

The station's auto-talk — idents, hourly time checks, between-track links,
segments, banter — will happily fire over a live call. Ducking our own clips
around it (`avoid_on_air_overlap`) only manages the collision; this removes
it: while a call is up, flip the station's own Voice switch
(`settings.tts.enabled`, SUB/WAVE #1180, in every release since v0.48.0) off,
and put it back after. Upstream built exactly the semantics a temporary flip
wants: every autonomous talk site checks the switch *before generating* (no
wasted tokens), music and requests keep working (a request just skips its
spoken intro), a pending programme intro stays pending and airs after the
switch returns, and manual pushes stay exempt — which includes every clip we
air, because ours go to Liquidsoap's voice_queue over telnet and never pass
the controller at all. Proven live 2026-08-19: POST /settings with the admin
credentials merges the one field, applies on the next tick, no restart.

This is the first Talk Wave feature that WRITES a station setting, so the
sharp edge is not the write — it is making sure the station can never be left
mute. The shape:

  * Each call claims a marker file (HUSH-CALL-<room>) and heartbeats its
    mtime; the flip itself is recorded in HUSH with the prior value.
  * The worker only ever asserts. It never restores — restore belongs to ONE
    process, the token server's janitor loop, which puts the switch back when
    no call marker is fresh. One restorer means no restore races between
    per-call job processes.
  * Crash-safety is the marker going stale: a worker that dies stops
    heartbeating, and within CALL_FRESH_SECS the janitor restores anyway. A
    whole-stack restart mid-call is the same story on the janitor's first
    tick. No path depends on a clean shutdown.
  * The operator outranks us both ways: a station whose Voice is already off
    is left alone entirely, and if they flip it back on mid-call the restore
    sees a switch that is not ours any more and stands down without writing.

Markers live in chunks.SERVE_DIR — the same store DUMP and the mixer verdict
already use to cross the web/worker process seam. Read at call time, not
import time, so tests that swap chunks.SERVE_DIR isolate this module too.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time

import httpx

log = logging.getLogger("callin.onair")

# How long a call marker stays trusted without a heartbeat. Heartbeats land
# every HEARTBEAT_SECS from the worker, so this is ~10 missed beats — a dead
# job, not a slow one. Deliberately NOT derived from max_call_seconds: 0 there
# means "no hangup limit", and a formula that reads 0 as short would have the
# janitor un-quiet the station in the middle of an unlimited call. The tape
# playout runs after the heartbeat task is cancelled, but a reel is capped by
# the on-air window (240s default) — well inside this margin.
CALL_FRESH_SECS = 600.0
HEARTBEAT_SECS = 60.0

# Matches the station-client timeout family (StationConfig, 2026-08-10): these
# sit on the call-setup path and must fail fast, not hold the ringing.
_TIMEOUT = 4.5

_ROOM_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def _dir():
    """chunks.SERVE_DIR, read late — tests swap that attribute per-case."""
    from onair import chunks

    chunks._ensure_dir()
    return chunks.SERVE_DIR


def _call_marker(room: str):
    return _dir() / f"HUSH-CALL-{_ROOM_SAFE.sub('_', room or 'unknown')[:64]}"


def scope(cfg: dict) -> str:
    """The setting, normalised: 'off' | 'on_air' | 'all'."""
    raw = str(cfg.get("quiet_station_on_calls") or "off").strip().lower()
    return raw if raw in ("on_air", "all") else "off"


# ── the station's switch, spoken to directly ────────────────────────────────
# Deliberately its own thin client rather than a StationConfig method: this
# module is the only writer of station settings in the whole repo, and keeping
# the write here means an auditor greps ONE file. The read shape is the
# admin GET /settings: the stored document rides under `values` (the top-level
# `tts` key is a voice CATALOGUE — learned by probing the wrong one), and the
# POST echoes the saved document back, which is the verify.


def _client() -> httpx.AsyncClient | None:
    import settings as settings_store
    from station_config import admin_credentials

    user, password = admin_credentials()
    if not (user and password):
        return None
    return httpx.AsyncClient(base_url=settings_store.station_base_url(),
                             timeout=_TIMEOUT,
                             auth=httpx.BasicAuth(user, password))


async def _read_enabled(client: httpx.AsyncClient) -> bool | None:
    """The stored tts.enabled, or None when the station doesn't publish one
    (older than #1180, or unreadable) — None is 'do not touch'."""
    r = await client.get("/settings")
    r.raise_for_status()
    values = (r.json() or {}).get("values") or {}
    enabled = (values.get("tts") or {}).get("enabled")
    return enabled if isinstance(enabled, bool) else None


async def _write_enabled(client: httpx.AsyncClient, on: bool) -> bool:
    """POST the one-field merge; True only when the station's own echo of the
    saved document confirms the value landed."""
    r = await client.post("/settings", json={"tts": {"enabled": on}})
    if r.status_code != 200:
        log.warning("station voice switch write answered %s: %s",
                    r.status_code, r.text[:200])
        return False
    saved = ((r.json() or {}).get("saved") or {}).get("tts") or {}
    return saved.get("enabled") is on


# ── verdict, for the panel ───────────────────────────────────────────────────


def _note(ok: bool, why: str = "") -> None:
    try:
        (_dir() / "HUSH-VERDICT").write_text(
            json.dumps({"ok": ok, "why": why, "at": time.time()}))
    except OSError as e:
        log.debug("could not write the hush verdict (harmless): %s", e)


def live_verdict(cfg: dict) -> dict | None:
    """What /live tells the panel, or None when the feature is off. Disk and
    credential presence only — this rides a public, cached payload and must
    never cost a station read."""
    which = scope(cfg)
    if which == "off":
        return None
    from station_config import has_admin

    out = {"scope": which, "creds": has_admin(),
           "quieted": (_dir() / "HUSH").exists(), "ok": True, "why": ""}
    try:
        v = json.loads((_dir() / "HUSH-VERDICT").read_text())
        out["ok"] = bool(v.get("ok", True))
        out["why"] = str(v.get("why") or "")
    except (OSError, ValueError):
        pass
    if not out["creds"]:
        out["ok"] = False
        out["why"] = "no station admin credentials"
    return out


# ── the worker's half: assert, heartbeat, hand back ─────────────────────────


async def engage(cfg: dict, room: str) -> None:
    """Quiet the station for this call. Best-effort and quick — one read, one
    write — because it rides the ringing; a station that answers slowly or
    not at all costs the caller nothing and the janitor re-asserts while the
    call marker is fresh. Never raises."""
    try:
        marker = _call_marker(room)
        marker.touch()
        client = _client()
        if client is None:
            _note(False, "no station admin credentials")
            return
        try:
            hush = _dir() / "HUSH"
            if hush.exists():
                # A sibling call already flipped the switch; this call only
                # needs its marker (above) to keep the janitor's hand off.
                return
            prior = await _read_enabled(client)
            if prior is None:
                _note(False, "the station doesn't publish its Voice switch — "
                             "needs SUB/WAVE v0.48+ (July 2026)")
                return
            if prior is False:
                # The operator's own voice-off. Nothing to do, nothing to
                # restore — and no HUSH, so the janitor never writes either.
                _note(True, "station voice was already off")
                return
            # Claim before writing, so the janitor never finds a flipped
            # switch with no note saying whose it is. O_EXCL: the first call
            # in records the claim; a concurrent sibling loses the race and
            # returns above on its next look — or simply double-writes the
            # same idempotent field.
            try:
                fd = os.open(hush, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError:
                return
            verified = await _write_enabled(client, False)
            with os.fdopen(fd, "w") as f:
                json.dump({"at": time.time(), "by": room,
                           "prior": True, "verified": verified}, f)
            _note(bool(verified),
                  "" if verified else "the voice-off write did not stick")
            log.info("station voice quieted for %s (verified=%s)",
                     room, verified)
        finally:
            await client.aclose()
    except Exception as e:                                     # noqa: BLE001
        _note(False, f"could not reach the station: {e}")
        log.warning("quieting the station failed (call continues): %s", e)


async def heartbeat(room: str) -> None:
    """Keep this call's marker fresh. Cancelled at shutdown like every other
    per-call task; a crash simply stops the beat, which IS the signal."""
    marker = _call_marker(room)
    while True:
        await asyncio.sleep(HEARTBEAT_SECS)
        with contextlib.suppress(OSError):
            marker.touch()


def call_ended(room: str) -> None:
    """Drop this call's marker. Runs at the tail of _on_shutdown — after the
    tape playout, so the janitor cannot un-quiet the station mid-reel."""
    with contextlib.suppress(OSError):
        _call_marker(room).unlink()


# ── the web process's half: the one restorer ────────────────────────────────


def _fresh_calls() -> int:
    """Fresh call markers, consuming stale ones — a marker nobody heartbeats
    is a dead job, and spent markers must not hold the switch forever."""
    n = 0
    now = time.time()
    for path in _dir().glob("HUSH-CALL-*"):
        try:
            if now - path.stat().st_mtime < CALL_FRESH_SECS:
                n += 1
            else:
                path.unlink()
        except OSError:
            continue
    return n


async def janitor_tick(cfg: dict) -> None:
    """One reconcile pass. Called on a timer in the token server, and safe to
    call any time: it only acts when HUSH exists. Never raises."""
    try:
        hush = _dir() / "HUSH"
        if not hush.exists():
            return
        try:
            state = json.loads(hush.read_text())
        except (OSError, ValueError):
            state = {"verified": True}
        client = _client()
        if client is None:
            return                       # creds withdrawn mid-flight; wait.
        try:
            if _fresh_calls():
                # Calls still live. The only work left is finishing an assert
                # the worker could not confirm (station was mid-restart, say).
                if not state.get("verified"):
                    if await _write_enabled(client, False):
                        state["verified"] = True
                        hush.write_text(json.dumps(state))
                        _note(True)
                return
            # No live calls: put the switch back — unless the operator beat
            # us to it. A read that shows anything but False (their flip, or
            # a station downgrade) means the switch is not ours to write.
            enabled = await _read_enabled(client)
            if enabled is False:
                if not await _write_enabled(client, True):
                    return               # station unreachable; retry next tick
            hush.unlink(missing_ok=True)
            _note(True)
            log.info("station voice restored (%s)",
                     "was ours" if enabled is False else "operator already had it")
        finally:
            await client.aclose()
    except Exception as e:                                     # noqa: BLE001
        log.warning("hush janitor tick failed (will retry): %s", e)
