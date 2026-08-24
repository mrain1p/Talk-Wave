"""What a tool does once the DJ calls it, and what it may claim afterwards.

Split out of test_sidecar.py; see tests/__init__.py.
"""

from __future__ import annotations

import asyncio
import os
import unittest
import settings as settings_store
from tests.support import _TempStores


class TestMainToolLogic(_TempStores):
    """Imports main.py (heavy: LiveKit plugins) — kept in one class so the
    cost is paid once and the rest of the suite stays fast."""

    @classmethod
    def setUpClass(cls):
        import main  # noqa: F401  (import cost only)
        from call import actions, air
        from call import providers
        from call.tools import control, music, registry

        cls.main = main
        cls.providers = providers
        cls.actions = actions
        cls.air = air
        cls.music = music
        cls.control = control
        cls.registry = registry

    def test_query_variants_strips_the_by_connector(self):
        v = self.music._query_variants("Let It Be by The Beatles")
        self.assertEqual(v[0], "Let It Be by The Beatles")
        self.assertIn("Let It Be The Beatles", v)
        self.assertIn("Let It Be", v)

    def test_query_variants_keeps_titles_containing_by(self):
        v = self.music._query_variants("Stand by Me by Ben E. King")
        self.assertIn("Stand by Me", v)  # rightmost split only

    def test_guarded_list_drops_on_air_tools_but_keeps_reads(self):
        cfg = {"allow_announcements": True, "allow_skills": True}
        allowed = self.registry.mcp_allowlist(cfg)
        self.assertNotIn("subwave_dj_announce", allowed)
        self.assertNotIn("subwave_run_skill", allowed)
        self.assertIn("subwave_list_skills", allowed)
        self.assertIn("subwave_now_playing", allowed)

    def test_wrapped_tools_never_served_raw(self):
        # Requests are always wrapped. Library search is wrapped only when the
        # wrapper can actually work — see the credential test below.
        import station_config

        cfg = {"allow_requests": True, "allow_library_search": True}
        original = station_config.admin_credentials
        try:
            station_config.admin_credentials = lambda: ("dj", "secret")
            allowed = self.registry.mcp_allowlist(cfg)
            self.assertNotIn("subwave_request_song", allowed)
            self.assertNotIn("subwave_search_library", allowed)
            et = self.registry.effective_tools({**cfg, "avoid_on_air_overlap": True})
            local_names = " ".join(et["local"])
            self.assertIn("subwave_request_song", local_names)
            self.assertIn("subwave_search_library", local_names)
        finally:
            station_config.admin_credentials = original

        # Requests stay wrapped with or without credentials — that wrapper
        # uses a public endpoint and works either way.
        self.assertNotIn("subwave_request_song", self.registry.mcp_allowlist(cfg))

    def test_action_ledger_caps_a_single_call(self):
        actions = self.actions.CallActions(2)
        self.assertFalse(actions.at_limit())
        actions.note("request", "a track")
        self.assertFalse(actions.at_limit())
        actions.note("skill", "weather")
        self.assertTrue(actions.at_limit())
        # The refusal is a steer for the DJ, not an error to read out.
        refusal = actions.refusal()
        self.assertNotIn("error", refusal.lower())
        self.assertIn("ring back", refusal)

    def test_action_ledger_zero_means_unlimited(self):
        actions = self.actions.CallActions(0)
        for _ in range(20):
            actions.note("request", "x")
        self.assertFalse(actions.at_limit())

    def test_every_action_kind_has_a_card_label(self):
        # A new action type shipping with no label would render as a blank
        # line in the caller's transcript.
        for kind in ("request", "announcement", "skill"):
            self.assertIn(kind, self.actions.CallActions.LABELS)

    def test_on_air_tools_are_never_served_raw_over_mcp(self):
        # MCP's session timeout is shorter than a segment takes to run, which
        # turned a segment that was audibly playing into "that didn't work".
        cfg = {"allow_announcements": True, "allow_skills": True}
        for guarded in (False, True):
            allowed = self.registry.mcp_allowlist(cfg)
            self.assertNotIn("subwave_dj_announce", allowed)
            self.assertNotIn("subwave_run_skill", allowed)
            self.assertIn("subwave_list_skills", allowed)
            local = " ".join(self.registry.effective_tools(
                {**cfg, "avoid_on_air_overlap": guarded})["local"])
            self.assertIn("subwave_dj_announce", local)
            self.assertIn("subwave_run_skill", local)

    def test_on_air_guard_is_a_no_op_when_the_toggle_is_off(self):
        import asyncio

        guard = self.air.OnAirGuard(None, {"avoid_on_air_overlap": False,
                                            "on_air_quiet_secs": 30})
        guard._clear.clear()          # even with the gate shut…
        self.assertEqual(asyncio.run(guard.wait_until_clear()), 0.0)
        # …and the watcher must not poll the station at all.
        asyncio.run(guard.watch(None))

    def test_on_air_guard_releases_rather_than_holding_forever(self):
        # A stale djLog entry must never strand a caller in silence: past the
        # cap the call carries on, overlap or not.
        import asyncio

        guard = self.air.OnAirGuard(None, {"avoid_on_air_overlap": True,
                                            "on_air_quiet_secs": 30})
        guard._clear.clear()
        waited = asyncio.run(guard.wait_until_clear(timeout=0.05))
        self.assertGreater(waited, 0)
        self.assertTrue(guard._clear.is_set())

    def test_firing_an_on_air_action_closes_the_gate_at_once(self):
        # Observed: the DJ said it was going off to air something, aired it,
        # then talked over its own delivery. The guard only closed when the
        # station's log showed speech, and it polls every 4s — so there was a
        # window where the DJ believed the air was clear. We know it isn't,
        # because we are the ones who just made it busy.
        import asyncio

        guard = self.air.OnAirGuard(None, {"avoid_on_air_overlap": True,
                                           "on_air_quiet_secs": 30})
        self.assertTrue(guard._clear.is_set())
        guard.mark_on_air(seconds=30)
        self.assertFalse(guard._clear.is_set())
        self.assertTrue(guard.on_air)
        # …and a reply now waits rather than going out over the announcement.
        waited = asyncio.run(guard.wait_until_clear(timeout=0.05))
        self.assertGreater(waited, 0)

    def test_marking_on_air_is_inert_when_the_guard_is_off(self):
        import asyncio

        guard = self.air.OnAirGuard(None, {"avoid_on_air_overlap": False,
                                           "on_air_quiet_secs": 30})
        guard.mark_on_air()
        self.assertEqual(asyncio.run(guard.wait_until_clear()), 0.0)

    def test_on_air_guard_clear_air_costs_nothing(self):
        import asyncio

        guard = self.air.OnAirGuard(None, {"avoid_on_air_overlap": True,
                                            "on_air_quiet_secs": 30})
        self.assertEqual(asyncio.run(guard.wait_until_clear()), 0.0)

    def test_random_persona_sentinel_is_shared(self):
        # The worker and the panel must agree on the spelling or the option
        # silently falls through to "whoever is live".
        self.assertEqual(settings_store.RANDOM_PERSONA, "__random__")
        self.assertNotIn(settings_store.RANDOM_PERSONA,
                         settings_store.load()["persona_override"])

    def test_blocked_tools_are_unreachable_at_every_setting(self):
        """The claim the panel makes to the operator: these are never exposed,
        whatever you switch on. Now enforceable directly, because one table
        describes both the block and the allowlist."""
        every_gate_on = {
            t.gate: True for t in self.registry.TOOLS
            if t.gate not in (self.registry.READ, self.registry.NEVER)
        }
        allowed = set(self.registry.mcp_allowlist(every_gate_on))
        local = set(self.registry.local_tool_names(every_gate_on))
        for name in self.registry.blocked_names():
            self.assertNotIn(name, allowed, f"{name} is claimed to be blocked")
            self.assertNotIn(name, local, f"{name} is claimed to be blocked")

    def test_the_catalogue_is_the_allowlist(self):
        """What the panel prints and what the worker serves come from the same
        table. This used to need a reconciliation test across two hand-kept
        lists; now it is a property of there being one list."""
        catalogue = {t["name"]: t["gate"] for t in self.registry.catalogue()}
        self.assertEqual(len(catalogue), len(self.registry.TOOLS))
        for tool in self.registry.TOOLS:
            self.assertEqual(catalogue[tool.name], tool.gate)
            # Every tool says something useful about itself, or the panel
            # renders a blank row.
            self.assertTrue(tool.what.strip(), tool.name)
            # A tool is either served or blocked — never gated but unservable.
            if tool.gate == self.registry.NEVER:
                self.assertEqual(tool.served, self.registry.NONE, tool.name)
            else:
                self.assertIn(tool.served,
                              (self.registry.MCP, self.registry.LOCAL), tool.name)

    def test_the_panel_never_claims_a_tool_that_will_not_be_built(self):
        # Caught by the registry refactor: without station credentials the
        # exact-queue wrapper is never built (it needs the ids only the
        # credentialed search returns), but the panel still listed it as
        # available. What the panel reports and what the worker builds have to
        # be the same set.
        import station_config

        cfg = {"allow_library_search": True, "allow_exact_queue": True}
        original = station_config.admin_credentials
        try:
            for creds, expected in ((("dj", "s"), True), (("", ""), False)):
                station_config.admin_credentials = lambda c=creds: c
                reported = "subwave_queue_track" in " ".join(
                    self.registry.effective_tools(cfg)["local"])
                built = "subwave_queue_track" in [
                    t.info.name for t in self.music.build_library_tools(
                        cfg, None, self.actions.CallActions(0))]
                self.assertEqual(reported, expected)
                self.assertEqual(built, expected)
                self.assertEqual(reported, built)
        finally:
            station_config.admin_credentials = original

    def test_every_gate_is_a_real_setting(self):
        # A typo in a gate name would silently disable a tool forever: the
        # lookup just returns None and the tool never appears.
        for tool in self.registry.TOOLS:
            if tool.gate in (self.registry.READ, self.registry.NEVER):
                continue
            self.assertIn(tool.gate, settings_store.FIELDS, tool.name)

    def test_reads_need_no_permission_and_locals_are_never_raw(self):
        # Reads split by who serves them: the MCP reads make up the whole
        # allowlist with nothing switched on, and the locally-served read
        # (lyrics — the station publishes it over REST, not MCP) is offered
        # by the wrapper side under the same no-permission rule.
        reads_over_mcp = [t.name for t in self.registry.TOOLS
                          if t.gate == self.registry.READ
                          and t.served == self.registry.MCP]
        allowed = self.registry.mcp_allowlist({})     # nothing switched on
        self.assertEqual(sorted(allowed), sorted(reads_over_mcp))
        self.assertIn("subwave_current_lyrics",
                      self.registry.local_tool_names({}))

        # A locally-wrapped tool must not also be offered over MCP, or the
        # model could reach the version without our guards and retries.
        everything = {t.gate: True for t in self.registry.TOOLS
                      if t.gate not in (self.registry.READ, self.registry.NEVER)}
        served_local = set(self.registry.local_tool_names(everything))
        served_mcp = set(self.registry.mcp_allowlist(everything))
        self.assertFalse(served_local & served_mcp)

    def test_library_search_falls_back_to_mcp_without_credentials(self):
        # The local wrapper reads an admin-only endpoint, so with no station
        # credentials it can only ever return nothing — which reaches the
        # caller as "not in the racks" for a track the library holds. The MCP
        # tool needs no auth, so it must take over.
        import station_config

        cfg = {"allow_library_search": True}
        original = station_config.admin_credentials
        try:
            station_config.admin_credentials = lambda: ("", "")
            self.assertTrue(self.registry.library_search_needs_mcp())
            self.assertIn("subwave_search_library", self.registry.mcp_allowlist(cfg))
            # The lyrics read is the one local tool credentials don't gate —
            # the station serves it public — so it is all that's left here.
            self.assertEqual(
                [t.info.name for t in self.music.build_library_tools(
                    cfg, None, self.actions.CallActions(0))],
                ["subwave_current_lyrics"])

            station_config.admin_credentials = lambda: ("dj", "secret")
            self.assertFalse(self.registry.library_search_needs_mcp())
            self.assertNotIn("subwave_search_library", self.registry.mcp_allowlist(cfg))
            # With credentials the recently-added read rides along — same
            # switch, same credentials, and no MCP tool to stand in for it.
            self.assertEqual(
                [t.info.name for t in self.music.build_library_tools(
                    cfg, None, self.actions.CallActions(0))],
                ["subwave_current_lyrics", "subwave_search_library",
                 "subwave_recent_tracks"])
        finally:
            station_config.admin_credentials = original

    def test_a_described_vibe_is_not_sent_to_the_name_search(self):
        # The call that prompted this: "find me some fun songs" was searched
        # by name and came back with "Fun, Fun, Fun" by The Beach Boys.
        for q in ("fun", "something fun", "find me some fun songs", "upbeat",
                  "chilled", "something for a rainy night", "party music",
                  "anything happy"):
            self.assertTrue(self.music.looks_like_a_vibe(q), q)

    def test_the_stations_own_request_slip_phrases_route_to_the_picker(self):
        # The station's request drawer offers these as one-tap examples, so a
        # caller will say them out loud. They must not become name searches.
        for q in ("sustained energy vibes", "surprise me", "more like this",
                  "something calm for a rainy evening"):
            self.assertTrue(self.music.looks_like_a_vibe(q), q)

    def test_real_track_names_are_never_mistaken_for_a_vibe(self):
        # Conservative on purpose — a false positive here refuses a search the
        # caller actually wanted.
        for q in ("Fun House by The Stooges", "Mr. Blue Sky", "Bowie",
                  "Sunny Afternoon by The Kinks", "Nightcall", "Kavinsky",
                  "Slow Hands by Interpol", "Party Hard Andrew WK"):
            self.assertFalse(self.music.looks_like_a_vibe(q), q)

    def test_search_results_carry_the_stations_mood_data(self):
        # The station returns moods and energy on every hit; dropping them left
        # the DJ describing a record purely from its title.
        #
        # `energy` is the SHAPE THE STATION ACTUALLY SENDS: a word, one of
        # low/medium/high. This test used to pass a float, and the formatter
        # only handled floats — so it went green for two months while the field
        # was silently dropped from every real row. A fixture the station has
        # never produced proves nothing about the station (2026-08-14).
        out = self.music._fmt_track({
            "title": "Open Eye Signal", "artist": "Jon Hopkins",
            "moods": ["hypnotic", "nocturnal"], "energy": "high",
        })
        self.assertIn("hypnotic", out)
        self.assertIn("nocturnal", out)
        self.assertIn("high energy", out)
        # A number is still read, for a hand-built row or an older station —
        # dropping the field on the way back in would be the same bug again.
        self.assertIn("low energy", self.music._fmt_track(
            {"title": "T", "artist": "A", "energy": 0.2}))
        # And stays clean when the station sends nothing.
        self.assertNotIn("energy", self.music._fmt_track({"title": "T", "artist": "A"}))

    def test_the_analysis_columns_reach_the_dj(self):
        # bpm and key ride every search, recent and browse row (the station
        # merges its library index into all three), and they are the DJ's own
        # vocabulary — "same tempo, in the relative minor" is a statement
        # rather than a claim. They used to be read on neighbour rows only.
        out = self.music._fmt_track({
            "title": "Hammer Orchid", "artist": "Will Slater",
            "bpm": 152.4, "musicalKey": "Am", "instrumental": True,
        })
        self.assertIn("152 bpm", out)
        self.assertIn("Am", out)
        self.assertIn("instrumental", out)

    def test_a_never_play_row_can_never_read_as_available(self):
        # Belt and braces behind the filtering: the tools drop blocked rows
        # before the DJ sees them, but anything that slips through must not
        # look queueable, because the queue gate answers 409 and the DJ will
        # already have promised it.
        out = self.music._fmt_track({
            "title": "Banned", "artist": "X",
            "blockedBy": {"kind": "rule", "field": "genre",
                          "label": "no Christmas in July", "seasonal": True},
        })
        self.assertIn("NEVER-PLAY", out)
        self.assertIn("no Christmas in July", out)
        self.assertIn("cannot be queued", out)

    def test_the_hangup_floor_is_configurable(self):
        # Asked for directly: "is there a setting for it?" There wasn't. The
        # default still guards, but an operator can move or remove it.
        import asyncio, time

        from version import APP_VERSION  # noqa: F401  (import sanity)

        # A shorter floor lets a call end sooner...
        tools = self.control.build_call_control_tools(
            None, lambda: None, time.time() - 20, min_call_secs=10)
        self.assertIn("the line is closing", asyncio.run(tools[0]()).lower())

        # ...and 0 removes the guard entirely.
        tools = self.control.build_call_control_tools(
            None, lambda: None, time.time(), min_call_secs=0)
        self.assertIn("the line is closing", asyncio.run(tools[0]()).lower())

        # A longer floor still refuses.
        tools = self.control.build_call_control_tools(
            None, lambda: None, time.time() - 60, min_call_secs=180)
        self.assertIn("can't close for another", asyncio.run(tools[0]()))

    def test_the_default_floor_is_still_a_minute(self):
        cfg = settings_store.load()
        self.assertEqual(int(cfg.get("min_call_seconds")), 60)

    def test_the_dj_cannot_hang_up_in_the_first_minute(self):
        # A model that decides to end the call early is worse than one that
        # lingers, so this floor is enforced in code, not asked for in a prompt.
        import asyncio, time

        tools = self.control.build_call_control_tools(None, lambda: None, time.time())
        end_call = tools[0]
        self.assertEqual(end_call.info.name, "end_call")
        out = asyncio.run(end_call(reason="done"))
        self.assertIn("can't close for another", out)
        # It must refuse the TIMING without inviting a new conversation at
        # someone who has just said goodbye — that was the observed failure.
        self.assertIn("not a disagreement about the goodbye", out)
        self.assertIn("Do NOT open a new subject", out)

    def test_ending_a_settled_call_is_allowed_once(self):
        import asyncio, time

        # A call that has been running a while: the tool arms the close and
        # asks for a sign-off rather than cutting the audio dead.
        tools = self.control.build_call_control_tools(None, lambda: None, time.time() - 600)
        end_call = tools[0]
        first = asyncio.run(end_call(reason="caller said goodbye"))
        self.assertIn("the line is closing", first.lower())
        # The sign-off is spoken in the same turn as the tool call, so asking
        # for one here made the caller hear a second, different farewell every
        # time — observed on a scripted run against the live deployment.
        self.assertIn("Do not say it again", first)
        self.assertIn("two or three words", first)
        # A second call must not stack another close task.
        second = asyncio.run(end_call(reason="again"))
        self.assertIn("Already wrapping up", second)

    def test_the_wrap_up_actually_hangs_up(self):
        """Shipped broken: the tool is named end_call, which shadowed the
        imported end_call helper, so the close raised TypeError inside a
        background task. The DJ said goodbye and the line stayed open until the
        idle watcher gave up. Nothing surfaced it — the exception died in a
        task nobody awaited."""
        import asyncio, time

        deleted = {}

        class FakeRoomApi:
            async def delete_room(self, req):
                deleted["room"] = getattr(req, "room", "?")

        class FakeCtx:
            room = type("R", (), {"name": "callin-test"})()
            api = type("A", (), {"room": FakeRoomApi()})()
            def shutdown(self, reason=""):
                deleted["shutdown"] = reason

        # A session that actually says its sign-off and then stops. `None`
        # would do for the shadowing bug above, but the close now WAITS for
        # the goodbye to be heard — see hangup.await_sign_off — so a fake with
        # no agent_state waits out the whole grace period before hanging up,
        # which is right behaviour and a useless test.
        class FakeSession:
            def __init__(self):
                self.reads = 0

            @property
            def agent_state(self):
                self.reads += 1
                return "speaking" if self.reads <= 6 else "listening"

        async def run():
            tools = self.control.build_call_control_tools(
                FakeCtx(), FakeSession, time.time() - 600)
            said = await tools[0](reason="caller said goodbye")
            self.assertIn("the line is closing", said.lower())
            # The close runs in the background; give it room to finish.
            for _ in range(60):
                await asyncio.sleep(0.1)
                if "shutdown" in deleted:
                    break

        asyncio.run(run())
        self.assertEqual(deleted.get("room"), "callin-test", "the room was never deleted")
        self.assertIn("wrapped up", deleted.get("shutdown", ""))

    def test_queue_position_becomes_something_a_dj_can_say(self):
        # Without this the DJ could only say "soon", which is how a caller
        # gets told their song is on when it is four tracks away.
        self.assertIn("next up", self.music._when_it_plays(1))
        self.assertIn("next up", self.music._when_it_plays(0))
        third = self.music._when_it_plays(3)
        self.assertIn("number 3", third)
        self.assertIn("9-12 minutes", third)
        # No position from the station means no guessing.
        for missing in (None, "", "soon"):
            self.assertIn("don't guess", self.music._when_it_plays(missing))

    def test_exact_queue_needs_search_and_credentials(self):
        import station_config

        original = station_config.admin_credentials
        try:
            # No credentials: the wrapper can't read ids, so the tool is absent
            # rather than present and broken.
            station_config.admin_credentials = lambda: ("", "")
            cfg = {"allow_library_search": True, "allow_exact_queue": True}
            names = [t.info.name for t in self.music.build_library_tools(
                cfg, None, self.actions.CallActions(0))]
            self.assertNotIn("subwave_queue_track", names)

            station_config.admin_credentials = lambda: ("dj", "secret")
            names = [t.info.name for t in self.music.build_library_tools(
                cfg, None, self.actions.CallActions(0))]
            self.assertIn("subwave_queue_track", names)
            self.assertIn("subwave_search_library", names)

            # Off by default, and never served raw over MCP.
            off = {"allow_library_search": True}
            names = [t.info.name for t in self.music.build_library_tools(
                off, None, self.actions.CallActions(0))]
            self.assertNotIn("subwave_queue_track", names)
            self.assertNotIn("subwave_queue_track",
                             self.registry.mcp_allowlist({**cfg, "allow_requests": True}))
        finally:
            station_config.admin_credentials = original

    def test_search_results_carry_ids_only_when_they_can_be_used(self):
        # The id is noise in the transcript unless something can act on it.
        with_id = self.music._fmt_track({"title": "T", "artist": "A", "id": "x1"}, with_id=True)
        self.assertIn("x1", with_id)
        self.assertNotIn("x1", self.music._fmt_track({"title": "T", "artist": "A", "id": "x1"}))

    def test_a_relative_adapter_name_resolves_from_anywhere(self):
        """This one shipped broken and every call crashed before the DJ spoke.

        build_tts resolved the adapter against AGENT_WORKER, which
        worked only because it happened to live next to tts-adapters/. Moving
        it into call/ during the refactor silently stopped resolving, and the
        job died on FileNotFoundError. Nothing tested build_tts at all.
        """
        import tts_adapter

        names = [p.name for p in tts_adapter.ADAPTER_DIR.glob("*.json")]
        self.assertTrue(names, "no adapter configs shipped — cannot test resolution")

        for name in names:
            resolved = tts_adapter.ADAPTER_DIR / name
            self.assertTrue(resolved.exists(), name)

        # The real call path: a bare filename must become a readable file.
        built = self.providers.build_tts(
            {"tts_mode": "local", "tts_adapter": names[0], "tts_base_url": "http://x"},
            "some-voice",
        )
        self.assertIsNotNone(built)

    def test_every_shipped_adapter_config_is_loadable(self):
        # A malformed adapter would fail the same way — at call time, in front
        # of a caller, rather than here.
        import tts_adapter

        for path in tts_adapter.ADAPTER_DIR.glob("*.json"):
            cfg = tts_adapter.load_adapter(str(path))
            self.assertIn("response", cfg, path.name)
            self.assertIn("audio", cfg, path.name)
            # Discovery is part of the contract too — an adapter with no path
            # to ask for voices silently disables the check that keeps a call
            # from requesting a voice the backend does not have.
            self.assertTrue(cfg.get("voices_path"), path.name)

    def test_effective_stt_falls_back_without_keys(self):
        # The last resort is the built-in Whisper since 0.10.86 — the old
        # google fallback assumed application-default credentials that no
        # fresh install has, and a real deployment's STT stage failed with
        # an ADC lecture while a working Whisper sat in the container.
        provider, model, note = self.providers.effective_stt({"stt_provider": "deepgram"})
        self.assertEqual(provider, "local")  # no deepgram or openai key set
        self.assertIn("built-in Whisper", note)
        os.environ["OPENAI_API_KEY"] = "sk-x"
        provider, model, note = self.providers.effective_stt({"stt_provider": "deepgram"})
        self.assertEqual(provider, "openai")
        self.assertEqual(model, "gpt-4o-mini-transcribe")

    def test_google_stt_needs_service_account_not_the_gemini_key(self):
        # The trap by name: a saved Gemini key makes 'google' appear in the
        # provider list, but Google STT authenticates with a SERVICE ACCOUNT.
        # Without one the pick must land on Whisper and the note must say
        # which credential is actually missing.
        old = os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
        try:
            provider, model, note = self.providers.effective_stt(
                {"stt_provider": "google"})
            self.assertEqual(provider, "local")
            self.assertIn("GOOGLE_APPLICATION_CREDENTIALS", note)
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/tmp/sa.json"
            provider, _, note = self.providers.effective_stt(
                {"stt_provider": "google"})
            self.assertEqual(provider, "google")
        finally:
            if old is None:
                os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
            else:
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = old

    def test_effective_stt_rejects_cross_provider_model(self):
        provider, model, _ = self.providers.effective_stt(
            {"stt_provider": "local", "stt_model": "nova-3"}
        )
        self.assertEqual(provider, "local")
        self.assertEqual(model, "base.en")  # nova-3 is not a local model


