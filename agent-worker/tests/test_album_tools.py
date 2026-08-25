"""The bulk queue: a whole album, or a run of picks, as one action.

Everything here fakes the station. The row shapes are what station.search_library
really returns — /dj/search rows with title/artist/album/year/duration/path and
a blockedBy marker on never-play tracks — because the album tool's whole job is
reading those rows honestly: which album the caller means, what may not be
offered, and what order a ripped library actually files things in.
"""

from __future__ import annotations

import asyncio
import unittest


def _row(i: int, album: str = "Rumours", artist: str = "Fleetwood Mac",
         **extra) -> dict:
    d = {"id": f"id{i}", "title": f"Track {i}", "artist": artist,
         "album": album, "year": 1977, "duration": 180,
         "path": f"{artist}/{album}/{i:02d} - Track {i}.mp3"}
    d.update(extra)
    return d


class _Station:
    """search_library + queue_track, the two calls the bulk tools make.

    `rows` answers every query; `by_query` (casefolded query -> rows), when
    set, answers per query and returns empty for anything unlisted — how the
    live station really behaves for a punctuated album name. `fail_reads`
    makes every search answer None: the read FAILED, which is a different
    fact from an empty result and must stay one.
    """

    def __init__(self, rows: list, by_query: dict | None = None):
        self.rows = rows
        self.by_query = by_query
        self.fail_reads = False
        self.queued: list[dict] = []
        self.searches: list[tuple] = []
        self.refuse: dict[str, str] = {}   # id -> the station's refusal words

    async def search_library(self, q, offset=0, limit=30):
        self.searches.append((q, offset, limit))
        if self.fail_reads:
            return None
        if self.by_query is not None:
            rows = self.by_query.get(" ".join(str(q).casefold().split()), [])
            return rows[offset:offset + limit]
        return self.rows[offset:offset + limit]

    async def queue_track(self, track):
        why = self.refuse.get(track.get("id"))
        if why:
            return {"ok": False, "error": why}
        self.queued.append(track)
        return {"ok": True, "queuePosition": len(self.queued)}


def _tools(station, actions=None, cfg=None):
    from call.actions import CallActions
    from call.tools import music
    from call.tools.music import build_library_tools

    orig = music.library_search_needs_mcp
    music.library_search_needs_mcp = lambda: False   # as if creds were set
    try:
        built = build_library_tools(
            {"allow_album_queue": True, **(cfg or {})}, station,
            actions or CallActions(5))
    finally:
        music.library_search_needs_mcp = orig
    return {t.info.name: t for t in built}


class TestTheBulkToolsRideTheirSwitch(unittest.TestCase):
    """One switch, deliberately not the exact queue's: one sentence taking
    thirty slots is a bigger grant than one taking one, and an operator who
    enabled exact picks must not find an upgrade turned album floods on."""

    def test_on_when_the_switch_is_on(self):
        names = _tools(_Station([]))
        self.assertIn("subwave_queue_album", names)
        self.assertIn("subwave_queue_mix", names)

    def test_off_when_the_switch_is_off(self):
        names = _tools(_Station([]), cfg={"allow_album_queue": False})
        self.assertNotIn("subwave_queue_album", names)
        self.assertNotIn("subwave_queue_mix", names)

    def test_never_built_without_station_credentials(self):
        # Same reasoning as the exact queue: without the credentialed search
        # there are no ids to queue, so the tool cannot exist honestly.
        from call.actions import CallActions
        from call.tools import music
        from call.tools.music import build_library_tools

        orig = music.library_search_needs_mcp
        music.library_search_needs_mcp = lambda: True
        try:
            built = build_library_tools(
                {"allow_album_queue": True}, _Station([]), CallActions(5))
        finally:
            music.library_search_needs_mcp = orig
        names = {t.info.name for t in built}
        self.assertNotIn("subwave_queue_album", names)
        self.assertNotIn("subwave_queue_mix", names)

    def test_the_album_switch_alone_puts_ids_on_search_rows(self):
        # A mix is built by passing ids from search rows, so the rows must
        # carry them even when the single-pick exact queue is off.
        st = _Station([_row(1)])
        names = _tools(st, cfg={"allow_library_search": True})
        out = asyncio.run(names["subwave_search_library"](q="rumours"))
        self.assertIn("[id: id1]", out)

    def test_the_registry_agrees(self):
        from call.tools.registry import BY_NAME

        for name in ("subwave_queue_album", "subwave_queue_mix"):
            self.assertEqual(BY_NAME[name].gate, "allow_album_queue")
            self.assertTrue(BY_NAME[name].needs_station_admin)


