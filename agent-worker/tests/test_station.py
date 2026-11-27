"""What the station tells us, and what the call card says about it.

Split out of test_sidecar.py; see tests/__init__.py.
"""

from __future__ import annotations

import asyncio
import json
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

    def test_reads_persona_skills_from_the_nested_shape(self):
        # Same nesting the voice reader above had to learn: a real SUB/WAVE
        # station keeps its roster at values.personas[], not the top level.
        # The skills reader used to look only at the top level, so a nested
        # station read as "every DJ runs everything" — the assigned segment
        # list was silently dropped, unrestricting a persona the operator had
        # restricted. On-air path, hit every call.
        import station_config

        nested = {
            "values": {
                "personas": [
                    {"id": "p_abc123", "name": "A", "skills": ["news", "weather"]},
                    {"id": "p_def456", "name": "B"},  # no skills key -> "all"
                ]
            },
            # Factory seeds must never answer: they'd narrow a DJ the operator
            # never narrowed.
            "defaults": {
                "personas": [
                    {"id": "p_abc123", "name": "A", "skills": ["nothing"]},
                ]
            },
        }
        self.assertEqual(
            station_config._persona_skills_from(nested, "p_abc123"),
            ["news", "weather"])
        # Absent skills key still means "all", not "none".
        self.assertIsNone(
            station_config._persona_skills_from(nested, "p_def456"))
        # An unknown persona is "all", and defaults never restricts anyone.
        self.assertIsNone(
            station_config._persona_skills_from(nested, "p_missing"))

    def test_persona_skills_top_level_shape_still_reads(self):
        # The simpler top-level shape other readers/snapshots use must keep
        # working unchanged, and keep precedence over any nested copy.
        import station_config

        payload = {
            "personas": [
                {"id": "p_abc123", "name": "A", "skills": ["news"]},
            ],
            "values": {
                "personas": [
                    {"id": "p_abc123", "name": "A", "skills": ["weather"]},
                ]
            },
        }
        # Top level wins the tie, so behaviour on a top-level payload is exactly
        # what it was before nesting-awareness was added.
        self.assertEqual(
            station_config._persona_skills_from(payload, "p_abc123"), ["news"])


class TestTheStationModelIsTheDJModelNotTheEmbedder(unittest.TestCase):
    """The station's settings payload has no documented shape, so station_config
    finds the DJ's model by a depth-first search for a `model`-ish key. The trap
    (station_config._SKIP_SUBTREES): the embedding, search and tagger configs
    ALSO carry a `model` key, and a blind DFS was seen reporting the embedding
    model as the DJ model — which the sidecar then defaults its own DJ to. The
    skip existed but nothing pinned that it works, so a reshuffle could silently
    start returning the wrong model. This is that pin (Batch 1, 2026-08-29)."""

    def test_a_sibling_embedding_model_does_not_win(self):
        import station_config
        payload = {
            "embedding": {"model": "text-embedding-3-small"},
            "tagger": {"model": "gpt-tagger-mini"},
            "dj": {"llmModel": "claude-opus-4-8"},
        }
        self.assertEqual(
            station_config._find_first(payload, station_config._MODEL_KEYS),
            "claude-opus-4-8",
            "the DFS returned a non-DJ model — a skip-subtree stopped skipping")

    def test_a_top_level_dj_model_still_resolves(self):
        import station_config
        payload = {"llmModel": "claude-opus-4-8", "embedding": {"model": "emb"}}
        self.assertEqual(
            station_config._find_first(payload, station_config._MODEL_KEYS),
            "claude-opus-4-8")


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
            "http://192.168.1.10:7700/api"))
        self.assertEqual(url, "https://live.example.com/stream.mp3")
        self.assertEqual(alts, [])

    def test_blank_falls_back_to_the_derived_lan_url(self):
        import asyncio

        url, alts = asyncio.run(self.tune_in.resolve(
            {}, "http://192.168.1.10:7700/api"))
        self.assertEqual(url, "http://192.168.1.10:7700/stream.mp3")
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
        # http://192.168.1.10:7700/stream.mp3. Taking that whole would hand
        # the browser the exact unreachable LAN address this setting exists to
        # escape, so discovery would be worse than none at all.
        out = self.tune_in._parse_playlist(
            "#EXTM3U\n#EXTINF:-1,Yosemite FM\n"
            "http://192.168.1.10:7700/stream.mp3\n")
        self.assertEqual(out, ["/stream.mp3"])
        self.assertNotIn("192.168", "".join(out))

    def test_a_bare_origin_in_the_playlist_is_not_turned_into_a_path(self):
        # Same answer, one line further down it. A station that lists its own
        # ORIGIN — no path — used to come back as the mount
        # "/192.168.1.10:7700", which the widget would then have asked the
        # operator's own server for. The host is thrown away; there is no path
        # left, so there is no mount to offer.
        out = self.tune_in._parse_playlist(
            "#EXTM3U\nhttp://192.168.1.10:7700\n"
            "http://192.168.1.10:7700/stream.mp3\n")
        self.assertEqual(out, ["/stream.mp3"])

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
            "#EXTM3U\nhttp://192.168.1.10:7700/stream.mp3\n"
            "http://192.168.1.10:7700/stream.opus\n")
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


    # -- where they are: the station's /audience rollup, behind its own switch
    # (approved 2026-09-28: countries only, a floor of three, off by default).
    def test_where_they_listen_from_is_off_unless_switched_on(self):
        import settings as settings_store

        self.assertEqual("off", settings_store.FIELDS["allow_listener_countries"][1])

    def test_only_countries_with_a_few_listeners_are_named(self):
        from brain.briefing import _fmt_audience

        line = _fmt_audience({"countries": [
            {"country": "US", "count": 14}, {"country": "gb", "count": 5},
            {"country": "DE", "count": 3}, {"country": "FR", "count": 2},
            {"country": "IS", "count": 1}]})
        self.assertIn("US 14, GB 5, DE 3.", line)
        self.assertNotIn("FR", line)
        self.assertNotIn("IS 1", line)

    def test_no_country_over_the_floor_is_no_line_at_all(self):
        from brain.briefing import _fmt_audience

        for aud in ({"countries": [{"country": "FR", "count": 2}]}, {},
                    {"countries": "junk"}, None):
            with self.subTest(aud=aud):
                self.assertEqual("", _fmt_audience(aud))

    def test_six_at_most_and_nothing_that_is_not_a_country_code(self):
        from brain.briefing import _fmt_audience

        rows = [{"country": "<script>", "count": 99}, {"country": "US", "count": True}]
        rows += [{"country": c, "count": 9} for c in ("US", "GB", "DE", "FR", "JP", "BR", "CA")]
        line = _fmt_audience({"countries": rows})
        self.assertNotIn("script", line)
        self.assertEqual(6, line.count(" 9"))
        self.assertNotIn("CA", line)

    def test_the_line_reaches_only_the_callers_the_switch_reaches(self):
        import asyncio

        from brain.briefing import station_context

        snap = {"now_playing": {}, "state": {}, "session": {}, "schedule": {},
                "skills": [], "audience": {"countries": [{"country": "US", "count": 4}]}}
        on = asyncio.run(station_context(None, {"allow_listener_countries": True}, snap, {}))
        off = asyncio.run(station_context(None, {"allow_listener_countries": False}, snap, {}))
        self.assertIn("US 4", on)
        self.assertNotIn("US 4", off)

    def _station_read(self, method, status, body, **kw):
        import httpx
        from unittest import mock

        import station as station_mod

        asked = []

        def handler(request):
            asked.append(request.url)
            return httpx.Response(status, json=body)

        async def run():
            client = station_mod.StationClient(base_url="http://station")
            client._client = httpx.AsyncClient(
                base_url="http://station", transport=httpx.MockTransport(handler))
            try:
                with mock.patch("station_config.admin_credentials",
                                return_value=("admin", "pw")):
                    return await getattr(client, method)(**kw)
            finally:
                await client.aclose()

        return asyncio.run(run()), asked

    def test_the_connections_read_names_an_icecast_refusal(self):
        # 502 is the station failing to reach Icecast's admin — not "nobody
        # listening", which is a 200 with an empty list.
        got, _ = self._station_read("listener_connections", 502,
                                    {"error": "Icecast admin auth rejected"})
        self.assertEqual({"error": "Icecast admin auth rejected"}, got)
        got, _ = self._station_read("listener_connections", 200,
                                    {"count": 0, "connections": []})
        self.assertEqual(0, got["count"])

    def test_the_untagged_walk_pages_by_the_stations_cursor(self):
        got, asked = self._station_read("untagged_tracks", 200,
                                        {"rows": [], "nextCursor": None},
                                        cursor="MTA6Mw")
        self.assertEqual("/library/untagged", asked[0].path)
        self.assertEqual("MTA6Mw", asked[0].params.get("cursor"))
        self.assertEqual("50", asked[0].params.get("limit"))
        self.assertIsNone(got["nextCursor"])

    def test_the_rollup_is_read_only_when_the_switch_is_on(self):
        # It rides the snapshot's own gather, so it never lengthens the
        # ringing; switched off it is not asked for at all.
        import asyncio
        from unittest import mock

        import settings as settings_store
        import station as station_mod

        async def empty(*a, **k):
            return {}

        async def none(*a, **k):
            return []

        asked = []

        async def audience(self, since_minutes=1440):
            asked.append(since_minutes)
            return {"countries": [{"country": "US", "count": 4}]}

        fakes = {n: empty for n in ("live_dj", "now_playing", "state", "session", "schedule")}
        try:
            for value, expect in (("off", []), ("admin", [1440])):
                asked.clear()
                settings_store.save({"allow_listener_countries": value})
                with mock.patch.multiple(station_mod.StationClient, personas=none,
                                         audience=audience, **fakes):
                    client = station_mod.StationClient.__new__(station_mod.StationClient)
                    snap = asyncio.run(client.snapshot())
                with self.subTest(value=value):
                    self.assertEqual(expect, asked)
                    self.assertEqual(bool(expect), bool(snap["audience"]))
        finally:
            settings_store.save({"allow_listener_countries": ""})

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


class TestNoIdEscapesItsPathSegment(unittest.TestCase):
    """The security sitting, 2026-08-28. Every id these tools drop into a
    station URL arrives from the station via a tool result the MODEL relayed,
    or from a caller's words — never ours to trust as a path. Three DELETE/GET
    builders interpolated it raw while two siblings quoted; a crafted
    "../../schedule/override" id re-targeted the request at another station
    endpoint under the admin credentials, reaching routes whose tools the
    operator had disabled. _seg quotes every one now."""

    def _capture(self, method_name, coro_factory):
        from station import StationClient

        seen = {}

        class _FakeResp:
            status_code = 200
            content = b"{}"
            headers = {"content-type": "application/json"}

            def raise_for_status(self):
                pass

            def json(self):
                return {"ok": True}

        class _FakeClient:
            async def request(self, method, url, **kw):
                seen["path"] = url
                return _FakeResp()

            async def get(self, url, **kw):
                seen["path"] = url
                return _FakeResp()

            async def delete(self, url, **kw):
                seen["path"] = url
                return _FakeResp()

            async def aclose(self):
                pass

        from unittest import mock

        async def _run():
            client = StationClient(base_url="http://station.invalid")
            client._client = _FakeClient()
            try:
                with mock.patch("station_config.admin_credentials",
                                return_value=("u", "p")):
                    await coro_factory(client)
            finally:
                await client.aclose()

        asyncio.run(_run())
        return seen.get("path", "")

    def test_a_traversal_id_is_percent_encoded_not_a_separator(self):
        import httpx

        import station

        # Every dangerous shape: the slash-carrying one the first fix covered,
        # AND the bare dot-segments the cloud review caught — quote() leaves
        # `.`/`..` untouched (dots are unreserved), so without the %2E encoding
        # httpx would still collapse `/dj/queue/..` to `/dj`.
        for evil in ("../../schedule/override", "..", ".", "...", "../foo"):
            seg = station._seg(evil)
            with self.subTest(id=evil):
                self.assertNotIn("/", seg)
                self.assertNotIn("..", seg, "a bare dot-segment survived")
                # Prove httpx cannot normalise it back into a new segment.
                c = httpx.Client(base_url="http://station.invalid")
                built = str(c.build_request(
                    "DELETE", f"/dj/queue/{seg}").url)
                c.close()
                self.assertTrue(built.endswith(f"/dj/queue/{seg}"),
                                f"{evil!r} re-targeted to {built}")

        # And end to end through a tool that was unquoted: the crafted id
        # cannot introduce a new path segment.
        for label, factory in (
            ("cancel_queued_track", lambda c: c.cancel_queued_track("..")),
        ):
            with self.subTest(tool=label):
                path = self._capture(label, factory)
                self.assertTrue(path.endswith("/dj/queue/%2E%2E"),
                                f"{label} let the id escape: {path}")


