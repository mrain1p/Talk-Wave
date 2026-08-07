"""Voicemail: the answering machine's promises, held to.

The design constraint that shapes every test here: nothing is recorded as
audio, a missing clip must never mean a silent pickup, and a message the
station refuses must land in the operator's list rather than vanishing.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path


class _VmDirs(unittest.TestCase):
    """Point the voicemail stores at a temp dir, the _TempStores way."""

    def setUp(self):
        from voicemail import deliver, greetings

        self.greetings = greetings
        self.deliver = deliver
        self.tmp = Path(tempfile.mkdtemp())
        self._old_dir = greetings.VOICEMAIL_DIR
        self._old_msgs = deliver.MESSAGES_PATH
        greetings.VOICEMAIL_DIR = self.tmp
        deliver.MESSAGES_PATH = self.tmp / "messages.json"

    def tearDown(self):
        self.greetings.VOICEMAIL_DIR = self._old_dir
        self.deliver.MESSAGES_PATH = self._old_msgs


class TestGreetingClipsFollowWhatTheyWereRenderedFrom(_VmDirs):
    """The cache key is the operator's own design: re-render only when the
    inputs changed — the text, the voice, the backend — never per message."""

    def test_the_key_moves_with_every_input_and_nothing_else(self):
        g = self.greetings
        base = g.render_key("hello", "-Cliff1", "local", "a.json")
        self.assertEqual(base, g.render_key("hello", "-Cliff1", "local", "a.json"))
        for changed in (
            g.render_key("hullo", "-Cliff1", "local", "a.json"),
            g.render_key("hello", "Lily", "local", "a.json"),
            g.render_key("hello", "-Cliff1", "cloud", "a.json"),
            g.render_key("hello", "-Cliff1", "local", "b.json"),
        ):
            self.assertNotEqual(base, changed)

    def test_a_written_clip_is_current_until_its_inputs_change(self):
        g = self.greetings
        key = g.render_key("hello", "v", "local", "")
        self.assertTrue(g.needs_render("p1", key))
        g.write_clip("p1", key, "hello", "v", b"\x00\x00" * 240, 24000)
        self.assertFalse(g.needs_render("p1", key))
        self.assertTrue(g.needs_render("p1", g.render_key("new", "v", "local", "")))

    def test_a_blank_greeting_is_the_derived_sentence_not_silence(self):
        g = self.greetings
        text = g.greeting_text({}, "Yosemite FM", "Danny Boy")
        self.assertIn("Yosemite FM", text)
        self.assertIn("Danny Boy", text)
        self.assertIn("beep", text)
        self.assertEqual("my words",
                         g.greeting_text({"voicemail_greeting": "my words"},
                                         "x", "y"))

    def test_a_missing_clip_falls_back_to_any_staged_voice(self):
        # A wrong voice beats silence — and the ack clips must not be
        # mistaken for greetings by the fallback.
        g = self.greetings
        self.assertIsNone(g.staged_clip("p9"))
        g.write_clip("p1", "k", "hello", "v", b"\x00\x00" * 240, 24000)
        (self.tmp / "p1-ack.wav").write_bytes(b"RIFF")
        fallback = g.staged_clip("p9")
        self.assertIsNotNone(fallback)
        self.assertFalse(fallback.name.endswith("-ack.wav"))

    def test_a_roster_change_drops_the_old_voice(self):
        g = self.greetings
        g.write_clip("gone", "k", "hello", "v", b"\x00\x00" * 240, 24000)
        g.write_clip("stays", "k", "hello", "v", b"\x00\x00" * 240, 24000)
        (self.tmp / "stays-ack.wav").write_bytes(b"RIFF")
        g.drop_stale(["stays"])
        self.assertFalse(g.clip_path("gone").is_file(),
                         "a stranger's old voice must not answer the phone")
        self.assertTrue(g.clip_path("stays").is_file())
        self.assertTrue((self.tmp / "stays-ack.wav").is_file(),
                        "the ack clip belongs to a persona that still exists")

    def test_the_clip_is_a_wav_that_carries_its_own_rate(self):
        from voicemail.capture import read_wav

        g = self.greetings
        g.write_clip("p1", "k", "hello", "v", b"\x01\x02" * 480, 22050)
        pcm, rate = read_wav(g.clip_path("p1"))
        self.assertEqual(22050, rate)
        self.assertEqual(b"\x01\x02" * 480, pcm)


class _FakeStation:
    def __init__(self, fail=False):
        self.fail = fail
        self.said = []
        self.requests = []

    async def submit_request(self, text, name=""):
        if self.fail:
            raise RuntimeError("station said no")
        self.requests.append(text)
        return {"message": "queued third"}

    async def dj_say(self, text, mode="styled", kind="callin"):
        if self.fail:
            raise RuntimeError("station said no")
        self.said.append((text, kind))
        return {"ok": True}


class TestAMessageIsNeverLost(_VmDirs):
    """Whatever the delivery mode and whatever the station does, the message
    lands in the operator's list — held is the floor, not a branch."""

    def _deliver(self, cfg, station, text="play some Bowie"):
        return asyncio.run(self.deliver.deliver(station, cfg, text, "Danny"))

    def test_hold_is_the_default_and_keeps_the_text(self):
        receipt = self._deliver({}, _FakeStation())
        self.assertIn("held", receipt)
        msgs = self.deliver.held_messages()
        self.assertEqual(1, len(msgs))
        self.assertEqual("play some Bowie", msgs[0]["text"])

    def test_a_request_rides_the_station_and_still_lands_in_the_list(self):
        station = _FakeStation()
        receipt = self._deliver({"voicemail_destination": "request"}, station)
        self.assertIn("request", receipt)
        self.assertEqual(["play some Bowie"], station.requests)
        self.assertEqual("request", self.deliver.held_messages()[0]["delivered"])

    def test_a_refused_request_is_held_not_lost(self):
        receipt = self._deliver({"voicemail_destination": "request"},
                                _FakeStation(fail=True))
        self.assertIn("held", receipt)
        msgs = self.deliver.held_messages()
        self.assertEqual("hold", msgs[0]["delivered"])
        self.assertIn("failed", msgs[0]["note"])

    def test_air_goes_out_with_its_own_kind(self):
        station = _FakeStation()
        self._deliver({"voicemail_destination": "air"}, station)
        self.assertEqual(1, len(station.said))
        self.assertEqual("voicemail", station.said[0][1])

    def test_the_list_cannot_grow_without_bound(self):
        for i in range(self.deliver.MAX_MESSAGES + 25):
            self.deliver.hold(f"m{i}", "dj")
        msgs = self.deliver.held_messages()
        self.assertEqual(self.deliver.MAX_MESSAGES, len(msgs))
        self.assertEqual(f"m{self.deliver.MAX_MESSAGES + 24}", msgs[-1]["text"])


