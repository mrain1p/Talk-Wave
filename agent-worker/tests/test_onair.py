"""The live-call relay: chunks, the one-turn hold, and both taps.

Everything here runs against a fake mixer socket and a fake station — the
transport is real, the network is loopback, and nothing reaches further.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from livekit import rtc
from livekit.agents.voice import io

import settings as settings_store
from call import tee
from onair import chunks, relay as relay_mod


class _FakeStation:
    def __init__(self, ok: bool = True):
        self.says: list[str] = []
        self.ok = ok

    async def dj_say(self, instruction, mode="styled", kind="callin"):
        self.says.append(instruction)
        return {"ok": self.ok}


class _FakeRecord:
    def __init__(self):
        self.problems: list[str] = []
        self.tools: list[str] = []

    def problem(self, what):
        self.problems.append(what)

    def tool(self, name, result=""):
        self.tools.append(f"{name}: {result}")


class _ChunkStore(unittest.TestCase):
    """Point the chunk store at a temp dir, per test."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._old = chunks.SERVE_DIR
        chunks.SERVE_DIR = Path(self._dir.name) / "onair"
        self.addCleanup(self._restore)

    def _restore(self):
        chunks.SERVE_DIR = self._old
        self._dir.cleanup()

    def _clip(self, name="c.wav", body=b"RIFFfake") -> Path:
        path = Path(self._dir.name) / name
        path.write_bytes(body)
        return path


class TestOnAirChunksAreTokenedAndShortLived(_ChunkStore):
    def test_adopt_moves_the_clip_behind_an_unguessable_token(self):
        src = self._clip()
        token = chunks.adopt(src)
        self.assertTrue(token and len(token) >= 12)
        self.assertFalse(src.exists(), "adopt must MOVE — no second copy")
        path = chunks.path_for(token)
        self.assertIsNotNone(path)
        self.assertEqual(path.read_bytes(), b"RIFFfake")

    def test_a_lookup_does_not_burn_the_token(self):
        # The mixer HEADs before it GETs — the studio's third silent take
        # (2026-08-17) was a lookup that burned on first touch. Two reads
        # must both answer; only discard() removes.
        token = chunks.adopt(self._clip())
        self.assertIsNotNone(chunks.path_for(token))
        self.assertIsNotNone(chunks.path_for(token))
        chunks.discard(token)
        self.assertIsNone(chunks.path_for(token))

    def test_a_token_cannot_reach_outside_the_store(self):
        for bad in ("../../etc/passwd", "a/b", "a\\b", "short", "", "x" * 99,
                    "tok.en"):
            self.assertIsNone(chunks.path_for(bad))
            chunks.discard(bad)      # must not raise, must not delete oddly

    def test_expired_chunks_answer_nothing_and_sweep_away(self):
        token = chunks.adopt(self._clip())
        stale = time.time() - chunks.CHUNK_TTL_SECS - 5
        os.utime(chunks.SERVE_DIR / f"{token}.wav", (stale, stale))
        self.assertIsNone(chunks.path_for(token))
        self.assertEqual(chunks.sweep(), 1)
        self.assertEqual(list(chunks.SERVE_DIR.glob("*.wav")), [])


class _RelayCase(_ChunkStore):
    """A relay against a live loopback mixer, with settings pinned open."""

    def setUp(self):
        super().setUp()
        self._old_load = settings_store.load
        settings_store.load = lambda: {"allow_on_air": "open"}
        self.addCleanup(lambda: setattr(settings_store, "load", self._old_load))

    def _fake_mixer(self, reply: bytes = b"409\nEND\n") -> tuple[str, list]:
        import socket as _socket
        import threading

        got: list[bytes] = []
        srv = _socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        port = srv.getsockname()[1]

        def _serve():
            while True:
                try:
                    conn, _ = srv.accept()
                except OSError:
                    return
                with conn:
                    conn.settimeout(3)
                    buf = b""
                    try:
                        while b"quit" not in buf:
                            chunk = conn.recv(1024)
                            if not chunk:
                                break
                            buf += chunk
                    except OSError:
                        pass
                    if buf:
                        got.append(buf)
                    try:
                        conn.sendall(reply)
                    except OSError:
                        pass

        threading.Thread(target=_serve, daemon=True).start()
        self.addCleanup(srv.close)
        return f"127.0.0.1:{port}", got

    def _relay(self, station=None, record=None, **cfg_extra):
        addr, got = self._fake_mixer()
        cfg = {"vm_mixer_telnet": addr,
               "vm_air_base_url": "http://192.168.1.245:8100", **cfg_extra}
        r = relay_mod.CallRelay(station or _FakeStation(), cfg,
                                room="callin-g-abcdef123456", tier="guest",
                                record=record or _FakeRecord())
        return r, got

    def _feed_file(self, name: str) -> Path:
        return self._clip(name, body=b"RIFF" + name.encode())


