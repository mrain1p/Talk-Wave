"""The two taps, and the running order they hand the relay.

Split from test_onair.py at 0.97.65. The seam is the one that module's own
SPLITTING entry named: the STORE AND BROADCAST side (chunks, the relay, the
door) against the TAP side — how a live call's audio becomes clips at all,
and in what order they reach the air. They shared only fixtures.

Everything here runs against fake audio IO. Nothing reaches a mixer.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from livekit import rtc
from livekit.agents.voice import io

from call import tee


class _Frames:
    @staticmethod
    def frame(rate=16000, samples=160, channels=1) -> rtc.AudioFrame:
        return rtc.AudioFrame.create(sample_rate=rate, num_channels=channels,
                                     samples_per_channel=samples)


class _FakeInput(io.AudioInput):
    def __init__(self, frames):
        super().__init__(label="FakeInput")
        self._frames = list(frames)

    async def __anext__(self) -> rtc.AudioFrame:
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)


class _FakeSink(io.AudioOutput):
    def __init__(self):
        super().__init__(label="FakeSink",
                         capabilities=io.AudioOutputCapabilities(pause=False))
        self.captured = 0
        self.flushes = 0
        self.clears = 0

    async def capture_frame(self, frame):
        await super().capture_frame(frame)
        self.captured += 1

    def flush(self):
        super().flush()
        self.flushes += 1

    def clear_buffer(self):
        self.clears += 1


class TestTheCallerTapRidesTheTurnBoundaries(unittest.TestCase):
    def test_preroll_rides_into_the_clip_and_stays_capped(self):
        # The VAD needs a moment to notice speech; without the preroll every
        # clip arrives saying "-ey" instead of "hey" — the same clipped
        # consonant the studio's trim margin exists for.
        async def run():
            frames = [_Frames.frame() for _ in range(80)]     # 10ms each
            tap = tee.CallerTap(_FakeInput(frames))
            for _ in range(60):                # 600ms of not-yet-speaking
                await tap.__anext__()
            tap.start_clip()
            for _ in range(20):
                await tap.__anext__()
            return tap.end_clip()

        clip = asyncio.run(run())
        secs = sum(f.samples_per_channel for f in clip) / 16000.0
        self.assertGreaterEqual(secs, 0.2 + tee.PREROLL_SECS - 0.05)
        self.assertLessEqual(secs, 0.2 + tee.PREROLL_SECS + 0.05,
                             "the preroll is a window, not a tape archive")

    def test_frames_flow_through_untouched(self):
        async def run():
            frames = [_Frames.frame() for _ in range(3)]
            tap = tee.CallerTap(_FakeInput(list(frames)))
            out = [await tap.__anext__() for _ in range(3)]
            return frames, out

        frames, out = asyncio.run(run())
        self.assertEqual([id(f) for f in frames], [id(f) for f in out],
                         "the tap listens; it must never alter the STT's feed")


class TestTheDJTeeCutsOnSegments(unittest.TestCase):
    def test_flush_closes_a_segment_and_forwards_everything(self):
        async def run():
            sink = _FakeSink()
            dj = tee.DJTee(sink)
            for _ in range(3):
                await dj.capture_frame(_Frames.frame())
            dj.flush()
            for _ in range(2):
                await dj.capture_frame(_Frames.frame())
            dj.clear_buffer()
            dj.flush()
            return sink, dj

        sink, dj = asyncio.run(run())
        self.assertEqual(sink.captured, 5, "every frame reaches the room")
        self.assertEqual(len(dj.pending), 1,
                         "an interrupted synthesis never becomes a clip")
        self.assertEqual(len(dj.pending[0]), 3)
        self.assertEqual(sink.clears, 1)

    def test_an_interrupted_line_barely_played_does_not_air(self):
        fed: list = []

        class _R:
            async def feed(self, wav, kind, secs):
                fed.append((kind, secs))

        handle = tee.TeeHandle.__new__(tee.TeeHandle)
        handle.relay = _R()
        handle._tasks = set()
        handle.tee = tee.DJTee(_FakeSink())
        # 1.0s of synthesis in one pending segment
        handle.tee.pending.append([_Frames.frame(samples=16000)])

        handle._on_playback_finished(
            SimpleNamespace(playback_position=0.2, interrupted=True))
        self.assertEqual(fed, [], "a fifth of a sentence is not a clip")
        self.assertEqual(len(handle.tee.pending), 0, "the verdict consumed it")

    def test_wav_writer_mixes_down_and_caps(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "clip.wav"
            frames = [_Frames.frame(samples=16000) for _ in range(3)]
            secs = tee._write_wav(frames, path, max_secs=2.0)
            self.assertAlmostEqual(secs, 2.0, places=2)
            import wave

            with wave.open(str(path), "rb") as w:
                self.assertEqual(w.getnchannels(), 1)
                self.assertEqual(w.getframerate(), 16000)


class TestTheAirGetsTheConversationInOrder(unittest.TestCase):
    """docs/on-air.md, relay.py's module docstring and feed()'s own docstring
    all promise the same thing in the same words: clips air "in conversation
    order". Nothing enforced it.

    The two legs do unequal work — the caller's runs the mastering chain for
    the phone-band costume, the DJ's writes a WAV and stops — so on a
    congested box the reply can reach the relay before the turn it answers
    and the audience hears the answer before the question. The record shows
    nothing wrong, because the record is built from session history and not
    from what aired.

    This is the same bug class the studio already met once at the adjacent
    seam: SAY_POLL_SECS exists because a clip push landed ahead of the intro
    on 2026-08-17. That one was found on air. This one is not, yet.
    """

    def _handle(self, relay, master_delay: float):
        handle = tee.TeeHandle.__new__(tee.TeeHandle)
        handle.relay = relay
        handle.session = None
        handle.tap = tee.CallerTap(_FakeInput([_Frames.frame(samples=16000)]))
        handle.tee = tee.DJTee(_FakeSink())
        handle._start_queue()

        async def _slow_master(raw, cooked, max_secs):
            await asyncio.sleep(master_delay)
            cooked.write_bytes(b"RIFF")
            return {"seconds": 1.0}

        # master.master is sync and reached through asyncio.to_thread; the
        # stand-in is async, so the seam is patched rather than the function.
        async def _to_thread(fn, *a, **k):
            if getattr(fn, "__name__", "") == "master":
                return await _slow_master(*a, **k)
            return fn(*a, **k)

        return handle, _to_thread

    def test_a_slow_caller_master_still_airs_before_the_dj_reply(self):
        fed: list[str] = []

        class _R:
            async def feed(self, wav, kind, seconds):
                fed.append(kind)

        async def run():
            handle, to_thread = self._handle(_R(), master_delay=0.25)
            real = asyncio.to_thread
            asyncio.to_thread = to_thread
            try:
                # The caller finishes a turn...
                handle._on_user_state(SimpleNamespace(new_state="speaking"))
                await handle.tap.__anext__()
                handle._on_user_state(SimpleNamespace(new_state="listening"))
                # ...and the DJ's reply finishes playing right behind it,
                # with no mastering in its way.
                handle.tee.pending.append([_Frames.frame(samples=16000)])
                handle._on_playback_finished(
                    SimpleNamespace(playback_position=1.0, interrupted=False))
                await handle.drain(timeout=5.0)
            finally:
                asyncio.to_thread = real

        asyncio.run(run())
        self.assertEqual(fed, ["caller", "dj"],
                         "the audience must hear the question before the answer")

    def test_a_clip_that_dies_does_not_stall_the_ones_behind_it(self):
        """A turn with nothing audible in it is dropped by the master, and the
        queue has to step over it — a hole that blocked would take the rest of
        the broadcast with it."""
        fed: list[str] = []

        class _R:
            async def feed(self, wav, kind, seconds):
                fed.append(kind)

        async def run():
            handle, _ = self._handle(_R(), master_delay=0.0)
            real = asyncio.to_thread

            async def _to_thread(fn, *a, **k):
                if getattr(fn, "__name__", "") == "master":
                    raise ValueError("nothing audible")
                return fn(*a, **k)

            asyncio.to_thread = _to_thread
            try:
                handle._on_user_state(SimpleNamespace(new_state="speaking"))
                await handle.tap.__anext__()
                handle._on_user_state(SimpleNamespace(new_state="listening"))
                handle.tee.pending.append([_Frames.frame(samples=16000)])
                handle._on_playback_finished(
                    SimpleNamespace(playback_position=1.0, interrupted=False))
                await handle.drain(timeout=5.0)
            finally:
                asyncio.to_thread = real

        asyncio.run(run())
        self.assertEqual(fed, ["dj"], "the breath died; the reply still aired")


if __name__ == "__main__":
    unittest.main()


class TestATurnThatDoesNotAirSaysWhy(unittest.TestCase):
    """A clip can fail to reach the air seven ways and three of them used to
    be a bare `return`.

    So the one question a live segment has to be able to answer afterwards —
    why did the audience not hear that bit — had no answer for three of its
    seven causes, and a segment came out with a hole in it and nothing
    anywhere saying which. The caller's own sub-threshold blip is deliberately
    NOT one of them: that is a cough, not a turn, and recording every one
    would bury the real drops.
    """

    def _handle(self, drops):
        class _R:
            async def feed(self, wav, kind, seconds):
                pass

            def dropped(self, kind, why):
                drops.append((kind, why))

        handle = tee.TeeHandle.__new__(tee.TeeHandle)
        handle.relay = _R()
        handle.session = None
        handle._queue = None
        handle._worker = None
        handle.tee = tee.DJTee(_FakeSink())
        return handle

    def test_a_line_the_caller_talked_over_says_how_much_played(self):
        drops: list = []
        handle = self._handle(drops)
        handle.tee.pending.append([_Frames.frame(samples=16000)])
        handle._on_playback_finished(
            SimpleNamespace(playback_position=0.2, interrupted=True))
        self.assertEqual(len(drops), 1)
        kind, why = drops[0]
        self.assertEqual(kind, "dj")
        self.assertIn("talked over", why)
        self.assertIn("0.2s of 1.0s", why,
                      "the numbers are the point — a reason without them "
                      "cannot be argued with")

    def test_a_relay_that_cannot_take_a_drop_never_costs_the_clip(self):
        # The explanation must never be worth more than the broadcast: a
        # relay mid-close, or one from an older build, has to be survivable.
        class _Broken:
            async def feed(self, wav, kind, seconds):
                pass

        handle = tee.TeeHandle.__new__(tee.TeeHandle)
        handle.relay = _Broken()
        handle.session = None
        handle._queue = None
        handle._worker = None
        handle.tee = tee.DJTee(_FakeSink())
        handle.tee.pending.append([_Frames.frame(samples=16000)])
        handle._on_playback_finished(
            SimpleNamespace(playback_position=0.2, interrupted=True))
        self.assertEqual(len(handle.tee.pending), 0,
                         "the verdict still consumed the segment")


if __name__ == "__main__":
    unittest.main()
