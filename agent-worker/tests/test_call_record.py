"""The transcript: what is written down about a call, and what is deliberately not.

Split out of test_sidecar.py; see tests/__init__.py.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import types
import unittest
from pathlib import Path
import brain
import settings as settings_store
from brain import briefing, conduct
from tests.support import _FakeRequest, _TempStores


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

    def test_a_history_entry_with_no_live_event_does_not_shift_the_rest(self):
        """Taken from a real call (2026-08-05, room 1023dbeb3e28), where the
        written transcript and the live log disagreed about who said what.

        finalise used to pair the Nth live event with the Nth history entry.
        The call opens with a primed `user` turn so the model has something to
        answer, and that turn never produces a live event — so every caller
        line landed on the NEXT line's timestamp, the last one was appended at
        call-end time, and callerTurns came out one too high (which gates the
        back-to-air mention). The record reported no problem while being wrong
        about the whole call.

        The prime is dropped at the source now; this pins the general case,
        because any unmatched history entry did the same damage.
        """
        r = self.record.CallRecord("callin-x", {"id": "p1", "name": "Dawn"}, {})
        r.turn("dj", "Yosemite FM, you're on with Dawn.")
        r.turn("caller", "Can you play me a song?")
        r.turn("caller", "Sit with.")
        r.turn("caller", "Hello.")
        stamps = {t["text"]: t["t"] for t in r.data["turns"]}

        # An extra caller entry the events never saw, at the FRONT.
        r.finalise([("caller", "[Call connected. You speak first.]"),
                    ("dj", "Yosemite FM, you're on with Dawn."),
                    ("caller", "Can you play me a song?"),
                    ("caller", "Sit with."),
                    ("caller", "Hello.")])

        said = [(t["who"], t["text"]) for t in r.data["turns"]]
        self.assertEqual(said, [
            ("dj", "Yosemite FM, you're on with Dawn."),
            ("caller", "Can you play me a song?"),
            ("caller", "Sit with."),
            ("caller", "Hello."),
            # The unmatched entry is appended, never folded into a real turn.
            ("caller", "[Call connected. You speak first.]"),
        ])
        # Every line the caller actually said keeps the time it was heard.
        for text in ("Can you play me a song?", "Sit with.", "Hello."):
            got = next(t["t"] for t in r.data["turns"] if t["text"] == text)
            self.assertEqual(got, stamps[text],
                             f"{text!r} was moved onto another line's timestamp")

    def test_the_opening_prime_is_not_a_caller_turn(self):
        """It sits in the history as a `user` message because that is the only
        shape Gemini accepts a leading function call after — but the caller
        neither said nor heard it. Counting it inflates callerTurns, which is
        what `callback_min_turns` reads to decide whether a call was worth
        mentioning on air."""
        from call import handoff, lifecycle

        class _Item:
            def __init__(self, role, content):
                self.role, self.content = role, content

        class _Session:
            history = type("H", (), {"items": [
                _Item("user", lifecycle.CALL_OPENING_PRIME),
                _Item("assistant", "Yosemite FM, you're on with Dawn."),
                _Item("user", "Can you play me a song?"),
            ]})()

        got = handoff.transcript(_Session())
        self.assertEqual(got, [("assistant", "Yosemite FM, you're on with Dawn."),
                               ("user", "Can you play me a song?")])

    def test_any_bracketed_note_is_a_prime_not_a_caller_turn(self):
        """The opening prime stopped being the only one: the late request-match
        note rides the same mechanism (a bracketed `user` turn the caller never
        said). One rule covers them all — a user turn that is nothing but
        bracketed text is ours — so a new note can't quietly re-inflate
        callerTurns the way the opening prime once did. An STT never produces
        brackets, so a real caller line is never mistaken for one."""
        from call import handoff

        class _Item:
            def __init__(self, role, content):
                self.role, self.content = role, content

        class _Session:
            history = type("H", (), {"items": [
                _Item("assistant", "That's in for you, love."),
                _Item("user", "[The station has just resolved the earlier "
                              "request: it matched \"Spiders\" by Moby.]"),
                _Item("assistant", "Turns out the station picked Spiders by Moby."),
                # Brackets mid-sentence are ordinary words, not a prime.
                _Item("user", "I said [something] in brackets once"),
            ]})()

        got = handoff.transcript(_Session())
        self.assertEqual(got, [
            ("assistant", "That's in for you, love."),
            ("assistant", "Turns out the station picked Spiders by Moby."),
            ("user", "I said [something] in brackets once"),
        ])

    def test_a_dj_repeating_itself_is_written_down_as_a_problem(self):
        """From a real call (2026-08-08): the idle ladder fired on schedule and
        the model answered the check-in AND the goodbye by re-generating its
        previous line — the caller heard the same sentence three times, and
        nothing in the record said anything was wrong. The transcript is the
        thing an operator reads to find bad calls, so the record flags it."""
        line = ("The station came up trumps, love — it's \"Spiders\" by Moby. "
                "Bloody perfect, innit? It's in the queue a couple of tracks down.")
        near = ("The station came up trumps, love — it's \"Spiders\" by Moby. "
                "Bloody perfect, innit? It's sat in the queue a couple of tracks down.")
        r = self._a_call()
        r.turn("dj", line)
        r.turn("dj", near)
        r.write()
        problems = " ".join(p["what"] for p in r.data["problems"])
        self.assertIn("same line twice in a row", problems)

    def test_an_answered_caller_is_not_a_repeat(self):
        # The other half: a DJ who says similar things across a normal
        # conversation — or twice with the caller answering in between — is
        # just a DJ with a catchphrase, not a looping model.
        line = ("That one's lined up for you, love — a couple of tracks down "
                "the running order, so keep an ear out for it.")
        r = self._a_call()
        r.turn("dj", line)
        r.turn("caller", "lovely, thanks")
        r.turn("dj", line)
        r.write()
        problems = " ".join(p["what"] for p in r.data["problems"])
        self.assertNotIn("same line twice in a row", problems)

    def test_short_canned_lines_may_repeat(self):
        # Two "Still with me?" in a row is the canned idle fallback doing its
        # job — the floor keeps the detector off it.
        r = self._a_call()
        r.turn("dj", "Still with me?")
        r.turn("dj", "Still with me?")
        r.write()
        self.assertEqual(
            [p for p in r.data["problems"] if "twice in a row" in p["what"]], [])

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
        r = self._a_call()
        r.write()                       # must not raise
        # And must actually have tried. Resting on "no exception" alone meant
        # this passed for a write() that returned early — or did nothing at
        # all — so it asserts the body ran and left the record complete.
        self.assertIn("endedAt", r.data)
        self.assertIn("durationSecs", r.data)
        self.assertEqual(self.record.recent(), [])   # nothing landed anywhere

    def test_a_runaway_call_cannot_write_an_unbounded_file(self):
        r = self._a_call()
        for i in range(self.record.MAX_TURNS + 200):
            r.turn("caller", f"line {i}")
        r.turn("caller", "x" * (self.record.MAX_TEXT + 500))
        r.write()
        c = self.record.recent()[0]
        self.assertLessEqual(len(c["turns"]), self.record.MAX_TURNS)
        self.assertTrue(all(len(t["text"]) <= self.record.MAX_TEXT for t in c["turns"]))


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


class TestTheCallRecordSaysWhoRang(_TempStores):
    """The worker writes the transcript and never sees the browser that rang,
    so what we knew at mint time is merged in when /calls is served.

    It is the first question when a call connects and then hears nothing: an
    off-LAN caller with no media path looks identical to a silent one from
    inside the booth. The two halves live in different modules now — the mint
    records it, diagnostics attaches it — which is exactly the join that a
    refactor can drop without any route changing shape.
    """

    def test_what_we_knew_at_mint_time_reaches_the_panel(self):
        import asyncio
        import json

        import admin_auth
        import call.record
        from api import auth as api_auth
        from api import diagnostics as api_diagnostics
        from api import tokens as api_tokens

        api_tokens._mint_info["room-x"] = {
            "client": "Firefox on Windows",
            "network": "off-network",
            "ip": "203.0.113.9",
        }
        # No password anywhere, so the panel gate opens for a request with no
        # Origin — this is testing the merge, not the lock.
        old_auth, admin_auth.AUTH_PATH = admin_auth.AUTH_PATH, Path(self._tmp.name) / "a.json"
        old_key, api_auth.ADMIN_KEY = api_auth.ADMIN_KEY, ""
        real = call.record.recent
        call.record.recent = lambda n: [{"room": "room-x"}, {"room": "room-y"}]
        try:
            resp = asyncio.run(api_diagnostics.handle_calls(_FakeRequest()))
        finally:
            call.record.recent = real
            admin_auth.AUTH_PATH = old_auth
            api_auth.ADMIN_KEY = old_key
            api_tokens._mint_info.pop("room-x", None)

        calls = json.loads(resp.body)["calls"]
        self.assertEqual(calls[0]["caller"]["network"], "off-network")
        self.assertEqual(calls[0]["caller"]["client"], "Firefox on Windows")
        # A call we have no mint record for is left alone rather than given an
        # empty one, so the panel can tell "we don't know" from "same network".
        self.assertNotIn("caller", calls[1])


class TestTheCallerGetsAVerdict(unittest.TestCase):
    """The caller's thumbs, merged into a record somebody else wrote.

    The worker writes the transcript in its shutdown callback; the rating
    arrives over HTTP at the TOKEN SERVER, a separate container. So this is a
    read-modify-write of a file this process did not create, and the two
    things it must not do are invent a record that isn't there and accept
    anything other than the two words the buttons send.
    """

    def setUp(self):
        from call import record

        self._tmp = tempfile.TemporaryDirectory()
        self._old = record.CALLS_DIR
        record.CALLS_DIR = Path(self._tmp.name)

    def tearDown(self):
        from call import record

        record.CALLS_DIR = self._old
        self._tmp.cleanup()

    def _seed(self, room="callin-abc123def456"):
        from call import record as call_record

        call_record.CALLS_DIR.mkdir(parents=True, exist_ok=True)
        path = call_record.CALLS_DIR / f"20260806-120000-{room[-12:]}.json"
        path.write_text(json.dumps({"room": room, "turns": []}),
                        encoding="utf-8")
        return path

    def test_a_rating_lands_on_that_calls_record(self):
        from call import record as call_record

        path = self._seed()
        self.assertTrue(call_record.rate("callin-abc123def456", "down"))
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8"))["rating"], "down")

    def test_the_rest_of_the_record_survives(self):
        # A read-modify-write that dropped the transcript would be worse than
        # no rating at all.
        from call import record as call_record

        path = self._seed()
        call_record.rate("callin-abc123def456", "up")
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8"))["room"],
            "callin-abc123def456")

    def test_only_up_and_down_are_accepted(self):
        from call import record as call_record

        self._seed()
        for junk in ("sideways", "", None, "UP; DROP TABLE"):
            with self.subTest(rating=junk):
                self.assertFalse(call_record.rate("callin-abc123def456", junk))

    def test_an_unknown_room_writes_nothing(self):
        from call import record as call_record

        self._seed()
        self.assertFalse(call_record.rate("callin-000000000000", "up"))
        self.assertEqual(
            len(list(call_record.CALLS_DIR.glob("*.json"))), 1,
            "a rating for a call we have no record of invented a file")

    def test_a_room_too_short_to_identify_is_refused(self):
        # The match is on the last 12 characters of the room name. A short
        # one would glob far more than the call it was meant for.
        from call import record as call_record

        self._seed()
        self.assertFalse(call_record.rate("x", "up"))


class TestStaleRecordsCanBeThrownAway(unittest.TestCase):
    """`record_keep` only trims as new calls arrive, so a deployment that has
    gone quiet keeps whatever it last had forever. After a run of test calls
    the panel is mostly conversations the operator has already read — and they
    are a caller's words, so "wait for enough new calls to age them out" is the
    wrong answer to wanting them gone."""

    def setUp(self):
        import call.record as record

        self._tmp = tempfile.TemporaryDirectory()
        self._old = record.CALLS_DIR
        record.CALLS_DIR = Path(self._tmp.name)

    def tearDown(self):
        import call.record as record

        record.CALLS_DIR = self._old
        self._tmp.cleanup()

    def test_clearing_removes_every_record_and_says_how_many(self):
        import call.record as record

        for n in range(3):
            (record.CALLS_DIR / f"2026080{n}-x.json").write_text("{}", encoding="utf-8")
        self.assertEqual(record.clear(), 3)
        self.assertEqual(list(record.CALLS_DIR.glob("*.json")), [])
        self.assertEqual(record.recent(), [])

    def test_clearing_an_empty_store_is_not_an_error(self):
        import call.record as record

        self.assertEqual(record.clear(), 0)

    def test_the_caller_context_goes_with_the_transcripts(self):
        # It lives in memory on the token server rather than in the record, so
        # clearing the files alone would leave the panel able to say which
        # browser and which network rang for a call that no longer exists.
        import asyncio
        import json

        import admin_auth
        from api import auth as api_auth
        from api import diagnostics as api_diagnostics
        from api import tokens as api_tokens

        api_tokens._mint_info["room-gone"] = {"client": "x", "network": "y", "ip": "z"}
        old_auth, admin_auth.AUTH_PATH = admin_auth.AUTH_PATH, Path(self._tmp.name) / "a.json"
        old_key, api_auth.ADMIN_KEY = api_auth.ADMIN_KEY, ""
        try:
            resp = asyncio.run(api_diagnostics.handle_clear_calls(_FakeRequest()))
        finally:
            admin_auth.AUTH_PATH = old_auth
            api_auth.ADMIN_KEY = old_key
            api_tokens._mint_info.pop("room-gone", None)

        self.assertTrue(json.loads(resp.body)["ok"])
        self.assertEqual(api_tokens._mint_info, {})

    def test_clearing_the_log_buffer_empties_the_viewer(self):
        import log_setup

        log_setup.RECENT.clear()
        for n in range(4):
            log_setup.RECENT.append(
                {"t": "12:00:00", "level": "INFO", "logger": "callin.test", "msg": str(n)})
        self.assertEqual(log_setup.clear(), 4)
        self.assertEqual(log_setup.recent_records(), [])


class TestTheCallRecordHearsBothSides(unittest.TestCase):
    """`heard:` alone was not enough. It showed only the CALLER's side, so a
    report like "he wouldn't hang up" had to be reconstructed from tracebacks
    to work out what the DJ had actually said or tried."""

    def _attach(self):
        from call.lifecycle import attach_heard_logging

        class _Session:
            def __init__(self):
                self.handlers = {}

            def on(self, name, fn):
                self.handlers[name] = fn

        class _Record:
            def __init__(self):
                self.turns = []
                self.tools = []

            def turn(self, who, text):
                self.turns.append((who, text))

            def tool(self, name, result=""):
                self.tools.append((name, result))

        session, record, counter = _Session(), _Record(), {"n": 0}
        attach_heard_logging(session, counter, record)
        return session, record, counter

    def test_the_caller_is_recorded_and_counted(self):
        session, record, counter = self._attach()
        session.handlers["user_input_transcribed"](
            types.SimpleNamespace(transcript="play something loud", is_final=True))
        self.assertEqual(record.turns, [("caller", "play something loud")])
        self.assertEqual(counter["n"], 1)

    def test_a_partial_transcript_is_not_a_turn(self):
        session, record, counter = self._attach()
        session.handlers["user_input_transcribed"](
            types.SimpleNamespace(transcript="play some", is_final=False))
        session.handlers["user_input_transcribed"](
            types.SimpleNamespace(transcript="   ", is_final=True))
        self.assertEqual(record.turns, [])
        self.assertEqual(counter["n"], 0)

    def test_the_dj_is_recorded_too(self):
        session, record, _ = self._attach()
        session.handlers["conversation_item_added"](types.SimpleNamespace(
            item=types.SimpleNamespace(role="assistant", text_content="You're through.")))
        self.assertEqual(record.turns, [("dj", "You're through.")])

    def test_the_callers_own_words_are_not_attributed_to_the_dj(self):
        session, record, _ = self._attach()
        session.handlers["conversation_item_added"](types.SimpleNamespace(
            item=types.SimpleNamespace(role="user", text_content="hello?")))
        self.assertEqual(record.turns, [])

    def test_every_tool_lands_in_the_record_with_its_result(self):
        # The DJ saying it did something is a claim; this line is the receipt.
        session, record, _ = self._attach()
        session.handlers["function_tools_executed"](types.SimpleNamespace(
            function_calls=[types.SimpleNamespace(name="subwave_request_song",
                                                  call_id="c1")],
            function_call_outputs=[types.SimpleNamespace(call_id="c1",
                                                         output="Added to the queue")],
        ))
        self.assertEqual(record.tools, [("subwave_request_song", "Added to the queue")])

    def test_a_tool_with_no_output_is_still_recorded(self):
        session, record, _ = self._attach()
        session.handlers["function_tools_executed"](types.SimpleNamespace(
            function_calls=[types.SimpleNamespace(name="subwave_skip_track",
                                                  call_id="c9")],
            function_call_outputs=[],
        ))
        self.assertEqual(record.tools, [("subwave_skip_track", "")])


class TestOneUtteranceIsOneLineInTheRecord(unittest.TestCase):
    """The SDK merges two live transcripts into one committed turn.

    A caller saying "yeah... maybe a mood" produced two `user_input_transcribed`
    events and one history entry. The first live event matched "Yeah Maybe a
    mood" and claimed it; the second, being the tail rather than the head,
    matched nothing and survived with its own wording. The record then read

        caller: Yeah Maybe a mood
        caller: Maybe a mood

    for something said once — and counted it, which is how one real call logged
    `caller_turns=5` and `only 4 caller turn(s)` one line apart.
    """

    def _record(self, live):
        from call.record import CallRecord

        rec = CallRecord("callin-a-abc", {"id": "p_1", "name": "Cliff"}, {})
        for who, text in live:
            rec.turn(who, text)
        return rec

    def test_the_tail_of_a_merged_turn_is_not_a_second_turn(self):
        rec = self._record([
            ("dj", "What kind of tune are you after?"),
            ("caller", "Yeah"),
            ("caller", "Maybe a mood"),
            ("caller", "Something relaxing."),
        ])
        rec.finalise([
            ("dj", "What kind of tune are you after?"),
            ("caller", "Yeah Maybe a mood"),
            ("caller", "Something relaxing."),
        ])
        said = [t["text"] for t in rec.data["turns"] if t["who"] == "caller"]
        self.assertEqual(said, ["Yeah Maybe a mood", "Something relaxing."])

    def test_the_count_agrees_with_the_lines(self):
        rec = self._record([("caller", "Yeah"), ("caller", "Maybe a mood")])
        rec.finalise([("caller", "Yeah Maybe a mood")])
        rec.write()
        self.assertEqual(rec.data["callerTurns"], 1)

    def test_a_caller_who_really_repeats_themselves_is_kept_twice(self):
        # Two committed entries means the second live turn finds one of its
        # own to match, so nothing is dropped. Deleting a genuine repeat would
        # be the same class of bug in the other direction.
        rec = self._record([("caller", "Maybe a mood"), ("caller", "Maybe a mood")])
        rec.finalise([("caller", "Maybe a mood"), ("caller", "Maybe a mood")])
        said = [t["text"] for t in rec.data["turns"] if t["who"] == "caller"]
        self.assertEqual(said, ["Maybe a mood", "Maybe a mood"])

    def test_an_unmatched_turn_that_is_not_a_fragment_survives(self):
        rec = self._record([("caller", "play some jazz"), ("caller", "actually, funk")])
        rec.finalise([("caller", "play some jazz")])
        said = [t["text"] for t in rec.data["turns"] if t["who"] == "caller"]
        self.assertEqual(said, ["play some jazz", "actually, funk"])


class TestTheRecordAndItsProblemsShareOneClock(unittest.TestCase):
    """A record built when the persona resolved, several seconds of ringing
    after the caller arrived, timed itself from there — so one real call wrote
    `durationSecs: 27.1` next to its own problem line saying "44s on the line".
    Two clocks, one of which the caller never experienced."""

    def test_the_call_start_is_what_gets_timed(self):
        import time as _time

        from call.record import CallRecord

        began = _time.time() - 40
        rec = CallRecord("callin-a-abc", {"id": "p_1", "name": "Cliff"}, {},
                         started=began)
        rec.write()
        self.assertGreaterEqual(rec.data["durationSecs"], 40)

    def test_it_still_times_itself_when_nobody_says_when(self):
        from call.record import CallRecord

        rec = CallRecord("callin-a-abc", {"id": "p_1", "name": "Cliff"}, {})
        rec.write()
        self.assertLess(rec.data["durationSecs"], 5)


class TestASwallowedRequestIsWrittenDown(unittest.TestCase):
    """The speech filter keeps a typed tool call off the air, but silently —
    and from the caller's side that is indistinguishable from the DJ agreeing
    and then doing nothing at all."""

    def _attach(self):
        from call.lifecycle import attach_heard_logging

        class _Session:
            def __init__(self):
                self.handlers = {}

            def on(self, name, fn):
                self.handlers[name] = fn

        class _Record:
            def __init__(self):
                self.turns, self.tools, self.problems = [], [], []

            def turn(self, who, text):
                self.turns.append((who, text))

            def tool(self, name, result=""):
                self.tools.append((name, result))

            def problem(self, what):
                self.problems.append(what)

        session, record = _Session(), _Record()
        attach_heard_logging(session, {"n": 0}, record)
        return session, record

    def _said(self, session, text):
        session.handlers["conversation_item_added"](types.SimpleNamespace(
            item=types.SimpleNamespace(role="assistant", text_content=text)))

    def test_a_typed_tool_call_becomes_a_problem(self):
        session, record = self._attach()
        self._said(session, "tool_code\nprint(default_api.subwave_request_song("
                            "request='Something relaxing'))")
        self.assertEqual(len(record.problems), 1)
        self.assertIn("typed a tool call", record.problems[0])

    def test_an_ordinary_turn_is_not_a_problem(self):
        session, record = self._attach()
        self._said(session, "Sure thing, I'll get that on for you.")
        self.assertEqual(record.problems, [])