class TestTheStationLogSaysWhatWasSaid(unittest.TestCase):
    """The djLog records when an utterance STARTED and what was said — never
    when it ended. The guard sizes the end of its hold from the words, so the
    words have to survive the trip out of the log with their timestamp."""

    def _speech(self, entries):
        from station import StationClient

        async def _run():
            client = StationClient(base_url="http://station.invalid")
            try:
                return await client.on_air_speech(state={"djLog": entries})
            finally:
                await client.aclose()

        return asyncio.run(_run())

    def test_the_newest_utterance_comes_back_with_its_words(self):
        from datetime import datetime, timedelta, timezone

        def iso(when):
            return when.isoformat().replace("+00:00", "Z")

        now = datetime.now(timezone.utc)
        got = self._speech([
            {"kind": "scheduler", "message": "picked the next track", "t": iso(now)},
            {"kind": "link", "message": "That was the new one from the lab.",
             "t": iso(now - timedelta(seconds=10))},
            {"kind": "link", "message": "an older link",
             "t": iso(now - timedelta(seconds=120))},
        ])
        self.assertIsNotNone(got)
        since, words = got
        self.assertAlmostEqual(since, 10, delta=5)
        self.assertEqual(words, "That was the new one from the lab.")

    def test_a_log_with_no_speech_says_none(self):
        self.assertIsNone(self._speech(
            [{"kind": "scheduler", "message": "bookkeeping",
              "t": "2026-01-01T00:00:00Z"}]))

    def test_every_station_voice_kind_reads_as_speech(self):
        # Checked against the station's own queue/kinds.ts VOICE_KINDS on the
        # 2026-08-31 upstream pass: the guard listed "hourly", which no
        # release has ever logged (the station says "hourly-check"), and
        # missed "banter" and "handoff" entirely — so a banter exchange or a
        # mic-pass sign-off just before pickup slipped past the same-persona
        # overlap check.
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for kind in ("dj-speak", "link", "station-id", "hourly-check",
                     "banter", "handoff"):
            with self.subTest(kind=kind):
                got = self._speech([{"kind": kind, "message": "words on air",
                                     "t": now}])
                self.assertIsNotNone(got)
                self.assertEqual(got[1], "words on air")


class TestTheSpeechReadFollowsTheLogOffThePublicState(unittest.TestCase):
    """SUB/WAVE 1.21 (#1858) moved the booth log from public GET /state to
    admin GET /debug/dj-log. Found on the 2026-11-09 upstream pass with the
    operator's station already on 1.29: for weeks every 4s poll had read the
    absent key as a quiet booth, and the guard that stops the same DJ talking
    on air and on a call at once was running on the voice.* webhooks alone.
    The read has to follow the log, and say when it cannot."""

    def _speech(self, handler, state, creds=("admin", "pw")):
        import httpx
        from unittest import mock

        import station as station_mod

        async def go():
            client = station_mod.StationClient(base_url="http://station")
            client._client = httpx.AsyncClient(
                base_url="http://station", transport=httpx.MockTransport(handler))
            try:
                with mock.patch("station_config.admin_credentials",
                                return_value=creds):
                    return await client.on_air_speech(state=state)
            finally:
                await client.aclose()

        return asyncio.run(go())

    @staticmethod
    def _now():
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def test_a_state_without_the_log_reads_the_admin_route(self):
        import httpx

        asked = []

        def handler(request):
            asked.append((request.url.path, request.headers.get("authorization", "")))
            return httpx.Response(200, json={
                "djLog": [{"kind": "link", "message": "Back after this.",
                           "t": self._now()}],
                "djLogCount": 1, "timezone": "UTC"})

        # The 1.29 /state: current track present, no djLog key at all.
        got = self._speech(handler, {"current": {"title": "Dreams"}, "upcoming": []})
        self.assertIsNotNone(got, "a 1.21+ station read as a quiet booth")
        self.assertEqual(got[1], "Back after this.")
        self.assertEqual([p for p, _ in asked], ["/debug/dj-log"])
        self.assertTrue(asked[0][1].startswith("Basic "),
                        "the admin route was asked without credentials")

    def test_an_older_station_that_still_publishes_the_log_is_not_asked_twice(self):
        def handler(request):
            raise AssertionError(f"needless admin read: {request.url.path}")

        got = self._speech(handler, {"djLog": [
            {"kind": "link", "message": "from the public read", "t": self._now()}]})
        self.assertEqual(got[1], "from the public read")

    def test_an_empty_log_is_a_quiet_booth_not_a_failure(self):
        import httpx

        got = self._speech(
            lambda req: httpx.Response(200, json={"djLog": [], "djLogCount": 0}),
            {"current": {}})
        self.assertIsNone(got)

    def test_without_credentials_the_read_is_blind_not_broken(self):
        def handler(request):
            raise AssertionError("asked the admin route with no credentials")

        # None, and no exception: a missing password is the operator's to
        # fix, not congestion, so it must not hold every call's gate.
        self.assertIsNone(self._speech(handler, {"current": {}}, creds=("", "")))

    def test_a_refused_read_is_config_not_congestion(self):
        import httpx

        for status in (401, 403, 404):
            with self.subTest(status=status):
                self.assertIsNone(self._speech(
                    lambda req, s=status: httpx.Response(s, json={"error": "no"}),
                    {"current": {}}))

    def test_a_station_too_slow_to_answer_raises_so_the_guard_holds(self):
        import httpx

        # _get swallows a failed read into {}, which is how a station outage
        # used to read as a quiet booth. The speech read raises instead, and
        # call/air.py's watch loop turns that into a short hold — a read that
        # cannot prove the air is clear must not open the gate.
        def handler(request):
            raise httpx.ReadTimeout("slow", request=request)

        with self.assertRaises(httpx.ReadTimeout):
            self._speech(handler, {"current": {}})

        with self.assertRaises(httpx.HTTPStatusError):
            self._speech(lambda req: httpx.Response(503, json={"error": "starting"}),
                         {"current": {}})


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
                self.spokens = []

            def mark_on_air(self, secs=25.0, spoken=""):
                self.holds.append(secs)
                self.spokens.append(spoken)

            def mark_pending_air(self, spoken=""):
                # The unconfirmed path: no countdown to record, only the fact
                # that the gate closed until the log shows the delivery.
                self.holds.append("pending")
                self.spokens.append(spoken)

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
        # No 12s floor any more (0.10.113). It meant a one-line shoutout
        # gagged the call for twelve seconds and the caller sat through most
        # of it in silence — "held working the booth way too long". A short
        # line is held for about as long as it takes to say, and the duck's
        # close is added once, by the guard, not baked in here.
        self.assertGreaterEqual(short_guard.holds[0], 2)
        self.assertLess(short_guard.holds[0], 12)

    def test_the_words_ride_along_for_the_comeback_line(self):
        # The guard remembers what went out so the DJ can nod at it when it
        # comes back to the caller, instead of returning as if the trip to
        # air never happened.
        import asyncio

        tools, guard, _ = self._tools(
            self._station(ok=True, spoken="Shout to Dave."))
        asyncio.run(tools["subwave_dj_announce"]("go on air"))
        self.assertEqual(guard.spokens, ["Shout to Dave."])

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

        tools, guard, actions = self._tools(
            self._station(ok=True, unconfirmed=True, spoken="On air now."))
        out = asyncio.run(tools["subwave_dj_announce"]("hello"))
        self.assertIn("gone through", out.lower())
        self.assertEqual(actions.count, 1)
        # And since 0.10.17 the hold is PENDING, not a countdown from the
        # tool's return — the delivery lands after that clock on a slow
        # station, which is the Ash overlap of 2026-08-09.
        self.assertEqual(guard.holds, ["pending"])


class TestARefusalNamesItsRule(unittest.TestCase):
    """SUB/WAVE 1.8's blocklist rules answer a refused request or queue with
    a body that names WHAT blocked the track. The old error path returned
    str(HTTPStatusError) — "Client error '409 …'" — which threw the body
    away, and the DJ fumbled a refusal it was never told the reason for."""

    class _Resp:
        def __init__(self, payload=None, text=""):
            self._payload = payload
            self.text = text

        def json(self):
            if self._payload is None:
                raise ValueError("not json")
            return self._payload

    def _words(self, payload=None, text=""):
        from station import StationClient

        return StationClient._refusal_words(self._Resp(payload, text))

    def test_the_named_rule_reaches_the_dj(self):
        said = self._words({
            "message": "request declined",
            "blockedBy": {"kind": "rule", "field": "genre",
                          "values": ["Death Metal"]},
        })
        self.assertIn("request declined", said)
        self.assertIn("rule", said)
        self.assertIn("genre: Death Metal", said)

    def test_a_plain_entry_block_reads_as_the_never_play_list(self):
        said = self._words({"blockedBy": {"kind": "entry", "label": "Last Christmas"}})
        self.assertIn("never-play list", said)
        self.assertIn("Last Christmas", said)

    def test_a_bare_message_survives_and_non_json_falls_to_text(self):
        self.assertEqual(self._words({"error": "requests are closed"}),
                         "requests are closed")
        self.assertEqual(self._words(text="  too many requests  "),
                         "too many requests")

    def test_it_is_bounded(self):
        said = self._words({"message": "x" * 5000})
        self.assertLessEqual(len(said), 240)


