"""The music tools: what a search is, what a request is, and what the caller
is told about either.

Split out of test_tools_logic.py when the late-match coverage pushed it over
the length ceiling — the seam is real: everything here defends the library
wrappers in call/tools/music.py, nothing here needs the heavyweight main.py
import the rest of that module pays for.
"""

from __future__ import annotations

import asyncio
import unittest


class TestSearchingForWhatTheCallerActuallySaid(unittest.TestCase):
    """The station's search needs EVERY word to match, so the natural phrase a
    caller uses returns nothing. A caller heard "can't pull that from the
    racks" for a track the library holds three copies of."""

    def test_the_by_connector_is_stripped_before_reporting_a_miss(self):
        from call.tools.music import _query_variants

        self.assertEqual(
            _query_variants("Let It Be by The Beatles"),
            ["Let It Be by The Beatles", "Let It Be The Beatles", "Let It Be"],
        )

    def test_a_title_containing_by_survives_the_split(self):
        # Rightmost split, so "Stand by Me" is never torn in half.
        from call.tools.music import _query_variants

        variants = _query_variants("Stand by Me by Ben E. King")
        self.assertIn("Stand by Me", variants)
        self.assertNotIn("Stand", variants)

    def test_a_query_with_no_connector_is_tried_once(self):
        from call.tools.music import _query_variants

        self.assertEqual(_query_variants("Mr. Blue Sky"), ["Mr. Blue Sky"])


class TestAMoodIsNotASearch(unittest.TestCase):
    """A caller saying "something fun" wants the station's picker, not a title
    match — but the model reaches for the search tool anyway, and "fun"
    dutifully returns "Fun, Fun, Fun" by The Beach Boys. Observed on a real
    call. The guard is deliberately conservative: every meaningful word has to
    be a mood word, so real titles are untouched."""

    def test_a_pure_description_is_recognised(self):
        from call.tools.music import looks_like_a_vibe

        for q in ("something fun", "chill", "some upbeat music",
                  "play me something nice"):
            self.assertTrue(looks_like_a_vibe(q), q)

    def test_real_titles_containing_a_mood_word_are_left_alone(self):
        from call.tools.music import looks_like_a_vibe

        for q in ("Fun House by The Stooges", "Mr. Blue Sky",
                  "Heavy Metal by Sammy Hagar"):
            self.assertFalse(looks_like_a_vibe(q), q)

    def test_a_long_phrase_is_never_treated_as_a_vibe(self):
        from call.tools.music import looks_like_a_vibe

        self.assertFalse(looks_like_a_vibe("something fun and upbeat and happy too"))

    def test_an_empty_query_is_not_a_vibe(self):
        from call.tools.music import looks_like_a_vibe

        self.assertFalse(looks_like_a_vibe(""))
        self.assertFalse(looks_like_a_vibe(None))


class TestSearchPagesLikeTheStation(unittest.TestCase):
    """/dj/search pages — the station's own admin Search tab rides the same
    offset — and after the station unfenced its wide sources (its #1339) the
    deep half of a big result set is only reachable through it. The tool's
    page argument is that reach: without one, the ninth match for a common
    word did not exist as far as any caller was concerned."""

    def _tool(self, answers):
        from call.actions import CallActions
        from call.tools import music
        from call.tools.music import build_library_tools

        class _Station:
            def __init__(self):
                self.asked = []

            async def search_library(self, q, offset=0, limit=30):
                self.asked.append((q, offset, limit))
                return answers.get(offset, [])

        st = _Station()
        orig = music.library_search_needs_mcp
        music.library_search_needs_mcp = lambda: False   # as if creds were set
        try:
            tools = build_library_tools(
                {"allow_library_search": True}, st, CallActions(5))
        finally:
            music.library_search_needs_mcp = orig
        tool = next(t for t in tools if t.info.name == "subwave_search_library")
        return st, tool

    def test_a_full_page_offers_the_next_and_page_two_asks_deeper(self):
        rows = [{"title": f"T{i}", "artist": "A"} for i in range(9)]
        st, tool = self._tool({0: rows, 8: rows[:1]})
        out = asyncio.run(tool(q="love"))
        # Nine fetched, eight shown: the extra row is only the "more exists"
        # signal, never a ninth line.
        self.assertIn("8 result(s)", out)
        self.assertIn("page=2", out)
        self.assertEqual(st.asked[0], ("love", 0, 9))
        out2 = asyncio.run(tool(q="love", page=2))
        self.assertIn("page 2", out2)
        self.assertEqual(st.asked[-1], ("love", 8, 9))

    def test_an_empty_deeper_page_says_the_results_ran_out(self):
        # Not the wrong-tool hint: the DJ has already read real results to the
        # caller, so an exhausted page must not send it off to file a request.
        st, tool = self._tool({0: [{"title": "T", "artist": "A"}]})
        out = asyncio.run(tool(q="love", page=3))
        self.assertIn("earlier pages", out)
        self.assertNotIn("subwave_request_song", out)


