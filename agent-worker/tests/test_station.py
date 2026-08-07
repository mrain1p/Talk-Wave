"""What the station tells us, and what the call card says about it.

Split out of test_sidecar.py; see tests/__init__.py.
"""

from __future__ import annotations

import asyncio
import unittest
import brain
from brain import briefing, conduct


class TestStationConfig(unittest.TestCase):
    def test_extracts_nested_persona_voice_shape(self):
        # SUB/WAVE publishes voices at values.personas[].tts.voice — nested
        # one level below the persona object. The extractor missing that
        # made mirroring silently return nothing.
        import station_config

        payload = {
            "values": {
                "personas": [
                    {"id": "p_abc123", "name": "A", "tts": {"voice": "-VoiceA"}},
                    {"id": "p_def456", "name": "B", "voice": "-VoiceB"},
                    {"id": "p_empty0", "name": "C", "tts": {"voice": ""}},
                ]
            },
            # Factory defaults must NEVER shadow the operator's real config —
            # this exact shape once made Brock mirror bm_daniel.
            "defaults": {
                "personas": [
                    {"id": "p_abc123", "name": "A", "tts": {"voice": "bm_daniel"}},
                ]
            },
        }
        m = station_config._extract_persona_voices(payload)
        self.assertEqual(m.get("p_abc123"), "-VoiceA")
        self.assertEqual(m.get("p_def456"), "-VoiceB")
        self.assertNotIn("p_empty0", m)


class TestTuneIn(unittest.TestCase):
    """Where the caller's browser pulls the broadcast from.

    This was silently broken on every TLS deployment: the URL was derived from
    the station's LAN address over plain http, and a browser refuses to load
    that into an https page. Nothing reported it — the widget logged to the
    console and the call ran with no station behind it.
    """

    def setUp(self):
        import tune_in
        self.tune_in = tune_in
        tune_in._cache.clear()

    def test_a_full_mount_is_used_as_given(self):
        import asyncio

        url, alts = asyncio.run(self.tune_in.resolve(
            {"tune_in_url": "https://live.example.com/stream.mp3"},
            "http://192.168.1.245:7700/api"))
        self.assertEqual(url, "https://live.example.com/stream.mp3")
        self.assertEqual(alts, [])

    def test_blank_falls_back_to_the_derived_lan_url(self):
        import asyncio

        url, alts = asyncio.run(self.tune_in.resolve(
            {}, "http://192.168.1.245:7700/api"))
        self.assertEqual(url, "http://192.168.1.245:7700/stream.mp3")
        self.assertEqual(alts, [])

    def test_a_bare_origin_discovers_the_published_mounts(self):
        # SubWave publishes its mount list at /listen.pls — "the always-served
        # MP3 mount first, appending any enabled optional mounts" — so an
        # operator shouldn't have to know whether opus is switched on.
        import asyncio

        self.tune_in._cache["https://live.example.com"] = (
            9e18,   # never expires during the test
            ["https://live.example.com/stream.mp3",
             "https://live.example.com/stream.opus"],
        )
        url, alts = asyncio.run(self.tune_in.resolve(
            {"tune_in_url": "https://live.example.com"}, "http://x/api"))
        self.assertEqual(url, "https://live.example.com/stream.mp3")
        self.assertEqual(alts, ["https://live.example.com/stream.opus"])

    def test_discovery_keeps_the_path_and_throws_away_the_host(self):
        # The one that actually bit. A station generates its playlist from its
        # own configured address, which is routinely internal — asked over a
        # public https origin, the real deployment answered with
        # http://192.168.1.245:7700/stream.mp3. Taking that whole would hand
        # the browser the exact unreachable LAN address this setting exists to
        # escape, so discovery would be worse than none at all.
        out = self.tune_in._parse_playlist(
            "#EXTM3U\n#EXTINF:-1,Yosemite FM\n"
            "http://192.168.1.245:7700/stream.mp3\n")
        self.assertEqual(out, ["/stream.mp3"])
        self.assertNotIn("192.168", "".join(out))

    def test_mp3_is_ordered_first_whatever_the_station_lists(self):
        # Every browser plays mp3; Safari is unreliable on opus. The widget
        # tries these in order, so the order is the whole point.
        out = self.tune_in._parse_playlist(
            "[playlist]\n"
            "File1=https://live.example.com/stream.opus\n"
            "File2=https://live.example.com/stream.mp3\n"
        )
        self.assertEqual(out[0], "/stream.mp3")

    def test_a_mount_is_told_apart_from_an_origin(self):
        self.assertTrue(self.tune_in.is_a_mount("https://a.example.com/stream.mp3"))
        self.assertTrue(self.tune_in.is_a_mount("https://a.example.com/x/live.opus"))
        self.assertFalse(self.tune_in.is_a_mount("https://a.example.com"))
        self.assertFalse(self.tune_in.is_a_mount("https://a.example.com/"))
        self.assertFalse(self.tune_in.is_a_mount(""))


