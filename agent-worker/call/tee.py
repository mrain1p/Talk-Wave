"""Hearing the call the way the room does, so the relay can air it.

The worker already touches both halves of the conversation — the caller's
frames flow through the session input on their way to the STT, and the DJ's
frames are our own TTS flowing out — but nothing kept them. This module is
the keeping: two thin wrappers on the session's audio chain (the SDK's own
`next_in_chain` / `source` composition, the same shape its processors use),
each cutting the stream into per-utterance clips and handing finished WAVs
to the relay. Nothing here decides anything about the air; that is
onair/relay.py's job.

Boundaries, because they are the whole trick:

- The CALLER's utterances are cut by the session's own turn-taking
  (`user_state_changed`): speaking opens a clip, listening closes it. A
  rolling pre-buffer catches the onset the VAD needed a moment to notice —
  without it the first take of every clip arrives saying "-ey" instead of
  "hey", the same clipped consonant the studio's trim margin exists for.
- The DJ's utterances are cut by the output's own segment lifecycle:
  `flush()` marks a synthesis complete, and the playback event that follows
  says how much of it the caller actually heard. A line interrupted before
  60% played does not air — airing a sentence the caller talked over would
  broadcast a conversation that never happened. Coarse on purpose; sample
  surgery on the tail of an interrupted line is not worth its bugs until a
  real call proves it is.

The caller's clips get the studio's mastering chain — clean by default since
the operator heard the phone-band costume aired and vetoed it ("never heard my
voice sound so bad on a phone call", 2026-08-18); the costume survives as the
on_air_caller_sound setting. The DJ's clips air as synthesized — the DJ is the
studio side of the conversation and should sound like it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import struct
import tempfile
import wave
from collections import deque
from pathlib import Path

from livekit import rtc
from livekit.agents.voice import io

from voicemail import master

log = logging.getLogger("callin.onair")

# A blip shorter than this is a cough or a keyclick, not a turn.
MIN_CLIP_SECS = 0.35
# One clip's ceiling — a runaway monologue must not build an unbounded file.
MAX_CLIP_SECS = 60.0
# How much audio from BEFORE the VAD noticed speech rides into the clip.
PREROLL_SECS = 0.5
# An interrupted DJ line that played at least this fraction airs; less dies.
PLAYED_ENOUGH = 0.6


def _scratch_wav(prefix: str) -> Path:
    """A temp path whose descriptor is CLOSED.

    `mkstemp` hands back an open fd and only the path was ever taken, so the
    handle leaked for the life of the worker — and on Windows an open handle
    makes the `unlink` in the finally below raise PermissionError, losing the
    clip. Harmless on the deployed Linux container, which is why it survived
    unnoticed; run-local.ps1 is where it bites.
    """
    fd, name = tempfile.mkstemp(suffix=".wav", prefix=prefix)
    os.close(fd)
    return Path(name)


def _write_wav(frames: list[rtc.AudioFrame], path: Path,
               max_secs: float = MAX_CLIP_SECS) -> float:
    """Mono 16-bit WAV at the frames' own rate; returns seconds written.
    Stereo is averaged down — the mixer wants one voice, not a field.

    The frames arrive as 16-bit PCM and leave as 16-bit PCM, so for the mono
    case — which is every call — there is nothing to convert and the bytes are
    concatenated as they are. Unpacking each frame into a tuple of Python ints
    and packing the whole clip back with `*out` cost 9.8ms and 18MB of garbage
    on a 30-second turn (measured in the deployed worker), on BOTH sides of
    every on-air turn, to arrive at bytes it had already been handed. Only
    stereo, which really does have to be averaged, still goes through struct.
    """
    if not frames:
        return 0.0
    rate = frames[0].sample_rate
    limit = int(rate * max_secs)
    chunks: list[bytes] = []
    total = 0
    for f in frames:
        raw = bytes(f.data)
        ch = max(1, f.num_channels)
        if ch > 1:
            data = struct.unpack(f"<{len(raw) // 2}h", raw)
            mono = [sum(data[i:i + ch]) // ch
                    for i in range(0, len(data) - ch + 1, ch)]
            raw = struct.pack(f"<{len(mono)}h", *mono)
        samples = len(raw) // 2
        # Trimmed on the byte boundary rather than after the fact, so a clip
        # that reaches the ceiling stops at exactly the same sample the
        # list-slice used to stop at.
        if total + samples >= limit:
            chunks.append(raw[:(limit - total) * 2])
            total = limit
            break
        chunks.append(raw)
        total += samples
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(chunks))
    return total / float(rate)


class CallerTap(io.AudioInput):
    """Sits between the room and the STT, keeping what flows through."""

    def __init__(self, source: io.AudioInput) -> None:
        super().__init__(label="OnAirCallerTap", source=source)
        self._collecting = False
        self._clip: list[rtc.AudioFrame] = []
        self._preroll: deque[rtc.AudioFrame] = deque()
        self._preroll_samples = 0

    async def __anext__(self) -> rtc.AudioFrame:
        frame = await super().__anext__()
        if self._collecting:
            if sum(f.samples_per_channel for f in self._clip) \
                    < frame.sample_rate * MAX_CLIP_SECS:
                self._clip.append(frame)
        else:
            self._preroll.append(frame)
            self._preroll_samples += frame.samples_per_channel
            while self._preroll_samples > frame.sample_rate * PREROLL_SECS:
                gone = self._preroll.popleft()
                self._preroll_samples -= gone.samples_per_channel
        return frame

    def start_clip(self) -> None:
        self._clip = list(self._preroll)
        self._preroll.clear()
        self._preroll_samples = 0
        self._collecting = True

    def end_clip(self) -> list[rtc.AudioFrame]:
        self._collecting = False
        clip, self._clip = self._clip, []
        return clip


class DJTee(io.AudioOutput):
    """Sits between the session and the room, keeping what it forwards."""

    def __init__(self, sink: io.AudioOutput) -> None:
        super().__init__(
            label="OnAirDJTee",
            capabilities=getattr(sink, "capabilities",
                                 io.AudioOutputCapabilities(pause=False)),
            next_in_chain=sink,
        )
        self._current: list[rtc.AudioFrame] = []
        # Synthesised segments waiting for their playback verdict, oldest
        # first — the playback events arrive in the same order the flushes
        # did, which is what lets a plain FIFO pair them.
        self.pending: deque[list[rtc.AudioFrame]] = deque()

    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        await super().capture_frame(frame)
        if sum(f.samples_per_channel for f in self._current) \
                < frame.sample_rate * MAX_CLIP_SECS:
            self._current.append(frame)
        if self.next_in_chain:
            await self.next_in_chain.capture_frame(frame)

    def flush(self) -> None:
        super().flush()
        if self._current:
            self.pending.append(self._current)
            self._current = []
        if self.next_in_chain:
            self.next_in_chain.flush()

    def clear_buffer(self) -> None:
        # An interruption mid-synthesis: whatever was still being captured
        # never fully reached the caller's ears. The already-flushed segments
        # keep their place — their playback events carry the honest verdict.
        self._current = []
        if self.next_in_chain:
            self.next_in_chain.clear_buffer()


def attach(session, relay, *, on_error=None) -> "TeeHandle":
    """Wire both taps into a started session and start feeding the relay.

    Returns a handle whose drain() the shutdown path awaits, so a clip that
    was mid-master when the caller hung up still reaches the relay before
    the relay pushes its tail.
    """
    handle = TeeHandle(session, relay)
    handle._install()
    return handle


class TeeHandle:
    def __init__(self, session, relay) -> None:
        self.session = session
        self.relay = relay
        self.tap: CallerTap | None = None
        self.tee: DJTee | None = None
        self._queue: asyncio.Queue | None = None
        self._worker: asyncio.Task | None = None

    def _install(self) -> None:
        self.tap = CallerTap(self.session.input.audio)
        self.session.input.audio = self.tap
        self.tee = DJTee(self.session.output.audio)
        self.session.output.audio = self.tee

        # Before either handler can fire — a boundary event with nowhere to
        # queue is a turn that never airs.
        self._start_queue()
        self.session.on("user_state_changed", self._on_user_state)
        self.tee.on("playback_finished", self._on_playback_finished)

    # -- the running order -------------------------------------------------
    # Both legs used to finish their own clip on their own task and race each
    # other to the relay. The caller's leg does strictly more work — it runs
    # the mastering chain for the phone-band costume, which the DJ's leg skips
    # entirely — so on a congested box the reply reached the relay before the
    # turn it answered, and the audience heard the answer before the question.
    # The record showed nothing wrong: it is built from session history, not
    # from what aired.
    #
    # Three places promise this in the same words — docs/on-air.md, relay.py's
    # module docstring, and feed()'s own docstring: clips air IN CONVERSATION
    # ORDER. Nothing enforced it. Now the boundary events claim a place in the
    # queue at the moment the turn ENDED, and one worker drains it, so no
    # amount of thread-pool weather can reshuffle what the room did.
    #
    # Second time this seam has bitten. SAY_POLL_SECS in onair/relay.py exists
    # because a clip push landed ahead of the DJ's intro on 2026-08-17 — the
    # same two-paths-into-one-FIFO shape, found on air and patched with a
    # sleep. A queue is the version that cannot come back.

    def _start_queue(self) -> None:
        self._queue = asyncio.Queue()
        self._worker = asyncio.create_task(self._run_queue())

    def _enqueue(self, kind: str, frames: list[rtc.AudioFrame]) -> None:
        """Claim this turn's place in the running order, at the moment the
        turn ended. Everything slow happens later, in the worker."""
        if self._queue is None:
            return
        self._queue.put_nowait((kind, frames))

    async def _run_queue(self) -> None:
        while True:
            kind, frames = await self._queue.get()
            try:
                if kind == "caller":
                    await self._finalise_caller(frames)
                else:
                    await self._finalise_dj(frames)
            except asyncio.CancelledError:
                self._queue.task_done()
                raise
            except Exception as e:                              # noqa: BLE001
                # A clip that dies must not take the ones behind it with it.
                # A hole that blocked would stall the rest of the broadcast,
                # which is a worse fault than the one missing turn.
                log.warning("on-air clip (%s) failed: %s", kind, e)
            self._queue.task_done()

    # -- caller leg --------------------------------------------------------
    def _on_user_state(self, ev) -> None:
        state = getattr(ev, "new_state", "")
        if state == "speaking":
            self.tap.start_clip()
        elif state == "listening":
            frames = self.tap.end_clip()
            secs = (sum(f.samples_per_channel for f in frames)
                    / float(frames[0].sample_rate)) if frames else 0.0
            if secs >= MIN_CLIP_SECS:
                self._enqueue("caller", frames)

    async def _finalise_caller(self, frames: list[rtc.AudioFrame]) -> None:
        raw = _scratch_wav("onair-c-")
        cooked = raw.with_suffix(".m.wav")
        # The relay's cfg is this CALL's settings — re-read at pickup like
        # everything else — so the operator flipping the sound style applies
        # to the next caller without a restart, same as every setting.
        style = str((getattr(self.relay, "cfg", None) or {})
                    .get("on_air_caller_sound") or "clean")
        try:
            await asyncio.to_thread(_write_wav, frames, raw)
            stats = await asyncio.to_thread(
                master.master, raw, cooked, MAX_CLIP_SECS, style)
        except ValueError:
            # Nothing audible in the turn — breath, rustle. It doesn't air.
            cooked.unlink(missing_ok=True)
            self._dropped("caller", "nothing audible once mastered")
            return
        except Exception as e:                                  # noqa: BLE001
            log.warning("caller clip failed to master: %s", e)
            cooked.unlink(missing_ok=True)
            self._dropped("caller", f"the master failed: {e}")
            return
        finally:
            raw.unlink(missing_ok=True)
        await self.relay.feed(cooked, "caller",
                              float(stats.get("seconds") or 0))

    # -- dj leg ------------------------------------------------------------
    def _on_playback_finished(self, ev) -> None:
        if not self.tee.pending:
            return
        frames = self.tee.pending.popleft()
        secs = (sum(f.samples_per_channel for f in frames)
                / float(frames[0].sample_rate)) if frames else 0.0
        if secs < MIN_CLIP_SECS:
            self._dropped("dj", f"too short to be a turn ({secs:.2f}s)")
            return
        played = float(getattr(ev, "playback_position", secs) or secs)
        if getattr(ev, "interrupted", False) and played < secs * PLAYED_ENOUGH:
            # The caller talked over it. Airing a sentence they cut off would
            # broadcast a conversation that never happened.
            self._dropped(
                "dj", f"the caller talked over it ({played:.1f}s of "
                      f"{secs:.1f}s played)")
            return
        self._enqueue("dj", frames)

    async def _finalise_dj(self, frames: list[rtc.AudioFrame]) -> None:
        path = _scratch_wav("onair-d-")
        try:
            secs = await asyncio.to_thread(_write_wav, frames, path)
        except Exception as e:                                  # noqa: BLE001
            log.warning("dj clip failed to write: %s", e)
            path.unlink(missing_ok=True)
            self._dropped("dj", f"the clip would not write: {e}")
            return
        if secs < MIN_CLIP_SECS:
            path.unlink(missing_ok=True)
            self._dropped("dj", f"too short to be a turn ({secs:.2f}s)")
            return
        await self.relay.feed(path, "dj", secs)

    # -- plumbing ----------------------------------------------------------
    def _dropped(self, kind: str, why: str) -> None:
        """Say why a turn did not reach the air, on the relay's record.

        Three of these paths used to be a bare `return`, so a segment could
        come out with a hole in it and nothing anywhere said which of the
        seven causes it was. Never allowed to cost the clip it is explaining.
        """
        try:
            self.relay.dropped(kind, why)
        except Exception:                                       # noqa: BLE001
            pass

    async def drain(self, timeout: float = 3.0) -> None:
        """Let queued clip work finish before the relay closes — the caller's
        last word is usually still mastering when the hangup lands."""
        if self._queue is None:
            return
        try:
            await asyncio.wait_for(self._queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            log.warning("on-air clips still in hand after %.1fs — the tail "
                        "does not air", timeout)
        if self._worker is not None:
            self._worker.cancel()
