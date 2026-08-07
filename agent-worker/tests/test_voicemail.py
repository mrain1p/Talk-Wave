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


class TestAVoicemailIsACallEntryToo(_VmDirs):
    """The operator's shape: messages appear in Recent calls alongside live
    calls, labelled as the machine's, transcript only — while the Voicemail
    section's list stays the working queue."""

    def test_the_entry_is_written_and_labelled(self):
        import json
        import os

        from call import record as call_record
        from voicemail.capture import write_call_entry

        import time

        # A live clock, not a fixed one: write() prunes to the newest N, and
        # a record stamped in the past is the first thing pruned when the
        # shared CALLS_PATH already holds a suite's worth of newer ones.
        write_call_entry("vm-g-abc123def456", {"id": "p1", "name": "Danny"},
                         {"record_calls": True, "record_keep": 0},
                         "play some Bowie", "held for the operator",
                         time.time())
        # Matched by the room's hex suffix, the same way /call-feedback finds
        # a record — CALLS_PATH is shared across the whole suite run, and
        # "the newest file" is whichever unrelated test wrote last.
        files = [f for f in call_record.CALLS_DIR.glob("*.json")
                 if "abc123def456" in f.name]
        self.assertTrue(files, "no call entry written")
        data = json.loads(files[-1].read_text(encoding="utf-8"))
        self.assertEqual("voicemail", data.get("kind"))
        self.assertEqual("guest", data["config"]["callerTier"])
        self.assertEqual("caller", data["turns"][-1]["who"])
        self.assertIn("Bowie", data["turns"][-1]["text"])
        self.assertEqual("voicemail_delivery", data["tools"][0]["name"])
        for f in files:
            os.unlink(f)

    def test_recording_off_means_no_entry(self):
        from call import record as call_record
        from voicemail.capture import write_call_entry

        before = len(list(call_record.CALLS_DIR.glob("*.json")))
        write_call_entry("vm-o-abc123def456", {}, {"record_calls": False},
                         "hello", "held", 1754500000.0)
        self.assertEqual(before,
                         len(list(call_record.CALLS_DIR.glob("*.json"))))

    def test_the_viewer_labels_it(self):
        from tests.support import REPO

        viewers = (REPO / "web-widget" / "panel-viewers.js").read_text(encoding="utf-8")
        self.assertIn("voicemail", viewers)


class TestEachPersonaCanHaveItsOwnLine(_VmDirs):
    """The operator edits one persona's greeting in place; the cache key
    carries the text, so only that clip re-renders."""

    def test_override_wins_and_clears(self):
        g = self.greetings
        base = g.greeting_text_for("p1", {}, "Yosemite FM", "Danny")
        self.assertIn("Danny", base)
        g.set_override("p1", "Danny here — say your piece.")
        self.assertEqual("Danny here — say your piece.",
                         g.greeting_text_for("p1", {}, "Yosemite FM", "Danny"))
        # Another persona is untouched.
        self.assertIn("Rosie", g.greeting_text_for("p2", {}, "x", "Rosie"))
        g.set_override("p1", "")
        self.assertEqual(base, g.greeting_text_for("p1", {}, "Yosemite FM", "Danny"))


class TestTheMachineHasATierDoor(unittest.TestCase):
    """allow_voicemail is a caller tier like every other permission: 'off'
    admits nobody, and the ladder is the caller ladder."""

    def test_the_gate_reads_the_setting_and_fails_closed(self):
        import inspect

        from api import tokens

        source = inspect.getsource(tokens.handle_token)
        self.assertIn("allow_voicemail", source)
        self.assertIn('"open": 0', source)
        # An unknown value must land on the refusing branch.
        self.assertIn("need not in ladder", source)

    def test_the_defaults_leave_upgrades_unchanged(self):
        import settings as settings_store

        self.assertEqual("open", settings_store.FIELDS["allow_voicemail"][1])
        self.assertEqual(0, settings_store.FIELDS["guest_session_minutes"][1])
        self.assertFalse(settings_store.FIELDS["show_voicemail_button"][1])
        self.assertFalse(settings_store.FIELDS["embed_voicemail_button"][1])


