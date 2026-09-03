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


class TestTheNeverPlayWritesAndTheGenreLock(unittest.TestCase):
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

    def test_a_station_without_the_genre_lock_says_so_rather_than_failing(self):
        # Upstream #1404 is not in a released SUB/WAVE. A 404 here is a
        # capability gap, and the tool depends on being able to tell it from a
        # refusal — the two get completely different words on air.
        client, _ = self._client(self._Resp(404))
        out = self._run(client, lambda c: c.set_genre_lock(["Jazz"], 60))
        self.assertFalse(out["ok"])
        self.assertTrue(out["unsupported"])

    def test_genres_dedupe_case_insensitively_in_first_seen_order(self):
        # The station's own schema does this; doing it here too means the list
        # the DJ reads back is the list that was actually set.
        client, sent = self._client(self._Resp(200, {}))
        out = self._run(
            client, lambda c: c.set_genre_lock(["Jazz", "jazz", "Soul", "JAZZ"], 60))
        self.assertEqual(sent["json"]["genres"], ["Jazz", "Soul"])
        self.assertEqual(out["genres"], ["Jazz", "Soul"])

    def test_an_over_long_list_is_trimmed_rather_than_400ing(self):
        from station import StationClient

        client, sent = self._client(self._Resp(200, {}))
        many = [f"g{i}" for i in range(StationClient.GENRE_LOCK_MAX_GENRES + 5)]
        self._run(client, lambda c: c.set_genre_lock(many, 60))
        self.assertEqual(len(sent["json"]["genres"]),
                         StationClient.GENRE_LOCK_MAX_GENRES)

    def test_an_empty_genre_list_never_reaches_the_station(self):
        client, sent = self._client(self._Resp(200, {}))
        out = self._run(client, lambda c: c.set_genre_lock(["  ", ""], 60))
        self.assertFalse(out["ok"])
        self.assertEqual(sent, {})


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

class TestTheGuideShapesTheStationsWeek(unittest.TestCase):
    """The programme guide card (operator, 2026-09-02) paints from /guide,
    which normalises the station's /schedule so the browser never has to
    know what the grid looks like. The station was off the LAN when this was
    built, so the grid's exact shape was never captured: `_hours` accepts
    the three shapes a schedule grid comes in and turns each into twenty-
    four slots, and a shape it cannot read is an empty day rather than a
    broken card. The avatar is rewritten onto this server's own proxy,
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
                {"id": "piazza", "name": "The Piazza", "topic": "Golden-era pop",
                 "mood": "romantic", "personaId": "fr", "guestPersonaIds": ["no-pic", ""]},
                {"id": "piazza", "name": "dupe"},
                {"name": "no id"},
            ],
            "schedule": {"Monday": ["piazza"] * 2 + [None] * 22, "tue": [],
                         "funday": ["piazza"] * 24},
        }
        d = guide.shape(raw)
        self.assertEqual("America/New_York", d["timezone"])
        self.assertTrue(d["soulsPublished"])
        self.assertEqual(["fr", "no-pic"], [p["id"] for p in d["personas"]])
        # Through OUR proxy, never the station's path; no picture, no path.
        self.assertEqual("/avatar/fr", d["personas"][0]["avatar"])
        self.assertEqual("", d["personas"][1]["avatar"])
        self.assertEqual("A long blurb.", d["personas"][0]["soul"])
        self.assertEqual(["piazza"], [s["id"] for s in d["shows"]])
        self.assertEqual(["no-pic"], d["shows"][0]["guestPersonaIds"])
        # Every day is present with twenty-four slots, whatever the station
        # sent: a full day name is read, a day it left out is empty, and a
        # day that is not a day is dropped.
        self.assertEqual(sorted(guide.DAYS), sorted(d["grid"]))
        self.assertEqual("piazza", d["grid"]["mon"][1])
        self.assertIsNone(d["grid"]["mon"][2])
        self.assertEqual([None] * 24, d["grid"]["tue"])
        self.assertEqual([None] * 24, d["grid"]["sun"])

    def test_nothing_from_the_station_is_an_empty_week_not_a_crash(self):
        from api import guide

        for raw in ({}, None, {"shows": "x", "personas": 3, "schedule": []}):
            with self.subTest(raw=raw):
                d = guide.shape(raw)
                self.assertEqual([], d["shows"])
                self.assertEqual([], d["personas"])
                self.assertEqual(24, len(d["grid"]["mon"]))

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
