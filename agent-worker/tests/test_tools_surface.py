"""Which tools reach a caller at all. The allowlist is the invariant here: a stranger drives this by voice.

Split out of test_sidecar.py; see tests/__init__.py.
"""

from __future__ import annotations

import asyncio
import json
import unittest
import settings as settings_store
from tests.support import AGENT_WORKER, _TempStores


class TestExposedSurface(unittest.TestCase):
    """Everything this service exposes, pinned so a change has to be deliberate.

    Not a style check. The failure it exists for is quiet: someone adds a route
    and forgets the auth gate, or relaxes a tool from `never` to a settings
    flag, and nothing anywhere says the attack surface just grew. Both are one
    line of a diff and neither breaks a test that only exercises behaviour.

    When this fails, read the diff and decide. Updating the manifest is the
    right fix for an intentional change — the point is that you had to.
    """

    # method path -> "public" | "admin"
    # "admin" means the handler consults the password gate. OPTIONS (CORS
    # preflight) and HEAD (mirrors GET) are excluded as noise.
    ROUTES = {
        "STATIC /": "public",                  # the widget's own files
        "GET /": "public",
        # Markup only, and deliberately public: every endpoint the panel calls
        # checks admin auth for itself, so serving the form to someone with no
        # password gets them an empty form and a login prompt. It is its own
        # URL so that a reverse proxy CAN put a rule in front of it — which is
        # the operator's choice to make, not something this route assumes.
        "GET /health": "public",
        "GET /live": "public",                 # what the call card renders
        "GET /avatar/{persona_id}": "public",  # proxied so embeds work on https
        "GET /sounds/{name}": "public",        # uploaded call sounds
        "GET /sound-lib/{name}": "public",     # bundled clips — the widget plays them on every caller's page
        "POST /settings/sounds/meta": "admin", # category edits on the sound board
        "GET /sound-packs": "public",
        "GET /pack-sounds/{pack}/{name}": "public",
        # Gated twice over, which this column is too coarse to say: a real
        # call needs the guest code, a pipeline probe needs the admin one.
        "POST /token": "admin",
        # Frees a concurrency slot by room id. Unauthenticated on purpose —
        # the widget calls it on hangup — and safe because the id is 48 bits
        # of uuid4, so you cannot release a slot you were not already in.
        "POST /call-ended": "public",          # releases a slot; no secrets
        # Public deliberately: the only person with an opinion about a call is
        # the anonymous stranger who was just on it, and there is no
        # credential they could hold. All it can do is set one of two words on
        # a record that already exists, keyed by a per-call room id the writer
        # had to have been handed.
        "POST /call-feedback": "public",
        # The text line. "public" is this column's coarseness again: the
        # route upgrades to a WebSocket and the FIRST FRAME carries the
        # credentials a browser cannot put in WS headers — chat_enabled must
        # be on, The Line unpaused, the guest ladder passed (allow_chat),
        # and the ceilings held, all checked in api/chat before any LLM is
        # touched. The gate is the same one /token holds, one frame later.
        "GET /chat/ws": "public",
        # The station does not sign its webhooks, so this cannot be
        # authenticated. It is safe only because it treats the payload as
        # untrusted data: store it, bust caches, never act on its contents.
        # If that ever changes, this entry is the thing to argue with.
        "POST /hooks/station": "public",
        "POST /auth/guest": "public",          # verifying a code needs no code
        "POST /auth/password": "admin",
        # Takes an arbitrary settings patch and says what it resolves to.
        # Writes nothing — but it answers questions about the stored config
        # to whoever asks, which is the operator's business alone.
        "POST /live/preview": "admin",
        "GET /settings": "admin",
        "POST /settings": "admin",
        "GET /settings/options": "admin",
        # Per-DJ voice effects: panel furniture both ways — the caller-facing
        # answer rides /live, which stays public.
        "GET /settings/voice-effects": "admin",
        "POST /settings/voice-effects": "admin",
        "POST /settings/secrets": "admin",
        "GET /settings/sounds": "admin",
        "POST /settings/sounds": "admin",
        # Staging spends the operator's TTS money; the messages are
        # strangers' words meant for the operator's eyes. Admin, all four.
        "GET /voicemail/status": "admin",
        "POST /voicemail/stage": "admin",
        "GET /voicemail/messages": "admin",
        "DELETE /voicemail/messages": "admin",
        "GET /voicemail/greeting/{persona_id}": "admin",
        "POST /voicemail/greeting/{persona_id}": "admin",
        "DELETE /voicemail/greeting/{persona_id}": "admin",
        "DELETE /settings/sounds/{name}": "admin",
        "GET /prompt": "admin",
        "GET /calls": "admin",
        "GET /logs": "admin",
        "DELETE /calls": "admin",
        "DELETE /logs": "admin",
        "GET /hooks/recent": "admin",
        # Costs the station a round trip and reveals the receiver address.
        "POST /hooks/test": "admin",
        # Every test button costs money or reveals config.
        "GET /test/station": "admin",
        "POST /test/admin": "admin",
        "POST /test/env": "admin",
        "POST /test/llm": "admin",
        "POST /test/tts": "admin",
        "POST /test/speed": "admin",
    }

    # Station tools, and what unlocks each. "never" is the important column:
    # those are not reachable at any setting, and moving one out of `never`
    # hands a stranger on the phone a new capability.
    TOOLS = {
        "subwave_health": "read",
        "subwave_now_playing": "read",
        "subwave_station_state": "read",
        "subwave_schedule": "read",
        "subwave_session": "read",
        "subwave_request_song": "allow_requests",
        "subwave_request_status": "allow_requests",
        "subwave_search_library": "allow_library_search",
        "subwave_queue_track": "allow_exact_queue",
        # The lowest-harm action: a like on the current record, the same heart
        # any listener taps. Public station endpoint, so no admin credentials.
        "subwave_like_track": "allow_favorite",
        # Its admin-only counterpart: un-hearts the operator's own curation
        # like (the public like has none), so station admin creds and admin tier.
        "subwave_unlike_track": "allow_unfavorite",
        "subwave_dj_announce": "allow_announcements",
        "subwave_list_skills": "allow_skills",
        "subwave_run_skill": "allow_skills",
        # Station-wide and opt-in: these reach every listener, not just the
        # caller. Moved out of `never` deliberately in 0.9.54, off by default,
        # and capped by Actions per call.
        "subwave_skip_track": "allow_skip_track",
        "subwave_dj_segment": "allow_dj_segment",
        # Further-reaching than either, and the only caller action whose effect
        # outlives the call: it puts a different show, and so a different DJ,
        # on air for an hour. Not an MCP tool — the station exposes takeover
        # over admin REST only — so these two are ours end to end.
        "subwave_takeover_show": "allow_takeover",
        "subwave_cancel_takeover": "allow_takeover",
        "subwave_refresh_playlist": "never",
        "subwave_list_sfx": "never",
        "subwave_play_sfx": "never",
    }

    def _live_routes(self) -> dict:
        import inspect

        import token_server

        found = {}
        for route in token_server.build_app().router.routes():
            if route.method in ("HEAD", "OPTIONS"):
                continue
            path = getattr(route.resource, "canonical", "")
            key = f"{route.method} {path}" if path else "STATIC /"
            try:
                src = inspect.getsource(route.handler)
            except (OSError, TypeError):
                src = ""
            gated = "_write_allowed" in src or "_check_admin" in src
            found[key] = "admin" if gated else "public"
        return found

    def test_no_route_appears_or_changes_gate_unnoticed(self):
        found = self._live_routes()
        added = sorted(set(found) - set(self.ROUTES))
        removed = sorted(set(self.ROUTES) - set(found))
        changed = sorted(
            f"{k}: pinned {self.ROUTES[k]}, now {found[k]}"
            for k in set(found) & set(self.ROUTES) if found[k] != self.ROUTES[k]
        )
        self.assertEqual(
            added, [],
            "New route(s) exposed. If deliberate, add them to ROUTES — and "
            "check whether they should be behind the password gate.")
        self.assertEqual(removed, [], "Route(s) gone; the widget may still call them.")
        self.assertEqual(changed, [], "A route's auth posture changed.")

    def test_nothing_reachable_without_a_password_grows_quietly(self):
        # The subset that matters most, stated on its own so it reads as the
        # security claim it is rather than a line in a bigger diff.
        public = {k for k, v in self._live_routes().items() if v == "public"}
        expected = {k for k, v in self.ROUTES.items() if v == "public"}
        self.assertEqual(
            sorted(public - expected), [],
            "Something new is reachable with no password at all.")

    def test_the_station_tool_surface_is_what_we_think(self):
        from call.tools.registry import TOOLS

        live = {t.name: t.gate for t in TOOLS}
        self.assertEqual(live, self.TOOLS, "The station tool surface changed.")

    def test_destructive_tools_stay_unreachable_at_every_setting(self):
        # The claim the README makes to operators: these are never exposed,
        # whatever the permission switches say.
        from call.tools.registry import blocked_names, mcp_allowlist, local_tool_names

        never = {n for n, gate in self.TOOLS.items() if gate == "never"}
        self.assertEqual(set(blocked_names()), never)

        everything_on = {gate: True for gate in self.TOOLS.values()}
        reachable = set(mcp_allowlist(everything_on)) | set(
            local_tool_names(everything_on, local_search_available=True))
        self.assertEqual(
            reachable & never, set(),
            "A tool marked `never` became reachable with every switch on.")