class TestTheCardOnlyClaimsOnAirWithARealDJ(unittest.TestCase):
    """The /live card used to read "on air" for any station that merely
    answered, because resolve_live_persona falls back to id "default" on every
    path — so `persona.get("id")` was always truthy and the widget's "cannot
    reach the station" branch was dead code. on_air now needs a REAL persona
    (top-down review, 2026-08-28)."""

    def _reach(self, health, persona, now):
        from api.live import _reachability
        return _reachability(health, persona, now)

    def test_a_default_persona_is_not_on_air(self):
        # A reachable box that nobody has configured: health answers, but the
        # persona is the sentinel. Reachable, yes; on air, no.
        reachable, on_air = self._reach({"ok": True}, {"id": "default"},
                                        {"nowPlaying": {"title": "Filler"}})
        self.assertTrue(reachable)
        self.assertFalse(on_air, "an unconfigured box read as on air")

    def test_a_real_persona_is_on_air(self):
        reachable, on_air = self._reach({"ok": True}, {"id": "midnight-jane"}, {})
        self.assertTrue(reachable)
        self.assertTrue(on_air)

    def test_a_real_persona_alone_makes_a_silent_station_reachable(self):
        # No health, nothing playing, but a real DJ resolved: still reachable.
        reachable, on_air = self._reach(None, {"id": "midnight-jane"}, {})
        self.assertTrue(reachable)
        self.assertTrue(on_air)

    def test_an_unreachable_station_is_neither(self):
        # The dead-code branch this fix revived: no health, nothing playing,
        # and only the fallback persona.
        reachable, on_air = self._reach(None, {"id": "default"}, {})
        self.assertFalse(reachable)
        self.assertFalse(on_air)


class TestTheCardCacheHasOneHome(unittest.TestCase):
    """Five modules stale the /live answer and one builds it. They must all be
    holding the same dict — a second copy would mean a settings save, a new
    ring tone or a password change clears a cache nobody reads, and the card
    keeps insisting otherwise for up to half a minute."""

    def test_every_module_that_stales_the_card_shares_the_dict(self):
        from api import auth as api_auth
        from api import hook_receiver as api_hook_receiver
        from api import live as api_live
        from api import live_cache
        from api import settings as api_settings
        from api import sounds as api_sounds

        # hook_receiver, not hooks, since the 0.10.89 split: the RECEIVER is
        # the side that busts the card cache when a push lands.
        for mod in (api_auth, api_hook_receiver, api_live, api_settings,
                    api_sounds):
            self.assertIs(
                mod._live_cache, live_cache._live_cache,
                f"{mod.__name__} busts a cache of its own")

    def test_a_webhook_cannot_force_more_work_than_the_ttl_would(self):
        from api import live_cache

        self.assertLess(live_cache._LIVE_BUST_FLOOR, live_cache._LIVE_TTL)

    def test_a_cache_miss_rebuilds_once_however_many_tabs_arrive(self):
        """The miss is single-flighted, or the cache's own arithmetic is wrong.

        Measured on the deployed box (2026-09-17): warm /live answers in
        1.4ms, cold in 374ms, because the miss fans out into four to six
        station reads run one after another. Nothing serialised those, so
        every tab polling during that 374ms window started its own full sweep
        and the station took the fan-out once per tab. The cache exists to
        stop exactly that multiplication.
        """
        import asyncio
        import time
        from unittest import mock

        from api import live as api_live
        from api import live_cache
        from tests.support import _FakeRequest

        builds = []

        async def _slow_build(request):
            builds.append(1)
            await asyncio.sleep(0.05)      # the 374ms sweep, in miniature
            live_cache._live_cache["data"] = {"card": "built"}
            live_cache._live_cache["at"] = time.time()
            return "the winner's answer"

        async def run():
            live_cache._live_cache["data"] = None
            live_cache._live_cache["at"] = 0.0
            with mock.patch.object(api_live, "_build_live", _slow_build),                     mock.patch.object(api_live, "_cors",
                                      lambda _req, res: res),                     mock.patch.object(api_live, "_for_this_caller",
                                      lambda _req, data: data),                     mock.patch.object(api_live, "web",
                                      mock.Mock(json_response=lambda d: d)):
                return await asyncio.gather(
                    *[api_live.handle_live(_FakeRequest()) for _ in range(8)])

        try:
            out = asyncio.run(run())
        finally:
            live_cache._live_cache["data"] = None
            live_cache._live_cache["at"] = 0.0

        self.assertEqual(len(builds), 1,
                         f"eight tabs missed together and the station was "
                         f"swept {len(builds)} times")
        # And every one of them was answered — a single-flight that leaves
        # the losers with nothing is worse than no single-flight.
        self.assertEqual(len(out), 8)
        self.assertEqual(out[1:], [{"card": "built"}] * 7)


