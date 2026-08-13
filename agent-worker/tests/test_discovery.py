"""The ways into the library that are not a name search.

Every test here is a call that went wrong on 2026-08-12/13, read back off the
records. The shape was always the same: the DJ had one crude tool and used it,
and a caller was told something untrue about the library as a result.
"""

from __future__ import annotations

import asyncio
import unittest

from call.actions import CallActions


class _Station:
    """A station that answers, and remembers what it was asked."""

    def __init__(self, sound=None, neighbours=None, browse=None,
                 now=None) -> None:
        self._sound = sound if sound is not None else []
        self._neighbours = neighbours if neighbours is not None else []
        self._browse = browse if browse is not None else {}
        self._now = now or {}
        self.asked: list[tuple] = []

    async def search_by_sound(self, description, limit=12):
        self.asked.append(("sound", description))
        return self._sound

    async def tracks_like(self, track_id):
        self.asked.append(("like", track_id))
        return self._neighbours

    async def browse_library(self, **kw):
        self.asked.append(("browse", kw))
        return self._browse

    async def now_playing(self):
        return self._now


def _build(cfg, station):
    """Build the discovery tools with credentials present.

    library_search_needs_mcp() reads the real station credentials, and without
    them every tool here is (correctly) withheld — so a test that forgot to
    fake it would pass by building nothing at all.
    """
    from unittest import mock

    from call.tools import discovery

    with mock.patch.object(discovery, "library_search_needs_mcp",
                           return_value=False):
        built = discovery.build_discovery_tools(cfg, station, CallActions(5))
    return {t.info.name: t for t in built}


ALL_ON = {"allow_sound_search": "open", "allow_library_search": "open"}


class TestFindingMusicByHowItSounds(unittest.TestCase):
    """The tool that answers "something dreamy" with real records.

    Before it existed the only answer to a description was a blind request:
    the DJ could not see what came back, could not name it, and could not
    offer a choice. That is the Firestorm chat, and the caller left annoyed.
    """

    def test_a_description_reaches_the_sound_search_not_a_word_match(self):
        station = _Station(sound=[{"id": "t1", "title": "Confession",
                                   "artist": "Craig Armstrong"}])
        tools = _build(ALL_ON, station)
        out = asyncio.run(tools["subwave_search_by_sound"](
            description="dreamy cinematic strings"))

        self.assertEqual(station.asked, [("sound", "dreamy cinematic strings")])
        self.assertIn("Confession", out)
        # The id has to be in the text or the exact-queue tool has nothing to
        # be passed, and the model silently falls back to a request.
        self.assertIn("t1", out)

    def test_an_unavailable_analyser_is_never_reported_as_an_empty_library(self):
        # The station answers 503 when the analyzer is down or nothing has been
        # audio-analysed, and that arrives as an empty list — identical to "no
        # matches". Telling a caller the library has no dreamy music because a
        # container is down is the lie this guards.
        tools = _build(ALL_ON, _Station(sound=[]))
        out = asyncio.run(tools["subwave_search_by_sound"](description="dreamy"))

        self.assertIn("analys", out.lower())
        self.assertIn("NOT evidence", out)

    def test_more_like_this_falls_back_to_the_track_on_air(self):
        # "More like this" is what a caller actually says; they never have an
        # id. If the tool needed one it would go unused.
        station = _Station(
            neighbours=[{"id": "n1", "title": "Hammer Orchid",
                         "artist": "Will Slater", "bpm": 152,
                         "musicalKey": "Am"}],
            now={"track": {"subsonic_id": "onair", "title": "Firestone"}})
        tools = _build(ALL_ON, station)
        out = asyncio.run(tools["subwave_more_like_this"]())

        self.assertIn(("like", "onair"), station.asked)
        self.assertIn("Hammer Orchid", out)
        self.assertIn("Firestone", out)          # names what it worked from
        # bpm and key are what make "this mixes well after that" a statement
        # rather than a claim.
        self.assertIn("152 bpm", out)
        self.assertIn("Am", out)

    def test_nothing_identifiable_on_air_asks_rather_than_guesses(self):
        tools = _build(ALL_ON, _Station(neighbours=[], now={}))
        out = asyncio.run(tools["subwave_more_like_this"]())
        self.assertIn("Ask the caller", out)


class TestBrowsingSpeaksTheStationsOwnVocabulary(unittest.TestCase):
    """A mood word the station does not file under matches nothing.

    Measured on the live library: "melancholy" returns 0 of 381,023 tracks,
    because the station's word is "reflective". A DJ that reports that as an
    empty library is wrong in the way a caller can hear.
    """

    def test_an_unknown_mood_hands_back_the_real_vocabulary(self):
        station = _Station(browse={
            "rows": [], "total": 0,
            "moodVocab": ["energetic", "calm", "reflective"]})
        tools = _build(ALL_ON, station)
        out = asyncio.run(tools["subwave_browse_library"](moods="melancholy"))

        self.assertIn("reflective", out)
        self.assertIn("do NOT tell them the library has nothing", out)

    def test_rows_come_back_with_ids_to_queue(self):
        station = _Station(browse={
            "rows": [{"id": "b1", "title": "Bella", "artist": "Santana"}],
            "total": 41, "moodVocab": ["calm"]})
        tools = _build(ALL_ON, station)
        out = asyncio.run(tools["subwave_browse_library"](genre="Jazz",
                                                          year_from=1960))

        self.assertIn("41", out)                 # the size of the real answer
        self.assertIn("b1", out)
        kw = [a for a in station.asked if a[0] == "browse"][0][1]
        self.assertEqual(kw["genre"], "Jazz")
        self.assertEqual(kw["year_from"], 1960)
        # 0 must not travel as a year — it would filter to nothing.
        self.assertIsNone(kw["year_to"])


class TestDiscoveryToolsRideTheirSwitches(unittest.TestCase):
    """A tool the operator switched off must not be built, and a tool that
    cannot work must not be offered — the model reaches for whatever it is
    given and tells the caller what the failure implies."""

    def test_each_tool_needs_its_own_permission(self):
        self.assertEqual(sorted(_build({}, _Station())), [])
        self.assertEqual(
            sorted(_build({"allow_sound_search": "open"}, _Station())),
            ["subwave_more_like_this", "subwave_search_by_sound"])
        self.assertEqual(
            sorted(_build({"allow_library_search": "open"}, _Station())),
            ["subwave_browse_library"])

    def test_no_station_credentials_means_no_tools_at_all(self):
        # Every endpoint behind these is admin-only, so without credentials
        # they could only ever fail — and a tool that always fails teaches the
        # DJ that the library is empty.
        from unittest import mock

        import station_config

        from call.tools import discovery

        with mock.patch.object(station_config, "admin_credentials",
                               return_value=("", "")):
            built = discovery.build_discovery_tools(ALL_ON, _Station(),
                                                    CallActions(5))
        self.assertEqual(built, [])

    def test_looking_is_free(self):
        # Reads must not spend the caller's action budget: a DJ that has to
        # ration searching is a DJ that guesses instead.
        station = _Station(sound=[{"id": "t1", "title": "x", "artist": "y"}])
        actions = CallActions(1)
        from unittest import mock

        from call.tools import discovery

        with mock.patch.object(discovery, "library_search_needs_mcp",
                               return_value=False):
            tools = {t.info.name: t for t in discovery.build_discovery_tools(
                ALL_ON, station, actions)}
        asyncio.run(tools["subwave_search_by_sound"](description="anything"))
        self.assertEqual(actions.count, 0)