class TestAnUnconfirmedDeliveryDoesNotStartAClock(unittest.TestCase):
    """broadcast.py's wiring for the Ash overlap (2026-08-09): a confirmed
    announce gets the hold sized from its words, a slow-to-confirm one gets
    the PENDING hold — the station accepted it but had not aired it, so a
    countdown from the tool's return measures the wrong thing, and the DJ
    read the wrong number out loud ("about twelve seconds") before talking
    over the delivery."""

    class _Guard:
        def __init__(self):
            self.on_air_calls = []
            self.pending_calls = []

        def mark_on_air(self, secs=None, spoken=""):
            self.on_air_calls.append(spoken)

        def mark_pending_air(self, spoken=""):
            self.pending_calls.append(spoken)

        async def wait_until_clear(self, timeout=None):
            return 0.0

    class _Station:
        def __init__(self, result):
            self.result = result

        async def dj_say(self, message, mode="styled", kind="callin"):
            return dict(self.result)

    def _announce(self, result):
        from call.actions import CallActions
        from call.tools.broadcast import build_on_air_tools

        guard = self._Guard()
        built = build_on_air_tools(
            {"allow_announcements": True}, self._Station(result),
            CallActions(5), guard, guarded=True)
        tools = {t.info.name: t for t in built}
        out = asyncio.run(tools["subwave_dj_announce"](message="hello there"))
        return guard, out

    def test_a_confirmed_announce_gets_the_sized_hold(self):
        guard, out = self._announce({"ok": True, "spoken": "hello there folks"})
        self.assertEqual(guard.on_air_calls, ["hello there folks"])
        self.assertEqual(guard.pending_calls, [])
        self.assertIn("seconds", out)

    def test_a_slow_confirmation_gets_the_pending_hold_and_no_number(self):
        guard, out = self._announce({"ok": True, "unconfirmed": True})
        self.assertEqual(guard.on_air_calls, [])
        self.assertEqual(guard.pending_calls, ["hello there"])
        # The DJ must not be handed a duration it would read out loud.
        self.assertNotIn("seconds", out)
        self.assertIn("slow to confirm", out)


