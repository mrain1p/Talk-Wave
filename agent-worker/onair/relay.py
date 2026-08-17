"""A live call, on the station's air, one finished turn at a time.

The station has no live input — everything that airs is a file the mixer
fetches (see onair/transport.py) — so a "live" phone-in here is a relay:
each completed utterance, the caller's and the DJ's, becomes a short clip
pushed to the voice queue in conversation order. The queue is FIFO and the
duck holds across back-to-back items, so what listeners hear is the
conversation, tightened (the model's thinking gaps don't air), running about
one exchange behind the room — on a stream every listener already hears 22
seconds late.

The lag is not an apology, it is the dump button. Real phone-in radio runs
on a deliberate delay for exactly this reason: a turn that has not been
pushed yet can still be killed. The relay holds exactly one finished clip
back (push turn k when turn k+1 completes), which both keeps the mixer's
queue fed — the duck never releases mid-conversation because the next clip
is already behind the current one — and keeps one turn of take-back in hand
at all times.

The relay re-reads settings before every push. Settings are re-read at the
start of every CALL by invariant; a live broadcast deserves tighter: the
operator flipping the feature off mid-call must stop the next clip, not the
next caller.

What this module does NOT do: capture audio (call/tee.py feeds it finished
WAVs), decide that a call is on air (the session arms it), or reach the
station's disk (the mixer fetches from us; nothing is written to theirs).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import settings as settings_store
from onair import chunks, transport

log = logging.getLogger("callin.onair")

# The say.txt poll interval, out-waited between a /dj/say 200 and the first
# telnet push — the studio measured a push landing inside that window airing
# AHEAD of the intro (2026-08-17). Same constant, same reason, same value as
# voicemail/air.py's inline sleep.
SAY_POLL_SECS = 0.8

# Two pushes failing back to back means the transport is gone, not unlucky —
# keep the call alive and take it off air out loud rather than airing a
# conversation with holes in it.
MAX_CONSECUTIVE_FAILURES = 2


class CallRelay:
    """One call's on-air feed. Built armed-but-idle; open() starts the
    broadcast, feed() takes finished clips, close() ends it. Every method is
    safe to call after close — a late TTS flush must never crash a hangup."""

    def __init__(self, station, cfg: dict, room: str, tier: str = "",
                 record=None) -> None:
        self.station = station
        self.cfg = cfg
        self.room = room
        self.tier = tier
        self.record = record
        self.active = False
        self.dumped = False
        self._seq = 0
        self._held: dict | None = None
        self._failures = 0
        self._deadline = 0.0
        self._lock = asyncio.Lock()
        self.pushed = 0

    # -- lifecycle ---------------------------------------------------------
    async def open(self) -> bool:
        """Preflight the transport, put the intro on air, start the window.

        False means the call goes ahead OFF air — the caller pressed the
        button in good faith, and a dead mixer is our problem, not theirs.
        The record says why, out loud, because a silently-absent broadcast
        is the studio's "week not noticing the network stanza" all over.
        """
        base = transport.air_base_url(self.cfg)
        reachable = base and await asyncio.to_thread(
            transport.mixer_reachable, self.cfg)
        if not reachable:
            why = ("on-air call fell back to a private call: "
                   + ("no air base URL" if not base else "no reachable mixer"))
            log.warning("%s (room=%s)", why, self.room)
            self._problem(why)
            return False

        ok = await self._say(
            "A listener is coming on the air, live, right now. In one short "
            "sentence, tell the audience a caller is on the line and hand "
            "over — do not invent their name or what they want.")
        if ok:
            # The intro's /dj/say 200 means WRITTEN to the mixer's handoff
            # file, polled every 0.5s; a push lands in voice_queue instantly.
            # Out-wait the poll so the first clip cannot beat the intro.
            await asyncio.sleep(SAY_POLL_SECS)
        else:
            self._problem("the on-air intro failed; the call aired without one")
        window = float(self.cfg.get("on_air_max_seconds") or 0) or 240.0
        self._deadline = time.time() + window
        self.active = True
        self._tool(f"on air: window open ({window:.0f}s max)")
        return True

    async def feed(self, wav: Path, kind: str, seconds: float) -> None:
        """One finished clip, in conversation order. kind is 'caller' or
        'dj' — the record's vocabulary, because the record is where these
        lines end up."""
        async with self._lock:
            if not self.active:
                wav.unlink(missing_ok=True)
                return
            if time.time() > self._deadline:
                wav.unlink(missing_ok=True)
                await self._close_locked(
                    "the on-air window closed", say_outro=True)
                return
            self._seq += 1
            chunk = {"wav": wav, "kind": kind, "seconds": seconds,
                     "seq": self._seq}
            held, self._held = self._held, chunk
            if held:
                await self._push(held)

    async def close(self, reason: str, *, say_outro: bool = True) -> None:
        async with self._lock:
            await self._close_locked(reason, say_outro=say_outro)

    async def dump(self) -> None:
        """The operator's kill: the unpushed tail dies, the broadcast ends
        now. The call itself continues — the caller may not even know."""
        async with self._lock:
            self.dumped = True
            if self._held:
                self._held["wav"].unlink(missing_ok=True)
                self._held = None
            await self._close_locked("dumped by the operator",
                                     say_outro=True)

    # -- internals ---------------------------------------------------------
    async def _close_locked(self, reason: str, *, say_outro: bool) -> None:
        if not self.active:
            return
        self.active = False
        if self._held and not self.dumped:
            await self._push(self._held)
        self._held = None
        self._tool(f"off air: {reason} ({self.pushed} clips aired)")
        if say_outro:
            await self._say(
                "The live caller segment on air just ended. In one short "
                "sentence, thank the caller and carry the show on — do not "
                "summarise the conversation.")
        chunks.sweep()

    async def _push(self, chunk: dict) -> None:
        """One clip to the voice queue — after asking settings whether this
        broadcast is still allowed to exist."""
        cfg = settings_store.permissions_for(settings_store.load(), self.tier)
        if not cfg.get("allow_on_air"):
            chunk["wav"].unlink(missing_ok=True)
            await self._close_locked(
                "the operator switched on-air calls off", say_outro=False)
            return
        token = await asyncio.to_thread(chunks.adopt, chunk["wav"])
        if token:
            base = transport.air_base_url(self.cfg)
            rid = await asyncio.to_thread(
                transport.telnet_push, self.cfg, f"{base}/on-air/{token}")
        else:
            rid = None
        if rid is None:
            if token:
                chunks.discard(token)
            self._failures += 1
            self._problem(
                f"an on-air clip failed to push (turn {chunk['seq']}, "
                f"{chunk['kind']})")
            if self._failures >= MAX_CONSECUTIVE_FAILURES:
                await self._close_locked(
                    "the mixer stopped answering", say_outro=False)
            return
        self._failures = 0
        self.pushed += 1
        self._tool(
            f"aired turn {chunk['seq']} ({chunk['kind']}, "
            f"{chunk['seconds']:.1f}s, RID {rid})")

    async def _say(self, instruction: str) -> bool:
        try:
            result = await self.station.dj_say(instruction, mode="styled",
                                               kind="callin")
            return bool(result.get("ok"))
        except Exception as e:                                  # noqa: BLE001
            log.warning("on-air dj line failed: %s", e)
            return False

    def _tool(self, line: str) -> None:
        log.info("%s (room=%s)", line, self.room)
        if self.record:
            try:
                self.record.tool("on_air_relay", line)
            except Exception:                                   # noqa: BLE001
                pass

    def _problem(self, line: str) -> None:
        log.warning("%s (room=%s)", line, self.room)
        if self.record:
            try:
                self.record.problem(line)
            except Exception:                                   # noqa: BLE001
                pass
