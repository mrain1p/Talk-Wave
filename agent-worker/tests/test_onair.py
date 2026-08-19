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


class TestTheHoldIsAPromiseNotAnAccident(_RelayCase):
    """MAX_HELD_SECS bounds the lag-by-one.

    The hold exists for the dump, but its length was whatever the NEXT turn
    happened to take — endpointing, the model thinking, the DJ's whole answer
    playing out — so a caller's turn sat unaired for 10-25 seconds and the
    broadcast filled the gap with music swells (~24s, measured on the first
    live tests). The cap trades that accident for a promise: every finished
    turn stays killable for exactly this long, and then it airs.
    """

    def test_a_turn_with_no_successor_airs_when_the_hold_expires(self):
        async def run():
            r, got = self._relay()
            r.max_held_secs = 0.15
            self.assertTrue(await r.open())
            await r.feed(self._feed_file("t1.wav"), "caller", 2.0)
            before = len([g for g in got if b"voice_queue.push" in g])
            await asyncio.sleep(0.6)
            after = len([g for g in got if b"voice_queue.push" in g])
            await r.close("done")
            return before, after

        before, after = asyncio.run(run())
        self.assertEqual(before, 0, "the hold is real — nothing airs at once")
        self.assertEqual(after, 1,
                         "the cap expired and the turn aired on its own — a "
                         "caller must not wait out the DJ's whole next answer "
                         "to be heard on the broadcast")

    def test_a_successor_arriving_first_keeps_the_ordinary_path(self):
        async def run():
            r, got = self._relay()
            r.max_held_secs = 5.0            # far beyond this test's life
            self.assertTrue(await r.open())
            await r.feed(self._feed_file("t1.wav"), "caller", 2.0)
            await r.feed(self._feed_file("t2.wav"), "dj", 3.0)
            n = len([g for g in got if b"voice_queue.push" in g])
            await r.close("done")
            return n

        self.assertEqual(asyncio.run(run()), 1,
                         "lag-by-one unchanged when the conversation flows")

    def test_the_dump_pressed_during_the_hold_still_kills_the_turn(self):
        # The window the cap exists to GUARANTEE: a marker pressed while the
        # clip is in hand must kill it when the timer fires, not lose the race.
        async def run():
            r, got = self._relay()
            r.max_held_secs = 0.15
            self.assertTrue(await r.open())
            await r.feed(self._feed_file("t1.wav"), "caller", 2.0)
            chunks.request_dump()
            await asyncio.sleep(0.6)
            pushes = len([g for g in got if b"voice_queue.push" in g])
            return r, pushes

        r, pushes = asyncio.run(run())
        self.assertEqual(pushes, 0, "the dumped turn never aired")
        self.assertTrue(r.dumped)
        self.assertFalse(r.active, "the segment closed with the dump")


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

    def test_the_door_says_which_kind_of_shut_it_is(self):
        # `calls` false means EITHER the operator's quick kill OR a mixer
        # nobody can reach, and only one of those deserves the panel's wiring
        # warning. `enabled` carries the kill's own state so the panel can
        # tell them apart (the operator's ask, 2026-08-18), and `mode` says
        # live or tape so the stage frame promises the right thing.
        chunks.record_mixer_verdict(False, "no reachable mixer")
        unwired = self._door(self._cfg())
        self.assertFalse(unwired["calls"])
        self.assertTrue(unwired["enabled"], "the kill was not thrown")
        killed = self._door(self._cfg(on_air_calls_enabled=False))
        self.assertFalse(killed["calls"])
        self.assertFalse(killed["enabled"])
        self.assertEqual(unwired["mode"], "live")
        self.assertEqual(
            self._door(self._cfg(on_air_call_mode="after"))["mode"], "after")
        # "heard" is a LIVE promise from where the caller stands — the stage
        # frame only needs to know tape from not-tape.
        self.assertEqual(
            self._door(self._cfg(on_air_call_mode="heard"))["mode"], "live")


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