class TestTheRelayHoldsOneTurnBack(_RelayCase):
    # The hold IS the dump button, and it is also what keeps the mixer's
    # queue fed so the duck never releases mid-conversation. Turn k airs
    # when turn k+1 finishes; the tail airs at close.
    def test_lag_by_one_then_the_tail_on_close(self):
        async def run():
            r, got = self._relay()
            self.assertTrue(await r.open())
            await r.feed(self._feed_file("t1.wav"), "caller", 2.0)
            await r.feed(self._feed_file("t2.wav"), "dj", 3.0)
            await r.feed(self._feed_file("t3.wav"), "caller", 1.0)
            pushed_mid = len([g for g in got if b"voice_queue.push" in g])
            await r.close("caller hung up")
            return r, got, pushed_mid

        r, got, pushed_mid = asyncio.run(run())
        pushes = [g for g in got if b"voice_queue.push" in g]
        self.assertEqual(pushed_mid, 2, "the newest turn stays in hand")
        self.assertEqual(len(pushes), 3, "the tail airs at close")
        self.assertEqual(r.pushed, 3)
        for push in pushes:
            self.assertIn(b"/on-air/", push)

    def test_the_intro_airs_before_any_clip_and_the_outro_after(self):
        async def run():
            station = _FakeStation()
            r, got = self._relay(station=station)
            await r.open()
            await r.feed(self._feed_file("t1.wav"), "caller", 2.0)
            await r.close("done")
            return station

        station = asyncio.run(run())
        self.assertEqual(len(station.says), 2, "intro and outro, no more")
        self.assertIn("caller is on the line", station.says[0])
        self.assertIn("thank the caller", station.says[1])


class TestTheRelayObeysTheOperatorMidCall(_RelayCase):
    def test_switching_the_feature_off_stops_the_next_push(self):
        # Settings are re-read per CALL by invariant; a live broadcast is
        # tighter — the flip must stop the next clip, not the next caller.
        async def run():
            r, got = self._relay()
            await r.open()
            await r.feed(self._feed_file("t1.wav"), "caller", 2.0)
            settings_store.load = lambda: {"allow_on_air": "off"}
            await r.feed(self._feed_file("t2.wav"), "dj", 2.0)
            await r.feed(self._feed_file("t3.wav"), "caller", 2.0)
            return r, got

        r, got = asyncio.run(run())
        self.assertEqual(len([g for g in got if b"voice_queue.push" in g]), 0,
                         "no clip airs once the operator said no")
        self.assertFalse(r.active)

    def test_the_panels_dump_crosses_the_process_seam(self):
        # The dump button lives in the WEB process and the relay in the
        # worker; the marker file in the shared store is the message. A
        # fresh marker kills the held turn AND the arriving one; a stale
        # one (a dump pressed long before, with nothing live) is spent
        # silently — it must never behead the next caller's first turn.
        async def run():
            station = _FakeStation()
            r, got = self._relay(station=station)
            await r.open()
            await r.feed(self._feed_file("t1.wav"), "caller", 2.0)
            chunks.request_dump()                  # the operator, mid-call
            await r.feed(self._feed_file("t2.wav"), "dj", 2.0)
            return r, got, station

        r, got, station = asyncio.run(run())
        self.assertEqual([g for g in got if b"voice_queue.push" in g], [],
                         "neither the held turn nor the new one airs")
        self.assertTrue(r.dumped)
        self.assertEqual(len(station.says), 2, "intro, then the sign-off")

    def test_a_stale_marker_is_spent_not_obeyed(self):
        async def run():
            r, got = self._relay()
            chunks.request_dump()
            stale = time.time() - chunks.DUMP_FRESH_SECS - 5
            os.utime(chunks.SERVE_DIR / "DUMP", (stale, stale))
            await r.open()                         # open() spends leftovers
            await r.feed(self._feed_file("t1.wav"), "caller", 2.0)
            await r.feed(self._feed_file("t2.wav"), "dj", 2.0)
            return r, got

        r, got = asyncio.run(run())
        self.assertTrue(r.active, "a stale dump must not end the broadcast")
        self.assertEqual(
            len([g for g in got if b"voice_queue.push" in g]), 1)

    def test_dump_kills_the_unpushed_tail(self):
        async def run():
            station = _FakeStation()
            r, got = self._relay(station=station)
            await r.open()
            await r.feed(self._feed_file("t1.wav"), "caller", 2.0)
            await r.feed(self._feed_file("t2.wav"), "dj", 2.0)
            await r.dump()
            return r, got, station

        r, got, station = asyncio.run(run())
        pushes = [g for g in got if b"voice_queue.push" in g]
        self.assertEqual(len(pushes), 1, "the held turn dies with the dump")
        self.assertTrue(r.dumped)
        self.assertFalse(r.active)
        self.assertEqual(len(station.says), 2, "the outro still airs")


class TestTheRelayFallsBackOutLoud(_RelayCase):
    def test_an_unreachable_mixer_means_a_private_call_that_says_so(self):
        # Silently downgrading is how an operator spends a week not noticing
        # the network stanza never landed — the studio's lesson, kept.
        async def run():
            record = _FakeRecord()
            r = relay_mod.CallRelay(
                _FakeStation(), {"vm_mixer_telnet": "127.0.0.1:1",
                                 "vm_air_base_url": "http://x:8100"},
                room="callin-g-abcdef123456", tier="guest", record=record)
            ok = await r.open()
            return ok, record, r

        ok, record, r = asyncio.run(run())
        self.assertFalse(ok)
        self.assertFalse(r.active)
        self.assertTrue(any("fell back to a private call" in p
                            for p in record.problems))

    def test_the_window_closes_itself(self):
        async def run():
            station = _FakeStation()
            r, got = self._relay(station=station, on_air_max_seconds=0.05)
            await r.open()
            await r.feed(self._feed_file("t1.wav"), "caller", 2.0)
            await asyncio.sleep(0.1)
            await r.feed(self._feed_file("t2.wav"), "dj", 2.0)
            return r, station

        r, station = asyncio.run(run())
        self.assertFalse(r.active)
        self.assertEqual(len(station.says), 2, "the window close says goodbye")


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


if __name__ == "__main__":
    unittest.main()