class TestTheStationAnswersWhenNobodyIsOnAir(_VmDirs):
    """A named DJ who is not actually there is a small lie the caller can
    hear. With no persona, the machine speaks as the station itself, and the
    greeting templates take {station}, {dj} and {show} without a typo ever
    crashing a pickup into the beep."""

    def test_no_dj_means_the_station_greeting(self):
        text = self.greetings.greeting_text({}, "Yosemite FM", "")
        self.assertIn("Yosemite FM", text)
        self.assertNotIn("on the air", text)

    def test_placeholders_fill_and_unknowns_vanish(self):
        cfg = {"voicemail_greeting":
               "You are through to {show} on {station}. {dj} cannot pick up. {typo}"}
        text = self.greetings.greeting_text(cfg, "Yosemite FM", "Danny",
                                            "The Night Shift")
        self.assertIn("The Night Shift", text)
        self.assertIn("Danny", text)
        self.assertNotIn("{", text)
        self.assertNotIn("  ", text)

    def test_the_station_clip_beats_a_strangers_voice(self):
        g = self.greetings
        g.write_clip("other", "k", "hi", "v", b"\x00\x00" * 240, 24000)
        g.write_clip(g.STATION_ID, "k", "hi", "v", b"\x00\x00" * 240, 24000)
        self.assertEqual(g.clip_path(g.STATION_ID),
                         g.staged_clip("nobody-home"))


class TestTheLineHasModes(unittest.TestCase):
    """Take live calls and the voicemail switch together are the line's mode:
    phone, phone with a machine, voicemail-only, or closed. The half that can
    rot silently is the refusal — a hand-built client asking for a live call
    on a voicemail-only line must get the same answer the widget shows."""

    def test_a_voicemail_only_line_refuses_a_live_mint(self):
        import inspect

        from api import tokens

        source = inspect.getsource(tokens.handle_token)
        gate = source[source.index("live_calls_enabled") - 400:]
        self.assertIn('voicemail_when', gate.split("live_calls_enabled")[0],
                      "the two switches must be one gate — either alone "
                      "refusing would strand the other's mode")
        self.assertIn("taking messages tonight", gate)

    def test_the_defaults_leave_upgrades_unchanged(self):
        import settings as settings_store

        self.assertTrue(settings_store.FIELDS["live_calls_enabled"][1])
        self.assertEqual("staged",
                         settings_store.FIELDS["voicemail_greeting_mode"][1])

    def test_the_card_is_told_which_line_it_is(self):
        from api.live import look_payload

        self.assertFalse(
            look_payload({"live_calls_enabled": False}, "X")["liveCalls"])
        self.assertTrue(look_payload({}, "X")["liveCalls"])


class TestAFreshGreetingIsBudgeted(unittest.TestCase):
    """'Fresh each call' is a model line plus a TTS render at pickup — the
    exact cost staging exists to avoid — so the caller of it owns a hard
    clock, and anything short of success falls back to the staged clip."""

    def test_the_fresh_branch_has_a_clock(self):
        import inspect

        from voicemail import capture

        source = inspect.getsource(capture.answer)
        branch = source[source.index("voicemail_greeting_mode"):]
        self.assertIn("asyncio.wait_for", branch)
        self.assertIn("_fresh_greeting", branch)
        # The fallback order survives: a missed budget still reaches the
        # staged clip, never straight to the beep.
        self.assertIn("staged_clip", branch)
        self.assertLess(branch.index("asyncio.wait_for"),
                        branch.index("staged_clip"))

    def test_a_strange_model_answer_falls_back_to_the_template(self):
        import inspect

        from voicemail import capture

        source = inspect.getsource(capture._fresh_greeting)
        self.assertIn("greeting_text_for", source,
                      "the template must be the floor under the model line")
        self.assertIn("8 <= len(line)", source)