class TestTapeModeAirsTheCallAtHangup(_RelayCase):
    """on_air_call_mode = "after" (0.98.5): nothing airs during the call and
    the whole conversation plays at close — the operator's ask, and the mode
    where PULL OFF AIR kills the ENTIRE call before a word of it airs, where
    live mode can only ever kill the turn still inside its delay window."""

    def test_nothing_airs_until_close_then_everything_in_order(self):
        async def run():
            station = _FakeStation()
            r, got = self._relay(station=station, on_air_call_mode="after")
            self.assertTrue(await r.open())
            await r.feed(self._feed_file("t1.wav"), "caller", 2.0)
            await r.feed(self._feed_file("t2.wav"), "dj", 3.0)
            await r.feed(self._feed_file("t3.wav"), "caller", 1.0)
            pushed_mid = len([g for g in got if b"voice_queue.push" in g])
            await r.close("the call ended")
            return r, got, pushed_mid, station

        r, got, pushed_mid, station = asyncio.run(run())
        pushes = [g for g in got if b"voice_queue.push" in g]
        self.assertEqual(pushed_mid, 0, "a taped turn aired during the call")
        self.assertEqual(len(pushes), 3, "the whole reel plays at hangup")
        self.assertEqual(r.pushed, 3)
        self.assertEqual(len(station.says), 2,
                         "intro and outro around the tape, no more")
        self.assertIn("recording", station.says[0])
        self.assertIn("thank the caller", station.says[1])

    def test_a_dump_during_the_call_kills_the_whole_tape_silently(self):
        # The tape's whole argument: at any moment of the call the operator
        # can make the broadcast never have happened. And with zero clips
        # aired there is nobody on the stream to say an outro to — a dumped
        # tape must not speak at all (the first deployed test's lesson, in
        # its tape shape).
        async def run():
            station = _FakeStation()
            r, got = self._relay(station=station, on_air_call_mode="after")
            await r.open()
            await r.feed(self._feed_file("t1.wav"), "caller", 2.0)
            await r.feed(self._feed_file("t2.wav"), "dj", 3.0)
            chunks.request_dump()
            await r.feed(self._feed_file("t3.wav"), "caller", 1.0)
            await r.close("the call ended")
            return r, got, station

        r, got, station = asyncio.run(run())
        self.assertTrue(r.dumped)
        self.assertEqual([g for g in got if b"voice_queue.push" in g], [],
                         "a dumped tape aired something")
        self.assertEqual(station.says, [], "a dumped tape spoke on air")

    def test_the_window_caps_the_reel(self):
        # on_air_max_seconds bounds AIRED seconds in both modes. In tape mode
        # that is the reel's sum — a marathon call must not tape an hour of
        # broadcast — and what falls off the end is reported like any other
        # unaired turn, while the call itself carries on.
        async def run():
            record = _FakeRecord()
            r, got = self._relay(record=record, on_air_call_mode="after",
                                 on_air_max_seconds=5)
            await r.open()
            await r.feed(self._feed_file("t1.wav"), "caller", 3.0)
            await r.feed(self._feed_file("t2.wav"), "dj", 3.0)
            await r.feed(self._feed_file("t3.wav"), "caller", 1.0)
            await r.close("the call ended")
            return r, got, record

        r, got, record = asyncio.run(run())
        self.assertEqual(
            len([g for g in got if b"voice_queue.push" in g]), 2,
            "the tape aired more than the window allows")
        self.assertTrue(any("tape is full" in t for t in record.tools),
                        "the dropped turn left no trace in the record")

    def test_an_untouched_deployment_still_runs_live(self):
        # The default is the behaviour every existing deployment already has.
        r, _ = self._relay()
        self.assertFalse(r.tape)
        self.assertFalse(r.wait_for_heard)

    def test_a_tape_with_no_caller_on_it_stays_in_the_drawer(self):
        # The operator's rule (2026-08-18): nothing sent, nothing delivered
        # to the booth. A caller whose media never arrived — or who never
        # spoke — leaves a reel of the DJ talking to nobody, and airing that
        # is worse than airing nothing. Unconditional in tape mode: at
        # hangup the reel is known, so the check costs no delay.
        async def run():
            station = _FakeStation()
            record = _FakeRecord()
            r, got = self._relay(station=station, record=record,
                                 on_air_call_mode="after")
            await r.open()
            await r.feed(self._feed_file("t1.wav"), "dj", 3.0)
            await r.feed(self._feed_file("t2.wav"), "dj", 2.0)
            await r.close("the call ended")
            return r, got, station, record

        r, got, station, record = asyncio.run(run())
        self.assertEqual([g for g in got if b"voice_queue.push" in g], [],
                         "a caller-less tape aired something")
        self.assertEqual(station.says, [], "a caller-less tape spoke on air")
        self.assertTrue(any("never heard" in t for t in record.tools),
                        "the unaired tape left no trace in the record")


