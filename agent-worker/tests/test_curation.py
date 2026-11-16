"""What a caller can do to a record's standing, rather than to what plays next.

Likes, un-likes, and the never-play ban. The gates are what is load-bearing in
every one of these: none of them changes the running order, so none of them is
noticeable while it is happening, and the ban is the only thing on a call line
with no expiry at all.
"""

from __future__ import annotations

import asyncio
import unittest

from call.actions import CallActions


class _Station:
    """A station that answers, and remembers what it was asked."""

    def __init__(self, now=None, block=None, unblock=None) -> None:
        self._now = now if now is not None else {
            "nowPlaying": {"id": "s1", "title": "X", "artist": "Y"}}
        self._block = block if block is not None else {"ok": True, "purged": 2}
        self._unblock = unblock if unblock is not None else {"ok": True}
        self.asked: list[tuple] = []

    async def now_playing(self):
        return self._now

    async def like_track(self, song_id):
        self.asked.append(("like", song_id))
        return {"ok": True, "count": 3}

    async def unlike_track(self, song_id):
        self.asked.append(("unlike", song_id))
        return {"ok": True}

    async def block_track(self, track_id):
        self.asked.append(("block", track_id))
        return self._block

    async def unblock_track(self, track_id):
        self.asked.append(("unblock", track_id))
        return self._unblock


def _build(cfg, station=None, actions=None):
    from unittest import mock

    from call.tools import curation

    with mock.patch.object(curation, "library_search_needs_mcp",
                           return_value=False):
        built = curation.build_curation_tools(
            cfg, station or _Station(), actions or CallActions(5))
    return {t.info.name: t for t in built}


class TestLikingTheTrackOnAir(unittest.TestCase):
    """A caller liking the record on air is the same heart any listener taps —
    public at the station, low-harm, off by default. Added on the operator's
    ask (2026-08-10). The gate is what's load-bearing: it must not build unless
    the operator switched it on."""

    def test_the_like_tool_is_gated_by_allow_favorite(self):
        self.assertNotIn("subwave_like_track", _build({}))
        self.assertIn("subwave_like_track", _build({"allow_favorite": True}))

    def test_the_unlike_tool_is_admin_gated_and_needs_credentials(self):
        # Un-hearting is the operator's curation, over an admin endpoint, so it
        # must not even build without station credentials — and never without
        # its own switch.
        from unittest import mock

        import station_config

        from call.tools import curation

        self.assertNotIn("subwave_unlike_track", _build({}))
        self.assertIn("subwave_unlike_track", _build({"allow_unfavorite": True}))
        with mock.patch.object(station_config, "admin_credentials",
                               return_value=("", "")):
            built = curation.build_curation_tools(
                {"allow_unfavorite": True}, _Station(), CallActions(5))
        self.assertEqual([t.info.name for t in built], [])


