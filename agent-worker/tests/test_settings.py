"""The layered config: file over environment over DEFAULTS, and the rules about what a setting is allowed to be.

Split out of test_sidecar.py; see tests/__init__.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path
import settings as settings_store
from tests.support import AGENT_WORKER, REPO, _TempStores


class TestTheEmbedAllowlistIsASetting(_TempStores):
    """CALLIN_ALLOWED_ORIGINS became the allowed_origins setting in 0.10.63 so
    allowing a new embed site is a settings save, not a container recreate.
    That promise has two halves: the layering (stored beats env, blank falls
    through), and the HTTP edge reading it live — a restart must never be part
    of the contract, which is why origin_allowed is exercised here rather than
    just the round-trip."""

    def _req(self, origin, host="callin.example"):
        import types

        return types.SimpleNamespace(headers={"Host": host, "Origin": origin})

    def test_stored_beats_env_and_blank_falls_through(self):
        os.environ["CALLIN_ALLOWED_ORIGINS"] = "https://env.example"
        try:
            self.assertEqual(settings_store.load()["allowed_origins"],
                             "https://env.example")
            settings_store.save({"allowed_origins": "https://stored.example"})
            self.assertEqual(settings_store.load()["allowed_origins"],
                             "https://stored.example")
            settings_store.save({"allowed_origins": ""})
            self.assertEqual(settings_store.load()["allowed_origins"],
                             "https://env.example")
        finally:
            del os.environ["CALLIN_ALLOWED_ORIGINS"]

    def test_the_edge_honours_a_save_without_a_restart(self):
        from api.wire import origin_allowed

        self.assertFalse(origin_allowed(self._req("https://radio.example")))
        settings_store.save({"allowed_origins": "https://radio.example"})
        self.assertTrue(origin_allowed(self._req("https://radio.example")))
        self.assertFalse(origin_allowed(self._req("https://evil.example")))


class TestSettings(_TempStores):
    def test_defaults_when_nothing_stored(self):
        cfg = settings_store.load()
        # The 0.10.80 fresh-install defaults: the built-in Whisper, and no
        # AI provider until the operator picks one.
        self.assertEqual(cfg["stt_provider"], "local")
        self.assertEqual(cfg["stt_model"], "base.en")
        self.assertEqual(cfg["llm_provider"], "")
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
        # A non-blank default, so the fall-through is visible as a VALUE:
        # llm_provider's default became genuinely blank at 0.10.80 and would
        # prove clearing and storing-empty indistinguishable.
        settings_store.save({"stt_provider": "openai"})
        self.assertEqual(settings_store.load()["stt_provider"], "openai")
        settings_store.save({"stt_provider": ""})
        self.assertEqual(settings_store.load()["stt_provider"], "local")

    def test_reset_semantics_for_booleans(self):
        # The panel's Reset sends '' for checkboxes too — that must CLEAR the
        # override so the default reasserts, never store a truthy override.
        settings_store.save({"strip_stage_directions": False})
        self.assertFalse(settings_store.load()["strip_stage_directions"])
        settings_store.save({"strip_stage_directions": ""})
        self.assertTrue(settings_store.load()["strip_stage_directions"])

    def test_reset_semantics_for_permissions(self):
        # Same rule, and it matters more here: the stored value is a TIER, and
        # every tier name including "off" is a non-empty string. A Reset that
        # stored "" rather than clearing would leave a permission whose value
        # is neither a tier nor absent.
        settings_store.save({"allow_skills": "open"})
        self.assertEqual(settings_store.load()["allow_skills"], "open")
        settings_store.save({"allow_skills": ""})
        # Falls to the 0.10.80 default tier (guest), not to "off": the store
        # already carries _rev, so the pre-0.10.80 stamp does not apply.
        self.assertEqual(settings_store.load()["allow_skills"], "guest")

    def test_coercion_of_string_bools_and_numbers(self):
        settings_store.save({"call_volume": "80", "call_sounds": "false"})
        cfg = settings_store.load()
        self.assertEqual(cfg["call_volume"], 80)
        self.assertIs(cfg["call_sounds"], False)

    def test_a_url_field_refuses_something_that_is_not_a_url(self):
        # A real deployment ran with "Michael" in the MCP endpoint — the
        # browser autofilled a name into the box — so the agent got NO station
        # tools on any call and invented library results instead. Nothing
        # downstream complained, which is the part that made it expensive.
        problem = settings_store.complain({"station_mcp_url": "Michael"})
        self.assertIsNotNone(problem)
        self.assertIn("URL", problem)
        self.assertIn("Michael", problem)          # says what it saw
        self.assertIn("empty", problem)            # and how to fix it

        for field in settings_store.URL_FIELDS:
            self.assertIsNotNone(settings_store.complain({field: "nonsense"}), field)
            self.assertIsNone(settings_store.complain({field: ""}))          # clears
            self.assertIsNone(settings_store.complain({field: "http://x:7700/api"}))
            self.assertIsNone(settings_store.complain({field: "https://x/api"}))
        # Everything else is still free text.
        self.assertIsNone(settings_store.complain({"greeting": "just say hi"}))

    def test_a_url_already_stored_broken_falls_back_instead_of_breaking(self):
        # Validation on save can't help a config that was already saved wrong,
        # and handing an unusable URL to the agent is worse than the default.
        settings_store.save({"station_mcp_url": "Michael",
                             "station_base_url": "http://box:7700/api"})
        self.assertEqual(settings_store.station_mcp_url(),
                         "http://box:7700/api/mcp")
        settings_store.save({"station_base_url": "Michael"})
        self.assertTrue(settings_store.station_base_url().startswith("http"))

    def test_unknown_keys_are_ignored(self):
        settings_store.save({"allow_sfx": True, "not_a_field": 1})
        stored = json.loads(settings_store.SETTINGS_PATH.read_text())
        self.assertNotIn("allow_sfx", stored)
        self.assertNotIn("not_a_field", stored)


class TestSettingsThatAreOnlyWrongTogether(_TempStores):
    """Every field validated itself and nothing validated a pair, so a floor
    above its own ceiling saved without complaint and what happened afterwards
    was nobody's intention."""

    def test_a_hangup_floor_above_the_ceiling_is_refused(self):
        why = settings_store.complain(
            {"min_call_seconds": 600, "max_call_seconds": 300})
        self.assertIsNotNone(why)
        self.assertIn("600", why)

    def test_a_daily_cap_below_the_hourly_one_is_refused(self):
        why = settings_store.complain(
            {"calls_per_hour": 30, "calls_per_day": 10})
        self.assertIsNotNone(why)
        self.assertIn("meaningless", why)

    def test_endpointing_delays_the_wrong_way_round_are_refused(self):
        why = settings_store.complain(
            {"min_endpointing_delay": 3.0, "max_endpointing_delay": 1.0})
        self.assertIsNotNone(why)

    def test_the_pair_is_checked_against_what_is_already_stored(self):
        # A patch usually carries one half. Saving a ceiling under the floor
        # already on disk has to be caught too.
        settings_store.save({"min_call_seconds": 600})
        self.assertIsNotNone(settings_store.complain({"max_call_seconds": 300}))

    def test_sensible_pairs_still_save(self):
        self.assertIsNone(settings_store.complain(
            {"min_call_seconds": 60, "max_call_seconds": 600}))
        # 0 means "no limit" on the caps, so it can never be the smaller one.
        self.assertIsNone(settings_store.complain(
            {"calls_per_hour": 30, "calls_per_day": 0}))


