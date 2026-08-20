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
        {"id": "s-late", "name": "The Late Show", "personaId": "p-owl"},
        {"id": "s-break", "name": "Breakfast", "personaId": "p-lark"},
        {"id": "s-night", "name": "Night Owls", "personaId": "p-owl"},
    ]
    # The takeover tool reads these too since 0.10.108 — a caller naming a DJ
    # rather than a show is the commonest way it is asked.
    PEOPLE = [
        {"id": "p-owl", "name": "Wade"},
        {"id": "p-lark", "name": "Dawn"},
    ]

    async def personas(self):
        return list(self.PEOPLE)

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

    def test_the_receipt_says_whose_show_was_pinned(self):
        # Observed twice on air, 2026-08-14: the caller said "change the dj to
        # duke", the model passed a show name it had chosen itself, the pin
        # worked exactly as asked, and the DJ announced the wrong presenter.
        # Nothing could catch it — the argument WAS a real show, so resolving
        # it was correct, and the receipt only named the show. It names the
        # presenter now, so the model can check its own work.
        station = _Station()
        out = asyncio.run(_tools(station)["subwave_takeover_show"](show="Breakfast"))
        self.assertIn("Dawn", out, "the receipt does not say whose show it is")
        self.assertIn("wrong show", out,
                      "the receipt does not tell the DJ what to do when the "
                      "presenter is not the one the caller named")

    def test_a_show_with_no_presenter_still_reads_cleanly(self):
        # A show whose personaId matches nobody must not produce "That is 's
        # show." — the station has had orphaned ids before.
        class _Orphan(_Station):
            SHOWS = [{"id": "s-x", "name": "Ghost Hour", "personaId": "p-gone"}]

        station = _Orphan()
        out = asyncio.run(_tools(station)["subwave_takeover_show"](show="Ghost Hour"))
        self.assertIn("Ghost Hour", out)
        self.assertNotIn("That is", out)
        self.assertNotIn("None", out)

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


class TestADJsNameResolvesToTheirShow(unittest.TestCase):
    """The conduct has promised this since 0.10.93 — "a DJ's name resolves to
    their show" — and the matcher could not do it.

    Real chat, 2026-08-13: the caller asked for "duke", then "Duke Sterling",
    a persona genuinely on the roster whose show is The Alibi Room. The DJ
    told them three times that no such name was on the books, recited a
    roster it had invented from the SHOW names it could see, and only got
    there when the caller named the show himself. The prompt promising a
    capability the code lacks is the same shape as the takeover bug above.
    """

    SHOWS = [
        {"id": "s1", "name": "The Alibi Room", "personaId": "p_a262b2"},
        {"id": "s2", "name": "DONOVAN'S PUB · Irish Folk & Trad",
         "personaId": "p_b69d0a"},
        {"id": "s3", "name": "Up Stream · Deep Cuts", "personaId": "p_10cfa2"},
        {"id": "s4", "name": "Late Feels", "personaId": "p_10cfa2"},
    ]
    PEOPLE = [
        {"id": "p_a262b2", "name": "Duke Sterling"},
        {"id": "p_b69d0a", "name": "Danny Boy"},
        {"id": "p_10cfa2", "name": "Wade"},
    ]

    def _match(self, wanted):
        from call.tools.broadcast import _match_show

        return _match_show(self.SHOWS, wanted, self.PEOPLE)

    def test_the_full_name_finds_the_show(self):
        self.assertEqual(self._match("Duke Sterling")["id"], "s1")

    def test_a_first_name_finds_it_too(self):
        # "change dj to duke" is how it was actually asked.
        self.assertEqual(self._match("duke")["id"], "s1")

    def test_a_show_name_still_wins_over_a_person(self):
        self.assertEqual(self._match("The Alibi Room")["id"], "s1")

    def test_a_dj_with_several_shows_is_refused_not_guessed(self):
        # Wade hosts three. Picking one would be a station-wide change
        # nobody asked for — the same rule the show matcher already had.
        self.assertIsNone(self._match("Wade"))

    def test_a_name_on_neither_list_is_still_a_miss(self):
        self.assertIsNone(self._match("Bananaman"))

    def test_no_personas_supplied_behaves_exactly_as_before(self):
        from call.tools.broadcast import _match_show

        self.assertIsNone(_match_show(self.SHOWS, "duke"))
        self.assertEqual(_match_show(self.SHOWS, "Late Feels")["id"], "s4")


class TestTheDJDoesNotBlameTheWeatherForItsOwnMiss(unittest.TestCase):
    """"Why didn't you get that the first time?" was answered with "the signal
    comes in fuzzy when the wind hits the towers". Same invention as blaming
    distance for an unsent dedication, and it followed three wrong denials."""

    def test_both_mouths_are_told_to_own_it(self):
        from brain import conduct, conduct_chat

        for text in (conduct.rules({}), conduct_chat.rules({})):
            self.assertIn("that is yours — not the transmitter's", text)
            self.assertIn("wind hits the towers", text)      # the NO example

    def test_booth_talk_stays_in_the_booth(self):
        # "Not seeing a tool that fits that one" reached a caller verbatim.
        from brain import conduct

        self.assertIn("not seeing a tool that fits", conduct.rules({}))