class TestBanningARecordIsTheOnePermanentThing(unittest.TestCase):
    """Everything else a caller can reach expires: a skip is over in three
    minutes, a takeover in an hour. A never-play entry outlives the call, the
    show and the operator's memory of the call, and nothing goes out on air to
    say it happened. So it is off by default, admin-tier, and it must never be
    reachable without its own switch."""

    def test_neither_half_builds_without_its_switch(self):
        self.assertNotIn("subwave_never_play_track", _build({}))
        self.assertNotIn("subwave_allow_track_again", _build({}))
        # Every other permission being on must not open this one.
        loud = {"allow_favorite": True, "allow_unfavorite": True,
                "allow_skip_track": True, "allow_takeover": True}
        self.assertNotIn("subwave_never_play_track", _build(loud))

    def test_the_lift_rides_the_same_switch_as_the_ban(self):
        # Deliberate: a caller who can impose a permanent judgement on the
        # operator's library must be able to lift one, or the only way back is
        # the operator noticing it happened.
        names = _build({"allow_never_play": True})
        self.assertIn("subwave_never_play_track", names)
        self.assertIn("subwave_allow_track_again", names)

    def test_a_ban_says_the_record_is_still_playing(self):
        # The station drops it from the QUEUE, not from the deck. A DJ that
        # says "it's off now" while the caller can still hear it is wrong in
        # the way a caller checks immediately.
        station = _Station()
        tools = _build({"allow_never_play": True}, station)
        out = asyncio.run(tools["subwave_never_play_track"]())
        self.assertIn(("block", "s1"), station.asked)
        self.assertIn("does NOT stop the copy playing", out)
        self.assertIn("permanent", out.lower())

    def test_an_already_banned_track_is_not_reported_as_a_change(self):
        station = _Station(block={"ok": True, "already": True})
        tools = _build({"allow_never_play": True}, station)
        out = asyncio.run(tools["subwave_never_play_track"]())
        self.assertIn("already", out.lower())
        self.assertIn("nothing", out.lower())

    def test_a_refusal_is_never_dressed_up_as_success(self):
        station = _Station(block={"ok": False, "error": "no station admin credentials"})
        tools = _build({"allow_never_play": True}, station)
        out = asyncio.run(tools["subwave_never_play_track"]())
        self.assertIn("do not claim it worked", out)

    def test_nothing_on_air_is_not_guessed_at(self):
        station = _Station(now={"nowPlaying": {}})
        tools = _build({"allow_never_play": True}, station)
        out = asyncio.run(tools["subwave_never_play_track"]())
        self.assertIn("nothing to ban", out)
        self.assertEqual(station.asked, [])

    def test_a_ban_counts_against_the_call_budget(self):
        # It reaches every listener, permanently. If it were free a caller
        # could empty a library in one call.
        actions = CallActions(1)
        tools = _build({"allow_never_play": True}, _Station(), actions)
        asyncio.run(tools["subwave_never_play_track"]())
        self.assertEqual(actions.count, 1)
        out = asyncio.run(tools["subwave_never_play_track"]())
        self.assertNotIn("never-play list and the station", out)

    def test_lifting_a_ban_that_was_never_set_says_so(self):
        station = _Station(unblock={"ok": True, "already": True})
        tools = _build({"allow_never_play": True}, station)
        out = asyncio.run(tools["subwave_allow_track_again"]())
        self.assertIn("wasn't on the never-play list", out)

    def test_lifting_does_not_claim_the_track_is_queued(self):
        station = _Station()
        tools = _build({"allow_never_play": True}, station)
        out = asyncio.run(tools["subwave_allow_track_again"]())
        self.assertIn(("unblock", "s1"), station.asked)
        self.assertIn("NOT queued", out)


class _Library(_Station):
    """A station with a play log and a library to look a NAME up in."""

    def __init__(self, recent=(), found=(), **kw) -> None:
        super().__init__(**kw)
        self._recent = recent
        self._found = found
        self.searched: list[str] = []

    async def play_history(self, limit=12):
        return [dict(r) for r in self._recent]

    async def search_library(self, q, *a, **k):
        self.searched.append(q)
        return None if self._found is None else [dict(r) for r in self._found]


FLEETWOOD = {"subsonic_id": "fm1", "title": "Dreams", "artist": "Fleetwood Mac"}
CRANBERRIES = {"subsonic_id": "cr1", "title": "Dreams",
               "artist": "The Cranberries"}