class TestABadPlaylistStaysSmall(unittest.TestCase):
    """Mount discovery reads whatever the station's playlist says, and every
    mount is copied into /live — which every open widget polls. A station
    answering with hundreds of URLs would otherwise become a payload this
    service repeats to everybody."""

    def test_a_flood_of_mounts_is_capped(self):
        import tune_in

        flood = "\n".join(f"http://s/mount{i}.mp3" for i in range(500))
        got = tune_in._parse_playlist(flood)
        self.assertLessEqual(len(got), tune_in._MAX_MOUNTS)
        self.assertTrue(got, "capping must not throw the playlist away")

    def test_an_absurdly_long_path_is_dropped(self):
        import tune_in

        got = tune_in._parse_playlist("http://s/" + "a" * 5000 + ".mp3")
        self.assertEqual(got, [])

    def test_a_normal_playlist_is_untouched(self):
        import tune_in

        got = tune_in._parse_playlist(
            "#EXTM3U\nhttp://192.168.1.245:7700/stream.mp3\n"
            "http://192.168.1.245:7700/stream.opus\n")
        self.assertEqual(got, ["/stream.mp3", "/stream.opus"])


class TestTheLiveShowRecordSurvivesTheScheduleLookup(unittest.TestCase):
    """`active_show` reads the show twice and merges the two answers.

    Measured against a live station before this was fixed: the schedule lookup
    REPLACED the running record, trading fifteen fields for three. The losses
    were `episodeAngle`, `guests` and the genre/mood/energy filters — all of
    which only exist on programme shows, so the lookup did the most damage
    exactly where the show had the most to say about itself.
    """

    def _client(self, schedule_shows):
        import station as station_mod

        client = station_mod.StationClient.__new__(station_mod.StationClient)

        async def schedule():
            return {"shows": schedule_shows}

        client.schedule = schedule
        return client

    LIVE = {
        "now_playing": {"context": {"activeShow": {
            "id": "s_pub", "name": "DONOVAN'S PUB", "topic": "Irish folk and trad.",
            "episodeAngle": "A relaxed morning session.",
            "guests": [{"id": "p_seamus", "name": "Seamus"}],
            "genres": ["folk", "trad"], "energies": ["low"],
            "filtersStrict": True, "themeId": "donovan-s-pub",
        }}},
    }
    CONFIGURED = [{
        "id": "s_pub", "name": "DONOVAN'S PUB", "topic": "Irish folk and trad.",
        "personaId": "p_danny", "guestPersonaIds": [], "mood": "warm",
    }]

    def test_the_running_record_is_not_thrown_away(self):
        import asyncio

        show = asyncio.run(
            self._client(self.CONFIGURED).active_show(self.LIVE["now_playing"]))

        self.assertEqual(show["episodeAngle"], "A relaxed morning session.")
        self.assertEqual([g["name"] for g in show["guests"]], ["Seamus"])
        self.assertEqual(show["genres"], ["folk", "trad"])
        self.assertTrue(show["filtersStrict"])

    def test_the_schedule_still_contributes_what_only_it_knows(self):
        import asyncio

        show = asyncio.run(
            self._client(self.CONFIGURED).active_show(self.LIVE["now_playing"]))

        self.assertEqual(show["personaId"], "p_danny")
        self.assertEqual(show["mood"], "warm")

    def test_an_empty_scheduled_field_does_not_erase_a_live_one(self):
        # The scheduled record carries empty lists for filters it doesn't
        # override. Merging them blind would blank the live show's own.
        import asyncio

        configured = [dict(self.CONFIGURED[0], genres=[], topic="")]
        show = asyncio.run(
            self._client(configured).active_show(self.LIVE["now_playing"]))

        self.assertEqual(show["genres"], ["folk", "trad"])
        self.assertEqual(show["topic"], "Irish folk and trad.")

    def test_an_unmatched_show_still_returns_the_live_record(self):
        import asyncio

        show = asyncio.run(
            self._client([{"id": "s_other"}]).active_show(self.LIVE["now_playing"]))
        self.assertEqual(show["episodeAngle"], "A relaxed morning session.")


