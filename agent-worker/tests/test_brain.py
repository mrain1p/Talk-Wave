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

    def test_tonights_episode_is_its_own_block(self):
        # The Show Card is the standing format the show runs every week;
        # `episodeAngle` is what THIS episode is about. It gets its own block
        # rather than hanging off the card, so a station that can't resolve
        # the show still keeps the one piece of framing it did publish.
        import asyncio

        class _Station:
            async def active_show(self, now_playing=None):
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
        self.assertEqual(cfg["record_keep"], 40)


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


class TestTheDJKnowsTheStationsShows(unittest.TestCase):
    """Two real calls (2026-08-09, rooms ee3ef9616834 and 7046da2b9289):
    Wade refused a takeover of The Overlook as caller nonsense, because the
    roster line was off by default and, even on, capped at four names on an
    eleven-show station. A DJ who can be asked to switch shows has to
    recognise their names."""

    SHOWS = {"shows": [{"id": f"s{i}", "name": f"Show Number {i}"}
                       for i in range(14)]}

    class _Station:
        def __init__(self, payload):
            self.payload = payload
            self.reads = 0

        async def schedule(self):
            self.reads += 1
            return self.payload

    def _context(self, cfg):
        snap = {"now_playing": {}, "state": {}, "session": {}, "skills": []}
        return asyncio.run(briefing.station_context(
            self._Station(self.SHOWS), cfg, snap, {"id": "s0"}))

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
