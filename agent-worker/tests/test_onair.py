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

    def test_the_dashboards_quick_kill_stops_the_next_clip_too(self):
        # Until 0.97.64 the Live Call quick kill closed the door only to the
        # NEXT caller (the mint refuses the route) while the master tier row
        # stopped a running broadcast at its next clip — two switches that
        # both read "close the door", only one of which closed it. The
        # dashboard's own switch now counts the same as the master.
        async def run():
            r, got = self._relay()
            await r.open()
            await r.feed(self._feed_file("t1.wav"), "caller", 2.0)
            settings_store.load = lambda: {"allow_on_air": "open",
                                           "on_air_calls_enabled": False}
            await r.feed(self._feed_file("t2.wav"), "dj", 2.0)
            return r, got

        r, got = asyncio.run(run())
        self.assertEqual(len([g for g in got if b"voice_queue.push" in g]), 0,
                         "no clip airs once the quick kill is off")
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
        # NOTHING aired before the dump (the only clip was still in hand),
        # so there is nobody on the stream to thank: intro alone.
        self.assertEqual(len(station.says), 1, "intro only — no sign-off "
                         "for a broadcast that carried no caller")

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
        self.assertEqual(len(station.says), 2,
                         "a clip aired, so the sign-off still does")


class TestTheBracketsOnlyAirWhenACallerDoes(_RelayCase):
    # The first deployed test (2026-08-17): a call whose media never arrived
    # aired "a caller is coming on the air…", a minute of nothing, then a
    # thank-you to nobody — interleaved into a REAL call's broadcast. The
    # intro now waits for the first clip; the outro requires an aired one.
    def test_a_call_with_no_audio_airs_not_one_word(self):
        async def run():
            station = _FakeStation()
            r, got = self._relay(station=station)
            self.assertTrue(await r.open())
            await r.close("the call ended")
            return r, got, station

        r, got, station = asyncio.run(run())
        self.assertEqual(station.says, [], "no intro, no outro, nothing")
        self.assertEqual([g for g in got if b"voice_queue.push" in g], [])
        self.assertEqual(r.pushed, 0)

    def test_the_intro_fires_at_the_first_clip_not_at_pickup(self):
        async def run():
            station = _FakeStation()
            r, got = self._relay(station=station)
            await r.open()
            armed_says = len(station.says)
            await r.feed(self._feed_file("t1.wav"), "dj", 2.0)
            live_says = len(station.says)
            await r.close("done")
            return armed_says, live_says, station

        armed_says, live_says, station = asyncio.run(run())
        self.assertEqual(armed_says, 0, "armed is silent")
        self.assertEqual(live_says, 1, "the first clip opens the broadcast")
        self.assertIn("caller is on the line", station.says[0])


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

    def test_the_open_preflight_leaves_a_verdict_either_way(self):
        # The verdict marker is how the WEB process learns whether the
        # WORKER can reach the mixer — its own probe runs on different
        # networks and answered for the wrong container (2026-08-18).
        async def run():
            dead = relay_mod.CallRelay(
                _FakeStation(), {"vm_mixer_telnet": "127.0.0.1:1",
                                 "vm_air_base_url": "http://x:8100"},
                room="callin-g-abcdef123456", tier="guest",
                record=_FakeRecord())
            self.assertFalse(await dead.open())
            no = chunks.mixer_verdict()
            live, _ = self._relay()
            self.assertTrue(await live.open())
            yes = chunks.mixer_verdict()
            return no, yes

        no, yes = asyncio.run(run())
        self.assertIsNotNone(no)
        self.assertFalse(no["ok"])
        self.assertEqual(no["why"], "no reachable mixer")
        self.assertIsNotNone(yes)
        self.assertTrue(yes["ok"])

    def test_a_failed_adopt_does_not_leak_the_callers_voice(self):
        # adopt() moves the clip; when the move itself fails the source file
        # would otherwise sit in the container's /tmp until the container
        # dies — a stranger's voice outliving every deletion rule.
        async def run():
            r, got = self._relay()
            await r.open()
            old_adopt = chunks.adopt
            chunks.adopt = lambda wav: None
            self.addCleanup(lambda: setattr(chunks, "adopt", old_adopt))
            first = self._feed_file("t1.wav")
            await r.feed(first, "caller", 2.0)
            await r.feed(self._feed_file("t2.wav"), "dj", 2.0)
            return first

        first = asyncio.run(run())
        self.assertFalse(first.exists(),
                         "the un-adopted clip must still be deleted")

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