class TestOneSettingReplacingAnotherSaysSo(unittest.TestCase):
    """Writing an Opening line overrides Greeting style completely — the
    greeting code reads `cfg["greeting"] or the style default`. Showing both
    with nothing saying which wins is the shape 0.9.61 took out of
    front_access, and it was still here."""

    def test_greeting_style_hides_once_an_opening_line_exists(self):
        needs = settings_store.SCHEMA["greeting_style"].get("needs")
        self.assertEqual(needs, ("greeting", False))

    def test_the_widget_understands_that_rule(self):
        # The panel is what actually hides it, and it lives in another
        # language with no test runner — so this pins the one line that
        # implements it.
        js = (REPO / "web-widget" / "panel.js").read_text(
            encoding="utf-8")
        self.assertIn("want === false", js,
                      "panel.js cannot honour a `needs` of False, so the field "
                      "would stay visible and keep looking like it works")


class TestNoSettingIsSmuggledThroughTheEnvironment(unittest.TestCase):
    """tts_mode was written into os.environ by four different modules purely so
    _default_adapter_path could read it back — a setting laundered through
    process-global state with no owner. In the token server that state is
    shared by every concurrent request, so two operators testing different
    backends raced each other."""

    def test_nothing_writes_tts_mode_into_the_environment(self):
        import re

        root = AGENT_WORKER
        offenders = []
        # The whole tree, not a list of directories: the /test/tts endpoint
        # this was written about has since moved into api/, and a guard that
        # has to be extended by hand every time a package appears is a guard
        # that eventually stops looking where the code is.
        for path in root.rglob("*.py"):
            if path.name == "test_sidecar.py" or "__pycache__" in path.parts:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                # Comments describing the old pattern are the point of the
                # comments — only an actual assignment counts.
                if line.strip().startswith("#"):
                    continue
                if re.search(r'os\.environ\[\s*[\"\']TTS_MODE[\"\']\s*\]\s*=', line):
                    offenders.append(f"{path.name}:{n}")
        self.assertEqual(offenders, [], f"tts_mode is being smuggled: {offenders}")

    def test_the_adapter_is_told_its_mode(self):
        from tts_adapter import _default_adapter_path

        self.assertIn("local", _default_adapter_path("local").name)
        self.assertIn("openai", _default_adapter_path("cloud").name)


class TestAConfigValueCannotNameAFileOnTheDisk(unittest.TestCase):
    """`tts_adapter` arrives in the BODY of /test/tts and /test/speed.

    The resolution was the same three lines copied into three modules, and all
    three read "join it to ADAPTER_DIR unless it is absolute" — so an absolute
    path went straight to open(), and ../ in a relative one walked out of the
    directory before exists() ever looked. A request could name any file on the
    disk and learn whether it existed and whether it parsed as JSON; in
    first-run mode that needs no password.

    Same family as the withheld-key fix: configuration that ARRIVES in a
    request was being treated as operator intent.
    """

    def test_an_absolute_path_is_refused(self):
        from tts_adapter import resolve_adapter

        for hostile in ("/etc/passwd", "/data/secrets.json",
                        "C:\\Windows\\win.ini"):
            with self.subTest(path=hostile):
                self.assertIsNone(resolve_adapter(hostile))

    def test_traversal_is_refused(self):
        from tts_adapter import resolve_adapter

        for hostile in ("../../etc/passwd", "../secrets.json",
                        "sub/dir/openai-cloud.json", "..\\..\\secrets.json"):
            with self.subTest(path=hostile):
                self.assertIsNone(resolve_adapter(hostile))

    def test_a_non_json_name_is_refused(self):
        from tts_adapter import resolve_adapter

        self.assertIsNone(resolve_adapter("openai-cloud.yaml"))
        self.assertIsNone(resolve_adapter("secrets"))

    def test_a_real_adapter_still_resolves(self):
        # The other half. Refusing everything would be a silent outage: the
        # DJ would fall back to the default adapter and sound wrong.
        from tts_adapter import ADAPTER_DIR, resolve_adapter

        shipped = sorted(p.name for p in ADAPTER_DIR.glob("*.json")
                         if not p.name.startswith("_"))
        self.assertTrue(shipped, "no adapters shipped — nothing to check")
        for name in shipped:
            with self.subTest(adapter=name):
                got = resolve_adapter(name)
                self.assertIsNotNone(got, f"{name} stopped resolving")
                self.assertTrue(Path(got).is_file())

    def test_the_deploy_time_env_escape_still_works(self):
        # TTS_ADAPTER_CONFIG is set by whoever runs the container, not by a
        # request, and pointing it at a mounted file outside the image is
        # supported. Honoured only when it matches that value exactly.
        from tts_adapter import resolve_adapter

        old = os.environ.get("TTS_ADAPTER_CONFIG")
        os.environ["TTS_ADAPTER_CONFIG"] = "/mnt/custom/adapter.json"
        try:
            self.assertEqual(resolve_adapter("/mnt/custom/adapter.json"),
                             "/mnt/custom/adapter.json")
            # ...and not for a different absolute path in the same request.
            self.assertIsNone(resolve_adapter("/etc/passwd"))
        finally:
            if old is None:
                os.environ.pop("TTS_ADAPTER_CONFIG", None)
            else:
                os.environ["TTS_ADAPTER_CONFIG"] = old


