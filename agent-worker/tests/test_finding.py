"""One way in to the six ways of looking.

`finding_rule`'s prose table, moved into code and therefore testable for the
first time. The point of the move is that the DJ stops choosing a tool and
starts reporting what it heard — so what is pinned here is the routing, the
gates, and the three things the operator said this must not cost: capability,
discretion, and the DJ's own voice.
"""

import asyncio
import unittest

from call.actions import CallActions
from call.tools.finding import ROUTES, build_finder_tools, route_for


ON = {"single_lookup_tool": True}


class _Tool:
    """Stands in for a built wrapper: remembers what it was handed."""

    class _Info:
        def __init__(self, name):
            self.name = name

    def __init__(self, name, reply="rows"):
        self.info = self._Info(name)
        self.reply = reply
        self.calls: list[dict] = []

    async def __call__(self, **kw):
        self.calls.append(kw)
        return self.reply


def _all_six():
    return [_Tool(name) for name in ROUTES.values()]


def _build(cfg=None, built=None):
    built = _all_six() if built is None else built
    tools = build_finder_tools(dict(cfg or ON), built, CallActions(5))
    return (tools[0] if tools else None), {t.info.name: t for t in built}


class TestTheRouterNamesItsOwnFields(unittest.TestCase):
    """ROUTE_FIELDS exists so the drill's C.5 A/B can credit a find_music
    call to the tool it routed to, from the model's own arguments. It must
    stay equal to route_for's real signature — a kwarg added to one and not
    the other silently mis-credits the A/B, which poisons exactly the
    measurement it was built for."""

    def test_route_fields_match_route_for_signature(self):
        import inspect

        from call.tools import finding

        params = set(inspect.signature(finding.route_for).parameters)
        self.assertEqual(params, set(finding.ROUTE_FIELDS))


class TestTheTableIsNowAFunction(unittest.TestCase):
    """Every row of `finding_rule`, in the order that file states it."""

    def test_each_kind_of_ask_goes_to_its_own_shelf(self):
        for kw, expected in [
            ({"named_track": "Firestone"}, "name"),
            ({"artist": "Kygo"}, "name"),
            ({"sounds_like": "dreamy cinematic strings"}, "sound"),
            ({"like_whats_on": True}, "neighbours"),
            ({"like_track_id": "abc123"}, "neighbours"),
            ({"mood": "calm"}, "browse"),
            ({"genre": "Jazz"}, "browse"),
            ({"year_from": 1980, "year_to": 1989}, "browse"),
            ({"vocal": "instrumental"}, "browse"),
            ({"already_played": True}, "history"),
            ({"let_you_pick": True}, "favourites"),
        ]:
            with self.subTest(**kw):
                self.assertEqual(route_for(**kw), expected)

    def test_a_named_record_beats_the_colour_around_it(self):
        # "something upbeat — got any Kygo?" is a caller naming a record.
        self.assertEqual(
            route_for(named_track="Firestone", mood="upbeat"), "name")

    def test_nothing_to_go_on_routes_nowhere(self):
        self.assertEqual(route_for(), "")


class TestNoCapabilityIsLost(unittest.TestCase):
    """The operator's first condition. Every argument of every wrapper has to
    be reachable through the one tool, or this trades a real capability for a
    tidier tool list."""

    def test_a_name_search_still_pages(self):
        tool, by_name = _build()
        asyncio.run(tool(named_track="Firestone", artist="Kygo", page=3))
        self.assertEqual(by_name["subwave_search_library"].calls,
                         [{"q": "Firestone Kygo", "page": 3}])

    def test_neighbours_still_take_an_explicit_record(self):
        # "more like THAT one" — the caller picked a row out of a search.
        tool, by_name = _build()
        asyncio.run(tool(like_track_id="abc123"))
        self.assertEqual(by_name["subwave_more_like_this"].calls,
                         [{"id": "abc123"}])

    def test_neighbours_with_no_id_still_mean_whats_on(self):
        tool, by_name = _build()
        asyncio.run(tool(like_whats_on=True))
        self.assertEqual(by_name["subwave_more_like_this"].calls, [{"id": ""}])

    def test_every_browse_filter_survives_the_trip(self):
        tool, by_name = _build()
        asyncio.run(tool(mood="calm", genre="Jazz", energy="low",
                         vocal="instrumental", year_from=1980, year_to=1989))
        self.assertEqual(by_name["subwave_browse_library"].calls, [{
            "moods": "calm", "energy": "low", "genre": "Jazz",
            "year_from": 1980, "year_to": 1989, "vocal": "instrumental",
        }])

    def test_the_wrappers_own_words_reach_the_dj_untouched(self):
        # Every careful sentence about what an empty answer means was written
        # in the wrapper and tested there. Routing must not paraphrase it.
        speech = ("Couldn't read the library just now. Say so plainly; "
                  "don't tell them the shelf is bare.")
        built = [_Tool(n, speech if n == "subwave_browse_library" else "rows")
                 for n in ROUTES.values()]
        tool, _ = _build(built=built)
        self.assertIn(speech, asyncio.run(tool(genre="Jazz")))


