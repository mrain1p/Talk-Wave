"""TTS and STT: whether a backend can actually say the thing, and whether it said it at the rate it claimed.

Split out of test_sidecar.py; see tests/__init__.py.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from tests.support import AGENT_WORKER


class TestAVoiceTheBackendCannotSpeakIsNotSilence(unittest.TestCase):
    """Observed on air 2026-08-05, room e7f9ff6f8252, and the record caught it
    perfectly while nothing prevented it.

    The station maps Rosie (p_default1) to `zmcVlqmyk3Jpn5AVYcAL`, an
    ElevenLabs id, because that is what she is broadcast with. This service was
    pointed at local VibeVoice, which has -Cliff1, Lily, Delia1 and no such
    voice. Mirroring the station's voice is RIGHT — the call-in DJ should sound
    like the one on air — but the voice belongs to the station's TTS, not
    necessarily to ours.

    So the DJ wrote a perfectly good greeting, every TTS request 400'd eight
    times over, and the caller sat in silence for the whole call. The 0.9.20
    dead-air fallback was mute too, because it speaks through the same backend.

    A voice the backend does not have is not a reason to say nothing. It is a
    reason to say it differently and write down why.
    """

    def test_the_station_voice_is_kept_when_the_backend_has_it(self):
        from tts_adapter import pick_speakable_voice

        got, why = pick_speakable_voice("-Cliff1", ["-Brock1", "-Cliff1", "Lily"])
        self.assertEqual(got, "-Cliff1")
        self.assertEqual(why, "")

    def test_rosie(self):
        from tts_adapter import pick_speakable_voice

        got, why = pick_speakable_voice(
            "zmcVlqmyk3Jpn5AVYcAL", ["-Brock1", "-Cliff1", "Lily"])
        self.assertIn(got, ["-Brock1", "-Cliff1", "Lily"])
        self.assertIn("zmcVlqmyk3Jpn5AVYcAL", why)
        # The operator has to be able to act on it, so it says what to do.
        self.assertIn("Voice", why)

    def test_a_failed_lookup_never_changes_the_voice(self):
        # The one that would be worse than the bug: an empty list means "could
        # not find out", and treating that as "has none" would make a slow or
        # unreachable TTS server rewrite every DJ's voice.
        from tts_adapter import pick_speakable_voice

        got, why = pick_speakable_voice("zmcVlqmyk3Jpn5AVYcAL", [])
        self.assertEqual(got, "zmcVlqmyk3Jpn5AVYcAL")
        self.assertEqual(why, "")

    def test_asking_for_nothing_is_not_worth_a_warning(self):
        from tts_adapter import pick_speakable_voice

        got, why = pick_speakable_voice("", ["-Brock1", "Lily"])
        self.assertEqual(got, "-Brock1")
        self.assertEqual(why, "")

    def test_the_worker_and_the_panel_share_one_voice_lookup(self):
        # A panel showing one set of voices while the worker believes another
        # is how a call asks for a voice that is not there. Same function.
        from api import settings as api_settings
        from tts_adapter import available_voices

        self.assertIs(api_settings.tts_voice_list, available_voices)


class TestWhatTheBackendSaidReachesTheOperator(unittest.TestCase):
    """httpx renders a failed request as "Client error '400 Bad Request' for
    url ..." and stops. The body — which is where the backend explains itself —
    was thrown away at every one of the four places this file checks a status.

    That body is routinely the only actionable thing in the failure. A voice
    cloning engine refusing because a reference clip is longer than Whisper's
    30-second ceiling says exactly that, in the body, and the operator saw an
    opaque 400. /test/tts grew a hand-written guess at what a 400 probably
    meant precisely because the real answer never arrived.
    """

    def _said(self, response):
        import asyncio

        from tts_adapter import _backend_said

        return asyncio.run(_backend_said(response))

    def _resp(self, status=400, **kw):
        import httpx

        return httpx.Response(
            status, request=httpx.Request("POST", "http://tts.test/v1/audio/speech"), **kw)

    def test_a_json_detail_is_what_the_operator_reads(self):
        # The real shape from a FastAPI-based cloning server hitting the
        # 30-second transcription limit.
        said = self._said(self._resp(json={
            "detail": "You have passed more than 3000 mel input features "
                      "(> 30 seconds) which automatically enables long-form "
                      "generation which requires the model to predict timestamp "
                      "tokens..."}))
        self.assertIn("30 seconds", said)

    def test_the_other_two_envelopes_in_the_wild(self):
        self.assertIn("no such voice", self._said(self._resp(
            json={"message": "no such voice"})))
        # OpenAI nests it one level down.
        self.assertIn("model not found", self._said(self._resp(
            json={"error": {"message": "model not found", "type": "invalid_request"}})))

    def test_plain_text_survives_too(self):
        self.assertIn("Voice 'New-Walken' not found", self._said(
            self._resp(text="Voice 'New-Walken' not found in /voices.")))

    def test_a_wall_of_audio_is_not_an_explanation(self):
        # A backend that answers 4xx with a body of PCM would otherwise put
        # several hundred bytes of mojibake in the operator's error line.
        self.assertEqual("", self._said(self._resp(
            content=b"\x00\x01" * 4000, headers={"content-type": "audio/pcm"})))

    def test_it_cannot_run_away_with_the_error_line(self):
        from tts_adapter import _ERROR_BODY_CHARS

        said = self._said(self._resp(text="x" * 5000))
        self.assertLessEqual(len(said), _ERROR_BODY_CHARS)

    def test_a_good_response_is_never_even_read(self):
        # Not just "does not raise". On the streaming path the body IS the
        # caller's audio and it has not been consumed yet when the status
        # arrives — reading it to look for an explanation would swallow the
        # speech before aiter_bytes ever saw it.
        import asyncio

        import tts_adapter

        async def explode(_):
            raise AssertionError("read the body of a perfectly good response")

        real = tts_adapter._backend_said
        tts_adapter._backend_said = explode
        try:
            got = asyncio.run(tts_adapter._raise_for_status(
                self._resp(200, content=b"\x00\x00")))
        finally:
            tts_adapter._backend_said = real
        self.assertIsNone(got)

    def test_the_status_AND_the_reason_are_both_in_the_raise(self):
        import asyncio

        from livekit.agents import APIConnectionError

        from tts_adapter import _raise_for_status

        with self.assertRaises(APIConnectionError) as caught:
            asyncio.run(_raise_for_status(
                self._resp(400, json={"detail": "reference clip too long"})))
        message = str(caught.exception)
        self.assertIn("400", message)
        self.assertIn("reference clip too long", message)

    def test_the_hand_written_guess_yields_to_the_real_answer(self):
        # /test/tts carries a hint about cloud voice ids not being local ones.
        # It exists only because the body used to be thrown away. A backend
        # that names the voice in its own error has explained itself, and
        # stacking a guess underneath is noise.
        source = (AGENT_WORKER / "api" / "diagnostics.py").read_text(
            encoding="utf-8")
        self.assertIn('if "400" in msg and voice and voice not in msg:', source)

    def test_every_status_check_in_the_synthesis_path_uses_it(self):
        # Three checks in the path a caller's audio comes down — streaming,
        # buffered, and the json_url follow-up. A bare raise_for_status left
        # in any of them puts that path back to an opaque status, which is
        # the whole bug.
        source = (AGENT_WORKER / "tts_adapter.py").read_text(encoding="utf-8")
        speaking = source.split("class AdapterChunkedStream", 1)[1]
        self.assertNotIn("raise_for_status()", speaking)
        self.assertEqual(3, speaking.count("await _raise_for_status("))


class TestVoiceDiscoveryIsNotHardcodedToOneShape(unittest.TestCase):
    """The adapter described how to SYNTHESIZE and never how to DISCOVER, so
    /v1/audio/voices and OpenAI's {"data":[{"id":...}]} were baked in.

    That is worse than it sounds, because an empty list means "could not find
    out" everywhere in tts_adapter. A backend that lists its voices perfectly
    well at a different path, or in a different shape, does not read as an
    error here — it silently switches off pick_speakable_voice, leaves the
    panel showing stock OpenAI names, and lets the station's voice go to a
    backend that never had it. All the machinery for that case exists and
    simply never engages.
    """

    def test_the_openai_shape_still_works(self):
        from tts_adapter import parse_voice_list

        self.assertEqual(
            ["alloy", "onyx"],
            parse_voice_list({"data": [{"id": "onyx"}, {"id": "alloy"}]}))

    def test_a_bare_list_of_names(self):
        from tts_adapter import parse_voice_list

        self.assertEqual(["Delia1", "Lily"], parse_voice_list(["Lily", "Delia1"]))

    def test_a_voices_key_with_either_thing_inside(self):
        from tts_adapter import parse_voice_list

        self.assertEqual(["Lily"], parse_voice_list({"voices": ["Lily"]}))
        self.assertEqual(["Lily"], parse_voice_list({"voices": [{"name": "Lily"}]}))

    def test_a_mapping_of_id_to_details(self):
        from tts_adapter import parse_voice_list

        self.assertEqual(
            ["Cliff1", "Lily"],
            parse_voice_list({"Lily": {"lang": "en"}, "Cliff1": {"lang": "en"}}))

    def test_an_error_envelope_does_not_become_a_voice_called_detail(self):
        # The reason the mapping branch insists every value is a dict.
        from tts_adapter import parse_voice_list

        self.assertEqual([], parse_voice_list({"detail": "not found"}))

    def test_nonsense_is_no_voices_rather_than_an_exception(self):
        from tts_adapter import parse_voice_list

        for junk in (None, 7, "", [], {}, [None, 3, {}], {"data": "nope"}):
            self.assertEqual([], parse_voice_list(junk))

    def test_the_adapter_chooses_the_path(self):
        from tts_adapter import ADAPTER_DIR, load_adapter

        # Unset, every existing deployment keeps the path it had.
        self.assertEqual(
            "/v1/audio/voices",
            load_adapter(ADAPTER_DIR / "openai-cloud.json")["voices_path"])

    def test_a_configured_path_is_the_one_that_gets_fetched(self):
        import asyncio
        import json as _json
        import tempfile
        from pathlib import Path

        import tts_adapter

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "elsewhere.json"
            path.write_text(_json.dumps({
                "endpoint_path": "/v1/audio/speech",
                "voices_path": "/voices",
            }), encoding="utf-8")

            asked = {}

            class FakeResponse:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    return ["New-Walken", "Delia1"]

            class FakeClient:
                def __init__(self, **kw):
                    pass

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def get(self, url, headers=None):
                    asked["url"] = url
                    asked["headers"] = headers
                    return FakeResponse()

            real = tts_adapter.httpx.AsyncClient
            tts_adapter.httpx.AsyncClient = FakeClient
            try:
                got = asyncio.run(tts_adapter.available_voices(
                    "http://tts.test", adapter_path=str(path)))
            finally:
                tts_adapter.httpx.AsyncClient = real

        self.assertEqual("/voices", asked["url"])
        self.assertEqual(["Delia1", "New-Walken"], got)

    def test_there_is_only_one_voice_lookup_left(self):
        # station_config carried a second copy: its own client, its own
        # hardcoded /v1/audio/voices, its own OpenAI-shaped parse. It answered
        # "no voices" for backends the panel next door listed fine.
        source = (AGENT_WORKER / "station_config.py").read_text(encoding="utf-8")
        # Comments are allowed to name the path — that is where the incident
        # is written down. Code is not.
        code = "\n".join(line for line in source.splitlines()
                         if not line.lstrip().startswith("#"))
        self.assertNotIn("/v1/audio/voices", code)
        self.assertIn("available_voices", code)


class TestADeclaredSampleRateIsMeasuredNotTrusted(unittest.TestCase):
    """A sample rate is a label attached to the samples, not something carried
    in them. Declare 24000 for a backend producing 48000 and every line plays
    at half speed an octave down — and no component anywhere reports an error,
    because nothing is wrong except the label.

    It is easy to get wrong for a reason no documentation fixes: the same
    build of a local engine commonly reports one rate on a GPU and half of it
    on a CPU, so an adapter that is correct on the operator's box is silently
    wrong on the next person's.

    The obvious check — infer the rate from how fast the speech sounds — is a
    trap, and the band here is enormous because of it. A persona written to
    speak in fast clipped fragments produces a fraction of the audio a normal
    voice does for the same text; reasoning from one lands several octaves out
    with total confidence. So: measure from a wav header, and treat the
    inference as "worth checking" and never as an answer.
    """

    def _wav(self, rate: int) -> bytes:
        import struct

        fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
        return (b"RIFF" + struct.pack("<I", 36) + b"WAVE"
                + b"fmt " + struct.pack("<I", len(fmt)) + fmt
                + b"data" + struct.pack("<I", 0))

    def test_the_rate_comes_out_of_the_header(self):
        from tts_adapter import riff_sample_rate

        for rate in (8000, 16000, 24000, 44100, 48000):
            self.assertEqual(rate, riff_sample_rate(self._wav(rate)))

    def test_something_that_is_not_a_wav_measures_nothing(self):
        from tts_adapter import riff_sample_rate

        self.assertIsNone(riff_sample_rate(b""))
        self.assertIsNone(riff_sample_rate(b"\x00\x01" * 500))       # raw pcm
        self.assertIsNone(riff_sample_rate(b'{"detail": "no wav"}'))

    def test_agreement_is_reported_as_confirmed_not_as_silence(self):
        from api.diagnostics import _sample_rate_verdict

        note = _sample_rate_verdict(24000, 24000, "", "hello there", 1.0)
        self.assertIn("24000", note)
        self.assertIn("confirmed", note)

    def test_a_mismatch_says_the_number_to_change_it_to(self):
        from api.diagnostics import _sample_rate_verdict

        note = _sample_rate_verdict(24000, 48000, "", "hello there", 1.0)
        self.assertIn("MISMATCH", note)
        self.assertIn("48000", note)
        # Useless without telling the operator where the wrong number lives.
        self.assertIn("sample_rate", note)

    def test_which_way_round_the_speed_goes(self):
        # Got this backwards once and the note confidently said the opposite
        # of what the operator could hear. The player consumes `declared`
        # samples a second out of audio carrying `measured` of them, so
        # declaring half the real rate plays SLOW and low, not fast and high.
        from api.diagnostics import _sample_rate_verdict

        self.assertIn("0.5× speed", _sample_rate_verdict(
            24000, 48000, "", "hello", 1.0))
        self.assertIn("2× speed", _sample_rate_verdict(
            48000, 24000, "", "hello", 1.0))

    def test_an_unmeasurable_backend_with_plausible_speech_says_nothing(self):
        from api.diagnostics import _sample_rate_verdict

        # 44 characters in 3.7s — 11.8 a second, ordinary speech.
        self.assertEqual("", _sample_rate_verdict(
            24000, None, "no wav", "x" * 44, 3.73))

    def test_an_impossible_pace_is_raised_as_suspicion_only(self):
        from api.diagnostics import _sample_rate_verdict

        # 119 characters in 0.9s: 132 a second. Nothing speaks like that.
        note = _sample_rate_verdict(48000, None, "no wav", "x" * 119, 0.9)
        self.assertIn("⚠", note)
        # It must not read as a verdict. The one time this reasoning was
        # trusted outright it produced a rate four times too slow.
        self.assertIn("confirm", note.lower())

    def test_the_probe_needs_a_format_field_it_knows_the_name_of(self):
        # Asking for wav means writing into a field only the adapter can name.
        # Guessing "response_format" at a backend that does not have it would
        # turn a working test button into a 422.
        import asyncio

        import tts_adapter

        tts = tts_adapter.AdapterTTS.__new__(tts_adapter.AdapterTTS)
        tts._adapter = {"static_fields": {"num_steps": 4}, "method": "POST",
                        "endpoint_path": "/x", "request_field_map": {}}
        rate, why = asyncio.run(tts.probe_sample_rate())
        self.assertIsNone(rate)
        self.assertIn("format", why)


class TestEveryPersonaIsCheckedNotOnlyTheOneOnAir(unittest.TestCase):
    """pick_speakable_voice only ever sees the persona a call actually
    reached, so a persona whose voice the backend does not have stays
    invisible until someone rings in while that DJ is live.

    It falls back and says why, which is right — but it is the wrong moment to
    find out. The caller reaches the station's DJ in somebody else's voice,
    and mirroring the on-air voice was the entire point. The roster is already
    fetched for the panel; checking all of it moves the discovery to the
    button press.
    """

    def _audit(self, available, voices):
        import asyncio

        from api import diagnostics

        class FakeStationConfig:
            def __init__(self, *a, **kw):
                pass

            async def persona_voices(self):
                if isinstance(voices, Exception):
                    raise voices
                return voices

            async def aclose(self):
                pass

        real = diagnostics.StationConfig
        diagnostics.StationConfig = FakeStationConfig
        try:
            return asyncio.run(diagnostics._persona_voice_audit(available))
        finally:
            diagnostics.StationConfig = real

    def test_a_persona_nobody_has_called_yet_is_named(self):
        note = self._audit(
            ["-Cliff1", "Lily"],
            {"p_default1": "-Cliff1", "p_night": "New-Walken"})
        self.assertIn("p_night", note)
        self.assertNotIn("p_default1", note)

    def test_a_complete_roster_says_so_rather_than_nothing(self):
        # Silence would be indistinguishable from the check not running.
        note = self._audit(["-Cliff1", "Lily"],
                           {"p_default1": "-Cliff1", "p_night": "Lily"})
        self.assertIn("2 personas", note)

    def test_a_failed_voice_lookup_accuses_no_one(self):
        # Same rule as pick_speakable_voice: an empty list is "could not find
        # out". Reporting every persona as broken because the TTS server was
        # slow would be worse than not checking.
        self.assertEqual("", self._audit([], {"p_default1": "-Cliff1"}))

    def test_an_unreadable_station_does_not_fail_the_whole_pipeline_test(self):
        note = self._audit(["-Cliff1"], RuntimeError("station 401"))
        self.assertIn("station 401", note)


class TestTheSttModelIsLoadedOnceForTheWholeProcess(unittest.TestCase):
    """Loading is slow enough that doing it per call would be audible, so one
    model is shared per (name, compute type) across every call in the process.
    The prewarm hook is synchronous on purpose: the Agents SDK calls
    stt.prewarm() without awaiting, so an async version silently never ran."""

    KEY = ("test-fake.en", "int8")

    def setUp(self):
        import local_stt

        # A sentinel in the cache means neither preload_sync nor the STT class
        # reaches for faster_whisper, so this test needs no model download.
        local_stt._models[self.KEY] = object()

    def tearDown(self):
        import local_stt

        local_stt._models.pop(self.KEY, None)

    def test_preloading_an_already_loaded_model_is_a_no_op(self):
        import local_stt

        before = dict(local_stt._models)
        local_stt.preload_sync("test-fake.en", "int8")
        self.assertEqual(local_stt._models, before)

    def test_prewarm_is_synchronous_so_the_sdk_actually_runs_it(self):
        import inspect

        import local_stt

        self.assertFalse(
            inspect.iscoroutinefunction(local_stt.LocalWhisperSTT.prewarm),
            "an async prewarm is never awaited by the SDK and silently does nothing",
        )
        stt_impl = local_stt.LocalWhisperSTT(model="test-fake.en", compute_type="int8")
        stt_impl.prewarm()      # must not try to download anything

    def test_it_declares_itself_non_streaming_so_the_sdk_wraps_it_with_the_vad(self):
        import local_stt

        caps = local_stt.LocalWhisperSTT(model="test-fake.en").capabilities
        self.assertFalse(caps.streaming)
        self.assertFalse(caps.interim_results)


class TestABackendTooSlowToBeOnAPhoneCallSaysSo(unittest.TestCase):
    """The failure with no symptom from in here.

    Time to first audio was measured at a healthy ~1.5s while the same backend
    ran at 1.6-2.3x realtime — so the DJ started speaking on cue and then fell
    further behind with every sentence. Audible to the caller as gaps and drag,
    invisible in the transcript, and nothing anywhere errored. The only evidence
    that existed was the operator saying calls "felt laggy".
    """

    def _meter(self, pairs):
        from tts_pace import PaceMeter

        m = PaceMeter()
        for wall, plays in pairs:
            m.note(wall, plays)
        return m

    def test_a_backend_that_keeps_up_says_nothing(self):
        self.assertEqual(self._meter([(1.8, 2.0), (5.0, 6.0)]).report(), "")

    def test_a_backend_that_falls_behind_reports_the_ratio(self):
        said = self._meter([(4.3, 1.9), (11.3, 6.8)]).report()
        self.assertIn("could not keep up", said)
        self.assertIn("1.79x realtime", said)

    def test_one_line_is_not_enough_to_judge_a_call_on(self):
        self.assertEqual(self._meter([(9.0, 2.0)]).report(), "")

    def test_a_line_too_short_to_measure_is_ignored(self):
        # One buffer's jitter dominates a sub-second line; counting it would
        # make a healthy backend look broken on a call full of short answers.
        m = self._meter([(0.9, 0.4), (0.9, 0.3)])
        self.assertEqual(m.lines, 0)
        self.assertEqual(m.report(), "")

    def test_a_little_overrun_is_not_called_a_fault(self):
        # Buffering absorbs a brief overrun, and on a single-GPU host the
        # station's own renders contend for the same card.
        self.assertEqual(self._meter([(2.1, 2.0), (5.2, 5.0)]).report(), "")

    def test_pcm_bytes_become_seconds(self):
        from tts_pace import seconds_of_pcm

        # 24kHz, 16-bit, mono: two bytes a sample.
        self.assertAlmostEqual(seconds_of_pcm(48000, 24000, 1), 1.0)

    def test_the_call_asks_the_backend_how_it_did(self):
        # The seam session.py reaches for at the end of a call. If this method
        # is renamed the report is silently never written, which is the exact
        # class of failure this whole measurement exists to end.
        from tts_adapter import AdapterTTS

        self.assertTrue(callable(getattr(AdapterTTS, "pace_report", None)))