class TestTheDataDirCheckCannotStopTheWorker(unittest.TestCase):
    """It runs at module scope in main.py, before the worker registers with
    LiveKit, so anything it raises means no calls at all. A diagnostic written
    to explain a broken data directory must not be able to break more than the
    directory did — the same trade record.write() makes when it refuses to cost
    the on-air handoff for the sake of a JSON file.

    Deliberately NOT platform-gated. The first version of this test lived in a
    POSIX-only class, was skipped on the author's machine, and reached CI
    broken — the third time in one afternoon that a skip hid a defect. The
    `hasattr(os, "getuid")` check moved inside the guarded function so the
    swallow itself runs everywhere and this test means something everywhere.
    """

    def test_a_failure_inside_it_is_swallowed_and_logged(self):
        class _Exploding:
            @property
            def parent(self):
                raise RuntimeError("the data directory is unreachable")

        original = settings_store.SETTINGS_PATH
        try:
            settings_store.SETTINGS_PATH = _Exploding()
            # assertLogs IS the assertion: check_data_dir must not raise, and
            # must have reached the swallow rather than returning early for
            # some unrelated reason.
            with self.assertLogs("callin.settings", level="DEBUG") as caught:
                settings_store.check_data_dir()
        finally:
            settings_store.SETTINGS_PATH = original
        self.assertTrue(
            any("data directory" in m for m in caught.output),
            f"something else was swallowed: {caught.output}")

    def test_a_healthy_directory_says_nothing(self):
        # The other half: it must not cry wolf on a directory that is fine.
        with self.assertNoLogs("callin.settings", level="ERROR"):
            settings_store.check_data_dir()


class TestBootLaysTheDataSkeleton(_TempStores):
    """One boot makes `ls data/` show the real shape (0.10.71): the operator
    should see calls/, sounds/ and voicemail/ on day one, not folders
    appearing weeks apart as features first fire. Directories ONLY — the JSON
    stores stay lazy because their absence is a state the app reads (no
    admin-auth.json means "no password yet"), and creating them empty would
    say nothing true."""

    def test_the_skeleton_directories_appear(self):
        data_dir = settings_store.SETTINGS_PATH.parent
        data_dir.mkdir(parents=True, exist_ok=True)
        settings_store.check_data_dir()
        for name in ("calls", "sounds", "voicemail"):
            with self.subTest(name=name):
                self.assertTrue((data_dir / name).is_dir())

    def test_no_json_store_is_invented(self):
        data_dir = settings_store.SETTINGS_PATH.parent
        data_dir.mkdir(parents=True, exist_ok=True)
        settings_store.check_data_dir()
        self.assertFalse(settings_store.SETTINGS_PATH.exists(),
                         "an empty settings.json at boot says nothing true")

    def test_an_unmounted_dir_is_left_alone(self):
        # No data directory at all means nothing is mounted — creating one
        # here would put state where the operator never asked for it.
        import shutil

        data_dir = settings_store.SETTINGS_PATH.parent
        if data_dir.exists():
            shutil.rmtree(data_dir)
        settings_store.check_data_dir()
        self.assertFalse(data_dir.exists())