class TestTheCeilingActuallyHangsUp(unittest.TestCase):
    """The message loop always stopped at the ceiling, but ctx.shutdown()
    alone ends the JOB, not the room — the caller stayed connected to an
    agent-less room with the timer counting past 30 seconds, which the
    operator read (correctly) as the limit not being honored."""

    def test_the_vm_leg_deletes_the_room(self):
        import inspect

        from voicemail import capture

        source = inspect.getsource(capture.answer)
        self.assertIn("delete_room", source)
        self.assertLess(source.index("delete_room"),
                        source.index('ctx.shutdown(reason="voicemail'))

    def test_the_card_counts_against_the_machines_clock(self):
        from tests.support import AGENT_WORKER, REPO

        js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        timer = js[js.index("function startTimer"):][:500]
        self.assertIn("voicemailMaxSeconds", timer)
        self.assertIn("vmCall", timer)
        live_py = (AGENT_WORKER / "api" / "live.py").read_text(encoding="utf-8")
        self.assertIn("voicemailMaxSeconds", live_py)


class TestTheBeepCanBeTheOperators(unittest.TestCase):
    """sound_vm_beep is the one server-played sound: uploads only, and
    anything unplayable falls back to the tone — never to silence, because
    the line is open and a caller is waiting to be told to speak."""

    def test_upload_only_and_missing_fails_soft(self):
        from voicemail import capture

        self.assertIsNone(capture.custom_beep({}, 24000))
        self.assertIsNone(capture.custom_beep(
            {"sound_vm_beep": "https://x/beep.wav"}, 24000))
        self.assertIsNone(capture.custom_beep(
            {"sound_vm_beep": "upload:not-there.wav"}, 24000))

    def test_an_ordinary_wav_is_converted_not_rejected(self):
        # The first beep uploaded in the wild was 44.1kHz — and rejecting it
        # for its rate produced the worst kind of failure: the tone played,
        # nothing said why, and the setting looked ignored. Stereo, 8-bit
        # and off-rate files all convert; only what wave can't read at all
        # falls back to the tone.
        import struct as _struct
        import tempfile
        import wave as _wave

        from api import sounds as api_sounds
        from voicemail import capture

        tmp = Path(tempfile.mkdtemp())
        old = api_sounds.SOUNDS_DIR
        api_sounds.SOUNDS_DIR = tmp
        try:
            with _wave.open(str(tmp / "beep.wav"), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(44100)
                w.writeframes(_struct.pack("<%dh" % (44100 * 2),
                                           *([3000, -3000] * 44100)))
            cfg = {"sound_vm_beep": "upload:beep.wav"}
            out = capture.custom_beep(cfg, 24000)
            self.assertTrue(out)
            # One second of stereo 44.1k comes out as one second of mono
            # 16-bit at the line's rate, within resampling slack.
            self.assertAlmostEqual(len(out), 24000 * 2, delta=200)

            (tmp / "fake.wav").write_bytes(b"ID3\x04not a wav at all")
            self.assertIsNone(capture.custom_beep(
                {"sound_vm_beep": "upload:fake.wav"}, 24000))
        finally:
            api_sounds.SOUNDS_DIR = old

    def test_a_long_file_is_capped(self):
        # A beep that runs for minutes is a jingle holding the line hostage.
        import tempfile
        import wave as _wave

        from api import sounds as api_sounds
        from voicemail import capture

        tmp = Path(tempfile.mkdtemp())
        old = api_sounds.SOUNDS_DIR
        api_sounds.SOUNDS_DIR = tmp
        try:
            with _wave.open(str(tmp / "long.wav"), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(b"\x00\x01" * (24000 * 20))
            out = capture.custom_beep(
                {"sound_vm_beep": "upload:long.wav"}, 24000)
            self.assertLessEqual(len(out), 24000 * 2 * 8 + 200)
        finally:
            api_sounds.SOUNDS_DIR = old

    def test_no_verdict_buttons_after_a_voicemail(self):
        # "How was it?" over "Message left" read as the machine fishing for
        # a compliment — there was no conversation to rate.
        from tests.support import REPO

        js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertIn("if (!wasVm) offerFeedback", js)

    def test_the_beep_dropdown_says_what_the_default_is(self):
        # "Sound set default" answered the wrong question: the operator
        # asked WHICH sound that is. The beep's default is no set's — it is
        # synthesized by the server — and the label has to say so.
        from tests.support import REPO

        js = (REPO / "web-widget" / "panel.js").read_text(encoding="utf-8")
        self.assertIn("Classic tone — synthesized (default)", js)
        self.assertIn("'vm_beep'", js.split("SOUND_SLOTS = ")[1][:120])