class TestQueueingAWholeAlbum(unittest.TestCase):
    def test_the_album_goes_in_whole_in_the_librarys_filing_order(self):
        from call.actions import CallActions

        # Rows arrive in search-relevance order, not tracklist order — the
        # station exposes no track numbers, so path order (the rip's numbered
        # filenames) is the only running order there is.
        rows = [_row(3), _row(1), _row(2), _row(9, album="Tusk")]
        st = _Station(rows)
        actions = CallActions(5)
        tool = _tools(st, actions)["subwave_queue_album"]
        out = asyncio.run(tool(album="Rumours", artist="Fleetwood Mac"))
        self.assertEqual([t["id"] for t in st.queued], ["id1", "id2", "id3"])
        self.assertIn('"Rumours"', out)
        self.assertIn("3 track(s)", out)
        self.assertIn("order the library files them", out)
        self.assertIn("NOT playing", out)
        # 9 minutes of programme, said only because every duration was known.
        self.assertIn("about 9 minutes", out)

    def test_the_whole_batch_is_one_action_not_thirty(self):
        from call.actions import CallActions

        st = _Station([_row(i) for i in range(1, 6)])
        actions = CallActions(5)
        tool = _tools(st, actions)["subwave_queue_album"]
        out = asyncio.run(tool(album="Rumours"))
        self.assertEqual(actions.count, 1)
        self.assertIn("ONE action", out)
        self.assertEqual(actions.taken[0][0], "album")

    def test_never_play_tracks_are_dropped_and_named(self):
        st = _Station([
            _row(1), _row(2, blockedBy={"kind": "rule", "label": "no live cuts"}),
            _row(3),
        ])
        tool = _tools(st)["subwave_queue_album"]
        out = asyncio.run(tool(album="Rumours"))
        self.assertEqual([t["id"] for t in st.queued], ["id1", "id3"])
        self.assertIn("never-play", out)

    def test_asking_again_does_not_queue_it_twice(self):
        # The station queues duplicates on purpose for its own operator (its
        # #619 bypass), so the per-call ledger is the only guard there is.
        from call.actions import CallActions

        st = _Station([_row(1), _row(2)])
        actions = CallActions(5)
        tool = _tools(st, actions)["subwave_queue_album"]
        asyncio.run(tool(album="Rumours"))
        again = asyncio.run(tool(album="Rumours"))
        self.assertEqual(len(st.queued), 2, "the album went in twice")
        self.assertIn("ALREADY in the queue", again)
        self.assertEqual(actions.count, 1)

    def test_two_matching_albums_ask_rather_than_guess(self):
        st = _Station([
            _row(1, album="Greatest Hits", artist="Abba"),
            _row(2, album="Best Hits", artist="Blur"),
        ])
        tool = _tools(st)["subwave_queue_album"]
        out = asyncio.run(tool(album="Hits"))
        self.assertEqual(st.queued, [])
        self.assertIn("More than one album", out)
        self.assertIn("NOTHING queued", out)

    def test_the_artist_settles_a_tie(self):
        st = _Station([
            _row(1, album="Greatest Hits", artist="Abba"),
            _row(2, album="Best Hits", artist="Blur"),
        ])
        tool = _tools(st)["subwave_queue_album"]
        asyncio.run(tool(album="Hits", artist="Blur"))
        self.assertEqual([t["id"] for t in st.queued], ["id2"])

    def test_a_miss_names_what_the_search_did_find(self):
        # "No album by that name" next to the albums that DID come back, so
        # the DJ can re-ask instead of declaring the shelf empty.
        st = _Station([_row(1)])
        tool = _tools(st)["subwave_queue_album"]
        out = asyncio.run(tool(album="Nevermind"))
        self.assertEqual(st.queued, [])
        self.assertIn('"Rumours"', out)

    def test_an_empty_library_answer_is_an_honest_miss(self):
        st = _Station([])
        tool = _tools(st)["subwave_queue_album"]
        out = asyncio.run(tool(album="Nevermind"))
        self.assertIn("Nothing in the racks", out)
        self.assertIn("don't guess", out)

    def test_a_station_refusal_is_not_reported_as_queued(self):
        st = _Station([_row(1)])
        st.refuse["id1"] = "blocked by the station's never-play list"
        tool = _tools(st)["subwave_queue_album"]
        out = asyncio.run(tool(album="Rumours"))
        self.assertIn("None of", out)
        self.assertIn("do NOT claim", out)

    def test_a_spent_call_is_refused_before_the_station_is_touched(self):
        from call.actions import CallActions

        st = _Station([_row(1)])
        spent = CallActions(1)
        spent.note("request", "earlier")
        tool = _tools(st, spent)["subwave_queue_album"]
        out = asyncio.run(tool(album="Rumours"))
        self.assertIn("limit", out.lower())
        self.assertEqual(st.searches, [])
        self.assertEqual(st.queued, [])

    def test_order_is_not_claimed_when_the_library_has_no_paths(self):
        st = _Station([_row(2, path=""), _row(1, path="")])
        tool = _tools(st)["subwave_queue_album"]
        out = asyncio.run(tool(album="Rumours"))
        self.assertNotIn("order the library files them", out)
        # Kept as the station returned them rather than re-sorted by a guess.
        self.assertEqual([t["id"] for t in st.queued], ["id2", "id1"])

    def test_an_oversized_album_is_capped_and_says_so(self):
        from call.tools.albums import ALBUM_MAX_TRACKS

        st = _Station([_row(i) for i in range(1, ALBUM_MAX_TRACKS + 4)])
        tool = _tools(st)["subwave_queue_album"]
        out = asyncio.run(tool(album="Rumours"))
        self.assertEqual(len(st.queued), ALBUM_MAX_TRACKS)
        self.assertIn("capped", out)