class TestNoDiscretionIsLost(unittest.TestCase):
    """The second condition. The model is spared a choice it was getting
    wrong; it is not being demoted to a form-filler."""

    def test_prefer_overrides_the_routing(self):
        # "something like the last one but sadder" is a judgment about a
        # person, not a parse of their words — and the DJ can still make it.
        tool, by_name = _build()
        asyncio.run(tool(mood="sad", prefer="neighbours"))
        self.assertEqual(len(by_name["subwave_more_like_this"].calls), 1)
        self.assertEqual(by_name["subwave_browse_library"].calls, [])

    def test_a_route_that_does_not_exist_is_named_not_swallowed(self):
        tool, by_name = _build()
        out = asyncio.run(tool(genre="Jazz", prefer="telepathy"))
        self.assertIn("not one of the ways to look", out)
        # And it must NOT quietly run a different search instead.
        self.assertEqual(by_name["subwave_browse_library"].calls, [])

    def test_an_empty_ask_never_fires_an_action_on_its_own(self):
        # subwave_request_song is rate-limited and its result cannot be seen
        # before the DJ speaks. Firing one off an empty lookup would be this
        # tool taking a decision that belongs to the DJ.
        tool, _ = _build()
        out = asyncio.run(tool())
        self.assertIn("subwave_request_song", out)
        self.assertIn("Ask them one short question", out)


class TestItCannotReachPastTheSettings(unittest.TestCase):
    """A tool that was never built is a capability the operator switched off.
    Routing to it would be a way round the gates, which is the one thing a
    convenience layer must never become."""

    def test_it_is_not_built_at_all_when_the_switch_is_off(self):
        self.assertEqual(build_finder_tools({}, _all_six(), CallActions(5)), [])

    def test_a_withheld_route_names_what_is_left(self):
        built = [t for t in _all_six()
                 if t.info.name != "subwave_search_by_sound"]
        tool, _ = _build(built=built)
        out = asyncio.run(tool(sounds_like="dreamy"))
        self.assertIn("isn't available", out)
        # Never a flat no: the ways in that DO exist are named.
        self.assertIn("browse", out)

    def test_one_remaining_route_is_not_worth_a_hop(self):
        built = [_Tool("subwave_search_library")]
        self.assertEqual(build_finder_tools(dict(ON), built, CallActions(5)), [])


class TestTheDJIsToldWhichShelfItCameOff(unittest.TestCase):
    """The third condition, and 0.98.17's rule: a receipt that names the shelf
    is why a caller hears "these are the ones people round here have loved"
    instead of a list."""

    def test_the_receipt_names_the_route(self):
        tool, _ = _build()
        self.assertIn("what listeners round here have loved",
                      asyncio.run(tool(let_you_pick=True)))

    def test_every_route_has_words_the_dj_can_say_out_loud(self):
        from call.tools.finding import SHELF

        self.assertEqual(set(SHELF), set(ROUTES))
        for route, words in SHELF.items():
            with self.subTest(route=route):
                self.assertNotIn("subwave_", words)


class TestItIsAModeNotAnExtraTool(unittest.TestCase):
    """Switched on, the six leave the model's list and the one takes their
    place. Offering both would be thirty-one tools, two ways to do the same
    job, and an A/B where neither arm is an arrangement anyone would ship."""

    def _apply(self, cfg):
        from call.tools.finding import apply_finder_dispatch

        built = _all_six() + [_Tool("subwave_request_song"),
                              _Tool("subwave_queue_track")]
        return [t.info.name for t in apply_finder_dispatch(dict(cfg), built)]

    def test_off_changes_absolutely_nothing(self):
        names = self._apply({})
        self.assertIn("subwave_search_library", names)
        self.assertNotIn("subwave_find_music", names)
        self.assertEqual(len(names), 8)

    def test_on_swaps_six_for_one(self):
        names = self._apply(ON)
        self.assertIn("subwave_find_music", names)
        for gone in ROUTES.values():
            with self.subTest(gone=gone):
                self.assertNotIn(gone, names)
        # 8 - 6 + 1
        self.assertEqual(len(names), 3)

    def test_requesting_a_song_is_an_action_and_is_left_alone(self):
        # subwave_request_song is not a way of LOOKING, and putting it behind
        # a finder would let a lookup fire a rate-limited action.
        for cfg in ({}, ON):
            with self.subTest(cfg=cfg):
                self.assertIn("subwave_request_song", self._apply(cfg))

    def test_unrelated_tools_are_never_touched(self):
        for cfg in ({}, ON):
            with self.subTest(cfg=cfg):
                self.assertIn("subwave_queue_track", self._apply(cfg))


if __name__ == "__main__":
    unittest.main()
