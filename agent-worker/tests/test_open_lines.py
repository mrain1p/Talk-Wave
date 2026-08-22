"""Open Lines: the subject the DJ puts to the audience.

The first test in here is the one the feature was allowed to exist on. Open
Lines was built under a standing instruction not to change the existing call,
chat or voicemail paths, and "additive" is a claim that rots the moment
somebody adds a line to the block without checking the gate. So it is pinned,
not asserted.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import settings as settings_store
from openlines import air, director, followup, premise, premises, prompt, state
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


@contextlib.contextmanager
def _fake_recent(items):
    """Stand in for the transcripts on disk."""
    from call import record as record_mod

    real = record_mod.recent
    record_mod.recent = lambda limit=20: list(items)
    try:
        yield
    finally:
        record_mod.recent = real


class _Delta:
    def __init__(self, content):
        self.content = content


class _Chunk:
    def __init__(self, content):
        self.delta = _Delta(content)


class _Stream:
    def __init__(self, text):
        self._text = text

    def __aiter__(self):
        async def gen():
            yield _Chunk(self._text)

        return gen()

    async def aclose(self):
        return None


class _Model:
    def __init__(self, text):
        self._text = text

    def chat(self, chat_ctx=None, **kw):
        return _Stream(self._text)

    async def aclose(self):
        return None


@contextlib.contextmanager
def _fake_llm(said):
    """A model that answers with `said`, and a prompt builder that needs no
    station. Both are stubbed together because line_for assembles the real
    prompt before it asks anything, and the suite never touches the network."""
    import call.providers as providers
    from brain import assemble

    real_build = providers.build_llm
    real_prompt = assemble.build_system_prompt

    async def fake_prompt(*a, **k):
        return "SYSTEM"

    providers.build_llm = lambda cfg: _Model(said)
    assemble.build_system_prompt = fake_prompt
    try:
        yield
    finally:
        providers.build_llm = real_build
        assemble.build_system_prompt = real_prompt


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


class TestTheDJAllowlistActuallyMatches(_OnDisk):
    """Who may open a line, and the bug that made it nobody."""

    def test_the_allowlist_matches_an_id_as_well_as_a_name(self):
        # The picker writes IDS; the matcher only read NAMES. On the operator's
        # panel all 22 personas were listed — which should mean "anyone" — and
        # persona_allowed returned False for every one of them, so the
        # automatic cadence would have refused the whole roster in silence.
        by_id = {"open_lines_personas": "p_default0,p_default1"}
        by_name = {"open_lines_personas": "Dalia, Wade"}
        dalia = {"id": "p_default0", "name": "Dalia"}
        stranger = {"id": "p_nope", "name": "Cliff"}
        for cfg in (by_id, by_name):
            with self.subTest(cfg=cfg):
                self.assertTrue(director.persona_allowed(cfg, dalia))
                self.assertFalse(director.persona_allowed(cfg, stranger))
        # Blank still means whoever is on air.
        self.assertTrue(director.persona_allowed({}, stranger))


class TestAQuizTheDJCanActuallyMark(_OnDisk):
    """A quiz cannot be a free-text premise, and the operator's station proved
    it (2026-08-21, room at 21:31).

    Given the typed subject "Quiz question" — a LABEL — the invitation aired as
    "the suits want me to push something called a 'Quiz question'", and when a
    caller took it up the DJ invented a question on the spot with no answer in
    mind, read their answer as a song request, queued Kenny Rogers, and told
    them they had "whiffed".

    So the question AND its answer are settled before anything airs, and both
    are pinned to the record.
    """

    def _quiz_record(self, **over):
        rec = _record(premise="what did I say I was drinking earlier?",
                      quiz_answer="a coffee that went cold hours ago", show="")
        rec.update(over)
        return rec

    def test_the_answer_is_handed_over_explicitly(self):
        # Without it the DJ marks against a question it half-remembers.
        settings_store.save({"open_lines_enabled": True})
        state.write(self._quiz_record())
        text = self.build_prompt()
        self.assertIn("a coffee that went cold hours ago", text)
        self.assertIn("The answer, which only you know", text)

    def test_an_answer_is_not_a_request(self):
        # The exact failure: "Let It Be by the Beatles" was an ANSWER and the
        # DJ queued Kenny Rogers' "Let It Be Me" instead.
        settings_store.save({"open_lines_enabled": True})
        state.write(self._quiz_record())
        text = self.build_prompt()
        self.assertIn("An answer is NOT a request", text)
        self.assertIn("do not queue anything unless they actually ask", text)

    def test_it_may_not_move_the_goalposts(self):
        settings_store.save({"open_lines_enabled": True})
        state.write(self._quiz_record())
        text = self.build_prompt()
        self.assertIn("Do not invent a different question", text)
        self.assertIn("do not change the answer to fit what they said", text)

    def test_close_enough_is_marked_right(self):
        settings_store.save({"open_lines_enabled": True})
        state.write(self._quiz_record())
        self.assertIn("Close is close", self.build_prompt())

    def test_the_pickup_asks_about_the_quiz_without_giving_it_away(self):
        settings_store.save({"open_lines_enabled": True})
        state.write(self._quiz_record())
        said = prompt.greeting_clause(
            settings_store.permissions_for(settings_store.load(), "admin"),
            PERSONA, "")
        self.assertIn("come to take the quiz", said)
        self.assertIn("Do not give them the answer", said)
        self.assertNotIn("coffee", said)

    def test_a_plain_subject_is_untouched_by_any_of_this(self):
        settings_store.save({"open_lines_enabled": True})
        state.write(_record(show=""))          # no quiz_answer
        text = self.build_prompt()
        self.assertNotIn("running a quiz", text)
        self.assertIn("How to handle whoever turns up", text)

    def test_the_invitation_asks_the_question_rather_than_naming_it(self):
        # "I want to hear about <question>" announces that a question exists.
        asked = air.open_direction("who am I named after?", {}, quiz=True)
        self.assertIn("Here is the question: who am I named after?", asked)
        plain = air.open_direction("the best B-side", {}, quiz=False)
        self.assertIn("I want to hear about the best B-side", plain)

    def test_the_model_is_told_to_ask_about_its_own_world(self):
        # General trivia is a question the DJ would be guessing at too, and a
        # listener cannot win it by having had the show on.
        from openlines import quiz as quiz_mod

        # Some bits have no right answer at all — "Ask the DJ", "Rate My
        # Take" — and demanding one would make the model invent something to
        # fill the field.
        self.assertIn("leave the answer empty", quiz_mod.ASK)
        # In the show's world, whatever that is — and NOT prescribed as
        # music. Donovan's Pub has a fire, a dog and a county; a detective
        # show has none of those, and flattening both into "name the band"
        # would make every persona run the same quiz.
        self.assertIn("does not have to be about music", quiz_mod.ASK)
        self.assertIn("the world of THIS show", quiz_mod.ASK)
        # And the whole point: the booth is never handed the NAME of a bit.
        self.assertIn("Turn that into the ACTUAL thing you will say on air",
                      quiz_mod.ASK)
        # And a claim about tonight is still held to the facts.
        self.assertIn("if it is not in the list then it did not happen",
                      quiz_mod.ASK)

    def test_the_facts_include_what_the_dj_really_said(self):
        # "What did I say earlier?" is a fair question — the station records
        # its own on-air speech, so we supply the answer rather than trusting
        # the DJ to remember it.
        from openlines import quiz as quiz_mod

        snap = {"session": {"messages": [
            {"kind": "dj-speak", "text": "I have had three coffees since lunch."},
            {"kind": "callin", "text": "the last caller's private business"},
            {"kind": "play", "text": "bookkeeping"},
        ]}}
        facts = quiz_mod.facts_from(snap, {"topic": "Late shift classics"}, {})
        self.assertTrue(any("three coffees" in f for f in facts))
        self.assertTrue(any("Late shift classics" in f for f in facts))
        # A line about an earlier CALL is not something the audience heard as
        # part of the show, and it is somebody else's business.
        self.assertFalse(any("private business" in f for f in facts))
        self.assertFalse(any("bookkeeping" in f for f in facts))

    def test_an_answer_from_nowhere_is_refused(self):
        # The bagel gate. Asked what drink it had mentioned, the DJ answered
        # "a plain bagel" — never said, not a drink, and it would have marked
        # a correct caller wrong.
        from openlines import quiz as quiz_mod

        facts = ['played tonight: "Hot Legs" by Rod Stewart',
                 "you said on air: I have had three coffees since lunch."]
        self.assertTrue(quiz_mod.answer_is_grounded("Rod Stewart", facts))
        self.assertTrue(quiz_mod.answer_is_grounded("three coffees", facts))
        self.assertFalse(quiz_mod.answer_is_grounded("a plain bagel", facts))
        self.assertFalse(quiz_mod.answer_is_grounded("", facts))
        self.assertFalse(quiz_mod.answer_is_grounded("anything", []))

    def test_general_knowledge_in_theme_is_not_ground_checked(self):
        # Restricting every question to tonight's facts was too tight. A quiz
        # can be about the show's world; the sin was invented AUTOBIOGRAPHY,
        # not trivia. So "theme" skips the check and "tonight" does not.
        from openlines import quiz as quiz_mod

        themed = quiz_mod._parse(
            '{"kind": "theme", "question": "which county is the pub in?", '
            '"answer": "County Cork"}')
        self.assertEqual(themed["kind"], "theme")
        claimed = quiz_mod._parse(
            '{"kind": "tonight", "question": "what did I say?", '
            '"answer": "a plain bagel"}')
        self.assertEqual(claimed["kind"], "tonight")

    def test_a_missing_kind_is_treated_as_a_claim_about_tonight(self):
        # The careful branch is the default: a model that omits the field must
        # not get the unchecked one.
        from openlines import quiz as quiz_mod

        got = quiz_mod._parse('{"question": "q", "answer": "a"}')
        self.assertEqual(got["kind"], "tonight")
        odd = quiz_mod._parse('{"kind": "whatever", "question": "q", "answer": "a"}')
        self.assertEqual(odd["kind"], "tonight")

    def test_an_unparseable_quiz_is_no_quiz(self):
        # Half a quiz must never air: no question means no answer to mark.
        from openlines import quiz as quiz_mod

        for junk in ("", "Sure! Here you go.", "{not json", '{"question": ""}',
                     '{"answer": "42"}'):
            with self.subTest(junk=junk):
                self.assertEqual(quiz_mod._parse(junk), {})

    def test_a_fenced_json_answer_still_parses(self):
        from openlines import quiz as quiz_mod

        got = quiz_mod._parse(
            'Sure —\n```json\n{"question": "what am I drinking?", '
            '"answer": "cold coffee"}\n```')
        self.assertEqual(got["question"], "what am I drinking?")
        self.assertEqual(got["answer"], "cold coffee")


class TestThePickupAsksAboutTheTopic(_OnDisk):
    """The greeting is the one turn the conduct block never reached.

    call/greeting.py generates the pickup from its OWN instruction, before the
    caller has said anything — the system prompt's Open Lines block governs
    every turn after it. So a listener who heard the subject on air, rang in,
    and was asked "what would you like to hear?" (operator, 2026-08-21, with a
    line demonstrably live for that DJ and show at the time).
    """

    def clause(self, persona=None, show=""):
        from openlines import prompt as open_lines

        return open_lines.greeting_clause(
            settings_store.permissions_for(settings_store.load(), "admin"),
            persona if persona is not None else PERSONA, show)

    def test_nothing_is_added_when_the_feature_is_off(self):
        settings_store.save({"open_lines_enabled": False})
        state.write(_record(show=""))
        self.assertEqual(self.clause(), "")

    def test_nothing_is_added_when_no_line_is_up(self):
        settings_store.save({"open_lines_enabled": True})
        state.write(None)
        self.assertEqual(self.clause(), "")

    def test_nothing_is_added_for_another_djs_line(self):
        settings_store.save({"open_lines_enabled": True})
        state.write(_record(persona_id="someone-else", show=""))
        self.assertEqual(self.clause(), "")

    def test_nothing_is_added_once_the_show_has_changed(self):
        # Same rule the block follows: a subject opened in one programme must
        # not greet a caller in the next.
        settings_store.save({"open_lines_enabled": True})
        state.write(_record(show="Late Shift"))
        self.assertEqual(self.clause(show="Breakfast"), "")
        self.assertNotEqual(self.clause(show="Late Shift"), "")

    def test_an_expired_line_adds_nothing(self):
        settings_store.save({"open_lines_enabled": True})
        old = datetime.now(timezone.utc) - timedelta(minutes=1)
        state.write(_record(expires_at=_iso(old), show=""))
        self.assertEqual(self.clause(), "")

    def test_it_replaces_the_usual_question_rather_than_adding_one(self):
        # Two questions in one breath is exactly what the conduct block two
        # inches away tells the DJ not to do.
        settings_store.save({"open_lines_enabled": True})
        state.write(_record(show=""))
        said = self.clause()
        self.assertIn("INSTEAD of your usual opening question", said)
        self.assertIn("not both", said)
        self.assertIn("whether the remaster ruined it", said)

    def test_the_pickup_does_not_re_announce_or_give_an_address(self):
        # They already came through. Reading the invitation back at somebody
        # who acted on it is the tell of an automated line.
        settings_store.save({"open_lines_enabled": True})
        state.write(_record(show=""))
        said = self.clause().lower()
        self.assertIn("do not re-read the subject", said)
        self.assertIn("do not give out any address", said)

    def test_the_greeting_instruction_actually_carries_it(self):
        # The clause is worth nothing if greet() never appends it, which is the
        # bug this whole class exists for.
        import inspect

        from call import greeting

        src = inspect.getsource(greeting.greet)
        self.assertIn("greeting_clause", src)
        # And it must ride the operator's OWN greeting too, not only the
        # default — cfg["greeting"] overrides the default entirely.
        self.assertIn("greeting +=", src)


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
        # A fresh install seeds three starters on first read. These tests are
        # about the shelf's BEHAVIOUR, so they start from an empty one — and
        # clearing it also proves the seed does not come back (it only fires
        # when the file is missing, never when it is present and empty).
        for item in premises.read():
            premises.remove(item["id"])

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

    def test_a_fresh_install_finds_subjects_and_bits(self):
        # So the button does something on day one. Seeded on READ rather than
        # shipped as a file, which is what lets an operator empty the shelf
        # deliberately and have it stay empty.
        for item in premises.read():
            premises.remove(item["id"])
        premises.PREMISES_PATH.unlink(missing_ok=True)
        from openlines.quiz import FORMATS

        seeded = premises.read()
        # Subjects AND bits: a fresh shelf offers both one-off topics and the
        # recurring formats, which are resolved into tonight's instance
        # rather than read out as a label.
        self.assertEqual(len(seeded), len(premises.STARTERS) + len(FORMATS))
        self.assertTrue(all(i.get("starter") for i in seeded))
        self.assertEqual(sum(1 for i in seeded if i.get("format")),
                         len(FORMATS))
        # Aimed at nobody, so they work whichever DJ is on.
        self.assertTrue(all(i["personas"] == [] for i in seeded))

    def test_an_emptied_shelf_stays_empty(self):
        for item in premises.read():
            premises.remove(item["id"])
        self.assertEqual(premises.read(), [], "the starters must not come back")

    def test_picking_one_by_id_marks_it_used(self):
        # The dashboard dropdown is a choice, not a rotation.
        a = premises.add("first")
        b = premises.add("second")
        got = premises.take_one(b["id"])
        self.assertEqual(got["text"], "second")
        by_id = {i["id"]: i for i in premises.read()}
        self.assertEqual(by_id[b["id"]]["used"], 1)
        self.assertEqual(by_id[a["id"]]["used"], 0)
        self.assertEqual(premises.take_one("gone"), {})

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


class TestTwoDirectorsCannotAirTheSameThingTwice(_OnDisk):
    """A redeploy runs two web containers for a few seconds, and each has a
    director loop reading the same record off the same bind mount.

    Every latch here used to be set AFTER the station finished speaking, so the
    window between "has this aired?" and "it has now" was the length of a TTS
    call. Observed on the operator's NAS 2026-08-21: a sign-off aired from one
    process while another was mid-air with its own, and the second overwrote
    the first's text with an empty string.

    Claiming first does not make it atomic — that needs a lock file, whose
    stale-lock failure mode is worse than the race — but it narrows the window
    from seconds to two filesystem operations.
    """

    def test_the_signoff_can_only_be_claimed_once(self):
        state.write(_record(closed=True))
        first = state.claim_signoff()
        second = state.claim_signoff()
        self.assertTrue(first, "the first director must get it")
        self.assertEqual(second, {}, "the second must be turned away")

    def test_claiming_marks_it_before_anything_airs(self):
        # The point of the whole change: the latch is on disk BEFORE the
        # station is asked to speak, not after it answers.
        state.write(_record(closed=True))
        state.claim_signoff()
        self.assertTrue(state.read_raw().get("signed_off"))

    def test_a_record_already_signed_off_is_never_reclaimed(self):
        state.write(_record(closed=True, signed_off=True))
        self.assertEqual(state.claim_signoff(), {})

    def test_no_record_claims_nothing(self):
        state.write(None)
        self.assertEqual(state.claim_signoff(), {})

    def test_the_reason_survives_a_hand_close(self):
        # An operator's Close writes closed_reason first; the claim must not
        # relabel it "expired" underneath them.
        state.write(_record())
        state.close(reason="operator")
        state.claim_signoff()
        self.assertEqual(state.read_raw().get("closed_reason"), "operator")

    def test_an_expired_line_gets_the_default_reason(self):
        state.write(_record(closed=True))
        state.claim_signoff()
        self.assertEqual(state.read_raw().get("closed_reason"), "expired")

    def test_a_conversation_is_claimed_before_the_model_is_asked(self):
        # Same shape for follow-ups: two directors that both saw one
        # contribution would both spend a model call and both report it, and
        # the room would hear the DJ discover one person's answer twice.
        rec = state.build("subject", "aired", PERSONA, "", minutes=60,
                          source="dj", reminder_minutes=0, reminder_max=0)
        rec = state.note_seen(rec, "c1")
        state.write(rec)
        self.assertIn("c1", state.read_raw()["followed_up"])
        # And a claimed one is no longer a candidate for anybody.
        self.assertEqual(
            followup.candidates(rec, rec["opened_at"], rec["followed_up"]), [])


class TestAnUnconfirmedLineIsStillAnOpenLine(_OnDisk):
    """Found by air-testing on the operator's own station, 2026-08-21.

    `station.dj_say` answers a slow-but-sent request with
    `{"ok": True, "unconfirmed": True}` and no `spoken` — correct, because the
    station almost certainly said something. `air.say` collapsed that into ""
    and every caller read "" as "nothing aired", so a slow station meant
    listeners heard the DJ open a subject while Talk Wave wrote no record at
    all: the DJ had no idea it had asked, and the operator was told the booth
    refused. Worse than either honest answer.

    Three outcomes now, not two.
    """

    def _station(self, result):
        class _S:
            async def dj_say(self, *a, **k):
                return result

        return _S()

    def test_a_refusal_is_nothing_aired(self):
        spoken, aired = asyncio.run(
            air.say(self._station({"ok": False, "error": "401"}), "d"))
        self.assertEqual((spoken, aired), ("", False))

    def test_the_good_case_returns_the_words(self):
        spoken, aired = asyncio.run(
            air.say(self._station({"ok": True, "spoken": "I said this"}), "d"))
        self.assertEqual((spoken, aired), ("I said this", True))

    def test_unconfirmed_aired_with_the_words_lost(self):
        # The case that was wrong: it went out, we just cannot quote it.
        spoken, aired = asyncio.run(
            air.say(self._station({"ok": True, "unconfirmed": True}), "d"))
        self.assertEqual(spoken, "")
        self.assertTrue(aired, "an unconfirmed line went out; it is not a miss")

    def test_the_prompt_still_works_without_the_words(self):
        # The DJ knows the subject even when its own phrasing is lost, so it
        # can still lead with it — it just must not be handed an empty quote
        # and told those were its words.
        settings_store.save({"open_lines_enabled": True})
        state.write(_record(spoken="", show=""))
        text = self.build_prompt()
        self.assertIn("whether the remaster ruined it", text)
        self.assertNotIn("Your own words on air", text)


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

        spoken, aired = asyncio.run(
            air.say(_Booth(), air.open_direction("a subject", {})))
        self.assertEqual(spoken, "the words that aired")
        self.assertTrue(aired)
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

        self.assertEqual(asyncio.run(air.say(_Refuses(), "x")), ("", False))

    def test_no_address_is_invented_when_none_is_set(self):
        # A DJ told to invite calls and given nowhere to send them makes one up.
        direction = air.open_direction("s", {})
        self.assertIn("Do not invent an address", direction)
        # And it must not then ask for the one thing it just forbade: the
        # model line has no address in it, so neither does what it keeps.
        self.assertNotIn("where to reach you", direction)
        self.assertIn("Keep both", direction)
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


class TestTheSignOffOnlySaysWhatItActuallyHeard(_OnDisk):
    """It used to invent the contributions.

    Handed only a COUNT and asked to "say what you took from what came in", the
    DJ had no idea what came in and filled the gap. On the operator's station,
    2026-08-21, it closed a line about first driving songs with "for everyone
    else, it's some obscure ballad or a goddamn polka" — nobody had said
    either, and follow-ups were switched off, so the DJ had heard nothing at
    all. Great radio, and a caller who rang in could hear their answer
    described as something they never said.
    """

    def test_nobody_took_it_up_is_unchanged(self):
        said = air.close_direction("the subject", 0)
        self.assertIn("Nobody took it up", said)

    def test_people_came_but_nothing_was_reported_forbids_inventing(self):
        # The exact hole the polka came out of: arrivals counted, content
        # never seen, because follow-ups were off.
        said = air.close_direction("the subject", 3)
        self.assertIn("you do not know what any of them said", said)
        self.assertIn("do NOT characterise their answers", said.replace("Do NOT", "do NOT"))
        self.assertNotIn("say what you took from what came in", said)

    def test_it_summarises_only_what_really_aired(self):
        lines = ["someone argued the original pressing wins every time",
                 "one caller said the remaster finally let them hear the bass"]
        said = air.close_direction("the subject", 2, lines)
        for line in lines:
            self.assertIn(line, said)
        self.assertIn("ALL you know about what came in", said)
        self.assertIn("do not invent what other people said", said)

    def test_blank_reported_lines_do_not_count_as_content(self):
        # A follow-up the station aired without echoing back leaves "" behind;
        # that is not something the DJ heard.
        said = air.close_direction("the subject", 2, ["", "   "])
        self.assertIn("you do not know what any of them said", said)

    def test_the_words_are_kept_when_a_followup_airs(self):
        rec = state.build("subject", "aired", PERSONA, "", minutes=60,
                          source="dj", reminder_minutes=0, reminder_max=0)
        rec = state.note_followup(rec, "c1", "they argued the original wins")
        self.assertEqual(rec["followup_lines"], ["they argued the original wins"])
        rec = state.note_followup(rec, "c2", "another said the opposite")
        self.assertEqual(len(rec["followup_lines"]), 2)
        # A conversation considered and not aired adds no words.
        rec = state.note_seen(rec, "c3")
        self.assertEqual(len(rec["followup_lines"]), 2)


class TestReportingBackToTheRoom(_OnDisk):
    """The loop was open at one end: the DJ asked a question on the broadcast,
    somebody answered it in a private conversation, and no listener ever heard
    that it happened — so nobody had reason to think the question was real.

    What airs is the POSITION, never the person and never their words. A
    contribution is offered to a DJ, not to a microphone.
    """

    def _conversation(self, cid, when, turns=6):
        return {"id": cid, "startedAt": _iso(when),
                "turns": [{"who": "caller" if i % 2 == 0 else "dj",
                           "text": "line %d" % i} for i in range(turns)]}

    def test_only_conversations_that_started_after_the_line_went_up(self):
        now = datetime.now(timezone.utc)
        rec = _record(opened_at=_iso(now - timedelta(minutes=10)))
        before = self._conversation("old", now - timedelta(minutes=30))
        after = self._conversation("new", now - timedelta(minutes=2))
        with _fake_recent([after, before]):
            ids = [c["id"] for c in followup.candidates(
                rec, rec["opened_at"], [])]
        self.assertEqual(ids, ["new"])

    def test_one_already_reported_is_never_reported_twice(self):
        # The latch. Without it a restart between ticks would have the DJ
        # discover the same contribution again, out loud.
        now = datetime.now(timezone.utc)
        rec = _record(opened_at=_iso(now - timedelta(minutes=10)))
        item = self._conversation("c1", now - timedelta(minutes=1))
        with _fake_recent([item]):
            self.assertEqual(len(followup.candidates(rec, rec["opened_at"], [])), 1)
            self.assertEqual(
                followup.candidates(rec, rec["opened_at"], ["c1"]), [])

    def test_a_hello_is_not_a_contribution(self):
        # Two turns is somebody arriving and leaving. Asking the model about
        # every one of those is spending a call on a wave.
        now = datetime.now(timezone.utc)
        rec = _record(opened_at=_iso(now - timedelta(minutes=10)))
        short = self._conversation("brief", now - timedelta(minutes=1), turns=2)
        with _fake_recent([short]):
            self.assertEqual(followup.candidates(rec, rec["opened_at"], []), [])

    def test_the_cap_is_per_line_and_not_a_setting(self):
        # Three is enough for a topic to feel alive and few enough that a busy
        # evening cannot turn the broadcast into a read-out of its own text
        # line. A cap an operator can raise is a cap that ends up raised.
        self.assertEqual(followup.MAX_PER_LINE, 3)
        self.assertNotIn("open_lines_followup_max", settings_store.FIELDS)

    def test_counting_one_records_which_conversation_it_was(self):
        rec = state.build("subject", "aired", PERSONA, "", minutes=60,
                          source="dj", reminder_minutes=0, reminder_max=0)
        self.assertEqual(rec["followups_sent"], 0)
        rec = state.note_followup(rec, "c1")
        self.assertEqual(rec["followups_sent"], 1)
        self.assertEqual(rec["followed_up"], ["c1"])
        # Seen-but-not-aired marks the id without spending the cap.
        rec = state.note_seen(rec, "c2")
        self.assertEqual(rec["followups_sent"], 1)
        self.assertEqual(rec["followed_up"], ["c1", "c2"])

    def test_nothing_means_nothing_airs(self):
        # Most conversations while a line is open are requests, and a DJ
        # reporting "someone asked for a Beatles record" as a contribution is
        # worse than silence.
        for said in ("NOTHING", "nothing", "NOTHING.", "  NOTHING  "):
            with self.subTest(said=said):
                with _fake_llm(said):
                    line = asyncio.run(followup.line_for(
                        {}, None, PERSONA, "the subject",
                        self._conversation("c", datetime.now(timezone.utc))))
                self.assertEqual(line, "")

    def test_the_direction_forbids_inventing_who_they_were(self):
        # A model handed "someone argued X" will cheerfully attribute it to a
        # caller named Dave from Fresno — words and a hometown a real person
        # never offered.
        direction = air.followup_direction(
            "the subject", "they argued the original is better", {})
        low = direction.lower()
        self.assertIn("do not invent a name", low)
        self.assertIn("do not quote them", low)

    def test_the_model_is_told_to_give_the_position_not_the_words(self):
        self.assertIn("POSITION they took", followup.FOLLOW_UP)
        for forbidden in ("No name", "no handle", "no quoting"):
            self.assertIn(forbidden, followup.FOLLOW_UP)

    def test_the_transcript_handed_over_is_two_speakers_oldest_first(self):
        text = followup._transcript({"turns": [
            {"who": "caller", "text": "the original, every time"},
            {"who": "dj", "text": "go on then"},
        ]})
        self.assertEqual(text, "Them: the original, every time\nYou: go on then")

    def test_switched_off_it_never_runs(self):
        # The toggle is the whole decision: this puts more of the DJ on a
        # broadcast, so it ships off.
        self.assertIs(settings_store.FIELDS["open_lines_followup"][1], False)


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