class TestTheShelfIsAReadNotAnAction(unittest.TestCase):
    def test_an_artist_alone_lists_their_albums_and_queues_nothing(self):
        from call.actions import CallActions

        st = _Station([_row(1), _row(2), _row(3, album="Tusk")])
        actions = CallActions(5)
        tool = _tools(st, actions)["subwave_queue_album"]
        out = asyncio.run(tool(artist="Fleetwood Mac"))
        self.assertEqual(st.queued, [])
        self.assertEqual(actions.count, 0)
        self.assertIn('"Rumours"', out)
        self.assertIn('"Tusk"', out)
        self.assertIn("NOTHING has been queued", out)

    def test_an_empty_shelf_is_said_plainly(self):
        st = _Station([])
        tool = _tools(st)["subwave_queue_album"]
        out = asyncio.run(tool(artist="Nobody"))
        self.assertIn("Nothing on the shelf", out)

    def test_no_album_and_no_artist_asks_the_caller(self):
        st = _Station([_row(1)])
        tool = _tools(st)["subwave_queue_album"]
        out = asyncio.run(tool())
        self.assertEqual(st.searches, [])
        self.assertIn("which album", out.lower())


class TestQueueingAMix(unittest.TestCase):
    def test_picked_ids_go_in_as_one_action_with_their_titles(self):
        from call.actions import CallActions

        st = _Station([])
        actions = CallActions(5)
        tool = _tools(st, actions)["subwave_queue_mix"]
        out = asyncio.run(tool(
            picks="id1 Lose Yourself\nid2 Stan", label="Eminem mix"))
        self.assertEqual([t["id"] for t in st.queued], ["id1", "id2"])
        self.assertEqual([t["title"] for t in st.queued],
                         ["Lose Yourself", "Stan"])
        self.assertEqual(actions.count, 1)
        self.assertEqual(actions.taken[0], ("mix", "Eminem mix"))
        self.assertIn("playing yet", out)
        self.assertIn("ONE action", out)

    def test_a_pile_of_picks_is_capped(self):
        from call.tools.albums import MIX_MAX_PICKS

        st = _Station([])
        tool = _tools(st)["subwave_queue_mix"]
        picks = "\n".join(f"id{i} Track {i}" for i in range(1, MIX_MAX_PICKS + 4))
        out = asyncio.run(tool(picks=picks))
        self.assertEqual(len(st.queued), MIX_MAX_PICKS)
        self.assertIn("capped", out)

    def test_a_track_already_queued_this_call_is_not_queued_again(self):
        from call.actions import CallActions

        st = _Station([])
        actions = CallActions(5)
        actions.queued_ids.add("id1")
        tool = _tools(st, actions)["subwave_queue_mix"]
        out = asyncio.run(tool(picks="id1 Stan\nid2 Mockingbird"))
        self.assertEqual([t["id"] for t in st.queued], ["id2"])
        self.assertIn("ALREADY queued", out)

    def test_a_refused_pick_is_named_not_papered_over(self):
        st = _Station([])
        st.refuse["id2"] = "on the never-play list"
        tool = _tools(st)["subwave_queue_mix"]
        out = asyncio.run(tool(picks="id1 Stan\nid2 Kim"))
        self.assertIn("refused", out)
        self.assertIn('"Kim"', out)
        self.assertIn("Don't claim", out)

    def test_no_picks_teaches_the_format_and_queues_nothing(self):
        from call.actions import CallActions

        st = _Station([])
        actions = CallActions(5)
        tool = _tools(st, actions)["subwave_queue_mix"]
        out = asyncio.run(tool(picks="   "))
        self.assertEqual(st.queued, [])
        self.assertEqual(actions.count, 0)
        self.assertIn("one per line", out)

    def test_a_spent_call_is_refused_before_the_station_is_touched(self):
        from call.actions import CallActions

        st = _Station([])
        spent = CallActions(1)
        spent.note("request", "earlier")
        tool = _tools(st, spent)["subwave_queue_mix"]
        out = asyncio.run(tool(picks="id1 Stan"))
        self.assertIn("limit", out.lower())
        self.assertEqual(st.queued, [])