class TestOnlyThisDJsSegmentsCanBeRun(unittest.TestCase):
    """The station's manual trigger is an operator OVERRIDE: `POST /dj/skill`
    runs a segment even when it is switched off, ignoring cooldowns and the
    frequency gate. Until 0.10.132 the call line handed the model the whole
    catalogue and passed whatever came back straight to it — so a caller could
    run a segment the operator had turned off, or one belonging to another
    DJ's show. Found on the 2026-08-14 upstream pass.
    """

    CATALOGUE = [
        {"name": "weather", "label": "Weather", "enabled": True, "ready": True},
        {"name": "news", "label": "News", "enabled": False, "ready": True},
        {"name": "web-search", "label": "Search", "enabled": True, "ready": False},
        {"name": "storytime", "label": "Story", "enabled": True, "ready": True},
    ]

    def _runnable(self, assigned):
        from station_config import runnable_skills

        return [s["name"] for s in runnable_skills(self.CATALOGUE, assigned)]

    def test_a_switched_off_segment_is_not_offered(self):
        self.assertNotIn("news", self._runnable(None))

    def test_a_segment_missing_its_api_key_is_not_offered(self):
        # Offering it buys one confident "let me get that" and then a failure.
        self.assertNotIn("web-search", self._runnable(None))

    def test_no_assignment_means_this_dj_runs_everything(self):
        # Absent and null both mean unrestricted on the station's side: its
        # seeded roster carries no `skills` key until the operator saves
        # personas once. Reading that as "runs nothing" would leave a fresh
        # station's DJ with no segments at all.
        self.assertEqual(self._runnable(None), ["weather", "storytime"])

    def test_another_djs_segment_is_withheld(self):
        self.assertEqual(self._runnable(["weather"]), ["weather"])

    def test_an_empty_assignment_is_not_read_as_all(self):
        # [] is a real answer — this DJ is assigned nothing — and is NOT the
        # same as the absent key above.
        self.assertEqual(self._runnable([]), [])

    def test_a_cron_only_segment_is_withheld_when_the_station_says_so(self):
        # Upstream #1379 withholds a clock-pinned skill from the station's own
        # random picks; `skillCatalog()` does not publish the field yet, so
        # this is read in advance. The day it appears, a segment written for
        # 7:10am stops being firable by a caller at one in the afternoon with
        # no change on this side.
        from station_config import runnable_skills

        pinned = [{"name": "dawn", "enabled": True, "ready": True,
                   "cronOnly": True}]
        self.assertEqual(runnable_skills(pinned, None), [])

    def test_the_tool_refuses_a_segment_that_is_not_ours_tonight(self):
        import asyncio

        from call.actions import CallActions
        from call.air import OnAirGuard
        from call.tools.broadcast import build_on_air_tools

        class _Station:
            def __init__(self):
                self.ran = []

            async def run_skill(self, name):
                self.ran.append(name)
                return {"ok": True, "spoken": "..."}

        station = _Station()
        guard = OnAirGuard(station, {})
        tools = {t.info.name: t for t in build_on_air_tools(
            {"allow_skills": True}, station, CallActions(5), guard,
            guarded=False, skills=["weather"])}
        out = asyncio.run(tools["subwave_run_skill"](name="news"))

        # Refused HERE, because the station would have run it.
        self.assertEqual(station.ran, [])
        self.assertIn("weather", out)          # names the real list
        self.assertIn("not a segment you can run", out)

    def test_an_empty_roster_is_said_plainly_rather_than_attempted(self):
        import asyncio

        from call.actions import CallActions
        from call.air import OnAirGuard
        from call.tools.broadcast import build_on_air_tools

        class _Station:
            def __init__(self):
                self.ran = []

            async def run_skill(self, name):
                self.ran.append(name)
                return {"ok": True, "spoken": "..."}

        station = _Station()
        tools = {t.info.name: t for t in build_on_air_tools(
            {"allow_skills": True}, station, CallActions(5),
            OnAirGuard(station, {}), guarded=False, skills=[])}
        out = asyncio.run(tools["subwave_run_skill"](name="weather"))

        self.assertEqual(station.ran, [])
        self.assertIn("no segments to run", out)