class TestHeardModeOpensAtTheCallersFirstWord(_RelayCase):
    """on_air_call_mode = "heard" (0.98.9): the broadcast opens at the first
    CALLER clip, the DJ's opening waits on the reel until then, and a call
    where the caller is never heard airs nothing at all. A mode rather than
    the default because the guarantee delays the start of the broadcast by
    about one exchange — the operator's words: nothing sent, nothing
    delivered to the booth, or at least a setting where that would delay
    the delivery."""

    def test_nothing_airs_until_the_caller_speaks_then_everything_in_order(self):
        async def run():
            station = _FakeStation()
            record = _FakeRecord()
            r, got = self._relay(station=station, record=record,
                                 on_air_call_mode="heard")
            self.assertTrue(await r.open())
            await r.feed(self._feed_file("t1.wav"), "dj", 3.0)   # the hello
            aired_early = len([g for g in got if b"voice_queue.push" in g])
            said_early = len(station.says)
            await r.feed(self._feed_file("t2.wav"), "caller", 2.0)
            await r.feed(self._feed_file("t3.wav"), "dj", 2.5)
            await r.close("the call ended")
            return r, got, aired_early, said_early, station, record

        r, got, aired_early, said_early, station, record = asyncio.run(run())
        self.assertEqual(aired_early, 0,
                         "the DJ's opening aired before the caller spoke")
        self.assertEqual(said_early, 0,
                         "the intro aired before the caller spoke")
        self.assertEqual(len([g for g in got if b"voice_queue.push" in g]), 3,
                         "the parked opening never made it to air")
        self.assertEqual(r.pushed, 3)
        self.assertEqual(len(station.says), 2,
                         "intro and outro around the broadcast, no more")
        self.assertIn("coming on the air", station.says[0])
        # Conversation order holds: the parked hello airs first, then the
        # caller's word that opened the broadcast.
        aired = [t for t in record.tools if "aired turn" in t]
        self.assertEqual(len(aired), 3)
        self.assertIn("turn 1 (dj", aired[0])
        self.assertIn("turn 2 (caller", aired[1])
        self.assertIn("turn 3 (dj", aired[2])

    def test_a_caller_never_heard_airs_nothing_at_all(self):
        async def run():
            station = _FakeStation()
            record = _FakeRecord()
            r, got = self._relay(station=station, record=record,
                                 on_air_call_mode="heard")
            await r.open()
            await r.feed(self._feed_file("t1.wav"), "dj", 3.0)
            await r.feed(self._feed_file("t2.wav"), "dj", 2.0)
            await r.close("the call ended")
            return r, got, station, record

        r, got, station, record = asyncio.run(run())
        self.assertEqual([g for g in got if b"voice_queue.push" in g], [],
                         "an unopened broadcast aired a clip")
        self.assertEqual(station.says, [],
                         "an unopened broadcast spoke on the station")
        self.assertEqual(r.pushed, 0)
        self.assertTrue(any("never heard" in t for t in record.tools),
                        "the silent call left no trace in the record")