class TestACallerCanNameTheRecordToBan(unittest.TestCase):
    """Review, 2026-11-15. The ban took no argument, so it could only ever
    land on the record on air — "never play Africa again" or "ban that one
    you played earlier", said a track later, banned whatever happened to be
    playing: the wrong record, permanently. The lift beside it takes any id
    and the station's blocklist takes any track; the restriction was ours.

    A name is resolved against the recent plays and the library, and the one
    rule it adds is the reason this class exists: a ban is the only thing on
    a call line with no expiry, so it lands only when exactly ONE record
    answers. Two is a question for the caller, never a pick."""

    ON = {"allow_never_play": True}

    def _ban(self, station, actions=None, **named):
        tools = _build(self.ON, station, actions)
        return asyncio.run(tools["subwave_never_play_track"](**named))

    def test_a_named_record_is_banned_not_the_one_on_air(self):
        station = _Library(found=[FLEETWOOD])
        actions = CallActions(5)
        out = self._ban(station, actions, title="Dreams")
        self.assertEqual([a for a in station.asked if a[0] == "block"],
                         [("block", "fm1")])
        self.assertNotIn(("block", "s1"), station.asked)
        # The receipt is checkable by ear, and carries the way back.
        self.assertIn("Dreams", out)
        self.assertIn("Fleetwood Mac", out)
        self.assertIn("found by NAME", out)
        self.assertIn("subwave_allow_track_again with id fm1", out)
        self.assertIn("permanent", out.lower())
        self.assertEqual(actions.count, 1)

    def test_the_one_you_played_earlier_comes_off_the_play_log(self):
        # The play log keys its rows `trackId`; an artist alone is enough
        # THERE, and never searches the whole library.
        station = _Library(recent=[
            {"trackId": "to1", "title": "Africa", "artist": "Toto"},
            {"trackId": "ab1", "title": "Waterloo", "artist": "ABBA"}])
        out = self._ban(station, artist="Toto")
        self.assertIn(("block", "to1"), station.asked)
        self.assertEqual(station.searched, [])
        self.assertIn("Africa", out)

    def test_two_answers_is_a_question_never_a_pick(self):
        station = _Library(found=[FLEETWOOD, CRANBERRIES])
        actions = CallActions(5)
        out = self._ban(station, actions, title="Dreams")
        self.assertEqual([a for a in station.asked if a[0] == "block"], [])
        self.assertIn("NOTHING was banned", out)
        self.assertIn("MORE THAN ONE", out)
        self.assertIn("Fleetwood Mac", out)
        self.assertIn("The Cranberries", out)
        # A question costs the caller nothing and cards nothing.
        self.assertEqual(actions.count, 0)

    def test_the_artist_settles_it(self):
        station = _Library(found=[FLEETWOOD, CRANBERRIES])
        self._ban(station, title="Dreams", artist="Cranberries")
        self.assertEqual([a for a in station.asked if a[0] == "block"],
                         [("block", "cr1")])

    def test_a_near_miss_is_never_banned_in_its_place(self):
        # The library's ONE hit for "Yesterday" — and a different song.
        station = _Library(found=[{"subsonic_id": "c1",
                                   "title": "Yesterday Once More",
                                   "artist": "Carpenters"}])
        out = self._ban(station, title="Yesterday")
        self.assertEqual([a for a in station.asked if a[0] == "block"], [])
        self.assertIn("NOTHING was banned", out)
        self.assertIn("exact title", out)

    def test_a_remaster_is_still_the_record_they_named(self):
        station = _Library(found=[{"subsonic_id": "fm2",
                                   "title": "Dreams (2004 Remaster)",
                                   "artist": "Fleetwood Mac"}])
        self._ban(station, title="dreams")
        self.assertIn(("block", "fm2"), station.asked)

    def test_one_record_seen_twice_is_one_record(self):
        # In the play log three times and in the library once: one id.
        played = {"trackId": "fm1", "title": "Dreams",
                  "artist": "Fleetwood Mac"}
        station = _Library(recent=[played, played, played], found=[FLEETWOOD])
        self._ban(station, title="Dreams")
        self.assertEqual([a for a in station.asked if a[0] == "block"],
                         [("block", "fm1")])

    def test_a_failed_library_read_is_not_no_such_record(self):
        station = _Library(found=None)
        out = self._ban(station, title="Dreams")
        self.assertEqual([a for a in station.asked if a[0] == "block"], [])
        self.assertIn("read FAILED", out)
        self.assertIn("do not say the station hasn't got it", out)

    def test_an_artist_alone_never_bans_from_the_whole_library(self):
        station = _Library(found=[FLEETWOOD])
        out = self._ban(station, artist="Fleetwood Mac")
        self.assertEqual(station.asked, [])
        self.assertEqual(station.searched, [])
        self.assertIn("Ask which song they mean", out)

    def test_no_name_is_still_the_record_on_air_and_looks_nothing_up(self):
        station = _Library(recent=[{"trackId": "to1", "title": "Africa",
                                    "artist": "Toto"}], found=[FLEETWOOD])
        out = self._ban(station)
        self.assertIn(("block", "s1"), station.asked)
        self.assertEqual(station.searched, [])
        self.assertIn("does NOT stop the copy playing", out)
        self.assertNotIn("found by NAME", out)

    def test_a_named_ban_still_rides_the_switch_and_the_budget(self):
        self.assertNotIn("subwave_never_play_track",
                         _build({"allow_favorite": True}, _Library()))
        spent = CallActions(1)
        spent.note("like", "X")
        station = _Library(found=[FLEETWOOD])
        self._ban(station, spent, title="Dreams")
        # At the limit nothing is even looked up, let alone banned.
        self.assertEqual(station.asked, [])
        self.assertEqual(station.searched, [])