class TestStationWideTools(_TempStores):
    """Skipping a track, firing a programme beat and pinning a show reach every
    listener.

    The first two were `never` until 0.9.54, the takeover until 0.9.110. The
    operator's terms for opening any of them up were the same: off by default,
    and capped. Both are load-bearing, so both are tested — the default
    especially, because a permission that quietly defaults to on is how
    someone else's station starts skipping tracks.
    """

    def _tools(self, cfg: dict) -> set[str]:
        from call.tools import build_on_air_tools

        class _Guard:
            def mark_on_air(self, secs, spoken=""):
                pass

        from call.actions import CallActions

        built = build_on_air_tools(
            cfg, object(), CallActions(5), _Guard(), guarded=False)
        return {t.info.name for t in built}

    STATION_WIDE = {"subwave_skip_track", "subwave_dj_segment",
                    "subwave_takeover_show", "subwave_cancel_takeover"}

    def test_all_of_them_are_off_by_default(self):
        # On the real defaults, not a hand-made dict — a permission that
        # quietly defaults to on is how someone else's station starts
        # skipping tracks. Resolved for the most trusted caller there is: if
        # even an admin caller does not get these by default, nobody does.
        cfg = settings_store.permissions_for(settings_store.load(), "admin")
        self.assertFalse(cfg.get("allow_skip_track"))
        self.assertFalse(cfg.get("allow_dj_segment"))
        self.assertFalse(cfg.get("allow_takeover"))
        self.assertEqual(self._tools(cfg) & self.STATION_WIDE, set())

    def test_raw_tiers_must_never_reach_a_tool_builder(self):
        # The trap this whole design is arranged around: the stored value is
        # a string, "off" is truthy, and every tool builder has always asked
        # `cfg.get("allow_x")`. Handing one the UNRESOLVED settings would
        # switch on every station-wide permission at once — the loudest
        # possible failure, on somebody else's broadcast. So resolving is not
        # a step that can be skipped: this is what says so out loud.
        raw = settings_store.load()
        self.assertEqual(raw["allow_skip_track"], "off")
        self.assertTrue(bool(raw["allow_skip_track"]),
                        "the point of this test: the raw value IS truthy")
        self.assertEqual(
            self._tools(settings_store.permissions_for(raw, "admin"))
            & self.STATION_WIDE, set())

    def test_a_tier_only_reaches_the_callers_at_or_above_it(self):
        for tier, expected in (("open", set()), ("guest", {"subwave_skip_track"}),
                               ("admin", {"subwave_skip_track"})):
            with self.subTest(tier=tier):
                cfg = settings_store.permissions_for(
                    {"allow_skip_track": "guest"}, tier)
                self.assertEqual(self._tools(cfg) & self.STATION_WIDE, expected)

    def test_each_appears_only_when_its_own_switch_is_on(self):
        self.assertEqual(
            self._tools({"allow_skip_track": True}) & self.STATION_WIDE,
            {"subwave_skip_track"})
        self.assertEqual(
            self._tools({"allow_dj_segment": True}) & self.STATION_WIDE,
            {"subwave_dj_segment"})
        # Both halves of the takeover ride one switch on purpose: cancelling
        # the operator's own pin is a station-wide change too, so it must not
        # be reachable on a line that was never given the pin.
        self.assertEqual(
            self._tools({"allow_takeover": True}) & self.STATION_WIDE,
            {"subwave_takeover_show", "subwave_cancel_takeover"})

    def test_they_are_local_wrappers_so_the_action_cap_applies(self):
        # The whole reason they are not MCP allowlist entries. An MCP-served
        # tool never consults CallActions, so it would have no ceiling.
        from call.tools.registry import local_tool_names, mcp_allowlist

        cfg = {"allow_skip_track": True, "allow_dj_segment": True,
               "allow_takeover": True}
        served_locally = local_tool_names(cfg, local_search_available=True)
        over_mcp = mcp_allowlist(cfg, local_search_available=True)
        for name in sorted(self.STATION_WIDE):
            self.assertIn(name, served_locally)
            self.assertNotIn(name, over_mcp)

    def test_the_takeover_is_not_claimed_without_station_credentials(self):
        # It is admin REST end to end, so with no credentials it cannot be
        # built at all. The panel must not list it as available — that is the
        # exact bug the exact-queue wrapper shipped with.
        import station_config
        from call.tools.registry import effective_tools

        original = station_config.admin_credentials
        try:
            station_config.admin_credentials = lambda: ("", "")
            listed = " ".join(effective_tools({"allow_takeover": True})["local"])
            self.assertNotIn("subwave_takeover_show", listed)
        finally:
            station_config.admin_credentials = original

    def test_they_refuse_once_the_call_has_spent_its_actions(self):
        import asyncio

        from call.actions import CallActions

        class _Guard:
            def mark_on_air(self, secs, spoken=""):
                pass

        class _Station:
            called = False

            async def skip_track(self):
                _Station.called = True
                return {"ok": True}

        from call.tools import build_on_air_tools

        spent = CallActions(1)
        spent.note("request", "something earlier")
        tools = {t.info.name: t for t in build_on_air_tools(
            {"allow_skip_track": True}, _Station(), spent, _Guard(), guarded=False)}
        out = asyncio.run(tools["subwave_skip_track"]())
        self.assertIn("limit", out.lower())
        self.assertFalse(_Station.called, "the station was called despite the cap")

    def test_sound_effects_and_playlist_rebuilds_are_still_never(self):
        from call.tools.registry import blocked_names

        self.assertEqual(
            set(blocked_names()),
            {"subwave_refresh_playlist", "subwave_list_sfx", "subwave_play_sfx"})