class TestTheSelfTitledAlbumFlood(unittest.TestCase):
    """"The Beatles" the album matches every Beatles track in the library, so
    the album's own rows can sit pages deep in the flood — the tool pages
    where the 8-row search never needs to."""

    def test_the_search_pages_through_a_flood(self):
        from call.tools import albums

        page = albums._SEARCH_PAGE
        flood = [_row(i, album="Loose Singles") for i in range(page)]
        wanted = [_row(page + 1, album="The Beatles", artist="The Beatles"),
                  _row(page + 2, album="The Beatles", artist="The Beatles")]
        st = _Station(flood + wanted)
        tool = _tools(st)["subwave_queue_album"]
        out = asyncio.run(tool(album="The Beatles"))
        self.assertEqual(len(st.queued), 2)
        self.assertIn('"The Beatles"', out)


class TestAFailedReadNeverReadsAsAnEmptyLibrary(unittest.TestCase):
    """2026-08-19, live: two station searches timed out mid-call and the DJ
    told the caller their artist wasn't in the library — "I don't have
    anything by Eminem", then no Beatles albums on the shelf — over a
    hundred Eminem tracks and the whole White Album on file. The caller said
    "bullshit" and was right. A failed READ now says it failed, in every
    tool that reads, and the claim is one incident so the coverage lives
    together."""

    def test_the_shelf_says_slow_not_empty(self):
        from call.actions import CallActions

        st = _Station([])
        st.fail_reads = True
        actions = CallActions(5)
        tool = _tools(st, actions)["subwave_queue_album"]
        out = asyncio.run(tool(artist="Eminem"))
        self.assertIn("couldn't be READ", out)
        self.assertNotIn("Nothing on the shelf", out)
        self.assertEqual(st.queued, [])
        self.assertEqual(actions.count, 0)

    def test_the_album_queue_says_slow_not_missing(self):
        st = _Station([])
        st.fail_reads = True
        tool = _tools(st)["subwave_queue_album"]
        out = asyncio.run(tool(album="Rumours", artist="Fleetwood Mac"))
        self.assertIn("couldn't be READ", out)
        self.assertNotIn("Nothing in the racks", out)
        self.assertEqual(st.queued, [])

    def test_the_name_search_says_slow_not_missing(self):
        # The same night's other lie, from the 8-row search tool.
        from call.actions import CallActions
        from call.tools import music
        from call.tools.music import build_library_tools

        class _St:
            async def search_library(self, q, offset=0, limit=30):
                return None

        orig = music.library_search_needs_mcp
        music.library_search_needs_mcp = lambda: False
        try:
            tools = build_library_tools({"allow_library_search": True},
                                        _St(), CallActions(5))
        finally:
            music.library_search_needs_mcp = orig
        tool = next(t for t in tools
                    if t.info.name == "subwave_search_library")
        out = asyncio.run(tool(q="Eminem"))
        self.assertIn("couldn't be READ", out)
        self.assertNotIn("No track or artist by that name", out)


