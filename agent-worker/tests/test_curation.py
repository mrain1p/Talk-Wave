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
