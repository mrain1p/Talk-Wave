"""Putting a different show on air — the one caller action that outlives the call.

Its own module because it is its own subject. Everything else a caller can set
in motion is over inside a minute: a request plays and ends, a segment runs, a
skipped record is one record. A takeover changes what the station IS for the
next hour, keeps running after the caller has hung up, and can cancel a pin the
operator set from the station's own admin page.

Split out of test_sidecar.py; see tests/__init__.py.
"""

from __future__ import annotations

import asyncio
import unittest

from call.actions import CallActions
from call.tools.broadcast import _match_show, build_on_air_tools
from station import StationClient


class _Guard:
    """The overlap guard, stubbed. A takeover makes no sound of its own — the
    station airs the handover at the next track boundary — so nothing here
    should ever mark the air busy."""

    def __init__(self) -> None:
        self.marked = []

    def mark_on_air(self, secs=None, spoken=""):
        self.marked.append(secs)


class _Station:
    """A station with two shows and a memory of what it was asked to do."""

    SHOWS = [
        {"id": "s-late", "name": "The Late Show"},
        {"id": "s-break", "name": "Breakfast"},
        {"id": "s-night", "name": "Night Owls"},
    ]

    def __init__(self, ok: bool = True, error: str = "") -> None:
        self.ok = ok
        self.error = error
        self.pinned = None
        self.cleared = False

    async def schedule(self) -> dict:
        return {"shows": list(self.SHOWS)}

    async def pin_show(self, show_id, minutes) -> dict:
        self.pinned = (show_id, minutes)
        return {"ok": True} if self.ok else {"ok": False, "error": self.error}

    async def clear_pinned_show(self) -> dict:
        self.cleared = True
        return {"ok": True} if self.ok else {"ok": False, "error": self.error}


def _tools(station, limit: int = 5) -> dict:
    actions = CallActions(limit)
    built = build_on_air_tools(
        {"allow_takeover": True}, station, actions, _Guard(), guarded=False)
    return {t.info.name: t for t in built}


class TestNamingAShowTheCallerSaid(unittest.TestCase):
    """The station's endpoint wants a showId; a caller says "the late show".

    Resolving that here rather than making the model fetch the schedule and
    pass an id back is a turn of latency saved and one fewer thing to
    hallucinate — an invented id 404s, and the DJ reports a failure for a show
    the station has.
    """

    def test_an_exact_id_wins(self):
        self.assertEqual(
            _match_show(_Station.SHOWS, "s-break")["id"], "s-break")

    def test_a_name_matches_whatever_the_caller_capitalised(self):
        for said in ("The Late Show", "the late show", "  THE LATE SHOW  "):
            self.assertEqual(_match_show(_Station.SHOWS, said)["id"], "s-late", said)

    def test_part_of_a_name_is_enough_when_only_one_show_has_it(self):
        self.assertEqual(_match_show(_Station.SHOWS, "breakfast")["id"], "s-break")

    def test_an_ambiguous_word_matches_nothing_rather_than_guessing(self):
        # "night" is in both The Late Show's stablemate and Night Owls here by
        # design. Picking the first would put a show nobody asked for on air —
        # station-wide, for an hour, on a coin toss.
        shows = [{"id": "a", "name": "Night Owls"}, {"id": "b", "name": "Late Night"}]
        self.assertIsNone(_match_show(shows, "night"))

    def test_nothing_asked_for_matches_nothing(self):
        for said in ("", "   ", None):
            self.assertIsNone(_match_show(_Station.SHOWS, said), repr(said))


class TestPinningAShow(unittest.TestCase):
    def test_an_hour_is_what_a_caller_gets_without_asking(self):
        station = _Station()
        asyncio.run(_tools(station)["subwave_takeover_show"](show="Breakfast"))
        self.assertEqual(station.pinned, ("s-break", 60))

    def test_a_longer_window_is_passed_through_when_they_ask_for_one(self):
        station = _Station()
        asyncio.run(
            _tools(station)["subwave_takeover_show"](show="Breakfast", minutes=180))
        self.assertEqual(station.pinned, ("s-break", 180))

    def test_a_window_the_station_would_refuse_is_corrected_not_sent(self):
        # The endpoint 400s outside its own bounds, and that reaches the caller
        # as "that didn't work" for a number we could have fixed ourselves.
        for asked, expected in ((5, StationClient.TAKEOVER_MIN_MINUTES),
                                (5000, StationClient.TAKEOVER_MAX_MINUTES)):
            station = _Station()
            out = asyncio.run(_tools(station)["subwave_takeover_show"](
                show="Breakfast", minutes=asked))
            self.assertEqual(station.pinned, ("s-break", expected))
            # And the DJ is told the real number, so the caller is not
            # promised the one they asked for.
            self.assertIn(str(expected), out)

    def test_a_show_nobody_has_is_refused_with_the_real_list(self):
        station = _Station()
        out = asyncio.run(
            _tools(station)["subwave_takeover_show"](show="Jazz Hour"))
        self.assertIsNone(station.pinned, "an unmatched name was pinned anyway")
        self.assertIn("Breakfast", out, "the DJ was not told what it could pick")
        self.assertIn("Night Owls", out)

    def test_the_dj_is_told_it_lands_at_the_next_track_not_now(self):
        """The station returns as soon as the pin is stored and airs the
        handover in the background. A DJ that says "you're listening to it
        now" is describing something the caller cannot hear."""
        out = asyncio.run(
            _tools(_Station())["subwave_takeover_show"](show="Breakfast"))
        self.assertIn("record", out.lower())
        self.assertIn("not this second", out.lower())

    def test_a_refusal_is_never_reported_as_success(self):
        station = _Station(ok=False, error="station said no")
        out = asyncio.run(
            _tools(station)["subwave_takeover_show"](show="Breakfast"))
        self.assertIn("station said no", out)
        self.assertIn("do not claim it worked", out.lower())

    def test_it_makes_no_sound_so_the_call_is_never_held(self):
        # Unlike an announcement or a segment: holding the caller silent for
        # speech that happens minutes later at a track boundary is dead air
        # for something they will not connect to their own request.
        guard = _Guard()
        tools = {t.info.name: t for t in build_on_air_tools(
            {"allow_takeover": True}, _Station(), CallActions(5), guard,
            guarded=False)}
        asyncio.run(tools["subwave_takeover_show"](show="Breakfast"))
        self.assertEqual(guard.marked, [])