class TestTheDJKnowsWhoIsListening(unittest.TestCase):
    """The listener count reaches the prompt whatever shape it arrives in.

    This read insisted on a bare int at `listeners`. A real station answers
    with `{"current": 0, "peak": 3}` there and `{"count": 0}` under `context`,
    so the line never once reached a prompt — and "is anyone even listening?"
    is one of the commonest things a caller asks.
    """

    def _facts(self, np: dict) -> str:
        from brain.briefing import _fmt_now_playing

        return _fmt_now_playing(dict(np, nowPlaying={"title": "Stand"}))

    def test_the_shape_a_live_station_actually_sends(self):
        self.assertIn("3 listeners tuned in",
                      self._facts({"listeners": {"current": 3, "peak": 9}}))

    def test_the_count_under_context(self):
        self.assertIn("2 listeners tuned in",
                      self._facts({"context": {"listeners": {"count": 2}}}))

    def test_a_bare_int_still_works(self):
        self.assertIn("1 listener tuned in", self._facts({"listeners": 1}))

    def test_an_empty_station_says_so(self):
        self.assertIn("Nobody else is tuned in",
                      self._facts({"listeners": {"current": 0}}))

    def test_a_station_that_does_not_say_is_not_guessed_at(self):
        # No claim either way — "nobody is listening" is a real thing for a DJ
        # to say out loud, and it must not be invented from a missing field.
        out = self._facts({})
        self.assertNotIn("tuned in", out)
        self.assertNotIn("Nobody else", out)


class TestTheDJKnowsWhoIsInTheBoothAndWhatTheShowPlays(unittest.TestCase):
    """Both come off the live show record and neither was read.

    A DJ hosting alongside a guest persona had no idea they were there, and
    the DJ could promise a caller a record the show's own filters would refuse.
    """

    def test_guests_are_named(self):
        from brain.briefing import _fmt_guests

        self.assertEqual(
            _fmt_guests({"guests": [{"name": "Seamus"}, {"name": "Maeve"}]}),
            "In the booth with you: Seamus and Maeve.")

    def test_a_solo_show_says_nothing(self):
        from brain.briefing import _fmt_guests

        self.assertEqual(_fmt_guests({"guests": []}), "")
        self.assertEqual(_fmt_guests({}), "")

    def test_the_show_shape_reaches_the_prompt(self):
        from brain.briefing import _fmt_show_shape

        out = _fmt_show_shape({"genres": ["folk"], "energies": ["low"],
                               "filtersStrict": True})
        self.assertIn("folk", out)
        self.assertIn("low energy", out)
        self.assertIn("strictly", out)

    def test_an_unfiltered_show_says_nothing(self):
        from brain.briefing import _fmt_show_shape

        self.assertEqual(_fmt_show_shape({"genres": [], "moods": []}), "")