class TestAPictureTheStationLacksIsNotAskedForAgain(unittest.TestCase):
    """/avatar/{id} and /cover/{id} are public, and every miss was a station
    request with an eight-second budget that nothing remembered — so one
    client asking for random ids relayed a flood onto the station for as long
    as it liked (review, 2026-11-15).

    A miss is now remembered briefly, and the table of them is bounded by
    REFUSING, not by forgetting: random ids are all different, so the only
    thing that slows them is stopping. Hits are exactly as they were.
    """

    class _Station:
        """httpx.AsyncClient, as much of it as the proxy uses."""

        def __init__(self, have=()):
            self.have = set(have)
            self.asked = []

        def __call__(self, **kw):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            self.asked.append(url)
            found = url.rsplit("/", 1)[-1] in self.have

            class _Resp:
                content = b"\x89PNG-bytes"
                headers = {"content-type": "image/webp; charset=binary"}

                def raise_for_status(self):
                    if not found:
                        raise RuntimeError("404 Not Found")

            return _Resp()

    def setUp(self):
        import types
        from unittest import mock

        from api import art

        self.art = art
        art._misses.clear()
        art._known.clear()
        art._said_full_at = 0.0
        self.addCleanup(art._misses.clear)
        self.addCleanup(art._known.clear)
        self.station = self._Station(have={"real-cover", "real-dj"})
        patcher = mock.patch.object(
            art, "httpx", types.SimpleNamespace(AsyncClient=self.station))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _cover(self, track_id):
        import types

        from aiohttp import web

        try:
            return asyncio.run(self.art.handle_cover(
                types.SimpleNamespace(match_info={"track_id": track_id})))
        except web.HTTPNotFound:
            return None

    def test_the_same_wrong_id_reaches_the_station_once(self):
        for _ in range(5):
            self.assertIsNone(self._cover("no-such-track"))
        self.assertEqual(len(self.station.asked), 1)

    def test_a_hit_is_served_exactly_as_it_was(self):
        for _ in range(2):
            resp = self._cover("real-cover")
            self.assertEqual(resp.body, b"\x89PNG-bytes")
            self.assertEqual(resp.content_type, "image/webp")
            self.assertEqual(resp.headers["Cache-Control"], "public, max-age=86400")
        # Not cached here: the browser holds a hit for a day, and the station
        # is the one that knows when art changes.
        self.assertEqual(len(self.station.asked), 2)

    def test_random_ids_stop_reaching_the_station(self):
        for i in range(self.art._MISS_MAX + 75):
            self.assertIsNone(self._cover(f"random-{i}"))
        self.assertEqual(
            len(self.station.asked), self.art._MISS_MAX,
            "every random id was still relayed to the station")
        self.assertLessEqual(len(self.art._misses), self.art._MISS_MAX)

    def test_real_art_is_still_served_while_somebody_guesses(self):
        self.assertIsNotNone(self._cover("real-cover"))
        for i in range(self.art._MISS_MAX + 10):
            self._cover(f"random-{i}")
        self.assertIsNotNone(
            self._cover("real-cover"),
            "a flood of wrong ids took the on-air cover down with it")

    def test_a_miss_is_asked_again_once_it_has_aged_out(self):
        self.assertIsNone(self._cover("late-arrival"))
        self.station.have.add("late-arrival")
        self.assertIsNone(self._cover("late-arrival"))
        for url in self.art._misses:
            self.art._misses[url] = 0.0
        self.assertIsNotNone(self._cover("late-arrival"))
        self.assertNotIn(
            next(iter(self.station.asked)), self.art._misses)

    def test_the_stop_eases_when_the_misses_age_out(self):
        for i in range(self.art._MISS_MAX + 5):
            self._cover(f"random-{i}")
        before = len(self.station.asked)
        for url in self.art._misses:
            self.art._misses[url] = 0.0
        self.assertIsNone(self._cover("one-more"))
        self.assertEqual(len(self.station.asked), before + 1)

    def test_the_avatar_route_shares_the_same_memory(self):
        import types

        from aiohttp import web

        def avatar(persona_id):
            try:
                return asyncio.run(self.art.handle_avatar(
                    types.SimpleNamespace(match_info={"persona_id": persona_id})))
            except web.HTTPNotFound:
                return None

        resp = avatar("real-dj")
        self.assertEqual(resp.headers["Cache-Control"], "public, max-age=300")
        self.assertTrue(self.station.asked[-1].endswith("/persona-avatar/real-dj"))
        for _ in range(3):
            self.assertIsNone(avatar("nobody"))
        self.assertEqual(len(self.station.asked), 2)

    def test_an_id_still_cannot_walk_out_of_its_path_segment(self):
        self._cover("../../schedule/override")
        self.assertTrue(
            self.station.asked[-1].endswith("/cover/..%2F..%2Fschedule%2Foverride"))


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

    def test_a_station_still_starting_is_said_to_be_starting(self):
        # SUB/WAVE 1.16+ answers every route 503 + Retry-After until its
        # startup recovery finishes (#1679). The credential test printed the
        # status line, which reads as a broken station mid-restart.
        from unittest import mock

        import httpx

        from api import diagnostics

        def handler(request):
            return httpx.Response(503, headers={"Retry-After": "1"},
                                  json={"error": "starting"})

        real = httpx.AsyncClient

        def fake(*a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            return real(*a, **kw)

        with mock.patch.object(diagnostics.httpx, "AsyncClient", fake), \
                mock.patch.object(diagnostics.settings_store, "station_base_url",
                                  return_value="http://station"):
            ok, said = asyncio.run(diagnostics.probe_station_admin("u", "p"))
            starting = asyncio.run(diagnostics._station_starting("http://station"))
        self.assertFalse(ok)
        self.assertEqual(diagnostics.STATION_STARTING, said)
        self.assertTrue(starting)


class TestATimingOutStationKeepsTheRightDJ(unittest.TestCase):
    """Observed on a real call 2026-08-10: every station read ReadTimeout'd,
    so /dj, /now-playing AND /personas all failed, and a fresh job process
    (each call is its own) had an empty in-process cache — it answered as the
    generic "the DJ" on "SUB/WAVE", the wrong DJ and the wrong station name to
    a real caller. The last-known persona is now remembered on DISK so the
    next process can fall back to it."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        import station

        self._tmp = tempfile.TemporaryDirectory()
        self._old_file = station._PERSONA_FILE
        station._PERSONA_FILE = Path(self._tmp.name) / "last-persona.json"
        station._persona_cache.update(value=None, at=0.0)

    def tearDown(self):
        import station
        station._PERSONA_FILE = self._old_file
        station._persona_cache.update(value=None, at=0.0)
        self._tmp.cleanup()

    def _client(self):
        from station import StationClient
        return StationClient()

    def test_a_good_read_is_remembered_and_a_timeout_recalls_it(self):
        import station
        c = self._client()
        good = c.persona_from(
            {"name": "Cliff", "id": "p_cliff", "soul": "…", "station": "Yosemite FM"},
            [])
        self.assertEqual(good["name"], "Cliff")
        self.assertEqual(good["station"], "Yosemite FM")

        # A fresh process: clear the in-process cache, then a timed-out read
        # (empty dj) must recall Cliff and the station name from disk, NOT
        # collapse to the default.
        station._persona_cache.update(value=None, at=0.0)
        recalled = c.persona_from({}, [])
        self.assertEqual(recalled["name"], "Cliff")
        self.assertEqual(recalled.get("station"), "Yosemite FM")

    def test_with_nothing_on_record_it_still_falls_back_to_the_default(self):
        c = self._client()
        out = c.persona_from({}, [])
        self.assertEqual(out["name"], "the DJ")

    def test_a_previewed_station_never_becomes_the_fallback_dj(self):
        # /test/station builds a client on a URL the operator has TYPED and
        # not saved. That client wrote whatever DJ answered into both caches —
        # including data/last-persona.json on the /data mount the WORKER reads
        # when its own /dj read times out. One press of Test against somebody
        # else's station and the next slow-read caller was greeted by a DJ
        # from another station, on this station's air.
        import station
        from station import StationClient

        StationClient().persona_from(
            {"name": "Cliff", "id": "p_cliff", "station": "Yosemite FM"}, [])
        station._persona_cache.update(value=None, at=0.0)

        preview = StationClient(base_url="http://someone-else.example",
                                remember=False)
        seen = preview.persona_from(
            {"name": "Foreign", "id": "p_foreign", "station": "Other FM"}, [])
        self.assertEqual(seen["name"], "Foreign")   # still REPORTED, just not kept

        station._persona_cache.update(value=None, at=0.0)
        self.assertEqual(StationClient().persona_from({}, [])["name"], "Cliff")

    def test_a_previewed_station_does_not_borrow_the_home_dj_either(self):
        # The other direction, and the reason the fallback reads are skipped:
        # a preview that answered nothing must look dead, not look like home.
        from station import StationClient

        StationClient().persona_from(
            {"name": "Cliff", "id": "p_cliff", "station": "Yosemite FM"}, [])
        out = StationClient(remember=False).persona_from({}, [])
        self.assertEqual(out["name"], "the DJ")

    def test_the_station_probe_is_the_client_that_forgets(self):
        # The fix only holds at the call site.
        from tests.support import AGENT_WORKER

        src = (AGENT_WORKER / "api" / "diagnostics.py").read_text(encoding="utf-8")
        body = src.split("async def handle_test_station")[1].split("\nasync def")[0]
        self.assertIn("remember=False", body)


class TestATimingOutStationKeepsTheRightVoice(unittest.TestCase):
    """Same shape as the persona cache, one layer down: when the station's
    /settings ReadTimeout'd, the voice mirror came back empty and a caller
    heard the WRONG DJ's voice (-Brock1 for Cliff), because the fallback was
    the TTS server's first voice. The last mirrored map is remembered on disk
    and reused on a timeout instead."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        import station_config

        self._tmp = tempfile.TemporaryDirectory()
        self._old = station_config._VOICE_CACHE
        station_config._VOICE_CACHE = Path(self._tmp.name) / "last-voices.json"

    def tearDown(self):
        import station_config
        station_config._VOICE_CACHE = self._old
        self._tmp.cleanup()

    def test_a_good_mirror_is_remembered_and_a_timeout_recalls_it(self):
        import station_config

        station_config._remember_voices({"p_cliff": "-Cliff1"})
        self.assertEqual(station_config._recall_voices(), {"p_cliff": "-Cliff1"})

    def test_nothing_on_record_recalls_nothing(self):
        import station_config

        self.assertIsNone(station_config._recall_voices())


class TestBothCancelRefusalsAre409(unittest.TestCase):
    """The station tells "too late" and "never queued" apart by a `reason`
    field in the body, NOT by the status code — both are 409.

    Written from the live station, not from the source: this client first
    read a 404 for not-queued, which the station never sends, so cancelling
    something that was not in the queue would have reached the caller as
    "too late, it's already on air" — plausible, confident, and wrong.
    """

    class _Resp:
        def __init__(self, payload):
            self.status_code = 409
            self._payload = payload
            self.text = ""
            # _body() checks .content before parsing — an empty-bodied station
            # answer must not become an exception.
            self.content = b"{}"

        def json(self):
            return self._payload

    def _cancel(self, payload):
        import asyncio

        from station import StationClient

        client = StationClient.__new__(StationClient)

        class _Http:
            async def delete(self, *a, **kw):
                return TestBothCancelRefusalsAre409._Resp(payload)

        client._client = _Http()
        from unittest import mock

        import station_config

        with mock.patch.object(station_config, "admin_credentials",
                               return_value=("u", "p")):
            return asyncio.run(client.cancel_queued_track("t1"))

    def test_already_playing_is_named_as_too_late(self):
        out = self._cancel({"error": "too late to cancel",
                            "reason": "already-playing"})
        self.assertEqual(out["reason"], "already-playing")
        self.assertIn("on the way to air", out["error"])

    def test_not_queued_is_not_reported_as_too_late(self):
        out = self._cancel({"error": "track is not in the queue",
                            "reason": "not-queued"})
        self.assertEqual(out["reason"], "not-queued")
        self.assertIn("isn't in the queue", out["error"])

    def test_a_409_with_no_reason_is_treated_as_not_queued(self):
        # The safer default: claiming "too late" invents a cause, while
        # "wasn't there" is what a missing reason actually implies.
        self.assertEqual(self._cancel({})["reason"], "not-queued")


class TestTheNeverPlayWrites(unittest.TestCase):
    """The three station writes added on the 2026-08-14 upstream pass. Each has
    a status code that is a SUCCESS from the caller's point of view rather than
    a failure, and getting that wrong makes the DJ report a fault for something
    that is exactly as the caller wanted it."""

    class _Resp:
        def __init__(self, status=200, payload=None):
            self.status_code = status
            self._payload = payload if payload is not None else {}
            self.content = b"{}"

        def raise_for_status(self):
            if self.status_code >= 400:
                raise AssertionError(
                    f"raise_for_status reached for {self.status_code} — the "
                    "code should have been handled before this")

        def json(self):
            return self._payload

    def _client(self, resp, method="post"):
        from station import StationClient

        client = StationClient.__new__(StationClient)
        sent = {}

        class _Http:
            async def post(self, path, json=None, **kw):
                sent["path"], sent["json"] = path, json
                return resp

            async def delete(self, path, **kw):
                sent["path"] = path
                return resp

        client._client = _Http()
        return client, sent

    def _run(self, client, coro_factory):
        import asyncio
        from unittest import mock

        import station_config

        with mock.patch.object(station_config, "admin_credentials",
                               return_value=("u", "p")):
            return asyncio.run(coro_factory(client))

    def test_a_block_sends_the_stations_own_track_form(self):
        # {type, trackId} lets the station resolve the album/artist ids and the
        # display snapshot itself, so nothing here has to know a track's shape.
        client, sent = self._client(self._Resp(201, {"entry": {}, "purged": 2}))
        out = self._run(client, lambda c: c.block_track("t1"))
        self.assertEqual(sent["path"], "/library/blocklist")
        self.assertEqual(sent["json"], {"type": "track", "trackId": "t1"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["purged"], 2)

    def test_an_already_blocked_track_is_a_success_not_a_failure(self):
        # 409 "already blocked" is precisely the state the caller asked for.
        # Reporting it as an error would have the DJ apologise for a ban that
        # is already in place.
        client, _ = self._client(self._Resp(409, {"error": "already blocked"}))
        out = self._run(client, lambda c: c.block_track("t1"))
        self.assertTrue(out["ok"])
        self.assertTrue(out["already"])

    def test_unblocking_something_never_blocked_is_a_success(self):
        client, sent = self._client(self._Resp(404), method="delete")
        out = self._run(client, lambda c: c.unblock_track("t1"))
        self.assertTrue(out["ok"])
        self.assertTrue(out["already"])
        self.assertIn("/library/blocklist/track/t1", sent["path"])

    def test_a_track_id_is_escaped_on_its_way_into_the_path(self):
        client, sent = self._client(self._Resp(200), method="delete")
        self._run(client, lambda c: c.unblock_track("a/b?c"))
        self.assertNotIn("a/b", sent["path"])
        self.assertIn("a%2Fb%3Fc", sent["path"])

    def test_nothing_identifiable_never_reaches_the_station(self):
        client, sent = self._client(self._Resp(200))
        out = self._run(client, lambda c: c.block_track(""))
        self.assertFalse(out["ok"])
        self.assertEqual(sent, {})

    # -- the never-play CHECK: bare listings get the station's own mark -----
    def _marked(self, rows, verdicts=None, creds=("admin", "pw"), status=200,
                id_key="id"):
        import json

        import httpx
        from unittest import mock

        import station as station_mod

        sent = []

        def handler(request):
            body = json.loads(request.content)
            sent.append(body["tracks"])
            if status != 200:
                return httpx.Response(status, json={"error": "nope"})
            return httpx.Response(200, json={"blocked": {
                t["id"]: (verdicts or {}).get(t["id"]) for t in body["tracks"]}})

        async def run():
            client = station_mod.StationClient(base_url="http://station")
            client._client = httpx.AsyncClient(
                base_url="http://station", transport=httpx.MockTransport(handler))
            try:
                with mock.patch("station_config.admin_credentials",
                                return_value=creds):
                    return await client.mark_blocked(rows, id_key=id_key)
            finally:
                await client.aclose()

        return asyncio.run(run()), sent

    def test_a_bare_listing_gets_the_stations_own_never_play_mark(self):
        # /similar-tracks, /playlists/:id and /library/history return rows
        # with no blockedBy (upstream pass 2026-09-28); the check stamps the
        # same ref /dj/search carries, so _drop_blocked reads it unchanged.
        ref = {"kind": "entry", "type": "artist", "id": "ar1", "name": "Nope"}
        rows = [{"id": "t1", "title": "A", "artist": "Nope"},
                {"id": "t2", "title": "B", "artist": "Fine"}]
        out, sent = self._marked(rows, {"t1": ref})
        self.assertEqual(ref, out[0]["blockedBy"])
        self.assertIsNone(out[1]["blockedBy"])
        self.assertEqual([{"id": "t1", "title": "A", "artist": "Nope"},
                          {"id": "t2", "title": "B", "artist": "Fine"}], sent[0])

    def test_a_history_row_is_checked_by_its_track_not_its_play(self):
        # A play-log row's own `id` is the PLAY (plays.ts PlayRecord).
        rows = [{"id": 42, "trackId": "t9", "title": "Aired"}]
        rule = {"kind": "rule", "label": "No Xmas", "seasonal": True}
        out, sent = self._marked(rows, {"t9": rule}, id_key="trackId")
        self.assertEqual("t9", sent[0][0]["id"])
        self.assertEqual(rule, out[0]["blockedBy"])

    def test_more_than_the_station_takes_goes_in_slices(self):
        out, sent = self._marked([{"id": f"t{i}"} for i in range(1201)])
        self.assertEqual([500, 500, 201], [len(s) for s in sent])
        self.assertTrue(all("blockedBy" in r for r in out))

    def test_a_row_the_station_already_marked_is_not_asked_about(self):
        _out, sent = self._marked([{"id": "t1", "blockedBy": None}, {"id": "t2"}])
        self.assertEqual(["t2"], [t["id"] for t in sent[0]])

    def test_without_admin_or_when_the_check_fails_nothing_changes(self):
        for kw in ({"creds": ("", "")}, {"status": 404}):
            with self.subTest(case=str(kw)):
                out, _ = self._marked([{"id": "t1"}], **kw)
                self.assertNotIn("blockedBy", out[0])


class TestTheDJsLanguageSurvivesTheRead(unittest.TestCase):
    """The station sets an on-air language per persona, and persona_from used
    to drop it.

    That dict is CONSTRUCTED — five named fields, everything else discarded —
    so the one setting that says what language a DJ works in never reached the
    prompt, and the model inferred one from the briefing instead. Heard on
    2026-08-18: Brock, an English persona, opened a call in Mandarin because
    the rotation was Mandarin-titled and the previous presenter works in it.
    The caller spoke English throughout.
    """

    def _client(self):
        from station import StationClient
        return StationClient()

    def test_the_language_rides_the_dj_read(self):
        out = self._client().persona_from(
            {"name": "Rosie", "id": "p_rosie", "soul": "…",
             "language": "Mandarin Chinese"}, [])
        self.assertEqual(out.get("language"), "Mandarin Chinese")

    def test_a_dj_read_without_it_falls_back_to_the_roster(self):
        # /dj times out often enough that the roster is a real path, and the
        # station's own answer beats an empty string from a thinner read.
        out = self._client().persona_from(
            {"name": "Rosie", "id": "p_rosie", "soul": "…"},
            [{"id": "p_rosie", "name": "Rosie", "language": "Mandarin Chinese"}])
        self.assertEqual(out.get("language"), "Mandarin Chinese")

    def test_empty_means_english_and_stays_empty(self):
        # Upstream's own comment: empty = English. An invented "English" here
        # would put a sentence in every prompt to say what the prompt already
        # demonstrates.
        out = self._client().persona_from(
            {"name": "Brock", "id": "p_brock", "soul": "…"},
            [{"id": "p_brock", "name": "Brock", "language": ""}])
        self.assertEqual(out.get("language"), "")


class TestTheMintsHeadStartIsFreshOrNothing(unittest.TestCase):
    """station_prefetch: the worker must never answer off worse data than its
    own read would get, so recall() refuses stale, mismatched or empty
    snapshots outright and the ringing falls back to reading the station —
    exactly what happened before the head start existed."""

    SNAP = {"dj": {"name": "Dalia"}, "personas": [{"id": "p1", "name": "Dalia"}],
            "now_playing": {}, "state": {}, "session": {}, "schedule": {},
            "skills": []}

    def setUp(self):
        import tempfile
        from pathlib import Path

        import station_prefetch

        self._tmp = tempfile.TemporaryDirectory()
        self._old_path = station_prefetch.PATH
        station_prefetch.PATH = Path(self._tmp.name) / "station-prefetch.json"

    def tearDown(self):
        import station_prefetch

        station_prefetch.PATH = self._old_path
        self._tmp.cleanup()

    def test_a_fresh_matching_snapshot_comes_back(self):
        import station_prefetch

        station_prefetch.store(self.SNAP, {"values": {}}, with_skills=False)
        got = station_prefetch.recall(with_skills=False)
        self.assertIsNotNone(got)
        snap, station_settings = got
        self.assertEqual("Dalia", snap["dj"]["name"])
        self.assertEqual({"values": {}}, station_settings)

    def test_stale_is_refused(self):
        import json
        import time

        import station_prefetch

        station_prefetch.store(self.SNAP, {}, with_skills=False)
        d = json.loads(station_prefetch.PATH.read_text(encoding="utf-8"))
        d["t"] = time.time() - station_prefetch.MAX_AGE_SECS - 1
        station_prefetch.PATH.write_text(json.dumps(d), encoding="utf-8")
        self.assertIsNone(station_prefetch.recall(with_skills=False))

    def test_a_skills_mismatch_is_refused(self):
        # The prompt would gain or lose segments the operator's settings
        # decided otherwise about, mid-ring.
        import station_prefetch

        station_prefetch.store(self.SNAP, {}, with_skills=True)
        self.assertIsNone(station_prefetch.recall(with_skills=False))

    def test_a_snapshot_of_a_down_station_is_refused(self):
        # The station was unreachable at mint time: every read came back
        # empty. Two seconds later it deserves the retry the worker's own
        # read effectively is, not an adopted blank.
        import station_prefetch

        empty = dict(self.SNAP, dj={}, personas=[])
        station_prefetch.store(empty, {}, with_skills=False)
        self.assertIsNone(station_prefetch.recall(with_skills=False))

    def test_garbage_on_disk_is_a_miss_not_a_crash(self):
        import station_prefetch

        station_prefetch.PATH.parent.mkdir(parents=True, exist_ok=True)
        station_prefetch.PATH.write_text("{not json", encoding="utf-8")
        self.assertIsNone(station_prefetch.recall(with_skills=False))

    def test_no_file_is_a_miss(self):
        import station_prefetch

        self.assertIsNone(station_prefetch.recall(with_skills=False))

    def test_capture_stores_what_the_clients_answered(self):
        # capture() builds its own clients by name, so faking the two classes
        # at module level is faking exactly what it reaches for.
        import station as station_mod
        import station_config as station_config_mod
        import station_prefetch

        snap = self.SNAP

        class FakeStation:
            async def snapshot(self, with_skills=False):
                return dict(snap)

            async def aclose(self):
                pass

        class FakeCfg:
            async def settings(self):
                return {"values": {"personas": []}}

            async def aclose(self):
                pass

        old = (station_mod.StationClient, station_config_mod.StationConfig)
        station_mod.StationClient = FakeStation
        station_config_mod.StationConfig = FakeCfg
        try:
            asyncio.run(station_prefetch.capture(with_skills=False))
        finally:
            station_mod.StationClient, station_config_mod.StationConfig = old
        got = station_prefetch.recall(with_skills=False)
        self.assertIsNotNone(got)
        self.assertEqual({"values": {"personas": []}}, got[1])

    # THE SETTINGS HALF KEEPS LONGER. /api/settings answered in a 3.5s median
    # on the operator's station (2026-10-02), so the head start lost the race
    # to the worker on 7 of 8 calls and every caller rang through the read.
    # It is configuration, not who is on air: a copy hours old is right.

    def _age(self, key, secs):
        import json
        import time

        import station_prefetch

        d = json.loads(station_prefetch.PATH.read_text(encoding="utf-8"))
        d[key] = time.time() - secs
        station_prefetch.PATH.write_text(json.dumps(d), encoding="utf-8")

    def test_the_settings_outlive_a_stale_snapshot(self):
        import station_prefetch

        station_prefetch.store(self.SNAP, {"values": {"v": 1}}, with_skills=False)
        self._age("t", station_prefetch.MAX_AGE_SECS + 60)
        self.assertIsNone(station_prefetch.recall(with_skills=False),
                          "the snapshot half must still go stale in seconds")
        self.assertEqual({"values": {"v": 1}}, station_prefetch.recall_settings())
        self._age("settingsT", station_prefetch.SETTINGS_MAX_AGE_SECS + 60)
        self.assertIsNone(station_prefetch.recall_settings())

    def test_an_empty_settings_copy_is_no_copy(self):
        # {} is what a failed or unauthenticated read looks like.
        import station_prefetch

        station_prefetch.store(self.SNAP, {}, with_skills=False)
        self.assertIsNone(station_prefetch.recall_settings())

    def _capture(self, settings_answer):
        import station as station_mod
        import station_config as station_config_mod
        import station_prefetch

        snap, reads = self.SNAP, []

        class FakeStation:
            async def snapshot(self, with_skills=False):
                return dict(snap)

            async def aclose(self):
                pass

        class FakeCfg:
            async def settings(self):
                reads.append(1)
                return settings_answer

            async def aclose(self):
                pass

        old = (station_mod.StationClient, station_config_mod.StationConfig)
        station_mod.StationClient = FakeStation
        station_config_mod.StationConfig = FakeCfg
        try:
            asyncio.run(station_prefetch.capture(with_skills=False))
        finally:
            station_mod.StationClient, station_config_mod.StationConfig = old
        return len(reads)

    def test_a_mint_with_a_recent_copy_does_not_read_settings_again(self):
        import station_prefetch

        station_prefetch.store(self.SNAP, {"values": {"v": 1}}, with_skills=False)
        self._age("settingsT", 60)
        self.assertEqual(0, self._capture({"values": {"v": 2}}))
        self.assertEqual({"values": {"v": 1}}, station_prefetch.recall_settings())
        self.assertIsNotNone(station_prefetch.recall(with_skills=False),
                             "the snapshot half is still fresh from this mint")
        self._age("settingsT", station_prefetch.SETTINGS_REFRESH_SECS + 60)
        self.assertEqual(1, self._capture({"values": {"v": 2}}))
        self.assertEqual({"values": {"v": 2}}, station_prefetch.recall_settings())

    def test_a_failed_refresh_keeps_the_good_copy(self):
        import station_prefetch

        station_prefetch.store(self.SNAP, {"values": {"v": 1}}, with_skills=False)
        self._age("settingsT", station_prefetch.SETTINGS_REFRESH_SECS + 60)
        self.assertEqual(1, self._capture({}))
        self.assertEqual({"values": {"v": 1}}, station_prefetch.recall_settings())

    def _config_reads(self, recent):
        import httpx

        import station_config as station_config_mod

        hits = []

        def handler(request):
            hits.append(request.url.path)
            return httpx.Response(200, json={"values": {"live": True}})

        async def run():
            sc = station_config_mod.StationConfig(base_url="http://station",
                                                  recent_settings=recent)
            await sc.aclose()
            sc._authed = True
            sc._client = httpx.AsyncClient(base_url="http://station",
                                           transport=httpx.MockTransport(handler))
            try:
                return await sc._get("/settings")
            finally:
                await sc.aclose()

        return asyncio.run(run()), hits

    def test_the_worker_rings_on_the_copy_and_the_panel_reads_live(self):
        import station_prefetch

        station_prefetch.store(self.SNAP, {"values": {"v": 1}}, with_skills=False)
        got, hits = self._config_reads(recent=True)
        self.assertEqual({"values": {"v": 1}}, got)
        self.assertEqual([], hits, "the ringing waited on the slow read")
        got, hits = self._config_reads(recent=False)
        self.assertEqual({"values": {"live": True}}, got)
        self.assertEqual(["/settings"], hits)

    def test_only_the_calls_own_setup_takes_the_copy(self):
        from tests.support import AGENT_WORKER

        session = (AGENT_WORKER / "call" / "session.py").read_text(encoding="utf-8")
        self.assertIn("self.station_cfg = StationConfig(recent_settings=True)", session)


class TestTheGenreShelvesTheStationComputesItself(unittest.TestCase):
    """GET /library/genres/related, adopted on the 2026-09-01 upstream pass.

    The station has always served this and nothing here read it. It carries
    two things a genre NAME cannot give up: which shelves sit next to each
    other by embedding, and how much music is on each one. Both halves may be
    absent — the station needs three genre centroids before it computes any
    neighbours — and an absent half has to read as "no answer", never as "no
    such genre".
    """

    def _read(self, handler):
        import httpx
        from unittest import mock

        import station as station_mod

        async def run():
            client = station_mod.StationClient(base_url="http://station")
            client._client = httpx.AsyncClient(
                base_url="http://station",
                transport=httpx.MockTransport(handler))
            try:
                with mock.patch("station_config.admin_credentials",
                                return_value=("op", "pw")):
                    return await client.genre_neighbours()
            finally:
                await client.aclose()

        return asyncio.run(run())

    def _payload(self, body, status=200):
        import httpx

        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            return httpx.Response(status, json=body)

        return self._read(handler), seen

    def test_the_counts_and_the_neighbours_both_come_back(self):
        out, seen = self._payload({
            "genres": [{"value": "Trip-Hop", "songCount": 312},
                       {"value": "Downtempo", "songCount": 208}],
            "related": {"Trip-Hop": [{"value": "Downtempo", "songCount": 208}]},
        })
        self.assertEqual(seen["path"], "/library/genres/related")
        self.assertEqual(out["counts"], {"Trip-Hop": 312, "Downtempo": 208})
        self.assertEqual(out["related"], {"Trip-Hop": ["Downtempo"]})

    def test_a_genre_with_no_count_is_left_out_rather_than_zeroed(self):
        # /library/genres merges Navidrome's own genres in, and those reach
        # the tagged index with nothing counted. A zero here would read as an
        # empty shelf when what it means is that nobody counted — the same
        # invented-fixture trap as the energy float, one listing along.
        out, _ = self._payload({
            "genres": [{"value": "Polka"},
                       {"value": "Ska", "songCount": None},
                       {"value": "Jazz", "songCount": True},
                       {"value": "Blues", "songCount": 12}],
            "related": {},
        })
        self.assertEqual(out["counts"], {"Blues": 12})

    def test_a_genre_with_no_neighbours_is_dropped_from_the_map(self):
        out, _ = self._payload({
            "genres": [],
            "related": {"Polka": [], "Trip-Hop": [{"value": "Downtempo"}],
                        "Ska": "not a list"},
        })
        self.assertEqual(out["related"], {"Trip-Hop": ["Downtempo"]})

    def test_a_genre_is_never_its_own_neighbour(self):
        # Nothing is gained by offering the caller the shelf they just asked
        # for, and the station's own list has been seen to include it.
        out, _ = self._payload({
            "genres": [],
            "related": {"Trip-Hop": [{"value": "Trip-Hop"},
                                     {"value": "Downtempo"}]},
        })
        self.assertEqual(out["related"], {"Trip-Hop": ["Downtempo"]})

    def test_a_station_that_cannot_answer_says_nothing_not_nothing_exists(self):
        # A 404 (an older station), a 500, or a timeout must all leave the
        # browse ladder exactly as it was. The one thing this may never do is
        # let a failed read be read as an empty library.
        out, _ = self._payload({"error": "nope"}, status=500)
        self.assertEqual(out, {})

    def test_a_malformed_body_is_not_a_crash_mid_call(self):
        out, _ = self._payload({"genres": "nonsense", "related": ["wrong"]})
        self.assertEqual(out, {"counts": {}, "related": {}})

    def test_without_station_credentials_nothing_is_asked(self):
        import httpx
        from unittest import mock

        import station as station_mod

        asked = []

        def handler(request: httpx.Request) -> httpx.Response:
            asked.append(request.url.path)
            return httpx.Response(200, json={})

        async def run():
            client = station_mod.StationClient(base_url="http://station")
            client._client = httpx.AsyncClient(
                base_url="http://station",
                transport=httpx.MockTransport(handler))
            try:
                with mock.patch("station_config.admin_credentials",
                                return_value=("", "")):
                    return await client.genre_neighbours()
            finally:
                await client.aclose()

        self.assertEqual(asyncio.run(run()), {})
        self.assertEqual(asked, [])


class TestTheGuideShapesTheStationsWeek(unittest.TestCase):
    """The programme guide card (operator, 2026-09-02) paints from /guide,
    which normalises the station's /schedule so the browser never has to
    know what the grid looks like. Read against the operator's own station
    the same day: days are NUMBERED, "0".."6" with 0 the Sunday, each a
    list of twenty-four show ids; a show's name carries its tagline after
    a middle dot, `moods` is a list, and its `topic` is a paragraph — the
    description. The first cut accepted day names only and dropped every
    day of the real week. `_hours` still accepts the other shapes a grid
    could come in, and a shape it cannot read is an empty day rather than
    a broken card. The avatar is rewritten onto this server's own proxy,
    because the browser cannot reach the station on most deployments."""

    def test_one_entry_per_hour_is_the_grid_as_given(self):
        from api import guide

        day = ["late"] * 6 + [None] * 12 + ["drive"] * 6
        self.assertEqual(guide._hours(day)[:2], ["late", "late"])
        self.assertEqual(guide._hours(day)[12], None)
        self.assertEqual(guide._hours(day)[23], "drive")
        # A cell that is an object names its show one of three ways.
        self.assertEqual(guide._hours([{"showId": "a"}, {"id": "b"},
                                       {"show": {"id": "c"}}])[:3], ["a", "b", "c"])

    def test_a_list_of_ranges_is_expanded(self):
        from api import guide

        slots = guide._hours([{"showId": "late", "start": 22, "end": 24},
                              {"showId": "drive", "from": 6, "to": 9}])
        self.assertEqual(slots[22], "late")
        self.assertEqual(slots[23], "late")
        self.assertEqual(slots[6], "drive")
        self.assertEqual(slots[8], "drive")
        self.assertIsNone(slots[9])
        self.assertIsNone(slots[0])

    def test_an_hour_keyed_map_and_an_unreadable_day_both_land_on_their_feet(self):
        from api import guide

        slots = guide._hours({"7": "morning", "8": {"showId": "morning"}, "x": "?"})
        self.assertEqual(slots[7], "morning")
        self.assertEqual(slots[8], "morning")
        self.assertEqual([None] * 24, guide._hours("not a day"))
        self.assertEqual([None] * 24, guide._hours(None))

    def test_the_week_is_shaped_for_the_card(self):
        from api import guide

        raw = {
            "timezone": "America/New_York",
            "soulsPublished": True,
            "personas": [
                {"id": "fr", "name": "Francesca", "tagline": "velvet",
                 "avatar": "/persona-avatar/fr", "soul": "A long blurb."},
                {"id": "no-pic", "name": "Nobody"},
                {"id": ""}, "junk",
            ],
            "shows": [
                {"id": "piazza", "name": "THE PIAZZA · Golden-Era Pop",
                 "topic": "Sixties pop from the Mediterranean.",
                 "mood": "romantic", "moods": ["romantic", "warm"],
                 "personaId": "fr", "guestPersonaIds": ["no-pic", ""]},
                {"id": "piazza", "name": "dupe"},
                {"name": "no id"},
            ],
            # The real station's keys are day numbers, 0 the Sunday; a day
            # NAME is read too, a day it left out is empty, and a key that is
            # neither is dropped.
            "schedule": {"0": ["piazza"] * 24, "3": ["piazza"] * 2 + [None] * 22,
                         "Monday": ["piazza"] * 24, "funday": ["piazza"] * 24},
        }
        d = guide.shape(raw)
        self.assertEqual("America/New_York", d["timezone"])
        self.assertTrue(d["soulsPublished"])
        self.assertEqual(["fr", "no-pic"], [p["id"] for p in d["personas"]])
        # Through OUR proxy, never the station's path; no picture, no path.
        self.assertEqual("/avatar/fr", d["personas"][0]["avatar"])
        self.assertEqual("", d["personas"][1]["avatar"])
        self.assertEqual("A long blurb.", d["personas"][0]["soul"])
        show = d["shows"][0]
        self.assertEqual(["piazza"], [s["id"] for s in d["shows"]])
        self.assertEqual(["no-pic"], show["guestPersonaIds"])
        # The title and the tagline come apart at the dot; the moods list
        # wins over the single mood; the topic is the description.
        self.assertEqual("THE PIAZZA", show["title"])
        self.assertEqual("Golden-Era Pop", show["tagline"])
        self.assertEqual(["romantic", "warm"], show["moods"])
        self.assertEqual("Sixties pop from the Mediterranean.", show["description"])
        self.assertEqual(sorted(guide.DAYS), sorted(d["grid"]))
        self.assertEqual(["piazza"] * 24, d["grid"]["sun"])       # "0"
        self.assertEqual("piazza", d["grid"]["wed"][1])          # "3"
        self.assertIsNone(d["grid"]["wed"][2])
        self.assertEqual(["piazza"] * 24, d["grid"]["mon"])       # "Monday"
        self.assertEqual([None] * 24, d["grid"]["tue"])

    def test_the_show_on_air_brings_its_angle_from_now_playing(self):
        # /schedule is the show as CONFIGURED; the episode's angle lives on
        # /now-playing's activeShow — "Tonight's angle" on the operator's
        # guide — so the two are read together.
        from api import guide

        now = {"context": {"activeShow": {"id": "s_709aeb",
                                          "episodeAngle": "Quiet textures."}}}
        self.assertEqual({"id": "s_709aeb", "angle": "Quiet textures."},
                         guide.shape({}, now)["onAir"])
        for bad in (None, {}, {"context": "x"}, {"context": {"activeShow": {}}}):
            with self.subTest(now=bad):
                self.assertEqual({}, guide.shape({}, bad)["onAir"])

    def test_nothing_from_the_station_is_an_empty_week_not_a_crash(self):
        from api import guide

        for raw in ({}, None, {"shows": "x", "personas": 3, "schedule": []}):
            with self.subTest(raw=raw):
                d = guide.shape(raw)
                self.assertEqual([], d["shows"])
                self.assertEqual([], d["personas"])
                self.assertEqual(24, len(d["grid"]["mon"]))


class TestTheBlockQueueClient(unittest.TestCase):
    """POST /dj/queue-block and DELETE /dj/queue/block/:id — SUB/WAVE 1.14's
    one-press album (#1632), adopted on the 2026-09-14 upstream pass.

    Two answers here are successes the caller must not hear as failures: a
    404 is a station without the route (or a seed it no longer holds), and
    the album tool falls back to its per-track loop on it; a cancel's 404 is
    "nothing of that block is still waiting", which is a fact about the
    queue, not a fault.
    """

    def _call(self, handler, fn):
        import httpx
        from unittest import mock

        import station as station_mod

        async def run():
            client = station_mod.StationClient(base_url="http://station")
            client._client = httpx.AsyncClient(
                base_url="http://station",
                transport=httpx.MockTransport(handler))
            try:
                with mock.patch("station_config.admin_credentials",
                                return_value=("op", "pw")):
                    return await fn(client)
            finally:
                await client.aclose()

        return asyncio.run(run())

    def _answer(self, status, body, fn):
        import json

        import httpx

        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content) if request.content else {}
            return httpx.Response(status, json=body)

        return self._call(handler, fn), seen

    def test_an_album_is_seeded_from_any_track_with_no_limit_or_order(self):
        # The station REFUSES limit/shuffle on an album rather than ignoring
        # them (schemas/dj.ts), so an album body must never carry either.
        out, seen = self._answer(
            200, {"ok": True, "queued": 3, "blockId": "b1", "queuePosition": 4,
                  "truncated": 0, "skipped": [], "runsPastShowChange": None},
            lambda c: c.queue_block("album", track_id="id7", limit=5,
                                    order="shuffle"))
        self.assertEqual((seen["method"], seen["path"]),
                         ("POST", "/dj/queue-block"))
        self.assertEqual(seen["body"], {"kind": "album", "trackId": "id7"})
        self.assertTrue(out["ok"])
        self.assertEqual((out["queued"], out["blockId"]), (3, "b1"))

    def test_an_artist_block_carries_its_limit_and_order(self):
        _, seen = self._answer(
            200, {"ok": True, "queued": 5},
            lambda c: c.queue_block("artist", artist="Toto", limit=5,
                                    order="shuffle"))
        self.assertEqual(seen["body"], {"kind": "artist", "artist": "Toto",
                                        "limit": 5, "order": "shuffle"})

    def test_a_station_without_the_route_is_unsupported_not_failed(self):
        out, _ = self._answer(404, {"error": "Not found"},
                              lambda c: c.queue_block("album", track_id="id7"))
        self.assertFalse(out["ok"])
        self.assertTrue(out["unsupported"])

    def test_a_bare_404_with_no_json_is_a_station_that_has_no_route(self):
        import httpx

        out = self._call(
            lambda r: httpx.Response(404, text="Cannot POST /dj/queue-block"),
            lambda c: c.queue_block("album", track_id="id7"))
        self.assertTrue(out["unsupported"])

    def test_a_404_naming_what_it_could_not_find_is_a_refusal(self):
        # SUB/WAVE 1.14 answers a JSON-bodied 404 for a name the library does
        # not hold. Read as `unsupported`, one misspelt artist had the DJ tell
        # the caller the station cannot do one-press runs AT ALL — a claim
        # about the whole station, made because of a typo.
        out, _ = self._answer(
            404, {"error": 'nothing by "Eminemm" in the library'},
            lambda c: c.queue_block("artist", artist="Eminemm"))
        self.assertFalse(out["ok"])
        self.assertNotIn("unsupported", out)
        self.assertIn("Eminemm", out["error"])

    def test_a_seed_shaped_404_stays_unsupported_so_the_album_loop_runs(self):
        # The one 404 a caller SHOULD fall back on: the seed itself is gone,
        # and the per-track loop is the pre-1.14 behaviour byte for byte.
        for said in ("track not found", "album not resolvable for this track"):
            with self.subTest(said=said):
                out, _ = self._answer(
                    404, {"error": said},
                    lambda c: c.queue_block("album", track_id="id7"))
                self.assertTrue(out["unsupported"])

    def test_a_wholly_blocked_record_relays_the_words_and_the_skips(self):
        out, _ = self._answer(
            409, {"error": 'every track on "Rumours — Fleetwood Mac" is on '
                           "the never-play blocklist — unblock it first "
                           "(Library → Blocked)",
                  "skipped": [{"title": "Dreams", "artist": "Fleetwood Mac",
                               "reason": "blocked",
                               "blockedBy": {"kind": "rule",
                                             "label": "no soft rock"}}]},
            lambda c: c.queue_block("album", track_id="id7"))
        self.assertFalse(out["ok"])
        self.assertFalse(out.get("unsupported"))
        self.assertIn("never-play", out["error"])
        self.assertEqual(out["skipped"][0]["title"], "Dreams")

    def test_nothing_to_seed_from_never_reaches_the_station(self):
        import httpx

        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return httpx.Response(200, json={})

        out = self._call(handler, lambda c: c.queue_block("album"))
        self.assertFalse(out["ok"])
        self.assertEqual(calls, [])

    def test_the_cancel_reports_removed_and_kept(self):
        out, seen = self._answer(
            200, {"removed": 9, "kept": 1, "label": "Rumours — Fleetwood Mac"},
            lambda c: c.cancel_queued_block("b1"))
        self.assertEqual((seen["method"], seen["path"]),
                         ("DELETE", "/dj/queue/block/b1"))
        self.assertEqual((out["removed"], out["kept"]), (9, 1))
        self.assertIn("Rumours", out["label"])

    def test_a_block_with_nothing_left_is_a_fact_not_a_failure(self):
        out, _ = self._answer(404, {"error": "no queued tracks from that block"},
                              lambda c: c.cancel_queued_block("b1"))
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "nothing-left")