class TestCancellingATakeover(unittest.TestCase):
    def test_it_hands_the_schedule_back(self):
        station = _Station()
        asyncio.run(_tools(station)["subwave_cancel_takeover"]())
        self.assertTrue(station.cleared)

    def test_a_refusal_is_never_reported_as_success(self):
        station = _Station(ok=False, error="nope")
        out = asyncio.run(_tools(station)["subwave_cancel_takeover"]())
        self.assertIn("nope", out)
        self.assertIn("do not claim it worked", out.lower())


class TestTheCallerCannotDoThisAllNight(unittest.TestCase):
    """Actions per call is the only thing pacing a takeover — the station
    rate-limits requests and segments, but not this."""

    def test_both_halves_stop_at_the_action_limit(self):
        for name, kwargs in (("subwave_takeover_show", {"show": "Breakfast"}),
                             ("subwave_cancel_takeover", {})):
            station = _Station()
            actions = CallActions(1)
            actions.note("request", "something earlier")
            tools = {t.info.name: t for t in build_on_air_tools(
                {"allow_takeover": True}, station, actions, _Guard(),
                guarded=False)}
            out = asyncio.run(tools[name](**kwargs))
            self.assertIn("limit", out.lower(), name)
            self.assertIsNone(station.pinned, name)
            self.assertFalse(station.cleared, name)

    def test_a_takeover_spends_one_of_them(self):
        station = _Station()
        actions = CallActions(5)
        tools = {t.info.name: t for t in build_on_air_tools(
            {"allow_takeover": True}, station, actions, _Guard(), guarded=False)}
        asyncio.run(tools["subwave_takeover_show"](show="Breakfast"))
        self.assertEqual(actions.count, 1)

    def test_a_failed_takeover_costs_the_caller_nothing(self):
        station = _Station(ok=False, error="station said no")
        actions = CallActions(5)
        tools = {t.info.name: t for t in build_on_air_tools(
            {"allow_takeover": True}, station, actions, _Guard(), guarded=False)}
        asyncio.run(tools["subwave_takeover_show"](show="Breakfast"))
        self.assertEqual(actions.count, 0)


class TestTheStationEndpointsAreTheOnesUpstreamServes(unittest.TestCase):
    """Pinned against the station's own route, because getting this wrong
    fails at runtime on someone's live station rather than here."""

    def setUp(self):
        import httpx
        self.httpx = httpx

    def _client(self, handler):
        import httpx

        client = StationClient(base_url="http://station")
        client._client = httpx.AsyncClient(
            base_url="http://station", transport=httpx.MockTransport(handler))
        return client

    def _with_credentials(self, fn):
        import station_config

        original = station_config.admin_credentials
        station_config.admin_credentials = lambda: ("dj", "secret")
        try:
            return fn()
        finally:
            station_config.admin_credentials = original

    def test_a_pin_posts_the_shape_the_station_reads(self):
        import json

        seen = {}

        def handler(request):
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return self.httpx.Response(200, json={"override": {"showId": "s-late"}})

        async def run():
            client = self._client(handler)
            try:
                return await client.pin_show("s-late", 60)
            finally:
                await client.aclose()

        res = self._with_credentials(lambda: asyncio.run(run()))
        self.assertTrue(res["ok"])
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["path"], "/schedule/override")
        self.assertEqual(seen["body"], {"showId": "s-late", "minutes": 60})

    def test_a_cancel_deletes_the_same_path(self):
        seen = {}

        def handler(request):
            seen["method"] = request.method
            seen["path"] = request.url.path
            return self.httpx.Response(200, json={"override": None})

        async def run():
            client = self._client(handler)
            try:
                return await client.clear_pinned_show()
            finally:
                await client.aclose()

        res = self._with_credentials(lambda: asyncio.run(run()))
        self.assertTrue(res["ok"])
        self.assertEqual(seen["method"], "DELETE")
        self.assertEqual(seen["path"], "/schedule/override")

    def test_without_credentials_neither_reaches_the_network(self):
        import station_config

        def handler(request):
            raise AssertionError("the station was called with no credentials")

        async def run():
            client = self._client(handler)
            try:
                return (await client.pin_show("s-late", 60),
                        await client.clear_pinned_show())
            finally:
                await client.aclose()

        original = station_config.admin_credentials
        station_config.admin_credentials = lambda: ("", "")
        try:
            pinned, cleared = asyncio.run(run())
        finally:
            station_config.admin_credentials = original
        for res in (pinned, cleared):
            self.assertFalse(res["ok"])
            self.assertIn("credentials", res["error"])

    def test_our_bounds_are_the_stations_bounds(self):
        # Mirrored from the controller's OVERRIDE_MIN/MAX_MINUTES. If upstream
        # moves them, this is the line that has to be argued with.
        self.assertEqual(StationClient.TAKEOVER_MIN_MINUTES, 15)
        self.assertEqual(StationClient.TAKEOVER_MAX_MINUTES, 720)