class TestCurrentLyricsAreARead(unittest.TestCase):
    """The lyrics tool is a read like now-playing: always built, no switch,
    and honest when the station has nothing. A station older than the lyrics
    feature answers 404, which must reach the DJ as 'no lyrics on file' —
    never as an error it apologises to the caller for."""

    def _tool(self, payload):
        from call.actions import CallActions
        from call.tools.music import build_library_tools

        class _Station:
            async def current_lyrics(self):
                return payload

        tools = build_library_tools({}, _Station(), CallActions(5))
        return next(t for t in tools if t.info.name == "subwave_current_lyrics")

    def test_it_is_built_with_nothing_switched_on(self):
        tool = self._tool({})
        self.assertEqual(tool.info.name, "subwave_current_lyrics")

    def test_no_payload_reads_as_no_lyrics_not_an_error(self):
        out = asyncio.run(self._tool({})())
        self.assertIn("No lyrics", out)
        self.assertIn("do not guess", out)

    def test_lines_come_back_and_a_full_sheet_is_capped(self):
        # Prompt budget: the sheet is paid for on every later turn, so a long
        # one is cut at ~2000 characters with an honest count of the rest.
        sheet = {"lines": [{"text": f"line {i} " + "x" * 60} for i in range(80)]}
        out = asyncio.run(self._tool(sheet)())
        self.assertIn("line 0", out)
        self.assertIn("more lines not shown", out)
        self.assertLess(len(out), 2600)


class TestWhatsNewInTheLibrary(unittest.TestCase):
    """/dj/recent has no MCP tool, so the wrapper is the only way a caller can
    ask what's new. It rides the library-search switch — both answer "what
    have you got", and an operator happy to expose one has no reason to hide
    the other — and it is honest when the shelf is empty or unreachable."""

    def _tools(self, cfg, items, creds=True):
        from call.actions import CallActions
        from call.tools import music
        from call.tools.music import build_library_tools

        class _Station:
            async def recent_tracks(self, limit=12):
                return items

            async def search_library(self, q, offset=0, limit=30):
                return []

        orig = music.library_search_needs_mcp
        music.library_search_needs_mcp = lambda: not creds
        try:
            return build_library_tools(cfg, _Station(), CallActions(5))
        finally:
            music.library_search_needs_mcp = orig

    def test_it_rides_the_library_search_switch(self):
        names = [t.info.name
                 for t in self._tools({"allow_library_search": True}, [])]
        self.assertIn("subwave_recent_tracks", names)
        names = [t.info.name for t in self._tools({}, [])]
        self.assertNotIn("subwave_recent_tracks", names)

    def test_without_credentials_it_is_not_built_at_all(self):
        # MCP can stand in for the search, but there is no MCP tool for the
        # recently-added read — so without credentials the tool must simply
        # not exist, rather than exist and always answer empty.
        names = [t.info.name for t in self._tools(
            {"allow_library_search": True}, [], creds=False)]
        self.assertNotIn("subwave_recent_tracks", names)

    def test_arrivals_are_capped_like_a_search_page(self):
        items = [{"title": f"Track{i}", "artist": "A"} for i in range(12)]
        tools = self._tools({"allow_library_search": True}, items)
        tool = next(t for t in tools if t.info.name == "subwave_recent_tracks")
        out = asyncio.run(tool())
        self.assertIn("newest first", out)
        self.assertIn("Track0", out)
        self.assertIn("Track7", out)
        self.assertNotIn("Track8", out)

    def test_an_empty_shelf_is_honest(self):
        tools = self._tools({"allow_library_search": True}, [])
        tool = next(t for t in tools if t.info.name == "subwave_recent_tracks")
        out = asyncio.run(tool())
        self.assertIn("don't invent", out)