class TestTheShowThatPausesForTalk(unittest.TestCase):
    """station_config.pause_talk_seconds — SUB/WAVE 1.15's pause-and-talk
    (#1645) mirrored: a per-show opt-in and a station-wide threshold, both
    on the authed /settings read. A manual segment from this line is
    eligible on the station's side, so on such a show the DJ's "coming
    right up" is wrong by a record's length unless it knows the number.
    """

    SETTINGS = {"values": {
        "pauseTalkMinSeconds": 30,
        "shows": [{"id": "s_long", "name": "Late Night", "pauseTalk": True},
                  {"id": "s_duck", "name": "Drive", "pauseTalk": False}],
    }}

    def _read(self, payload, show_id):
        from unittest import mock

        from station_config import StationConfig

        async def run():
            with mock.patch("station_config.admin_credentials",
                            return_value=("op", "pw")):
                sc = StationConfig(base_url="http://station")
            # Primed, never fetched: the suite is network-free by house rule.
            sc.prime("/settings", payload)
            try:
                return await sc.pause_talk_seconds(show_id)
            finally:
                await sc.aclose()

        return asyncio.run(run())

    def test_a_show_that_opted_in_reads_the_stations_threshold(self):
        self.assertEqual(self._read(self.SETTINGS, "s_long"), 30)

    def test_a_show_that_did_not_opt_in_is_zero(self):
        self.assertEqual(self._read(self.SETTINGS, "s_duck"), 0)
        self.assertEqual(self._read(self.SETTINGS, "s_missing"), 0)
        self.assertEqual(self._read(self.SETTINGS, ""), 0)

    def test_an_absent_threshold_is_the_stations_own_default(self):
        # settings/defaults.ts: pauseTalkMinSeconds 20, bounds 5-90.
        only_show = {"values": {"shows": [{"id": "s_long", "pauseTalk": True}]}}
        self.assertEqual(self._read(only_show, "s_long"), 20)
        wild = {"values": {"pauseTalkMinSeconds": 900,
                           "shows": [{"id": "s_long", "pauseTalk": True}]}}
        self.assertEqual(self._read(wild, "s_long"), 20)

    def test_an_unreadable_station_is_zero(self):
        self.assertEqual(self._read({"defaults": {}}, "s_long"), 0)