class TestTheKeypairCanLiveInOneFile(unittest.TestCase):
    """The LiveKit keypair used to live in two files that had to match by
    hand — livekit.yaml for the media server, .env for these processes — and
    a fresh install tripping over the dance is what prompted the fallback:
    when the env supplies nothing, api/env.py reads the pair from the
    mounted livekit.yaml (stdlib parse, one documented shape)."""

    def _with_yaml(self, text):
        import tempfile
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = os.path.join(td.name, "livekit.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        os.environ["LIVEKIT_CONFIG_PATH"] = path
        self.addCleanup(lambda: os.environ.pop("LIVEKIT_CONFIG_PATH", None))

    def test_the_pair_is_read_from_the_yaml(self):
        from api import env as api_env

        self._with_yaml("port: 7880\nkeys:\n  mykey: s3cretvalue\n")
        self.assertEqual(api_env._keys_from_livekit_yaml(),
                         ("mykey", "s3cretvalue"))

    def test_env_wins_and_apply_fills_only_a_gap(self):
        from api import env as api_env

        self._with_yaml("keys:\n  yamlkey: yamlsecret\n")
        old_key = os.environ.pop("LIVEKIT_API_KEY", None)
        old_sec = os.environ.pop("LIVEKIT_API_SECRET", None)
        try:
            api_env.apply_livekit_keys()
            self.assertEqual(os.environ.get("LIVEKIT_API_KEY"), "yamlkey")
            self.assertEqual(os.environ.get("LIVEKIT_API_SECRET"), "yamlsecret")
            # A pair the env already holds is never overwritten.
            os.environ["LIVEKIT_API_SECRET"] = "envwins"
            os.environ["LIVEKIT_API_KEY"] = "envkey"
            api_env.apply_livekit_keys()
            self.assertEqual(os.environ["LIVEKIT_API_SECRET"], "envwins")
        finally:
            for name, val in (("LIVEKIT_API_KEY", old_key),
                              ("LIVEKIT_API_SECRET", old_sec)):
                if val is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = val

    def test_comments_and_quotes_do_not_confuse_the_parse(self):
        from api import env as api_env

        self._with_yaml("keys:\n  devkey: \"quoted\"  # a comment\n")
        self.assertEqual(api_env._keys_from_livekit_yaml(),
                         ("devkey", "quoted"))


class TestUploadedSoundsCannotFillTheVolume(unittest.TestCase):
    """Each file was capped at 2MB and the collection at nothing, so the same
    2MB could be uploaded until the volume filled — the volume the settings,
    the keys and the call records live on."""

    def test_the_caps_are_sane_for_five_sounds(self):
        from api import sounds as api_sounds

        self.assertGreaterEqual(api_sounds.MAX_SOUND_FILES, 5)
        self.assertLessEqual(api_sounds.MAX_SOUND_FILES, 100)
        self.assertLessEqual(
            api_sounds.MAX_SOUND_TOTAL_BYTES,
            api_sounds.MAX_SOUND_FILES * api_sounds.MAX_SOUND_BYTES,
            "the total cap is higher than the per-file cap allows, so it can "
            "never be the thing that stops an upload")


class TestTurnTakingDelaysAreOptOut(unittest.TestCase):
    """0 means "leave the SDK's tuned default alone", not "no delay". Passing a
    literal zero would make the DJ answer the instant the caller stopped making
    sound, which is not patience — it is interrupting."""

    def test_unset_passes_nothing_at_all(self):
        from call.session import turn_handling

        self.assertNotIn("endpointing", turn_handling({}))
        self.assertNotIn("endpointing", turn_handling(
            {"min_endpointing_delay": 0, "max_endpointing_delay": 0}))

    def test_a_real_value_is_passed_through(self):
        from call.session import turn_handling

        self.assertEqual(
            turn_handling({"min_endpointing_delay": 0.8})["endpointing"],
            {"min_delay": 0.8})

    def test_nonsense_is_ignored_rather_than_raised(self):
        from call.session import turn_handling

        self.assertNotIn(
            "endpointing", turn_handling({"min_endpointing_delay": "soon"}))


class TestTurnTakingSettingsReachTheCall(unittest.TestCase):
    """The three turn-taking settings were in the panel, documented, saved —
    and silently ignored on every call for as long as they have existed.

    `AgentSession` accepts `allow_interruptions`, `min_endpointing_delay` and
    `max_endpointing_delay` as arguments, but only reads them on the branch
    where `turn_handling` was NOT passed. We passed both, so the SDK took the
    dict and dropped the three on the floor: `allow_interruptions=False` still
    resolved to `enabled: True`, and endpointing stayed at the stock 0.3/2.5.

    That is why the DJ came back chopped mid-sentence on real calls —
    `min_duration` is half a second of SOUND, not words, and the station stream
    playing into the caller's room clears that bar. The documented remedy could
    not be applied because the setting never arrived.

    So this asserts against what the SDK RESOLVED, not against the dict we
    built. A test that checks our own argument shape is exactly what passed
    while this was broken.
    """

    def _resolved(self, cfg: dict):
        import asyncio
        import warnings

        from livekit.agents import AgentSession

        from call.session import turn_handling

        # Built inside a loop: AgentSession.__init__ calls get_event_loop(), so
        # constructing one at import-time depends on whichever test ran last
        # leaving a loop lying around. It passed alone and errored in the suite.
        async def build():
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return AgentSession(turn_handling=turn_handling(cfg)).options

        return asyncio.run(build())

    def test_turning_interruptions_off_actually_turns_them_off(self):
        self.assertIs(
            self._resolved({"allow_interruptions": False}).interruption["enabled"],
            False,
            "the caller can still talk over the DJ with the setting off — the "
            "operator's only remedy for the station bleeding into the mic",
        )
        self.assertIs(
            self._resolved({"allow_interruptions": True}).interruption["enabled"],
            True)

    def test_the_endpointing_delays_arrive_as_set(self):
        got = self._resolved(
            {"min_endpointing_delay": 2.5, "max_endpointing_delay": 9.0}
        ).endpointing
        self.assertEqual((got["min_delay"], got["max_delay"]), (2.5, 9.0))

    def test_unset_delays_leave_the_sdk_tuned_defaults_alone(self):
        stock = self._resolved({}).endpointing
        self.assertEqual((stock["min_delay"], stock["max_delay"]), (0.3, 2.5))

    def test_the_interruption_floor_is_raisable(self):
        # The fix for the chopped turns: how much SOUND it takes to stop the DJ.
        self.assertEqual(
            self._resolved({"min_interruption_secs": 1.6}).interruption["min_duration"],
            1.6)
        self.assertEqual(
            self._resolved({}).interruption["min_duration"], 0.5,
            "unset must leave the SDK's own floor alone")

    def test_the_shipped_default_clears_the_sdk_floor(self):
        # The line above is about an ABSENT key; this is about the value a
        # deployment that never opens the panel actually runs on, and they were
        # not the same question. The shipped default was 0 — which means "use
        # the SDK's 0.5s" — and 0.5s of SOUND is half a second of the record
        # playing in the caller's room. A 0.97.23 call came back with three
        # one-word DJ turns on a box that had never touched the setting.
        import settings as settings_store

        self.assertGreater(
            float(settings_store.FIELDS["min_interruption_secs"][1]), 0.5,
            "the shipped default must clear the SDK's own floor, or every "
            "deployment that never opens the panel gets the chopped turns")

    def test_preemptive_generation_is_still_off(self):
        # Folding three settings into the same dict must not lose the thing
        # that dict was originally there for: a speculative turn carrying a
        # tool call makes Gemini reject the whole conversation with a 400.
        self.assertIs(
            self._resolved({}).preemptive_generation["enabled"], False)


class TestANeighbouringServiceIsNotOnLocalhost(_TempStores):
    """`localhost` was the autofill for Ollama and for local TTS, and it is
    wrong by construction: this runs in a container, so localhost is the
    container itself and never where those servers live. The panel offered it
    anyway, and the log filled with `ollama model list unavailable at
    http://localhost:11434` with nothing saying why."""

    def test_the_default_follows_the_station(self):
        settings_store.save({"station_base_url": "http://192.168.1.245:7700"})
        self.assertEqual(
            settings_store.provider_base_urls()["ollama"],
            "http://192.168.1.245:11434/v1",
        )
        self.assertEqual(
            settings_store.tts_base_urls()["local"], "http://192.168.1.245:8001"
        )

    def test_an_explicit_environment_variable_still_wins(self):
        settings_store.save({"station_base_url": "http://192.168.1.245:7700"})
        os.environ["OLLAMA_BASE_URL"] = "http://elsewhere:11434/v1"
        try:
            self.assertEqual(
                settings_store.provider_base_urls()["ollama"],
                "http://elsewhere:11434/v1",
            )
        finally:
            os.environ.pop("OLLAMA_BASE_URL", None)

    def test_the_cloud_entries_are_untouched(self):
        urls = settings_store.provider_base_urls()
        self.assertEqual(urls["openrouter"], "https://openrouter.ai/api/v1")
        self.assertEqual(urls["openai"], "")
        self.assertEqual(settings_store.tts_base_urls()["cloud"],
                         "https://api.openai.com")


class TestTheModelWarmedIsTheModelUsed(_TempStores):
    """Switching STT provider without also changing the model left "nova-3" —
    a Deepgram name — going to faster-whisper, which rejected it: `Invalid
    model size 'nova-3'`, prewarm skipped, and the first caller paid the ~7s
    load mid-conversation anyway. The prewarm has to resolve the model the same
    way the call does, or it warms a different one."""

    def test_a_model_from_another_provider_does_not_reach_the_prewarm(self):
        from call.providers import effective_stt

        provider, model, note = effective_stt(
            {"stt_provider": "local", "stt_model": "nova-3"}
        )
        self.assertEqual(provider, "local")
        self.assertEqual(model, "base.en")
        self.assertIn("nova-3", note)

    def test_the_prewarm_asks_the_same_question_the_call_does(self):
        # Mutation guard: reading cfg["stt_model"] directly here is the bug.
        source = (AGENT_WORKER / "main.py").read_text(encoding="utf-8")
        prewarm = source.split("def prewarm(")[1].split("\nasync def ")[0]
        self.assertIn("effective_stt(cfg)", prewarm)
        self.assertNotIn('cfg.get("stt_model")', prewarm)


class TestTheProviderTablesAgreeWithEachOther(unittest.TestCase):
    """Four tables describe the LLM providers — the key each needs, the model
    fallbacks, the dropdown labels, the OpenAI-protocol hosts — and they live
    in settings.py precisely so they cannot drift from the UI. This holds them
    to each other, so adding a provider to one table and not the rest fails
    here instead of as a dropdown entry that silently cannot work."""

    def test_every_provider_has_a_label_and_model_entry(self):
        import settings as settings_store

        for provider in settings_store.LLM_PROVIDER_KEY:
            with self.subTest(provider=provider):
                self.assertIn(provider, settings_store.LLM_PROVIDER_LABELS)
                self.assertIn(provider, settings_store.MODEL_CHOICES)

    def test_every_named_key_is_a_field_the_panel_can_store(self):
        # A provider keyed on a field the secrets store never writes would
        # list itself only after a key that cannot be entered.
        import secrets_store
        import settings as settings_store

        for provider, field in settings_store.LLM_PROVIDER_KEY.items():
            if field is None:
                continue
            with self.subTest(provider=provider):
                self.assertIn(field, secrets_store.SECRET_FIELDS)

    def test_every_protocol_host_is_a_full_provider(self):
        import settings as settings_store

        for provider, (host, _default) in settings_store.OPENAI_PROTOCOL_HOSTS.items():
            with self.subTest(provider=provider):
                self.assertIn(provider, settings_store.LLM_PROVIDER_KEY)
                self.assertTrue(host.startswith("https://"))
                self.assertIn(provider, settings_store.provider_base_urls())

    def test_an_aggregator_without_a_model_refuses_with_a_sentence(self):
        # Their catalogues are namespaced and move; a guessed default id would
        # 404 on every utterance while looking configured. The refusal has to
        # say where to fix it.
        from call.providers import build_llm

        for provider in ("requesty", "gateway"):
            with self.subTest(provider=provider):
                with self.assertRaises(ValueError) as caught:
                    build_llm({"llm_provider": provider, "llm_model": ""})
                self.assertIn("Brains", str(caught.exception))

    def test_openai_compatible_without_an_endpoint_refuses_with_a_sentence(self):
        from call.providers import build_llm

        with self.assertRaises(ValueError) as caught:
            build_llm({"llm_provider": "openai-compatible", "llm_model": "x",
                       "llm_base_url": ""})
        self.assertIn("Endpoint", str(caught.exception))

    def test_locca_with_a_blank_endpoint_dials_the_host_default(self):
        # The one provider where a blank Endpoint still names a server —
        # mirrored from the station's DEFAULT_LOCCA_BASE_URL, so an operator
        # whose station thinks on locca picks the name here and is done.
        import settings as settings_store
        from call.providers import build_llm

        model = build_llm({"llm_provider": "locca", "llm_model": "x",
                           "llm_base_url": ""})
        self.assertIn(settings_store.LOCCA_BASE_URL_DEFAULT,
                      str(model._client.base_url))
        # An explicit endpoint always wins over the well-known address.
        model = build_llm({"llm_provider": "locca", "llm_model": "x",
                           "llm_base_url": "http://10.0.0.5:9999/v1"})
        self.assertIn("10.0.0.5:9999", str(model._client.base_url))

    def test_a_protocol_provider_dials_its_own_host(self):
        # The whole branch is one base_url away from posting a DeepSeek key
        # to api.openai.com.
        import os

        from call.providers import build_llm

        # The openai-compatible client wants SOME key to construct. This test
        # is about the base_url, not the key, so give it a throwaway — without
        # it the test only passed because an earlier MODULE happened to set
        # OPENAI_API_KEY, which the parallel runner exposed (each module runs
        # in its own process, so cross-module env leakage is gone).
        old = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-test"
        try:
            model = build_llm({"llm_provider": "deepseek", "llm_model": ""})
            self.assertIn("api.deepseek.com", str(model._client.base_url))
        finally:
            if old is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = old


class TestEverySecretRendersSomewhere(unittest.TestCase):
    """Keys render per-section now (secrets_store.SECRET_GROUPS), into the
    panel's .keyblock divs. A group name with no matching div is a key the
    operator can never enter — the exact unreachable-setting failure
    TestPanelMarkup guards, one payload over."""

    def test_every_group_has_a_key_block_in_the_markup(self):
        import re

        import secrets_store
        from tests.support import REPO

        html = (REPO / "web-widget" / "panel.html").read_text(encoding="utf-8")
        blocks = set(re.findall(r'class="keyblock" id="keys_([a-z]+)"', html))
        for field, group in secrets_store.SECRET_GROUPS.items():
            with self.subTest(key=field):
                self.assertIn(group, blocks,
                              f"{field} is grouped under {group!r}, and no "
                              f"keys_{group} block exists to render it")

    def test_every_secret_is_grouped_and_explained(self):
        import secrets_store

        for field in secrets_store.SECRET_FIELDS:
            with self.subTest(key=field):
                self.assertIn(field, secrets_store.SECRET_GROUPS)
                self.assertTrue(secrets_store.SECRET_HELP.get(field),
                                "a key with no help line is a vendor name in "
                                "a list, asking the operator to already know")


class TestTheGuestExpiryMovedToHoursWithoutMovingAnyonesExpiry(_TempStores):
    """0.9.139 renamed guest_session_minutes to hours — day-shaped answers.
    A stored minutes value keeps its real duration, rounded UP, so nobody's
    handed-out code expires earlier after an upgrade; 0 stays 0."""

    def test_minutes_migrate_up_and_zero_stays_zero(self):
        import json

        import settings as settings_store

        settings_store.SETTINGS_PATH.write_text(
            json.dumps({"guest_session_minutes": 90}), encoding="utf-8")
        self.assertEqual(2, settings_store.load()["guest_session_hours"])
        settings_store.SETTINGS_PATH.write_text(
            json.dumps({"guest_session_minutes": 0}), encoding="utf-8")
        self.assertEqual(0, settings_store.load()["guest_session_hours"],
                         "stored 0 minutes was the operator explicitly "
                         "choosing 'until Sign out' — the migration must "
                         "carry that choice, not replace it with the new "
                         "24-hour default")


class TestACommentedEnvValueIsNamedAtBoot(_TempStores):
    """compose's env_file format has no inline comments — `KEY=value # note`
    puts the note INTO the value, silently. A real container ran with
    CALLIN_INTERNAL_URL holding half a sentence of English (0.10.82); the
    boot check names such values instead of leaving each to be found by
    symptom."""

    def test_the_leak_is_named_and_a_hashy_password_is_not(self):
        import settings as settings_store

        os.environ["STT_MODEL"] = "nova-3            # model for the provider"
        # A '#' with no whitespace before it is a legitimate value (a
        # password, say) and must not be reported as a leak.
        os.environ["SUBWAVE_ADMIN_PASS"] = "p#ssw0rd"
        try:
            with self.assertLogs("callin.settings", level="ERROR") as cm:
                settings_store._warn_commented_env()
            joined = "\n".join(cm.output)
            self.assertIn("STT_MODEL", joined)
            self.assertNotIn("SUBWAVE_ADMIN_PASS", joined)
        finally:
            os.environ.pop("SUBWAVE_ADMIN_PASS", None)

    def test_a_clean_environment_stays_quiet(self):
        import settings as settings_store

        with self.assertNoLogs("callin.settings", level="ERROR"):
            settings_store._warn_commented_env()

    def test_a_poisoned_mcp_env_still_derives_a_real_url(self):
        # The leak's sharpest edge, from the operator's NAS: SUBWAVE_MCP_URL
        # holding "# blank derives {SUBWAVE_BASE_URL}/mcp" reached httpx as a
        # request URL. The sane-URL helper must shrug it off and derive.
        import settings as settings_store

        old = os.environ.get("SUBWAVE_MCP_URL")
        os.environ["SUBWAVE_MCP_URL"] = "# blank derives {SUBWAVE_BASE_URL}/mcp"
        try:
            url = settings_store.station_mcp_url()
            self.assertTrue(url.startswith("http"), url)
            self.assertTrue(url.endswith("/mcp"), url)
        finally:
            if old is None:
                os.environ.pop("SUBWAVE_MCP_URL", None)
            else:
                os.environ["SUBWAVE_MCP_URL"] = old


class TestAnUpgradeClosesNoDoorAndHandsOutNoPower(_TempStores):
    """0.10.80 changed the fresh-install defaults: front_access became
    admin-only and the permission grants became a real tier ladder. The
    0.9.61 rule is that a default change must never alter what an existing
    deployment does — a store written before 0.10.80 (no _rev marker) is
    stamped with the doors and grants it was actually running."""

    def _write_store(self, data: dict) -> None:
        import json

        import settings as settings_store

        settings_store.SETTINGS_PATH.write_text(
            json.dumps(data), encoding="utf-8")

    def test_an_old_store_keeps_its_open_door_and_off_grants(self):
        import settings as settings_store

        # Any pre-0.10.80 store: has keys, has no _rev marker.
        self._write_store({"llm_provider": "openai"})
        cfg = settings_store.load()
        self.assertEqual(cfg["front_access"], "auto",
                         "an upgrade must not close a line that was open")
        for field in ("allow_announcements", "allow_skills",
                      "allow_exact_queue", "allow_favorite",
                      "allow_unfavorite", "allow_skip_track",
                      "allow_dj_segment", "allow_takeover",
                      "allow_album_queue"):
            self.assertEqual(cfg[field], "off",
                             f"an upgrade handed out {field}")

    def test_a_current_store_gets_the_new_defaults(self):
        import settings as settings_store

        # A store first written by 0.10.80+: save() stamps the generation
        # marker, so leaving a field unset means the NEW default, not the
        # preserved old one.
        settings_store.save({"llm_provider": "openai"})
        cfg = settings_store.load()
        self.assertEqual(cfg["front_access"], "admin")
        self.assertEqual(cfg["allow_skills"], "guest")
        self.assertEqual(cfg["allow_takeover"], "admin")
        self.assertEqual(cfg["allow_favorite"], "open")
        self.assertEqual(cfg["allow_album_queue"], "guest")

    def test_saving_an_old_store_locks_its_behaviour_in(self):
        import json

        import settings as settings_store

        self._write_store({"llm_provider": "openai"})
        settings_store.save({"llm_model": "gpt-4.1-mini"})
        raw = json.loads(
            settings_store.SETTINGS_PATH.read_text(encoding="utf-8"))
        # The stamps are now real stored values, so the preserved behaviour
        # survives however many upgrades come later.
        self.assertEqual(raw.get("front_access"), "auto")
        self.assertEqual(raw.get("allow_takeover"), "off")
        self.assertEqual(raw.get("_rev"), settings_store.STORE_REV)

    def test_the_voice_backend_moved_the_same_way_a_release_later(self):
        # 0.10.85 blanked tts_mode's default. Three stores, three answers:
        # pre-0.10.80 (no _rev) keeps the cloud shape it was running; a
        # 0.10.80-84 store (_rev 2) predates the change too and keeps cloud —
        # WITHOUT re-receiving rev 2's stamps, which is what the per-block
        # generation gates exist for; a current store left blank means blank.
        import settings as settings_store

        self._write_store({"llm_provider": "openai"})
        self.assertEqual(settings_store.load()["tts_mode"], "cloud")
        self._write_store({"_rev": 2})
        cfg = settings_store.load()
        self.assertEqual(cfg["tts_mode"], "cloud")
        self.assertEqual(cfg["front_access"], "admin",
                         "a rev-2 store took rev 2's stamps again")
        # A CURRENT store: fresh file, written by this generation's save().
        settings_store.SETTINGS_PATH.unlink()
        settings_store.save({"llm_provider": "openai"})
        self.assertEqual(settings_store.load()["tts_mode"], "")

    def test_an_unpicked_voice_refuses_with_the_fix_in_the_message(self):
        from call.providers import build_tts

        with self.assertRaises(ValueError) as ctx:
            build_tts({"tts_mode": ""}, "some-voice")
        self.assertIn("no voice backend", str(ctx.exception))

    def test_the_chat_ceiling_moved_to_minutes_without_moving_anyones(self):
        import settings as settings_store

        self._write_store({"chat_max_hours": 2})
        self.assertEqual(120, settings_store.load()["chat_max_minutes"])
        # Never set = the new default, in the new unit.
        self._write_store({"llm_provider": "openai"})
        self.assertEqual(10, settings_store.load()["chat_max_minutes"])

    def test_the_receipt_placement_survives_its_promotion_to_every_door(self):
        # 0.10.92 renamed chat_action_cards to action_cards when it grew from
        # chat-only to covering calls and voicemail too. A chat-era answer is
        # the operator's answer for every door and is carried across; a store
        # that never set it takes the booth-wide default.
        import settings as settings_store

        self._write_store({"chat_action_cards": "before"})
        self.assertEqual("before", settings_store.load()["action_cards"])
        self._write_store({"llm_provider": "openai"})
        self.assertEqual("after", settings_store.load()["action_cards"])


class TestTheStationPlayerShipsOff(_TempStores):
    """A gesture surface appearing on every deployed card unasked is the
    0.9.61 shape again — and without a public https tune_in_url the player
    would open onto silence behind TLS. New card behaviour arrives switched
    off and is opened as a decision."""

    def _write_store(self, data: dict) -> None:
        import json

        import settings as settings_store

        settings_store.SETTINGS_PATH.write_text(
            json.dumps(data), encoding="utf-8")

    def test_off_by_default_and_off_for_existing_stores(self):
        import settings as settings_store

        self.assertIs(settings_store.FIELDS["swipe_player"][1], False)
        # A store from before the setting existed keeps the closed door.
        self._write_store({"llm_provider": "openai"})
        self.assertFalse(settings_store.load().get("swipe_player"))

    def test_the_operators_yes_is_honoured(self):
        import settings as settings_store

        self._write_store({"swipe_player": True})
        self.assertTrue(settings_store.load()["swipe_player"])

    def test_the_front_page_stays_the_phone_until_chosen(self):
        import settings as settings_store

        # A dropdown since 0.98.30 (operator's ask — two faces is a choice
        # between two things, not a tick naming one of them). This asserted
        # `is False`, which was the REPRESENTATION; what matters is that an
        # untouched deployment still opens on the phone.
        self.assertEqual(settings_store.FIELDS["start_on_player"][1], "call")
        self.assertEqual(settings_store.SCHEMA["start_on_player"]["kind"],
                         "select")

    def test_a_tick_from_before_the_dropdown_still_means_the_player(self):
        # The stored key kept its name AND its truthiness so nothing needed
        # migrating: `true` is not the string "call", so it still resolves to
        # the player. The card reads one boolean either way — see
        # api/live.py's playerStart.
        resolve = lambda v: bool(v) and v != "call"
        self.assertTrue(resolve(True))          # the old ticked box
        self.assertTrue(resolve("player"))      # the new choice
        self.assertFalse(resolve(False))        # the old unticked box
        self.assertFalse(resolve("call"))       # the new default
        self.assertFalse(resolve(None))         # never set

    def test_the_bed_under_the_machine_defaults_like_tune_in(self):
        # The same percentage grammar as tune_in_volume, deliberately — one
        # mental model for "music under the conversation".
        import settings as settings_store

        self.assertEqual(settings_store.FIELDS["vm_player_duck"][1], 10)
        self.assertEqual(settings_store.FIELDS["tune_in_volume"][1], 10)


class TestTheOnAirLetterRidesTheRoomName(_TempStores):
    """The phone-in flag lives inside the signed room name, behind the tier
    letter — `callin-gl-…` — so a caller can no more put themselves on air
    than raise their own tier. Parsing must fail CLOSED both ways: an
    unrecognised name is an open-tier private call, never an on-air one."""

    def test_the_tier_still_reads_through_the_flag(self):
        self.assertEqual(
            settings_store.tier_from_room("callin-gl-0123456789ab"), "guest")
        self.assertEqual(
            settings_store.tier_from_room("callin-al-0123456789ab"), "admin")
        # And the plain rooms are untouched.
        self.assertEqual(
            settings_store.tier_from_room("callin-g-0123456789ab"), "guest")

    def test_on_air_reads_only_the_shape_the_mint_writes(self):
        self.assertTrue(
            settings_store.on_air_from_room("callin-ol-0123456789ab"))
        self.assertTrue(
            settings_store.on_air_from_room("callin-gl-0123456789ab"))
        for not_on_air in ("callin-g-0123456789ab",     # plain call
                           "vm-g-0123456789ab",         # the machine
                           "probe-0123456789ab",        # pipeline check
                           "callin-xl-0123456789ab",    # x is not a tier
                           "callin-lg-0123456789ab",    # flag before tier
                           "", "garbage"):
            self.assertFalse(settings_store.on_air_from_room(not_on_air),
                             not_on_air)
        # The x-tier room also fails the TIER parse closed, to open.
        self.assertEqual(
            settings_store.tier_from_room("callin-xl-0123456789ab"), "open")

    def test_the_door_ships_shut_and_the_window_has_a_number(self):
        # A stranger's voice on the broadcast is the operator's decision;
        # every deployment that never touches the row must stay private.
        self.assertEqual(settings_store.FIELDS["allow_on_air"][1], "off")
        self.assertIn("allow_on_air", settings_store.TIERED_PERMISSIONS)
        self.assertEqual(settings_store.FIELDS["on_air_max_seconds"][1], 240)
        cfg = settings_store.permissions_for(
            {"allow_on_air": "guest"}, "guest")
        self.assertTrue(cfg["allow_on_air"])
        self.assertFalse(settings_store.permissions_for(
            {"allow_on_air": "guest"}, "open")["allow_on_air"])


class TestTheOnAirDelayIsTheOperatorsDial(unittest.TestCase):
    """`on_air_delay_secs` is the take-back window the hold cap guarantees —
    the operator's editorial dial, not an engineering constant. It has to
    actually reach the relay, because a control that saves and drives nothing
    is worse than no control (the avoid_on_air_overlap lesson).
    """

    def _relay(self, cfg):
        from onair.relay import CallRelay

        return CallRelay(None, cfg, "callin-g-abcdef123456")

    def test_the_default_matches_what_shipped(self):
        # 0.97.78 shipped the cap as a constant 6; the setting must not move
        # any deployment that never touches the row.
        self.assertEqual(settings_store.FIELDS["on_air_delay_secs"][1], 6)
        self.assertEqual(self._relay({}).max_held_secs, 6.0)

    def test_the_dial_reaches_the_hold(self):
        self.assertEqual(
            self._relay({"on_air_delay_secs": 12}).max_held_secs, 12.0)

    def test_the_floor_holds_because_zero_must_not_mean_no_window(self):
        # A second off-switch for a safety control is the quiet_secs trap:
        # the pull must always have SOME window, so 0 clamps to the floor
        # the panel's help promises rather than meaning "push immediately".
        self.assertEqual(
            self._relay({"on_air_delay_secs": 0}).max_held_secs, 6.0,
            "0 is falsy and falls through to the default, like blank")
        self.assertEqual(
            self._relay({"on_air_delay_secs": 1}).max_held_secs, 2.0)
        self.assertEqual(
            self._relay({"on_air_delay_secs": 900}).max_held_secs, 30.0)
        self.assertEqual(
            self._relay({"on_air_delay_secs": "nonsense"}).max_held_secs, 6.0)


class TestTheOnAirQuickKillsShipOpen(_TempStores):
    """The dashboard's two Live-on-air kills default ON: opening the tier
    row lights both doors at once, and a store written before the kills
    existed behaves identically after the upgrade."""

    def test_both_doors_default_on(self):
        self.assertIs(settings_store.FIELDS["on_air_calls_enabled"][1], True)
        self.assertIs(
            settings_store.FIELDS["on_air_voicemail_enabled"][1], True)
        cfg = settings_store.load()
        self.assertTrue(cfg.get("on_air_calls_enabled"))
        self.assertTrue(cfg.get("on_air_voicemail_enabled"))


class TestTheCountAndHeartShipOn(unittest.TestCase):
    """The card's listener count and track heart are ON by default — the
    operator's explicit ask (2026-08-18): they are one line of text and one
    small button on furniture the card already has, not a new surface like
    the player, which ships off. Both must degrade to nothing on their own
    (no count when the station won't say, no heart without a track line)."""

    def test_both_ship_on(self):
        import settings as settings_store

        self.assertTrue(settings_store.FIELDS["show_listener_count"][1])
        self.assertTrue(settings_store.FIELDS["show_track_like"][1])

    def test_the_player_still_ships_off(self):
        # The heart's door reads EITHER switch, so this pair is what keeps a
        # fresh deployment's card working while the player stays a choice.
        import settings as settings_store

        self.assertFalse(settings_store.FIELDS["swipe_player"][1])


class TestTheOnAirCallerSoundSetting(_TempStores):
    """Clean replaced the phone costume as the default ON PURPOSE — the
    operator's verdict after hearing themselves aired (2026-08-18, "I've
    never heard my voice sound so bad on a phone call"). The costume stays a
    stored choice, and blank falls through to clean like every setting."""

    def test_clean_is_the_default_and_the_costume_is_a_choice(self):
        self.assertEqual(settings_store.load()["on_air_caller_sound"], "clean")
        settings_store.save({"on_air_caller_sound": "phone"})
        self.assertEqual(settings_store.load()["on_air_caller_sound"], "phone")


class TestTheQuietStationSetting(_TempStores):
    """Quieting the station's own DJ during calls WRITES a station setting —
    the first feature that does — so off must be the default for every
    deployment that never touches it (the invariant-1 exception is the
    operator's to grant, agreed 2026-08-19). onair/hush.py reads the stored
    choice through scope()."""

    def test_off_is_the_default_and_the_scopes_are_choices(self):
        from onair import hush

        self.assertEqual(settings_store.load()["quiet_station_on_calls"], "off")
        self.assertEqual(hush.scope(settings_store.load()), "off")
        settings_store.save({"quiet_station_on_calls": "all"})
        self.assertEqual(hush.scope(settings_store.load()), "all")
        settings_store.save({"quiet_station_on_calls": "on_air"})
        self.assertEqual(hush.scope(settings_store.load()), "on_air")

    def test_blank_falls_through_to_off_like_every_setting(self):
        from onair import hush

        settings_store.save({"quiet_station_on_calls": "all"})
        settings_store.save({"quiet_station_on_calls": ""})
        self.assertEqual(settings_store.load()["quiet_station_on_calls"], "off")
        self.assertEqual(hush.scope(settings_store.load()), "off")
        settings_store.save({"on_air_caller_sound": ""})
        self.assertEqual(settings_store.load()["on_air_caller_sound"], "clean")


class TestTheMapTheOperatorNavigatesBy(_TempStores):
    """0.98.22 rebuilt the panel's information architecture after a review
    measured it: 188 settings behind 34 folded sections across nine pages,
    with nothing on screen saying what existed. Three of the things it added
    are only correct if they stay in step with the schema, and each one fails
    SILENTLY when it does not — a picker band nobody named drops a page into
    an unlabelled row, and a cross-reference to a section id that no longer
    exists is a link that goes nowhere with no error anywhere."""

    def test_every_cross_reference_points_at_a_real_section(self):
        # `[Caller permissions](#perms)` in a help string is expanded into a
        # link by writeLinked() in panel.js. A typo in the id renders a link
        # that quietly does nothing: the click is swallowed only when the
        # section exists, so a bad one falls through to a hash the router
        # resolves to the dashboard.
        import re

        known = {g for g, *_ in settings_store.GROUPS}
        bad = []
        for name, meta in settings_store.SCHEMA.items():
            for target in re.findall(r"\]\(#([a-z0-9_]+)\)", meta.get("help", "")):
                if target not in known:
                    bad.append(f"{name} -> #{target}")
        for _gid, _sup, _title, blurb in settings_store.GROUPS:
            for target in re.findall(r"\]\(#([a-z0-9_]+)\)", blurb):
                if target not in known:
                    bad.append(f"blurb -> #{target}")
        self.assertEqual(bad, [], f"cross-references to sections that do not exist: {bad}")

    def test_the_picker_knows_about_every_page(self):
        # Three pages are built by panel.js rather than by the schema —
        # Dashboard and Diagnostics — and they are declared here so
        # the picker's whole order lives in one file. An id that collides with
        # a super-group would take that super-group's chip; an unknown `where`
        # silently drops the page off the only map of the panel there is.
        extras = settings_store.NAV_EXTRA_PAGES
        pages = {s for s, *_ in settings_store.SUPERGROUPS}
        clashes = sorted({i for i, _t, _w in extras} & pages)
        self.assertFalse(clashes,
                         f"ids used as both an extra page and a super-group: {clashes}")
        for ident, _title, where in extras:
            self.assertIn(where, ("lead", "tail"),
                          f"{ident} stands at neither end of the strip")
        # panel.js hard-codes these two as its fallback if the payload is old,
        # and pageOfSection/currentPage reserve them; a rename here without one
        # there would be a page that exists twice or not at all.
        self.assertLessEqual({"dash", "diag"}, {i for i, _t, _w in extras})

    def test_the_finder_is_given_the_words_the_labels_do_not_use(self):
        # `alias=` and GROUP_ALIASES are search-only synonyms, and they exist
        # because the review measured the misses: "color" found nothing while
        # "colour" found two, "avatar" found nothing though the field is
        # avatar_style, and "mute", "logo", "spam" and "language" all found
        # nothing at all. They are only useful if the payload carries them.
        payload = settings_store.schema_payload()
        self.assertEqual(payload["fields"]["avatar_style"]["alias"], "avatar")
        self.assertIn("color", payload["fields"]["widget_theme"]["alias"])
        self.assertIn("password", payload["fields"]["front_access"]["alias"])
        groups = {g["id"]: g["alias"] for g in payload["groups"]}
        self.assertIn("password", groups["security"])
        # Every alias belongs to a field that exists, and is lower case: the
        # finder lower-cases the needle, so an alias in caps can never match.
        for name, meta in settings_store.SCHEMA.items():
            alias = meta.get("alias", "")
            self.assertEqual(alias, alias.lower(), f"{name}'s alias is not lower case")
        for gid in settings_store.GROUP_ALIASES:
            self.assertIn(gid, {g for g, *_ in settings_store.GROUPS},
                          f"GROUP_ALIASES names a section that does not exist: {gid}")