class TestOneRequestCannotTakeTwoQueueSlots(unittest.TestCase):
    """Four queue entries for two records, on a real call.

    20260816: both went in at 84s as ONE PARALLEL GROUP, and only the first
    call of such a group is signed on Gemini — the rest replay as plain text,
    so the model lost its own receipts, said "I didn't actually lock those in
    — my mistake!", and the promise guard pushed it to redo the lot. Positions
    3 and 4, then 5 and 6.

    Guarded at the TOOL because the cause does not matter: a lost receipt, a
    nudge that fired twice and a caller who repeats themselves all arrive here.
    """

    def _tool(self, actions=None):
        from call.actions import CallActions
        from call.tools import music
        from call.tools.music import build_library_tools

        class _Station:
            def __init__(self):
                self.queued = []

            async def queue_track(self, track):
                self.queued.append(track.get("id"))
                return {"ok": True, "queuePosition": len(self.queued) + 2}

        st = _Station()
        orig = music.library_search_needs_mcp
        music.library_search_needs_mcp = lambda: False
        try:
            tools = build_library_tools(
                {"allow_exact_queue": True}, st, actions or CallActions(9))
        finally:
            music.library_search_needs_mcp = orig
        tool = next((t for t in tools
                     if t.info.name == "subwave_queue_track"), None)
        return st, tool

    def test_the_same_track_twice_only_lands_once(self):
        st, tool = self._tool()
        self.assertIn("in the queue",
                      asyncio.run(tool(id="JGUH6", title="Murder on the Dancefloor")))
        second = asyncio.run(tool(id="JGUH6", title="Murder on the Dancefloor"))
        self.assertEqual(st.queued, ["JGUH6"], "the record took a second slot")
        self.assertIn("ALREADY in the queue", second)
        # And the DJ is told not to say "right, THAT time it went in" — which
        # is what it said on the call, and it was untrue both times.
        self.assertIn("Don't queue it again", second)
        self.assertIn("still waiting its turn", second)
        # A DIFFERENT record is unaffected.
        asyncio.run(tool(id="sUpwE", title="Classical Gas (live)"))
        self.assertEqual(st.queued, ["JGUH6", "sUpwE"])

    def test_a_repeat_does_not_spend_the_callers_action_limit(self):
        from call.actions import CallActions

        actions = CallActions(9)
        st, tool = self._tool(actions)
        asyncio.run(tool(id="JGUH6", title="Murder on the Dancefloor"))
        asyncio.run(tool(id="JGUH6", title="Murder on the Dancefloor"))
        self.assertEqual(actions.count, 1)