class TestAPunctuatedFiledNameStillQueues(unittest.TestCase):
    """The operator's own White Album, live 2026-08-19: the library files it
    as "The Beatles (The White Album)", and the station's search returns
    NOTHING for that string — or for "White Album". Only the plain artist
    query finds the rows, so the tool walks its variants down to the artist
    and matches the album by punctuation-blind name."""

    ROWS = [_row(i, album="The Beatles (The White Album)",
                 artist="The Beatles") for i in range(1, 4)]

    def _station(self):
        # Only the bare artist query answers — the filed name, the joined
        # album+artist query and "White Album" all return nothing, which is
        # exactly what the live station did.
        return _Station([], by_query={"the beatles": self.ROWS})

    def test_the_colloquial_name_finds_the_filed_album(self):
        st = self._station()
        tool = _tools(st)["subwave_queue_album"]
        out = asyncio.run(tool(album="White Album", artist="The Beatles"))
        self.assertEqual(len(st.queued), 3)
        self.assertIn("The Beatles (The White Album)", out)

    def test_the_full_filed_name_works_too(self):
        st = self._station()
        tool = _tools(st)["subwave_queue_album"]
        asyncio.run(tool(album="The Beatles (The White Album)",
                         artist="The Beatles"))
        self.assertEqual(len(st.queued), 3)

    def test_punctuation_differences_do_not_block_the_match(self):
        rows = [_row(1, album="Sgt. Pepper’s Lonely Hearts Club Band",
                     artist="The Beatles")]
        st = _Station([], by_query={"the beatles": rows})
        tool = _tools(st)["subwave_queue_album"]
        asyncio.run(tool(album="Sgt Peppers Lonely Hearts Club Band",
                         artist="The Beatles"))
        self.assertEqual([t["id"] for t in st.queued], ["id1"])


