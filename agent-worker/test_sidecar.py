"""
Unit tests for the pure-logic parts of the sidecar — the pieces where a silent
regression would be audible on air (speech filtering), would misroute money
(settings/secrets precedence), or would break tool truthfulness.

Run from agent-worker/:  python -m unittest test_sidecar -v

Deliberately stdlib-only (unittest, tempfile) so the venv needs nothing new.
Network is never touched.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

# Before any module that calls log_setup.setup() is imported — otherwise the
# test run pollutes the real data/logs/worker.log.
os.environ["LOG_TO_FILE"] = "0"

import prompts
import secrets_store
import settings as settings_store
import speech_filter


class TestSpeechFilter(unittest.TestCase):
    def test_strips_asterisk_stage_directions(self):
        out = speech_filter.strip_stage_directions(
            "*shuffles through records* Here's one for you."
        )
        self.assertEqual(out, "Here's one for you.")

    def test_strips_bracketed_and_paren_actions(self):
        out = speech_filter.strip_stage_directions("[pause] Right. (laughs) Where were we?")
        self.assertNotIn("[pause]", out)
        self.assertNotIn("(laughs)", out)
        self.assertIn("Where were we?", out)

    def test_keeps_ordinary_parenthetical_speech(self):
        text = "the set (which runs till two) is all vinyl"
        self.assertEqual(speech_filter.strip_stage_directions(text), text)

    def test_profanity_mask_and_drop_and_off(self):
        words = ["fuck", "shit"]
        self.assertEqual(
            speech_filter.filter_profanity("well fuck that", words, "mask"),
            "well f— that",
        )
        self.assertEqual(
            speech_filter.filter_profanity("well fuck that", words, "drop"),
            "well that",
        )
        self.assertEqual(
            speech_filter.filter_profanity("well fuck that", words, "off"),
            "well fuck that",
        )

    def test_profanity_respects_word_boundaries(self):
        # "Scunthorpe problem": substrings must survive.
        text = "let me assess the Scunthorpe situation"
        self.assertEqual(
            speech_filter.filter_profanity(text, ["cunt", "ass"], "drop"), text
        )

    def test_clean_for_speech_combined(self):
        out = speech_filter.clean_for_speech(
            "*sighs* That's some shit, huh?",
            strip_directions=True, profanity_mode="mask", profanity_words=["shit"],
        )
        self.assertEqual(out, "That's some s—, huh?")


class TestPrompts(unittest.TestCase):
    def test_demojibake_repairs_double_encoding(self):
        self.assertEqual(prompts._demojibake("night â€” slow"), "night — slow")

    def test_demojibake_leaves_clean_text_alone(self):
        self.assertEqual(prompts._demojibake("plain text — fine"), "plain text — fine")

    def test_clip_respects_budget_on_word_boundary(self):
        out = prompts._clip("one two three four five", 13)
        self.assertLessEqual(len(out), 14)  # budget + ellipsis
        self.assertTrue(out.endswith("…"))


class _TempStores(unittest.TestCase):
    """Points the settings/secrets stores at temp files and scrubs the env
    vars the tests touch, restoring everything afterwards."""

    ENV_VARS = (
        "STT_MODEL", "DEEPGRAM_MODEL", "STT_PROVIDER", "LLM_PROVIDER",
        "DEEPGRAM_API_KEY", "OPENAI_API_KEY", "TTS_MODE",
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._old_settings_path = settings_store.SETTINGS_PATH
        self._old_secrets_path = secrets_store.SECRETS_PATH
        settings_store.SETTINGS_PATH = tmp / "settings.json"
        secrets_store.SECRETS_PATH = tmp / "secrets.json"
        self._old_env = {k: os.environ.get(k) for k in self.ENV_VARS}
        for k in self.ENV_VARS:
            os.environ.pop(k, None)

    def tearDown(self):
        settings_store.SETTINGS_PATH = self._old_settings_path
        secrets_store.SECRETS_PATH = self._old_secrets_path
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()


class TestSettings(_TempStores):
    def test_defaults_when_nothing_stored(self):
        cfg = settings_store.load()
        self.assertEqual(cfg["stt_model"], "nova-3")
        self.assertTrue(cfg["allow_requests"])
        self.assertNotIn("allow_sfx", cfg)  # removed feature stays removed

    def test_env_beats_default_and_stored_beats_env(self):
        os.environ["STT_PROVIDER"] = "openai"
        self.assertEqual(settings_store.load()["stt_provider"], "openai")
        settings_store.save({"stt_provider": "local"})
        self.assertEqual(settings_store.load()["stt_provider"], "local")

    def test_stt_model_env_alias_and_fallback(self):
        os.environ["DEEPGRAM_MODEL"] = "nova-2"
        self.assertEqual(settings_store.load()["stt_model"], "nova-2")
        os.environ["STT_MODEL"] = "base.en"  # new name wins over old
        self.assertEqual(settings_store.load()["stt_model"], "base.en")

    def test_empty_string_clears_an_override(self):
        settings_store.save({"llm_provider": "google"})
        self.assertEqual(settings_store.load()["llm_provider"], "google")
        settings_store.save({"llm_provider": ""})
        self.assertEqual(settings_store.load()["llm_provider"], "openai")

    def test_reset_semantics_for_booleans(self):
        # The panel's Reset sends '' for checkboxes too — that must CLEAR the
        # override so the default reasserts, never store a truthy override.
        settings_store.save({"allow_skills": True})
        self.assertTrue(settings_store.load()["allow_skills"])
        settings_store.save({"allow_skills": ""})
        self.assertFalse(settings_store.load()["allow_skills"])  # default off

    def test_coercion_of_string_bools_and_numbers(self):
        settings_store.save({"call_volume": "80", "call_sounds": "false"})
        cfg = settings_store.load()
        self.assertEqual(cfg["call_volume"], 80)
        self.assertIs(cfg["call_sounds"], False)

    def test_unknown_keys_are_ignored(self):
        settings_store.save({"allow_sfx": True, "not_a_field": 1})
        stored = json.loads(settings_store.SETTINGS_PATH.read_text())
        self.assertNotIn("allow_sfx", stored)
        self.assertNotIn("not_a_field", stored)


class TestSecrets(_TempStores):
    def test_blank_means_unchanged_and_clear_is_explicit(self):
        secrets_store.save({"openai_api_key": "sk-real"}, [])
        self.assertEqual(secrets_store.get("openai_api_key"), "sk-real")
        secrets_store.save({"openai_api_key": ""}, [])   # untouched masked field
        self.assertEqual(secrets_store.get("openai_api_key"), "sk-real")
        secrets_store.save({}, ["openai_api_key"])       # explicit clear
        self.assertEqual(secrets_store.get("openai_api_key"), "")

    def test_status_never_reveals_material(self):
        secrets_store.save({"anthropic_api_key": "sk-ant-secret123"}, [])
        s = secrets_store.status()["anthropic_api_key"]
        self.assertTrue(s["set"])
        self.assertNotIn("secret123", json.dumps(s))

    def test_apply_to_env_sets_and_reverts(self):
        os.environ["OPENAI_API_KEY"] = "from-dotenv"
        secrets_store.save({"openai_api_key": "from-panel"}, [])
        self.assertEqual(os.environ["OPENAI_API_KEY"], "from-panel")
        secrets_store.save({}, ["openai_api_key"])
        self.assertEqual(os.environ.get("OPENAI_API_KEY"), "from-dotenv")


class TestPromptAssembly(_TempStores):
    def test_call_momentum_rules_are_always_in_the_prompt(self):
        # Observed on real calls: without this block the DJ interviews the
        # caller ("what are you planning tomorrow?"). It must be present
        # regardless of settings — it is not an operator toggle.
        import asyncio

        from station import StationClient

        snapshot = {"dj": {}, "personas": [], "now_playing": {},
                    "state": {}, "session": {}, "schedule": {}}
        persona = {"id": "p_test", "name": "Test DJ", "soul": "A test soul."}

        async def build() -> str:
            station = StationClient()
            try:
                return await prompts.build_system_prompt(
                    station, persona, snapshot=snapshot
                )
            finally:
                await station.aclose()

        text = asyncio.run(build())
        self.assertIn("Keep the call moving", text)
        self.assertIn("dig into the caller's", text)
        self.assertIn("quippy tangent", text)
        # And the operator's own steers still layer on top when set.
        settings_store.save({"style_answering": "keep answers to two sentences"})
        text = asyncio.run(build())
        self.assertIn("keep answers to two sentences", text)
        self.assertIn("Keep the call moving", text)


class TestAdminAuth(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        import admin_auth
        self.auth = admin_auth
        self._old_path = admin_auth.AUTH_PATH
        admin_auth.AUTH_PATH = Path(self._tmp.name) / "admin-auth.json"

    def tearDown(self):
        self.auth.AUTH_PATH = self._old_path
        self._tmp.cleanup()

    def test_set_verify_and_change(self):
        self.assertFalse(self.auth.is_set())
        self.auth.set_password("correct horse battery")
        self.assertTrue(self.auth.is_set())
        self.assertTrue(self.auth.verify("correct horse battery"))
        self.assertFalse(self.auth.verify("wrong"))
        self.assertFalse(self.auth.verify(""))
        self.auth.set_password("new password here")
        self.assertFalse(self.auth.verify("correct horse battery"))
        self.assertTrue(self.auth.verify("new password here"))

    def test_no_plaintext_on_disk(self):
        self.auth.set_password("do-not-store-me")
        raw = self.auth.AUTH_PATH.read_text()
        self.assertNotIn("do-not-store-me", raw)

    def test_lockout_cooldown_then_ban(self):
        # 5 wrong tries -> cooldown; a second round of 5 -> banned until
        # restart. Uses the token server's pure helpers with a fake IP.
        import token_server as ts

        ip = "test-ip-1"
        ts._auth_state.pop(ip, None)
        for _ in range(4):
            msg = ts._auth_fail(ip)
            self.assertIn("tr", msg)          # "N tries left"
        msg = ts._auth_fail(ip)               # 5th -> cooldown starts
        self.assertIn("try again", msg)
        self.assertIsNotNone(ts._auth_gate(ip))

        # Simulate the cooldown expiring, then a second round of failures.
        ts._auth_state[ip]["cooldown_until"] = 0
        self.assertIsNone(ts._auth_gate(ip))
        for _ in range(5):
            msg = ts._auth_fail(ip)
        self.assertIn("blocked until the app restarts", msg)
        self.assertIn("blocked", ts._auth_gate(ip))

        # Success clears everything (and "restart" == fresh state).
        ts._auth_clear(ip)
        self.assertIsNone(ts._auth_gate(ip))


class TestStationConfig(unittest.TestCase):
    def test_extracts_nested_persona_voice_shape(self):
        # SUB/WAVE publishes voices at values.personas[].tts.voice — nested
        # one level below the persona object. The extractor missing that
        # made mirroring silently return nothing.
        import station_config

        payload = {
            "values": {
                "personas": [
                    {"id": "p_abc123", "name": "A", "tts": {"voice": "-VoiceA"}},
                    {"id": "p_def456", "name": "B", "voice": "-VoiceB"},
                    {"id": "p_empty0", "name": "C", "tts": {"voice": ""}},
                ]
            }
        }
        m = station_config._extract_persona_voices(payload)
        self.assertEqual(m.get("p_abc123"), "-VoiceA")
        self.assertEqual(m.get("p_def456"), "-VoiceB")
        self.assertNotIn("p_empty0", m)


class TestMainToolLogic(_TempStores):
    """Imports main.py (heavy: LiveKit plugins) — kept in one class so the
    cost is paid once and the rest of the suite stays fast."""

    @classmethod
    def setUpClass(cls):
        import main  # noqa: F401  (import cost only)
        cls.main = main

    def test_query_variants_strips_the_by_connector(self):
        v = self.main._query_variants("Let It Be by The Beatles")
        self.assertEqual(v[0], "Let It Be by The Beatles")
        self.assertIn("Let It Be The Beatles", v)
        self.assertIn("Let It Be", v)

    def test_query_variants_keeps_titles_containing_by(self):
        v = self.main._query_variants("Stand by Me by Ben E. King")
        self.assertIn("Stand by Me", v)  # rightmost split only

    def test_sfx_never_in_any_tool_list(self):
        cfg = {flag: True for flag in self.main.OPTIONAL_TOOLS}
        for guarded in (False, True):
            allowed = self.main.build_allowed_tools(cfg, guarded=guarded)
            self.assertNotIn("subwave_play_sfx", allowed)
            self.assertNotIn("subwave_list_sfx", allowed)

    def test_guarded_list_drops_on_air_tools_but_keeps_reads(self):
        cfg = {"allow_announcements": True, "allow_skills": True}
        allowed = self.main.build_allowed_tools(cfg, guarded=True)
        self.assertNotIn("subwave_dj_announce", allowed)
        self.assertNotIn("subwave_run_skill", allowed)
        self.assertIn("subwave_list_skills", allowed)
        self.assertIn("subwave_now_playing", allowed)

    def test_wrapped_tools_never_served_raw(self):
        cfg = {"allow_requests": True, "allow_library_search": True}
        allowed = self.main.build_allowed_tools(cfg)
        self.assertNotIn("subwave_request_song", allowed)
        self.assertNotIn("subwave_search_library", allowed)
        et = self.main.effective_tools({**cfg, "avoid_on_air_overlap": True})
        local_names = " ".join(et["local"])
        self.assertIn("subwave_request_song", local_names)
        self.assertIn("subwave_search_library", local_names)

    def test_effective_stt_falls_back_without_keys(self):
        provider, model, note = self.main.effective_stt({"stt_provider": "deepgram"})
        self.assertEqual(provider, "google")  # no deepgram or openai key set
        self.assertIn("falling back", note)
        os.environ["OPENAI_API_KEY"] = "sk-x"
        provider, model, note = self.main.effective_stt({"stt_provider": "deepgram"})
        self.assertEqual(provider, "openai")
        self.assertEqual(model, "gpt-4o-mini-transcribe")

    def test_effective_stt_rejects_cross_provider_model(self):
        provider, model, _ = self.main.effective_stt(
            {"stt_provider": "local", "stt_model": "nova-3"}
        )
        self.assertEqual(provider, "local")
        self.assertEqual(model, "base.en")  # nova-3 is not a local model


if __name__ == "__main__":
    unittest.main(verbosity=2)
