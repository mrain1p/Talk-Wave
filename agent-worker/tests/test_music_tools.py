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
        from call.tools import music

        asyncio.run(music._surface_late_match(
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
        from call.tools import music

        station, session, rec = self._bits(
            [{"track": {"title": "Spiders", "artist": "Moby"}}])
        session.user_state = "speaking"          # caller mid-word, forever
        beats = music._QUIET_BEATS
        try:
            music._QUIET_BEATS = 1
            self._run(station, session, rec)
        finally:
            music._QUIET_BEATS = beats
        self.assertEqual(session.said, [])

    def test_a_finished_call_is_left_alone(self):
        # The poller can outlive the call; a session that is already gone
        # must end it quietly.
        station, _, rec = self._bits(
            [{"track": {"title": "Spiders", "artist": "Moby"}}])
        from call.tools import music

        asyncio.run(music._surface_late_match(
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
