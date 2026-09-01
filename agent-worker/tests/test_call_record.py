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


class TestTheFirstWordIsStampedOnce(unittest.TestCase):
    """firstWordAt is when the DJ's audio STARTS — the first speaking
    transition — because a dj turn commits only after the utterance ends,
    and 'time to first word' measured off turns silently included the whole
    greeting (a 12.5s median on calls whose first audio landed in ~4)."""

    def test_first_word_writes_once_and_reads_as_an_instant(self):
        from call.record import CallRecord

        rec = CallRecord("room-abc123def456", {"name": "Rosie"}, {})
        self.assertNotIn("firstWordAt", rec.data)
        rec.first_word()
        stamped = rec.data["firstWordAt"]
        self.assertIn("+00:00", stamped)     # an instant, with its offset
        rec.first_word()                      # a later utterance is not the first
        self.assertEqual(rec.data["firstWordAt"], stamped)


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
        from call import greeting, handoff

        class _Item:
            def __init__(self, role, content):
                self.role, self.content = role, content

        class _Session:
            history = type("H", (), {"items": [
                _Item("user", greeting.CALL_OPENING_PRIME),
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

        # The default build resolves at the ADMIN tier (assemble.py), and
        # skills default to the guest tier since 0.10.80 — so the fullest
        # view carries the list. What must stay true is that a tier the
        # grant does not reach gets no list: promising the DJ a segment the
        # registry will refuse is how invented capabilities happen.
        self.assertIn("weather", build().lower())

        settings_store.save({"allow_skills": "off"})
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
        from api import readback
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
            resp = asyncio.run(readback.handle_calls(_FakeRequest()))
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


class TestTheOperatorGetsAVerdictToo(unittest.TestCase):
    """The operator's own mark on a call, beside the caller's rather than over
    it.

    Most records carry no caller rating at all — nobody presses the thumbs, and
    a test call the operator placed themselves has nobody to press them. The
    mark is keyed by record id (the panel has it) rather than by room, and it
    must never overwrite `rating`: the thumbs filters and the activity charts
    have always meant "what the caller said", and a second opinion filed in the
    same field would silently rewrite that history.
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

    def _seed(self, **extra):
        from call import record as call_record

        call_record.CALLS_DIR.mkdir(parents=True, exist_ok=True)
        path = call_record.CALLS_DIR / "20260806-120000-abc123def456.json"
        body = {"room": "callin-abc123def456", "turns": []}
        body.update(extra)
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def _read(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_a_mark_lands_on_that_record(self):
        from call import record as call_record

        path = self._seed()
        self.assertTrue(
            call_record.mark_one("20260806-120000-abc123def456", "down"))
        self.assertEqual(self._read(path)["opRating"], "down")

    def test_the_callers_own_rating_is_left_alone(self):
        from call import record as call_record

        path = self._seed(rating="up")
        call_record.mark_one("20260806-120000-abc123def456", "down")
        got = self._read(path)
        self.assertEqual(got["rating"], "up",
                         "the operator's mark overwrote the caller's verdict")
        self.assertEqual(got["opRating"], "down")

    def test_the_rest_of_the_record_survives(self):
        from call import record as call_record

        path = self._seed(turns=[{"who": "caller", "text": "hello"}])
        call_record.mark_one("20260806-120000-abc123def456", "up")
        self.assertEqual(len(self._read(path)["turns"]), 1)

    def test_an_empty_mark_takes_it_off_again(self):
        # A verdict pressed by mistake must have a way back that isn't
        # "delete the transcript". None counts as empty — the panel posts JSON
        # and an absent field arrives as null.
        from call import record as call_record

        for blank in ("", None):
            with self.subTest(mark=blank):
                path = self._seed(opRating="down")
                self.assertTrue(
                    call_record.mark_one("20260806-120000-abc123def456", blank))
                self.assertNotIn("opRating", self._read(path))

    def test_only_up_down_and_empty_are_accepted(self):
        from call import record as call_record

        self._seed()
        for junk in ("sideways", "UP; DROP TABLE", "good"):
            with self.subTest(mark=junk):
                self.assertFalse(
                    call_record.mark_one("20260806-120000-abc123def456", junk))

    def test_an_id_that_leaves_the_directory_is_refused(self):
        # The id arrives from a browser and is used to build a path — the same
        # containment delete_one has, for the same reason.
        from call import record as call_record

        self._seed()
        for junk in ("../../etc/passwd", "20260806-120000-abc123def456.json",
                     "", "a/b"):
            with self.subTest(rid=junk):
                self.assertFalse(call_record.mark_one(junk, "up"))

    def test_a_record_that_does_not_exist_writes_nothing(self):
        from call import record as call_record

        self._seed()
        self.assertFalse(call_record.mark_one("20260101-000000-000000000000", "up"))
        self.assertEqual(len(list(call_record.CALLS_DIR.glob("*.json"))), 1,
                         "a mark for a call we have no record of invented a file")


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
        from api import readback
        from api import tokens as api_tokens

        api_tokens._mint_info["room-gone"] = {"client": "x", "network": "y", "ip": "z"}
        old_auth, admin_auth.AUTH_PATH = admin_auth.AUTH_PATH, Path(self._tmp.name) / "a.json"
        old_key, api_auth.ADMIN_KEY = api_auth.ADMIN_KEY, ""
        try:
            resp = asyncio.run(readback.handle_clear_calls(_FakeRequest()))
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

            def tool(self, name, result="", failed=False):
                self.tools.append((name, result, failed))

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
        self.assertEqual(record.tools,
                         [("subwave_request_song", "Added to the queue", False)])

    def test_a_tool_with_no_output_is_still_recorded(self):
        session, record, _ = self._attach()
        session.handlers["function_tools_executed"](types.SimpleNamespace(
            function_calls=[types.SimpleNamespace(name="subwave_skip_track",
                                                  call_id="c9")],
            function_call_outputs=[],
        ))
        self.assertEqual(record.tools, [("subwave_skip_track", "", False)])

    def test_what_it_searched_FOR_is_written_down_too(self):
        """The half of a tool call the phone never recorded.

        "search_library returned nothing" does not say whether the library
        lacks the track or the DJ looked up the wrong words, and those are
        different bugs with different fixes — it is exactly what the Firestone
        diagnosis turned on. The chat line has recorded arguments since
        0.10.104 with that reasoning written next to it; the phone, which is
        the surface almost every caller uses, did not.
        """
        session, record, _ = self._attach()
        session.handlers["function_tools_executed"](types.SimpleNamespace(
            function_calls=[types.SimpleNamespace(
                name="subwave_search_library", call_id="c2",
                arguments='{"q": "Firestorm by Kygo"}')],
            function_call_outputs=[types.SimpleNamespace(
                call_id="c2", output="No track or artist by that name")],
        ))
        name, detail, failed = record.tools[0]
        self.assertEqual(name, "subwave_search_library")
        self.assertIn("Firestorm by Kygo", detail)
        self.assertIn("No track or artist", detail)
        self.assertFalse(failed)

    def test_a_refused_tool_is_marked_failed(self):
        """A call that talked its way around three refusals used to read back
        as a call where the DJ simply chatted. The record has carried the flag
        since 0.10.104 and only the chat line ever set it."""
        session, record, _ = self._attach()
        session.handlers["function_tools_executed"](types.SimpleNamespace(
            function_calls=[types.SimpleNamespace(
                name="subwave_request_song", call_id="c3", arguments="{}")],
            function_call_outputs=[types.SimpleNamespace(
                call_id="c3", output="rate limited", is_error=True)],
        ))
        self.assertTrue(record.tools[0][2], "the refusal is invisible again")

    def test_unreadable_arguments_never_cost_the_receipt(self):
        # A record is what you reach for when a call went wrong, so nothing in
        # here may throw. Malformed JSON keeps the result and says what it saw.
        session, record, _ = self._attach()
        session.handlers["function_tools_executed"](types.SimpleNamespace(
            function_calls=[types.SimpleNamespace(
                name="subwave_browse_library", call_id="c4",
                arguments="{not json")],
            function_call_outputs=[types.SimpleNamespace(call_id="c4",
                                                         output="8 results")],
        ))
        self.assertIn("8 results", record.tools[0][1])


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


class TestAnAskThatWentNowhereIsWrittenDown(unittest.TestCase):
    """The gap that made the director question unanswerable.

    A record holds turns, tools and problems, and nothing joins them — so "the
    caller asked for a shoutout and no shoutout was ever sent" could not be seen
    in the archive at all. Not rare: INVISIBLE. Three reviews argued about
    whether calls lose things across turns with no way to look.

    Detection only. Nothing branches on it, the DJ is never told, and no turn is
    generated because of it — if the archive fills with these, a director has a
    case; if it does not, the turn-by-turn shape is fine.
    """

    def _asks(self):
        from call.asks import Asks

        return Asks()

    def test_an_ask_with_nothing_after_it_is_reported(self):
        asks = self._asks()
        asks.heard("can you play Africa by Toto", at=100.0)
        self.assertEqual(1, len(asks.unanswered(acted_at=[])))

    def test_an_action_after_the_ask_answers_it(self):
        asks = self._asks()
        asks.heard("can you play Africa by Toto", at=100.0)
        self.assertEqual([], asks.unanswered(acted_at=[101.0]))

    def test_an_action_BEFORE_the_ask_answers_nothing(self):
        # The whole reason this holds timestamps rather than a count: an action
        # that landed earlier belonged to an earlier ask.
        asks = self._asks()
        asks.heard("and put a shoutout out for Marcus", at=200.0)
        self.assertEqual(1, len(asks.unanswered(acted_at=[100.0])))

    def test_chatter_is_not_an_ask(self):
        # Counting "what's playing?" would report a dropped ask on every call
        # that went perfectly well — it is answered in words and leaves no
        # receipt. Only the shape that OWES an action counts.
        asks = self._asks()
        for chatter in ("what's playing right now?",
                        "how long have you been on air?",
                        "that's a great record",
                        "he's had a rough week",
                        "yeah that's the one",
                        # The three near-misses the 2026-08-14 widening had to
                        # step around. Each is one word away from a real ask.
                        "have you got any idea what that was?",
                        "give me a second, the dog's going mad",
                        "how long have you been on the air tonight?"):
            asks.heard(chatter, at=1.0)
        self.assertEqual([], asks.unanswered(acted_at=[]))

    def test_the_plainest_request_shapes_are_heard(self):
        # Replaying this detector over the real archive on 2026-08-14 — the
        # first time it had met a caller rather than scenario text — caught
        # five of thirteen tool-shaped asks. Every line below is one it MISSED,
        # verbatim from a record, and they are the ordinary ways people ask a
        # radio station for a record. The one that matters most is the first:
        # on 2026-08-11 a caller asked "Got any Zeppelin?", was told "let me
        # take a quick look through the racks", no tool ever ran, and the call
        # ended twenty seconds later with "Still with me?" — a dropped ask that
        # this module exists to make visible and could not see.
        for said in ("Got any Zeppelin?",
                     "Do you have any Zeppelin?",
                     "Give me something acoustic, surprise me.",
                     "surprise me on Zeppelin.",
                     "Can you put Wade on the radio?",
                     "Hey, can you tell me a story on air?"):
            asks = self._asks()
            asks.heard(said, at=1.0)
            self.assertEqual(1, len(asks.unanswered(acted_at=[])), said)

    def test_every_shape_of_action_ask_is_caught(self):
        for said in ("can you play Dreams for me",
                     "put on something mellow",
                     "give a shoutout to my mate Marcus",
                     "can you skip this one",
                     "change the DJ to Wade",
                     "never play that again"):
            asks = self._asks()
            asks.heard(said, at=1.0)
            self.assertEqual(1, len(asks.unanswered(acted_at=[])), said)

    def test_an_unrelated_action_no_longer_wipes_the_first_ask(self):
        # The slice-3 recon's verified evaporation (2026-08-31): one action
        # of ANY kind used to settle every ask before it, so the moment a
        # shoutout landed, "play Africa" vanished from the comeback, the
        # hold-return nod and the dropped-ask line all at once. Each action
        # settles the latest open ask it followed — one action, one ask.
        asks = self._asks()
        asks.heard("can you play Africa by Toto", at=100.0)
        asks.heard("and give a shoutout to Marcus", at=200.0)
        open_ = asks.unanswered(acted_at=[210.0])
        self.assertEqual(1, len(open_))
        self.assertIn("Africa", open_[0])

    def test_two_actions_still_settle_two_asks(self):
        asks = self._asks()
        asks.heard("can you play Africa by Toto", at=100.0)
        asks.heard("and give a shoutout to Marcus", at=200.0)
        self.assertEqual([], asks.unanswered(acted_at=[210.0, 220.0]))

    def test_a_rephrase_is_one_ask_not_two(self):
        # A caller who says it twice and gets it once is answered — the
        # settled ask folds any open ask that reads as the same request
        # (stuck.same_ask), so the rephrase does not haunt the record as a
        # second dropped ask.
        asks = self._asks()
        asks.heard("can you play Africa by Toto", at=100.0)
        asks.heard("play Africa by Toto for me please", at=150.0)
        self.assertEqual([], asks.unanswered(acted_at=[160.0]))

    def test_find_me_something_is_heard(self):
        # The flow set's interrupted-ask row says "find me something by Max
        # Richter" — and the pattern was deaf to it, hearing the row only
        # through the accidental token "queue" inside "DON'T queue anything
        # yet" (slice-3 recon, 2026-08-31).
        asks = self._asks()
        asks.heard("find me something by Max Richter", at=1.0)
        self.assertEqual(1, len(asks.unanswered(acted_at=[])))

    def test_the_ledger_stamps_when_an_action_landed(self):
        # The record's side of the same question.
        from call.actions import CallActions

        actions = CallActions(5)
        actions.note("request", "Africa")
        self.assertEqual(1, len(actions.taken_at))
        self.assertGreater(actions.taken_at[0], 0)


class TestASearchNobodyHeardTheAnswerToIsWrittenDown(unittest.TestCase):
    """The inverse of every honesty finding here, and the archive was blind.

    Those are all the DJ claiming MORE than a tool returned. This is the tool
    returning the goods and the DJ withholding them — which the unanswered-ask
    check scores as fine, because a tool did run.

    Both fixtures below are real, from 2026-08-16, and they are the point: the
    same station, the same tool, the same eight results, one call that told the
    caller and one that did not.
    """

    FOUND = ('(q=\'Fleetwood Mac\') -> 8 result(s):\n'
             '"Landslide" by Fleetwood Mac (The White Album, 1975) — calm\n'
             '"Crystal" by Fleetwood Mac (The White Album, 1975) — calm')

    def _call(self, dj_lines):
        import types

        from call.record import CallRecord

        rec = CallRecord.__new__(CallRecord)
        rec.data = {"turns": [], "tools": [], "problems": []}
        rec.data["tools"].append(
            {"t": "2026-08-16T05:14:42+00:00",
             "name": "subwave_search_library", "result": self.FOUND})
        for i, line in enumerate(dj_lines):
            rec.data["turns"].append(
                {"t": f"2026-08-16T05:14:5{i}+00:00", "who": "dj",
                 "text": line})
        return types.SimpleNamespace(record=rec)

    def _problems(self, call):
        from call import postmortem

        postmortem._note_if_a_lookup_was_never_read_out(call)
        return call.record.data["problems"]

    def test_a_search_the_dj_never_mentioned_is_reported(self):
        # Verbatim from the call. Eight tracks came back and the caller was
        # told nothing at all — not yes, not no, not one title. That record
        # carried zero problems.
        call = self._call([
            "Fair enough — just keeping an eye on the collection. It's a "
            "classic list, always good to know what's tucked away in the "
            "crates just in case."])
        found = self._problems(call)
        self.assertEqual(1, len(found), "a search nobody heard back is invisible")
        self.assertIn("never told", found[0]["what"])

    def test_naming_the_results_counts_as_answering(self):
        # Also verbatim, from the call that went well an hour later.
        call = self._call([
            "That's a classic choice. I've got a few versions here — the one "
            "by Fleetwood Mac, The Chicks, or even a jazz take from Dexter "
            "Gordon. Which one were you thinking of?"])
        self.assertEqual([], self._problems(call))

    def test_a_summary_counts_too(self):
        # The bar is "did the caller learn the answer", not "did the DJ read
        # the list out". Naming the artist they asked about is enough.
        call = self._call(["We've got a good handful of Fleetwood Mac, yeah."])
        self.assertEqual([], self._problems(call))

    def test_the_answer_may_arrive_one_turn_late(self):
        # The promise guard can put a turn between the tool and the answer,
        # and that turn is usually the DJ saying it is looking.
        call = self._call(["One second, let me have a look.",
                           "Right — Landslide is in there."])
        self.assertEqual([], self._problems(call))

    def test_a_search_that_found_nothing_is_not_a_missed_answer(self):
        # "Nothing in the racks tonight" cannot contain a title, so scoring it
        # by whether titles were spoken would flag every honest empty search.
        import types

        from call.record import CallRecord

        rec = CallRecord.__new__(CallRecord)
        rec.data = {"turns": [{"t": "2026-08-16T05:14:50+00:00", "who": "dj",
                               "text": "Nothing in the racks for that one."}],
                    "tools": [{"t": "2026-08-16T05:14:42+00:00",
                               "name": "subwave_search_library",
                               "result": "(q='Zzz') -> no results"}],
                    "problems": []}
        self.assertEqual([], self._problems(types.SimpleNamespace(record=rec)))


class TestAFabricatedQueueIdIsWrittenDown(unittest.TestCase):
    """The DJ passed invented slug ids to subwave_queue_mix with no search
    anywhere before them, the picks parser fell back to the title text, the
    tool reported success — and the model learned that fabrication WORKS
    (record 20260827-174809, 17:48:43, verified by hand in the 2026-08-31
    brain review). An id is fabricated exactly when no earlier tool result of
    the same call contains it, which is mechanical to check and was checked
    by nothing."""

    SEARCH = ('(q=\'Gimme Shelter\') -> 1 result(s):\n'
              '"Gimme Shelter" by The Rolling Stones (Let It Bleed, 1969)'
              '  [id: GO9zyAYwhmsdpCOvl0CEiB]')

    def _call(self, tools):
        import types

        from call.record import CallRecord

        rec = CallRecord.__new__(CallRecord)
        rec.data = {"turns": [], "tools": list(tools), "problems": []}
        return types.SimpleNamespace(record=rec)

    def _problems(self, call):
        from call import postmortem

        postmortem._note_if_queued_ids_came_from_nowhere(call)
        return call.record.data["problems"]

    def test_the_real_incident_is_flagged(self):
        # Verbatim shape from the record: a mix queued from ids that exist
        # nowhere, before any search at all.
        call = self._call([
            {"t": "2026-08-27T17:48:43+00:00", "name": "subwave_queue_mix",
             "result": "(label='Ambient electronica mix', picks='id-nils-"
                       "frahm-1 Nils Frahm - Says\nid-kiasmos-1 Kiasmos - "
                       "Looped') -> Queued 2 track(s)"},
        ])
        found = self._problems(call)
        self.assertEqual(1, len(found))
        self.assertIn("id-nils-frahm-1", found[0]["what"])
        self.assertIn("made them up", found[0]["what"])

    def test_an_id_from_a_search_result_is_honest(self):
        call = self._call([
            {"t": "2026-08-27T17:52:50+00:00",
             "name": "subwave_search_library", "result": self.SEARCH},
            {"t": "2026-08-27T17:52:53+00:00", "name": "subwave_queue_mix",
             "result": "(picks='GO9zyAYwhmsdpCOvl0CEiB Gimme Shelter', "
                       "label='Casino tracks') -> Queued 1 track(s)"},
        ])
        self.assertEqual([], self._problems(call))

    def test_queue_track_ids_are_held_to_the_same_bar(self):
        call = self._call([
            {"t": "2026-08-31T12:54:00+00:00", "name": "subwave_queue_track",
             "result": "(title='X', id='raEBK8a0HYd1sgUKTvZhJl') -> queued"},
        ])
        found = self._problems(call)
        self.assertEqual(1, len(found))
        self.assertIn("raEBK8a0HYd1sgUKTvZhJl", found[0]["what"])

    def test_a_truncated_final_pick_is_not_read_as_fabricated(self):
        # The record caps a tool row at 400 characters, so the picks arg can
        # be cut mid-id — the last line of an UNCLOSED picks quote must not
        # count. (The real record's third mix was cut exactly like this.)
        call = self._call([
            {"t": "2026-08-27T17:52:50+00:00",
             "name": "subwave_search_library", "result": self.SEARCH},
            {"t": "2026-08-27T17:56:00+00:00", "name": "subwave_queue_mix",
             "result": "(picks='GO9zyAYwhmsdpCOvl0CEiB Gimme Shelter\n579SLFsZ"},
        ])
        self.assertEqual([], self._problems(call))


class TestTheRecordKeepsThePremiseItRanUnder(unittest.TestCase):
    """Three 2026-09-01 review findings called the DJ's topic references
    fabricated — "that song for the heartbreak subject" and kin — and every
    one was the Open Lines premise doing its job, invisible because the
    record never stored it. The record's config now carries the live
    premise, and "" when the line is closed, so a review can tell the
    feature from an invention."""

    def _line(self, tmp, premise):
        import importlib

        from openlines import state

        old = state.STATE_PATH
        state.STATE_PATH = tmp / "open-line.json"
        rec = state.build(premise, "aired", {"id": "p1", "name": "Cliff"},
                          "", minutes=60, source="dj",
                          reminder_minutes=0, reminder_max=0)
        state.write(rec)
        self.addCleanup(lambda: setattr(state, "STATE_PATH", old))
        return importlib

    def test_a_live_premise_is_written_into_the_config(self):
        import pathlib
        import tempfile

        from call.record import CallRecord

        tmp = pathlib.Path(tempfile.mkdtemp())
        self._line(tmp, "music for the quiet")
        rec = CallRecord("room-abc123def456", {"id": "p1", "name": "Cliff"},
                         {})
        self.assertEqual("music for the quiet",
                         rec.data["config"]["openLine"])

    def test_a_closed_line_writes_an_empty_string_not_a_ghost(self):
        import pathlib
        import tempfile

        from call.record import CallRecord
        from openlines import state

        tmp = pathlib.Path(tempfile.mkdtemp())
        old = state.STATE_PATH
        state.STATE_PATH = tmp / "open-line.json"
        self.addCleanup(lambda: setattr(state, "STATE_PATH", old))
        rec = CallRecord("room-abc123def456", {"id": "p1", "name": "Cliff"},
                         {})
        self.assertEqual("", rec.data["config"]["openLine"])


class TestNextPromisedFromDownTheQueueIsWrittenDown(unittest.TestCase):
    """Record 20260831-125306: the caller asked for a track "next", the
    receipt said number three, and the DJ said "lined up for you next" in
    the same breath as the number. Unlike the general false-state-claim
    detector this repo has twice declined to build, this claim has ground
    truth in the same record — the receipt carries the position in the
    tool's own words — so the contradiction is checkable, not guessed at."""

    RECEIPT = ("(\"Africa\") -> queued. It's number 3 in the queue — roughly "
               "9-12 minutes away. You may tell them that.")

    def _call(self, tools, dj_lines):
        import types

        from call.record import CallRecord

        rec = CallRecord.__new__(CallRecord)
        rec.data = {"turns": [{"who": "dj", "t": t, "text": x}
                              for t, x in dj_lines],
                    "tools": list(tools), "problems": []}
        return types.SimpleNamespace(record=rec)

    def _problems(self, call):
        from call import postmortem

        postmortem._note_if_next_was_promised_from_down_the_queue(call)
        return call.record.data["problems"]

    def test_the_real_incident_is_flagged(self):
        call = self._call(
            [{"name": "subwave_queue_track", "t": "10", "result": self.RECEIPT}],
            [("11", "That's lined up for you next, my friend.")])
        found = self._problems(call)
        self.assertEqual(1, len(found))
        self.assertIn("number 3", found[0]["what"])

    def test_an_honest_read_out_of_the_number_is_not_flagged(self):
        call = self._call(
            [{"name": "subwave_queue_track", "t": "10", "result": self.RECEIPT}],
            [("11", "It's number three in the queue, ten minutes or so out.")])
        self.assertEqual([], self._problems(call))

    def test_a_track_that_really_is_next_is_not_flagged(self):
        call = self._call(
            [{"name": "subwave_queue_track", "t": "10",
              "result": "-> queued. It's next up, so it plays after the "
                        "current track."}],
            [("11", "That's up next for you!")])
        self.assertEqual([], self._problems(call))


class TestAFailedToolCallIsWrittenDown(unittest.TestCase):
    """The record has carried a failed flag on tool rows since 0.10.104, and
    nothing anywhere read it: a call where the model INVENTED a tool name
    ('no such tool') carried seven problems, none of them about that (record
    20260827-174809, brain review 2026-08-31)."""

    def _call(self, tools):
        import types

        from call.record import CallRecord

        rec = CallRecord.__new__(CallRecord)
        rec.data = {"turns": [], "tools": list(tools), "problems": []}
        return types.SimpleNamespace(record=rec)

    def _problems(self, call):
        from call import postmortem

        postmortem._note_if_a_tool_call_failed(call)
        return call.record.data["problems"]

    def test_an_invented_tool_name_is_a_routing_failure(self):
        call = self._call([
            {"t": "2026-08-27T17:56:44+00:00", "name": "subwave_station_state",
             "result": "no such tool", "failed": True},
        ])
        found = self._problems(call)
        self.assertEqual(1, len(found))
        self.assertIn("does not exist", found[0]["what"])
        self.assertIn("subwave_station_state", found[0]["what"])

    def test_an_errored_tool_is_named_with_its_result(self):
        call = self._call([
            {"t": "2026-08-27T17:56:44+00:00", "name": "subwave_queue_track",
             "result": "raised TimeoutError: station gone", "failed": True},
        ])
        found = self._problems(call)
        self.assertEqual(1, len(found))
        self.assertIn("subwave_queue_track", found[0]["what"])

    def test_a_refusal_is_not_a_failure(self):
        # A station refusal comes back as a normal result with no failed
        # flag — the system working. Only rows the tool layer marked failed
        # land here.
        call = self._call([
            {"t": "2026-08-27T17:50:06+00:00",
             "name": "subwave_clear_from_queue",
             "result": "Nothing waiting in the queue matches — don't claim a "
                       "clear-out."},
        ])
        self.assertEqual([], self._problems(call))


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

            def tool(self, name, result="", failed=False):
                self.tools.append((name, result, failed))

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


class TestThePickupTimelineIsWrittenDown(unittest.TestCase):
    """The pickup used to be a black box between startedAt and firstWordAt:
    diagnosing 2026-08-18's slow connects meant probing the station, the TTS
    backend and the model by hand to find which leg the wait lived in (it was
    the model). The leg stamps and the snapshot-source note make the next
    slow-pickup report readable off the record alone."""

    def _record(self):
        from call.record import CallRecord

        return CallRecord("callin-o-abcdef123456", {"id": "p1", "name": "D"},
                          {}, "open")

    def test_legs_land_as_seconds_since_the_caller_arrived(self):
        r = self._record()
        r.leg("prepared")
        r.leg("onLine")
        setup = r.data["setup"]
        self.assertGreaterEqual(setup["preparedSecs"], 0.0)
        self.assertGreaterEqual(setup["onLineSecs"], setup["preparedSecs"])

    def test_the_snapshot_source_is_named(self):
        r = self._record()
        r.setup_note("snapshot", "prefetched")
        self.assertEqual("prefetched", r.data["setup"]["snapshot"])


class TestTheDayLogRemembersActionsNotPeople(unittest.TestCase):
    """call/daylog.py — decision 3 of the conversation-engine review, in the
    operator's own scoping: station-CHANGING actions only, attributed by
    door tier and time, and never a word of caller content. "Did you
    recently cancel my queue?" (2026-08-26, the Casino night's opening
    line) is what this exists to answer with a lookup instead of a
    per-call evasion."""

    def setUp(self):
        import os
        import tempfile

        self._dir = tempfile.TemporaryDirectory()
        os.environ["DAYLOG_PATH"] = os.path.join(self._dir.name,
                                                 "day-log.json")

    def tearDown(self):
        import os

        os.environ.pop("DAYLOG_PATH", None)
        self._dir.cleanup()

    def test_station_changing_kinds_land_and_speech_does_not(self):
        # Sharpened 2026-09-01 when the Requests tab widened KINDS: the
        # announcement EVENT lands now (the tab is the receipt printer),
        # but the SPEECH — the caller's own dedication — still does not.
        # The covenant was always about the words, not the row.
        from call import daylog

        daylog.note("album", "Rumours", tier="open")
        daylog.note("announcement", "a shoutout for June", tier="open")
        daylog.note("skip", "Solar", tier="admin")
        entries = daylog.recent()
        self.assertEqual(["skip", "announcement", "album"],
                         [e["kind"] for e in entries])
        self.assertEqual("", entries[1]["what"],
                         "the shoutout's words reached the day-log")

    def test_no_tier_means_no_entry(self):
        # The preview builders and unit fixtures construct a CallActions
        # without a door; their notes must never read as a caller's.
        from call import daylog

        daylog.note("album", "phantom", tier="")
        self.assertEqual([], daylog.recent())

    def test_the_daylog_kinds_match_the_tools(self):
        # KINDS drifted (top-down review, 2026-08-28): it carried two dead
        # strings and missed three kinds the bulk-queue and un-ban tools
        # emit, so a whole album a caller queued left NO trace and the DJ
        # denied it on the ring-back. This pins the door both ways: the
        # bulk actions ARE remembered, and a kind nothing emits is gone.
        from call import daylog

        for live in ("album", "mix", "never-play lifted"):
            self.assertIn(live, daylog.KINDS, f"{live} would be dropped")
        for dead in ("queue", "allowed again"):
            self.assertNotIn(dead, daylog.KINDS, f"{dead} is emitted by no tool")
        # The Requests tab widened the log to the whole receipt printer
        # (2026-09-01) — these are all kinds a tool actually emits.
        for live in ("like", "unlike", "announcement", "skill", "segment"):
            self.assertIn(live, daylog.KINDS, f"{live} would be dropped")

    def test_a_shoutouts_words_never_reach_the_day_log(self):
        # An announcement's detail IS the caller's own dedication, and the
        # 48h log outlives the call and now feeds the player's Requests
        # tab. The KIND lands; the words do not — the same covenant
        # test_a_request_fallback_logs_no_caller_words holds for requests.
        import os
        import tempfile

        from call import daylog

        old = os.environ.get("DAYLOG_PATH")
        os.environ["DAYLOG_PATH"] = os.path.join(
            tempfile.mkdtemp(), "dl.json")
        try:
            daylog.note("announcement", "for June, from her sibling",
                        tier="open")
            daylog.note("like", "Africa", tier="open")
            entries = {e["kind"]: e for e in daylog.recent(10)}
            self.assertEqual("", entries["announcement"]["what"])
            self.assertEqual("Africa", entries["like"]["what"])
        finally:
            if old is None:
                os.environ.pop("DAYLOG_PATH", None)
            else:
                os.environ["DAYLOG_PATH"] = old

    def test_a_request_fallback_logs_no_caller_words(self):
        # Security sitting, 2026-08-28: the request_song fallback paths
        # noted the caller's own request phrase (a dedication naming a
        # person, say), which the day-log then read back to LATER callers —
        # a breach of this module's no-caller-content contract. The
        # wrappers now note a neutral label. This pins the DOOR: whatever a
        # caller typed, a stored request line carries no free text from it.
        from unittest import mock

        from call import daylog
        from call.actions import CallActions
        from call.tools import music

        class _Station:
            async def submit_request(self, text, requester):
                return {"requestId": "r1"}

            async def request_status(self, rid):
                # No matched track yet — drives the ack-only fallback that
                # used to log the caller's words.
                return {"ack": "got it", "track": {}}

        actions = CallActions(9, tier="open")
        secret = "play something for my sister June in Fresno"
        with mock.patch.object(music, "_INLINE_POLL_SECS", 0), \
                mock.patch.object(music, "library_search_needs_mcp",
                                  lambda: False):
            tools = music.build_library_tools(
                {"allow_requests": True}, _Station(), actions)
            tool = next(t for t in tools
                        if t.info.name == "subwave_request_song")
            asyncio.run(tool(request=secret))
        lines = " ".join(e.get("what", "") for e in daylog.recent())
        self.assertNotIn("June", lines)
        self.assertNotIn("Fresno", lines)

    def test_the_lines_carry_doors_never_names(self):
        from call import daylog

        daylog.note("takeover", "The Graveyard Shift", tier="guest")
        text = daylog.as_lines()
        self.assertIn("a guest-code caller", text)
        self.assertIn("The Graveyard Shift", text)

    def test_old_entries_age_out(self):
        import json
        import time

        from call import daylog

        old = [{"t": time.time() - 3 * 24 * 3600, "tier": "open",
                "kind": "album", "what": "ancient"}]
        daylog._path().parent.mkdir(parents=True, exist_ok=True)
        daylog._path().write_text(json.dumps(old), encoding="utf-8")
        daylog.note("album", "fresh", tier="open")
        self.assertEqual(["fresh"], [e["what"] for e in daylog.recent()])

    def test_a_note_rides_the_actions_ledger(self):
        # CallActions.note is where every station action already lands; the
        # day-log rides it, so the phone and the text line feed it with no
        # second wiring — and a day-log failure can never cost the receipt.
        from call import daylog
        from call.actions import CallActions

        a = CallActions(5, tier="open")
        a.note("album", "Africa")
        self.assertEqual("Africa", daylog.recent()[0]["what"])