class TestTakingAHeartBackOff(unittest.TestCase):
    """"Can you un-favourite this song?" — twice, and refused both times.

    2026-08-16: the caller liked "Everyday" by Don McLean, changed their mind
    forty seconds later, and got "nothing is playing to un-like right now"
    while a record was plainly playing. Two separate faults, and the first one
    means un-like had never once worked against a real station.

    1. `_track_on_air` read `id` and `songId`. The station sends
       `subsonic_id`. So the id was ALWAYS "" — liking survived it because
       POST /like likes whatever is on air and the id is only a guard against
       the record changing mid-request; un-liking needs it for the URL.
    2. Tying it to the current record was our restriction, never the
       station's: DELETE /likes/song/:id/operator takes any song id. A caller
       changes their mind a beat late, by which time the track has moved on —
       or the DJ has skipped it, which is exactly what happened here.
    """

    def _tools(self, now_playing=None, search=None, liked=None):
        from call.actions import CallActions
        from call.tools import curation

        class _Station:
            def __init__(self):
                self.unliked = []

            async def now_playing(self):
                return {"nowPlaying": now_playing or {}}

            async def search_library(self, q, offset=0, limit=30):
                return list(search or [])

            async def unlike_track(self, song_id):
                if not song_id:
                    return {"ok": False,
                            "error": "nothing is playing to un-like right now"}
                self.unliked.append(song_id)
                return {"ok": True}

        st = _Station()
        actions = CallActions(9)
        if liked:
            actions.last_liked = liked
        orig = curation.library_search_needs_mcp
        curation.library_search_needs_mcp = lambda: False
        try:
            tools = curation.build_curation_tools(
                {"allow_favorite": True, "allow_unfavorite": True},
                st, actions)
        finally:
            curation.library_search_needs_mcp = orig
        tool = next(t for t in tools if t.info.name == "subwave_unlike_track")
        return st, tool

    def test_the_stations_own_id_field_is_read(self):
        # The whole bug in one assertion: subsonic_id is what arrives.
        st, tool = self._tools(
            now_playing={"title": "UFOF", "artist": "Big Thief",
                         "subsonic_id": "m2VUWQI9gwmHLK9lvFwLvU"})
        out = asyncio.run(tool())
        self.assertEqual(["m2VUWQI9gwmHLK9lvFwLvU"], st.unliked)
        self.assertIn("took the heart off", out)

    def test_a_named_track_does_not_have_to_be_on_air(self):
        st, tool = self._tools(
            now_playing={"title": "Something Else", "subsonic_id": "onair"},
            search=[{"title": "Everyday", "artist": "Don McLean",
                     "subsonic_id": "donmc1"}])
        asyncio.run(tool(title="Everyday", artist="Don McLean"))
        self.assertEqual(["donmc1"], st.unliked,
                         "it un-liked whatever happened to be on air instead")

    def test_with_no_name_it_undoes_what_this_call_liked(self):
        # The caller who changes their mind after the record has moved on —
        # which on the real call was because the DJ had skipped it.
        st, tool = self._tools(
            now_playing={"title": "Something Else", "subsonic_id": "onair"},
            liked=("donmc1", {"title": "Everyday", "artist": "Don McLean"}))
        asyncio.run(tool())
        self.assertEqual(["donmc1"], st.unliked)

    def test_nothing_to_go_on_asks_rather_than_claiming(self):
        st, tool = self._tools(now_playing={})
        out = asyncio.run(tool(title="A Record Nobody Has"))
        self.assertEqual([], st.unliked)
        self.assertIn("Ask them which record", out)

    def test_liking_remembers_the_id_for_the_undo(self):
        from call.actions import CallActions
        from call.tools import curation

        class _Station:
            async def now_playing(self):
                return {"nowPlaying": {"title": "Everyday",
                                       "artist": "Don McLean",
                                       "subsonic_id": "donmc1"}}

            async def like_track(self, song_id):
                return {"ok": True, "count": 1}

        actions = CallActions(9)
        tools = curation.build_curation_tools(
            {"allow_favorite": True}, _Station(), actions)
        like = next(t for t in tools if t.info.name == "subwave_like_track")
        asyncio.run(like())
        self.assertEqual("donmc1", actions.last_liked[0])

    def test_the_prompt_stops_saying_there_is_no_un_like(self):
        # like_track's own description told the model "there is no un-like, so
        # don't offer either" — while subwave_unlike_track sat beside it in the
        # same registry. The DJ was being taught to refuse.
        from call.actions import CallActions
        from call.tools import curation

        class _Station:
            pass

        tools = curation.build_curation_tools(
            {"allow_favorite": True}, _Station(), CallActions(9))
        like = next(t for t in tools if t.info.name == "subwave_like_track")
        self.assertNotIn("no un-like", like.info.description)