class TestAPendingLyricsLookupIsAskedAgain(unittest.TestCase):
    """SUB/WAVE 1.28 (#2082) made /lyrics/current real: a track the music
    server has no sheet for comes back `pending: true` with empty `lines`
    while LRCLIB is asked, "ask again shortly". The client waits and re-asks
    a bounded number of times, and an answer still pending after that is
    handed on WITH the flag — never flattened into the empty-index answer."""

    def _client(self, answers):
        import httpx

        import station as station_mod

        asked = []

        def handler(request):
            asked.append(request.url.path)
            body = answers[min(len(asked) - 1, len(answers) - 1)]
            return httpx.Response(200, json=body)

        client = station_mod.StationClient(base_url="http://station")
        client._client = httpx.AsyncClient(
            base_url="http://station", transport=httpx.MockTransport(handler))
        return client, asked

    def _read(self, answers):
        import station as station_mod
        from unittest import mock

        client, asked = self._client(answers)

        async def run():
            try:
                return await client.current_lyrics()
            finally:
                await client.aclose()

        # No real sleeping in the suite; the WAITS tuple is the knob.
        with mock.patch.object(station_mod, "LYRICS_PENDING_WAITS", (0, 0)):
            return asyncio.run(run()), asked

    def test_a_pending_answer_is_asked_again_until_the_lines_arrive(self):
        pending = {"songId": "a1", "pending": True, "lines": [], "source": None}
        sheet = {"songId": "a1", "pending": False, "source": "lrclib",
                 "lines": [{"startMs": 1000, "text": "first line"}]}
        got, asked = self._read([pending, sheet])
        self.assertEqual(asked, ["/lyrics/current", "/lyrics/current"])
        self.assertEqual("first line", got["lines"][0]["text"])
        self.assertFalse(got.get("pending"))

    def test_still_pending_after_the_waits_keeps_its_flag(self):
        # 1 ask + one per wait, then stop: a caller is holding, and the tool
        # names a lookup in progress from the flag we hand it.
        pending = {"songId": "a1", "pending": True, "lines": []}
        got, asked = self._read([pending])
        self.assertEqual(3, len(asked))
        self.assertTrue(got["pending"])
        self.assertNotIn("unavailable", got)

    def test_a_settled_answer_is_asked_once(self):
        got, asked = self._read([{"songId": "a1", "pending": False, "lines": []}])
        self.assertEqual(1, len(asked))
        self.assertEqual([], got["lines"])