class _FakeVoiceSwitch:
    """The station's /settings surface, as onair/hush.py speaks to it: the
    stored value rides GET's `values.tts`, and a POST echoes the saved
    document back. `enabled=None` is a station older than the switch."""

    def __init__(self, enabled=True, post_status=200, sticks=True):
        self.enabled = enabled
        self.posts: list[dict] = []
        self.post_status = post_status
        self.sticks = sticks

    async def get(self, path):
        tts = {} if self.enabled is None else {"enabled": self.enabled}
        body = {"values": {"tts": tts}}
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                               json=lambda: body)

    async def post(self, path, json=None):
        self.posts.append(json)
        if self.post_status == 200 and self.sticks:
            self.enabled = json["tts"]["enabled"]
        body = {"saved": {"tts": ({"enabled": self.enabled}
                                  if self.sticks else {})}}
        return SimpleNamespace(status_code=self.post_status,
                               json=lambda: body, text="nope")

    async def aclose(self):
        pass


class _HushCase(_ChunkStore):
    """Temp marker store + a fake voice switch in place of the station."""

    def setUp(self):
        super().setUp()
        from onair import hush

        self.hush = hush
        self.switch = _FakeVoiceSwitch()
        self._old_client = hush._client
        hush._client = lambda: self.switch
        self.addCleanup(lambda: setattr(hush, "_client", self._old_client))

    def _engage(self, cfg=None, room="callin-t1"):
        asyncio.run(self.hush.engage(cfg or {}, room))

    def _tick(self, cfg=None):
        asyncio.run(self.hush.janitor_tick(cfg or {}))

    def _hushfile(self):
        return chunks.SERVE_DIR / "HUSH"


class TestQuietingClaimsBeforeItWrites(_HushCase):
    def test_engage_flips_the_switch_and_leaves_a_note_saying_whose(self):
        self._engage()
        self.assertEqual(self.switch.posts, [{"tts": {"enabled": False}}],
                         "the write must be the one-field merge, nothing more")
        self.assertFalse(self.switch.enabled)
        import json as _json

        state = _json.loads(self._hushfile().read_text())
        self.assertTrue(state["prior"], "only a switch that was ON is ours")
        self.assertTrue(state["verified"], "the POST echo is the verify")
        self.assertTrue((chunks.SERVE_DIR / "HUSH-CALL-callin-t1").exists())

    def test_a_second_call_rides_the_first_flip_without_more_traffic(self):
        self._engage(room="callin-a")
        posts = len(self.switch.posts)
        self._engage(room="callin-b")
        self.assertEqual(len(self.switch.posts), posts,
                         "a sibling call re-wrote a switch already down")
        self.assertTrue((chunks.SERVE_DIR / "HUSH-CALL-callin-b").exists(),
                        "the sibling still needs its own marker")


class TestTheOperatorOutranksTheHush(_HushCase):
    def test_a_station_the_operator_muted_is_left_entirely_alone(self):
        self.switch.enabled = False
        self._engage()
        self.assertEqual(self.switch.posts, [])
        self.assertFalse(self._hushfile().exists(),
                         "no claim means the janitor will never write either")

    def test_a_station_too_old_for_the_switch_is_not_written_to(self):
        self.switch.enabled = None
        self._engage()
        self.assertEqual(self.switch.posts, [])
        self.assertFalse(self._hushfile().exists())
        verdict = (chunks.SERVE_DIR / "HUSH-VERDICT").read_text()
        self.assertIn("SUB/WAVE", verdict,
                      "the panel must be told why, not shown a silent no-op")

    def test_an_operator_flip_back_on_mid_call_is_respected(self):
        self._engage()
        self.switch.enabled = True          # their hand, in the station admin
        self.switch.posts.clear()
        self._tick()                        # call marker still fresh: no-op
        (chunks.SERVE_DIR / "HUSH-CALL-callin-t1").unlink()
        self._tick()                        # call over: restore would run now
        self.assertEqual(self.switch.posts, [],
                         "the restore wrote over the operator's own choice")
        self.assertFalse(self._hushfile().exists(),
                         "standing down still clears the claim")