class _LockStation(_Station):
    """A station that can also take a genre lock — or can't, which is the
    interesting case: upstream #1404 is not in a released SUB/WAVE yet."""

    def __init__(self, ok: bool = True, error: str = "",
                 unsupported: bool = False, override=None) -> None:
        super().__init__(ok=ok, error=error)
        self.unsupported = unsupported
        self.locked = None
        self._override = override

    async def schedule(self) -> dict:
        d = {"shows": list(self.SHOWS)}
        if self._override is not None:
            d["override"] = self._override
        return d

    async def set_genre_lock(self, genres, minutes) -> dict:
        self.locked = (list(genres), minutes)
        if self.unsupported:
            return {"ok": False, "unsupported": True,
                    "error": "this station's software has no genre lock yet"}
        if not self.ok:
            return {"ok": False, "error": self.error}
        return {"ok": True, "genres": list(genres)}


def _lock_tools(station, limit: int = 5) -> dict:
    actions = CallActions(limit)
    built = build_on_air_tools(
        {"allow_genre_lock": True}, station, actions, _Guard(), guarded=False)
    return {t.info.name: t for t in built}


class TestLockingTheStationToAGenre(unittest.TestCase):
    """The same reach as a takeover, and quieter: a pinned show announces
    itself on air in a voice listeners recognise, a narrowed playlist doesn't.
    Built against upstream #1404, which no released station carries yet — so
    the case that matters most is the one where the station says it can't.
    """

    def test_it_rides_its_own_switch(self):
        built = build_on_air_tools({"allow_takeover": True}, _LockStation(),
                                   CallActions(5), _Guard(), guarded=False)
        names = {t.info.name for t in built}
        self.assertNotIn("subwave_genre_lock", names)
        self.assertIn("subwave_genre_lock", _lock_tools(_LockStation()))

    def test_a_station_without_the_control_is_not_reported_as_a_failure(self):
        # "That didn't work" sends the DJ round again; "this station hasn't got
        # one" is the truth and ends the attempt.
        tools = _lock_tools(_LockStation(unsupported=True))
        out = asyncio.run(tools["subwave_genre_lock"](genres="Jazz"))
        self.assertIn("doesn't have a genre lock", out)
        self.assertIn("do NOT retry", out)
        # And it must not reach for the takeover to fake it — that would pin a
        # real show on a caller who asked about a genre.
        self.assertIn("do NOT", out.replace("Do NOT", "do NOT"))

    def test_the_window_is_clamped_like_a_takeover_and_said_out_loud(self):
        station = _LockStation()
        tools = _lock_tools(station)
        out = asyncio.run(tools["subwave_genre_lock"](genres="Jazz", minutes=5000))
        self.assertEqual(station.locked[1], StationClient.TAKEOVER_MAX_MINUTES)
        self.assertIn(str(StationClient.TAKEOVER_MAX_MINUTES), out)
        self.assertIn("Say the real number", out)

    def test_a_comma_list_becomes_several_genres(self):
        station = _LockStation()
        tools = _lock_tools(station)
        asyncio.run(tools["subwave_genre_lock"](genres="Jazz, Soul , Funk"))
        self.assertEqual(station.locked[0], ["Jazz", "Soul", "Funk"])

    def test_naming_no_genre_asks_rather_than_locking_nothing(self):
        station = _LockStation()
        tools = _lock_tools(station)
        out = asyncio.run(tools["subwave_genre_lock"](genres="  ,  "))
        self.assertIsNone(station.locked)
        self.assertIn("Ask the caller", out)

    def test_the_handover_is_never_described_as_instant(self):
        # The station pins it exactly like a takeover: it lands at the end of
        # the record playing now. A caller told otherwise checks immediately.
        tools = _lock_tools(_LockStation())
        out = asyncio.run(tools["subwave_genre_lock"](genres="Jazz"))
        self.assertIn("end of the record", out)


class TestLiftingALockIsNotLiftingSomeoneElsesTakeover(unittest.TestCase):
    """On the station's side a genre lock and a show takeover are the SAME
    pin, cleared by the same DELETE. Clearing blind would cancel a takeover the
    operator set, for a caller who only asked about a genre."""

    def test_a_show_takeover_is_not_cleared_by_the_genre_tool(self):
        station = _LockStation(override={"showId": "s-late"})
        tools = _lock_tools(station)
        out = asyncio.run(tools["subwave_clear_genre_lock"]())
        self.assertFalse(station.cleared)
        self.assertIn("subwave_cancel_takeover", out)

    def test_the_reserved_lock_show_is_cleared(self):
        from call.tools.broadcast import GENRE_LOCK_SHOW_ID

        station = _LockStation(override={"showId": GENRE_LOCK_SHOW_ID})
        tools = _lock_tools(station)
        out = asyncio.run(tools["subwave_clear_genre_lock"]())
        self.assertTrue(station.cleared)
        self.assertIn("can play anything", out)

    def test_nothing_pinned_is_said_plainly(self):
        station = _LockStation(override=None)
        tools = _lock_tools(station)
        out = asyncio.run(tools["subwave_clear_genre_lock"]())
        self.assertFalse(station.cleared)
        self.assertIn("Nothing is pinned", out)