class TestATargetedSkipNamesItsRefusal(unittest.TestCase):
    """SUB/WAVE #2023 made /dj/skip targetable: `expectId` is the on-air
    track as the caller saw it, and a 409 with a `reason` means the station
    skipped NOTHING because that track is no longer what is on air (it ended
    by itself, or a jingle, bed or break is running after it). Before this
    the client sent no body, so a caller's "skip this" that landed a few
    seconds late cut the NEXT record — and the DJ reported a clean skip."""

    def _skip(self, status, body, expect_id="t2"):
        import httpx
        from unittest import mock

        import station as station_mod

        seen = {}

        def handler(request):
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content) if request.content else None
            return httpx.Response(status, json=body)

        async def run():
            client = station_mod.StationClient(base_url="http://station")
            client._client = httpx.AsyncClient(
                base_url="http://station", transport=httpx.MockTransport(handler))
            try:
                with mock.patch("station_config.admin_credentials",
                                return_value=("admin", "pw")):
                    return await client.skip_track(expect_id=expect_id)
            finally:
                await client.aclose()

        return asyncio.run(run()), seen

    def test_the_on_air_id_rides_the_body(self):
        got, seen = self._skip(200, {"ok": True})
        self.assertEqual("/dj/skip", seen["path"])
        self.assertEqual({"expectId": "t2"}, seen["body"])
        self.assertTrue(got["ok"])

    def test_no_id_means_the_bare_skip_it_always_was(self):
        # An older station, or a booth with no library track on air: the
        # route took no body before #2023 and must keep working without one.
        got, seen = self._skip(200, {"ok": True}, expect_id=None)
        self.assertIsNone(seen["body"])
        self.assertTrue(got["ok"])

    def test_a_409_is_a_refusal_with_the_stations_reason_and_words(self):
        for reason in ("track-changed", "jingle-on-air", "bed-on-air", "break-on-air"):
            msg = f"station words for {reason}"
            got, _ = self._skip(409, {"error": msg, "reason": reason,
                                      "onAirId": "t3"})
            self.assertFalse(got["ok"], reason)
            self.assertEqual(reason, got["reason"])
            self.assertEqual(msg, got["error"])

    def test_a_409_without_words_still_says_nothing_skipped(self):
        got, _ = self._skip(409, {"reason": "bed-on-air"})
        self.assertFalse(got["ok"])
        self.assertIn("nothing skipped", got["error"])