class TestActionsAllHaveAReceipt(unittest.TestCase):
    def test_every_action_a_tool_records_has_a_label(self):
        """The caller's transcript shows a line per action, and the point of
        that line is that the DJ *saying* it did something is a claim while the
        line is the receipt. Two station-wide actions — skipping the record
        everyone is listening to, and firing a programme beat — shipped with no
        label and rendered as a bare "Action completed"."""
        import re

        from call.actions import CallActions

        recorded = set()
        for path in AGENT_WORKER.joinpath("call/tools").glob("*.py"):
            recorded.update(
                re.findall(r"actions\.note\(\s*[\"'](\w+)[\"']", path.read_text())
            )
        self.assertTrue(recorded, "found no actions.note() calls to check")
        self.assertEqual(
            sorted(recorded - set(CallActions.LABELS)), [],
            "an action kind is recorded but has no label, so the caller sees "
            "'Action completed' instead of what actually happened",
        )


class TestStationActionResults(unittest.TestCase):
    """A station action that WORKED must never be reported to the caller as a
    failure. Both halves of that were live bugs: a segment takes longer than a
    read timeout, and some endpoints answer 200 with no JSON."""

    def setUp(self):
        import httpx
        import station
        self.httpx = httpx
        self.station = station

    def test_body_survives_empty_text_and_list_payloads(self):
        import httpx
        make = lambda **kw: httpx.Response(200, **kw)
        self.assertEqual(self.station._body(make(content=b"")), {})
        self.assertEqual(self.station._body(make(text="queued")), {})
        self.assertEqual(self.station._body(make(json={"ok": True})), {"ok": True})
        self.assertEqual(
            self.station._body(make(json=["a", "b"])), {"result": ["a", "b"]}
        )

    def test_read_timeout_is_unconfirmed_not_failed(self):
        # Reached the station, answer never came back — the action has run.
        self.assertTrue(self.station._sent_but_unconfirmed(self.httpx.ReadTimeout("x")))
        self.assertTrue(self.station._sent_but_unconfirmed(self.httpx.PoolTimeout("x")))
        # Never got there at all — that IS a failure.
        self.assertFalse(
            self.station._sent_but_unconfirmed(self.httpx.ConnectTimeout("x")))
        self.assertFalse(self.station._sent_but_unconfirmed(ValueError("x")))

    def test_a_5xx_on_a_request_is_retried_once(self):
        """Real call: the caller asked eight seconds after pickup, the station
        answered 503 because their tune-in hadn't reached its listener count
        yet, and the DJ told them the request failed. A 5xx is transient."""
        import asyncio

        import httpx

        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            # Fails once, then succeeds — the window the retry exists for.
            if len(calls) == 1:
                return httpx.Response(503, text="Service Unavailable")
            return httpx.Response(200, json={"requestId": "abc", "status": "pending"})

        async def run():
            client = self.station.StationClient(base_url="http://station")
            client._client = httpx.AsyncClient(
                base_url="http://station", transport=httpx.MockTransport(handler))
            try:
                return await client.submit_request("something fun")
            finally:
                await client.aclose()

        res = asyncio.run(run())
        self.assertEqual(len(calls), 2, "the 5xx was not retried")
        self.assertEqual(res.get("requestId"), "abc")
        self.assertNotIn("error", res)

    def test_a_4xx_refusal_is_not_retried(self):
        # A real refusal means retrying just repeats it and delays the answer.
        import asyncio

        import httpx

        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(429, text="Too Many Requests")

        async def run():
            client = self.station.StationClient(base_url="http://station")
            client._client = httpx.AsyncClient(
                base_url="http://station", transport=httpx.MockTransport(handler))
            try:
                return await client.submit_request("something fun")
            finally:
                await client.aclose()

        res = asyncio.run(run())
        self.assertEqual(len(calls), 1, "a 4xx should not be retried")
        self.assertIn("error", res)

    def test_action_timeout_is_well_clear_of_the_read_timeout(self):
        self.assertGreaterEqual(self.station.ACTION_TIMEOUT, 30.0)


