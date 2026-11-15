"""The late match: a request the station resolves AFTER the tool has answered.

Split out of test_music_tools.py at 0.99.56 along the seam its ledger entry
named — the one class here drives call/tools/late_match.py, the background
poller, and nothing else in that module needs it.
"""

from __future__ import annotations

import asyncio
import unittest


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