class TestASteerIsPostedAsTheStationSpellsIt(unittest.TestCase):
    """`POST /steer` (SUB/WAVE 1.31, schemas/steer.ts): `until` 'fixed' wants
    `minutes` and 'schedule-change' refuses it; the prompt is ≤500 characters;
    refusals are the station's own sentence; and a station older than 1.31
    has no route, which is a capability gap, not a failure."""

    def _call(self, method, status, body, **kw):
        import httpx
        from unittest import mock

        import station as station_mod

        seen = {}

        def handler(request):
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content) if request.content else None
            return httpx.Response(status, json=body)

        async def run():
            client = station_mod.StationClient(base_url="http://station")
            client._client = httpx.AsyncClient(
                base_url="http://station", transport=httpx.MockTransport(handler))
            try:
                with mock.patch("station_config.admin_credentials",
                                return_value=("admin", "pw")):
                    return await getattr(client, method)(**kw)
            finally:
                await client.aclose()

        return asyncio.run(run()), seen

    def test_a_fixed_steer_carries_minutes_and_a_collapsed_prompt(self):
        got, seen = self._call("steer", 200, {"steer": {}, "queued": 5},
                               prompt="  more   upbeat\n please ", minutes=90)
        self.assertEqual(("POST", "/steer"), (seen["method"], seen["path"]))
        self.assertEqual(seen["body"], {"prompt": "more upbeat please", "playlistId": None,
                                        "startNow": False, "introduce": False,
                                        "until": "fixed", "minutes": 90})
        self.assertTrue(got["ok"])
        self.assertEqual(5, got["queued"])

    def test_until_the_schedule_changes_sends_no_minutes(self):
        _, seen = self._call("steer", 200, {"steer": {}}, prompt="jazz",
                             until="schedule-change", minutes=90)
        self.assertEqual("schedule-change", seen["body"]["until"])
        self.assertNotIn("minutes", seen["body"])

    def test_the_prompt_is_capped_at_the_stations_limit(self):
        _, seen = self._call("steer", 200, {"steer": {}}, prompt="x" * 900)
        self.assertEqual(500, len(seen["body"]["prompt"]))

    def test_a_playlist_alone_is_enough_and_nothing_is_not(self):
        _, seen = self._call("steer", 200, {"steer": {}}, playlist_id="pl-1")
        self.assertEqual("pl-1", seen["body"]["playlistId"])
        got, seen = self._call("steer", 200, {"steer": {}})
        self.assertFalse(got["ok"])
        self.assertNotIn("path", seen)

    def test_refusals_are_the_stations_own_words(self):
        for status in (400, 404, 409, 502):
            got, _ = self._call("steer", status, {"error": f"said {status}"},
                                prompt="x", playlist_id="pl-1")
            self.assertFalse(got["ok"], status)
            self.assertEqual(f"said {status}", got["error"])
            self.assertNotIn("unsupported", got)

    def test_an_older_station_without_the_route_is_unsupported(self):
        got, _ = self._call("steer", 404, {"error": "Not found"}, prompt="x")
        self.assertFalse(got["ok"])
        self.assertTrue(got["unsupported"])

    def test_clearing_is_a_delete_that_reports_what_it_pulled(self):
        got, seen = self._call("clear_steer", 200, {"steer": None, "removed": 2, "kept": 0})
        self.assertEqual(("DELETE", "/steer"), (seen["method"], seen["path"]))
        self.assertTrue(got["ok"])
        self.assertEqual(2, got["removed"])

    def test_status_is_a_read_that_fails_to_nothing(self):
        got, seen = self._call("steer_status", 200, {"steer": None, "budgetMode": "normal", "queued": []})
        self.assertEqual("GET", seen["method"])
        self.assertIsNone(got["steer"])
        got, _ = self._call("steer_status", 500, {"error": "boom"})
        self.assertEqual({}, got)


class TestTheDJsLeaningsAreMirrored(unittest.TestCase):
    """SUB/WAVE's Musical Leanings field (#1678) lives on the admin persona
    record, not on the public /dj read, and the call DJ had never seen it —
    so a caller who left the pick to the DJ got a different taste on the
    phone from the one in the booth."""

    def test_reads_the_nested_roster_and_blank_means_nothing(self):
        import station_config

        payload = {"values": {"personas": [
            {"id": "p_g", "name": "G Money", "musicLean": "  golden-era  boom bap,\n a little G-funk "},
            {"id": "p_f", "name": "Francesca", "musicLean": ""},
            {"id": "p_n", "name": "Nobody"},
        ]}}
        self.assertEqual("golden-era boom bap, a little G-funk",
                         station_config._music_lean_from(payload, "p_g"))
        self.assertEqual("", station_config._music_lean_from(payload, "p_f"))
        self.assertEqual("", station_config._music_lean_from(payload, "p_n"))
        self.assertEqual("", station_config._music_lean_from(payload, "p_missing"))
        self.assertEqual("", station_config._music_lean_from(payload, ""))
        self.assertEqual("", station_config._music_lean_from({}, "p_g"))


class TestTheDJsLanguagesAndRescueVoiceAreMirrored(unittest.TestCase):
    """Two persona fields from the 2026-11-27 upstream pass, both admin-only
    on the station: `alsoSpeaks` (SUB/WAVE 1.34, #1740), the languages a
    multilingual DJ mixes in, and `fallbackVoice` (1.33, #2089), the voice the
    station gives a DJ when its own engine fails."""

    PAYLOAD = {"values": {"personas": [
        {"id": "p_m", "name": "Mari", "language": "Spanish",
         "alsoSpeaks": " English , spanish, Catalan,english,  ",
         "fallbackVoice": {"engine": "gemini", "cloudProvider": "openai",
                           "voice": " Kore "}},
        {"id": "p_e", "name": "Ed", "alsoSpeaks": "English, French"},
        {"id": "p_n", "name": "Nobody", "alsoSpeaks": "", "fallbackVoice": None},
        {"id": "p_x", "name": "Odd", "alsoSpeaks": 7, "fallbackVoice": "Kore"},
    ]}}

    def test_also_speaks_is_read_the_way_the_station_reads_it(self):
        import station_config

        # Split on commas, trimmed; the primary and any repeat dropped
        # case-insensitively, first spelling kept (personaLanguages()).
        self.assertEqual(["English", "Catalan"],
                         station_config._also_speaks_from(self.PAYLOAD, "p_m"))
        # A blank language is English on the station, so English is dropped.
        self.assertEqual(["French"],
                         station_config._also_speaks_from(self.PAYLOAD, "p_e"))
        for pid in ("p_n", "p_x", "p_missing", ""):
            self.assertEqual([], station_config._also_speaks_from(self.PAYLOAD, pid))
        self.assertEqual([], station_config._also_speaks_from({}, "p_m"))

    def test_the_rescue_voice_is_the_voice_and_nothing_else(self):
        import station_config

        self.assertEqual("Kore", station_config._rescue_voice_from(self.PAYLOAD, "p_m"))
        for pid in ("p_e", "p_n", "p_x", "p_missing", ""):
            self.assertEqual("", station_config._rescue_voice_from(self.PAYLOAD, pid))

    def test_musical_leanings_still_read_through_the_shared_record(self):
        import station_config

        payload = {"personas": [{"id": "p_g", "musicLean": " boom  bap "}]}
        self.assertEqual("boom bap", station_config._music_lean_from(payload, "p_g"))


class TestAQueueChangeIsSeenInSeconds(unittest.TestCase):
    """Operator's report, 2026-11-27: a request took most of a minute to show
    in Up next. Measured on the stack: the station fires request.received when
    it ACCEPTS a request, before it resolves the track, so the cache was
    cleared, refilled with the old queue and held for the full 30s. Whatever
    is known to be about to move the queue now opens a short window in which
    the card's answer is held for seconds."""

    def setUp(self):
        from api import live_cache

        self.lc = live_cache
        self._saved = dict(live_cache._live_cache)

    def tearDown(self):
        self.lc._live_cache.clear()
        self.lc._live_cache.update(self._saved)

    def test_outside_a_window_the_card_is_held_for_the_ordinary_ttl(self):
        self.lc._live_cache["quick_until"] = 0.0
        self.assertEqual(self.lc.live_ttl(1000.0), self.lc._LIVE_TTL)

    def test_a_coming_change_drops_the_answer_and_shortens_the_hold(self):
        self.lc._live_cache.update(data={"stale": True}, quick_until=0.0)
        self.lc.expect_queue_change(1000.0)
        self.assertIsNone(self.lc._live_cache["data"])
        self.assertEqual(self.lc.live_ttl(1001.0), self.lc._LIVE_QUICK_TTL)
        # A window, not a mode: it lapses back to the ordinary hold.
        self.assertEqual(
            self.lc.live_ttl(1000.0 + self.lc._LIVE_QUICK_SECS + 1),
            self.lc._LIVE_TTL)

    def test_a_second_change_never_shortens_an_open_window(self):
        self.lc._live_cache["quick_until"] = 0.0
        self.lc.expect_queue_change(1000.0, secs=60)
        self.lc.expect_queue_change(1001.0, secs=5)
        self.assertEqual(self.lc._live_cache["quick_until"], 1060.0)

    def test_the_window_still_bounds_the_station_work(self):
        # However many tabs or hooks arrive, a rebuild at most every few
        # seconds — the floor the quick window keeps.
        self.assertGreaterEqual(self.lc._LIVE_QUICK_TTL, 2.0)
        self.assertLess(self.lc._LIVE_QUICK_TTL, self.lc._LIVE_TTL)


class TestThePlayersLyricsRead(unittest.TestCase):
    """GET /player/lyrics, the Lyrics tab's feed (2026-11-27): the station's
    /lyrics/current narrowed to what the card draws, behind the player's own
    door, held briefly so an open tab costs the station one read a record."""

    class _Req(dict):
        headers: dict = {}

    def setUp(self):
        from api import live_cache, player

        self.player = player
        self.lc = live_cache
        self._lyr = dict(live_cache._lyrics_cache)
        self._door = player._door
        live_cache._lyrics_cache.update(data=None, at=0.0)

    def tearDown(self):
        self.player._door = self._door
        self.lc._lyrics_cache.clear()
        self.lc._lyrics_cache.update(self._lyr)

    def _station(self, lyrics, buffer_secs=6, calls=None):
        calls = [] if calls is None else calls

        class _St:
            async def _lyrics_read(self):
                calls.append(1)
                return lyrics

            async def now_playing(self):
                return {"stream": {"bufferSeconds": buffer_secs}}

            async def aclose(self):
                pass

        return _St, calls

    def _get(self, station_cls):
        import asyncio
        import json
        from unittest import mock

        self.player._door = lambda request, *a, **k: None
        with mock.patch("station.StationClient", station_cls):
            resp = asyncio.run(self.player.handle_player_lyrics(self._Req()))
        return resp.status, json.loads(resp.body.decode("utf-8"))

    def test_timed_lines_arrive_with_the_corrections_the_card_needs(self):
        st, _ = self._station({"songId": "s1", "synced": True, "offsetMs": 250,
                               "pending": False, "source": "lrclib",
                               "lines": [{"startMs": 0, "text": " one "},
                                         {"startMs": 4000, "text": "two"},
                                         {"startMs": 8000, "text": "   "}]})
        status, d = self._get(st)
        self.assertEqual(status, 200)
        self.assertEqual(d["lines"], [{"t": 0, "text": "one"}, {"t": 4000, "text": "two"}])
        self.assertTrue(d["synced"])
        self.assertEqual(d["offsetMs"], 250)
        # The stream buffer: the card counts from the station's start, the
        # listener hears the record that much later.
        self.assertEqual(d["heardDelayMs"], 6000)

    def test_untimed_words_are_not_called_synced(self):
        st, _ = self._station({"songId": "s1", "synced": True,
                               "lines": [{"startMs": None, "text": "words"}]})
        _, d = self._get(st)
        self.assertFalse(d["synced"])
        self.assertEqual(d["lines"], [{"t": None, "text": "words"}])

    def test_a_failed_read_is_no_lyrics_not_an_error(self):
        class _Broken:
            async def _lyrics_read(self):
                raise RuntimeError("404")

            async def aclose(self):
                pass

        status, d = self._get(_Broken)
        self.assertEqual(status, 200)
        self.assertEqual(d["lines"], [])

    def test_one_read_serves_the_record_until_it_lapses(self):
        st, calls = self._station({"songId": "s1", "synced": False,
                                   "lines": [{"startMs": None, "text": "w"}]})
        self._get(st)
        self._get(st)
        self.assertEqual(len(calls), 1, "the second ask went to the station")

    def test_a_pending_lookup_is_asked_again_soon(self):
        self.assertLess(self.player._LYRICS_PENDING_TTL, self.player._LYRICS_TTL)

    def test_it_answers_to_the_players_door(self):
        import inspect

        src = inspect.getsource(self.player.handle_player_lyrics)
        self.assertIn("_door(request)", src)