class TestLikingTheTrackOnAir(unittest.TestCase):
    """A caller liking the record on air is the same heart any listener taps —
    public at the station, low-harm, off by default. Added on the operator's
    ask (2026-08-10). The gate is what's load-bearing: it must not build unless
    the operator switched it on."""

    def _names(self, cfg):
        from call.tools.music import build_library_tools
        from call.actions import CallActions

        class _Station:
            async def now_playing(self):
                return {"nowPlaying": {"id": "s1", "title": "X", "artist": "Y"}}

            async def like_track(self, song_id):
                return {"ok": True, "count": 3}

            async def search_library(self, q):
                return []

        built = build_library_tools(cfg, _Station(), CallActions(5))
        return {t.info.name for t in built}

    def test_the_like_tool_is_gated_by_allow_favorite(self):
        self.assertNotIn("subwave_like_track", self._names({}))
        self.assertIn("subwave_like_track", self._names({"allow_favorite": True}))

    def test_the_unlike_tool_is_admin_gated_and_needs_credentials(self):
        # Un-hearting is the operator's curation, over an admin endpoint, so it
        # must not even build without station credentials — and never without
        # its own switch. Pretend creds exist to prove the switch, then confirm
        # that with no creds it stays gone even when switched on.
        from call.tools import music

        orig = music.library_search_needs_mcp
        music.library_search_needs_mcp = lambda: False   # as if creds were set
        try:
            self.assertNotIn("subwave_unlike_track", self._names({}))
            self.assertIn("subwave_unlike_track", self._names({"allow_unfavorite": True}))
        finally:
            music.library_search_needs_mcp = orig
        # No credentials in this env, so it cannot be built at all.
        self.assertNotIn("subwave_unlike_track", self._names({"allow_unfavorite": True}))


class TestALateMatchStillReachesTheCaller(unittest.TestCase):
    """The station's resolver can land after the request tool has already
    answered. Observed on a real call (2026-08-08): the station matched
    "Spiders" by Moby some time after the tool's inline look, so the DJ said
    "something is lined up" and could not name it — the caller had to ask.
    The tool now answers immediately and a background task keeps asking, so
    the DJ can volunteer the pick when it lands."""

    def _bits(self, statuses, dj_history=()):
        """A station that answers request_status from a script, a session that
        records what the DJ is told to say, and a record that keeps receipts."""

        class _Station:
            def __init__(self):
                self.calls = 0

            async def request_status(self, rid):
                i = min(self.calls, len(statuses) - 1)
                self.calls += 1
                return statuses[i]

        class _Item:
            def __init__(self, role, content):
                self.role, self.content = role, content

        class _Session:
            agent_state = "listening"
            user_state = "idle"
            history = type("H", (), {
                "items": [_Item("assistant", t) for t in dj_history]})()

            def __init__(self):
                self.said = []

            async def generate_reply(self, **kw):
                self.said.append(kw)

        class _Record:
            def __init__(self):
                self.problems, self.receipts = [], []

            def problem(self, what):
                self.problems.append(what)

            def tool(self, name, result=""):
                self.receipts.append((name, result))

        return _Station(), _Session(), _Record()

    def _run(self, station, session, rec, delays=(0.01, 0.01)):
        from call.tools import late_match

        asyncio.run(late_match._surface_late_match(
            station, "r1", get_session=lambda: session, record=rec,
            delays=delays))

    def test_a_late_match_is_announced_queued_not_playing(self):
        station, session, rec = self._bits([
            {}, {"track": {"title": "Spiders", "artist": "Moby"},
                 "queuePosition": 2}])
        self._run(station, session, rec)

        self.assertEqual(len(session.said), 1)
        note = session.said[0]["user_input"]
        # The same trick as the greeting: a bracketed situation note, never
        # spoken and never a caller turn — see handoff.is_prime.
        self.assertTrue(note.startswith("[") and note.endswith("]"))
        self.assertIn("Spiders", note)
        self.assertIn("not playing", session.said[0]["instructions"])
        # And the receipt is in the record even though the tool had already
        # returned.
        self.assertTrue(any("Spiders" in r for _, r in rec.receipts))

    def test_a_track_the_dj_already_named_is_not_reannounced(self):
        # Exactly the motivating call: the caller asked, the DJ read the
        # answer off station state — announcing it again reads as the DJ
        # forgetting the conversation.
        station, session, rec = self._bits(
            [{"track": {"title": "Spiders", "artist": "Moby"}}],
            dj_history=["It's \"Spiders\" by Moby — bloody perfect, innit?"])
        self._run(station, session, rec)
        self.assertEqual(session.said, [])
        # The receipt still lands; only the announcement is skipped.
        self.assertTrue(rec.receipts)

    def test_a_match_that_never_lands_is_a_problem_line(self):
        # The other half of the operator's complaint: a request whose title
        # never surfaced should be findable without reading every transcript.
        station, session, rec = self._bits([{}])
        self._run(station, session, rec)
        self.assertEqual(session.said, [])
        self.assertTrue(any("never said what it matched" in p
                            for p in rec.problems))

    def test_a_busy_line_is_never_talked_over(self):
        from call.tools import late_match

        station, session, rec = self._bits(
            [{"track": {"title": "Spiders", "artist": "Moby"}}])
        session.user_state = "speaking"          # caller mid-word, forever
        beats = late_match._QUIET_BEATS
        try:
            late_match._QUIET_BEATS = 1
            self._run(station, session, rec)
        finally:
            late_match._QUIET_BEATS = beats
        self.assertEqual(session.said, [])

    def test_a_finished_call_is_left_alone(self):
        # The poller can outlive the call; a session that is already gone
        # must end it quietly.
        station, _, rec = self._bits(
            [{"track": {"title": "Spiders", "artist": "Moby"}}])
        from call.tools import late_match

        asyncio.run(late_match._surface_late_match(
            station, "r1", get_session=lambda: None, record=rec,
            delays=(0.01,)))
        self.assertTrue(rec.receipts)            # the receipt still lands

    def test_library_tools_can_read_the_session_late(self):
        # Same shape as the hang-up tool: tools are built before the
        # AgentSession exists, so the announcer is handed a reader.
        import inspect

        from call.tools.music import build_library_tools

        params = inspect.signature(build_library_tools).parameters
        for name in ("get_session", "air", "record"):
            self.assertIn(name, params)


