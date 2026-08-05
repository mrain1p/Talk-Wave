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

import brain
import secrets_store
import settings as settings_store
import speech_filter
from brain import briefing, conduct


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

    def test_strips_stage_directions_that_do_not_start_on_the_verb(self):
        # Went out on a real call: "(Phone rings) Yeah, Cliff here." The old
        # rule only matched a parenthetical whose FIRST word was a verb.
        out = speech_filter.strip_stage_directions(
            "(Phone rings) Yeah, Cliff here. We're letting the last track settle."
        )
        self.assertNotIn("Phone rings", out)
        self.assertTrue(out.startswith("Yeah, Cliff here."))
        for direction in ("(the receiver clicks)", "(static crackles)",
                          "(sound of vinyl scratches)"):
            self.assertNotIn(
                direction, speech_filter.strip_stage_directions(direction + " right then")
            )

    def test_keeps_ordinary_parenthetical_speech(self):
        text = "the set (which runs till two) is all vinyl"
        self.assertEqual(speech_filter.strip_stage_directions(text), text)

    def test_keeps_parentheticals_that_merely_end_in_s(self):
        # The permissive "any word ending in -s" version of the verb-last rule
        # ate ordinary speech like this.
        for text in ("back in (about three minutes)",
                     "that one's from (one of my favourite albums)"):
            self.assertEqual(speech_filter.strip_stage_directions(text), text)

    def test_strips_the_djs_own_name_used_as_a_script_label(self):
        # Went out on a real call: the model slipped into screenplay format and
        # the voice read the DJ's own name aloud at the top of every turn.
        speech_filter.set_speaker("Francesca Hale")
        try:
            self.assertEqual(
                speech_filter.strip_speaker_labels(
                    "Francesca: Hey there, thanks for holding on."),
                "Hey there, thanks for holding on.",
            )
            for variant in ("**Francesca:** right then", "Francesca Hale: right then",
                            "DJ: right then", "HOST: right then"):
                self.assertEqual(
                    speech_filter.strip_speaker_labels(variant), "right then", variant)
        finally:
            speech_filter.set_speaker("")

    def test_label_strip_leaves_a_following_stage_direction_intact(self):
        # A greedy bold matcher ate the opening asterisk of what came next,
        # so the direction no longer looked like one and went out on air.
        speech_filter.set_speaker("Francesca")
        try:
            self.assertEqual(
                speech_filter.clean_for_speech(
                    "Francesca: *adjusts headphones* Loud and clear now.",
                    profanity_mode="off"),
                "Loud and clear now.",
            )
            self.assertEqual(
                speech_filter.clean_for_speech(
                    "**Francesca:** (Phone rings) Yeah, Cliff here.",
                    profanity_mode="off"),
                "Yeah, Cliff here.",
            )
        finally:
            speech_filter.set_speaker("")

    def test_the_dj_can_still_say_its_own_name_out_loud(self):
        # Only the SCRIPT LABEL form is a problem. Introducing yourself is
        # what a DJ does — the fix must not cost that.
        speech_filter.set_speaker("Wade")
        try:
            for kept in ("This is Wade, you're through to the booth.",
                         "Wade here, what can I do for you?",
                         "You're on with Wade on the late shift.",
                         "Wade's the name, records are the game."):
                self.assertEqual(speech_filter.strip_speaker_labels(kept), kept)
            # …but the label form still goes.
            self.assertEqual(
                speech_filter.strip_speaker_labels("Wade: You're through to the booth."),
                "You're through to the booth.")
        finally:
            speech_filter.set_speaker("")

    def test_never_eats_ordinary_speech_that_contains_a_colon(self):
        speech_filter.set_speaker("Francesca")
        try:
            for text in ("Listen: this one's a classic.",
                         "Here's the deal: we're out of time.",
                         "One thing: it's not on the album."):
                self.assertEqual(speech_filter.strip_speaker_labels(text), text)
            # Another person's name is dialogue, not a label for OUR voice.
            self.assertEqual(
                speech_filter.strip_speaker_labels("Bowie: an underrated run"),
                "Bowie: an underrated run",
            )
        finally:
            speech_filter.set_speaker("")

    def test_label_stripping_is_inert_before_a_persona_is_known(self):
        speech_filter.set_speaker("")
        self.assertEqual(
            speech_filter.strip_speaker_labels("Francesca: hello"), "Francesca: hello")

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
        self.assertEqual(briefing.demojibake("night â€” slow"), "night — slow")

    def test_demojibake_leaves_clean_text_alone(self):
        self.assertEqual(briefing.demojibake("plain text — fine"), "plain text — fine")

    def test_clip_respects_budget_on_word_boundary(self):
        out = briefing.clip("one two three four five", 13)
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


class TestPanelMarkup(unittest.TestCase):
    """The panel builds itself from the schema, but it can only fill in a
    control the markup actually contains — `byKind` skips any field with no
    matching element id. So a setting declared in settings.py with no input in
    index.html is simply unreachable, with nothing to say so. That shipped
    twice (avoid_on_air_overlap, on_air_quiet_secs)."""

    def setUp(self):
        import re

        html = (Path(__file__).parent.parent / "web-widget" / "index.html").read_text(
            encoding="utf-8"
        )
        self.ids = set(re.findall(r'id="([^"]+)"', html))
        self.groups = set(re.findall(r'data-group="([^"]+)"', html))

    def test_every_schema_field_has_a_control(self):
        missing = sorted(f for f in settings_store.SCHEMA if f not in self.ids)
        self.assertFalse(
            missing,
            "settings with no input in index.html — they cannot be changed from "
            f"the panel: {missing}",
        )

    def test_every_schema_group_has_a_section(self):
        missing = sorted(g for g, *_ in settings_store.GROUPS if g not in self.groups)
        self.assertFalse(missing, f"schema groups with no section: {missing}")

    def test_every_field_belongs_to_a_real_group(self):
        known = {g for g, *_ in settings_store.GROUPS}
        strays = sorted(
            f for f, meta in settings_store.SCHEMA.items() if meta["group"] not in known
        )
        self.assertFalse(strays, f"settings in an unknown group: {strays}")

    def test_every_group_belongs_to_a_real_supergroup(self):
        known = {s for s, *_ in settings_store.SUPERGROUPS}
        strays = sorted(
            g for g, sup, *_ in settings_store.GROUPS if sup not in known
        )
        self.assertFalse(strays, f"groups under an unknown supergroup: {strays}")

    def test_every_declared_field_is_storable(self):
        # A SCHEMA entry with no FIELDS entry renders a control that silently
        # discards whatever you type into it (save() drops unknown keys).
        strays = sorted(f for f in settings_store.SCHEMA if f not in settings_store.FIELDS)
        self.assertFalse(strays, f"settings that cannot be saved: {strays}")


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