class TestTheLiveCallDoorTellsTheWorkersTruth(_ChunkStore):
    """The dashboard's Live Call door and the widget's ON AIR toggle both
    come from _on_air_door, which runs in the WEB process — but the WORKER
    is the process that pushes, and the two containers sit on their own
    docker networks. A deployment that joined only the web to the station's
    network showed the door open while every phone-in quietly fell back
    private (2026-08-18). The worker's written verdict now outranks the
    web's own probe whenever it is fresh."""

    def _door(self, cfg):
        from api import live as live_mod

        live_mod._onair_probe["at"] = 0.0
        live_mod._onair_probe["ok"] = False
        return asyncio.run(live_mod._on_air_door(cfg))

    def _cfg(self, **extra):
        # 127.0.0.1:1 refuses instantly, so the web's OWN probe always fails
        # here — which is exactly the half-joined shape under test.
        return {"allow_on_air": "open", "on_air_calls_enabled": True,
                "vm_mixer_telnet": "127.0.0.1:1",
                "vm_air_base_url": "http://x:8100", **extra}

    def test_a_fresh_no_from_the_worker_shuts_the_door(self):
        chunks.record_mixer_verdict(False, "no reachable mixer")
        self.assertFalse(self._door(self._cfg())["calls"])

    def test_a_fresh_ok_from_the_worker_opens_it_over_the_webs_own_probe(self):
        chunks.record_mixer_verdict(True)
        self.assertTrue(self._door(self._cfg())["calls"])

    def test_a_stale_verdict_falls_back_to_the_webs_probe(self):
        chunks.record_mixer_verdict(True)
        stale = time.time() - chunks.VERDICT_FRESH_SECS - 5
        os.utime(chunks.SERVE_DIR / "MIXER", (stale, stale))
        self.assertFalse(self._door(self._cfg())["calls"],
                         "a worker that stopped talking does not hold the "
                         "door open forever")


class TestTheLiveSegmentLandsInsteadOfStopping(unittest.TestCase):
    """The on-air window was enforced and never announced.

    onair/relay.py watched its deadline pass, signed the segment off and said
    an outro, and the first the DJ knew of any of it was that it had happened
    — so a live phone-in was cut at whatever sentence the clock fell on, in
    front of the audience. Radio does not end a segment that way.
    """

    def test_the_clock_starts_at_the_first_clip_not_at_pickup(self):
        # A segment that never got a caller's voice into it has no window to
        # be near the end of, so there is nothing to wrap and nobody to hear
        # it. Same reason the brackets are lazy.
        r = relay_mod.CallRelay(_FakeStation(), {}, "callin-g-abcdef123456")
        self.assertEqual(r.seconds_left(), 0.0, "armed is not live")
        r.active = True
        self.assertEqual(r.seconds_left(), 0.0, "active is still not live")

    def test_a_live_segment_reports_what_is_left(self):
        r = relay_mod.CallRelay(_FakeStation(), {}, "callin-g-abcdef123456")
        r.active = True
        r._live = True
        r._deadline = time.time() + 45
        left = r.seconds_left()
        self.assertGreater(left, 40)
        self.assertLessEqual(left, 45)

    def test_a_closed_segment_stops_reporting(self):
        # Otherwise the wrap cue would fire at a caller whose broadcast has
        # already ended — a line about being nearly out of time, said
        # privately, about nothing.
        r = relay_mod.CallRelay(_FakeStation(), {}, "callin-g-abcdef123456")
        r.active, r._live = True, True
        r._deadline = time.time() + 45
        r.active = False
        self.assertEqual(r.seconds_left(), 0.0)


if __name__ == "__main__":
    unittest.main()