class TestAnAnthologyShelfSaysItsRealYears(unittest.TestCase):
    """The shelf line's year follows the station's era rule (upstream
    #1418/#1431): a singles anthology's tracks resolve to their own recording
    years, and the first track's file year was the reissue date presented as
    a fact. On the live library the Carpenters' "The Singles 1974-1978" files
    every row as 1996 with originalYear 1990 — a raw first-row read said 1996
    about a shelf that never saw 1996."""

    def test_resolved_years_win_and_a_spread_becomes_a_span(self):
        from call.tools.albums import _album_year

        rows = [
            {"title": "a", "year": 1996, "originalYear": 1974},
            {"title": "b", "year": 1996, "originalYear": 1978},
            {"title": "c", "year": 1996, "originalYear": 1975},
        ]
        self.assertEqual(_album_year(rows), "1974-1978")

    def test_an_album_that_agrees_with_itself_gets_one_year(self):
        from call.tools.albums import _album_year

        rows = [{"title": "a", "year": 1990, "originalYear": None},
                {"title": "b", "year": 1990}]
        self.assertEqual(_album_year(rows), "1990")

    def test_a_suspect_shelf_with_no_answer_says_nothing(self):
        # The station's own rule, mirrored: no year rather than the wrong
        # decade. A flagged row without a resolved original year contributes
        # nothing, and a shelf of nothing but those shows no year at all.
        from call.tools.albums import _album_year

        rows = [{"title": "a", "year": 2012, "isCompilation": True},
                {"title": "b", "year": 2012, "eraUntrusted": True}]
        self.assertEqual(_album_year(rows), "")

    def test_a_trusted_row_carries_a_suspect_shelf(self):
        # One resolved answer beats silence — the suspect rows still say
        # nothing, but the year that IS known is shown.
        from call.tools.albums import _album_year

        rows = [{"title": "a", "year": 2012, "isCompilation": True},
                {"title": "b", "year": 2012, "originalYear": 1964}]
        self.assertEqual(_album_year(rows), "1964")

    def test_a_garbage_year_cannot_crash_the_shelf_and_dates_still_show(self):
        # "²⁰¹²" passes str.isdigit() but int() rejects it (found in review),
        # and a date-shaped year ("1996-03-01") is what _fmt_track shows for
        # the same rows — the shelf must degrade to it, not to silence.
        from call.tools.albums import _album_year

        rows = [{"title": "a", "year": 2012, "isCompilation": True,
                 "originalYear": "²⁰¹²"},
                {"title": "b", "year": "1996-03-01"}]
        self.assertEqual(_album_year(rows), "1996-03-01")