class TestBrainSplit(_TempStores):
    """Phase 3's seam: what the DJ KNOWS and how it BEHAVES are separable.

    Each half has to be buildable and assertable without the other — that is
    the whole point of the split, and the thing a later edit is most likely to
    quietly undo by reaching for a station read from inside a rule.
    """

    FACT_MARKERS = ("Now playing", "Just played", "Coming up",
                    "Other shows on this station", "Segments you can run")
    RULE_MARKERS = ("# Running the call", "# Closing a call",
                    "Keep the call moving", "# What you can do")

    class _FakeStation:
        """The only station call the briefing makes on its own."""

        async def schedule(self):
            return {"shows": [{"id": "s_other", "name": "Morning Drive"}]}

    def _facts(self, cfg: dict, snap: dict | None = None) -> str:
        import asyncio

        snap = snap or {
            "now_playing": {"nowPlaying": {"title": "Dreams", "artist": "Fleetwood Mac"}},
            "state": {"history": [{"title": "Tusk", "artist": "Fleetwood Mac"}],
                      "upcoming": [{"title": "Sara", "artist": "Fleetwood Mac"}]},
            "session": {},
            "skills": [{"kind": "weather", "label": "Weather"}],
        }
        return asyncio.run(
            briefing.station_context(self._FakeStation(), cfg, snap, {"id": "s_now"})
        )

    def test_conduct_is_a_pure_function_of_settings(self):
        # No station, no network, no settings file — if a rule ever needs a
        # station read, the split has leaked and this stops compiling.
        text = conduct.rules({})
        for marker in self.RULE_MARKERS:
            self.assertIn(marker, text)

    def test_conduct_carries_no_station_facts(self):
        text = conduct.rules({"allow_skills": True, "context_schedule": True})
        for marker in self.FACT_MARKERS:
            self.assertNotIn(marker, text)

    def test_briefing_carries_no_rules(self):
        text = self._facts({"allow_skills": True, "context_schedule": True})
        for marker in self.RULE_MARKERS:
            self.assertNotIn(marker, text)

    def test_briefing_reports_what_the_station_is_doing(self):
        text = self._facts({})
        self.assertIn("Now playing: \"Dreams\" by Fleetwood Mac", text)
        self.assertIn("Just played", text)
        self.assertIn("Coming up", text)

    def test_briefing_reads_the_schedule_only_when_asked(self):
        self.assertNotIn("Morning Drive", self._facts({}))
        self.assertIn("Morning Drive", self._facts({"context_schedule": True}))

    def test_briefing_lists_segments_only_when_they_can_be_run(self):
        self.assertNotIn("weather", self._facts({}))
        self.assertIn("weather", self._facts({"allow_skills": True}))

    def test_each_toggle_picks_exactly_one_fragment(self):
        # The pairs contradict each other by design, so shipping both is the
        # failure mode — that is how a caller gets asked what kind of fun they
        # meant AND has something submitted anyway.
        pairs = [
            ({"confirm_requests": True},
             "say it back and get a quick yes", "No need to confirm"),
            ({"confirm_requests": False},
             "No need to confirm", "say it back and get a quick yes"),
            ({"shape_vague_requests": True},
             "two or three real directions", "don't interrogate them"),
            ({"shape_vague_requests": False},
             "don't interrogate them", "two or three real directions"),
            ({"ask_caller_name": True},
             "ask once, briefly", "Don't ask the caller their name"),
            ({"ask_caller_name": False},
             "Don't ask the caller their name", "ask once, briefly"),
        ]
        for cfg, present, absent in pairs:
            with self.subTest(cfg=cfg):
                text = conduct.rules(cfg)
                self.assertIn(present, text)
                self.assertNotIn(absent, text)

    def test_offering_a_segment_needs_both_switches(self):
        self.assertNotIn("Offering a segment", conduct.rules({"offer_skills": True}))
        self.assertNotIn("Offering a segment", conduct.rules({"allow_skills": True}))
        self.assertIn(
            "Offering a segment",
            conduct.rules({"allow_skills": True, "offer_skills": True}),
        )

    def test_the_two_halves_do_not_import_each_other(self):
        # Independence is the property worth protecting: a station field
        # should never be an edit to conduct, and a bad call should never be
        # an edit to briefing. Imports, not prose — the docstrings are allowed
        # to point at each other.
        import ast
        import inspect

        def imported(module) -> set[str]:
            names: set[str] = set()
            for node in ast.walk(ast.parse(inspect.getsource(module))):
                if isinstance(node, ast.Import):
                    names.update(a.name for a in node.names)
                elif isinstance(node, ast.ImportFrom):
                    names.add(node.module or "")
                    names.update(f"{node.module}.{a.name}" for a in node.names)
            return names

        self.assertFalse([n for n in imported(briefing) if "conduct" in n])
        self.assertFalse([n for n in imported(conduct) if "briefing" in n])
        # Conduct imports nothing from the station at all — it is settings in,
        # text out.
        self.assertFalse([n for n in imported(conduct) if "station" in n])

    def test_the_assembled_prompt_is_briefing_then_conduct(self):
        import asyncio

        from station import StationClient

        snapshot = {"dj": {"station": "Yosemite FM"}, "personas": [],
                    "now_playing": {"nowPlaying": {"title": "Dreams"}},
                    "state": {}, "session": {}, "schedule": {}}

        async def build() -> str:
            station = StationClient()
            try:
                return await brain.build_system_prompt(
                    station, {"id": "p", "name": "Dalia", "soul": "x"},
                    snapshot=snapshot)
            finally:
                await station.aclose()

        text = asyncio.run(build())
        facts_at = text.index("Now playing")
        rules_at = text.index("# Running the call")
        self.assertLess(facts_at, rules_at)
        # And the identity header still comes before both.
        self.assertLess(text.index("a DJ on Yosemite FM"), facts_at)


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
                return await brain.build_system_prompt(
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
        # Offering segments is opt-in and needs skills enabled too.
        self.assertNotIn("Offering a segment", text)
        settings_store.save({"allow_skills": True, "offer_skills": True})
        self.assertIn("Offering a segment", asyncio.run(build()))
        settings_store.save({"offer_skills": ""})
        self.assertNotIn("Offering a segment", asyncio.run(build()))


class TestCallPrivacy(_TempStores):
    """Every call is a first call. The back-to-air line from the LAST caller
    goes into the station's own chatter feed, and it was being fed straight
    back into the next caller's prompt — so the DJ carried on where the
    previous conversation left off, in front of a stranger."""

    def _prompt(self, session: dict) -> str:
        import asyncio

        from station import StationClient

        snapshot = {"dj": {}, "personas": [], "now_playing": {},
                    "state": {}, "session": session, "schedule": {}}
        persona = {"id": "p_test", "name": "Test DJ", "soul": "A test soul."}

        async def build() -> str:
            station = StationClient()
            try:
                return await brain.build_system_prompt(
                    station, persona, snapshot=snapshot
                )
            finally:
                await station.aclose()

        return asyncio.run(build())

    def test_previous_call_never_reaches_the_next_caller(self):
        session = {"messages": [
            {"kind": "link", "text": "Back with you after that one."},
            {"kind": "callin", "text": "Just had Sarah on the line about her divorce."},
        ]}
        text = self._prompt(session)
        self.assertIn("Back with you after that one.", text)   # ordinary chatter stays
        self.assertNotIn("Sarah", text)
        self.assertNotIn("divorce", text)

    def test_the_dj_is_told_which_segments_this_station_actually_has(self):
        # Without the catalogue the agent either guessed at segment names or
        # spent a turn asking the station mid-call, and the caller heard the
        # pause. "What can you do?" was answered vaguely for the same reason.
        import asyncio

        from station import StationClient

        snapshot = {"dj": {}, "personas": [], "now_playing": {}, "state": {},
                    "session": {}, "schedule": {},
                    "skills": [{"kind": "weather", "label": "Weather", "cooldownMin": 60},
                               {"kind": "storytime", "label": "Story time"}]}
        persona = {"id": "p_test", "name": "Test DJ", "soul": "A test soul."}

        def build() -> str:
            async def go():
                station = StationClient()
                try:
                    return await brain.build_system_prompt(
                        station, persona, snapshot=snapshot)
                finally:
                    await station.aclose()
            return asyncio.run(go())

        # Off by default: no segment list, because it can't run any.
        self.assertNotIn("weather", build().lower())

        settings_store.save({"allow_skills": True})
        text = build()
        self.assertIn("weather", text)
        self.assertIn("storytime", text)
        self.assertIn("these and no others", text)

    def test_segment_list_names_only_no_cooldowns(self):
        # Telling the DJ the intervals made it ration segments itself and
        # explain timings to callers. The station decides if one is due.
        out = briefing._fmt_skills([
            {"kind": "weather", "label": "Weather", "cooldownMin": 60},
            {"kind": "storytime", "label": "Story time", "cooldownMin": 45},
        ])
        self.assertIn("weather", out)
        self.assertIn("storytime", out)
        self.assertNotIn("60", out)
        self.assertNotIn("min", out)
        self.assertIn("the station decides if it's due", out)

    def test_prompt_tells_the_dj_how_to_close_a_call(self):
        text = self._prompt({})
        self.assertIn("Closing a call", text)
        self.assertIn("anything else before i let you go?", text.lower())
        self.assertIn("end_call", text)
        # And the guard against closing early, which is the real risk.
        self.assertIn("is NOT a call to close", text)
        self.assertIn("nothing good about a short", text)
        self.assertIn("never end a call because it's gone quiet", text.lower())

    def test_the_closing_check_is_the_end_not_a_full_stop_on_every_action(self):
        # Measured against the live deployment: the closing question landed in
        # eight of twelve turns, attached to every completed action. The model
        # was reading "I did the thing" as "the call is over", and momentum
        # agreed with it — so both places had to stop saying so.
        text = self._prompt({})
        self.assertIn("Calls end when the CALLER is finished", text)
        self.assertIn("is the LAST thing you say in a call", text)
        self.assertIn("nothing to angle for", text)
        # Momentum must not undo it by asking for a wind-down after each action.
        self.assertNotIn("wind toward a close", text)
        self.assertIn("does NOT mean moving it", text)

    def test_a_refused_hangup_does_not_invite_a_new_subject(self):
        # A caller who says goodbye inside the first minute was getting the
        # sign-off AND then a fresh line of questioning, because the refusal
        # read as "go find something else to talk about".
        text = self._prompt({})
        self.assertIn("overruled on the timing, not on the goodbye", text)
        self.assertIn("Do NOT open a new subject", text)

    def test_a_mood_request_either_ships_or_offers_options_never_both(self):
        # The two rules contradict each other, so exactly one must be in the
        # prompt. Shipping both is how a caller gets asked what kind of fun
        # they meant AND has something submitted anyway.
        off = self._prompt({})
        self.assertIn("don't interrogate them", off)
        self.assertNotIn("two or three real directions", off)

        settings_store.save({"shape_vague_requests": True})
        on = self._prompt({})
        self.assertIn("two or three real directions", on)
        self.assertNotIn("don't interrogate them", on)
        # Concrete options, never an open question, and only one round.
        self.assertIn("never an open", on)
        self.assertIn("ONE round", on)
        self.assertIn("don't invent names", on)

    def test_prompt_carries_a_triage_guide(self):
        text = self._prompt({})
        self.assertIn("Running the call", text)
        self.assertIn("that IS a request", text)      # a vibe is not a search
        self.assertIn("never recite a menu", text)    # the "what can you do" answer
        self.assertIn("Never two questions in a row", text)

    def test_the_prompt_names_the_operators_station_not_ours(self):
        # A DJ on Yosemite FM told callers they were live on SUB/WAVE — the
        # software's name, which no listener has heard of. GET /dj has carried
        # the real one all along.
        import asyncio

        from station import StationClient

        def build(dj: dict) -> str:
            snapshot = {"dj": dj, "personas": [], "now_playing": {}, "state": {},
                        "session": {}, "schedule": {}, "skills": []}
            async def go():
                station = StationClient()
                try:
                    return await brain.build_system_prompt(
                        station, {"id": "p", "name": "Dalia", "soul": "x"},
                        snapshot=snapshot)
                finally:
                    await station.aclose()
            return asyncio.run(go())

        text = build({"station": "Yosemite FM"})
        self.assertIn("a DJ on Yosemite FM", text)
        self.assertNotIn("SUB/WAVE", text)
        # Falls back only when the station doesn't say.
        self.assertIn("SUB/WAVE", build({}))

    def test_prompt_states_the_caller_is_new(self):
        text = self._prompt({})
        self.assertIn("This caller is NEW", text)

    def test_programme_intro_is_background_not_a_topic(self):
        session = {"messages": [
            {"kind": "programme-intro", "text": "Welcome to the Midnight Hour."},
        ]}
        text = self._prompt(session)
        # Still pinned, so the fiction holds…
        self.assertIn("Midnight Hour", text)
        # …but explicitly fenced off as background.
        self.assertIn("Do NOT recap it", text)
        self.assertIn("taking over from another DJ", text)


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

    def test_guest_password_is_independent_of_admin(self):
        self.auth.set_password("admin password here")
        self.assertFalse(self.auth.guest_is_set())
        self.auth.set_guest_password("guestcode")
        self.assertTrue(self.auth.guest_is_set())
        # Guest opens the phone but NOT the panel — the whole point.
        self.assertTrue(self.auth.verify_guest("guestcode"))
        self.assertFalse(self.auth.verify("guestcode"))
        # Admin opens both, so an operator carries one password.
        self.assertTrue(self.auth.verify_guest("admin password here"))
        self.assertTrue(self.auth.verify("admin password here"))
        # Changing the admin password leaves the guest one alone.
        self.auth.set_password("a different admin one")
        self.assertTrue(self.auth.verify_guest("guestcode"))

    def test_the_two_passwords_cannot_be_made_identical(self):
        # Sharing a code with callers that also opens the settings panel is
        # the single most likely way to get this wrong.
        self.auth.set_password("shared secret pw")
        with self.assertRaises(ValueError):
            self.auth.set_guest_password("shared secret pw")
        self.assertFalse(self.auth.guest_is_set())
        # …and from the other direction too.
        self.auth.set_guest_password("guestcode")
        with self.assertRaises(ValueError):
            self.auth.set_password("guestcode")
        self.assertTrue(self.auth.verify("shared secret pw"))   # unchanged

    def test_clearing_the_guest_password_reopens_the_line(self):
        self.auth.set_guest_password("guestcode")
        self.auth.clear_guest_password()
        self.assertFalse(self.auth.guest_is_set())
        self.assertFalse(self.auth.verify_guest("guestcode"))

    def test_guest_gate_is_open_until_a_code_exists(self):
        import token_server as ts

        self.assertIsNone(ts._guest_check("", "ip-a"))
        self.auth.set_guest_password("guestcode")
        self.assertIsNotNone(ts._guest_check("", "ip-a"))
        self.assertIsNotNone(ts._guest_check("wrong", "ip-a"))
        self.assertIsNone(ts._guest_check("guestcode", "ip-a"))
        ts._auth_state.pop("guest:ip-a", None)

    def test_guest_failures_do_not_lock_the_operator_out(self):
        # A caller fumbling the door code must not ban the address from the
        # settings panel — the two live in separate buckets.
        import token_server as ts

        self.auth.set_guest_password("guestcode")
        ts._auth_state.pop("ip-b", None)
        ts._auth_state.pop("guest:ip-b", None)
        for _ in range(10):
            ts._guest_check("nope", "ip-b")
        self.assertIsNotNone(ts._auth_gate("guest:ip-b"))
        self.assertIsNone(ts._auth_gate("ip-b"))
        ts._auth_state.pop("guest:ip-b", None)

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


class _FakeRequest:
    """Just enough of an aiohttp request for _caller_key/_check_usage."""

    def __init__(self, ip="1.2.3.4", fwd=""):
        self.headers = {"X-Forwarded-For": fwd} if fwd else {}
        self.remote = ip


class TestUsageControls(unittest.TestCase):
    """The guard against runaway spend — every refusal must fire, phrased
    in-world, and 0 must mean unlimited."""

    def setUp(self):
        import token_server as ts
        self.ts = ts
        ts._recent_mints[:] = []
        ts._caller_last.clear()
        ts._live_calls.clear()

    tearDown = setUp  # leave module state clean either way

    def test_concurrent_limit(self):
        import time
        self.ts._live_calls.update({"room-a": time.time(), "room-b": time.time()})
        msg = self.ts._check_usage(_FakeRequest(), {"max_concurrent_calls": 2})
        self.assertIn("tied up", msg)
        self.assertIsNone(
            self.ts._check_usage(_FakeRequest(), {"max_concurrent_calls": 0}))

    def test_per_hour_limit(self):
        import time
        self.ts._recent_mints.extend([time.time()] * 3)
        msg = self.ts._check_usage(_FakeRequest(), {"calls_per_hour": 3})
        self.assertIn("this hour", msg)
        self.assertIsNone(self.ts._check_usage(_FakeRequest(), {"calls_per_hour": 0}))

    def test_per_day_limit_counts_beyond_the_hour(self):
        # The hourly cap alone still permits 24x that in a day, so the daily
        # ceiling must count calls that have already aged out of the hour.
        import time
        old = time.time() - 7200          # two hours ago: outside the hour
        self.ts._recent_mints.extend([old] * 5)
        cfg = {"calls_per_hour": 10, "calls_per_day": 5}
        msg = self.ts._check_usage(_FakeRequest(), cfg)
        self.assertIn("today", msg)
        # …and the hourly limit is unaffected by those same old calls.
        self.assertIsNone(
            self.ts._check_usage(_FakeRequest(), {"calls_per_hour": 10}))
        self.assertIsNone(self.ts._check_usage(_FakeRequest(), {"calls_per_day": 0}))

    def test_pause_refuses_everything(self):
        msg = self.ts._check_usage(_FakeRequest(), {"calls_paused": True})
        self.assertIsNotNone(msg)
        self.assertNotIn("error", msg.lower())   # in-world, never a code

    def test_refusals_never_mention_the_mechanism(self):
        # A caller pressing Call should hear a busy station, not a rate limit.
        import time
        self.ts._live_calls["room-a"] = time.time()
        self.ts._recent_mints.append(time.time())
        self.ts._caller_last["1.2.3.4"] = time.time()
        banned = ("rate", "limit", "quota", "429", "token", "api")
        for cfg in ({"calls_paused": True}, {"max_concurrent_calls": 1},
                    {"calls_per_hour": 1}, {"calls_per_day": 1},
                    {"caller_cooldown_secs": 45}):
            msg = self.ts._check_usage(_FakeRequest(), cfg) or ""
            self.assertTrue(msg, cfg)
            for word in banned:
                self.assertNotIn(word, msg.lower(), f"{cfg} -> {msg}")

    def test_redial_cooldown_is_per_caller(self):
        import time
        self.ts._caller_last["1.2.3.4"] = time.time()
        msg = self.ts._check_usage(
            _FakeRequest("1.2.3.4"), {"caller_cooldown_secs": 45})
        self.assertIn("only just hung up", msg)
        # A different caller is unaffected.
        self.assertIsNone(self.ts._check_usage(
            _FakeRequest("5.6.7.8"), {"caller_cooldown_secs": 45}))

    def test_secure_origin_derivation(self):
        old = self.ts.LIVEKIT_PUBLIC_URL
        try:
            self.ts.LIVEKIT_PUBLIC_URL = "wss://192.168.1.245:8443"
            self.assertEqual(self.ts._secure_origin(), "https://192.168.1.245:8443")
            self.ts.LIVEKIT_PUBLIC_URL = "ws://localhost:7880"
            self.assertEqual(self.ts._secure_origin(), "")
        finally:
            self.ts.LIVEKIT_PUBLIC_URL = old


class TestCallStructure(unittest.TestCase):
    """The call is an object with phases, not a 334-line function. These pin
    the seams so a future edit can't quietly put the call back in one place."""

    @classmethod
    def setUpClass(cls):
        from call import lifecycle
        from call.session import CallSession

        cls.CallSession = CallSession
        cls.lifecycle = lifecycle

    def test_entrypoint_only_decides_whether_to_answer(self):
        # main.py's job is wiring. If this grows again, the call has started
        # leaking back out of CallSession.
        import inspect

        import main

        body = inspect.getsource(main.entrypoint)
        self.assertLess(len(body.splitlines()), 30)
        self.assertIn("probe-", body)          # still refuses probe rooms
        for phase in ("prepare()", "start()", "greet()"):
            self.assertIn(phase, body)

    def test_every_lifecycle_hook_is_registered(self):
        # Each of these was a closure in the old entrypoint. Losing one in the
        # move would be silent: the call still connects, it just stops doing
        # something (checking in on a quiet caller, enforcing the time limit).
        import inspect

        src = inspect.getsource(self.CallSession)
        for hook in ("station.aclose", "station_cfg.aclose", "air.watch",
                     "attach_error_recovery", "attach_heard_logging",
                     "attach_idle_watch", "attach_time_limit", "_on_shutdown"):
            self.assertIn(hook, src, hook)

    def test_the_hangup_tool_reads_the_session_late(self):
        # Tools are built before the AgentSession exists. Handing the tool a
        # callable rather than the session is what makes that safe; passing
        # the value directly would capture None for the life of the call.
        import inspect

        from call.tools.control import build_call_control_tools

        params = inspect.signature(build_call_control_tools).parameters
        self.assertIn("get_session", params)

    def test_the_greeting_opens_with_a_user_turn(self):
        """Reproduced against the Gemini API: a function call as the FIRST
        turn is rejected outright — "function call turn comes immediately
        after a user turn or after a function response turn", 400, fatal. The
        DJ routinely calls a tool while writing its greeting (checking what's
        playing), so the conversation has to start with a user turn."""
        import asyncio, inspect

        src = inspect.getsource(self.lifecycle.greet)
        self.assertIn("user_input=", src)

        seen = {}

        class FakeSession:
            async def generate_reply(self, **kw):
                seen.update(kw)
            async def say(self, *a, **kw):
                pass

        asyncio.run(self.lifecycle.greet(FakeSession(), {}))
        self.assertTrue(seen.get("user_input"), "no user turn seeded")
        self.assertTrue(seen.get("instructions"), "the greeting itself was lost")

        # It is a cue, not words put in the caller's mouth — and bracketed
        # text can never reach the voice.
        import speech_filter
        self.assertEqual(
            speech_filter.strip_stage_directions(seen["user_input"]), "")

    def test_idle_watch_and_time_limit_are_opt_out_by_setting(self):
        # Both used to be `if` blocks inside entrypoint; as functions they must
        # still no-op on 0 rather than starting a task that never fires.
        self.assertIsNone(self.lifecycle.attach_idle_watch(
            None, None, {"idle_prompt_secs": 0}))
        self.assertIsNone(self.lifecycle.attach_time_limit(
            None, None, {"max_call_seconds": 0}))


class TestCallRecord(unittest.TestCase):
    """Diagnosing a bad call meant reading the CALLER's half and inferring the
    rest from tracebacks. The record is both halves plus the tools, so a call
    can be reviewed as a call."""

    def setUp(self):
        from call import record

        self.record = record
        self._tmp = tempfile.TemporaryDirectory()
        self._old = record.CALLS_DIR
        record.CALLS_DIR = Path(self._tmp.name)

    def tearDown(self):
        self.record.CALLS_DIR = self._old
        self._tmp.cleanup()

    def _a_call(self, room="callin-abc123456789"):
        r = self.record.CallRecord(
            room, {"id": "p1", "name": "Wade"},
            {"llm_provider": "google", "llm_model": "gemini-3.1-flash-lite",
             "stt_provider": "local", "stt_model": "base.en", "tts_mode": "local",
             "allow_requests": True, "allow_skills": False},
        )
        r.turn("dj", "You're through to the booth.")
        r.turn("caller", "Can you play something fun?")
        r.tool("subwave_request_song", "Added to the queue")
        r.turn("dj", "That's going in.")
        return r

    def test_a_call_records_both_sides_and_the_tools(self):
        self._a_call().write(reason="caller hung up")
        calls = self.record.recent()
        self.assertEqual(len(calls), 1)
        c = calls[0]
        self.assertEqual(c["persona"]["name"], "Wade")
        self.assertEqual(c["callerTurns"], 1)
        self.assertEqual(c["endedBecause"], "caller hung up")
        self.assertEqual([t["who"] for t in c["turns"]], ["dj", "caller", "dj"])
        self.assertEqual(c["tools"][0]["name"], "subwave_request_song")
        # The config it ran under, so a bad call can be tied to a setting.
        self.assertIn("gemini", c["config"]["llm"])
        self.assertIn("allow_requests", c["config"]["permissions"])
        self.assertNotIn("allow_skills", c["config"]["permissions"])

    def test_final_wording_replaces_the_clipped_live_capture(self):
        # conversation_item_added fires while the DJ is still speaking, so the
        # live text came out clipped ("Take a breath, I've"). Timing from the
        # events is right; the wording has to come from the session history.
        r = self.record.CallRecord("callin-x", {"name": "Dalia"}, {})
        r.turn("dj", "Take a breath, I've")
        r.turn("caller", "Play me Let It Go")
        r.turn("dj", "Still with")
        stamps = [t["t"] for t in r.data["turns"]]

        r.finalise([("dj", "Take a breath, I've got you."),
                    ("caller", "Play me Let It Go"),
                    ("dj", "Still with me?"),
                    ("dj", "A line the events never saw.")])

        turns = r.data["turns"]
        self.assertEqual(turns[0]["text"], "Take a breath, I've got you.")
        self.assertEqual(turns[2]["text"], "Still with me?")
        self.assertEqual([t["t"] for t in turns[:3]], stamps)   # timings kept
        self.assertEqual(turns[3]["text"], "A line the events never saw.")

    def test_finalise_on_an_empty_history_keeps_what_was_captured(self):
        # If the session cannot be flattened, a clipped record beats none.
        r = self.record.CallRecord("callin-x", {"name": "Dalia"}, {})
        r.turn("dj", "something")
        r.finalise([])
        self.assertEqual(r.data["turns"][0]["text"], "something")

    def test_problems_are_kept_with_the_call_that_had_them(self):
        r = self._a_call()
        r.problem("APIStatusError: gemini llm: client error 400")
        r.write()
        self.assertIn("400", self.record.recent()[0]["problems"][0]["what"])

    def test_old_calls_are_pruned(self):
        for i in range(self.record.KEEP + 8):
            self._a_call(room=f"callin-{i:012d}").write()
        self.assertLessEqual(
            len(list(self.record.CALLS_DIR.glob("*.json"))), self.record.KEEP)

    def test_writing_never_raises_into_the_call(self):
        # This runs during shutdown, just before the on-air handoff. A crash
        # here would cost that handoff for the sake of a diagnostic file.
        self.record.CALLS_DIR = Path("/nonexistent\x00/bad")
        self._a_call().write()          # must not raise

    def test_a_runaway_call_cannot_write_an_unbounded_file(self):
        r = self._a_call()
        for i in range(self.record.MAX_TURNS + 200):
            r.turn("caller", f"line {i}")
        r.turn("caller", "x" * (self.record.MAX_TEXT + 500))
        r.write()
        c = self.record.recent()[0]
        self.assertLessEqual(len(c["turns"]), self.record.MAX_TURNS)
        self.assertTrue(all(len(t["text"]) <= self.record.MAX_TEXT for t in c["turns"]))


class TestStationActionResults(unittest.TestCase):
    """A station action that WORKED must never be reported to the caller as a
    failure. Both halves of that were live bugs: a segment takes longer than a
    read timeout, and some endpoints answer 200 with no JSON."""

    def setUp(self):
        import httpx
        import station
        self.httpx = httpx
        self.station = station

    def test_body_survives_empty_text_and_list_payloads(self):
        import httpx
        make = lambda **kw: httpx.Response(200, **kw)
        self.assertEqual(self.station._body(make(content=b"")), {})
        self.assertEqual(self.station._body(make(text="queued")), {})
        self.assertEqual(self.station._body(make(json={"ok": True})), {"ok": True})
        self.assertEqual(
            self.station._body(make(json=["a", "b"])), {"result": ["a", "b"]}
        )

    def test_read_timeout_is_unconfirmed_not_failed(self):
        # Reached the station, answer never came back — the action has run.
        self.assertTrue(self.station._sent_but_unconfirmed(self.httpx.ReadTimeout("x")))
        self.assertTrue(self.station._sent_but_unconfirmed(self.httpx.PoolTimeout("x")))
        # Never got there at all — that IS a failure.
        self.assertFalse(
            self.station._sent_but_unconfirmed(self.httpx.ConnectTimeout("x")))
        self.assertFalse(self.station._sent_but_unconfirmed(ValueError("x")))

    def test_a_5xx_on_a_request_is_retried_once(self):
        """Real call: the caller asked eight seconds after pickup, the station
        answered 503 because their tune-in hadn't reached its listener count
        yet, and the DJ told them the request failed. A 5xx is transient."""
        import asyncio

        import httpx

        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            # Fails once, then succeeds — the window the retry exists for.
            if len(calls) == 1:
                return httpx.Response(503, text="Service Unavailable")
            return httpx.Response(200, json={"requestId": "abc", "status": "pending"})

        async def run():
            client = self.station.StationClient(base_url="http://station")
            client._client = httpx.AsyncClient(
                base_url="http://station", transport=httpx.MockTransport(handler))
            try:
                return await client.submit_request("something fun")
            finally:
                await client.aclose()

        res = asyncio.run(run())
        self.assertEqual(len(calls), 2, "the 5xx was not retried")
        self.assertEqual(res.get("requestId"), "abc")
        self.assertNotIn("error", res)

    def test_a_4xx_refusal_is_not_retried(self):
        # A real refusal means retrying just repeats it and delays the answer.
        import asyncio

        import httpx

        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(429, text="Too Many Requests")

        async def run():
            client = self.station.StationClient(base_url="http://station")
            client._client = httpx.AsyncClient(
                base_url="http://station", transport=httpx.MockTransport(handler))
            try:
                return await client.submit_request("something fun")
            finally:
                await client.aclose()

        res = asyncio.run(run())
        self.assertEqual(len(calls), 1, "a 4xx should not be retried")
        self.assertIn("error", res)

    def test_action_timeout_is_well_clear_of_the_read_timeout(self):
        self.assertGreaterEqual(self.station.ACTION_TIMEOUT, 30.0)


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
            },
            # Factory defaults must NEVER shadow the operator's real config —
            # this exact shape once made Brock mirror bm_daniel.
            "defaults": {
                "personas": [
                    {"id": "p_abc123", "name": "A", "tts": {"voice": "bm_daniel"}},
                ]
            },
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
        from call import actions, air
        from call import providers
        from call.tools import control, music, registry

        cls.main = main
        cls.providers = providers
        cls.actions = actions
        cls.air = air
        cls.music = music
        cls.control = control
        cls.registry = registry

    def test_query_variants_strips_the_by_connector(self):
        v = self.music._query_variants("Let It Be by The Beatles")
        self.assertEqual(v[0], "Let It Be by The Beatles")
        self.assertIn("Let It Be The Beatles", v)
        self.assertIn("Let It Be", v)

    def test_query_variants_keeps_titles_containing_by(self):
        v = self.music._query_variants("Stand by Me by Ben E. King")
        self.assertIn("Stand by Me", v)  # rightmost split only

    def test_guarded_list_drops_on_air_tools_but_keeps_reads(self):
        cfg = {"allow_announcements": True, "allow_skills": True}
        allowed = self.registry.mcp_allowlist(cfg)
        self.assertNotIn("subwave_dj_announce", allowed)
        self.assertNotIn("subwave_run_skill", allowed)
        self.assertIn("subwave_list_skills", allowed)
        self.assertIn("subwave_now_playing", allowed)

    def test_wrapped_tools_never_served_raw(self):
        # Requests are always wrapped. Library search is wrapped only when the
        # wrapper can actually work — see the credential test below.
        import station_config

        cfg = {"allow_requests": True, "allow_library_search": True}
        original = station_config.admin_credentials
        try:
            station_config.admin_credentials = lambda: ("dj", "secret")
            allowed = self.registry.mcp_allowlist(cfg)
            self.assertNotIn("subwave_request_song", allowed)
            self.assertNotIn("subwave_search_library", allowed)
            et = self.registry.effective_tools({**cfg, "avoid_on_air_overlap": True})
            local_names = " ".join(et["local"])
            self.assertIn("subwave_request_song", local_names)
            self.assertIn("subwave_search_library", local_names)
        finally:
            station_config.admin_credentials = original

        # Requests stay wrapped with or without credentials — that wrapper
        # uses a public endpoint and works either way.
        self.assertNotIn("subwave_request_song", self.registry.mcp_allowlist(cfg))

    def test_action_ledger_caps_a_single_call(self):
        actions = self.actions.CallActions(2)
        self.assertFalse(actions.at_limit())
        actions.note("request", "a track")
        self.assertFalse(actions.at_limit())
        actions.note("skill", "weather")
        self.assertTrue(actions.at_limit())
        # The refusal is a steer for the DJ, not an error to read out.
        refusal = actions.refusal()
        self.assertNotIn("error", refusal.lower())
        self.assertIn("ring back", refusal)

    def test_action_ledger_zero_means_unlimited(self):
        actions = self.actions.CallActions(0)
        for _ in range(20):
            actions.note("request", "x")
        self.assertFalse(actions.at_limit())

    def test_every_action_kind_has_a_card_label(self):
        # A new action type shipping with no label would render as a blank
        # line in the caller's transcript.
        for kind in ("request", "announcement", "skill"):
            self.assertIn(kind, self.actions.CallActions.LABELS)

    def test_on_air_tools_are_never_served_raw_over_mcp(self):
        # MCP's session timeout is shorter than a segment takes to run, which
        # turned a segment that was audibly playing into "that didn't work".
        cfg = {"allow_announcements": True, "allow_skills": True}
        for guarded in (False, True):
            allowed = self.registry.mcp_allowlist(cfg)
            self.assertNotIn("subwave_dj_announce", allowed)
            self.assertNotIn("subwave_run_skill", allowed)
            self.assertIn("subwave_list_skills", allowed)
            local = " ".join(self.registry.effective_tools(
                {**cfg, "avoid_on_air_overlap": guarded})["local"])
            self.assertIn("subwave_dj_announce", local)
            self.assertIn("subwave_run_skill", local)

    def test_on_air_guard_is_a_no_op_when_the_toggle_is_off(self):
        import asyncio

        guard = self.air.OnAirGuard(None, {"avoid_on_air_overlap": False,
                                            "on_air_quiet_secs": 30})
        guard._clear.clear()          # even with the gate shut…
        self.assertEqual(asyncio.run(guard.wait_until_clear()), 0.0)
        # …and the watcher must not poll the station at all.
        asyncio.run(guard.watch(None))

    def test_on_air_guard_releases_rather_than_holding_forever(self):
        # A stale djLog entry must never strand a caller in silence: past the
        # cap the call carries on, overlap or not.
        import asyncio

        guard = self.air.OnAirGuard(None, {"avoid_on_air_overlap": True,
                                            "on_air_quiet_secs": 30})
        guard._clear.clear()
        waited = asyncio.run(guard.wait_until_clear(timeout=0.05))
        self.assertGreater(waited, 0)
        self.assertTrue(guard._clear.is_set())

    def test_firing_an_on_air_action_closes_the_gate_at_once(self):
        # Observed: the DJ said it was going off to air something, aired it,
        # then talked over its own delivery. The guard only closed when the
        # station's log showed speech, and it polls every 4s — so there was a
        # window where the DJ believed the air was clear. We know it isn't,
        # because we are the ones who just made it busy.
        import asyncio

        guard = self.air.OnAirGuard(None, {"avoid_on_air_overlap": True,
                                           "on_air_quiet_secs": 30})
        self.assertTrue(guard._clear.is_set())
        guard.mark_on_air(seconds=30)
        self.assertFalse(guard._clear.is_set())
        self.assertTrue(guard.on_air)
        # …and a reply now waits rather than going out over the announcement.
        waited = asyncio.run(guard.wait_until_clear(timeout=0.05))
        self.assertGreater(waited, 0)

    def test_marking_on_air_is_inert_when_the_guard_is_off(self):
        import asyncio

        guard = self.air.OnAirGuard(None, {"avoid_on_air_overlap": False,
                                           "on_air_quiet_secs": 30})
        guard.mark_on_air()
        self.assertEqual(asyncio.run(guard.wait_until_clear()), 0.0)

    def test_on_air_guard_clear_air_costs_nothing(self):
        import asyncio

        guard = self.air.OnAirGuard(None, {"avoid_on_air_overlap": True,
                                            "on_air_quiet_secs": 30})
        self.assertEqual(asyncio.run(guard.wait_until_clear()), 0.0)

    def test_random_persona_sentinel_is_shared(self):
        # The worker and the panel must agree on the spelling or the option
        # silently falls through to "whoever is live".
        self.assertEqual(settings_store.RANDOM_PERSONA, "__random__")
        self.assertNotIn(settings_store.RANDOM_PERSONA,
                         settings_store.load()["persona_override"])

    def test_blocked_tools_are_unreachable_at_every_setting(self):
        """The claim the panel makes to the operator: these are never exposed,
        whatever you switch on. Now enforceable directly, because one table
        describes both the block and the allowlist."""
        every_gate_on = {
            t.gate: True for t in self.registry.TOOLS
            if t.gate not in (self.registry.READ, self.registry.NEVER)
        }
        allowed = set(self.registry.mcp_allowlist(every_gate_on))
        local = set(self.registry.local_tool_names(every_gate_on))
        for name in self.registry.blocked_names():
            self.assertNotIn(name, allowed, f"{name} is claimed to be blocked")
            self.assertNotIn(name, local, f"{name} is claimed to be blocked")

    def test_the_catalogue_is_the_allowlist(self):
        """What the panel prints and what the worker serves come from the same
        table. This used to need a reconciliation test across two hand-kept
        lists; now it is a property of there being one list."""
        catalogue = {t["name"]: t["gate"] for t in self.registry.catalogue()}
        self.assertEqual(len(catalogue), len(self.registry.TOOLS))
        for tool in self.registry.TOOLS:
            self.assertEqual(catalogue[tool.name], tool.gate)
            # Every tool says something useful about itself, or the panel
            # renders a blank row.
            self.assertTrue(tool.what.strip(), tool.name)
            # A tool is either served or blocked — never gated but unservable.
            if tool.gate == self.registry.NEVER:
                self.assertEqual(tool.served, self.registry.NONE, tool.name)
            else:
                self.assertIn(tool.served,
                              (self.registry.MCP, self.registry.LOCAL), tool.name)

    def test_the_panel_never_claims_a_tool_that_will_not_be_built(self):
        # Caught by the registry refactor: without station credentials the
        # exact-queue wrapper is never built (it needs the ids only the
        # credentialed search returns), but the panel still listed it as
        # available. What the panel reports and what the worker builds have to
        # be the same set.
        import station_config

        cfg = {"allow_library_search": True, "allow_exact_queue": True}
        original = station_config.admin_credentials
        try:
            for creds, expected in ((("dj", "s"), True), (("", ""), False)):
                station_config.admin_credentials = lambda c=creds: c
                reported = "subwave_queue_track" in " ".join(
                    self.registry.effective_tools(cfg)["local"])
                built = "subwave_queue_track" in [
                    t.info.name for t in self.music.build_library_tools(
                        cfg, None, self.actions.CallActions(0))]
                self.assertEqual(reported, expected)
                self.assertEqual(built, expected)
                self.assertEqual(reported, built)
        finally:
            station_config.admin_credentials = original

    def test_every_gate_is_a_real_setting(self):
        # A typo in a gate name would silently disable a tool forever: the
        # lookup just returns None and the tool never appears.
        for tool in self.registry.TOOLS:
            if tool.gate in (self.registry.READ, self.registry.NEVER):
                continue
            self.assertIn(tool.gate, settings_store.FIELDS, tool.name)

    def test_reads_need_no_permission_and_locals_are_never_raw(self):
        reads = [t.name for t in self.registry.TOOLS
                 if t.gate == self.registry.READ]
        allowed = self.registry.mcp_allowlist({})     # nothing switched on
        self.assertEqual(sorted(allowed), sorted(reads))

        # A locally-wrapped tool must not also be offered over MCP, or the
        # model could reach the version without our guards and retries.
        everything = {t.gate: True for t in self.registry.TOOLS
                      if t.gate not in (self.registry.READ, self.registry.NEVER)}
        served_local = set(self.registry.local_tool_names(everything))
        served_mcp = set(self.registry.mcp_allowlist(everything))
        self.assertFalse(served_local & served_mcp)

    def test_library_search_falls_back_to_mcp_without_credentials(self):
        # The local wrapper reads an admin-only endpoint, so with no station
        # credentials it can only ever return nothing — which reaches the
        # caller as "not in the racks" for a track the library holds. The MCP
        # tool needs no auth, so it must take over.
        import station_config

        cfg = {"allow_library_search": True}
        original = station_config.admin_credentials
        try:
            station_config.admin_credentials = lambda: ("", "")
            self.assertTrue(self.registry.library_search_needs_mcp())
            self.assertIn("subwave_search_library", self.registry.mcp_allowlist(cfg))
            self.assertEqual(self.music.build_library_tools(
                cfg, None, self.actions.CallActions(0)), [])

            station_config.admin_credentials = lambda: ("dj", "secret")
            self.assertFalse(self.registry.library_search_needs_mcp())
            self.assertNotIn("subwave_search_library", self.registry.mcp_allowlist(cfg))
            self.assertEqual(len(self.music.build_library_tools(
                cfg, None, self.actions.CallActions(0))), 1)
        finally:
            station_config.admin_credentials = original

    def test_a_described_vibe_is_not_sent_to_the_name_search(self):
        # The call that prompted this: "find me some fun songs" was searched
        # by name and came back with "Fun, Fun, Fun" by The Beach Boys.
        for q in ("fun", "something fun", "find me some fun songs", "upbeat",
                  "chilled", "something for a rainy night", "party music",
                  "anything happy"):
            self.assertTrue(self.music.looks_like_a_vibe(q), q)

    def test_the_stations_own_request_slip_phrases_route_to_the_picker(self):
        # The station's request drawer offers these as one-tap examples, so a
        # caller will say them out loud. They must not become name searches.
        for q in ("sustained energy vibes", "surprise me", "more like this",
                  "something calm for a rainy evening"):
            self.assertTrue(self.music.looks_like_a_vibe(q), q)

    def test_real_track_names_are_never_mistaken_for_a_vibe(self):
        # Conservative on purpose — a false positive here refuses a search the
        # caller actually wanted.
        for q in ("Fun House by The Stooges", "Mr. Blue Sky", "Bowie",
                  "Sunny Afternoon by The Kinks", "Nightcall", "Kavinsky",
                  "Slow Hands by Interpol", "Party Hard Andrew WK"):
            self.assertFalse(self.music.looks_like_a_vibe(q), q)

    def test_search_results_carry_the_stations_mood_data(self):
        # The station returns moods and energy on every hit; dropping them left
        # the DJ describing a record purely from its title.
        out = self.music._fmt_track({
            "title": "Open Eye Signal", "artist": "Jon Hopkins",
            "moods": ["hypnotic", "nocturnal"], "energy": 0.7,
        })
        self.assertIn("hypnotic", out)
        self.assertIn("nocturnal", out)
        self.assertIn("high energy", out)
        # And stays clean when the station sends nothing.
        self.assertNotIn("energy", self.music._fmt_track({"title": "T", "artist": "A"}))

    def test_the_dj_cannot_hang_up_in_the_first_minute(self):
        # A model that decides to end the call early is worse than one that
        # lingers, so this floor is enforced in code, not asked for in a prompt.
        import asyncio, time

        tools = self.control.build_call_control_tools(None, lambda: None, time.time())
        end_call = tools[0]
        self.assertEqual(end_call.info.name, "end_call")
        out = asyncio.run(end_call(reason="done"))
        self.assertIn("can't close for another", out)
        # It must refuse the TIMING without inviting a new conversation at
        # someone who has just said goodbye — that was the observed failure.
        self.assertIn("not a disagreement about the goodbye", out)
        self.assertIn("Do NOT open a new subject", out)

    def test_ending_a_settled_call_is_allowed_once(self):
        import asyncio, time

        # A call that has been running a while: the tool arms the close and
        # asks for a sign-off rather than cutting the audio dead.
        tools = self.control.build_call_control_tools(None, lambda: None, time.time() - 600)
        end_call = tools[0]
        first = asyncio.run(end_call(reason="caller said goodbye"))
        self.assertIn("the line is closing", first.lower())
        # The sign-off is spoken in the same turn as the tool call, so asking
        # for one here made the caller hear a second, different farewell every
        # time — observed on a scripted run against the live deployment.
        self.assertIn("Do not say it again", first)
        self.assertIn("two or three words", first)
        # A second call must not stack another close task.
        second = asyncio.run(end_call(reason="again"))
        self.assertIn("Already wrapping up", second)

    def test_the_wrap_up_actually_hangs_up(self):
        """Shipped broken: the tool is named end_call, which shadowed the
        imported end_call helper, so the close raised TypeError inside a
        background task. The DJ said goodbye and the line stayed open until the
        idle watcher gave up. Nothing surfaced it — the exception died in a
        task nobody awaited."""
        import asyncio, time

        deleted = {}

        class FakeRoomApi:
            async def delete_room(self, req):
                deleted["room"] = getattr(req, "room", "?")

        class FakeCtx:
            room = type("R", (), {"name": "callin-test"})()
            api = type("A", (), {"room": FakeRoomApi()})()
            def shutdown(self, reason=""):
                deleted["shutdown"] = reason

        async def run():
            tools = self.control.build_call_control_tools(
                FakeCtx(), lambda: None, time.time() - 600)
            said = await tools[0](reason="caller said goodbye")
            self.assertIn("the line is closing", said.lower())
            # The close runs in the background; give it room to finish.
            for _ in range(60):
                await asyncio.sleep(0.1)
                if "shutdown" in deleted:
                    break

        asyncio.run(run())
        self.assertEqual(deleted.get("room"), "callin-test", "the room was never deleted")
        self.assertIn("wrapped up", deleted.get("shutdown", ""))

    def test_queue_position_becomes_something_a_dj_can_say(self):
        # Without this the DJ could only say "soon", which is how a caller
        # gets told their song is on when it is four tracks away.
        self.assertIn("next up", self.music._when_it_plays(1))
        self.assertIn("next up", self.music._when_it_plays(0))
        third = self.music._when_it_plays(3)
        self.assertIn("number 3", third)
        self.assertIn("9-12 minutes", third)
        # No position from the station means no guessing.
        for missing in (None, "", "soon"):
            self.assertIn("don't guess", self.music._when_it_plays(missing))

    def test_exact_queue_needs_search_and_credentials(self):
        import station_config

        original = station_config.admin_credentials
        try:
            # No credentials: the wrapper can't read ids, so the tool is absent
            # rather than present and broken.
            station_config.admin_credentials = lambda: ("", "")
            cfg = {"allow_library_search": True, "allow_exact_queue": True}
            names = [t.info.name for t in self.music.build_library_tools(
                cfg, None, self.actions.CallActions(0))]
            self.assertNotIn("subwave_queue_track", names)

            station_config.admin_credentials = lambda: ("dj", "secret")
            names = [t.info.name for t in self.music.build_library_tools(
                cfg, None, self.actions.CallActions(0))]
            self.assertIn("subwave_queue_track", names)
            self.assertIn("subwave_search_library", names)

            # Off by default, and never served raw over MCP.
            off = {"allow_library_search": True}
            names = [t.info.name for t in self.music.build_library_tools(
                off, None, self.actions.CallActions(0))]
            self.assertNotIn("subwave_queue_track", names)
            self.assertNotIn("subwave_queue_track",
                             self.registry.mcp_allowlist({**cfg, "allow_requests": True}))
        finally:
            station_config.admin_credentials = original

    def test_search_results_carry_ids_only_when_they_can_be_used(self):
        # The id is noise in the transcript unless something can act on it.
        with_id = self.music._fmt_track({"title": "T", "artist": "A", "id": "x1"}, with_id=True)
        self.assertIn("x1", with_id)
        self.assertNotIn("x1", self.music._fmt_track({"title": "T", "artist": "A", "id": "x1"}))

    def test_a_relative_adapter_name_resolves_from_anywhere(self):
        """This one shipped broken and every call crashed before the DJ spoke.

        build_tts resolved the adapter against Path(__file__).parent, which
        worked only because it happened to live next to tts-adapters/. Moving
        it into call/ during the refactor silently stopped resolving, and the
        job died on FileNotFoundError. Nothing tested build_tts at all.
        """
        import tts_adapter

        names = [p.name for p in tts_adapter.ADAPTER_DIR.glob("*.json")]
        self.assertTrue(names, "no adapter configs shipped — cannot test resolution")

        for name in names:
            resolved = tts_adapter.ADAPTER_DIR / name
            self.assertTrue(resolved.exists(), name)

        # The real call path: a bare filename must become a readable file.
        built = self.providers.build_tts(
            {"tts_mode": "local", "tts_adapter": names[0], "tts_base_url": "http://x"},
            "some-voice",
        )
        self.assertIsNotNone(built)

    def test_every_shipped_adapter_config_is_loadable(self):
        # A malformed adapter would fail the same way — at call time, in front
        # of a caller, rather than here.
        import tts_adapter

        for path in tts_adapter.ADAPTER_DIR.glob("*.json"):
            cfg = tts_adapter.load_adapter(str(path))
            self.assertIn("response", cfg, path.name)
            self.assertIn("audio", cfg, path.name)

    def test_effective_stt_falls_back_without_keys(self):
        provider, model, note = self.providers.effective_stt({"stt_provider": "deepgram"})
        self.assertEqual(provider, "google")  # no deepgram or openai key set
        self.assertIn("falling back", note)
        os.environ["OPENAI_API_KEY"] = "sk-x"
        provider, model, note = self.providers.effective_stt({"stt_provider": "deepgram"})
        self.assertEqual(provider, "openai")
        self.assertEqual(model, "gpt-4o-mini-transcribe")

    def test_effective_stt_rejects_cross_provider_model(self):
        provider, model, _ = self.providers.effective_stt(
            {"stt_provider": "local", "stt_model": "nova-3"}
        )
        self.assertEqual(provider, "local")
        self.assertEqual(model, "base.en")  # nova-3 is not a local model


if __name__ == "__main__":
    unittest.main(verbosity=2)