class TestAMissSaysWhatItDidFind(unittest.TestCase):
    """A miss used to be one sentence and one sentence only: the whole
    roster, twice over, and "ask the caller which one they mean".

    Two calls made the case for something better. 2026-08-19: "i want to hear
    wade, change shows" — Wade presents four shows on the operator's station,
    so the matcher (rightly) refused to guess and the DJ was told no show
    matched, about a DJ who is on the roster four times over. And the
    operator's own words on the second, 2026-08-20: it should be "self-aware
    enough where its not matching letter and case and then saying NO" — a
    caller who says Walt should hear that Wade runs Up Stream, not a refusal.

    Nothing here PINS on a near miss. The tool suggests; the caller confirms.
    """

    SHOWS = [
        {"id": "s1", "name": "The Alibi Room", "personaId": "p_a262b2"},
        {"id": "s2", "name": "Up Stream · Deep Cuts", "personaId": "p_10cfa2"},
        {"id": "s3", "name": "Late Feels", "personaId": "p_10cfa2"},
        {"id": "s4", "name": "Friday Drive", "personaId": "p_10cfa2"},
    ]
    PEOPLE = [
        {"id": "p_a262b2", "name": "Duke Sterling"},
        {"id": "p_10cfa2", "name": "Wade"},
        {"id": "p_ghost", "name": "The Archivist"},
    ]

    def _miss(self, wanted):
        from call.tools.broadcast import _show_miss

        return _show_miss(self.SHOWS, wanted, self.PEOPLE)

    def test_a_dj_with_several_shows_is_named_not_denied(self):
        out = self._miss("wade")
        self.assertIn("Wade", out)
        self.assertIn("3 shows", out)
        for show in ("Up Stream · Deep Cuts", "Late Feels", "Friday Drive"):
            self.assertIn(show, out)
        # The exact thing the DJ said on the call this came from.
        self.assertIn("not because Wade is missing", out)
        self.assertIn("Do NOT tell them there's no such DJ", out)

    def test_a_near_miss_on_a_name_offers_who_was_meant(self):
        out = self._miss("walt")
        self.assertIn("Wade", out)
        self.assertIn("closest", out.lower())
        # Suggested, never pinned: the caller has to confirm first.
        self.assertIn("ask if that's the one", out.lower())
        self.assertIn("do not pin anything until", out.lower())

    def test_a_near_miss_on_a_show_name_offers_the_show(self):
        out = self._miss("alibi room")
        self.assertIn("The Alibi Room", out)
        self.assertIn("closest", out.lower())

    def test_a_dj_with_no_show_is_told_apart_from_a_dj_who_is_missing(self):
        out = self._miss("the archivist")
        self.assertIn("The Archivist", out)
        self.assertIn("no show in the schedule", out)

    def test_a_name_close_to_nothing_still_gets_the_roster(self):
        out = self._miss("xyzzy")
        self.assertIn("The Alibi Room", out)
        self.assertIn("Duke Sterling", out)
        self.assertIn("absent from BOTH lists", out)

    def test_the_tool_returns_the_miss_rather_than_pinning_something(self):
        station = _Station()
        out = asyncio.run(_tools(station)["subwave_takeover_show"](show="walt"))
        self.assertIsNone(station.pinned)
        self.assertIn("Wade", out)


class TestAShowIsReachableHoweverItIsSpelled(unittest.TestCase):
    """The matcher lowercased and compared, and nothing else.

    "Up Stream · Deep Cuts" was reachable by "up stream" and NOT by
    "upstream" — a closed-up space got the same flat refusal as a show that
    does not exist. Every show on the operator's station carries a "·"
    strapline the caller never says, too.
    """

    SHOWS = [
        {"id": "s1", "name": "Up Stream · Deep Cuts", "personaId": "p1"},
        {"id": "s2", "name": "THE OVERLOOK · After Dark", "personaId": "p2"},
        {"id": "s3", "name": "DONOVAN'S PUB · Irish Folk & Trad",
         "personaId": "p3"},
    ]

    def test_the_strapline_is_not_part_of_the_name_a_caller_says(self):
        for said in ("Up Stream", "up stream", "upstream", "UPSTREAM",
                     "Up Stream · Deep Cuts"):
            self.assertEqual(_match_show(self.SHOWS, said)["id"], "s1", said)

    def test_punctuation_a_caller_never_says_is_not_required(self):
        for said in ("donovans pub", "Donovan's Pub", "DONOVANS PUB"):
            self.assertEqual(_match_show(self.SHOWS, said)["id"], "s3", said)

    def test_the_overlook_still_answers_to_its_own_head(self):
        self.assertEqual(_match_show(self.SHOWS, "overlook")["id"], "s2")