class TestLiftingABanTakesTheRecordNotItsName(unittest.TestCase):
    """Drill on the deployed 1.0.1, 2026-11-16. The caller banned the record
    on air and then relented; the model had been given no id, so it called the
    lift with the TITLE as the id. The harness's fake station said Done, and a
    real one would have had no such track: the ban stands while the DJ says it
    is lifted, on the one caller action with no expiry."""

    ON = {"allow_never_play": True}

    def _tools(self, station):
        return _build(self.ON, station)

    def test_the_on_air_bans_receipt_carries_the_way_back(self):
        out = asyncio.run(self._tools(_Station())["subwave_never_play_track"]())
        self.assertIn("subwave_allow_track_again with id s1", out)

    def test_a_title_for_a_record_banned_on_this_call_finds_its_id(self):
        station = _Library(found=[FLEETWOOD])
        tools = self._tools(station)
        asyncio.run(tools["subwave_never_play_track"](title="Dreams"))
        out = asyncio.run(tools["subwave_allow_track_again"](id="Dreams"))
        self.assertIn(("unblock", "fm1"), station.asked)
        self.assertNotIn(("unblock", "Dreams"), station.asked)
        self.assertIn("off the never-play list", out)

    def test_the_title_of_the_record_on_air_finds_its_id(self):
        # The exact shape from the drill: ban what is playing, then hand the
        # lift its title.
        station = _Station()
        tools = self._tools(station)
        asyncio.run(tools["subwave_never_play_track"]())
        asyncio.run(tools["subwave_allow_track_again"](id="X"))
        self.assertIn(("unblock", "s1"), station.asked)

    def test_words_that_match_nothing_are_refused_not_sent(self):
        station = _Station()
        out = asyncio.run(self._tools(station)["subwave_allow_track_again"](
            id="My Head & My Heart (Jonas Blue remix)"))
        self.assertEqual([a for a in station.asked if a[0] == "unblock"], [])
        self.assertIn("NOTHING was lifted", out)
        self.assertIn("TITLE, not a track id", out)

    def test_a_real_id_still_passes_straight_through(self):
        station = _Station()
        asyncio.run(self._tools(station)["subwave_allow_track_again"](
            id="3OwmOAWVrnDKqvvGXai8ya"))
        self.assertIn(("unblock", "3OwmOAWVrnDKqvvGXai8ya"), station.asked)

    def test_a_short_title_is_not_found_inside_another_word(self):
        # The on-air record is called "X". "Xylophone Song" is not it.
        station = _Station()
        out = asyncio.run(self._tools(station)["subwave_allow_track_again"](
            id="Xylophone Song"))
        self.assertEqual([a for a in station.asked if a[0] == "unblock"], [])
        self.assertIn("NOTHING was lifted", out)