class TestQuietingNeverBlocksTheCall(_HushCase):
    def test_no_credentials_means_a_verdict_not_an_error(self):
        self.hush._client = lambda: None
        self._engage()                       # must not raise
        self.assertFalse(self._hushfile().exists())
        self.assertIn("credentials",
                      (chunks.SERVE_DIR / "HUSH-VERDICT").read_text())

    def test_a_dead_station_costs_the_caller_nothing(self):
        async def boom(path):
            raise OSError("connection refused")

        self.switch.get = boom
        self._engage()                       # must not raise
        self.assertFalse(self._hushfile().exists())


class TestTheJanitorIsTheOneRestorer(_HushCase):
    def test_the_switch_stays_down_while_any_call_marker_is_fresh(self):
        self._engage()
        self.switch.posts.clear()
        self._tick()
        self.assertEqual(self.switch.posts, [])
        self.assertTrue(self._hushfile().exists())

    def test_an_unconfirmed_flip_is_finished_while_the_call_lives(self):
        # The worker wrote into a station mid-restart: claimed, unverified.
        self.switch.sticks = False
        self._engage()
        self.switch.sticks = True
        self.switch.enabled = True           # the restart lost the write
        self.switch.posts.clear()
        self._tick()
        self.assertEqual(self.switch.posts, [{"tts": {"enabled": False}}])
        import json as _json

        self.assertTrue(_json.loads(self._hushfile().read_text())["verified"])

    def test_the_last_call_out_restores_the_voice(self):
        self._engage()
        self.hush.call_ended("callin-t1")
        self.switch.posts.clear()
        self._tick()
        self.assertEqual(self.switch.posts, [{"tts": {"enabled": True}}])
        self.assertTrue(self.switch.enabled)
        self.assertFalse(self._hushfile().exists())

    def test_a_restore_the_station_missed_is_retried_not_forgotten(self):
        self._engage()
        self.hush.call_ended("callin-t1")
        self.switch.post_status = 500
        self._tick()
        self.assertTrue(self._hushfile().exists(),
                        "a failed restore dropped the claim — nothing would retry")
        self.switch.post_status = 200
        self._tick()
        self.assertTrue(self.switch.enabled)
        self.assertFalse(self._hushfile().exists())


class TestACrashedCallCannotMuteTheStation(_HushCase):
    def test_a_marker_nobody_heartbeats_goes_stale_and_the_voice_returns(self):
        self._engage()
        marker = chunks.SERVE_DIR / "HUSH-CALL-callin-t1"
        stale = time.time() - self.hush.CALL_FRESH_SECS - 5
        os.utime(marker, (stale, stale))
        self.switch.posts.clear()
        self._tick()
        self.assertTrue(self.switch.enabled, "the dead job held the station mute")
        self.assertFalse(marker.exists(), "the spent marker must not linger")
        self.assertFalse(self._hushfile().exists())

    def test_a_boot_finds_an_orphaned_claim_and_restores(self):
        # The whole stack died mid-call: HUSH on disk, no markers, switch down.
        self._engage()
        self.hush.call_ended("callin-t1")
        self._tick()                         # this IS the first tick after boot
        self.assertTrue(self.switch.enabled)


class TestHushMarkersAreScopedAndSafe(_HushCase):
    def test_scope_reads_the_setting_and_nonsense_reads_as_off(self):
        self.assertEqual(self.hush.scope({}), "off")
        self.assertEqual(self.hush.scope({"quiet_station_on_calls": "all"}), "all")
        self.assertEqual(self.hush.scope({"quiet_station_on_calls": "on_air"}),
                         "on_air")
        self.assertEqual(self.hush.scope({"quiet_station_on_calls": "sideways"}),
                         "off")

    def test_a_hostile_room_name_cannot_walk_the_store(self):
        self._engage(room="../../etc/passwd")
        for path in chunks.SERVE_DIR.glob("HUSH-CALL-*"):
            self.assertEqual(path.parent, chunks.SERVE_DIR)
        self.hush.call_ended("../../etc/passwd")   # and removal finds it too
        self.assertEqual(list(chunks.SERVE_DIR.glob("HUSH-CALL-*")), [])

    def test_ending_a_call_that_never_marked_is_quiet(self):
        self.hush.call_ended("callin-never-existed")   # must not raise
        self.assertEqual(list(chunks.SERVE_DIR.glob("HUSH-CALL-*")), [],
                         "removing nothing must also CREATE nothing")


