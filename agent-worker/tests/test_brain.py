"""Prompt assembly: what the DJ is told before the caller says anything.

The speech filter moved to tests/test_speech_filter.py — it was a second,
unrelated subject living in this file.

Split out of test_sidecar.py; see tests/__init__.py.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path
import brain
import settings as settings_store
from brain import briefing, conduct
from tests.support import AGENT_WORKER, _TempStores


class TestPrompts(unittest.TestCase):
    def test_demojibake_repairs_double_encoding(self):
        self.assertEqual(briefing.demojibake("night â€” slow"), "night — slow")

    def test_demojibake_leaves_clean_text_alone(self):
        self.assertEqual(briefing.demojibake("plain text — fine"), "plain text — fine")

    def test_clip_respects_budget_on_word_boundary(self):
        out = briefing.clip("one two three four five", 13)
        self.assertLessEqual(len(out), 14)  # budget + ellipsis
        self.assertTrue(out.endswith("…"))


class TestBrainSplit(_TempStores):
    """Phase 3's seam: what the DJ KNOWS and how it BEHAVES are separable.

    Each half has to be buildable and assertable without the other — that is
    the whole point of the split, and the thing a later edit is most likely to
    quietly undo by reaching for a station read from inside a rule.
    """

    FACT_MARKERS = ("Playing when this call connected", "Just played", "Coming up",
                    "Other shows on this station", "Segments you can run")
    RULE_MARKERS = ("# Running the call", "# Closing a call",
                    "Keep the call moving", "# What you can do")

    class _FakeStation:
        """The briefing makes no station call of its own — every read, the
        schedule included, rides in the snapshot. A schedule() that raised
        would prove that; this one is here only so an accidental call is loud."""

        async def schedule(self):
            raise AssertionError("the briefing must reuse snap['schedule'], not re-read")

    def _facts(self, cfg: dict, snap: dict | None = None) -> str:
        import asyncio

        snap = snap or {
            "now_playing": {"nowPlaying": {"title": "Dreams", "artist": "Fleetwood Mac"}},
            "schedule": {"shows": [{"id": "s_other", "name": "Morning Drive"}]},
            "state": {"history": [{"title": "Tusk", "artist": "Fleetwood Mac"}],
                      "upcoming": [{"title": "Sara", "artist": "Fleetwood Mac"}]},
            "session": {},
            "skills": [{"kind": "weather", "label": "Weather"}],
        }
        return asyncio.run(
            briefing.station_context(self._FakeStation(), cfg, snap, {"id": "s_now"})
        )

    def test_the_goodbye_turn_is_named_as_a_turn(self):
        # A real caller said "Alright, thanks." after their request was in;
        # the DJ answered with programme information and no close, and the
        # caller sat twenty seconds and hung up (2026-08-11). The conduct
        # must name that acknowledgment as the goodbye turn itself.
        text = conduct.rules({})
        self.assertIn("IS the\n  goodbye turn", text)
        self.assertIn("end_call in that same turn", text)

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
        # Frozen at pickup, so the line says WHEN it was true — see
        # briefing._fmt_now_playing.
        self.assertIn(
            'Playing when this call connected: "Dreams" by Fleetwood Mac',
            text)
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
        # allow_requests rides along: the fragments live inside the Requests
        # bullet, which since the 2026-08-12 miming incident only exists when
        # the switch is on.
        pairs = [
            ({"allow_requests": True, "confirm_requests": True},
             "say it back and get a quick yes", "No need to confirm"),
            ({"allow_requests": True, "confirm_requests": False},
             "No need to confirm", "say it back and get a quick yes"),
            ({"allow_requests": True, "shape_vague_requests": True},
             "two or three real directions", "don't interrogate them"),
            ({"allow_requests": True, "shape_vague_requests": False},
             "don't interrogate them", "two or three real directions"),
            ({"allow_requests": True, "ask_caller_name": True},
             "ask once, briefly", "Don't ask the caller their name"),
            ({"allow_requests": True, "ask_caller_name": False},
             "Don't ask the caller their name", "ask once, briefly"),
        ]
        for cfg, present, absent in pairs:
            with self.subTest(cfg=cfg):
                text = conduct.rules(cfg)
                self.assertIn(present, text)
                self.assertNotIn(absent, text)

    def test_a_question_about_what_to_play_is_not_a_request(self):
        """Asking the DJ what it would recommend must not queue anything.

        From the operator's chat of 2026-08-15: they asked what it recommends,
        it named "Swinging on a Star" and queued it in the same turn (18:15:18
        in the web log, cancelled at 18:17:12). `shape_vague_requests` was ON
        the whole time — the shaped branch says to come back with options, and
        the model still acted, because "ONE round: whatever they say next"
        reads as satisfied by the DJ's own suggestion. The rule now separates a
        question from an instruction; without that line this section says
        nothing about the difference.
        """
        text = conduct.rules({"allow_requests": True,
                              "shape_vague_requests": True})
        self.assertIn("A QUESTION IS NOT A REQUEST", text)
        self.assertIn("What would you recommend?", text)
        # And it is part of the SHAPED branch, not a standing rule: with
        # shaping off the DJ is deliberately told to act on one vibe, and two
        # instructions pulling opposite ways is the failure this pairs against.
        self.assertNotIn(
            "A QUESTION IS NOT A REQUEST",
            conduct.rules({"allow_requests": True,
                           "shape_vague_requests": False}))

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
        facts_at = text.index("Playing when this call connected")
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

    def test_tonights_episode_is_its_own_block(self):
        # The Show Card is the standing format the show runs every week;
        # `episodeAngle` is what THIS episode is about. It gets its own block
        # rather than hanging off the card, so a station that can't resolve
        # the show still keeps the one piece of framing it did publish.
        import asyncio

        class _Station:
            async def active_show(self, now_playing=None, schedule=None):
                return {"id": "s_pub", "name": "Donovan's Pub",
                        "topic": "Irish folk and trad.",
                        "episodeAngle": "A relaxed morning session."}

            async def schedule(self):
                raise AssertionError("assembling a prompt read the schedule")

        snapshot = {"dj": {}, "personas": [], "now_playing": {},
                    "state": {}, "session": {}, "schedule": {}}
        text = asyncio.run(brain.build_system_prompt(
            _Station(), {"id": "p_danny", "name": "Danny", "soul": "A soul."},
            snapshot=snapshot))

        self.assertIn("Tonight's episode in particular", text)
        self.assertIn("A relaxed morning session.", text)
        self.assertIn("Irish folk and trad.", text)


class TestCallerContext(unittest.TestCase):
    """What we can say about a caller when a call goes wrong.

    The worker writes the call record and never sees the browser that rang, so
    the token server attaches what it knew at mint time. Kept in memory only —
    enough to answer "why did that call fail" while the process is up, without
    the call archive quietly becoming a log of who rang and from where.
    """

    def test_it_tells_the_browsers_apart(self):
        from api.tokens import _describe_client

        cases = {
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:153.0) Gecko/20100101 Firefox/153.0":
                "Firefox on macOS",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36":
                "Chrome on Windows",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1":
                "Safari on iPhone",
        }
        for ua, want in cases.items():
            with self.subTest(browser=want):
                self.assertEqual(_describe_client(ua), want)
        self.assertEqual(_describe_client(""), "unknown client")

    def test_it_says_whether_the_caller_was_on_this_network(self):
        # The point of the whole thing: a call that connects and then hears
        # nothing looks identical whether the caller was off-LAN with no media
        # path or simply silent. This separates them.
        from api.tokens import _network_of

        for ip in ("192.168.1.51", "10.0.0.8", "172.19.0.4", "127.0.0.1"):
            with self.subTest(ip=ip):
                self.assertEqual(_network_of(ip), "same network")
        for ip in ("100.33.134.4", "8.8.8.8", "172.32.0.1"):
            with self.subTest(ip=ip):
                self.assertEqual(_network_of(ip), "off-network")
        self.assertEqual(_network_of(""), "unknown")
        self.assertEqual(_network_of("nonsense"), "off-network")

    def test_caller_context_never_reaches_the_call_record_on_disk(self):
        # It is diagnostic, not archive. If this ever changes, every stored
        # call becomes a record of an address, which is a different promise
        # than "both sides of the conversation". Tested on what is actually
        # written, not on the source text — an earlier version grepped the
        # module and matched "ip" inside "description".
        import json

        from call import record

        tmp = Path(tempfile.mkdtemp())
        original = record.CALLS_DIR
        try:
            record.CALLS_DIR = tmp
            r = record.CallRecord("callin-abc", {"id": "p1", "name": "Cliff"}, {})
            r.turn("caller", "hello")
            r.write(reason="caller hung up")
            written = json.loads(next(tmp.glob("*.json")).read_text(encoding="utf-8"))
        finally:
            record.CALLS_DIR = original
            shutil.rmtree(tmp, ignore_errors=True)

        for key in ("caller", "ip", "client", "network", "userAgent"):
            self.assertNotIn(key, written, f"the record on disk now carries {key}")


class TestOneBadTrackCannotSwallowThePrompt(unittest.TestCase):
    """Search results are capped at 8, but nothing capped the size of one.
    Every field goes into the prompt, where length is latency on every
    remaining turn and is paid for per token."""

    def test_a_giant_field_is_trimmed(self):
        from call.tools.music import _fmt_track

        out = _fmt_track({"title": "x" * 5000, "artist": "y" * 5000,
                          "album": "z" * 5000, "moods": ["m" * 900] * 9,
                          "id": "i" * 900}, with_id=True)
        self.assertLess(len(out), 700, f"one track rendered {len(out)} chars")

    def test_an_ordinary_track_is_unchanged(self):
        from call.tools.music import _fmt_track

        self.assertEqual(
            _fmt_track({"title": "Roads", "artist": "Portishead",
                        "album": "Dummy", "year": 1994}),
            '"Roads" by Portishead (Dummy, 1994)')

    def test_the_briefing_path_caps_its_fields_too(self):
        # The search path always capped its fields; the now-playing / recent
        # briefing path was missed until the 0.10.58 review — a huge or
        # prompt-like title lands in the SYSTEM PROMPT and re-costs every turn.
        from brain.briefing import _fmt_now_playing, _tracks

        # 0.10.61 raised the per-field caps for obvious headroom (title/album
        # 200), so a maxed line is longer — but still BOUNDED. The point is
        # that a multi-KB junk field can't reach the prompt, not the exact
        # ceiling: five 5000-char fields collapse from 25KB to ~1KB.
        # The `context` block (clock, time.vibe, weather, dominantMood) was
        # the SAME missed-cap gap one level down — this test set no context,
        # so it passed green while those fields rode uncapped (security
        # sitting, 2026-08-28). Now every context field is _fld'd too.
        np = {"nowPlaying": {"title": "x" * 5000, "artist": "y" * 5000,
                             "album": "z" * 5000, "genre": "g" * 5000,
                             "moods": ["m" * 900] * 5},
              "context": {"clock": {"display": "c" * 5000},
                          "time": {"vibe": "v" * 5000},
                          "weather": {"condition": "w" * 5000,
                                      "temp": "9" * 5000, "tempUnit": "F" * 500},
                          "dominantMood": "d" * 5000}}
        line = _fmt_now_playing(np)
        self.assertLess(len(line), 1600, f"now-playing rendered {len(line)}")
        rows = _tracks([{"title": "t" * 5000, "artist": "a" * 5000}], 4)
        self.assertTrue(all(len(r) < 500 for r in rows), rows)

    def test_an_ordinary_now_playing_still_reads_naturally(self):
        from brain.briefing import _fmt_now_playing

        line = _fmt_now_playing({"nowPlaying": {
            "title": "Roads", "artist": "Portishead", "album": "Dummy"}})
        self.assertIn('"Roads" by Portishead', line)
        self.assertIn("Dummy", line)

    def test_a_clockless_station_keeps_its_call_dj_clockless_too(self):
        # The djSpeakClock mirror (SUB/WAVE 1.8): with the station's clock
        # off air, the wall time stays out of the briefing — otherwise the
        # call-in DJ is the one voice still announcing the hour. The daypart
        # vibe survives either way, the station's own carve-out.
        from brain.briefing import _fmt_now_playing

        np = {"nowPlaying": {"title": "Roads"},
              "context": {"clock": {"display": "9:41 pm"},
                          "time": {"vibe": "late night"}}}
        spoken = _fmt_now_playing(np, speak_clock=True)
        self.assertIn("9:41 pm", spoken)
        silent = _fmt_now_playing(np, speak_clock=False)
        self.assertNotIn("9:41", silent)
        self.assertIn("late night", silent)

    def test_the_clock_mirror_defaults_on_and_coerces_like_the_station(self):
        # Absent, non-boolean, unreadable or unauthed all mean the switch
        # effectively doesn't exist — the station coerces the same way, so
        # an upgrade is byte-identical for a station that never set it.
        import asyncio

        from station_config import StationConfig

        sc = StationConfig.__new__(StationConfig)
        for payload, want in (({}, True),
                              ({"settings": {"djSpeakClock": False}}, False),
                              ({"djSpeakClock": "nope"}, True),
                              ({"nested": [{"djSpeakClock": True}]}, True)):
            async def fake_settings(p=payload):
                return p
            sc.settings = fake_settings
            self.assertIs(asyncio.run(StationConfig.speak_clock(sc)), want,
                          payload)


class TestACallerCanBeToldNothingIsKept(_TempStores):
    """A transcript is both sides of a stranger's conversation, kept on the
    operator's disk. It is how a bad call gets diagnosed and the README says
    so — but until now there was no way to say no, and no way to say for how
    long. An operator who does not want that has to be able to have it."""

    def setUp(self):
        super().setUp()
        from call import record

        self.record = record
        self._old = record.CALLS_DIR
        record.CALLS_DIR = Path(self._tmp.name) / "calls"

    def tearDown(self):
        self.record.CALLS_DIR = self._old
        super().tearDown()

    def _a_call(self, room="callin-abcdefghijkl"):
        r = self.record.CallRecord(room, {"id": "p1", "name": "Wade"}, {})
        r.turn("caller", "hello")
        return r

    def test_retention_is_the_setting_not_the_constant(self):
        for i in range(8):
            self._a_call(f"callin-{i:012d}").write(keep=3)
        self.assertEqual(len(list(self.record.CALLS_DIR.glob("*.json"))), 3)

    def test_zero_does_not_mean_delete_everything(self):
        # Turning recording OFF is how you keep nothing; a 0 here would be a
        # misreading, not an instruction.
        self._a_call().write(keep=0)
        self.assertEqual(len(list(self.record.CALLS_DIR.glob("*.json"))), 1)

    def test_the_setting_exists_and_defaults_to_keeping_them(self):
        cfg = settings_store.load()
        self.assertIs(cfg["record_calls"], True)
        # 1000 since 0.10.106 (operator's ask). A record is a few KB, so a
        # thousand is single-digit MB — and the old 100 aged out a busy
        # evening's calls before anyone had read them back.
        self.assertEqual(cfg["record_keep"], 1000)


class TestEveryMouthSpeaksAsTheSameDJ(unittest.TestCase):
    """There are four mouths, not two, and two of them had no character.

    The phone and the text line share the brain by construction. The other two
    write their own prompts, and until 0.10.146 neither carried the DJ card:

      * the back-to-air mention — the ONE line the station's whole audience
        hears about a call — was written from the persona's NAME alone. No
        card, no conduct, no house style, while the call it was summarising had
        been run by 28,000 characters of brain.
      * the voicemail greeting clipped the card to 900 characters, a number
        from nowhere and less than half what a call carries, so a caller who
        got the machine met a shorter version of the DJ.

    Neither is a place to assemble the whole brain — the handoff runs during
    shutdown and a station read there delays the worker letting go — but the
    card is already in hand in both, and it is what makes the voice the same.
    """

    def test_the_back_to_air_line_is_written_by_the_persona(self):
        import inspect

        from call import handoff

        src = inspect.getsource(handoff.send_on_air_callback)
        self.assertIn("CARD_BUDGET", src)
        self.assertIn('persona.get("soul"', src)

    def test_the_voicemail_greeting_uses_the_same_card_budget(self):
        import inspect

        from voicemail import capture

        src = inspect.getsource(capture._fresh_greeting)
        self.assertIn("CARD_BUDGET", src)
        # Comments stripped: the one above the fix QUOTES the old `soul[:900]`
        # to say what it replaced, and a check that cannot tell code from the
        # note explaining it would forbid describing the bug at all.
        code = "\n".join(line for line in src.splitlines()
                         if not line.lstrip().startswith("#"))
        self.assertNotIn("[:900]", code,
                         "the machine is back on its own private card length")

    def test_the_card_budget_is_one_number(self):
        # Both read it from the briefing rather than each keeping a copy, which
        # is how the 900 happened in the first place.
        from brain.briefing import CARD_BUDGET

        self.assertEqual(CARD_BUDGET, 2000)

    def test_the_short_identity_fields_are_capped_too(self):
        # Security sitting, 2026-08-28: soul and topic were clipped to
        # CARD_BUDGET, but their short siblings — the DJ name, station name
        # and show name — rode the opening line of the prompt raw. A corrupt
        # or hostile /dj or /schedule value re-costs every turn of a voice
        # call. NAME_BUDGET now caps all three.
        import inspect

        from brain import assemble

        src = inspect.getsource(assemble.build_system_prompt)
        self.assertIn("NAME_BUDGET = 120", src)
        # Every short identity string goes through clip(..., NAME_BUDGET) —
        # the DJ name, the station name, the show name, and (since the
        # top-down review) the persona language.
        self.assertEqual(src.count("NAME_BUDGET)"), 4)


class TestTheConductHarnessCannotReachTheRealStation(unittest.TestCase):
    """`scripted_call.py` is run against the LIVE station, deliberately — it is
    the only way to check conduct, and its docstring promises that nothing is
    queued, nothing is announced and no segment runs.

    That promise is kept by one function, `muzzle_the_station()`, which swaps
    each writing StationClient method for a recorder. It is a hand-maintained
    list, and it had already fallen behind: `skip_track` and `dj_segment`
    arrived in 0.9.54, went into the tool registry, and were never added here —
    so with either switched on, a scripted run could cut the record the
    station's listeners were hearing. Deriving the list from station.py means
    the next write method cannot slip through the same gap.
    """

    @classmethod
    def setUpClass(cls):
        import ast
        import re

        here = AGENT_WORKER
        tree = ast.parse((here / "station.py").read_text(encoding="utf-8"))
        client = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.ClassDef) and n.name == "StationClient")

        cls.writes = set()
        for fn in client.body:
            if not isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in {"post", "put", "delete"}):
                    cls.writes.add(fn.name)

        harness = (here / "scripted_call.py").read_text(encoding="utf-8")
        cls.muzzled = set(re.findall(r"StationClient\.(\w+)\s*=", harness))

    def test_the_scan_found_the_writing_methods(self):
        # A regex that quietly matched nothing would make the real assertion
        # below pass forever.
        self.assertGreaterEqual(len(self.writes), 4)
        self.assertIn("dj_say", self.writes)

    def test_every_writing_station_method_is_muzzled(self):
        escaped = sorted(self.writes - self.muzzled)
        self.assertEqual(
            escaped, [],
            "scripted_call.py is documented as safe to run against a live "
            "station, but these StationClient methods write and are not "
            f"swapped for a recorder: {escaped}",
        )

    def test_the_two_station_wide_actions_are_covered_by_name(self):
        # Named explicitly as well as derived: these two reach every listener
        # rather than the caller, so they are the ones that must never regress.
        self.assertIn("skip_track", self.muzzled)
        self.assertIn("dj_segment", self.muzzled)


class TestTheDrillsMcpReadsKeepUpWithTheRegistry(unittest.TestCase):
    """The drill sweep (`scripted_call.py` with MCP=1) attaches the station's
    REAL MCP server to a muzzled run, on the strength of one fact: every
    MCP-served tool is a read. MCP_READS pins the names by hand so a write
    that ever became MCP-served would fail closed — left off the sweep —
    rather than firing on air mid-drill. But a pinned list can also fall
    silently behind a NEW read, which shrinks the drill's coverage with no
    sign anywhere. This keeps the two in step. If it fails because a tool
    became MCP-served: add it to MCP_READS only if it is a read; if it
    writes, it belongs behind a LOCAL wrapper (registry.py's own rule), not
    on this list."""

    def test_mcp_reads_equals_the_registrys_mcp_surface(self):
        import ast

        from call.tools.registry import MCP, TOOLS

        tree = ast.parse(
            (AGENT_WORKER / "scripted_call.py").read_text(encoding="utf-8"))
        pinned = None
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign)
                    and any(getattr(t, "id", "") == "MCP_READS"
                            for t in node.targets)):
                pinned = set(ast.literal_eval(node.value))
        self.assertIsNotNone(pinned, "scripted_call.py no longer pins MCP_READS")
        self.assertEqual(pinned, {t.name for t in TOOLS if t.served == MCP})


class TestTheDJKnowsTheStationsShows(unittest.TestCase):
    """Two real calls (2026-08-09, rooms ee3ef9616834 and 7046da2b9289):
    Wade refused a takeover of The Overlook as caller nonsense, because the
    roster line was off by default and, even on, capped at four names on an
    eleven-show station. A DJ who can be asked to switch shows has to
    recognise their names."""

    SHOWS = {"shows": [{"id": f"s{i}", "name": f"Show Number {i}"}
                       for i in range(14)]}

    class _Station:
        """The roster comes from the snapshot now; a station read here is a
        regression, so make one raise rather than quietly re-fetch."""

        async def schedule(self):
            raise AssertionError("the briefing must reuse snap['schedule'], not re-read")

    def _context(self, cfg):
        snap = {"now_playing": {}, "state": {}, "session": {}, "skills": [],
                "schedule": self.SHOWS}
        return asyncio.run(briefing.station_context(
            self._Station(), cfg, snap, {"id": "s0"}))

    def test_the_roster_names_twelve_shows_not_four(self):
        line = briefing._fmt_schedule(self.SHOWS, "s0")
        for i in range(1, 13):
            self.assertIn(f"Show Number {i}", line)

    def test_takeover_brings_the_roster_whatever_context_schedule_says(self):
        text = self._context({"allow_takeover": True, "context_schedule": False})
        self.assertIn("Other shows on this station", text)
        # And the DJ is told the names are real things it can put on air.
        self.assertIn("subwave_takeover_show", text)

    def test_without_either_setting_the_prompt_stays_lean(self):
        text = self._context({"allow_takeover": False, "context_schedule": False})
        self.assertNotIn("Other shows on this station", text)


class TestTheDJIsToldItsOwnLanguage(_TempStores):
    """The prompt states the station's per-DJ language — mirrored, not
    inferred. See TestTheDJsLanguageSurvivesTheRead for the call that made
    this necessary; this half proves the field reaches the DJ's eyes.
    """

    def _build(self, persona) -> str:
        import asyncio

        from station import StationClient

        snapshot = {"dj": {}, "personas": [], "now_playing": {},
                    "state": {}, "session": {}, "schedule": {}}

        async def build() -> str:
            station = StationClient()
            try:
                return await brain.build_system_prompt(
                    station, persona, snapshot=snapshot)
            finally:
                await station.aclose()

        return asyncio.run(build())

    def test_a_named_language_lands_under_who_you_are(self):
        text = self._build({"id": "p_test", "name": "Rosie",
                            "soul": "A test soul.",
                            "language": "Mandarin Chinese"})
        self.assertIn("You work in Mandarin Chinese", text)
        who = text.index("# Who you are")
        facts = text.index("# What's happening on the station")
        self.assertLess(who, text.index("You work in Mandarin Chinese"))
        self.assertLess(text.index("You work in Mandarin Chinese"), facts,
                        "the language belongs to WHO YOU ARE, not to the "
                        "station facts it exists to outrank")

    def test_no_language_means_no_sentence(self):
        text = self._build({"id": "p_test", "name": "Brock",
                            "soul": "A test soul."})
        self.assertNotIn("You work in", text)


class TestTheLastCallersBusinessStaysOutOfTheBriefing(unittest.TestCase):
    """Our own on-air lines must not come back as booth chatter.

    The kind-based filter (_PRIVATE_KINDS) has never matched a live entry:
    /dj/say accepts only 'dj-speak'/'link' and coerces our "callin" marker,
    so the station stores our hand-back lines as plain 'dj-speak' — checked
    against the live session feed 2026-08-23, which holds no 'callin'
    anywhere. The fixtures that pinned the old filter invented the field,
    the same green-test trap as the energy float. What fires live is
    station.said_by_us, fed by dj_say with the text of every aired line.
    """

    def setUp(self):
        import station

        station._AIRED_BY_US.clear()
        self.station = station

    def tearDown(self):
        self.station._AIRED_BY_US.clear()

    def _booth(self, messages):
        from brain.briefing import _fmt_booth

        return _fmt_booth({"messages": messages}, 4)

    def test_a_line_we_aired_is_dropped_however_it_is_kinded(self):
        self.station.note_aired_by_us(
            "Big thanks to Sarah on the line — that request is coming up.")
        out = self._booth([
            {"kind": "dj-speak",
             "text": "Big thanks to Sarah on the line — that request is "
                     "coming up."},
            {"kind": "dj-speak",
             "text": "That was a station announcement about the weekend."},
        ])
        self.assertNotIn("Sarah", out)
        self.assertIn("weekend", out,
                      "the station's own dj-speak lines must survive — the "
                      "kind cannot tell them apart, only the ledger can")

    def test_matching_survives_case_and_whitespace(self):
        self.station.note_aired_by_us("A  Line   We\nAired Tonight, truly.")
        out = self._booth([{"kind": "dj-speak",
                            "text": "a line we aired tonight, TRULY."}])
        self.assertEqual("", out)

    def test_the_kind_filter_still_holds_for_feeds_that_carry_one(self):
        # Costs nothing to keep, and a future station that stores raw kinds
        # gets the stronger filter for free.
        out = self._booth([{"kind": "callin",
                            "text": "Just had Piotr on about his divorce."}])
        self.assertEqual("", out)

    def test_dj_say_feeds_the_ledger_with_what_actually_aired(self):
        import asyncio

        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "ok": True, "mode": "styled", "kind": "dj-speak",
                "spoken": "Here's the styled version that really went out."})

        async def run():
            client = self.station.StationClient(base_url="http://station")
            client._client = httpx.AsyncClient(
                base_url="http://station",
                transport=httpx.MockTransport(handler))
            try:
                with unittest.mock.patch(
                        "station_config.admin_credentials",
                        return_value=("op", "pw")):
                    return await client.dj_say("shout out to sarah")
            finally:
                await client.aclose()

        res = asyncio.run(run())
        self.assertTrue(res.get("ok"))
        self.assertTrue(self.station.said_by_us(
            "Here's the styled version that really went out."),
            "the ledger must hold the SPOKEN text, not the instruction")

    def test_the_ledger_does_not_swallow_strangers(self):
        self.station.note_aired_by_us("Our line about the caller's request.")
        self.assertFalse(self.station.said_by_us(
            "A completely different link the station wrote itself."))


class TestTheDJKnowsWhenTheBroadcastIsNotNormal(unittest.TestCase):
    """/state has carried streamIdle and musicStarved for a while and the
    briefing read neither — so a station paused for an empty room answered a
    request with a 503 and the DJ narrated it as a jammed queue (2026-08-13).
    A fact the DJ holds is a fact it can say instead of invent."""

    def test_a_normal_night_adds_nothing(self):
        from brain.briefing import _fmt_stream_health

        self.assertEqual("", _fmt_stream_health({}))
        self.assertEqual("", _fmt_stream_health(
            {"streamIdle": False, "musicStarved": False}))

    def test_an_idle_station_is_a_stated_fact(self):
        from brain.briefing import _fmt_stream_health

        out = _fmt_stream_health({"streamIdle": True})
        self.assertIn("IDLE", out)
        self.assertIn("resumes as listeners", out)
        self.assertIn("rather than inventing a fault", out)

    def test_a_starved_chain_is_named_as_the_emergency_loop(self):
        from brain.briefing import _fmt_stream_health

        out = _fmt_stream_health({"musicStarved": True})
        self.assertIn("STARVED", out)
        self.assertIn("emergency loop", out)