class TestAQueuedTrackCanComeBackOut(unittest.TestCase):
    """The undo the line spent months telling callers did not exist.

    Record 20260813-021212: the caller asked for a track back out of the
    queue and was told "can't pull a track back once it's rolling down the
    wire". The station has had DELETE /dj/queue/:trackId all along, and the
    track had not started. These hold the tool honest in both directions —
    it must not claim a cancel the station refused either.
    """

    def _tool(self, result, upcoming=(), cfg=None):
        from call.actions import CallActions
        from call.tools import music
        from call.tools.music import build_library_tools

        class _Station:
            def __init__(self):
                self.cancelled = []

            async def state(self):
                return {"upcoming": list(upcoming)}

            async def cancel_queued_track(self, track_id):
                self.cancelled.append(track_id)
                return result

        st = _Station()
        orig = music.library_search_needs_mcp
        music.library_search_needs_mcp = lambda: False   # as if creds were set
        try:
            tools = build_library_tools(
                cfg or {"allow_cancel_queue": True}, st, CallActions(5))
        finally:
            music.library_search_needs_mcp = orig
        tool = next((t for t in tools
                     if t.info.name == "subwave_cancel_queued_track"), None)
        return st, tool

    def test_a_waiting_track_is_pulled_and_said_to_be_gone(self):
        st, tool = self._tool({"ok": True})
        out = asyncio.run(tool(id="t1", title="Firestone"))
        self.assertEqual(st.cancelled, ["t1"])
        self.assertIn("out of the queue", out)
        self.assertIn("will not play", out)

    def test_a_title_is_resolved_against_what_is_actually_queued(self):
        # What the DJ has after "no, not that one" is the NAME it just said
        # out loud, never an id. /state calls the field subsonic_id.
        st, tool = self._tool(
            {"ok": True},
            upcoming=[{"subsonic_id": "q9", "title": "Bella"}])
        out = asyncio.run(tool(title="bella"))
        self.assertEqual(st.cancelled, ["q9"])
        self.assertIn("Bella", out)

    def test_a_track_that_is_not_queued_is_not_claimed_as_pulled(self):
        st, tool = self._tool({"ok": True}, upcoming=[])
        out = asyncio.run(tool(title="Firestone"))
        self.assertEqual(st.cancelled, [])
        self.assertIn("is in the queue", out)
        self.assertIn("rather than saying you pulled it", out)

    def test_the_stations_refusal_reaches_the_caller_as_too_late(self):
        # 409 already-playing is a normal answer, not a fault: the track has
        # left the player's queue. Skip is the only tool for that, and the DJ
        # must be told so rather than inventing a reason.
        st, tool = self._tool({"ok": False, "reason": "already-playing",
                               "error": "on the way to air"})
        out = asyncio.run(tool(id="t1", title="Bella"))
        self.assertIn("Too late", out)
        self.assertIn("CANNOT be pulled", out)
        self.assertIn("skip", out)

    def test_a_failure_is_never_dressed_up_as_a_cancel(self):
        st, tool = self._tool({"ok": False, "error": "station said no"})
        out = asyncio.run(tool(id="t1", title="Bella"))
        self.assertIn("station said no", out)
        self.assertIn("do NOT claim it's gone", out)

    def test_the_tool_rides_its_own_switch(self):
        _, tool = self._tool({"ok": True}, cfg={"allow_requests": True})
        self.assertIsNone(tool)