class TestTellingTheCallerWhenTheirSongPlays(unittest.TestCase):
    """Without the queue position the DJ could only say "soon", which is how a
    caller ends up being told their song is on when it is four tracks away."""

    def test_next_up_is_said_plainly(self):
        from call.tools.music import _when_it_plays

        self.assertIn("next up", _when_it_plays(1))

    def test_a_position_becomes_a_time_range(self):
        from call.tools.music import _when_it_plays

        out = _when_it_plays(4)
        self.assertIn("number 4", out)
        self.assertIn("12-16 minutes", out)

    def test_an_unknown_position_tells_the_dj_not_to_guess(self):
        from call.tools.music import _when_it_plays

        for bad in (None, "soon", ""):
            self.assertIn("don't guess", _when_it_plays(bad))


class TestTheDJDescribesRecordsItHasInformationAbout(unittest.TestCase):
    """The station returns mood tags and an energy score on every hit.
    Dropping them left the DJ describing records purely from the title."""

    def test_moods_and_energy_reach_the_model(self):
        from call.tools.music import _fmt_track

        out = _fmt_track({"title": "Roads", "artist": "Portishead",
                          "moods": ["moody", "nocturnal"], "energy": 0.2})
        self.assertIn("moody", out)
        self.assertIn("nocturnal", out)
        self.assertIn("low energy", out)

    def test_the_id_is_included_only_when_exact_queueing_is_on(self):
        # Without the id in the text the model has nothing to pass to the
        # exact-queue tool and silently falls back to guessing.
        track = {"title": "Roads", "artist": "Portishead", "id": "t-42"}
        from call.tools.music import _fmt_track

        self.assertIn("[id: t-42]", _fmt_track(track, with_id=True))
        self.assertNotIn("t-42", _fmt_track(track, with_id=False))
