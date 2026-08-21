"""Open Lines: the subject the DJ puts to the audience.

The first test in here is the one the feature was allowed to exist on. Open
Lines was built under a standing instruction not to change the existing call,
chat or voicemail paths, and "additive" is a claim that rots the moment
somebody adds a line to the block without checking the gate. So it is pinned,
not asserted.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import settings as settings_store
from openlines import air, director, premise, premises, prompt, state
from openlines import schedule as schedule_mod

from tests.support import _TempStores

# The snapshot the brain tests use — enough station for a prompt, no network.
SNAPSHOT = {"dj": {"station": "Yosemite FM"}, "personas": [],
            "now_playing": {"nowPlaying": {"title": "Dreams"}},
            "state": {}, "session": {}, "schedule": {}}
PERSONA = {"id": "p1", "name": "Dalia", "soul": "warm, unhurried"}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _record(**over) -> dict:
    now = datetime.now(timezone.utc)
    base = {
        "premise": "whether the remaster ruined it",
        "spoken": "I want to hear from you on this one tonight.",
        "opened_at": _iso(now - timedelta(minutes=5)),
        "expires_at": _iso(now + timedelta(minutes=55)),
        "persona_id": "p1", "persona_name": "Dalia", "show": "Late Shift",
        "source": "dj", "reminders_sent": 0, "reminder_max": 2,
        "next_reminder_at": None, "closed": False,
    }
    base.update(over)
    return base


class _OnDisk(_TempStores):
    """Every test here writes the open-line record, so it goes to a temp file.
    A suite that wrote the real one would open a line on the developer's own
    deployment the next time the director ticked."""

    def setUp(self):
        super().setUp()
        self._dir = tempfile.TemporaryDirectory()
        self._old_path = state.STATE_PATH
        state.STATE_PATH = Path(self._dir.name) / "open-line.json"

    def tearDown(self):
        state.STATE_PATH = self._old_path
        self._dir.cleanup()
        super().tearDown()

    def build_prompt(self, mode: str = "call") -> str:
        import brain
        from station import StationClient

        async def go() -> str:
            station = StationClient()
            try:
                return await brain.build_system_prompt(
                    station, PERSONA, snapshot=SNAPSHOT, mode=mode)
            finally:
                await station.aclose()

        return asyncio.run(go())


class TestOpenLinesIsAdditive(_OnDisk):
    """Switched off, or with nothing up, the prompt is byte-for-byte the one
    this codebase built before Open Lines existed."""

    def _without_the_feature(self, mode: str = "call") -> str:
        # `block` is what assemble concatenates. Neutralising it reproduces the
        # prompt as it was before this feature was written — the honest
        # baseline, rather than comparing the feature against itself.
        real = prompt.block
        prompt.block = lambda *a, **k: ""
        try:
            return self.build_prompt(mode)
        finally:
            prompt.block = real

    def test_switched_off_changes_not_one_byte(self):
        settings_store.save({"open_lines_enabled": False})
        for mode in ("call", "chat"):
            with self.subTest(mode=mode):
                self.assertEqual(self._without_the_feature(mode),
                                 self.build_prompt(mode))

    def test_switched_on_with_nothing_up_changes_not_one_byte(self):
        # The regression that matters most: an operator who turns the feature
        # on between topics must not get a different DJ on an ordinary call.
        settings_store.save({"open_lines_enabled": True})
        for mode in ("call", "chat"):
            with self.subTest(mode=mode):
                self.assertEqual(self._without_the_feature(mode),
                                 self.build_prompt(mode))

    def test_a_line_belonging_to_another_dj_changes_not_one_byte(self):
        settings_store.save({"open_lines_enabled": True})
        state.write(_record(persona_id="someone-else"))
        self.assertEqual(self._without_the_feature(), self.build_prompt())

    def test_an_expired_line_changes_not_one_byte(self):
        settings_store.save({"open_lines_enabled": True})
        old = datetime.now(timezone.utc) - timedelta(minutes=1)
        state.write(_record(expires_at=_iso(old)))
        self.assertEqual(self._without_the_feature(), self.build_prompt())


class TestWhatTheDJIsToldAboutTheTopic(_OnDisk):
    def setUp(self):
        super().setUp()
        settings_store.save({"open_lines_enabled": True})
        state.write(_record(show=""))

    def test_the_words_that_aired_are_what_is_pinned(self):
        # Not the direction we sent. With an invented premise the specifics
        # only exist in what the station actually said, and a DJ reminded of
        # the instruction instead invents a second, contradictory version.
        text = self.build_prompt()
        self.assertIn("I want to hear from you on this one tonight.", text)

    def test_a_request_is_never_pushed_aside_for_the_topic(self):
        # The behaviour the whole feature turns on: one light question, and
        # somebody who wants a record played is served exactly as before.
        text = self.build_prompt().lower()
        self.assertIn("if they are not", text)
        self.assertIn("drop it completely", text)

    def test_the_typed_line_is_told_it_is_typed(self):
        self.assertIn("typing", self.build_prompt(mode="chat"))


class TestAnOpenLineBelongsToOneDJAndOneShow(_OnDisk):
    def test_a_show_change_closes_it(self):
        # Same rule the rest of the briefing gets free by re-reading live
        # state. This object outlives a session, so it has to check.
        rec = _record(show="Late Shift")
        self.assertTrue(state.is_live(rec, "p1", "Late Shift"))
        self.assertFalse(state.is_live(rec, "p1", "Breakfast"))

    def test_a_persona_change_closes_it(self):
        rec = _record()
        self.assertFalse(state.is_live(rec, "p2", ""))

    def test_closing_by_hand_wins_immediately(self):
        state.write(_record())
        state.close(reason="operator")
        self.assertFalse(state.is_live(state.read_raw(), "p1", ""))
        self.assertEqual(state.read_raw().get("closed_reason"), "operator")

    def test_a_half_written_record_reads_as_no_line(self):
        # Two containers share this file. A torn read must never crash a call.
        state.STATE_PATH.write_text("{not json", encoding="utf-8")
        self.assertEqual(state.read_raw(), {})
        self.assertEqual(state.current("p1", ""), {})


class TestRemindersCannotRunAway(_OnDisk):
    def test_the_cap_is_what_stops_them(self):
        rec = state.build("subject", "aired", PERSONA, "", minutes=60,
                          source="dj", reminder_minutes=10, reminder_max=2)
        self.assertIsNotNone(rec["next_reminder_at"])
        rec = state.note_reminder(rec, 10)
        self.assertEqual(rec["reminders_sent"], 1)
        self.assertIsNotNone(rec["next_reminder_at"])
        rec = state.note_reminder(rec, 10)
        self.assertEqual(rec["reminders_sent"], 2)
        self.assertIsNone(rec["next_reminder_at"], "the cap must end them")

    def test_none_are_scheduled_when_the_interval_is_zero(self):
        rec = state.build("s", "a", PERSONA, "", minutes=60, source="dj",
                          reminder_minutes=0, reminder_max=3)
        self.assertIsNone(rec["next_reminder_at"])

    def test_a_reminder_is_not_scheduled_past_the_close(self):
        # An invitation nobody can take up. The window is 10 minutes and the
        # interval 30, so the second reminder would land after the sign-off.
        rec = state.build("s", "a", PERSONA, "", minutes=10, source="dj",
                          reminder_minutes=30, reminder_max=5)
        rec = state.note_reminder(rec, 30)
        self.assertIsNone(rec["next_reminder_at"])


class TestTheShelfIsPerDJAndLeastRecentlyUsed(_OnDisk):
    """The shelf replaced a flat list of lines in a settings box.

    A single pool made the DJ allowlist do a job it could not do: it said who
    may open a line at all, not which subjects suit whom. An argument that
    lands in one persona's mouth is wrong in another's, so the aim belongs on
    the premise.
    """

    def setUp(self):
        super().setUp()
        self._pdir = tempfile.TemporaryDirectory()
        self._old_premises = premises.PREMISES_PATH
        premises.PREMISES_PATH = Path(self._pdir.name) / "shelf.json"

    def tearDown(self):
        premises.PREMISES_PATH = self._old_premises
        self._pdir.cleanup()
        super().tearDown()

    def test_an_unaimed_subject_is_available_to_everyone(self):
        premises.add("open to all")
        self.assertEqual(len(premises.for_persona("p1")), 1)
        self.assertEqual(len(premises.for_persona("p99")), 1)

    def test_an_aimed_subject_reaches_only_its_djs(self):
        premises.add("for Dalia and Wade", ["p1", "p2"])
        self.assertEqual(len(premises.for_persona("p1")), 1)
        self.assertEqual(len(premises.for_persona("p2")), 1)
        self.assertEqual(premises.for_persona("p3"), [])

    def test_the_least_recently_used_goes_up_next(self):
        premises.add("first")
        premises.add("second")
        # Never-used sorts before any date, so a freshly added subject is next
        # out — what an operator who just typed it expects.
        self.assertEqual(premises.take_next("p1")["text"], "first")
        self.assertEqual(premises.take_next("p1")["text"], "second")
        self.assertEqual(premises.take_next("p1")["text"], "first")

    def test_using_one_is_counted(self):
        premises.add("counted")
        premises.take_next("p1")
        self.assertEqual(premises.read()[0]["used"], 1)
        self.assertTrue(premises.read()[0]["last_used"])

    def test_a_dj_with_nothing_aimed_at_them_gets_nothing(self):
        premises.add("not for you", ["someone-else"])
        self.assertEqual(premises.take_next("p1"), {})

    def test_removing_one_does_not_disturb_the_rest(self):
        a = premises.add("keep me")
        b = premises.add("remove me")
        self.assertTrue(premises.remove(b["id"]))
        self.assertEqual([i["id"] for i in premises.read()], [a["id"]])
        self.assertFalse(premises.remove("nonexistent"))

    def test_the_aim_can_be_changed_after_the_fact(self):
        item = premises.add("aim me later")
        premises.update(item["id"], personas=["p7"])
        self.assertEqual(premises.read()[0]["personas"], ["p7"])
        self.assertEqual(premises.for_persona("p1"), [])

    def test_an_unreadable_shelf_is_an_empty_one(self):
        premises.PREMISES_PATH.write_text("{not json", encoding="utf-8")
        self.assertEqual(premises.read(), [])
        self.assertEqual(premises.take_next("p1"), {})


class TestALineDoesNotOutlastItsProgramme(unittest.TestCase):
    """The station publishes its week as an hour grid, not as start/end times.

    Sunday-first, which is not a guess — SUB/WAVE's own schedule schema says
    `0 (Sunday) .. 6 (Saturday), matching JS Date.getDay()`.

    `state.is_live` already ends a line when the show changes, so a 60-minute
    line opened 20 minutes before a changeover was always going to stop early.
    Bounding the recorded expiry only makes the countdown honest.
    """

    # Sunday 10:00 and 11:00 belong to show A, 12:00 to show B.
    WEEK = {
        "timezone": "UTC",
        "schedule": {"0": (["x"] * 10) + ["A", "A", "B"] + (["x"] * 11)},
    }

    def at(self, hour, minute=0):
        # 2026-08-23 is a Sunday.
        return datetime(2026, 8, 23, hour, minute, tzinfo=timezone.utc)

    def test_it_ends_when_the_next_show_starts(self):
        end = schedule_mod.item_end(self.WEEK, "A", self.at(10, 30))
        self.assertEqual(end, self.at(12))

    def test_a_long_line_is_cut_to_the_programme(self):
        minutes, cut = schedule_mod.bounded_minutes(
            self.WEEK, "A", 120, self.at(11, 30))
        self.assertEqual(minutes, 30)
        self.assertTrue(cut)

    def test_a_short_line_is_left_alone(self):
        minutes, cut = schedule_mod.bounded_minutes(
            self.WEEK, "A", 15, self.at(11, 30))
        self.assertEqual(minutes, 15)
        self.assertFalse(cut)

    def test_a_schedule_that_cannot_answer_never_shortens_anything(self):
        # No grid, an overridden schedule, or a show that is not where the
        # clock says it is. None of these may stop a line opening.
        for label, week in (
            ("no grid", {"timezone": "UTC"}),
            ("overridden", {**self.WEEK, "override": {"showId": "Z"}}),
        ):
            with self.subTest(case=label):
                self.assertIsNone(schedule_mod.item_end(week, "A", self.at(10, 30)))
                self.assertEqual(
                    schedule_mod.bounded_minutes(week, "A", 90, self.at(10, 30)),
                    (90, False))
        # On air outside its own slot: do not bound on a guess.
        self.assertIsNone(schedule_mod.item_end(self.WEEK, "A", self.at(9)))

    def test_the_tail_of_a_show_is_not_bounded_at_all(self):
        # Cutting a line to the last minutes of a programme would air its
        # invitation and its sign-off back to back, which is worse than not
        # bounding: the show change ends it anyway, through is_live.
        for minute in (59, 56):
            with self.subTest(minute=minute):
                minutes, cut = schedule_mod.bounded_minutes(
                    self.WEEK, "A", 45, self.at(11, minute))
                self.assertEqual(minutes, 45)
                self.assertFalse(cut)
        # Just past the floor it does bound.
        minutes, cut = schedule_mod.bounded_minutes(
            self.WEEK, "A", 45, self.at(11, 54))
        self.assertEqual(minutes, 6)
        self.assertTrue(cut)

    def test_sunday_is_day_zero(self):
        # Monday-first indexing would read row "1" and find nothing. Pinned
        # because getting it wrong fails silently: no bound, never an error.
        monday = datetime(2026, 8, 24, 10, 30, tzinfo=timezone.utc)
        self.assertIsNone(schedule_mod.item_end(self.WEEK, "A", monday))
        self.assertEqual(schedule_mod.item_end(self.WEEK, "A", self.at(10, 30)),
                         self.at(12))


class TestOpenLinesRefusesOutLoud(_OnDisk):
    """Every refusal names the gate that stopped it. "Nothing happened" is the
    one answer an operator cannot act on."""

    def test_switched_off_says_so(self):
        settings_store.save({"open_lines_enabled": False})
        out = asyncio.run(director.open_now())
        self.assertFalse(out["ok"])
        self.assertIn("switched off", out["why"])

    def test_a_dj_off_the_list_is_named(self):
        cfg = {"open_lines_personas": "Wade, Marguerite"}
        self.assertFalse(director.persona_allowed(cfg, {"name": "Dalia"}))
        self.assertTrue(director.persona_allowed(cfg, {"name": "wade"}))

    def test_a_blank_list_means_whoever_is_on_air(self):
        self.assertTrue(director.persona_allowed({}, {"name": "Anyone"}))

    def test_an_empty_room_stops_it(self):
        cfg = {"open_lines_min_listeners": 3}
        ok, count = director.listeners_ok(cfg, {"listeners": {"current": 1}})
        self.assertFalse(ok)
        self.assertEqual(count, 1)

    def test_a_floor_of_zero_ignores_the_room(self):
        ok, _ = director.listeners_ok({"open_lines_min_listeners": 0},
                                      {"listeners": {"current": 0}})
        self.assertTrue(ok)

    def test_a_station_that_never_reports_listeners_is_not_shut_out(self):
        # Silence is not proof of an empty room, and the alternative disables
        # the whole feature with nothing on screen to explain why.
        ok, count = director.listeners_ok({"open_lines_min_listeners": 2}, {})
        self.assertTrue(ok)
        self.assertIsNone(count)


class TestTheStationIsNeverReconfigured(_OnDisk):
    """The rule the whole design rests on: Talk Wave may act on the station,
    never reconfigure it."""

    def test_the_announcement_goes_out_as_an_action(self):
        sent = {}

        class _Booth:
            async def dj_say(self, text, mode="raw", kind=""):
                sent.update(text=text, mode=mode, kind=kind)
                return {"ok": True, "spoken": "the words that aired"}

        spoken = asyncio.run(air.say(_Booth(), air.open_direction("a subject", {})))
        self.assertEqual(spoken, "the words that aired")
        # styled = the station writes it in the live persona's own voice. raw
        # would put our phrasing on air in the DJ's mouth.
        self.assertEqual(sent["mode"], "styled")

    def test_the_kind_keeps_it_out_of_the_chatter_window(self):
        # brain.briefing._PRIVATE_KINDS filters this kind out of "things you
        # said on air". That is correct here: prompt.block pins the premise
        # deliberately, and without the filter it would arrive twice.
        from brain import briefing

        self.assertIn(air.SAY_KIND, briefing._PRIVATE_KINDS)

    def test_a_booth_that_says_no_opens_nothing(self):
        class _Refuses:
            async def dj_say(self, text, mode="raw", kind=""):
                return {"ok": False, "error": "401"}

        self.assertEqual(asyncio.run(air.say(_Refuses(), "x")), "")

    def test_no_address_is_invented_when_none_is_set(self):
        # A DJ told to invite calls and given nowhere to send them makes one up.
        self.assertIn("Do not give out any address", air.open_direction("s", {}))
        aimed = air.open_direction("s", {"open_lines_address": "the usual place"})
        self.assertIn("the usual place", aimed)


class TestTheVoicemailGreetingOnlyGrowsWhileALineIsUp(_OnDisk):
    def test_nothing_is_added_when_the_feature_is_off(self):
        settings_store.save({"open_lines_enabled": False})
        state.write(_record(show=""))
        self.assertEqual(prompt.voicemail_clause({}, {"id": "p1"}, ""), "")

    def test_the_subject_is_named_when_one_is_up(self):
        cfg = {"open_lines_enabled": True}
        state.write(_record(show=""))
        said = prompt.voicemail_clause(cfg, {"id": "p1"}, "")
        self.assertIn("whether the remaster ruined it", said)

    def test_the_staged_clip_reverts_when_it_closes(self):
        # The clause rides the greeting TEXT, so it flows through render_key
        # and the clip is re-rendered once per open line rather than per
        # caller. When it closes the text must be exactly what it always was.
        from voicemail import greetings

        cfg = {"open_lines_enabled": True}
        before = greetings.greeting_text_for("p1", cfg, "Yosemite FM", "Dalia")
        state.write(_record(show=""))
        during = greetings.greeting_text_for("p1", cfg, "Yosemite FM", "Dalia")
        state.close()
        after = greetings.greeting_text_for("p1", cfg, "Yosemite FM", "Dalia")
        self.assertNotEqual(before, during)
        self.assertEqual(before, after)


class TestOpenLinesReachesThePanel(unittest.TestCase):
    """Five places, and the panel silently skips a field missing any one."""

    def test_every_setting_has_a_control_with_its_own_id(self):
        from tests.support import REPO

        markup = (REPO / "web-widget" / "panel.html").read_text(
            encoding="utf-8")
        fields = [f for f in settings_store.FIELDS if f.startswith("open_lines")]
        self.assertTrue(fields)
        for name in fields:
            with self.subTest(field=name):
                self.assertIn(name, settings_store.SCHEMA,
                              "no schema entry — the panel cannot render it")
                self.assertIn(f'id="{name}"', markup,
                              "no control in panel.html — it would save and "
                              "be invisible")

    def test_it_is_its_own_page_between_on_air_and_players(self):
        # Operator's call: On air answers "may a caller reach the
        # broadcast"; this answers "what is the station asking them".
        groups = {g[0]: g for g in settings_store.GROUPS}
        self.assertIn("openlines", groups)
        self.assertEqual(groups["openlines"][1], "openlines")
        pages = [s[0] for s in settings_store.SUPERGROUPS]
        self.assertEqual(pages[pages.index("air") + 1], "openlines")
        self.assertEqual(pages[pages.index("openlines") + 1], "card")

    def test_the_buttons_talk_to_routes_that_exist(self):
        from tests.support import REPO

        routes = (REPO / "agent-worker" / "token_server.py").read_text(
            encoding="utf-8")
        for path in ("/open-lines", "/open-lines/open", "/open-lines/close"):
            with self.subTest(path=path):
                self.assertIn(f'"{path}"', routes)


class TestSectionTagsCanShowTheirState(unittest.TestCase):
    """Found while verifying Open Lines' own tag, and it was never about Open
    Lines: EVERY section tag on the panel rendered the same grey.

    setTag() has always written data-state, but the rules reading it sat
    unscoped as `.tag[data-state="on"]` — (0,2,0) against
    `details.sec > summary .tag`'s (0,2,2). The summary rule took both the
    colour and the opacity, so the state never showed anywhere. Measured with
    four tags live in the "on" state, all computing rgb(92,87,79).
    """

    @classmethod
    def setUpClass(cls):
        import re

        from tests.support import REPO

        raw = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")
        # Comments stripped: this file explains its own traps by quoting the
        # selectors involved, and a test that counts rules must not count the
        # prose describing them. (It caught itself doing exactly that.)
        cls.css = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)

    def test_the_state_rules_are_scoped_to_outrank_the_summary(self):
        for state, colour in (("on", "--sage"), ("off", "--sage-dim")):
            with self.subTest(state=state):
                rule = ('details.sec > summary .tag[data-state="%s"]' % state)
                self.assertIn(rule, self.css,
                              "unscoped, this rule loses to the summary rule")
                after = self.css.split(rule, 1)[1].split("}", 1)[0]
                self.assertIn(colour, after)

    def test_the_dead_unscoped_rules_are_gone(self):
        # Left in place they read as working code and invite the next person
        # to "fix" the state by editing a rule that has never applied. Counted
        # rather than matched on whitespace: the scoped rule contains the
        # unscoped selector as a substring, so one occurrence is the fix and
        # two means the dead rule is back.
        for state in ("on", "off"):
            with self.subTest(state=state):
                self.assertEqual(
                    self.css.count('.tag[data-state="%s"]' % state), 1,
                    "an unscoped copy is back; it can never apply")

    def test_the_on_state_beats_the_summary_opacity_too(self):
        # The summary sets opacity .85. Colour alone would have left the "on"
        # tag dimmed against a background it is meant to stand out from.
        rule = 'details.sec > summary .tag[data-state="on"]'
        body = self.css.split(rule, 1)[1].split("}", 1)[0]
        self.assertIn("opacity: 1", body)


class TestTheRecordLandsWhereTheOtherStateDoes(unittest.TestCase):
    """The record must sit in the SAME directory as settings.json.

    It did not. `openlines/state.py` is a directory deeper than `settings.py`,
    so the identical `Path(__file__).parent.parent / "data"` walk landed on
    `agent-worker/data` instead of the repo's `data/` — in the image, `/app/data`
    instead of `/data`, which does not exist. Every write raised PermissionError
    and the whole feature was dead on a real deployment while all 38 local tests
    passed, because every one of them overrides STATE_PATH.

    Found by deploying to the operator's NAS on 2026-08-21. Nothing that stubs
    the path can catch it; only comparing against another module's answer can.
    """

    def test_it_defaults_into_the_repos_own_data_directory(self):
        import importlib
        import os

        from openlines import state as state_mod
        from tests.support import REPO

        # The suite points every writable path at a temp dir, so read the
        # DEFAULT by clearing the override and reloading. Restored after.
        old = os.environ.pop("OPEN_LINE_PATH", None)
        try:
            default = importlib.reload(state_mod).STATE_PATH
        finally:
            if old is not None:
                os.environ["OPEN_LINE_PATH"] = old
            importlib.reload(state_mod)
        self.assertEqual(
            default.parent, REPO / "data",
            "the record must default into the repo's data/, the directory the "
            "deployed stack mounts — one level too shallow lands on "
            "agent-worker/data, which does not exist in the image")


class TestTheRecordSurvivesBothContainers(_OnDisk):
    def test_it_is_written_where_the_worker_can_read_it(self):
        # The worker (calls) and the web container (panel, chat, voicemail,
        # the director) are separate processes sharing only the bind-mounted
        # data/. Anything cached in memory would outlive the operator's Close.
        rec = _record()
        state.write(rec)
        self.assertEqual(json.loads(state.STATE_PATH.read_text(
            encoding="utf-8"))["premise"], rec["premise"])

    def test_clearing_it_leaves_no_file_behind(self):
        state.write(_record())
        state.write(None)
        self.assertFalse(state.STATE_PATH.exists())
        self.assertEqual(state.current(), {})


if __name__ == "__main__":
    unittest.main()