class TestTheHoldMatchesHowLongTheStationWillTalk(unittest.TestCase):
    """A fixed hold was the wrong shape. An announcement is a sentence and a
    segment can run a minute or more, so one number either reopens the gate
    mid-delivery — the DJ talking over its own voice on the broadcast — or
    gags it long after the air is clear."""

    def _tools(self, station, cfg=None, limit=5):
        from call.actions import CallActions
        from call.tools import build_on_air_tools

        class _Guard:
            def __init__(self):
                self.holds = []

            def mark_on_air(self, secs=25.0):
                self.holds.append(secs)

            async def wait_until_clear(self, timeout=None):
                return 0.0

        guard = _Guard()
        actions = CallActions(limit)
        built = build_on_air_tools(
            cfg or {"allow_announcements": True}, station, actions, guard)
        return {t.info.name: t for t in built}, guard, actions

    def _station(self, **result):
        class _Station:
            def __init__(self):
                self.calls = []

            async def dj_say(self, message, mode="styled", kind=None):
                self.calls.append(message)
                return dict(result)

        return _Station()

    def test_a_long_segment_is_held_longer_than_a_short_line(self):
        import asyncio

        long_tools, long_guard, _ = self._tools(
            self._station(ok=True, spoken=" ".join(["word"] * 120)))
        asyncio.run(long_tools["subwave_dj_announce"]("go on air"))

        short_tools, short_guard, _ = self._tools(
            self._station(ok=True, spoken="Quick shout to Dave."))
        asyncio.run(short_tools["subwave_dj_announce"]("go on air"))

        self.assertGreater(long_guard.holds[0], short_guard.holds[0])
        self.assertLessEqual(long_guard.holds[0], 180)
        self.assertGreaterEqual(short_guard.holds[0], 12)

    def test_a_refused_announcement_is_never_reported_as_done(self):
        import asyncio

        tools, guard, actions = self._tools(
            self._station(ok=False, error="the station refused it"))
        out = asyncio.run(tools["subwave_dj_announce"]("hello"))
        self.assertIn("do not claim it worked", out.lower())
        self.assertEqual(actions.count, 0, "a failed action was counted")
        self.assertEqual(guard.holds, [], "the gate closed for speech that never happened")

    def test_a_slow_confirmation_is_not_a_failure(self):
        # A read timeout means the station took the action and hadn't finished
        # answering. Reporting that as failure told callers their message
        # hadn't gone out while it was audibly going out.
        import asyncio

        tools, _, actions = self._tools(
            self._station(ok=True, unconfirmed=True, spoken="On air now."))
        out = asyncio.run(tools["subwave_dj_announce"]("hello"))
        self.assertIn("gone through", out.lower())
        self.assertEqual(actions.count, 1)


class TestTheCardCacheHasOneHome(unittest.TestCase):
    """Five modules stale the /live answer and one builds it. They must all be
    holding the same dict — a second copy would mean a settings save, a new
    ring tone or a password change clears a cache nobody reads, and the card
    keeps insisting otherwise for up to half a minute."""

    def test_every_module_that_stales_the_card_shares_the_dict(self):
        from api import auth as api_auth
        from api import hooks as api_hooks
        from api import live as api_live
        from api import live_cache
        from api import settings as api_settings
        from api import sounds as api_sounds

        for mod in (api_auth, api_hooks, api_live, api_settings, api_sounds):
            self.assertIs(
                mod._live_cache, live_cache._live_cache,
                f"{mod.__name__} busts a cache of its own")

    def test_a_webhook_cannot_force_more_work_than_the_ttl_would(self):
        from api import live_cache

        self.assertLess(live_cache._LIVE_BUST_FLOOR, live_cache._LIVE_TTL)


class TestAFailedReadSaysWhyItFailed(unittest.TestCase):
    """`str(httpx.ReadTimeout())` is the empty string, and httpx raises its
    timeouts bare — so every station read failure on the operator's deployment
    logged as

        station read /state failed:

    and stopped. The fact was recorded, the reason was not, and a timeout was
    indistinguishable from a refused connection and from a bug in the logging.
    """

    def test_an_exception_with_no_message_still_names_itself(self):
        import httpx

        from log_setup import describe

        self.assertEqual(describe(httpx.ReadTimeout("")), "ReadTimeout")
        self.assertEqual(describe(httpx.ConnectTimeout("")), "ConnectTimeout")

    def test_an_exception_with_a_message_keeps_it(self):
        from log_setup import describe

        self.assertEqual(describe(ValueError("bad url")), "ValueError: bad url")

    def test_the_station_client_logs_through_it(self):
        import logging

        import httpx

        import station as station_mod

        class _Boom:
            async def get(self, path):
                raise httpx.ReadTimeout("")

        client = station_mod.StationClient.__new__(station_mod.StationClient)
        client._client = _Boom()
        with self.assertLogs("callin.station", level=logging.WARNING) as caught:
            self.assertEqual(asyncio.run(client._get("/state")), {})
        self.assertIn("ReadTimeout", "\n".join(caught.output))