class TestASegmentThatStoodDownIsNotReportedAsAiring(unittest.TestCase):
    """Station 1.8's forced-skill path may answer 200 with `aired: false` and
    a reason — the skill ran, looked at what it fetched, and had nothing worth
    saying (their #1416/#1412). Before the tool learned the shape, that answer
    counted as success: the DJ told the caller a segment was coming and the
    overlap guard held the floor for a minute of nothing."""

    class _Station:
        def __init__(self, result):
            self.result = result
            self.ran = []

        async def run_skill(self, name):
            self.ran.append(name)
            return dict(self.result)

    def _run(self, result):
        import asyncio

        from call.actions import CallActions
        from call.air import OnAirGuard
        from call.tools.broadcast import build_on_air_tools

        station = self._Station(result)
        guard = OnAirGuard(station, {})
        tools = {t.info.name: t for t in build_on_air_tools(
            {"allow_skills": True}, station, CallActions(5), guard,
            guarded=False, skills=["news"])}
        out = asyncio.run(tools["subwave_run_skill"](name="news"))
        return guard, out

    def test_a_stand_down_is_relayed_with_its_reason(self):
        guard, out = self._run(
            {"ok": True, "aired": False, "reason": "nothing new since the last run"})
        self.assertIn("chose not to air", out)
        self.assertIn("nothing new since the last run", out)
        self.assertIn("do not promise", out.lower())
        # And the guard must NOT hold the floor for speech that is not coming.
        self.assertFalse(guard.on_air,
                         "a stood-down segment held the on-air gate shut")

    def test_an_older_station_without_the_field_still_counts_as_running(self):
        # Absent must keep meaning "it ran": stations older than 1.8 send no
        # `aired` at all, and a strict `is False` is what protects them.
        guard, out = self._run({"ok": True, "spoken": "the news segment text"})
        self.assertNotIn("chose not to air", out)
        self.assertTrue(guard.on_air, "a segment that ran must hold the gate")