class TestTheLiveVerdictTellsThePanelTheTruth(_HushCase):
    def test_off_is_none_so_the_panel_paints_nothing(self):
        self.assertIsNone(self.hush.live_verdict({}))

    def test_missing_credentials_read_as_not_ok_with_the_why(self):
        import station_config

        old = station_config.has_admin
        station_config.has_admin = lambda: False
        self.addCleanup(lambda: setattr(station_config, "has_admin", old))
        v = self.hush.live_verdict({"quiet_station_on_calls": "all"})
        self.assertFalse(v["ok"])
        self.assertIn("credentials", v["why"])

    def test_a_working_flip_reads_ok_and_quieted(self):
        import station_config

        old = station_config.has_admin
        station_config.has_admin = lambda: True
        self.addCleanup(lambda: setattr(station_config, "has_admin", old))
        self._engage()
        v = self.hush.live_verdict({"quiet_station_on_calls": "on_air"})
        self.assertTrue(v["ok"])
        self.assertTrue(v["quieted"])
        self.assertEqual(v["scope"], "on_air")


class TestSessionWiringForHush(unittest.TestCase):
    """Source pins on call/session.py: the marker's two owners and the
    playout ordering are load-bearing (shutdown callbacks run CONCURRENTLY),
    and nothing at runtime would fail loudly if the wiring quietly left."""

    def setUp(self):
        from tests.support import AGENT_WORKER

        self.src = (AGENT_WORKER / "call"
                    / "session.py").read_text(encoding="utf-8")

    def test_the_sweep_is_registered_before_anything_can_raise(self):
        self.assertIn("ctx.add_shutdown_callback(self._hush_sweep)", self.src)

    def test_the_started_call_drops_its_marker_after_the_playout(self):
        shutdown = self.src.split("async def _on_shutdown", 1)[1]
        aclose = shutdown.index("self.station.aclose()")
        ended = shutdown.index("hush.call_ended(self.room_name)")
        self.assertGreater(ended, aclose,
                           "the marker fell before the tape finished airing")

    def test_the_shutdown_carries_its_own_beat_and_stops_it_before_the_drop(self):
        # The staleness ceiling is minutes (hush.CALL_FRESH_SECS), which is
        # only safe because the drain and the tape playout run under this
        # beat — and the beat must die BEFORE the unlink, or it resurrects
        # the marker as an orphan the janitor then waits the ceiling out on.
        shutdown = self.src.split("async def _on_shutdown", 1)[1]
        beat = shutdown.index("hush.heartbeat(self.room_name)")
        work = shutdown.index("await self._shutdown_work()")
        cancel = shutdown.index("lifecycle.cancel(beat)")
        ended = shutdown.index("hush.call_ended(self.room_name)")
        self.assertLess(beat, work, "the beat must start before the drain")
        self.assertLess(cancel, ended, "the beat outlived the marker")

    def test_the_ceiling_tolerates_a_swap_stalled_worker(self):
        # 180/20 = 9 missed beats. A NAS deep in swap can stall a container
        # for tens of seconds; a tight ratio would read a slow call as a
        # dead one and un-quiet the station over a live conversation.
        from onair import hush

        self.assertGreaterEqual(hush.CALL_FRESH_SECS / hush.HEARTBEAT_SECS, 5,
                                "the ceiling no longer tolerates missed beats")

    def test_the_heartbeat_rides_only_calls_that_quieted(self):
        self.assertIn('hush.scope(cfg) == "all"', self.src)
        self.assertIn('hush.scope(cfg) == "on_air"', self.src)
        self.assertIn("hush.heartbeat(self.room_name)", self.src)


if __name__ == "__main__":
    unittest.main()
