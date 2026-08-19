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

TAPE MODE (on_air_call_mode = "after", 0.98.5) trades the lag-by-one for a
reel: nothing airs during the call, and the whole conversation plays at
hangup — intro, the exchange in order, outro. The dump promise inverts with
it: live mode can kill the one turn still inside its delay window; tape mode
kills the ENTIRE call at any moment before playout, which is the reason an
operator would accept the wait.

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

# How long the lag-by-one may HOLD a finished turn before pushing it anyway.
# The hold exists for the dump, but its length was whatever the next turn
# happened to take — endpointing, the model thinking, then the DJ's whole
# answer playing out in the room — so a caller's turn sat unaired for 10-25
# seconds and the broadcast filled the gap with music swells (~24s, measured
# on the first live tests). The take-back window that bought was accidental:
# sometimes half a minute, guaranteed nothing. This cap trades the accident
# for a promise — every finished turn stays killable for THIS long, and the
# air runs about this far behind the room instead of a full turn. Ordering
# is untouched: the timer only ever pushes the clip already at the head.
MAX_HELD_SECS = 6.0


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
        # Armed is not live: active means the relay will take clips, _live
        # means the intro has aired and the window clock is running.
        self._live = False
        self.dumped = False
        self._seq = 0
        self._held: dict | None = None
        # The operator's dial since 0.97.79 ("On-air delay"), read per call
        # like every relay setting; MAX_HELD_SECS is the default and the
        # fallback for an unreadable value. Clamped to the range the panel's
        # help promises. Note what the `or` does to 0: it falls to the
        # 6-second default exactly like blank — it does NOT reach the
        # 2-second floor. Either way a literal 0 cannot mean "no hold": the
        # pull must always have SOME window, and a second off-switch for a
        # safety control is the trap quiet_secs already fell into once. The
        # floor guards typed-in small values (1 becomes 2). An attribute
        # rather than the constant read directly, so a test about the timer
        # can compress six real seconds the way the air guard's tests
        # compress theirs.
        try:
            self.max_held_secs = min(30.0, max(
                2.0, float(cfg.get("on_air_delay_secs") or MAX_HELD_SECS)))
        except (TypeError, ValueError):
            self.max_held_secs = MAX_HELD_SECS
        # TAPE MODE (on_air_call_mode = "after", 0.98.5): nothing airs during
        # the call. Finished clips collect on the reel and the whole
        # conversation plays at close — intro, the exchange in order, outro.
        # What the operator buys with the wait: PULL OFF AIR at any moment of
        # the call kills the entire tape before a word of it airs, where live
        # mode can only ever kill the turn still inside its delay window.
        self.tape = str(cfg.get("on_air_call_mode") or "live") == "after"
        self._reel: list[dict] = []
        self._reel_secs = 0.0
        self._hold_timer: asyncio.Task | None = None
        self._failures = 0
        self._deadline = 0.0
        self._lock = asyncio.Lock()
        self.pushed = 0

    # -- lifecycle ---------------------------------------------------------
    async def open(self) -> bool:
        """Preflight the transport and ARM — nothing airs yet.

        False means the call goes ahead OFF air — the caller pressed the
        button in good faith, and a dead mixer is our problem, not theirs.
        The record says why, out loud, because a silently-absent broadcast
        is the studio's "week not noticing the network stanza" all over.

        The intro waits for the first clip (_go_live). The first deployed
        test aired the brackets around a call whose media never arrived:
        listeners got "a caller is coming on the air…", a minute of nothing,
        then a thank-you to nobody. A broadcast that never has a first clip
        now never says a word.
        """
        reachable, why = await asyncio.to_thread(
            transport.probe_and_record, self.cfg)
        if not reachable:
            why = "on-air call fell back to a private call: " + why
            log.warning("%s (room=%s)", why, self.room)
            self._problem(why)
            return False
        # Spend any marker a previous call left, so an old dump cannot
        # behead this caller's first turn.
        await asyncio.to_thread(chunks.take_dump)
        self.active = True
        self._live = False
        # The sound style rides the armed line because nothing else records
        # it: the clip that would prove it is deleted the moment it airs, so
        # a soak reading the record needs the claim written down here.
        sound = str(self.cfg.get("on_air_caller_sound") or "clean")
        self._tool(("on air: armed — taping; the broadcast plays when the "
                    "call ends" if self.tape else
                    "on air: armed — the broadcast opens at the first clip")
                   + f" (caller sound: {sound})")
        return True

    async def _go_live(self) -> None:
        """The intro, and the window clock — at the FIRST clip, holding the
        feed lock. The clip that triggered this is then HELD by lag-by-one,
        so the intro precedes any caller audio by at least one full turn."""
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
        self._live = True
        self._tool(f"on air: window open ({window:.0f}s max)")

    async def feed(self, wav: Path, kind: str, seconds: float) -> None:
        """One finished clip, in conversation order. kind is 'caller' or
        'dj' — the record's vocabulary, because the record is where these
        lines end up."""
        async with self._lock:
            if not self.active:
                wav.unlink(missing_ok=True)
                return
            # The panel's dump, read here because this is the moment before
            # anything else can air: the turn in hand and the one arriving
            # both die, the segment signs off, the call itself carries on.
            if await asyncio.to_thread(chunks.take_dump):
                self.dumped = True
                wav.unlink(missing_ok=True)
                if self._held:
                    self._held["wav"].unlink(missing_ok=True)
                    self._held = None
                # say_outro is a request, not a promise: with zero clips
                # aired (a taped call dumped whole) _close_locked stays
                # silent — an outro to nobody is the first deployed test's
                # lesson, and a dumped tape aired NOTHING.
                await self._close_locked("dumped by the operator",
                                         say_outro=True)
                return
            if self.tape:
                # The reel: no intro yet, no window clock, no hold timer —
                # nothing is on air. The window caps the REEL instead, so a
                # marathon call cannot tape an hour of broadcast; what falls
                # off the end is reported the same way every unaired turn is.
                window = float(self.cfg.get("on_air_max_seconds") or 0) or 240.0
                if self._reel_secs + seconds > window:
                    wav.unlink(missing_ok=True)
                    self.dropped(kind, "the tape is full "
                                       f"({self._reel_secs:.0f}s of "
                                       f"{window:.0f}s allowed)")
                    return
                self._seq += 1
                self._reel.append({"wav": wav, "kind": kind,
                                   "seconds": seconds, "seq": self._seq,
                                   "heldAt": time.time()})
                self._reel_secs += seconds
                return
            if not self._live:
                await self._go_live()
            if time.time() > self._deadline:
                wav.unlink(missing_ok=True)
                await self._close_locked(
                    "the on-air window closed", say_outro=True)
                return
            self._seq += 1
            chunk = {"wav": wav, "kind": kind, "seconds": seconds,
                     "seq": self._seq, "heldAt": time.time()}
            held, self._held = self._held, chunk
            if held:
                await self._push(held)
            self._arm_hold_timer(chunk)

    def _arm_hold_timer(self, chunk: dict) -> None:
        """Bound the hold — see MAX_HELD_SECS. One timer, re-armed per clip;
        the previous one is cancelled rather than left to wake up and find
        its clip already pushed by a successor's arrival."""
        if self._hold_timer is not None:
            self._hold_timer.cancel()

        async def _expire() -> None:
            try:
                await asyncio.sleep(self.max_held_secs)
            except asyncio.CancelledError:
                return
            async with self._lock:
                # Only the clip this timer was armed FOR, and only if it is
                # still the one in hand — a successor arriving between the
                # sleep and the lock has already pushed it the ordinary way.
                if not self.active or self._held is not chunk:
                    return
                # The dump outranks the timer, exactly as it outranks feed():
                # a marker pressed during the hold kills the held turn — that
                # is the window the cap exists to guarantee.
                if await asyncio.to_thread(chunks.take_dump):
                    self.dumped = True
                    self._held["wav"].unlink(missing_ok=True)
                    self._held = None
                    await self._close_locked("dumped by the operator",
                                             say_outro=True)
                    return
                if time.time() > self._deadline:
                    self._held["wav"].unlink(missing_ok=True)
                    self._held = None
                    await self._close_locked(
                        "the on-air window closed", say_outro=True)
                    return
                held, self._held = self._held, None
                await self._push(held)

        self._hold_timer = asyncio.create_task(_expire())

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
        if self._hold_timer is not None:
            self._hold_timer.cancel()
            self._hold_timer = None
        if self._held and not self.dumped:
            await self._push(self._held)
        self._held = None
        if self.tape and self._reel and not self.dumped:
            await self._play_reel()
        # Whatever is still on the reel — a dump, a mixer that stopped
        # answering mid-playout — is a caller's voice in /tmp. It goes now.
        for chunk in self._reel:
            chunk["wav"].unlink(missing_ok=True)
        self._reel = []
        self._tool(f"off air: {reason} ({self.pushed} clips aired)")
        # The outro only follows a broadcast that happened: with zero clips
        # aired there is nobody on the stream to thank, and the first
        # deployed test proved a thank-you to nobody is worse than silence.
        # A FAILED outro is recorded like the failed intro is — the first
        # tape soak lost both brackets to the same closed client and only
        # the intro left a trace (callin-ol-cd4e089a2eb0, 2026-08-19).
        if say_outro and self.pushed > 0:
            ok = await self._say(
                "The live caller segment on air just ended. In one short "
                "sentence, thank the caller and carry the show on — do not "
                "summarise the conversation.")
            if not ok:
                self._problem("the on-air outro failed; the broadcast ended "
                              "without a sign-off")
        chunks.sweep()

    async def _play_reel(self) -> None:
        """Tape mode's playout: the whole conversation, at hangup.

        Runs inside _close_locked with the lock held — this IS the close, not
        a broadcast the close has to wait for. The pushes queue the clips
        back-to-back in the mixer's FIFO (the duck holds across them, same as
        the live chain), so the playout itself takes seconds even when the
        airing takes minutes. The dump marker is consulted between pushes:
        the un-pushed remainder of a tape is still killable, though once a
        clip is in the mixer's queue it is the mixer's.
        """
        ok = await self._say(
            "A call with a listener just wrapped, and the recording is about "
            "to play on the air. In one short sentence, set it up — a caller "
            "was on the line and here's how it went — without inventing "
            "their name or what they wanted.")
        if ok:
            await asyncio.sleep(SAY_POLL_SECS)
        else:
            self._problem("the tape's intro failed; the conversation aired "
                          "without one")
        reel, self._reel = self._reel, []
        for i, chunk in enumerate(reel):
            if await asyncio.to_thread(chunks.take_dump):
                self.dumped = True
                for rest in reel[i:]:
                    rest["wav"].unlink(missing_ok=True)
                self._tool("dumped mid-playout — the rest of the tape dies")
                return
            if self._failures >= MAX_CONSECUTIVE_FAILURES:
                for rest in reel[i:]:
                    rest["wav"].unlink(missing_ok=True)
                return
            # The held time would read as the whole call's length here and
            # look like a broken timer; the playout's own pacing is the
            # honest number for the record's aired-turn lines.
            chunk["heldAt"] = time.time()
            await self._push(chunk)

    async def _push(self, chunk: dict) -> None:
        """One clip to the voice queue — after asking settings whether this
        broadcast is still allowed to exist."""
        cfg = settings_store.permissions_for(settings_store.load(), self.tier)
        # The dashboard's Live Call quick kill counts the same as the master
        # tier row here. It always closed the door to the NEXT caller (the
        # mint refuses the route); until 0.97.64 it did not stop a broadcast
        # already running, while the master row did — two switches that both
        # read "close the door", only one of which closed it.
        if (not cfg.get("allow_on_air")
                or not cfg.get("on_air_calls_enabled", True)):
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
            # A failed adopt never moved the clip, so the source file is
            # still sitting wherever the tee wrote it — clean it here or a
            # full store leaks a caller's voice into the container's /tmp.
            chunk["wav"].unlink(missing_ok=True)
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
        # The held time is the instrument the next latency question reads —
        # the same reason the pickup grew its leg stamps. "held 6.0s" on every
        # line means the timer is doing the releasing; "held 14s" means it is
        # broken; small varying numbers mean the conversation is flowing.
        held_for = time.time() - float(chunk.get("heldAt") or time.time())
        self._tool(
            f"aired turn {chunk['seq']} ({chunk['kind']}, "
            f"{chunk['seconds']:.1f}s, RID {rid}, held {held_for:.1f}s)")

    async def _say(self, instruction: str) -> bool:
        try:
            result = await self.station.dj_say(instruction, mode="styled",
                                               kind="callin")
            return bool(result.get("ok"))
        except Exception as e:                                  # noqa: BLE001
            log.warning("on-air dj line failed: %s", e)
            return False

    def seconds_left(self) -> float:
        """How much of the on-air window remains, or 0 when it is not running.

        The clock starts at the FIRST CLIP, not at pickup — a segment that
        never got a caller's voice into it has no window to be near the end
        of. Read by call/clocks.py, which is what actually cues the wrap: the
        relay knows the time and the session owns the DJ's mouth, and keeping
        those apart is why this returns a number rather than saying anything.
        """
        if not (self.active and self._live and self._deadline):
            return 0.0
        return max(0.0, self._deadline - time.time())

    def dropped(self, kind: str, why: str) -> None:
        """A turn that never reached the air, and which of the seven ways it
        went.

        A clip can fail to air seven ways — too short, nothing audible once
        mastered, interrupted before enough of it played, dumped by the
        operator, the window closing, the push failing, the master erroring —
        and three of them used to be a bare `return` in call/tee.py. So the
        one question a live segment has to be able to answer afterwards ("why
        did the audience not hear that bit") had no answer for three of its
        seven possible causes, and the record showed a conversation with a
        hole in it and no explanation.

        Called by the tee as well as from here, which is why it is public.
        """
        self._tool(f"not aired ({kind}): {why}")

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
