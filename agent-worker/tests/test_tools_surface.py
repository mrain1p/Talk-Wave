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
        # Public like the avatar and for the same reason — and like the
        # station's own /cover/:id, which answers unauthenticated listeners.
        # It reads art by track id and writes nothing.
        "GET /cover/{track_id}": "public",
        # The player's listener actions. "public" here means not admin-gated;
        # each self-gates in api/player._door — the player switch must be ON
        # and the caller through the phone's own guest door — and the station
        # side of both is public listener API with its own per-IP limits.
        # Writes, but the station's own page hands the same writes to any
        # listener; the MCP allowlist still owns everything the DJ does.
        "GET /player/like": "public",
        "POST /player/like": "public",
        "POST /player/request": "public",
        # The player's OPERATOR side (2026-09-01). "public" is this column's
        # coarse word for "not admin-header-gated at the door" — every one
        # of these self-gates twice in api/player: the phone's guest door,
        # then the permission matrix against the caller's resolved tier
        # (allow_skip_track / allow_unfavorite / allow_player_commands, all
        # defaulting admin), before anything touches the admin client.
        "GET /player/abilities": "public",
        "POST /player/skip": "public",
        "POST /player/unlike": "public",
        "POST /player/command": "public",
        "GET /player/booth-log": "public",
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
        # Open Lines. Admin on all three, including the status read: it
        # carries the words the DJ is about to be reminded of, and opening
        # or closing puts speech on the broadcast. Nothing here is a door
        # a caller comes through.
        "GET /open-lines": "admin",
        "POST /open-lines/open": "admin",
        "POST /open-lines/close": "admin",
        "GET /open-lines/premises": "admin",
        "POST /open-lines/premises": "admin",
        "POST /open-lines/premises/{premise_id}": "admin",
        "DELETE /open-lines/premises/{premise_id}": "admin",
        # The dashboard's station-override box: read what stands, clear it.
        # Admin both ways — the read names a show before its airtime and the
        # clear cancels an operator's (or a caller's) standing takeover.
        "GET /station/override": "admin",
        "POST /station/override/clear": "admin",
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
        # The soundbite line. "public" is this column's coarseness again:
        # every draft route checks the guest code AND the machine's own tier
        # door (allow_voicemail via tier_reaches, in _draft_gate) — the
        # census only reads the admin gate.
        "POST /voicemail/draft": "public",
        "GET /voicemail/draft/{draft_id}/audio": "public",
        "POST /voicemail/draft/{draft_id}/send": "public",
        "DELETE /voicemail/draft/{draft_id}": "public",
        # The studio's pickup greeting — the same staged clip the machine
        # answers with, behind the same guest gate as the draft routes.
        "GET /vm-greeting": "public",
        # Truly public, by design: the mixer's ONE fetch of a finished clip —
        # it is curl on another network and can hold no password. The
        # unguessable token is the credential; review.py burns it on first
        # claim and expires it in two minutes, so the URL a log leaks is dead.
        "GET /vm-air/{token}": "public",
        # The live relay's clips, same pattern and same defence as /vm-air:
        # the worker writes a turn, the mixer curls it once, the token is the
        # filename and chunks.py expires it in three minutes (onair/chunks.py).
        "GET /on-air/{token}": "public",
        # The broadcast-delay dump — an operator action on a live phone-in.
        "POST /on-air/dump": "admin",
        "DELETE /settings/sounds/{name}": "admin",
        "GET /prompt": "admin",
        "GET /calls": "admin",
        "GET /logs": "admin",
        # The listener series behind the ACTIVITY strip — operator telemetry,
        # gated like the call records and logs it sits beside.
        "GET /stats/listeners": "admin",
        "DELETE /calls": "admin",
        # One record rather than all of them. Same gate as clear-all — a
        # transcript is a caller's words either way.
        "DELETE /calls/{rid}": "admin",
        # The operator's own verdict on a record. Admin, like everything else
        # that touches a transcript — and unlike /call-feedback, which is the
        # caller's thumbs and is deliberately open to the caller.
        "POST /calls/{rid}/mark": "admin",
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
        # Speaks a line with the configured voice and hears it back with the
        # configured ear. Admin like the other two: it spends the STT key.
        "POST /test/stt": "admin",
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
        # A read like the five above it, but served by our wrapper: the
        # station publishes lyrics over public REST, not MCP. Added 0.10.47.
        "subwave_booth_log": "read",
        "subwave_current_lyrics": "read",
        # One way in to the six finders, behind its own switch and OFF until
        # measured (0.98.22). It routes to wrappers already built, so it can
        # never reach a capability the settings withheld — and it is not a
        # new capability itself, which is why it is safe to have at all.
        "subwave_find_music": "single_lookup_tool",
        "subwave_request_song": "allow_requests",
        "subwave_request_status": "allow_requests",
        "subwave_search_library": "allow_library_search",
        # REST-only at the station (/dj/recent, no MCP tool), served by our
        # wrapper. Same switch as search on purpose: both answer "what have
        # you got". Added 0.10.59.
        "subwave_recent_tracks": "allow_library_search",
        # The three discovery tools, added 0.10.104. All REST-only at the
        # station, all reads. They exist because the call line had exactly two
        # ways to find music — a literal word match and a blind resolver — and
        # a caller was told a track was missing that the library held one
        # letter away. Sound search and its neighbours tool share one switch;
        # browse rides library search, because it answers the same question.
        "subwave_search_by_sound": "allow_sound_search",
        "subwave_more_like_this": "allow_sound_search",
        "subwave_browse_library": "allow_library_search",
        # Two reads the station had served all along and the call line never
        # used, taken up on the 2026-08-14 upstream pass. Both ride the
        # library-search switch: "what does this station love" and "what has it
        # already played" are the same "what have you got" question from two
        # more directions, and an operator happy with one has no reason to
        # withhold these.
        "subwave_station_favourites": "allow_library_search",
        "subwave_already_played": "allow_library_search",
        "subwave_queue_track": "allow_exact_queue",
        # Bulk queueing, 0.98.10: a whole album, or a curated run of picks,
        # as ONE action. Their own switch rather than riding the exact queue,
        # because one sentence taking thirty slots is a bigger grant than one
        # taking one — an operator who enabled exact picks must not find an
        # upgrade turned album floods on.
        "subwave_queue_album": "allow_album_queue",
        "subwave_queue_mix": "allow_album_queue",
        # Its undo. The station has had DELETE /dj/queue/:id all along, while
        # the prompt told the DJ a request could never be cancelled — so a
        # caller who changed their mind was told it was impossible. Off by
        # default: the queue is shared, so this can pull someone else's track.
        "subwave_cancel_queued_track": "allow_cancel_queue",
        # Its batch, 0.98.12: bulk OUT mirroring the album's bulk IN — an
        # album took one action to queue and an action per track to unqueue,
        # so the cap made honest cleanup impossible (2026-08-19). Same
        # switch as the single cancel: the same power at batch size.
        "subwave_clear_from_queue": "allow_cancel_queue",
        # The lowest-harm action: a like on the current record, the same heart
        # any listener taps. Public station endpoint, so no admin credentials.
        "subwave_like_track": "allow_favorite",
        # Its admin-only counterpart: un-hearts the operator's own curation
        # like (the public like has none), so station admin creds and admin tier.
        "subwave_unlike_track": "allow_unfavorite",
        # The one PERMANENT thing on a call line: a never-play entry outlives
        # the call, the show and the operator's memory of it, and nothing goes
        # out on air to say it happened. Off by default, admin tier, and the
        # lift shares the switch so a mistake has a way back that doesn't
        # depend on the operator noticing.
        "subwave_never_play_track": "allow_never_play",
        "subwave_allow_track_again": "allow_never_play",
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
        # The same reach as a takeover and quieter — a pinned show announces
        # itself on air, a narrowed playlist doesn't. Built against upstream
        # #1404, which is not in a released station yet: a station without it
        # answers plainly that it can't rather than failing.
        "subwave_genre_lock": "allow_genre_lock",
        "subwave_clear_genre_lock": "allow_genre_lock",
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
        # quietly defaults to on for a STRANGER is how someone else's station
        # starts skipping tracks. Since 0.10.80 the defaults are a tier
        # ladder: the admin caller — the operator's own phone — holds the
        # station-wide switches, and nobody below gets any of them.
        raw = settings_store.load()
        for tier in ("open", "guest"):
            with self.subTest(tier=tier):
                cfg = settings_store.permissions_for(raw, tier)
                self.assertFalse(cfg.get("allow_skip_track"))
                self.assertFalse(cfg.get("allow_dj_segment"))
                self.assertFalse(cfg.get("allow_takeover"))
                self.assertEqual(self._tools(cfg) & self.STATION_WIDE, set())
        admin = settings_store.permissions_for(raw, "admin")
        self.assertTrue(admin.get("allow_skip_track"))
        self.assertEqual(self._tools(admin) & self.STATION_WIDE,
                         self.STATION_WIDE)

    def test_raw_tiers_must_never_reach_a_tool_builder(self):
        # The trap this whole design is arranged around: the stored value is
        # a string, every tier name is truthy, and every tool builder has
        # always asked `cfg.get("allow_x")`. Handing one the UNRESOLVED
        # settings would switch on every station-wide permission for every
        # caller at once — the loudest possible failure, on somebody else's
        # broadcast. So resolving is not a step that can be skipped: this is
        # what says so out loud.
        raw = settings_store.load()
        self.assertEqual(raw["allow_skip_track"], "admin")
        self.assertTrue(bool(raw["allow_skip_track"]),
                        "the point of this test: the raw value IS truthy")
        self.assertEqual(
            self._tools(settings_store.permissions_for(raw, "open"))
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

        # NOT \w+. This scraped with \w+ for four releases, which matches
        # neither the space in "genre lock" nor the hyphen in "never-play" —
        # so the three kinds that actually had no label were the exact three
        # the guard could not see, and both powers shipped rendering as a bare
        # "Action completed". A guard with a hole shaped like the bug is worse
        # than no guard, because it reads as coverage.
        recorded = set()
        for path in AGENT_WORKER.joinpath("call/tools").glob("*.py"):
            recorded.update(
                re.findall(r"actions\.note\(\s*[\"']([^\"']+)[\"']",
                           path.read_text(encoding="utf-8"))
            )
        self.assertTrue(recorded, "found no actions.note() calls to check")
        self.assertEqual(
            sorted(recorded - set(CallActions.LABELS)), [],
            "an action kind is recorded but has no label, so the caller sees "
            "'Action completed' instead of what actually happened",
        )

    def test_every_denial_a_tool_cards_has_a_label(self):
        """Same guard for the denied-card family — a refusal kind with no
        label renders as the fallback, which tells the caller nothing about
        what was refused. Scraped over all of call/, because the withheld
        watcher cards from outside call/tools/."""
        import re

        from call.actions import CallActions

        carded = set()
        for path in AGENT_WORKER.joinpath("call").rglob("*.py"):
            carded.update(re.findall(
                r"\.denied\(\s*[\"']([^\"']+)[\"']",
                path.read_text(encoding="utf-8")))
        self.assertTrue(carded, "found no denied() calls to check")
        self.assertEqual(sorted(carded - set(CallActions.LABELS)), [])


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
    """The station returns mood tags and an energy word on every hit.
    Dropping them left the DJ describing records purely from the title."""

    def test_moods_and_energy_reach_the_model(self):
        from call.tools.music import _fmt_track

        # 'low' — a WORD, which is what the station sends. The float this used
        # to pass was invented by the fixture, and the formatter handled only
        # floats, so the field was dropped from every real row while both
        # tests that covered it stayed green (2026-08-14).
        out = _fmt_track({"title": "Roads", "artist": "Portishead",
                          "moods": ["moody", "nocturnal"], "energy": "low"})
        self.assertIn("moody", out)
        self.assertIn("nocturnal", out)
        self.assertIn("low energy", out)

    def test_inherited_feel_tags_are_hedged_as_a_guess(self):
        # `source: 'propagated'` marks moods inherited from embedding
        # neighbours — upstream's "guesses built on guesses", 41% of a real
        # library — and the DJ was reading them with per-track confidence.
        # Real per-track judgements (llm/manual/uncertain-llm) stay unhedged.
        from call.tools.music import _fmt_track

        row = {"title": "Weesnaawwww", "artist": "$ilkMoney",
               "moods": ["energetic", "driving"], "energy": "high"}
        hedged = _fmt_track(dict(row, source="propagated"))
        self.assertIn("inherited", hedged)
        self.assertIn("guess", hedged)
        for source in ("llm", "manual", "uncertain-llm", None):
            out = _fmt_track(dict(row, source=source))
            self.assertNotIn("inherited", out, f"hedged a real judgement: {source}")

    def test_the_id_is_included_only_when_exact_queueing_is_on(self):
        # Without the id in the text the model has nothing to pass to the
        # exact-queue tool and silently falls back to guessing.
        track = {"title": "Roads", "artist": "Portishead", "id": "t-42"}
        from call.tools.music import _fmt_track

        self.assertIn("[id: t-42]", _fmt_track(track, with_id=True))
        self.assertNotIn("t-42", _fmt_track(track, with_id=False))


class TestTheCapAnnouncesItselfAsACard(unittest.TestCase):
    """The operator's ask (2026-08-19): when the per-call cap refuses, the
    caller sees an OFFICIAL card say so — not only a DJ who, on the chat
    that asked for this, dressed the cap as the scheduler fighting him and
    claimed pulls the ledger had refused. Once per call: that chat hit the
    cap four times in twenty seconds, and four identical warnings would
    bury the one that matters."""

    def _spent(self):
        from call.actions import CallActions

        spent = CallActions(1)
        spent.note("request", "earlier")
        return spent

    def test_the_first_refusal_carries_the_card_and_the_rest_stay_quiet(self):
        spent = self._spent()
        cards = []
        spent.on_note = cards.append
        first = spent.refusal()
        spent.refusal()
        spent.refusal()
        self.assertIn("limit", first)
        limit_cards = [c for c in cards if c.get("kind") == "limit"]
        self.assertEqual(len(limit_cards), 1, "the cap card must fire exactly once")
        self.assertEqual(limit_cards[0]["label"], "Call limit reached")
        self.assertIn("no more", limit_cards[0]["detail"])

    def test_the_card_is_not_an_action(self):
        # It announces that nothing more will happen — counting it, or
        # listing it among things the caller made happen, would both lie.
        spent = self._spent()
        spent.on_note = lambda c: None
        before_count, before_taken = spent.count, list(spent.taken)
        spent.refusal()
        self.assertEqual(spent.count, before_count)
        self.assertEqual(spent.taken, before_taken)

    def test_the_model_is_told_the_card_is_already_public(self):
        # The refusal text is the model's only steer at that moment; naming
        # the card is what makes a contradicting story a visible lie.
        spent = self._spent()
        self.assertIn("CALL LIMIT REACHED card", spent.refusal())


class TestARefusalIsACardTheDJCannotSpin(unittest.TestCase):
    """The receipt channel's other half (see CallActions.denied): what was
    refused, on screen, in the station's own words. Every kind here maps to
    a narrated invention on a real call — "the queue's jammed" for a rate
    limit (2026-08-13), "stubborn with the queue" for a withheld mix
    (2026-08-22), "it's an instrumental" for a missing lyrics feature
    (2026-08-20)."""

    def test_a_denial_cards_with_its_reason_and_costs_nothing(self):
        from call.actions import CallActions

        a = CallActions(5)
        cards = []
        a.on_note = cards.append
        a.denied("refused", "rate limited: one request per 20s")
        self.assertEqual(a.count, 0)
        self.assertEqual(a.taken, [])
        self.assertEqual(cards[0]["kind"], "refused")
        self.assertEqual(cards[0]["label"], "The station refused that")
        self.assertIn("20s", cards[0]["detail"])

    def test_the_same_refusal_repeated_cards_once(self):
        # The 2026-08-13 burst sent four identical requests in one turn and
        # collected four identical refusals — that is one fact, not four.
        from call.actions import CallActions

        a = CallActions(5)
        cards = []
        a.on_note = cards.append
        for _ in range(4):
            a.denied("refused", "rate limited")
        self.assertEqual(len(cards), 1)

    def test_a_different_refusal_still_cards(self):
        from call.actions import CallActions

        a = CallActions(5)
        cards = []
        a.on_note = cards.append
        a.denied("refused", "rate limited")
        a.denied("refused", "on the never-play blocklist")
        self.assertEqual(len(cards), 2)


class TestTheTwoMouthsShareOneSurface(unittest.TestCase):
    """The operator's parity rule (2026-08-27): the call and the text line
    run the same conversation, so their tool surfaces may only diverge
    where a reason is written down. That evening's text exchange is what a
    silent fork costs: the chat had no station reads at all — nobody had
    decided that, they had just never been built — and the DJ answered
    queue questions from guesses for twenty minutes, then invented a
    station rule to explain a duplicate it could not see. Same shape as
    the size ledgers: measure the real thing, subtract the written-down
    exceptions, fail on anything left over."""

    # Tool FAMILIES one mouth builds and the other does not, each with the
    # reason it may stay that way. Adding a builder to one session only:
    # either add it to the other, or write its reason here.
    JUSTIFIED_BUILDERS = {
        "build_call_control_tools":
            "call only — end_call puts a receiver down, and a text line "
            "has no receiver",
    }

    # MCP-served reads the text line does NOT yet serve locally. Removing
    # a name from here means its local twin now exists in
    # call/tools/reads.py; adding an MCP read to the registry forces a
    # decision about its chat story instead of a silent fork.
    STILL_MCP_ONLY_ON_CHAT = {
        "subwave_health":
            "liveness probe — a dead station fails the chat's own tools "
            "visibly, which is the same information",
        "subwave_schedule":
            "the prompt briefing already carries the schedule to both "
            "mouths",
        "subwave_session":
            "the on-air transcript feed; no typed exchange has needed it "
            "yet",
        "subwave_list_skills":
            "the runnable skills already reach the prompt via the "
            "briefing's skills narrowing",
    }

    def _builders(self, rel):
        import re

        src = (AGENT_WORKER / rel).read_text(encoding="utf-8")
        return set(re.findall(r"\b(build_\w+_tools)\(", src))

    def test_builder_families_diverge_only_with_a_reason(self):
        call = self._builders("call/session.py")
        chat = self._builders("chat/session.py")
        unexplained = (call ^ chat) - set(self.JUSTIFIED_BUILDERS)
        self.assertFalse(
            unexplained,
            f"one mouth builds {sorted(unexplained)} and the other does "
            "not, and no reason is written down — add it to the other "
            "mouth, or justify the split in JUSTIFIED_BUILDERS")
        stale = set(self.JUSTIFIED_BUILDERS) - (call ^ chat)
        self.assertFalse(
            stale,
            f"{sorted(stale)} no longer diverge — delete the stale "
            "justification so the table stays the truth")

    def test_every_mcp_read_has_a_chat_story(self):
        from unittest import mock

        from call.tools import reads
        from call.tools.registry import MCP, TOOLS

        mcp_names = {t.name for t in TOOLS if t.served == MCP}
        with mock.patch.object(reads, "library_search_needs_mcp",
                               return_value=False):
            chat_local = {t.info.name
                          for t in reads.build_read_tools(
                              {"allow_requests": True}, object())}
        unexplained = mcp_names - chat_local - set(self.STILL_MCP_ONLY_ON_CHAT)
        self.assertFalse(
            unexplained,
            f"MCP reads {sorted(unexplained)} reach a call and have no "
            "chat story — build the local twin in call/tools/reads.py, or "
            "write the reason in STILL_MCP_ONLY_ON_CHAT")
        stale = set(self.STILL_MCP_ONLY_ON_CHAT) & chat_local
        self.assertFalse(
            stale,
            f"{sorted(stale)} now have local twins — delete the stale "
            "entries so the table stays the truth")
        # And the twins must be real MCP names, not inventions.
        self.assertTrue(chat_local <= mcp_names, chat_local - mcp_names)


class TestABlindCallGetsTheChatsEyes(unittest.TestCase):
    """0.99.1: a call whose MCP handshake DECISIVELY failed used to attach
    the dead toolset anyway — the SDK retries once, swallows the second
    failure, and the call proceeds with zero MCP tools while the prompt
    still promises the reads. Now that call builds the same local read
    twins the chat runs on, and never both, so no name is served twice.
    Pinned at the source: start() is unbuildable in a unit test (it needs
    a room, a worker and three live providers — test_call_flow.py says
    so), but the branch's load-bearing pieces are namable."""

    def _start_src(self):
        import inspect

        from call.session import CallSession

        return inspect.getsource(CallSession.start)

    def test_the_fallback_branch_exists_and_is_recorded(self):
        src = self._start_src()
        # The decisive signal: a finished warm whose MCP session never
        # opened — not the warm task's exception, which is always None.
        self.assertIn("self.station_tools.initialized", src)
        self.assertIn("build_read_tools(", src)
        # The record says which route served the station this call.
        self.assertIn('"stationTools"', src)
        self.assertIn('"local-fallback"', src)

    def test_never_both_routes_at_once(self):
        # The toolset and the twins reuse the same tool names; the branch
        # must be exclusive — the toolset only on the ready path.
        src = self._start_src()
        ready = src.index("station_ready")
        self.assertLess(ready, src.index("MCPToolset("))
        self.assertLess(ready, src.index("build_read_tools("))
