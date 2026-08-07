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
        self.assertEqual(settings_store.load()["allow_skills"], "off")

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

    def test_a_protocol_provider_dials_its_own_host(self):
        # The whole branch is one base_url away from posting a DeepSeek key
        # to api.openai.com.
        from call.providers import build_llm

        model = build_llm({"llm_provider": "deepseek", "llm_model": ""})
        self.assertIn("api.deepseek.com", str(model._client.base_url))


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