class TestClearingARunFromTheQueue(unittest.TestCase):
    """Bulk OUT, mirroring the album's bulk IN. The 2026-08-19 chat: an
    album went in as one action, "remove all the Eminem" cost one action
    per track, and the DJ hit the per-call cap with four still queued —
    then described the cap as the scheduler fighting him."""

    def _tool(self, upcoming, actions=None, too_late=()):
        from call.actions import CallActions
        from call.tools import music
        from call.tools.music import build_library_tools

        class _St:
            def __init__(self):
                self.cancelled = []
                self.state_reads = 0

            async def state(self):
                self.state_reads += 1
                return {"upcoming": upcoming}

            async def cancel_queued_track(self, tid):
                if tid in too_late:
                    return {"ok": False, "reason": "already-playing",
                            "error": "that one's already on the way to air"}
                self.cancelled.append(tid)
                return {"ok": True}

        st = _St()
        orig = music.library_search_needs_mcp
        music.library_search_needs_mcp = lambda: False   # as if creds were set
        try:
            built = build_library_tools({"allow_cancel_queue": True}, st,
                                        actions or CallActions(5))
        finally:
            music.library_search_needs_mcp = orig
        names = {t.info.name: t for t in built}
        return st, names

    QUEUE = [
        {"subsonic_id": "e1", "title": "Stan", "artist": "Eminem"},
        {"subsonic_id": "e2", "title": "Kim", "artist": "Eminem"},
        {"subsonic_id": "e3", "title": "Drug Ballad", "artist": "Eminem"},
        {"subsonic_id": "x1", "title": "Two Magpies", "artist": "Fink"},
    ]

    def test_everything_by_the_artist_goes_as_one_action(self):
        from call.actions import CallActions

        actions = CallActions(5)
        st, names = self._tool(self.QUEUE, actions)
        out = asyncio.run(names["subwave_clear_from_queue"](artist="Eminem"))
        self.assertEqual(st.cancelled, ["e1", "e2", "e3"])
        self.assertEqual(actions.count, 1)
        self.assertEqual(actions.taken[0][0], "clear")
        self.assertIn("3 track(s)", out)
        self.assertIn("ONE action", out)
        # The bystander's track was never touched.
        self.assertNotIn("x1", st.cancelled)

    def test_the_next_up_refusal_is_named_not_papered_over(self):
        st, names = self._tool(self.QUEUE, too_late={"e1"})
        out = asyncio.run(names["subwave_clear_from_queue"](artist="Eminem"))
        self.assertEqual(st.cancelled, ["e2", "e3"])
        self.assertIn("Too late", out)
        self.assertIn('"Stan"', out)
        self.assertIn("skip", out)

    def test_held_picks_come_out_before_the_mixer_bound_ones(self):
        # `sent` (surfaced by upstream #1458) is which side of the mixer
        # handoff a row sits on: unsent is the controller's own held pick and
        # cancels instantly Node-side, sent is already in Liquidsoap's queue —
        # a telnet round-trip each, and the only kind that can answer
        # "already-playing". The batch pulls the instant ones first, so a
        # budget that dies mid-run has cleared the most it could; queue order
        # holds within each half, and an absent flag counts as unsent.
        queue = [
            {"subsonic_id": "e1", "title": "Stan", "artist": "Eminem",
             "sent": True},
            {"subsonic_id": "e2", "title": "Kim", "artist": "Eminem"},
            {"subsonic_id": "e3", "title": "Drug Ballad", "artist": "Eminem",
             "sent": False},
        ]
        st, names = self._tool(queue)
        out = asyncio.run(names["subwave_clear_from_queue"](artist="Eminem"))
        self.assertEqual(st.cancelled, ["e2", "e3", "e1"])
        self.assertIn("3 track(s)", out)

    def test_an_empty_match_is_honest_and_costs_nothing(self):
        from call.actions import CallActions

        actions = CallActions(5)
        st, names = self._tool(self.QUEUE, actions)
        out = asyncio.run(names["subwave_clear_from_queue"](artist="Nirvana"))
        self.assertEqual(st.cancelled, [])
        self.assertEqual(actions.count, 0)
        self.assertIn("Nothing waiting", out)
        self.assertIn("don't claim", out)

    def test_titles_one_per_line_work_too(self):
        st, names = self._tool(self.QUEUE)
        asyncio.run(names["subwave_clear_from_queue"](titles="Kim\nStan"))
        self.assertEqual(sorted(st.cancelled), ["e1", "e2"])

    def test_a_spent_call_is_refused_before_the_station_is_touched(self):
        from call.actions import CallActions

        spent = CallActions(1)
        spent.note("request", "earlier")
        st, names = self._tool(self.QUEUE, spent)
        out = asyncio.run(names["subwave_clear_from_queue"](artist="Eminem"))
        self.assertIn("limit", out.lower())
        self.assertEqual(st.state_reads, 0)
        self.assertEqual(st.cancelled, [])

    def test_both_unqueue_tools_ride_the_cancel_switch(self):
        # The single cancel moved house (music.py -> removal.py); this
        # guards that the move kept it reachable, beside its batch.
        st, names = self._tool(self.QUEUE)
        self.assertIn("subwave_cancel_queued_track", names)
        self.assertIn("subwave_clear_from_queue", names)

    def test_the_registry_agrees(self):
        from call.tools.registry import BY_NAME

        tool = BY_NAME["subwave_clear_from_queue"]
        self.assertEqual(tool.gate, "allow_cancel_queue")
        self.assertTrue(tool.needs_station_admin)