class TestTheMachineAnswersThroughTheRightRefusals(unittest.TestCase):
    """Voicemail exists FOR the closed line: paused and lines-busy must not
    close it, while the caller cooldown and the daily ceilings still do. The
    match is on the caller-facing wording, so this pins both sides."""

    def test_the_line_state_refusals_are_the_ones_matched(self):
        from api import tokens

        self.assertTrue(tokens._refusal_is_line_state(
            "The booth isn't taking calls at the moment — the line's closed for now."))
        self.assertTrue(tokens._refusal_is_line_state(
            "The booth line is tied up with another caller. Give it a minute and try again."))
        for still_refused in (
            "You've only just hung up — give it 30s before ringing back.",
            "The switchboard has been lit up this hour. Try the booth again a little later.",
        ):
            self.assertFalse(tokens._refusal_is_line_state(still_refused))

    def test_the_wording_this_matches_is_the_wording_check_usage_uses(self):
        # The match is textual, so the strings live in two places; this is
        # the test that fails if either side is reworded alone.
        import inspect

        from api import tokens

        source = inspect.getsource(tokens._check_usage)
        self.assertIn("line's closed", source)
        self.assertIn("tied up", source)


class TestTheBeepIsRealAudio(unittest.TestCase):
    def test_shape_and_length(self):
        from voicemail.capture import beep_pcm

        pcm = beep_pcm(24000)
        self.assertEqual(int(24000 * 0.4) * 2, len(pcm))
        self.assertTrue(any(b for b in pcm), "a beep of zeros is silence")
