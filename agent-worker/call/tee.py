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

The caller's clips get the studio's mastering chain (phone-band is the right
costume for a caller on radio); the DJ's clips air as synthesized — the DJ
is the studio side of the conversation and should sound like it.
"""

from __future__ import annotations

import asyncio
import logging
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


def _write_wav(frames: list[rtc.AudioFrame], path: Path,
               max_secs: float = MAX_CLIP_SECS) -> float:
    """Mono 16-bit WAV at the frames' own rate; returns seconds written.
    Stereo is averaged down — the mixer wants one voice, not a field."""
    if not frames:
        return 0.0
    rate = frames[0].sample_rate
    limit = int(rate * max_secs)
    out: list[int] = []
    for f in frames:
        data = struct.unpack(f"<{len(bytes(f.data)) // 2}h", bytes(f.data))
        ch = max(1, f.num_channels)
        if ch > 1:
            data = tuple(sum(data[i:i + ch]) // ch
                         for i in range(0, len(data) - ch + 1, ch))
        out.extend(data)
        if len(out) >= limit:
            out = out[:limit]
            break
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(out)}h", *out))
    return len(out) / float(rate)


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
        self._tasks: set[asyncio.Task] = set()

    def _install(self) -> None:
        self.tap = CallerTap(self.session.input.audio)
        self.session.input.audio = self.tap
        self.tee = DJTee(self.session.output.audio)
        self.session.output.audio = self.tee

        self.session.on("user_state_changed", self._on_user_state)
        self.tee.on("playback_finished", self._on_playback_finished)

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
                self._spawn(self._finalise_caller(frames))

    async def _finalise_caller(self, frames: list[rtc.AudioFrame]) -> None:
        raw = Path(tempfile.mkstemp(suffix=".wav", prefix="onair-c-")[1])
        cooked = raw.with_suffix(".m.wav")
        try:
            await asyncio.to_thread(_write_wav, frames, raw)
            stats = await asyncio.to_thread(
                master.master, raw, cooked, MAX_CLIP_SECS)
        except ValueError:
            # Nothing audible in the turn — breath, rustle. It doesn't air.
            cooked.unlink(missing_ok=True)
            return
        except Exception as e:                                  # noqa: BLE001
            log.warning("caller clip failed to master: %s", e)
            cooked.unlink(missing_ok=True)
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
            return
        played = float(getattr(ev, "playback_position", secs) or secs)
        if getattr(ev, "interrupted", False) and played < secs * PLAYED_ENOUGH:
            return
        self._spawn(self._finalise_dj(frames))

    async def _finalise_dj(self, frames: list[rtc.AudioFrame]) -> None:
        path = Path(tempfile.mkstemp(suffix=".wav", prefix="onair-d-")[1])
        try:
            secs = await asyncio.to_thread(_write_wav, frames, path)
        except Exception as e:                                  # noqa: BLE001
            log.warning("dj clip failed to write: %s", e)
            path.unlink(missing_ok=True)
            return
        if secs < MIN_CLIP_SECS:
            path.unlink(missing_ok=True)
            return
        await self.relay.feed(path, "dj", secs)

    # -- plumbing ----------------------------------------------------------
    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self, timeout: float = 3.0) -> None:
        """Let in-flight clip work finish before the relay closes — the
        caller's last word is usually still mastering when the hangup lands."""
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=timeout)
