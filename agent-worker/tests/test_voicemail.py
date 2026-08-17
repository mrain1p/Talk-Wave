"""Voicemail: the answering machine's promises, held to.

The design constraints that shape every test here: a missing clip must never
mean a silent pickup, and a message the station refuses must land in the
operator's list rather than vanishing. The classic machine records nothing as
audio; the soundbite flow (review.py, master.py) deliberately reverses that —
it HOLDS a caller's clip so it can be aired — and the tests below hold it to
the terms of that reversal: briefly, deletably, and never as an orphan.
"""

from __future__ import annotations

import asyncio
import math
import struct
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
    """Voicemail exists FOR the refusals a live call meets: lines-busy must
    not close it, while the kill switch, the caller cooldown and the daily
    ceilings still do. Paused used to answer through too, until the operator
    drew the hierarchy: the kill switch is the LINE, and both transmission
    modes hang off it — a paused line that still took messages made the
    dashboard's one big switch a lie. The match is on the caller-facing
    wording, so this pins both sides."""

    def test_the_line_state_refusals_are_the_ones_matched(self):
        from api import tokens

        self.assertTrue(tokens._refusal_is_line_state(
            "The booth line is tied up with another caller. Give it a minute and try again."))
        for still_refused in (
            "The booth isn't taking calls at the moment — the line's closed for now.",
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
        # The ladder moved to settings.tier_reaches (0.10.20); fail-closed
        # is TestTheLadderLivesInOnePlace's claim now.
        self.assertIn("tier_reaches", source)

    def test_the_defaults_leave_upgrades_unchanged(self):
        import settings as settings_store

        self.assertEqual("open", settings_store.FIELDS["allow_voicemail"][1])
        self.assertEqual(24, settings_store.FIELDS["guest_session_hours"][1])
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
        self.assertIn('voicemail_policy', gate.split("live_calls_enabled")[0],
                      "the gate must read the RESOLVED policy — the master "
                      "switch and the when-select through one resolver, or "
                      "the two call sites drift")
        self.assertIn("taking messages tonight", gate)

    def test_the_defaults_leave_upgrades_unchanged(self):
        import settings as settings_store

        self.assertTrue(settings_store.FIELDS["live_calls_enabled"][1])
        # Fresh since 0.10.80 (the operator's fresh-install review): written
        # in persona at pickup, staged clip as the instant fallback — safe to
        # default because the fallback ladder means a slow backend still
        # answers promptly (TestAFreshGreetingIsBudgeted holds that line).
        self.assertEqual("fresh",
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

    def test_the_voicemail_verdict_is_the_operators_choice(self):
        # This used to pin "no verdict buttons after a voicemail" — the fear
        # being the machine fishing for a compliment over "Message left". In
        # 0.10.48 the operator asked for thumbs per door, so the claim moves:
        # the machine may ask, but ONLY behind its own switch (never the
        # call's), and only when a message was actually left.
        from tests.support import REPO

        js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertIn("if (wasVm) {", js)
        self.assertIn("showVmReceipt();", js)
        vm_half = js.split("if (wasVm) {", 1)[1].split("} else {", 1)[0]
        self.assertIn("askVmFeedback", vm_half)
        self.assertNotIn("live.askFeedback", vm_half)
        self.assertIn(".cap.you", vm_half)      # only after a message left
        # The call branch still reads the call's own switch, not the machine's.
        else_half = js.split("if (wasVm) {", 1)[1].split("} else {", 1)[1]
        self.assertIn("live.askFeedback", else_half.split("}", 1)[0])

    def test_the_beep_dropdown_says_what_the_default_is(self):
        # "Sound set default" answered the wrong question: the operator
        # asked WHICH sound that is. The beep's default is no set's — it is
        # synthesized by the server — and the label has to say so.
        from tests.support import REPO

        js = (REPO / "web-widget" / "panel-sounds.js").read_text(encoding="utf-8")
        self.assertIn("Classic tone — synthesized (default)", js)
        self.assertIn("'vm_beep'", js.split("SOUND_SLOTS = ")[1][:120])


class TestTheBeepVerdictIsVisible(unittest.TestCase):
    """The worker fails an unplayable beep to the tone SILENTLY — correct on
    a live pickup, maddening from the panel, where the setting just looks
    ignored. The status endpoint tries the real conversion and says so."""

    def test_the_status_payload_carries_the_verdict(self):
        import inspect

        from api import voicemail as api_vm

        source = inspect.getsource(api_vm.handle_voicemail_status)
        self.assertIn("_wav_as_mono16", source)
        self.assertIn('"beep"', source)

    def test_the_panel_paints_it(self):
        from tests.support import REPO

        js = (REPO / "web-widget" / "panel.js").read_text(encoding="utf-8")
        self.assertIn("vmBeepNote", js)
        self.assertIn("cannot play", js)


class TestTheDjOnlySpeaksOnce(unittest.TestCase):
    """A mid-call reconnect re-fires TrackSubscribed; attaching again
    without tearing down the first element left two playbacks of the same
    voice a few ms apart — 'the DJ speaking twice, slightly off sync',
    reported from a live call."""

    def test_the_pickup_handler_is_reentrant(self):
        from tests.support import REPO

        js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        handler = js.split("RoomEvent.TrackSubscribed")[1]
        before_attach = handler[:handler.index("track.attach()")]
        self.assertIn("djEl.srcObject = null", before_attach)
        self.assertIn("dropEffect()", before_attach)


class TestTheBeepIsACueNotAGate(unittest.TestCase):
    """Both halves are operator reports. The quiet clock restarts at the
    beep (it once ran from before the greeting, and the machine hung up the
    moment it beeped); the mic does NOT wait for it — the widget's old
    mic-until-the-beep gate threw away everything said over the greeting,
    and real messages arrived as their last two words."""

    def test_the_quiet_clock_restarts_at_the_beep(self):
        import inspect

        from voicemail import capture

        source = inspect.getsource(capture.answer)
        beep_call = source.index("custom_beep(cfg, rate) or beep_pcm(rate)")
        reset = source.index("last_event.update(at=time.monotonic()", beep_call)
        loop = source.index("while True:", beep_call)
        self.assertLess(reset, loop,
                        "the quiet clock must restart after the beep and "
                        "before the bounded loop")

    def test_the_worker_announces_the_beep(self):
        import inspect

        from voicemail import capture

        self.assertIn('topic="vm-beep"', inspect.getsource(capture.answer))

    def test_the_delivery_receipt_obeys_the_receipts_setting(self):
        # action_cards went booth-wide in 0.10.92: "off" withholds the
        # machine's "Message delivered" card too. The gate has to sit ABOVE
        # the publish — the delivery itself and the held message must never
        # depend on whether the caller is shown the paperwork.
        import inspect

        from voicemail import capture

        src = inspect.getsource(capture.answer)
        deliver = src.index("vm_deliver.deliver(")
        gate = src.index('cfg.get("action_cards")')
        publish = src.index('"Message delivered"')
        self.assertLess(deliver, gate,
                        "delivery must not be inside the receipt gate")
        self.assertLess(gate, publish,
                        "the receipt publish must sit behind the setting")

    def test_the_caller_sees_the_machine_hearing_them(self):
        # A voicemail card sat silent while someone spoke — no sign a word was
        # registering (operator, 2026-08-10). The worker now publishes what it
        # hears on its own topic; the widget renders it as the caller's line.
        import inspect

        from voicemail import capture

        from tests.support import REPO

        src = inspect.getsource(capture.answer)
        self.assertIn('topic="vm-heard"', src)
        widget = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertIn("'vm-heard'", widget)

    def test_the_widget_listens_from_pickup(self):
        # The beep used to GATE the caller's mic, so everything said over
        # the greeting was thrown away — the operator's real messages
        # arrived as their last two words ("…thank you."). Mic live from
        # pickup now, like the worker's STT always was; the beep is a cue.
        from tests.support import REPO

        js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertIn("'vm-beep'", js)          # still announced, still heard
        # Voicemail is push-to-talk now (operator's ask): the mic-close after
        # connect applies to it too, so the !vmCall guard is gone. The
        # regression this STILL defends is the one that made voicemail open-mic
        # in the first place — a closed mic with NO visible control ("MIC OFF,
        # nothing to hold", empty message). The close is safe now only because
        # the bar is present and a TAP latches the mic open, leaving a message
        # exactly like an open mic. See the pickup branch and the CSS below.
        after_mic = js.split("setMicrophoneEnabled(true);", 1)[1][:700]
        self.assertIn("pttOn() && !pttOpen", after_mic)
        self.assertNotIn("!vmCall && pttOn()", after_mic)
        # The voicemail pickup keeps the bar when PTT is on (only an open-mic
        # card, PTT switched off, drops it) — so the closed mic has a control.
        self.assertIn("if (pttOn()) {", js)
        # And the beep handler never forces the mic open (that would un-mute a
        # caller who pressed Mute during the greeting).
        self.assertNotIn("setMicOpen(true)",
                         js.split("topic !== 'vm-beep'", 1)[1][:900])

    def test_a_thinking_pause_does_not_end_the_message(self):
        # 3.5s cut real messages off at the first pause for thought.
        from voicemail import capture

        self.assertGreaterEqual(capture._SETTLE_SECS, 6.0)


def _tone_wav(path: Path, freqs: list[float], secs: float, *, rate: int = 16000,
              channels: int = 1, gain: float = 0.5,
              lead_silence: float = 0.0, tail_silence: float = 0.0) -> None:
    """A synthetic 'caller': sine content between stretches of silence."""
    import wave

    def _samples(n_secs: float, loud: bool) -> list[int]:
        n = int(rate * n_secs)
        if not loud:
            return [0] * n
        out = []
        for i in range(n):
            v = sum(math.sin(2 * math.pi * f * i / rate) for f in freqs)
            out.append(int(32767 * gain * v / max(1, len(freqs))))
        return out

    body = (_samples(lead_silence, False) + _samples(secs, True)
            + _samples(tail_silence, False))
    frames = b"".join(struct.pack("<h", v) * channels for v in body)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)


def _tone_power(samples: list[int], freq: float, rate: int = 16000) -> float:
    """Energy at one frequency — a single DFT bin, enough to compare bands."""
    re = sum(v * math.cos(2 * math.pi * freq * i / rate)
             for i, v in enumerate(samples))
    im = sum(v * math.sin(2 * math.pi * freq * i / rate)
             for i, v in enumerate(samples))
    return (re * re + im * im) / max(1, len(samples)) ** 2


class TestMasteringMakesAClipTheAirCanCarry(unittest.TestCase):
    """The chain exists because the first caller clip ever aired was decoded
    end to end by the mixer and heard by nobody — speech's 18 dB crest factor
    let it sit 7 dB under the music while its peaks claimed it was loud."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _master(self, src: str, dst: str = "out.wav", max_secs: float = 30.0):
        from voicemail import master as m

        return m.master(self.tmp / src, self.tmp / dst, max_secs)

    def test_anything_in_becomes_the_one_format_the_air_takes(self):
        import wave

        # 44.1k stereo with dead air both sides — the shape a browser upload
        # actually has, not the shape the chain would prefer.
        _tone_wav(self.tmp / "in.wav", [440.0, 880.0], 1.0, rate=44100,
                  channels=2, lead_silence=0.6, tail_silence=0.8)
        stats = self._master("in.wav")
        with wave.open(str(self.tmp / "out.wav"), "rb") as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(),
                              w.getframerate()), (1, 2, 16000))
        # Trimmed to the speech plus margins, not the 2.4s the file held.
        self.assertLess(stats["seconds"], 1.6)
        self.assertGreater(stats["seconds"], 0.8)
        self.assertLessEqual(stats["peakDb"], -0.5)

    def test_input_level_does_not_change_the_outcome(self):
        # The systematic promise made to the operator: normalise INTO the
        # drive, so a quiet phone and a hot phone land at the same level and
        # only the noise floor differs.
        _tone_wav(self.tmp / "quiet.wav", [500.0], 1.0, gain=0.05)
        _tone_wav(self.tmp / "hot.wav", [500.0], 1.0, gain=0.9)
        quiet = self._master("quiet.wav", "q-out.wav")
        hot = self._master("hot.wav", "h-out.wav")
        self.assertLess(abs(quiet["rmsDb"] - hot["rmsDb"]), 1.5)

    def test_the_band_pass_keeps_the_voice_and_drops_the_fight(self):
        import wave

        # Rumble far louder than the voice going in; the voice must win
        # coming out — that reversal IS the band-pass doing its job.
        _tone_wav(self.tmp / "in.wav", [100.0], 1.5, gain=0.8)
        with wave.open(str(self.tmp / "in.wav"), "rb") as w:
            rumble_only = w.readframes(w.getnframes())
        _tone_wav(self.tmp / "voice.wav", [1000.0], 1.5, gain=0.2)
        with wave.open(str(self.tmp / "voice.wav"), "rb") as w:
            voice_only = w.readframes(w.getnframes())
        mixed = [a + b for a, b in
                 zip(struct.unpack("<%dh" % (len(rumble_only) // 2), rumble_only),
                     struct.unpack("<%dh" % (len(voice_only) // 2), voice_only))]
        with wave.open(str(self.tmp / "mix.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(struct.pack(
                "<%dh" % len(mixed),
                *[max(-32768, min(32767, v)) for v in mixed]))
        self._master("mix.wav")
        with wave.open(str(self.tmp / "out.wav"), "rb") as w:
            out = list(struct.unpack("<%dh" % (w.getnframes()),
                                     w.readframes(w.getnframes())))
        self.assertGreater(_tone_power(out, 1000.0),
                           _tone_power(out, 100.0) * 4)

    def test_silence_is_refused_not_aired(self):
        _tone_wav(self.tmp / "in.wav", [500.0], 0.0, lead_silence=3.0)
        with self.assertRaises(ValueError):
            self._master("in.wav")

    def test_the_ceiling_is_the_callers_not_the_files(self):
        _tone_wav(self.tmp / "in.wav", [500.0], 10.0)
        stats = self._master("in.wav", max_secs=2.0)
        self.assertLessEqual(stats["seconds"], 2.5)

    def test_the_adapter_can_read_a_finished_clips_length(self):
        from voicemail import master as m

        _tone_wav(self.tmp / "in.wav", [500.0], 1.0)
        self._master("in.wav")
        self.assertAlmostEqual(m.wav_seconds(self.tmp / "out.wav"),
                               1.0, delta=0.4)


class TestADraftIsHeldBrieflyAndLeavesNoOrphans(unittest.TestCase):
    """The soundbite flow stores a stranger's voice — the first thing in this
    codebase that does. These tests are the terms: every exit deletes it, the
    sweep catches what a crash leaves, and the air URL dies on first use."""

    def setUp(self):
        from voicemail import review

        self.review = review
        self.tmp = Path(tempfile.mkdtemp())
        self._old = review.DRAFTS_DIR
        review.DRAFTS_DIR = self.tmp

    def tearDown(self):
        self.review.DRAFTS_DIR = self._old

    def _draft(self, at: float | None = None) -> dict:
        src = self.tmp / "mastered.wav"
        src.write_bytes(b"RIFFfakewav")
        d = self.review.create(src, {"seconds": 1.0}, "guest")
        if at is not None:
            d["at"] = at
            self.review._write_sidecar(d["id"], d)
        return d

    def test_the_move_into_the_store_can_cross_filesystems(self):
        # The mastered clip is born in the container's /tmp; the drafts live
        # on the /data bind mount. Path.replace is a bare rename and EXDEV'd
        # on the first real upload (2026-08-17) after every test had passed
        # here, where both paths share a device — so the requirement is
        # pinned at the source: shutil.move, which copies across the seam.
        import inspect

        from voicemail import review

        src = inspect.getsource(review.create)
        self.assertIn("shutil.move", src)
        self.assertNotIn(".replace(", src)

    def test_create_moves_the_clip_and_annotate_writes_back(self):
        src = self.tmp / "mastered.wav"
        src.write_bytes(b"RIFFfakewav")
        d = self.review.create(src, {"seconds": 1.0}, "guest")
        self.assertFalse(src.exists(), "the mastered clip must be MOVED — "
                         "a second copy is a copy nobody deletes")
        self.assertTrue(self.review.audio_path(d["id"]).is_file())
        self.review.annotate(d["id"], transcript="play landslide",
                             action={"kind": "queue", "trackId": "abc"})
        back = self.review.get(d["id"])
        self.assertEqual(back["transcript"], "play landslide")
        self.assertEqual(back["action"]["trackId"], "abc")

    def test_every_exit_deletes_both_files(self):
        d = self._draft()
        self.review.delete(d["id"])
        self.assertIsNone(self.review.get(d["id"]))
        self.assertFalse(self.review.audio_path(d["id"]).exists())
        # And a hostile id never reaches the filesystem.
        self.assertIsNone(self.review.get("../../settings"))
        self.review.delete("../../settings")   # must simply do nothing

    def test_the_sweep_removes_expired_drafts_and_orphaned_audio(self):
        import time as _time

        # fresh first: create() runs the sweep itself, so a draft backdated
        # before another create would already be gone — which is the sweep
        # doing its job, not the sweep being provable.
        fresh = self._draft()
        old = self._draft(at=_time.time() - self.review.DRAFT_TTL_SECS - 5)
        (self.tmp / "orphan.wav").write_bytes(b"RIFF")   # crash leftover
        removed = self.review.sweep()
        self.assertGreaterEqual(removed, 2)
        self.assertIsNone(self.review.get(old["id"]))
        self.assertIsNotNone(self.review.get(fresh["id"]))
        self.assertFalse((self.tmp / "orphan.wav").exists())

    def test_the_air_url_is_single_use_and_expires(self):
        import time as _time

        d = self._draft()
        token = self.review.mint_air_token(d["id"])
        self.assertTrue(token)
        self.assertIsNone(self.review.claim_air_token("invented"))
        # The mixer HEAD-probes before it downloads: peeking must not spend
        # the token — a probe that burned it left the real GET a 404 six
        # milliseconds later and the caller's voice aired as a hole
        # (2026-08-17, the operator's third silent take).
        self.assertIsNotNone(self.review.peek_air_token(token))
        self.assertIsNotNone(self.review.peek_air_token(token),
                             "peek twice, still unspent")
        self.assertIsNone(self.review.peek_air_token("invented"))
        path = self.review.claim_air_token(token)
        self.assertIsNotNone(path)
        self.assertIsNone(self.review.claim_air_token(token),
                          "a claimed token must be dead — the URL is the "
                          "credential and it was just spent")
        self.assertIsNone(self.review.peek_air_token(token),
                          "and dead to the probe as well")
        # An expired token is dead even on its first claim.
        token2 = self.review.mint_air_token(d["id"])
        data = self.review.get(d["id"])
        data["airTokenAt"] = _time.time() - self.review.AIR_TOKEN_TTL_SECS - 5
        self.review._write_sidecar(d["id"], data)
        self.assertIsNone(self.review.claim_air_token(token2))


class _AdapterStation:
    """Records what the adapter asks of the station, answers as told.
    (Not _FakeStation — this module already has one with another shape.)"""

    def __init__(self, queue_ok: bool = True, say_ok: bool = True):
        self.says: list[str] = []
        self.queued: list[dict] = []
        self._queue_ok = queue_ok
        self._say_ok = say_ok

    async def dj_say(self, text, mode="styled", kind="callin"):
        self.says.append(text)
        return {"ok": self._say_ok, "spoken": text}

    async def queue_track(self, track):
        self.queued.append(track)
        return ({"ok": True} if self._queue_ok
                else {"ok": False, "error": "blocklist says no"})

    async def submit_request(self, text, name=""):
        return {"ok": True}

    async def pin_show(self, show_id, minutes):
        self.pinned = (show_id, minutes)
        return {"ok": True}


class TestTheSoundbiteAirsWithReceipts(unittest.TestCase):
    """The adapter's contract: the close the DJ speaks is chosen AFTER the
    station answered, the caller-voice backend degrades to dj-reads out loud,
    and the mixer push carries a single-use URL — never a path."""

    def setUp(self):
        import os as _os

        from voicemail import air, review

        self.air = air
        self.review = review
        self.tmp = Path(tempfile.mkdtemp())
        self._old_dir = review.DRAFTS_DIR
        review.DRAFTS_DIR = self.tmp
        self._old_env = {k: _os.environ.get(k) for k in ("HOST_IP", "TOKEN_PORT")}

    def tearDown(self):
        import os as _os

        self.review.DRAFTS_DIR = self._old_dir
        for k, v in self._old_env.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v

    def _draft(self, action: dict | None = None) -> dict:
        src = self.tmp / "m.wav"
        src.write_bytes(b"RIFFfake")
        d = self.review.create(src, {"seconds": 1.0}, "guest")
        return self.review.annotate(
            d["id"], transcript="play landslide for danny",
            action=action if action is not None else {})

    def _fake_mixer(self, reply: bytes = b"409\nEND\n") -> tuple[str, list]:
        import socket as _socket
        import threading

        got: list[bytes] = []
        srv = _socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        port = srv.getsockname()[1]

        def _serve():
            # Loop: the adapter probes reachability on one connection and
            # pushes on another — a single accept ate the probe and the push
            # found nobody home, downgrading the very test of caller-voice.
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

    def test_dj_reads_is_honest_about_a_refused_action(self):
        station = _AdapterStation(queue_ok=False)
        draft = self._draft({"kind": "queue",
                             "track": {"id": "t1", "title": "Landslide",
                                       "artist": "Fleetwood Mac"}})
        result = asyncio.run(self.air.deliver(
            station, {"vm_air_backend": "dj-reads"}, draft))
        self.assertEqual(result["backend"], "dj-reads")
        self.assertFalse(result["ok"])
        self.assertIn("do NOT claim it worked", station.says[0])
        self.assertIn("refused", result["receipt"])

    def test_the_push_waits_out_the_mixers_poll(self):
        # /dj/say 200 = the intro is WRITTEN to say.txt; the mixer READS it
        # on a 0.5s poll, and a telnet push landing inside that window put
        # the caller's clip on air before either DJ line (2026-08-17). The
        # wait between the intro and the push is load-bearing; this pins it
        # to at least the poll interval.
        import inspect
        import re

        from voicemail import air

        src = inspect.getsource(air.deliver)
        intro_at = src.index("hand over to the caller")
        push_at = src.index("telnet_push(cfg")
        sleep = re.search(r"asyncio\.sleep\(([0-9.]+)\)", src[intro_at:push_at])
        self.assertIsNotNone(sleep, "the intro→push wait is gone")
        self.assertGreaterEqual(float(sleep.group(1)), 0.5)

    def test_caller_voice_pushes_a_token_url_and_closes_on_the_receipt(self):
        addr, got = self._fake_mixer()
        station = _AdapterStation()
        draft = self._draft({"kind": "queue",
                             "track": {"id": "t1", "title": "Landslide"}})
        result = asyncio.run(self.air.deliver(
            station,
            {"vm_air_backend": "caller-voice", "vm_mixer_telnet": addr,
             "vm_air_base_url": "http://192.168.1.245:8100"},
            draft))
        self.assertEqual(result["backend"], "caller-voice")
        self.assertTrue(result["ok"])
        self.assertIn("RID 409", result["receipt"])
        self.assertEqual(len(station.says), 2, "intro and close, no more")
        self.assertIn("queued Landslide", result["receipt"])
        sent = got[0].decode()
        self.assertIn("voice_queue.push http://192.168.1.245:8100/vm-air/",
                      sent)
        # The URL is the credential: never a bare path, never a guessable id.
        self.assertNotIn(draft["id"], sent)

    def test_an_unreachable_mixer_downgrades_out_loud(self):
        station = _AdapterStation()
        draft = self._draft()
        result = asyncio.run(self.air.deliver(
            station,
            {"vm_air_backend": "caller-voice",
             "vm_mixer_telnet": "127.0.0.1:1",     # nothing listens on 1
             "vm_air_base_url": "http://192.168.1.245:8100"},
            draft))
        self.assertEqual(result["backend"], "dj-reads")
        self.assertIn("caller-voice unavailable", result["receipt"])

    def test_preview_resolves_to_a_track_and_falls_safe_everywhere_else(self):
        # The preview IS the receipts discipline moved earlier: a queue
        # verdict must come back holding the library's own id, and every
        # failure shape — no hits, a "none", garbage from the model — must
        # land on no-action, never on a guess.
        import call.providers as providers
        from voicemail import preview

        class _FakeStream:
            def __init__(self, text):
                self._text = text

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def __aiter__(self):
                async def _gen():
                    class _D:  # the two-attribute shape resolve() reads
                        pass
                    d = _D()
                    d.content = self._text
                    c = _D()
                    c.delta = d
                    yield c
                return _gen()

        class _FakeLLM:
            def __init__(self, text):
                self._text = text

            def chat(self, chat_ctx=None):
                return _FakeStream(self._text)

            async def aclose(self):
                pass

        class _SearchStation:
            def __init__(self, hits):
                self.hits = hits
                self.queries = []

            async def search_library(self, q, *a, **k):
                self.queries.append(q)
                return self.hits

        # Requests and exact-id queueing both on for this caller — the gates
        # the resolver now reads, the same ones a live call reads at pickup.
        CFG = {"allow_requests": "open", "allow_exact_queue": "open"}
        old = providers.build_llm
        try:
            hit = {"id": "t9", "title": "Landslide", "artist": "Fleetwood Mac"}
            providers.build_llm = lambda cfg, **k: _FakeLLM(
                '{"action": "queue", "query": "landslide fleetwood mac"}')
            st = _SearchStation([hit])
            action = asyncio.run(preview.resolve(st, CFG, "play landslide"))
            self.assertEqual(action["kind"], "queue")
            self.assertEqual(action["track"]["id"], "t9",
                             "the preview must hold the LIBRARY's id — send "
                             "executes this record, never the words again")
            self.assertIn("Landslide", action["label"])

            # Same verdict, empty library: a request, and the label says so.
            st2 = _SearchStation([])
            action = asyncio.run(preview.resolve(st2, CFG, "play landslide"))
            self.assertEqual(action["kind"], "request")

            # The model says none, or says nonsense: no action, both times.
            providers.build_llm = lambda cfg, **k: _FakeLLM('{"action": "none"}')
            action = asyncio.run(preview.resolve(st, CFG, "hi mum"))
            self.assertEqual(action["kind"], "none")
            providers.build_llm = lambda cfg, **k: _FakeLLM("not json at all")
            action = asyncio.run(preview.resolve(st, CFG, "hello"))
            self.assertEqual(action["kind"], "none")

            # And an empty transcript never wakes the model at all.
            providers.build_llm = lambda cfg, **k: (_ for _ in ()).throw(
                AssertionError("the LLM must not be built for silence"))
            action = asyncio.run(preview.resolve(st, CFG, "   "))
            self.assertEqual(action["kind"], "none")
        finally:
            providers.build_llm = old

    def test_a_vibe_message_stages_a_request_like_the_live_line(self):
        # The bug this defends: "play something a bit lighter" — a MOOD — came
        # back "no action asked for" (RID 280, 2026-08-17), because the studio
        # only ever offered a NAMED track, while the live line's
        # subwave_request_song takes a mood and lets the station's picker
        # resolve it. The studio now stages that SAME request.
        action = asyncio.run(self._resolve(
            '{"action": "queue", "query": "something a bit lighter"}',
            hits=[], transcript="play something a bit lighter this morning",
            cfg={"allow_requests": "open", "allow_exact_queue": "open"}))
        self.assertEqual(action["kind"], "request",
                         "a mood must stage a request, not 'no action'")
        self.assertIn("lighter", action["text"])

    def test_music_and_exact_queue_ride_their_own_switches(self):
        # Consistent with the live line: a music ask rides allow_requests, and
        # an exact-id pin rides allow_exact_queue. Off, nothing is staged; on
        # without exact-queue, a found track is a REQUEST (the station picks),
        # not a by-id queue that would skip its rate limit.
        hit = {"id": "t9", "title": "Landslide", "artist": "Fleetwood Mac"}
        verdict = '{"action": "queue", "query": "landslide"}'

        # Requests OFF: no music action, and the model is never even built.
        off = asyncio.run(self._resolve(
            verdict, hits=[hit], transcript="play landslide",
            cfg={"allow_requests": "off"}, no_llm=True))
        self.assertEqual(off["kind"], "none")

        # Requests on, exact-queue OFF: a found track stages a REQUEST.
        req = asyncio.run(self._resolve(
            verdict, hits=[hit], transcript="play landslide",
            cfg={"allow_requests": "open"}))
        self.assertEqual(req["kind"], "request",
                         "without allow_exact_queue a named track is a "
                         "request, never a by-id queue")

        # Both on: the exact record is pinned by id — the receipts discipline.
        both = asyncio.run(self._resolve(
            verdict, hits=[hit], transcript="play landslide",
            cfg={"allow_requests": "open", "allow_exact_queue": "open"}))
        self.assertEqual(both["kind"], "queue")
        self.assertEqual(both["track"]["id"], "t9")

    async def _resolve(self, verdict, *, hits, transcript, cfg, no_llm=False):
        """Run preview.resolve with a stubbed one-shot LLM and search — the
        shared harness for the studio's resolver tests."""
        import call.providers as providers
        from voicemail import preview

        class _Stream:
            async def __aenter__(s):
                return s

            async def __aexit__(s, *a):
                return False

            def __aiter__(s):
                async def _gen():
                    d = type("D", (), {"content": verdict})()
                    yield type("C", (), {"delta": d})()
                return _gen()

        class _LLM:
            def chat(self, chat_ctx=None):
                return _Stream()

            async def aclose(self):
                pass

        class _St:
            async def search_library(self, q, *a, **k):
                return list(hits)

        old = providers.build_llm
        try:
            providers.build_llm = (
                (lambda cfg, **k: (_ for _ in ()).throw(
                    AssertionError("the model must not be built when nothing "
                                   "is permitted")))
                if no_llm else (lambda cfg, **k: _LLM()))
            return await preview.resolve(_St(), cfg, transcript)
        finally:
            providers.build_llm = old

    def test_a_takeover_rides_the_live_lines_own_switch(self):
        # "Change the DJ" from a voicemail is the furthest-reaching thing the
        # studio can do, so it rides allow_takeover — the SAME switch the
        # live line uses — read at preview AND again at send.
        import call.providers as providers
        from voicemail import preview

        class _Llm:
            def chat(self, chat_ctx=None):
                self.prompt = chat_ctx.items[-1].content[0] if chat_ctx else ""

                class _S:
                    async def __aenter__(s):
                        return s

                    async def __aexit__(s, *a):
                        return False

                    def __aiter__(s):
                        async def _gen():
                            class _D:
                                pass
                            d = _D()
                            d.content = '{"action":"takeover","who":"duke"}'
                            c = _D()
                            c.delta = d
                            yield c
                        return _gen()
                return _S()

            async def aclose(self):
                pass

        class _St:
            async def schedule(self):
                return {"shows": [{"id": "s1", "name": "The Alibi Room",
                                   "personaId": "p1"}]}

            async def personas(self):
                return [{"id": "p1", "name": "Duke Sterling"}]

            async def search_library(self, q, *a, **k):
                return []

        old = providers.build_llm
        try:
            providers.build_llm = lambda cfg, **k: _Llm()
            on = asyncio.run(preview.resolve(
                _St(), {"allow_takeover": True}, "put duke on"))
            self.assertEqual(on["kind"], "takeover")
            self.assertEqual(on["showId"], "s1")
            self.assertIn("Duke Sterling", on["label"])
            # Switch off: even a model that answers takeover anyway resolves
            # to nothing — the option was never offered and never honoured.
            off = asyncio.run(preview.resolve(
                _St(), {}, "put duke on"))
            self.assertEqual(off["kind"], "none")
        finally:
            providers.build_llm = old

        # Send-time: the adapter executes the pin, and refuses honestly when
        # the switch went off between preview and send.
        station = _AdapterStation()
        draft = self._draft({"kind": "takeover", "showId": "s1",
                             "show": "The Alibi Room", "who": "Duke Sterling"})
        result = asyncio.run(self.air.deliver(
            station, {"vm_air_backend": "dj-reads", "allow_takeover": True},
            draft))
        self.assertTrue(result["ok"])
        self.assertEqual(station.pinned, ("s1", 60))
        self.assertIn("Duke Sterling", result["receipt"])

        station2 = _AdapterStation()
        draft2 = self._draft({"kind": "takeover", "showId": "s1",
                              "show": "The Alibi Room", "who": "Duke"})
        result2 = asyncio.run(self.air.deliver(
            station2, {"vm_air_backend": "dj-reads"}, draft2))
        self.assertFalse(result2["ok"])
        self.assertFalse(hasattr(station2, "pinned"))
        self.assertIn("switched off", result2["receipt"])
        self.assertIn("do NOT claim it worked", station2.says[0])

    def test_the_clip_dies_at_the_claim_not_at_the_send(self):
        # The mixer fetches the pushed URL LAZILY — when the queue reaches
        # the clip, after the DJ's intro has played. Deleting the draft at
        # send beat that fetch by seven seconds and the mixer got a 404: the
        # operator heard the DJ speak around a hole where their own voice
        # should have been (2026-08-17). So caller-voice defers deletion to
        # the claim itself, which serves from memory and removes the files
        # before the response goes out; every other backend deletes at send.
        import inspect

        from api import voicemail as api_vm

        send_src = inspect.getsource(api_vm.handle_vm_draft_send)
        self.assertIn('!= "caller-voice"', send_src)
        clip_src = inspect.getsource(api_vm.handle_vm_air_clip)
        self.assertIn("read_bytes", clip_src)
        self.assertIn("vm_review.delete(path.stem)", clip_src)

    def test_the_studio_declares_its_own_visibility_in_the_rig(self):
        # The rig ships reserved-but-hidden (`visibility: hidden`) and every
        # band that should show must opt in — the chat input learned this
        # ("present but invisible", operator-reported) and the studio
        # relearned it: laid out, occupying its rows, no Record button
        # anywhere (operator's screenshot, 2026-08-17). offsetParent checks
        # saw nothing wrong, because offsetParent is blind to visibility —
        # which is why this is pinned at the stylesheet, not probed at runtime.
        from tests.support import REPO

        css = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")
        if ".rig {\n    visibility: hidden" not in css.replace("\r\n", "\n"):
            self.skipTest("the rig no longer reserves-hidden")
        self.assertIn(".rig > .vmstudio { visibility: visible; }",
                      css.replace("\r\n", "\n"))
        # Third firing of the same trap: the rail carries the studio's
        # RINGING/RECORDING chips and clock (and the idle card's LISTEN
        # chip), and sat laid-out-but-invisible under the reservation —
        # "i dont see the chips above the transcript box" (operator,
        # 2026-08-17).
        self.assertIn(".rig > .nprail { visibility: visible; }",
                      css.replace("\r\n", "\n"))

    def test_the_studio_answers_to_the_machines_own_tier_door(self):
        # The first build hard-refused the open tier, while the operator's
        # real line runs allow_voicemail=open — their strangers could record
        # a whole take and only learn at upload that nobody would accept it.
        # One door for both flows: the studio gate must walk the same
        # tier_reaches ladder the vm mint walks, and never carry a hardcoded
        # tier opinion of its own.
        import inspect

        from api import voicemail as api_vm

        src = inspect.getsource(api_vm._draft_gate)
        self.assertIn("tier_reaches", src)
        self.assertIn("allow_voicemail", src)
        self.assertNotIn('!= "open"', src)

    def test_the_search_retries_without_the_by_connector(self):
        # Found by the first live probe of the shipping prompt: the model's
        # query kept the caller's "by" ("Landslide by Fleetwood Mac"), the
        # station's every-word search returned nothing, and a track the
        # library holds five times over previewed as a mere request.
        import call.providers as providers
        from voicemail import preview

        class _Llm:
            def chat(self, chat_ctx=None):
                class _S:
                    async def __aenter__(s):
                        return s

                    async def __aexit__(s, *a):
                        return False

                    def __aiter__(s):
                        async def _gen():
                            class _D:
                                pass
                            d = _D()
                            d.content = ('{"action":"queue","query":'
                                         '"Landslide by Fleetwood Mac"}')
                            c = _D()
                            c.delta = d
                            yield c
                        return _gen()
                return _S()

            async def aclose(self):
                pass

        class _St:
            def __init__(self):
                self.queries = []

            async def search_library(self, q, *a, **k):
                self.queries.append(q)
                return ([{"id": "t1", "title": "Landslide",
                          "artist": "Fleetwood Mac"}]
                        if "by" not in q.lower().split() else [])

        old = providers.build_llm
        try:
            providers.build_llm = lambda cfg, **k: _Llm()
            st = _St()
            action = asyncio.run(preview.resolve(
                st, {"allow_requests": "open", "allow_exact_queue": "open"},
                "play landslide"))
            self.assertEqual(action["kind"], "queue",
                             "the by-variant retry must reach the hit")
            self.assertGreater(len(st.queries), 1)
        finally:
            providers.build_llm = old

    def test_the_air_base_url_prefers_the_setting_then_host_ip(self):
        import os as _os

        self.assertEqual(
            self.air.air_base_url({"vm_air_base_url": "http://x:9/"}),
            "http://x:9")
        _os.environ["HOST_IP"] = "192.168.1.245"
        _os.environ.pop("TOKEN_PORT", None)
        self.assertEqual(self.air.air_base_url({}),
                         "http://192.168.1.245:8100")
        _os.environ.pop("HOST_IP", None)
        self.assertEqual(self.air.air_base_url({}), "",
                         "no setting and no HOST_IP must read as 'no base', "
                         "which the adapter treats as caller-voice being "
                         "unavailable — never a URL with an empty host")


class TestTheStudioGreetingIsRenderedOnceNotPerVisit(unittest.TestCase):
    """With nothing staged, /vm-greeting renders the DJ's line on demand —
    the operator heard ring, beep and NO voice on a fresh persona
    (2026-08-17). The render spends TTS money at a GUEST's say-so, so the
    handler must cache through the same index staging uses (needs_render →
    write_clip) behind a lock: a stranger can cost one render per persona,
    never one per visit."""

    def setUp(self):
        from tests.support import AGENT_WORKER

        self.api = (AGENT_WORKER / "api" / "voicemail.py").read_text(
            encoding="utf-8")
        self.greet = (AGENT_WORKER / "voicemail" / "greetings.py").read_text(
            encoding="utf-8")

    def test_the_fallback_goes_through_the_cache_and_a_lock(self):
        handler = self.api.split("async def handle_vm_greeting")[1]
        handler = handler[:handler.index("\nasync def ")]
        self.assertIn("ensure_clip", handler,
                      "the studio pickup no longer goes through "
                      "greetings.ensure_clip — the on-demand voice is gone")
        ensure = self.greet.split("async def ensure_clip")[1]
        for piece in ("needs_render", "write_clip", "_lock()", "staged_clip"):
            self.assertIn(piece, ensure,
                          f"ensure_clip no longer uses {piece} — either the "
                          "cache or the lock is gone, and either way a guest "
                          "can now spend unbounded TTS money")