class TestAnObligationBelongsToTheCallerNotTheDJsWording(unittest.TestCase):
    """The promise guard stops reading the DJ's vocabulary for a speech act.

    Every line here is from `chat-db74032fb058` (2026-08-22), the conversation
    the operator reported as "one song, requested multiple times, and then it
    didn't queue until later". It queued first time. What went wrong is that
    the DJ asked permission and then answered itself:

        caller  Skip the current song and queue up songs for mina
        TOOL    search_library -> 8 results
        TOOL    skip_track     -> Done
        TOOL    queue_track    -> "Amor mio" is in the queue
        DJ      "Shall I queue that one up for you, or were you looking for
                 something else from the list?
                 <blank>
                 That's locked in - "Amor mio" is queued up and ready to go."

    The nudge that produced the second half fired on the gerund `looking`, in
    a question about what the CALLER wanted. No pattern list fixes that:
    "looking" is honestly ambiguous. So the trigger moved off the prose and
    onto whether the caller has an ask a tool still owes.
    """

    LOOKING = ("Shall I queue that one up for you, or were you looking "
               "for something else from the list?")

    def test_the_line_that_caused_it_still_matches_the_pattern(self):
        # Not a regex fix — the pattern is unchanged and still fires. What
        # changed is that a match alone no longer nudges.
        from promises import PROMISES_ACTION

        self.assertTrue(PROMISES_ACTION.search(self.LOOKING))

    def test_nothing_owed_means_nothing_to_nudge(self):
        from promises import unbacked

        self.assertEqual(unbacked(self.LOOKING, tools_ran=False, owed=False), "")

    def test_an_outstanding_ask_still_nudges(self):
        # The guard's real catch must survive: a caller waiting on something,
        # and a DJ saying it is about to happen with no tool behind it.
        from promises import unbacked

        self.assertEqual(unbacked(self.LOOKING, tools_ran=False, owed=True),
                         "promise")

    def test_a_false_claim_is_a_lie_whether_or_not_anyone_asked(self):
        # Only the PROMISE verdict is gated. "It's done" when it is not done
        # is not excused by nobody having asked for it.
        from promises import unbacked

        self.assertEqual(
            unbacked("I've just put that in the queue for you.",
                     acted=False, owed=False), "claim")

    def test_a_refusal_is_structural_and_ungated(self):
        from promises import unbacked

        self.assertEqual(
            unbacked("It'll head out onto the airwaves as soon as that clears.",
                     tools_ran=True, acted=False, refused=True, owed=False),
            "refused")

    def test_the_default_preserves_the_old_behaviour(self):
        # Any caller that has not learned about `owed` gets exactly what it
        # got before — the phone had this wiring first and the text line did
        # not, so the default has to be the conservative one.
        from promises import unbacked

        self.assertEqual(unbacked(self.LOOKING, tools_ran=False), "promise")

    def test_the_real_turn_resolves_the_way_the_transcript_should_have(self):
        """End to end on the actual conversation, with real timestamps."""
        from call.asks import Asks
        from promises import unbacked

        asks = Asks()
        asks.heard("Skip the current song and queue up songs for mina", at=100.0)
        # skip_track and queue_track both landed after the ask.
        taken_at = [102.0, 112.0]
        owed = bool(asks.unanswered(taken_at))
        self.assertFalse(owed, "the caller's ask was answered by two actions")
        self.assertEqual(unbacked(self.LOOKING, tools_ran=False, owed=owed), "")

    def test_an_ask_with_no_action_after_it_is_still_owed(self):
        from call.asks import Asks

        asks = Asks()
        asks.heard("can you play Africa by Toto", at=200.0)
        # The action that landed belongs to the PREVIOUS ask, not this one.
        self.assertTrue(asks.unanswered([150.0]))
        self.assertFalse(asks.settled([150.0]))

    def test_a_detector_that_heard_nothing_leaves_the_guard_alone(self):
        """The fail-safe, and the reason `settled` is not `not unanswered`.

        The 2026-08-23 archive replay fixed the pronoun deafness ("Play
        diciembre first" is heard now — see below), but the detector is still
        lexical and still misses the follow-up FRAGMENT: a caller who was
        just offered choices and answers "Italian songs" (real line,
        2026-08-22) has made a request only context can read.

        If "heard nothing" were read as "nothing owed", the promise guard
        would fall silent on exactly those requests. A quiet false negative
        is worse than a noisy false positive here: an operator can see a DJ
        answering its own question; nobody can see a request that vanished.
        """
        from call.asks import Asks

        deaf = Asks()
        deaf.heard("Italian songs", at=100.0)
        self.assertEqual(deaf.asked, [], "if this passes, the fragment "
                                         "deafness is fixed — rewrite this "
                                         "test around what is still unheard, "
                                         "do not delete it")
        # No information, so the guard is left exactly as it was.
        self.assertFalse(deaf.settled([110.0]))

    def test_the_diciembre_call_is_heard_now(self):
        """The line this whole stream is named for, plus the replay's gains.

        Every string here is a real caller line from the archive that the
        pattern was deaf to until the 2026-08-23 replay (97 records, 29
        unheard asks, zero previously-heard lines lost). If one of these
        stops matching, a regression has re-deafened the detector to
        something a real caller actually said.
        """
        from call.asks import Asks

        heard_now = [
            "Play diciembre first",
            "Play song by giorgia",
            "could yo play the marshal mathers lp?",
            "spin me a mix all 90s rock",
            "i want to hear wade, change shows",
            "Can you cue the word album?",
            "cancel that track",
            "Can I get Rosie and Chinese music?",
            "hey have you got the lyrics for this song?",
            "Create me a mix from the artist mina",
            "What Eminem albums do you have?",
            "can you remove all the eminiem songs from the queu?",
            "Skip current song and play dicembre",
        ]
        for line in heard_now:
            asks = Asks()
            asks.heard(line, at=100.0)
            self.assertTrue(asks.asked, f"deaf again to: {line!r}")
        # And an ask that is heard can be settled by an action after it —
        # the diciembre call's actual failure was that this was impossible.
        asks = Asks()
        asks.heard("Play diciembre first", at=100.0)
        self.assertTrue(asks.settled([104.0]))
        self.assertFalse(asks.settled([90.0]))

    def test_chatter_about_music_is_still_not_an_ask(self):
        # The other direction, from the same archive: informational questions
        # are answered in words and leave no receipt, so hearing them would
        # report a dropped ask on every call that went well.
        from call.asks import Asks

        for line in [
            "Can you hear me from over there?",
            "What song is playing right now?",
            "How would I possibly know what you're going to play next?",
            "i liked the song that was on before.",
        ]:
            asks = Asks()
            asks.heard(line, at=100.0)
            self.assertEqual(asks.asked, [], f"false ask heard in: {line!r}")