class TestAMixCanBeUndoneByTheNameItWasGiven(unittest.TestCase):
    """`subwave_queue_mix` takes a label, says it back on the receipt, and
    used to drop it there.

    Read off the record, 2026-08-19. The DJ queued five tracks as "90s alt
    rock mix" and told the caller so. The caller said "ok how about you just
    cancel the 90s alt rock mix i queued". The only field that could hold a
    name was `artist`, so that is where the label went — and no queue row has
    ever carried a mix label, so the tool answered "nothing matching that
    description waiting in the queue... it may have played already". It had
    not: all five aired over the next ten minutes, and the operator watched
    them go.

    The label the caller was GIVEN has to be a label they can hand back.
    """

    QUEUE = [
        {"subsonic_id": "m1", "title": "All Mixed Up", "artist": "311"},
        {"subsonic_id": "m2", "title": "Brodels", "artist": "311"},
        {"subsonic_id": "m3", "title": "DLMD", "artist": "311"},
        {"subsonic_id": "z9", "title": "Someone Else's", "artist": "Fink"},
    ]

    def _tool(self, upcoming, actions):
        return TestClearingARunFromTheQueue._tool(
            TestClearingARunFromTheQueue(), upcoming, actions)

    def _queued(self, actions):
        """As if queue_mix had just run — the ledger it now leaves behind."""
        actions.note_batch("90s alt rock mix", ["m1", "m2", "m3"])

    def test_the_label_clears_the_batch(self):
        from call.actions import CallActions

        actions = CallActions(5)
        self._queued(actions)
        st, names = self._tool(self.QUEUE, actions)
        out = asyncio.run(names["subwave_clear_from_queue"](
            label="90s alt rock mix"))

        self.assertEqual(st.cancelled, ["m1", "m2", "m3"])
        self.assertIn("3 track(s)", out)
        # Another caller's record is not in this batch and is not touched.
        self.assertNotIn("z9", st.cancelled)

    def test_the_label_put_in_the_artist_field_still_finds_it(self):
        # What the model actually did, before `label` existed to reach for.
        from call.actions import CallActions

        actions = CallActions(5)
        self._queued(actions)
        st, names = self._tool(self.QUEUE, actions)
        asyncio.run(names["subwave_clear_from_queue"](
            artist="90s alt rock mix"))
        self.assertEqual(st.cancelled, ["m1", "m2", "m3"])

    def test_the_caller_paraphrasing_the_label_is_enough(self):
        from call.actions import CallActions

        actions = CallActions(5)
        self._queued(actions)
        st, names = self._tool(self.QUEUE, actions)
        asyncio.run(names["subwave_clear_from_queue"](label="the 90s alt rock mix"))
        self.assertEqual(st.cancelled, ["m1", "m2", "m3"])

    def test_a_batch_that_has_already_aired_is_not_called_a_stranger(self):
        # The tracks went in on THIS call and the queue has moved past them.
        # "It never went in" is the sentence that starts an argument with a
        # caller who watched it go in.
        from call.actions import CallActions

        actions = CallActions(5)
        self._queued(actions)
        st, names = self._tool([{"subsonic_id": "z9", "title": "Someone Else's",
                                 "artist": "Fink"}], actions)
        out = asyncio.run(names["subwave_clear_from_queue"](
            label="90s alt rock mix"))

        self.assertEqual(st.cancelled, [])
        self.assertIn("did go into the queue on this call", out)
        self.assertNotIn("never went in", out)
        self.assertEqual(actions.count, 0)      # nothing pulled, nothing spent

    def test_a_label_nobody_queued_is_still_an_ordinary_miss(self):
        from call.actions import CallActions

        actions = CallActions(5)
        st, names = self._tool(self.QUEUE, actions)
        out = asyncio.run(names["subwave_clear_from_queue"](
            label="jazz hour mix"))
        self.assertEqual(st.cancelled, [])
        self.assertIn("Nothing waiting", out)

    def test_nothing_named_at_all_is_still_refused(self):
        from call.actions import CallActions

        st, names = self._tool(self.QUEUE, CallActions(5))
        out = asyncio.run(names["subwave_clear_from_queue"]())
        self.assertEqual(st.state_reads, 0)
        self.assertIn("Say WHAT to clear", out)

    def test_the_ledger_only_remembers_what_actually_queued(self):
        from call.actions import CallActions

        actions = CallActions(5)
        actions.note_batch("empty mix", [])
        actions.note_batch("", ["m1"])
        self.assertEqual(actions.batches, [])
        self.assertEqual(actions.batch_ids("empty mix"), [])

    def test_the_newest_batch_under_a_reused_label_is_the_one_undone(self):
        from call.actions import CallActions

        actions = CallActions(5)
        actions.note_batch("mellow mix", ["old1", "old2"])
        actions.note_batch("mellow mix", ["m1", "m2"])
        self.assertEqual(actions.batch_ids("mellow mix"), ["m1", "m2"])
