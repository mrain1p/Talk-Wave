"""
Unit tests for the pure-logic parts of the sidecar — the pieces where a silent
regression would be audible on air (speech filtering), would misroute money
(settings/secrets precedence), or would break tool truthfulness.

Run from agent-worker/:  python -m unittest test_sidecar -v

Deliberately stdlib-only (unittest, tempfile) so the venv needs nothing new.
Network is never touched.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import types
import unittest
from pathlib import Path

# Before any module that calls log_setup.setup() is imported — otherwise the
# test run pollutes the real data/logs/worker.log.
os.environ["LOG_TO_FILE"] = "0"

import brain
import secrets_store
import settings as settings_store
import speech_filter
from brain import briefing, conduct


def widget_js(exclude=("embed.js",)) -> dict:
    """Every JS file the widget's own pages load, by filename.

    Discovered rather than listed, because the contract tests below used to
    name `app.js` directly and that file has since been split into shared.js,
    call.js and panel.js. A named file silently stops covering the code that
    moved out of it; a glob picks the new one up the moment it lands.

    embed.js is excluded by default: it is the third-party drop-in, it fetches
    nothing and it reaches for no id in this repo's markup.
    """
    d = Path(__file__).parent.parent / "web-widget"
    return {p.name: p.read_text(encoding="utf-8")
            for p in sorted(d.glob("*.js")) if p.name not in exclude}


class TestSpeechFilter(unittest.TestCase):
    def test_strips_asterisk_stage_directions(self):
        out = speech_filter.strip_stage_directions(
            "*shuffles through records* Here's one for you."
        )
        self.assertEqual(out, "Here's one for you.")

    def test_strips_bracketed_and_paren_actions(self):
        out = speech_filter.strip_stage_directions("[pause] Right. (laughs) Where were we?")
        self.assertNotIn("[pause]", out)
        self.assertNotIn("(laughs)", out)
        self.assertIn("Where were we?", out)

    def test_strips_stage_directions_that_do_not_start_on_the_verb(self):
        # Went out on a real call: "(Phone rings) Yeah, Cliff here." The old
        # rule only matched a parenthetical whose FIRST word was a verb.
        out = speech_filter.strip_stage_directions(
            "(Phone rings) Yeah, Cliff here. We're letting the last track settle."
        )
        self.assertNotIn("Phone rings", out)
        self.assertTrue(out.startswith("Yeah, Cliff here."))
        for direction in ("(the receiver clicks)", "(static crackles)",
                          "(sound of vinyl scratches)"):
            self.assertNotIn(
                direction, speech_filter.strip_stage_directions(direction + " right then")
            )

    def test_keeps_ordinary_parenthetical_speech(self):
        text = "the set (which runs till two) is all vinyl"
        self.assertEqual(speech_filter.strip_stage_directions(text), text)

    def test_keeps_parentheticals_that_merely_end_in_s(self):
        # The permissive "any word ending in -s" version of the verb-last rule
        # ate ordinary speech like this.
        for text in ("back in (about three minutes)",
                     "that one's from (one of my favourite albums)"):
            self.assertEqual(speech_filter.strip_stage_directions(text), text)

    def test_strips_the_djs_own_name_used_as_a_script_label(self):
        # Went out on a real call: the model slipped into screenplay format and
        # the voice read the DJ's own name aloud at the top of every turn.
        speech_filter.set_speaker("Francesca Hale")
        try:
            self.assertEqual(
                speech_filter.strip_speaker_labels(
                    "Francesca: Hey there, thanks for holding on."),
                "Hey there, thanks for holding on.",
            )
            for variant in ("**Francesca:** right then", "Francesca Hale: right then",
                            "DJ: right then", "HOST: right then"):
                self.assertEqual(
                    speech_filter.strip_speaker_labels(variant), "right then", variant)
        finally:
            speech_filter.set_speaker("")

    def test_label_strip_leaves_a_following_stage_direction_intact(self):
        # A greedy bold matcher ate the opening asterisk of what came next,
        # so the direction no longer looked like one and went out on air.
        speech_filter.set_speaker("Francesca")
        try:
            self.assertEqual(
                speech_filter.clean_for_speech(
                    "Francesca: *adjusts headphones* Loud and clear now.",
                    profanity_mode="off"),
                "Loud and clear now.",
            )
            self.assertEqual(
                speech_filter.clean_for_speech(
                    "**Francesca:** (Phone rings) Yeah, Cliff here.",
                    profanity_mode="off"),
                "Yeah, Cliff here.",
            )
        finally:
            speech_filter.set_speaker("")

    def test_the_dj_can_still_say_its_own_name_out_loud(self):
        # Only the SCRIPT LABEL form is a problem. Introducing yourself is
        # what a DJ does — the fix must not cost that.
        speech_filter.set_speaker("Wade")
        try:
            for kept in ("This is Wade, you're through to the booth.",
                         "Wade here, what can I do for you?",
                         "You're on with Wade on the late shift.",
                         "Wade's the name, records are the game."):
                self.assertEqual(speech_filter.strip_speaker_labels(kept), kept)
            # …but the label form still goes.
            self.assertEqual(
                speech_filter.strip_speaker_labels("Wade: You're through to the booth."),
                "You're through to the booth.")
        finally:
            speech_filter.set_speaker("")

    def test_never_eats_ordinary_speech_that_contains_a_colon(self):
        speech_filter.set_speaker("Francesca")
        try:
            for text in ("Listen: this one's a classic.",
                         "Here's the deal: we're out of time.",
                         "One thing: it's not on the album."):
                self.assertEqual(speech_filter.strip_speaker_labels(text), text)
            # Another person's name is dialogue, not a label for OUR voice.
            self.assertEqual(
                speech_filter.strip_speaker_labels("Bowie: an underrated run"),
                "Bowie: an underrated run",
            )
        finally:
            speech_filter.set_speaker("")

    def test_label_stripping_is_inert_before_a_persona_is_known(self):
        speech_filter.set_speaker("")
        self.assertEqual(
            speech_filter.strip_speaker_labels("Francesca: hello"), "Francesca: hello")

    def test_profanity_mask_and_drop_and_off(self):
        words = ["fuck", "shit"]
        self.assertEqual(
            speech_filter.filter_profanity("well fuck that", words, "mask"),
            "well f— that",
        )
        self.assertEqual(
            speech_filter.filter_profanity("well fuck that", words, "drop"),
            "well that",
        )
        self.assertEqual(
            speech_filter.filter_profanity("well fuck that", words, "off"),
            "well fuck that",
        )

    def test_profanity_respects_word_boundaries(self):
        # "Scunthorpe problem": substrings must survive.
        text = "let me assess the Scunthorpe situation"
        self.assertEqual(
            speech_filter.filter_profanity(text, ["cunt", "ass"], "drop"), text
        )

    def test_clean_for_speech_combined(self):
        out = speech_filter.clean_for_speech(
            "*sighs* That's some shit, huh?",
            strip_directions=True, profanity_mode="mask", profanity_words=["shit"],
        )
        self.assertEqual(out, "That's some s—, huh?")


class TestPrompts(unittest.TestCase):
    def test_demojibake_repairs_double_encoding(self):
        self.assertEqual(briefing.demojibake("night â€” slow"), "night — slow")

    def test_demojibake_leaves_clean_text_alone(self):
        self.assertEqual(briefing.demojibake("plain text — fine"), "plain text — fine")

    def test_clip_respects_budget_on_word_boundary(self):
        out = briefing.clip("one two three four five", 13)
        self.assertLessEqual(len(out), 14)  # budget + ellipsis
        self.assertTrue(out.endswith("…"))


class _TempStores(unittest.TestCase):
    """Points the settings/secrets stores at temp files and scrubs the env
    vars the tests touch, restoring everything afterwards."""

    ENV_VARS = (
        "STT_MODEL", "DEEPGRAM_MODEL", "STT_PROVIDER", "LLM_PROVIDER",
        "DEEPGRAM_API_KEY", "OPENAI_API_KEY", "TTS_MODE",
        # Set by the key-withholding tests; restored so a real key in the
        # developer's environment survives the run.
        "OPENROUTER_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY", "TTS_API_KEY",
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._old_settings_path = settings_store.SETTINGS_PATH
        self._old_secrets_path = secrets_store.SECRETS_PATH
        settings_store.SETTINGS_PATH = tmp / "settings.json"
        secrets_store.SECRETS_PATH = tmp / "secrets.json"
        self._old_env = {k: os.environ.get(k) for k in self.ENV_VARS}
        for k in self.ENV_VARS:
            os.environ.pop(k, None)

    def tearDown(self):
        settings_store.SETTINGS_PATH = self._old_settings_path
        secrets_store.SECRETS_PATH = self._old_secrets_path
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()


class TestSettings(_TempStores):
    def test_defaults_when_nothing_stored(self):
        cfg = settings_store.load()
        self.assertEqual(cfg["stt_model"], "nova-3")
        self.assertTrue(cfg["allow_requests"])
        self.assertNotIn("allow_sfx", cfg)  # removed feature stays removed

    def test_env_beats_default_and_stored_beats_env(self):
        os.environ["STT_PROVIDER"] = "openai"
        self.assertEqual(settings_store.load()["stt_provider"], "openai")
        settings_store.save({"stt_provider": "local"})
        self.assertEqual(settings_store.load()["stt_provider"], "local")

    def test_stt_model_env_alias_and_fallback(self):
        os.environ["DEEPGRAM_MODEL"] = "nova-2"
        self.assertEqual(settings_store.load()["stt_model"], "nova-2")
        os.environ["STT_MODEL"] = "base.en"  # new name wins over old
        self.assertEqual(settings_store.load()["stt_model"], "base.en")

    def test_empty_string_clears_an_override(self):
        settings_store.save({"llm_provider": "google"})
        self.assertEqual(settings_store.load()["llm_provider"], "google")
        settings_store.save({"llm_provider": ""})
        self.assertEqual(settings_store.load()["llm_provider"], "openai")

    def test_reset_semantics_for_booleans(self):
        # The panel's Reset sends '' for checkboxes too — that must CLEAR the
        # override so the default reasserts, never store a truthy override.
        settings_store.save({"allow_skills": True})
        self.assertTrue(settings_store.load()["allow_skills"])
        settings_store.save({"allow_skills": ""})
        self.assertFalse(settings_store.load()["allow_skills"])  # default off

    def test_coercion_of_string_bools_and_numbers(self):
        settings_store.save({"call_volume": "80", "call_sounds": "false"})
        cfg = settings_store.load()
        self.assertEqual(cfg["call_volume"], 80)
        self.assertIs(cfg["call_sounds"], False)

    def test_a_url_field_refuses_something_that_is_not_a_url(self):
        # A real deployment ran with "Michael" in the MCP endpoint — the
        # browser autofilled a name into the box — so the agent got NO station
        # tools on any call and invented library results instead. Nothing
        # downstream complained, which is the part that made it expensive.
        problem = settings_store.complain({"station_mcp_url": "Michael"})
        self.assertIsNotNone(problem)
        self.assertIn("URL", problem)
        self.assertIn("Michael", problem)          # says what it saw
        self.assertIn("empty", problem)            # and how to fix it

        for field in settings_store.URL_FIELDS:
            self.assertIsNotNone(settings_store.complain({field: "nonsense"}), field)
            self.assertIsNone(settings_store.complain({field: ""}))          # clears
            self.assertIsNone(settings_store.complain({field: "http://x:7700/api"}))
            self.assertIsNone(settings_store.complain({field: "https://x/api"}))
        # Everything else is still free text.
        self.assertIsNone(settings_store.complain({"greeting": "just say hi"}))

    def test_a_url_already_stored_broken_falls_back_instead_of_breaking(self):
        # Validation on save can't help a config that was already saved wrong,
        # and handing an unusable URL to the agent is worse than the default.
        settings_store.save({"station_mcp_url": "Michael",
                             "station_base_url": "http://box:7700/api"})
        self.assertEqual(settings_store.station_mcp_url(),
                         "http://box:7700/api/mcp")
        settings_store.save({"station_base_url": "Michael"})
        self.assertTrue(settings_store.station_base_url().startswith("http"))

    def test_unknown_keys_are_ignored(self):
        settings_store.save({"allow_sfx": True, "not_a_field": 1})
        stored = json.loads(settings_store.SETTINGS_PATH.read_text())
        self.assertNotIn("allow_sfx", stored)
        self.assertNotIn("not_a_field", stored)


class TestNothingToSay(_TempStores):
    """A line that cleans down to nothing must never reach the TTS backend.

    Found on a real call. The model answered with a stage direction and
    nothing else; speech hygiene stripped it to an empty string, which is
    correct; that empty string was then sent to the voice server, which
    errored, four times, until the agent gave up and the caller heard the
    dead-air fallback instead of the DJ:

        Generating speech (streaming) - Text:  | Voice: -Cliff1
        ValueError: No valid speaker lines found in script
        POST /v1/audio/speech 500
    """

    def _synth(self, lines: list[str]) -> list[tuple[bool, str]]:
        """(silent?, what would be sent) for each line.

        Runs in a loop because a livekit ChunkedStream starts a metrics task
        on construction.
        """
        import asyncio

        from tts_adapter import AdapterTTS

        async def go():
            engine = AdapterTTS(voice="-Cliff1", base_url="http://tts.invalid")
            out = []
            for text in lines:
                s = engine.synthesize(text)
                out.append((s._silent, s.input_text))
                await s.aclose()
            await engine.aclose()
            return out

        return asyncio.run(go())

    def test_a_stage_direction_only_line_is_not_spoken(self):
        settings_store.save({"strip_stage_directions": True})
        lines = ["*shuffles records*", "(laughs)", "[pause]", "   "]
        for line, (silent, sent) in zip(lines, self._synth(lines)):
            with self.subTest(line=line):
                self.assertTrue(silent, f"{line!r} would still reach the voice server")
                self.assertEqual(sent, "")

    def test_real_speech_is_still_spoken(self):
        settings_store.save({"strip_stage_directions": True})
        (silent, sent), = self._synth(["*grins* Alright, putting that in for you."])
        self.assertFalse(silent)
        self.assertIn("Alright", sent)


class TestFrontDoorPolicy(_TempStores):
    """Who may reach the PHONE, as opposed to the panel.

    This used to be inferred from whether a guest password happened to exist,
    so the policy changed as a side effect of setting or clearing one. It is
    now an explicit choice, and the property that matters most is the last
    test: an unconfigured gate must refuse, never fall open.
    """

    def _check(self, mode: str, key: str = "") -> str | None:
        import admin_auth  # noqa: F401  (imported for the reset in _TempStores)
        from api import auth as api_auth

        settings_store.save({"front_access": mode})
        api_auth._auth_state.clear()
        return api_auth._guest_check(key, "10.0.0.9")

    def test_auto_is_the_old_behaviour_and_the_default(self):
        # The default has to leave existing deployments exactly as they were:
        # a line that took calls yesterday must take them after an upgrade.
        import admin_auth

        self.assertEqual(settings_store.load()["front_access"], "auto")
        admin_auth.clear_guest_password()
        self.assertIsNone(self._check("auto"), "auto closed a line that had no code")
        admin_auth.set_guest_password("guest-code")
        self.assertEqual(self._check("auto"), "code required")
        self.assertIsNone(self._check("auto", "guest-code"))

    def test_open_lets_anyone_call(self):
        import admin_auth

        admin_auth.set_guest_password("guest-code")
        self.assertIsNone(self._check("open"))
        self.assertIsNone(self._check("open", "anything"))

    def test_guest_mode_wants_the_code(self):
        import admin_auth

        admin_auth.set_password("admin-pw")
        admin_auth.set_guest_password("guest-code")
        self.assertEqual(self._check("guest"), "code required")
        self.assertIsNone(self._check("guest", "guest-code"))
        # An operator carries one password: admin is accepted as a guest code.
        self.assertIsNone(self._check("guest", "admin-pw"))
        self.assertIsNotNone(self._check("guest", "wrong"))

    def test_admin_mode_closes_the_phone_to_guests(self):
        import admin_auth

        admin_auth.set_password("admin-pw")
        admin_auth.set_guest_password("guest-code")
        self.assertIsNone(self._check("admin", "admin-pw"))
        # The guest code is a valid code — just not for this door.
        self.assertIsNotNone(self._check("admin", "guest-code"))
        self.assertEqual(self._check("admin"), "code required")

    def test_an_unconfigured_gate_refuses_rather_than_opening(self):
        # The property worth having. Selecting a password-based policy without
        # having set that password must not silently behave like "open" — that
        # is how a deployment ends up publicly callable while its operator
        # believes it is locked.
        import admin_auth
        from api import auth as api_auth

        # Isolated rather than relying on store state: this asserts the
        # no-password-configured branch specifically.
        real_admin, real_guest = api_auth._auth_configured, admin_auth.guest_is_set
        api_auth._auth_configured = lambda: False
        admin_auth.guest_is_set = lambda: False
        try:
            for mode in ("guest", "admin"):
                with self.subTest(mode=mode):
                    reason = self._check(mode)
                    self.assertIsNotNone(reason, "an unset gate fell open")
                    self.assertIn("isn't taking calls", reason)
        finally:
            api_auth._auth_configured = real_admin
            admin_auth.guest_is_set = real_guest


class TestCallerContext(unittest.TestCase):
    """What we can say about a caller when a call goes wrong.

    The worker writes the call record and never sees the browser that rang, so
    the token server attaches what it knew at mint time. Kept in memory only —
    enough to answer "why did that call fail" while the process is up, without
    the call archive quietly becoming a log of who rang and from where.
    """

    def test_it_tells_the_browsers_apart(self):
        from api.tokens import _describe_client

        cases = {
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:153.0) Gecko/20100101 Firefox/153.0":
                "Firefox on macOS",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36":
                "Chrome on Windows",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1":
                "Safari on iPhone",
        }
        for ua, want in cases.items():
            with self.subTest(browser=want):
                self.assertEqual(_describe_client(ua), want)
        self.assertEqual(_describe_client(""), "unknown client")

    def test_it_says_whether_the_caller_was_on_this_network(self):
        # The point of the whole thing: a call that connects and then hears
        # nothing looks identical whether the caller was off-LAN with no media
        # path or simply silent. This separates them.
        from api.tokens import _network_of

        for ip in ("192.168.1.51", "10.0.0.8", "172.19.0.4", "127.0.0.1"):
            with self.subTest(ip=ip):
                self.assertEqual(_network_of(ip), "same network")
        for ip in ("100.33.134.4", "8.8.8.8", "172.32.0.1"):
            with self.subTest(ip=ip):
                self.assertEqual(_network_of(ip), "off-network")
        self.assertEqual(_network_of(""), "unknown")
        self.assertEqual(_network_of("nonsense"), "off-network")

    def test_caller_context_never_reaches_the_call_record_on_disk(self):
        # It is diagnostic, not archive. If this ever changes, every stored
        # call becomes a record of an address, which is a different promise
        # than "both sides of the conversation". Tested on what is actually
        # written, not on the source text — an earlier version grepped the
        # module and matched "ip" inside "description".
        import json

        from call import record

        tmp = Path(tempfile.mkdtemp())
        original = record.CALLS_DIR
        try:
            record.CALLS_DIR = tmp
            r = record.CallRecord("callin-abc", {"id": "p1", "name": "Cliff"}, {})
            r.turn("caller", "hello")
            r.write(reason="caller hung up")
            written = json.loads(next(tmp.glob("*.json")).read_text(encoding="utf-8"))
        finally:
            record.CALLS_DIR = original
            shutil.rmtree(tmp, ignore_errors=True)

        for key in ("caller", "ip", "client", "network", "userAgent"):
            self.assertNotIn(key, written, f"the record on disk now carries {key}")


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
        "GET /health": "public",
        "GET /live": "public",                 # what the call card renders
        "GET /avatar/{persona_id}": "public",  # proxied so embeds work on https
        "GET /sounds/{name}": "public",        # uploaded call sounds
        "GET /sound-packs": "public",
        "GET /pack-sounds/{pack}/{name}": "public",
        # Gated twice over, which this column is too coarse to say: a real
        # call needs the guest code, a pipeline probe needs the admin one.
        "POST /token": "admin",
        # Frees a concurrency slot by room id. Unauthenticated on purpose —
        # the widget calls it on hangup — and safe because the id is 48 bits
        # of uuid4, so you cannot release a slot you were not already in.
        "POST /call-ended": "public",          # releases a slot; no secrets
        # The station does not sign its webhooks, so this cannot be
        # authenticated. It is safe only because it treats the payload as
        # untrusted data: store it, bust caches, never act on its contents.
        # If that ever changes, this entry is the thing to argue with.
        "POST /hooks/station": "public",
        "POST /auth/guest": "public",          # verifying a code needs no code
        "POST /auth/password": "admin",
        "GET /settings": "admin",
        "POST /settings": "admin",
        "GET /settings/options": "admin",
        "POST /settings/secrets": "admin",
        "GET /settings/sounds": "admin",
        "POST /settings/sounds": "admin",
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
        "subwave_dj_announce": "allow_announcements",
        "subwave_list_skills": "allow_skills",
        "subwave_run_skill": "allow_skills",
        # Station-wide and opt-in: these reach every listener, not just the
        # caller. Moved out of `never` deliberately in 0.9.54, off by default,
        # and capped by Actions per call.
        "subwave_skip_track": "allow_skip_track",
        "subwave_dj_segment": "allow_dj_segment",
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
    """Skipping a track and firing a programme beat reach every listener.

    They were `never` until 0.9.54. The operator's terms for opening them up
    were: off by default, and capped. Both are load-bearing, so both are
    tested — the default especially, because a permission that quietly
    defaults to on is how someone else's station starts skipping tracks.
    """

    def _tools(self, cfg: dict) -> set[str]:
        from call.tools import build_on_air_tools

        class _Guard:
            def mark_on_air(self, secs):
                pass

        from call.actions import CallActions

        built = build_on_air_tools(
            cfg, object(), CallActions(5), _Guard(), guarded=False)
        return {t.info.name for t in built}

    STATION_WIDE = {"subwave_skip_track", "subwave_dj_segment"}

    def test_both_are_off_by_default(self):
        # On the real defaults, not a hand-made dict — a permission that
        # quietly defaults to on is how someone else's station starts
        # skipping tracks. (Announcements ARE on by default; that predates
        # this and is a caller talking, not the programme changing.)
        cfg = settings_store.load()
        self.assertFalse(cfg.get("allow_skip_track"))
        self.assertFalse(cfg.get("allow_dj_segment"))
        self.assertEqual(self._tools(cfg) & self.STATION_WIDE, set())

    def test_each_appears_only_when_its_own_switch_is_on(self):
        self.assertEqual(
            self._tools({"allow_skip_track": True}) & self.STATION_WIDE,
            {"subwave_skip_track"})
        self.assertEqual(
            self._tools({"allow_dj_segment": True}) & self.STATION_WIDE,
            {"subwave_dj_segment"})

    def test_they_are_local_wrappers_so_the_action_cap_applies(self):
        # The whole reason they are not MCP allowlist entries. An MCP-served
        # tool never consults CallActions, so it would have no ceiling.
        from call.tools.registry import local_tool_names, mcp_allowlist

        cfg = {"allow_skip_track": True, "allow_dj_segment": True}
        served_locally = local_tool_names(cfg, local_search_available=True)
        self.assertIn("subwave_skip_track", served_locally)
        self.assertIn("subwave_dj_segment", served_locally)
        over_mcp = mcp_allowlist(cfg, local_search_available=True)
        self.assertNotIn("subwave_skip_track", over_mcp)
        self.assertNotIn("subwave_dj_segment", over_mcp)

    def test_they_refuse_once_the_call_has_spent_its_actions(self):
        import asyncio

        from call.actions import CallActions

        class _Guard:
            def mark_on_air(self, secs):
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


class TestHttpSurface(_TempStores):
    """The routes, exercised the way a browser reaches them.

    Everything else in this file tests a function. Nothing tested that the
    functions are actually *wired up* — so a route registered without its
    gate, a renamed path the widget still calls, or a middleware that stopped
    running would all pass a green suite and fail in the browser. The auth
    cases matter most: `admin_auth.verify` being correct is no comfort if
    `/settings` forgets to call it.
    """

    # Reads config, changes state, or costs money. Each must refuse a caller
    # who has no password once one is configured.
    PROTECTED = [
        ("GET", "/settings"),
        ("POST", "/settings"),
        ("GET", "/settings/sounds"),
        ("GET", "/prompt"),
        ("GET", "/calls"),
        ("GET", "/logs"),
    ]
    # Reachable by anyone: the widget itself, and what it reads to render.
    PUBLIC = ["/health", "/", "/call.js", "/style.css", "/embed.js"]

    def _serve(self, coro):
        import asyncio

        from aiohttp.test_utils import TestClient, TestServer

        import admin_auth
        import token_server

        self.admin_auth = admin_auth

        async def go():
            client = TestClient(TestServer(token_server.build_app()))
            await client.start_server()
            try:
                return await coro(client, token_server)
            finally:
                await client.close()

        return asyncio.run(go())

    def test_public_routes_answer_without_credentials(self):
        # Body length as well as status: a 200 carrying nothing would break
        # every embed while looking perfectly healthy in a status check.
        async def check(client, ts):
            out = {}
            for path in self.PUBLIC:
                r = await client.get(path)
                out[path] = (r.status, len(await r.read()))
            return out

        for path, (status, size) in self._serve(check).items():
            with self.subTest(path=path):
                self.assertEqual(status, 200, f"{path} should be public")
                if path != "/health":
                    self.assertGreater(size, 500, f"{path} served an empty body")

    def test_served_assets_match_the_files_on_disk(self):
        async def check(client, ts):
            out = {}
            for name in ("call.js", "style.css", "embed.js"):
                r = await client.get("/" + name)
                out[name] = len(await r.read())
            return out

        served = self._serve(check)
        for name, size in served.items():
            with self.subTest(asset=name):
                on_disk = (Path(__file__).parent.parent / "web-widget" / name).stat().st_size
                self.assertEqual(size, on_disk, f"{name} decoded to {size}, file is {on_disk}")

    def test_health_reports_the_running_version(self):
        async def check(client, ts):
            return await (await client.get("/health")).json()

        from version import APP_VERSION

        self.assertEqual(self._serve(check)["version"], APP_VERSION)

    def test_protected_routes_refuse_once_a_password_is_set(self):
        # The real regression risk: a route added without the gate. Asserting
        # 401 specifically — "not 200" would also pass for a route that has
        # been renamed out of existence.
        async def check(client, ts):
            self.admin_auth.set_password("a-real-password")
            out = {}
            for method, path in self.PROTECTED:
                r = await client.request(method, path, json={})
                out[f"{method} {path}"] = r.status
            return out

        for route, status in self._serve(check).items():
            with self.subTest(route=route):
                self.assertEqual(status, 401, f"{route} answered {status}, not 401")

    def test_the_break_glass_key_opens_them_again(self):
        # Proves the previous test is measuring a gate rather than a 404.
        async def check(client, ts):
            from api import auth as api_auth

            self.admin_auth.set_password("a-real-password")
            api_auth.ADMIN_KEY = "break-glass"
            try:
                r = await client.get("/settings", headers={"X-Admin-Key": "break-glass"})
                return r.status
            finally:
                api_auth.ADMIN_KEY = ""

        self.assertEqual(self._serve(check), 200)

    def test_a_wrong_password_is_refused(self):
        async def check(client, ts):
            self.admin_auth.set_password("a-real-password")
            r = await client.get("/settings", headers={"X-Admin-Key": "not-it"})
            return r.status

        self.assertEqual(self._serve(check), 401)

    def test_versioned_assets_are_immutable_and_bare_ones_revalidate(self):
        # The caching contract from 0.9.53. If this inverts, either everyone
        # re-downloads 150KB on every load, or they get a stale interface
        # after an update — the bug no-cache existed to prevent.
        async def check(client, ts):
            from api import widget as api_widget

            good = await client.get(f"/call.js?v={api_widget.asset_tag('call.js')}")
            bare = await client.get("/call.js")
            stale = await client.get("/call.js?v=0.0.1")
            page = await client.get("/")
            return {
                "versioned": good.headers.get("Cache-Control"),
                "bare": bare.headers.get("Cache-Control"),
                "stale": stale.headers.get("Cache-Control"),
                "html": page.headers.get("Cache-Control"),
            }

        got = self._serve(check)
        self.assertIn("immutable", got["versioned"])
        self.assertEqual(got["bare"], "no-cache")
        self.assertEqual(got["stale"], "no-cache")
        self.assertEqual(got["html"], "no-cache")

    def test_compression_is_negotiated_not_forced(self):
        # Forcing it would corrupt the response for a client that can't
        # decode; never offering it was the 0.9.53 bug.
        async def check(client, ts):
            asked = await client.get("/call.js", headers={"Accept-Encoding": "gzip, deflate"})
            plain = await client.get("/call.js", headers={"Accept-Encoding": "identity"})
            return {
                "encoding": asked.headers.get("Content-Encoding"),
                "vary": asked.headers.get("Vary"),
                "plain_encoding": plain.headers.get("Content-Encoding"),
                "plain_body": len(await plain.read()),
            }

        got = self._serve(check)
        self.assertIn(got["encoding"], ("gzip", "deflate"))
        self.assertEqual(got["vary"], "Accept-Encoding")
        self.assertIsNone(got["plain_encoding"])
        self.assertGreater(got["plain_body"], 10000)


class TestAssetVersioning(unittest.TestCase):
    """The html must point at versioned asset URLs.

    This is the silent kind of failure: if index.html's script or link tag is
    ever reformatted, the rewrite quietly matches nothing, the browser asks for
    the bare /call.js, and the middleware correctly answers `no-cache` — so
    every visitor silently goes back to re-downloading 150KB on every load with
    nothing broken enough to notice.
    """

    def test_the_served_html_versions_its_own_assets(self):
        from api import widget as api_widget

        api_widget._index_cache.update(mtime=0.0, html="")
        html = api_widget._versioned_index()
        self.assertIn(f'src="/call.js?v={api_widget.asset_tag("call.js")}"', html)
        self.assertIn(
            f'href="/style.css?v={api_widget.asset_tag("style.css")}"', html)
        self.assertNotIn('src="/call.js"', html)
        self.assertNotIn('href="/style.css"', html)

    def test_the_tag_changes_when_the_file_does(self):
        # The bug this prevents: assets are served `immutable` for a year, so
        # keying the URL on APP_VERSION meant any change to call.js without a
        # version bump left every browser pinned to the old copy.
        #
        # Tested by actually changing a file. An earlier version of this
        # asserted that two different assets had different tags, which passed
        # locally and failed in CI — a fresh checkout stamps every file with
        # the same mtime, and sharing a tag was never the property that
        # mattered anyway.
        import os
        import time

        from api import widget as api_widget

        original = api_widget.WIDGET_DIR
        tmp = Path(tempfile.mkdtemp())
        try:
            api_widget.WIDGET_DIR = tmp
            asset = tmp / "call.js"
            asset.write_text("// one", encoding="utf-8")
            before = api_widget.asset_tag("call.js")

            asset.write_text("// two", encoding="utf-8")
            os.utime(asset, (time.time() + 5, time.time() + 5))
            self.assertNotEqual(
                api_widget.asset_tag("call.js"), before,
                "editing the file left the cache key unchanged")

            # A missing file must not crash the page; it falls back.
            from version import APP_VERSION

            self.assertEqual(api_widget.asset_tag("nope.js"), APP_VERSION)
        finally:
            api_widget.WIDGET_DIR = original
            shutil.rmtree(tmp, ignore_errors=True)

    def test_it_is_the_real_widget_html(self):
        # Guards against the rewrite silently operating on an empty string.
        from api import widget as api_widget

        html = api_widget._versioned_index()
        self.assertIn("<html", html.lower())
        self.assertGreater(len(html), 2000)


class TestSoundPacks(unittest.TestCase):
    """Bundled sound assets: a pack is a folder, not a code change.

    The tier that did not exist before — uploads worked and synthesis worked,
    so shipping a default ring meant writing oscillator code in the widget.
    """

    def setUp(self):
        import sounds

        self.sounds = sounds
        self._real_dir = sounds.ASSETS_DIR
        self.tmp = Path(tempfile.mkdtemp())
        sounds.ASSETS_DIR = self.tmp

    def tearDown(self):
        self.sounds.ASSETS_DIR = self._real_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pack(self, name: str, files=(), label: str | None = None) -> Path:
        folder = self.tmp / name
        folder.mkdir(parents=True, exist_ok=True)
        for f in files:
            (folder / f).write_bytes(b"not really audio")
        if label:
            (folder / "label.txt").write_text(label, encoding="utf-8")
        return folder

    def test_with_no_assets_at_all_nothing_changes(self):
        # The product has always worked with zero audio files and must keep
        # doing so: every sound resolves to "", which the widget synthesizes.
        self.assertEqual(self.sounds.asset_url("classic", "ring"), "")
        self.assertEqual(self.sounds.assets_for("classic"), {})
        self.assertEqual(
            sorted(p for p, _ in self.sounds.packs()), ["classic", "phone"])

    def test_a_new_folder_becomes_a_new_pack(self):
        self._pack("vintage", ["ring.mp3"])
        self.assertIn(("vintage", "Vintage"), self.sounds.packs())
        self.assertEqual(
            self.sounds.asset_url("vintage", "ring"), "/pack-sounds/vintage/ring.mp3")

    def test_a_pack_can_name_itself(self):
        self._pack("old-bell", ["ring.mp3"], label="Old Bell — 1950s exchange")
        self.assertIn(("old-bell", "Old Bell — 1950s exchange"), self.sounds.packs())

    def test_a_folder_name_without_a_label_is_tidied(self):
        self._pack("old-bell", ["ring.mp3"])
        self.assertIn(("old-bell", "Old Bell"), self.sounds.packs())

    def test_a_partial_pack_only_covers_what_it_ships(self):
        # One file is a valid pack; everything else stays synthesized.
        self._pack("sparse", ["ring.mp3"])
        self.assertEqual(self.sounds.assets_for("sparse"), {"ring": "/pack-sounds/sparse/ring.mp3"})
        self.assertEqual(self.sounds.asset_url("sparse", "hangup"), "")

    def test_a_folder_named_after_a_builtin_supplies_files_for_it(self):
        # Not a new pack — the curated label is kept and only the one sound
        # is replaced.
        self._pack("classic", ["ring.mp3"])
        ids = [p for p, _ in self.sounds.packs()]
        self.assertEqual(ids.count("classic"), 1)
        self.assertIn(("classic", self.sounds.SYNTHESIZED["classic"]), self.sounds.packs())
        self.assertEqual(self.sounds.asset_url("classic", "ring"), "/pack-sounds/classic/ring.mp3")
        self.assertEqual(self.sounds.asset_url("classic", "pickup"), "")

    def test_mp3_wins_when_a_pack_ships_several_encodings(self):
        # Every browser plays mp3; ogg and friends are less reliable.
        self._pack("multi", ["ring.ogg", "ring.mp3", "ring.wav"])
        self.assertEqual(self.sounds.asset_url("multi", "ring"), "/pack-sounds/multi/ring.mp3")

    def test_a_pack_name_cannot_escape_the_assets_directory(self):
        for evil in ("../../etc", "..", "a/b", ""):
            with self.subTest(pack=evil):
                self.assertIsNone(self.sounds.file_for(evil, "ring"))

    def test_only_the_five_known_sounds_resolve(self):
        self._pack("vintage", ["ring.mp3", "voicemail.mp3"])
        self.assertIsNone(self.sounds.file_for("vintage", "voicemail"))

    def test_the_panel_dropdown_reads_packs_from_disk(self):
        # settings.schema_payload is what the panel builds its Sound set
        # dropdown from — a new folder has to reach it with no code change.
        self._pack("vintage", ["ring.mp3"])
        choices = settings_store.schema_payload()["fields"]["sound_pack"]["choices"]
        self.assertIn(["vintage", "Vintage"], [list(c) for c in choices])


class TestSilentCallIsRecorded(unittest.TestCase):
    """A call that received no caller audio has to say so.

    The first off-LAN caller failed exactly this way and nothing in our own
    logs mentioned it: room joined, agent started, greeting played, line
    dropped at ~15s with nothing received. The diagnosis lived only in
    LiveKit's ICE candidates and the caller's browser console.
    """

    def _session(self, heard: int):
        from call.record import CallRecord
        from call.session import CallSession

        s = CallSession.__new__(CallSession)          # no room, no livekit
        s.heard = {"n": heard}
        s.ctx = type("C", (), {"room": type("R", (), {"name": "callin-test"})()})()
        s.record = CallRecord("callin-test", {"name": "Test DJ"}, {})
        return s

    def test_a_call_with_no_caller_audio_is_flagged(self):
        s = self._session(heard=0)
        s._note_if_nothing_was_heard(15.0, [("dj", "Evening, you're through.")])
        problems = s.record.data["problems"]
        self.assertEqual(len(problems), 1)
        what = problems[0]["what"]
        self.assertIn("No audio was ever received", what)
        self.assertIn("off-LAN", what)          # the likeliest cause, named
        self.assertIn("the DJ did speak", what)  # so a mic problem is separable

    def test_a_call_that_heard_the_caller_is_not_flagged(self):
        s = self._session(heard=3)
        s._note_if_nothing_was_heard(90.0, [("caller", "hello"), ("dj", "hi")])
        self.assertEqual(s.record.data["problems"], [])

    def test_it_records_whether_the_dj_spoke_at_all(self):
        # A DJ that never spoke points at the pipeline; one that did points at
        # the caller's side. The record has to keep them apart.
        s = self._session(heard=0)
        s._note_if_nothing_was_heard(12.0, [])
        self.assertIn("the DJ did not speak", s.record.data["problems"][0]["what"])


class TestCallRecordTimestamps(unittest.TestCase):
    """A call record has to say WHEN, unambiguously.

    These were naive container-local times, and the container runs in UTC — so
    an operator four hours west read every record four hours off, and nothing
    could correct it because the string carried no offset at all.
    """

    def test_timestamps_carry_an_offset(self):
        import datetime

        from call.record import _iso

        out = _iso(1770000000.0)
        parsed = datetime.datetime.fromisoformat(out)
        self.assertIsNotNone(parsed.tzinfo, f"{out} has no timezone")
        self.assertEqual(parsed.utcoffset(), datetime.timedelta(0))

    def test_the_instant_survives_the_round_trip(self):
        import datetime

        from call.record import _iso

        ts = 1770000000.0
        back = datetime.datetime.fromisoformat(_iso(ts)).timestamp()
        self.assertAlmostEqual(back, ts, delta=1.0)

    def test_records_still_sort_by_string(self):
        # The panel merges speech and tool events and sorts them as plain
        # strings, so the format must stay lexicographically ordered.
        from call.record import _iso

        stamps = [_iso(1770000000.0 + n) for n in (0, 5, 61, 3600)]
        self.assertEqual(stamps, sorted(stamps))


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


class TestPanelMarkup(unittest.TestCase):
    """The panel builds itself from the schema, but it can only fill in a
    control the markup actually contains — `byKind` skips any field with no
    matching element id. So a setting declared in settings.py with no input in
    index.html is simply unreachable, with nothing to say so. That shipped
    twice (avoid_on_air_overlap, on_air_quiet_secs)."""

    def setUp(self):
        import re

        html = (Path(__file__).parent.parent / "web-widget" / "index.html").read_text(
            encoding="utf-8"
        )
        self.ids = set(re.findall(r'id="([^"]+)"', html))
        self.groups = set(re.findall(r'data-group="([^"]+)"', html))

    def test_every_schema_field_has_a_control(self):
        missing = sorted(f for f in settings_store.SCHEMA if f not in self.ids)
        self.assertFalse(
            missing,
            "settings with no input in index.html — they cannot be changed from "
            f"the panel: {missing}",
        )

    def test_every_schema_group_has_a_section(self):
        missing = sorted(g for g, *_ in settings_store.GROUPS if g not in self.groups)
        self.assertFalse(missing, f"schema groups with no section: {missing}")

    def test_every_field_belongs_to_a_real_group(self):
        known = {g for g, *_ in settings_store.GROUPS}
        strays = sorted(
            f for f, meta in settings_store.SCHEMA.items() if meta["group"] not in known
        )
        self.assertFalse(strays, f"settings in an unknown group: {strays}")


class TestPanelLoadsOnOpen(unittest.TestCase):
    """Opening the panel must actually fetch the settings.

    0.9.61 shipped with an admin panel that had nothing in it: every dropdown
    empty, no values, no section headers, and no login prompt either. The cause
    was one word. The gear's "have I loaded already?" guard was
    `if (... || options || loading) return;`, written when `options` started as
    null — then 0.9.58 changed it to `{}` so the panel could paint before the
    slow provider lists arrived. `{}` is truthy, so from that commit on the
    guard fired on the very first open and `loadSettings()` was never called.

    Nothing failed loudly: no request, no console error, no 401. There is no JS
    test runner here, so this reads the source — in the same spirit as
    TestPanelMarkup above. It is deliberately narrow: the loaded-flag must be
    its own boolean, never a data container tested for truthiness."""

    @classmethod
    def setUpClass(cls):
        cls.js = (Path(__file__).parent.parent / "web-widget" / "panel.js").read_text(
            encoding="utf-8"
        )

    def _gear_handler(self) -> str:
        start = self.js.index("$('gearBtn').onclick")
        return self.js[start : self.js.index("};", start)]

    def test_the_gear_guard_uses_a_dedicated_flag(self):
        guard = self._gear_handler()
        self.assertIn(
            "loaded ||",
            guard,
            "the gear's skip-the-fetch guard must test the `loaded` flag",
        )

    def test_the_gear_guard_never_tests_a_data_container(self):
        # The actual bug: any of these is an object that is truthy while empty,
        # so using one as "already loaded" skips the fetch on the first open.
        guard = self._gear_handler()
        for name in ("options", "overrides", "resolved", "secrets", "SCHEMA"):
            self.assertNotIn(
                f"|| {name} ||",
                guard,
                f"`{name}` is truthy when empty — it cannot stand in for `loaded`",
            )

    def test_loading_the_settings_sets_the_flag(self):
        start = self.js.index("async function loadSettings()")
        body = self.js[start : self.js.index("\n  }", start)]
        self.assertIn(
            "loaded = true",
            body,
            "loadSettings must record that the panel is filled, or the gear "
            "refetches everything on every open",
        )

    def test_the_flag_starts_false(self):
        self.assertIn("let loaded = false;", self.js)

    def test_every_group_belongs_to_a_real_supergroup(self):
        known = {s for s, *_ in settings_store.SUPERGROUPS}
        strays = sorted(
            g for g, sup, *_ in settings_store.GROUPS if sup not in known
        )
        self.assertFalse(strays, f"groups under an unknown supergroup: {strays}")

    def test_every_declared_field_is_storable(self):
        # A SCHEMA entry with no FIELDS entry renders a control that silently
        # discards whatever you type into it (save() drops unknown keys).
        strays = sorted(f for f in settings_store.SCHEMA if f not in settings_store.FIELDS)
        self.assertFalse(strays, f"settings that cannot be saved: {strays}")


class TestSecrets(_TempStores):
    def test_blank_means_unchanged_and_clear_is_explicit(self):
        secrets_store.save({"openai_api_key": "sk-real"}, [])
        self.assertEqual(secrets_store.get("openai_api_key"), "sk-real")
        secrets_store.save({"openai_api_key": ""}, [])   # untouched masked field
        self.assertEqual(secrets_store.get("openai_api_key"), "sk-real")
        secrets_store.save({}, ["openai_api_key"])       # explicit clear
        self.assertEqual(secrets_store.get("openai_api_key"), "")

    def test_status_never_reveals_material(self):
        secrets_store.save({"anthropic_api_key": "sk-ant-secret123"}, [])
        s = secrets_store.status()["anthropic_api_key"]
        self.assertTrue(s["set"])
        self.assertNotIn("secret123", json.dumps(s))

    def test_apply_to_env_sets_and_reverts(self):
        os.environ["OPENAI_API_KEY"] = "from-dotenv"
        secrets_store.save({"openai_api_key": "from-panel"}, [])
        self.assertEqual(os.environ["OPENAI_API_KEY"], "from-panel")
        secrets_store.save({}, ["openai_api_key"])
        self.assertEqual(os.environ.get("OPENAI_API_KEY"), "from-dotenv")


class TestBrainSplit(_TempStores):
    """Phase 3's seam: what the DJ KNOWS and how it BEHAVES are separable.

    Each half has to be buildable and assertable without the other — that is
    the whole point of the split, and the thing a later edit is most likely to
    quietly undo by reaching for a station read from inside a rule.
    """

    FACT_MARKERS = ("Now playing", "Just played", "Coming up",
                    "Other shows on this station", "Segments you can run")
    RULE_MARKERS = ("# Running the call", "# Closing a call",
                    "Keep the call moving", "# What you can do")

    class _FakeStation:
        """The only station call the briefing makes on its own."""

        async def schedule(self):
            return {"shows": [{"id": "s_other", "name": "Morning Drive"}]}

    def _facts(self, cfg: dict, snap: dict | None = None) -> str:
        import asyncio

        snap = snap or {
            "now_playing": {"nowPlaying": {"title": "Dreams", "artist": "Fleetwood Mac"}},
            "state": {"history": [{"title": "Tusk", "artist": "Fleetwood Mac"}],
                      "upcoming": [{"title": "Sara", "artist": "Fleetwood Mac"}]},
            "session": {},
            "skills": [{"kind": "weather", "label": "Weather"}],
        }
        return asyncio.run(
            briefing.station_context(self._FakeStation(), cfg, snap, {"id": "s_now"})
        )

    def test_conduct_is_a_pure_function_of_settings(self):
        # No station, no network, no settings file — if a rule ever needs a
        # station read, the split has leaked and this stops compiling.
        text = conduct.rules({})
        for marker in self.RULE_MARKERS:
            self.assertIn(marker, text)

    def test_conduct_carries_no_station_facts(self):
        text = conduct.rules({"allow_skills": True, "context_schedule": True})
        for marker in self.FACT_MARKERS:
            self.assertNotIn(marker, text)

    def test_briefing_carries_no_rules(self):
        text = self._facts({"allow_skills": True, "context_schedule": True})
        for marker in self.RULE_MARKERS:
            self.assertNotIn(marker, text)

    def test_briefing_reports_what_the_station_is_doing(self):
        text = self._facts({})
        self.assertIn("Now playing: \"Dreams\" by Fleetwood Mac", text)
        self.assertIn("Just played", text)
        self.assertIn("Coming up", text)

    def test_briefing_reads_the_schedule_only_when_asked(self):
        self.assertNotIn("Morning Drive", self._facts({}))
        self.assertIn("Morning Drive", self._facts({"context_schedule": True}))

    def test_briefing_lists_segments_only_when_they_can_be_run(self):
        self.assertNotIn("weather", self._facts({}))
        self.assertIn("weather", self._facts({"allow_skills": True}))

    def test_each_toggle_picks_exactly_one_fragment(self):
        # The pairs contradict each other by design, so shipping both is the
        # failure mode — that is how a caller gets asked what kind of fun they
        # meant AND has something submitted anyway.
        pairs = [
            ({"confirm_requests": True},
             "say it back and get a quick yes", "No need to confirm"),
            ({"confirm_requests": False},
             "No need to confirm", "say it back and get a quick yes"),
            ({"shape_vague_requests": True},
             "two or three real directions", "don't interrogate them"),
            ({"shape_vague_requests": False},
             "don't interrogate them", "two or three real directions"),
            ({"ask_caller_name": True},
             "ask once, briefly", "Don't ask the caller their name"),
            ({"ask_caller_name": False},
             "Don't ask the caller their name", "ask once, briefly"),
        ]
        for cfg, present, absent in pairs:
            with self.subTest(cfg=cfg):
                text = conduct.rules(cfg)
                self.assertIn(present, text)
                self.assertNotIn(absent, text)

    def test_offering_a_segment_needs_both_switches(self):
        self.assertNotIn("Offering a segment", conduct.rules({"offer_skills": True}))
        self.assertNotIn("Offering a segment", conduct.rules({"allow_skills": True}))
        self.assertIn(
            "Offering a segment",
            conduct.rules({"allow_skills": True, "offer_skills": True}),
        )

    def test_the_two_halves_do_not_import_each_other(self):
        # Independence is the property worth protecting: a station field
        # should never be an edit to conduct, and a bad call should never be
        # an edit to briefing. Imports, not prose — the docstrings are allowed
        # to point at each other.
        import ast
        import inspect

        def imported(module) -> set[str]:
            names: set[str] = set()
            for node in ast.walk(ast.parse(inspect.getsource(module))):
                if isinstance(node, ast.Import):
                    names.update(a.name for a in node.names)
                elif isinstance(node, ast.ImportFrom):
                    names.add(node.module or "")
                    names.update(f"{node.module}.{a.name}" for a in node.names)
            return names

        self.assertFalse([n for n in imported(briefing) if "conduct" in n])
        self.assertFalse([n for n in imported(conduct) if "briefing" in n])
        # Conduct imports nothing from the station at all — it is settings in,
        # text out.
        self.assertFalse([n for n in imported(conduct) if "station" in n])

    def test_the_assembled_prompt_is_briefing_then_conduct(self):
        import asyncio

        from station import StationClient

        snapshot = {"dj": {"station": "Yosemite FM"}, "personas": [],
                    "now_playing": {"nowPlaying": {"title": "Dreams"}},
                    "state": {}, "session": {}, "schedule": {}}

        async def build() -> str:
            station = StationClient()
            try:
                return await brain.build_system_prompt(
                    station, {"id": "p", "name": "Dalia", "soul": "x"},
                    snapshot=snapshot)
            finally:
                await station.aclose()

        text = asyncio.run(build())
        facts_at = text.index("Now playing")
        rules_at = text.index("# Running the call")
        self.assertLess(facts_at, rules_at)
        # And the identity header still comes before both.
        self.assertLess(text.index("a DJ on Yosemite FM"), facts_at)


class TestPromptAssembly(_TempStores):
    def test_call_momentum_rules_are_always_in_the_prompt(self):
        # Observed on real calls: without this block the DJ interviews the
        # caller ("what are you planning tomorrow?"). It must be present
        # regardless of settings — it is not an operator toggle.
        import asyncio

        from station import StationClient

        snapshot = {"dj": {}, "personas": [], "now_playing": {},
                    "state": {}, "session": {}, "schedule": {}}
        persona = {"id": "p_test", "name": "Test DJ", "soul": "A test soul."}

        async def build() -> str:
            station = StationClient()
            try:
                return await brain.build_system_prompt(
                    station, persona, snapshot=snapshot
                )
            finally:
                await station.aclose()

        text = asyncio.run(build())
        self.assertIn("Keep the call moving", text)
        self.assertIn("dig into the caller's", text)
        self.assertIn("quippy tangent", text)
        # And the operator's own steers still layer on top when set.
        settings_store.save({"style_answering": "keep answers to two sentences"})
        text = asyncio.run(build())
        self.assertIn("keep answers to two sentences", text)
        self.assertIn("Keep the call moving", text)
        # Offering segments is opt-in and needs skills enabled too.
        self.assertNotIn("Offering a segment", text)
        settings_store.save({"allow_skills": True, "offer_skills": True})
        self.assertIn("Offering a segment", asyncio.run(build()))
        settings_store.save({"offer_skills": ""})
        self.assertNotIn("Offering a segment", asyncio.run(build()))

    def test_tonights_episode_is_its_own_block(self):
        # The Show Card is the standing format the show runs every week;
        # `episodeAngle` is what THIS episode is about. It gets its own block
        # rather than hanging off the card, so a station that can't resolve
        # the show still keeps the one piece of framing it did publish.
        import asyncio

        class _Station:
            async def active_show(self, now_playing=None):
                return {"id": "s_pub", "name": "Donovan's Pub",
                        "topic": "Irish folk and trad.",
                        "episodeAngle": "A relaxed morning session."}

            async def schedule(self):
                raise AssertionError("assembling a prompt read the schedule")

        snapshot = {"dj": {}, "personas": [], "now_playing": {},
                    "state": {}, "session": {}, "schedule": {}}
        text = asyncio.run(brain.build_system_prompt(
            _Station(), {"id": "p_danny", "name": "Danny", "soul": "A soul."},
            snapshot=snapshot))

        self.assertIn("Tonight's episode in particular", text)
        self.assertIn("A relaxed morning session.", text)
        self.assertIn("Irish folk and trad.", text)


class TestCallPrivacy(_TempStores):
    """Every call is a first call. The back-to-air line from the LAST caller
    goes into the station's own chatter feed, and it was being fed straight
    back into the next caller's prompt — so the DJ carried on where the
    previous conversation left off, in front of a stranger."""

    def _prompt(self, session: dict) -> str:
        import asyncio

        from station import StationClient

        snapshot = {"dj": {}, "personas": [], "now_playing": {},
                    "state": {}, "session": session, "schedule": {}}
        persona = {"id": "p_test", "name": "Test DJ", "soul": "A test soul."}

        async def build() -> str:
            station = StationClient()
            try:
                return await brain.build_system_prompt(
                    station, persona, snapshot=snapshot
                )
            finally:
                await station.aclose()

        return asyncio.run(build())

    def test_previous_call_never_reaches_the_next_caller(self):
        session = {"messages": [
            {"kind": "link", "text": "Back with you after that one."},
            {"kind": "callin", "text": "Just had Sarah on the line about her divorce."},
        ]}
        text = self._prompt(session)
        self.assertIn("Back with you after that one.", text)   # ordinary chatter stays
        self.assertNotIn("Sarah", text)
        self.assertNotIn("divorce", text)

    def test_the_dj_is_told_which_segments_this_station_actually_has(self):
        # Without the catalogue the agent either guessed at segment names or
        # spent a turn asking the station mid-call, and the caller heard the
        # pause. "What can you do?" was answered vaguely for the same reason.
        import asyncio

        from station import StationClient

        snapshot = {"dj": {}, "personas": [], "now_playing": {}, "state": {},
                    "session": {}, "schedule": {},
                    "skills": [{"kind": "weather", "label": "Weather", "cooldownMin": 60},
                               {"kind": "storytime", "label": "Story time"}]}
        persona = {"id": "p_test", "name": "Test DJ", "soul": "A test soul."}

        def build() -> str:
            async def go():
                station = StationClient()
                try:
                    return await brain.build_system_prompt(
                        station, persona, snapshot=snapshot)
                finally:
                    await station.aclose()
            return asyncio.run(go())

        # Off by default: no segment list, because it can't run any.
        self.assertNotIn("weather", build().lower())

        settings_store.save({"allow_skills": True})
        text = build()
        self.assertIn("weather", text)
        self.assertIn("storytime", text)
        self.assertIn("these and no others", text)

    def test_segment_list_names_only_no_cooldowns(self):
        # Telling the DJ the intervals made it ration segments itself and
        # explain timings to callers. The station decides if one is due.
        out = briefing._fmt_skills([
            {"kind": "weather", "label": "Weather", "cooldownMin": 60},
            {"kind": "storytime", "label": "Story time", "cooldownMin": 45},
        ])
        self.assertIn("weather", out)
        self.assertIn("storytime", out)
        self.assertNotIn("60", out)
        self.assertNotIn("min", out)
        self.assertIn("the station decides if it's due", out)

    def test_prompt_tells_the_dj_how_to_close_a_call(self):
        text = self._prompt({})
        self.assertIn("Closing a call", text)
        self.assertIn("anything else before i let you go?", text.lower())
        self.assertIn("end_call", text)
        # And the guard against closing early, which is the real risk.
        self.assertIn("is NOT a call to close", text)
        self.assertIn("nothing good about a short", text)
        self.assertIn("never end a call because it's gone quiet", text.lower())

    def test_the_closing_check_is_the_end_not_a_full_stop_on_every_action(self):
        # Measured against the live deployment: the closing question landed in
        # eight of twelve turns, attached to every completed action. The model
        # was reading "I did the thing" as "the call is over", and momentum
        # agreed with it — so both places had to stop saying so.
        text = self._prompt({})
        self.assertIn("Calls end when the CALLER is finished", text)
        self.assertIn("is the LAST thing you say in a call", text)
        self.assertIn("nothing to angle for", text)
        # Momentum must not undo it by asking for a wind-down after each action.
        self.assertNotIn("wind toward a close", text)
        self.assertIn("does NOT mean moving it", text)

    def test_a_refused_hangup_does_not_invite_a_new_subject(self):
        # A caller who says goodbye inside the first minute was getting the
        # sign-off AND then a fresh line of questioning, because the refusal
        # read as "go find something else to talk about".
        text = self._prompt({})
        self.assertIn("overruled on the timing, not on the goodbye", text)
        self.assertIn("Do NOT open a new subject", text)

    def test_a_mood_request_either_ships_or_offers_options_never_both(self):
        # The two rules contradict each other, so exactly one must be in the
        # prompt. Shipping both is how a caller gets asked what kind of fun
        # they meant AND has something submitted anyway.
        off = self._prompt({})
        self.assertIn("don't interrogate them", off)
        self.assertNotIn("two or three real directions", off)

        settings_store.save({"shape_vague_requests": True})
        on = self._prompt({})
        self.assertIn("two or three real directions", on)
        self.assertNotIn("don't interrogate them", on)
        # Concrete options, never an open question, and only one round.
        self.assertIn("never an open", on)
        self.assertIn("ONE round", on)
        self.assertIn("don't invent names", on)

    def test_prompt_carries_a_triage_guide(self):
        text = self._prompt({})
        self.assertIn("Running the call", text)
        self.assertIn("that IS a request", text)      # a vibe is not a search
        self.assertIn("never recite a menu", text)    # the "what can you do" answer
        self.assertIn("Never two questions in a row", text)

    def test_the_prompt_names_the_operators_station_not_ours(self):
        # A DJ on Yosemite FM told callers they were live on SUB/WAVE — the
        # software's name, which no listener has heard of. GET /dj has carried
        # the real one all along.
        import asyncio

        from station import StationClient

        def build(dj: dict) -> str:
            snapshot = {"dj": dj, "personas": [], "now_playing": {}, "state": {},
                        "session": {}, "schedule": {}, "skills": []}
            async def go():
                station = StationClient()
                try:
                    return await brain.build_system_prompt(
                        station, {"id": "p", "name": "Dalia", "soul": "x"},
                        snapshot=snapshot)
                finally:
                    await station.aclose()
            return asyncio.run(go())

        text = build({"station": "Yosemite FM"})
        self.assertIn("a DJ on Yosemite FM", text)
        self.assertNotIn("SUB/WAVE", text)
        # Falls back only when the station doesn't say.
        self.assertIn("SUB/WAVE", build({}))

    def test_prompt_states_the_caller_is_new(self):
        text = self._prompt({})
        self.assertIn("This caller is NEW", text)

    def test_programme_intro_is_background_not_a_topic(self):
        session = {"messages": [
            {"kind": "programme-intro", "text": "Welcome to the Midnight Hour."},
        ]}
        text = self._prompt(session)
        # Still pinned, so the fiction holds…
        self.assertIn("Midnight Hour", text)
        # …but explicitly fenced off as background.
        self.assertIn("Do NOT recap it", text)
        self.assertIn("taking over from another DJ", text)


class TestAdminAuth(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        import admin_auth
        self.auth = admin_auth
        self._old_path = admin_auth.AUTH_PATH
        admin_auth.AUTH_PATH = Path(self._tmp.name) / "admin-auth.json"

    def tearDown(self):
        self.auth.AUTH_PATH = self._old_path
        self._tmp.cleanup()

    def test_set_verify_and_change(self):
        self.assertFalse(self.auth.is_set())
        self.auth.set_password("correct horse battery")
        self.assertTrue(self.auth.is_set())
        self.assertTrue(self.auth.verify("correct horse battery"))
        self.assertFalse(self.auth.verify("wrong"))
        self.assertFalse(self.auth.verify(""))
        self.auth.set_password("new password here")
        self.assertFalse(self.auth.verify("correct horse battery"))
        self.assertTrue(self.auth.verify("new password here"))

    def test_no_plaintext_on_disk(self):
        self.auth.set_password("do-not-store-me")
        raw = self.auth.AUTH_PATH.read_text()
        self.assertNotIn("do-not-store-me", raw)

    def test_guest_password_is_independent_of_admin(self):
        self.auth.set_password("admin password here")
        self.assertFalse(self.auth.guest_is_set())
        self.auth.set_guest_password("guestcode")
        self.assertTrue(self.auth.guest_is_set())
        # Guest opens the phone but NOT the panel — the whole point.
        self.assertTrue(self.auth.verify_guest("guestcode"))
        self.assertFalse(self.auth.verify("guestcode"))
        # Admin opens both, so an operator carries one password.
        self.assertTrue(self.auth.verify_guest("admin password here"))
        self.assertTrue(self.auth.verify("admin password here"))
        # Changing the admin password leaves the guest one alone.
        self.auth.set_password("a different admin one")
        self.assertTrue(self.auth.verify_guest("guestcode"))

    def test_the_two_passwords_cannot_be_made_identical(self):
        # Sharing a code with callers that also opens the settings panel is
        # the single most likely way to get this wrong.
        self.auth.set_password("shared secret pw")
        with self.assertRaises(ValueError):
            self.auth.set_guest_password("shared secret pw")
        self.assertFalse(self.auth.guest_is_set())
        # …and from the other direction too.
        self.auth.set_guest_password("guestcode")
        with self.assertRaises(ValueError):
            self.auth.set_password("guestcode")
        self.assertTrue(self.auth.verify("shared secret pw"))   # unchanged

    def test_clearing_the_guest_password_reopens_the_line(self):
        self.auth.set_guest_password("guestcode")
        self.auth.clear_guest_password()
        self.assertFalse(self.auth.guest_is_set())
        self.assertFalse(self.auth.verify_guest("guestcode"))

    def test_guest_gate_is_open_until_a_code_exists(self):
        from api import auth as api_auth

        self.assertIsNone(api_auth._guest_check("", "ip-a"))
        self.auth.set_guest_password("guestcode")
        self.assertIsNotNone(api_auth._guest_check("", "ip-a"))
        self.assertIsNotNone(api_auth._guest_check("wrong", "ip-a"))
        self.assertIsNone(api_auth._guest_check("guestcode", "ip-a"))
        api_auth._auth_state.pop("guest:ip-a", None)

    def test_guest_failures_do_not_lock_the_operator_out(self):
        # A caller fumbling the door code must not ban the address from the
        # settings panel — the two live in separate buckets.
        from api import auth as api_auth

        self.auth.set_guest_password("guestcode")
        api_auth._auth_state.pop("ip-b", None)
        api_auth._auth_state.pop("guest:ip-b", None)
        for _ in range(10):
            api_auth._guest_check("nope", "ip-b")
        self.assertIsNotNone(api_auth._auth_gate("guest:ip-b"))
        self.assertIsNone(api_auth._auth_gate("ip-b"))
        api_auth._auth_state.pop("guest:ip-b", None)

    def test_lockout_cooldown_then_ban(self):
        # 5 wrong tries -> cooldown; a second round of 5 -> banned until
        # restart. Uses the token server's pure helpers with a fake IP.
        from api import auth as api_auth

        ip = "test-ip-1"
        api_auth._auth_state.pop(ip, None)
        for _ in range(4):
            msg = api_auth._auth_fail(ip)
            self.assertIn("tr", msg)          # "N tries left"
        msg = api_auth._auth_fail(ip)               # 5th -> cooldown starts
        self.assertIn("try again", msg)
        self.assertIsNotNone(api_auth._auth_gate(ip))

        # Simulate the cooldown expiring, then a second round of failures.
        api_auth._auth_state[ip]["cooldown_until"] = 0
        self.assertIsNone(api_auth._auth_gate(ip))
        for _ in range(5):
            msg = api_auth._auth_fail(ip)
        self.assertIn("blocked until the app restarts", msg)
        self.assertIn("blocked", api_auth._auth_gate(ip))

        # Success clears everything (and "restart" == fresh state).
        api_auth._auth_clear(ip)
        self.assertIsNone(api_auth._auth_gate(ip))


class _FakeRequest:
    """Just enough of an aiohttp request for _caller_key/_check_usage."""

    def __init__(self, ip="1.2.3.4", fwd=""):
        self.headers = {"X-Forwarded-For": fwd} if fwd else {}
        self.remote = ip


class TestUsageControls(unittest.TestCase):
    """The guard against runaway spend — every refusal must fire, phrased
    in-world, and 0 must mean unlimited."""

    def setUp(self):
        from api import live as api_live
        from api import tokens as api_tokens

        self.ts, self.live = api_tokens, api_live
        api_tokens._recent_mints[:] = []
        api_tokens._caller_last.clear()
        api_tokens._live_calls.clear()

    tearDown = setUp  # leave module state clean either way

    def test_concurrent_limit(self):
        import time
        self.ts._live_calls.update({"room-a": time.time(), "room-b": time.time()})
        msg = self.ts._check_usage(_FakeRequest(), {"max_concurrent_calls": 2})
        self.assertIn("tied up", msg)
        self.assertIsNone(
            self.ts._check_usage(_FakeRequest(), {"max_concurrent_calls": 0}))

    def test_per_hour_limit(self):
        import time
        self.ts._recent_mints.extend([time.time()] * 3)
        msg = self.ts._check_usage(_FakeRequest(), {"calls_per_hour": 3})
        self.assertIn("this hour", msg)
        self.assertIsNone(self.ts._check_usage(_FakeRequest(), {"calls_per_hour": 0}))

    def test_per_day_limit_counts_beyond_the_hour(self):
        # The hourly cap alone still permits 24x that in a day, so the daily
        # ceiling must count calls that have already aged out of the hour.
        import time
        old = time.time() - 7200          # two hours ago: outside the hour
        self.ts._recent_mints.extend([old] * 5)
        cfg = {"calls_per_hour": 10, "calls_per_day": 5}
        msg = self.ts._check_usage(_FakeRequest(), cfg)
        self.assertIn("today", msg)
        # …and the hourly limit is unaffected by those same old calls.
        self.assertIsNone(
            self.ts._check_usage(_FakeRequest(), {"calls_per_hour": 10}))
        self.assertIsNone(self.ts._check_usage(_FakeRequest(), {"calls_per_day": 0}))

    def test_pause_refuses_everything(self):
        msg = self.ts._check_usage(_FakeRequest(), {"calls_paused": True})
        self.assertIsNotNone(msg)
        self.assertNotIn("error", msg.lower())   # in-world, never a code

    def test_refusals_never_mention_the_mechanism(self):
        # A caller pressing Call should hear a busy station, not a rate limit.
        import time
        self.ts._live_calls["room-a"] = time.time()
        self.ts._recent_mints.append(time.time())
        self.ts._caller_last["1.2.3.4"] = time.time()
        banned = ("rate", "limit", "quota", "429", "token", "api")
        for cfg in ({"calls_paused": True}, {"max_concurrent_calls": 1},
                    {"calls_per_hour": 1}, {"calls_per_day": 1},
                    {"caller_cooldown_secs": 45}):
            msg = self.ts._check_usage(_FakeRequest(), cfg) or ""
            self.assertTrue(msg, cfg)
            for word in banned:
                self.assertNotIn(word, msg.lower(), f"{cfg} -> {msg}")

    def test_redial_cooldown_is_per_caller(self):
        import time
        self.ts._caller_last["1.2.3.4"] = time.time()
        msg = self.ts._check_usage(
            _FakeRequest("1.2.3.4"), {"caller_cooldown_secs": 45})
        self.assertIn("only just hung up", msg)
        # A different caller is unaffected.
        self.assertIsNone(self.ts._check_usage(
            _FakeRequest("5.6.7.8"), {"caller_cooldown_secs": 45}))

    def test_secure_origin_derivation(self):
        old = self.live.LIVEKIT_PUBLIC_URL
        try:
            self.live.LIVEKIT_PUBLIC_URL = "wss://192.168.1.245:8443"
            self.assertEqual(self.live._secure_origin(), "https://192.168.1.245:8443")
            self.live.LIVEKIT_PUBLIC_URL = "ws://localhost:7880"
            self.assertEqual(self.live._secure_origin(), "")
        finally:
            self.live.LIVEKIT_PUBLIC_URL = old


class TestCallStructure(unittest.TestCase):
    """The call is an object with phases, not a 334-line function. These pin
    the seams so a future edit can't quietly put the call back in one place."""

    @classmethod
    def setUpClass(cls):
        from call import lifecycle
        from call.session import CallSession

        cls.CallSession = CallSession
        cls.lifecycle = lifecycle

    def test_entrypoint_only_decides_whether_to_answer(self):
        # main.py's job is wiring. If this grows again, the call has started
        # leaking back out of CallSession.
        import inspect

        import main

        body = inspect.getsource(main.entrypoint)
        self.assertLess(len(body.splitlines()), 30)
        self.assertIn("probe-", body)          # still refuses probe rooms
        for phase in ("prepare()", "start()", "greet()"):
            self.assertIn(phase, body)

    def test_every_lifecycle_hook_is_registered(self):
        # Each of these was a closure in the old entrypoint. Losing one in the
        # move would be silent: the call still connects, it just stops doing
        # something (checking in on a quiet caller, enforcing the time limit).
        import inspect

        src = inspect.getsource(self.CallSession)
        for hook in ("station.aclose", "station_cfg.aclose", "air.watch",
                     "attach_error_recovery", "attach_heard_logging",
                     "attach_idle_watch", "attach_time_limit", "_on_shutdown"):
            self.assertIn(hook, src, hook)

    def test_the_hangup_tool_reads_the_session_late(self):
        # Tools are built before the AgentSession exists. Handing the tool a
        # callable rather than the session is what makes that safe; passing
        # the value directly would capture None for the life of the call.
        import inspect

        from call.tools.control import build_call_control_tools

        params = inspect.signature(build_call_control_tools).parameters
        self.assertIn("get_session", params)

    def test_the_greeting_opens_with_a_user_turn(self):
        """Reproduced against the Gemini API: a function call as the FIRST
        turn is rejected outright — "function call turn comes immediately
        after a user turn or after a function response turn", 400, fatal. The
        DJ routinely calls a tool while writing its greeting (checking what's
        playing), so the conversation has to start with a user turn."""
        import asyncio, inspect

        src = inspect.getsource(self.lifecycle.greet)
        self.assertIn("user_input=", src)

        seen = {}

        class FakeSession:
            async def generate_reply(self, **kw):
                seen.update(kw)
            async def say(self, *a, **kw):
                pass

        asyncio.run(self.lifecycle.greet(FakeSession(), {}))
        self.assertTrue(seen.get("user_input"), "no user turn seeded")
        self.assertTrue(seen.get("instructions"), "the greeting itself was lost")

        # It is a cue, not words put in the caller's mouth — and bracketed
        # text can never reach the voice.
        import speech_filter
        self.assertEqual(
            speech_filter.strip_stage_directions(seen["user_input"]), "")

    def test_idle_watch_and_time_limit_are_opt_out_by_setting(self):
        # Both used to be `if` blocks inside entrypoint; as functions they must
        # still no-op on 0 rather than starting a task that never fires.
        self.assertIsNone(self.lifecycle.attach_idle_watch(
            None, None, {"idle_prompt_secs": 0}))
        self.assertIsNone(self.lifecycle.attach_time_limit(
            None, None, {"max_call_seconds": 0}))


class TestCallRecord(unittest.TestCase):
    """Diagnosing a bad call meant reading the CALLER's half and inferring the
    rest from tracebacks. The record is both halves plus the tools, so a call
    can be reviewed as a call."""

    def setUp(self):
        from call import record

        self.record = record
        self._tmp = tempfile.TemporaryDirectory()
        self._old = record.CALLS_DIR
        record.CALLS_DIR = Path(self._tmp.name)

    def tearDown(self):
        self.record.CALLS_DIR = self._old
        self._tmp.cleanup()

    def _a_call(self, room="callin-abc123456789"):
        r = self.record.CallRecord(
            room, {"id": "p1", "name": "Wade"},
            {"llm_provider": "google", "llm_model": "gemini-3.1-flash-lite",
             "stt_provider": "local", "stt_model": "base.en", "tts_mode": "local",
             "allow_requests": True, "allow_skills": False},
        )
        r.turn("dj", "You're through to the booth.")
        r.turn("caller", "Can you play something fun?")
        r.tool("subwave_request_song", "Added to the queue")
        r.turn("dj", "That's going in.")
        return r

    def test_a_call_records_both_sides_and_the_tools(self):
        self._a_call().write(reason="caller hung up")
        calls = self.record.recent()
        self.assertEqual(len(calls), 1)
        c = calls[0]
        self.assertEqual(c["persona"]["name"], "Wade")
        self.assertEqual(c["callerTurns"], 1)
        self.assertEqual(c["endedBecause"], "caller hung up")
        self.assertEqual([t["who"] for t in c["turns"]], ["dj", "caller", "dj"])
        self.assertEqual(c["tools"][0]["name"], "subwave_request_song")
        # The config it ran under, so a bad call can be tied to a setting.
        self.assertIn("gemini", c["config"]["llm"])
        self.assertIn("allow_requests", c["config"]["permissions"])
        self.assertNotIn("allow_skills", c["config"]["permissions"])

    def test_final_wording_replaces_the_clipped_live_capture(self):
        # conversation_item_added fires while the DJ is still speaking, so the
        # live text came out clipped ("Take a breath, I've"). Timing from the
        # events is right; the wording has to come from the session history.
        r = self.record.CallRecord("callin-x", {"name": "Dalia"}, {})
        r.turn("dj", "Take a breath, I've")
        r.turn("caller", "Play me Let It Go")
        r.turn("dj", "Still with")
        stamps = [t["t"] for t in r.data["turns"]]

        r.finalise([("dj", "Take a breath, I've got you."),
                    ("caller", "Play me Let It Go"),
                    ("dj", "Still with me?"),
                    ("dj", "A line the events never saw.")])

        turns = r.data["turns"]
        self.assertEqual(turns[0]["text"], "Take a breath, I've got you.")
        self.assertEqual(turns[2]["text"], "Still with me?")
        self.assertEqual([t["t"] for t in turns[:3]], stamps)   # timings kept
        self.assertEqual(turns[3]["text"], "A line the events never saw.")

    def test_finalise_on_an_empty_history_keeps_what_was_captured(self):
        # If the session cannot be flattened, a clipped record beats none.
        r = self.record.CallRecord("callin-x", {"name": "Dalia"}, {})
        r.turn("dj", "something")
        r.finalise([])
        self.assertEqual(r.data["turns"][0]["text"], "something")

    def test_a_history_entry_with_no_live_event_does_not_shift_the_rest(self):
        """Taken from a real call (2026-08-05, room 1023dbeb3e28), where the
        written transcript and the live log disagreed about who said what.

        finalise used to pair the Nth live event with the Nth history entry.
        The call opens with a primed `user` turn so the model has something to
        answer, and that turn never produces a live event — so every caller
        line landed on the NEXT line's timestamp, the last one was appended at
        call-end time, and callerTurns came out one too high (which gates the
        back-to-air mention). The record reported no problem while being wrong
        about the whole call.

        The prime is dropped at the source now; this pins the general case,
        because any unmatched history entry did the same damage.
        """
        r = self.record.CallRecord("callin-x", {"id": "p1", "name": "Dawn"}, {})
        r.turn("dj", "Yosemite FM, you're on with Dawn.")
        r.turn("caller", "Can you play me a song?")
        r.turn("caller", "Sit with.")
        r.turn("caller", "Hello.")
        stamps = {t["text"]: t["t"] for t in r.data["turns"]}

        # An extra caller entry the events never saw, at the FRONT.
        r.finalise([("caller", "[Call connected. You speak first.]"),
                    ("dj", "Yosemite FM, you're on with Dawn."),
                    ("caller", "Can you play me a song?"),
                    ("caller", "Sit with."),
                    ("caller", "Hello.")])

        said = [(t["who"], t["text"]) for t in r.data["turns"]]
        self.assertEqual(said, [
            ("dj", "Yosemite FM, you're on with Dawn."),
            ("caller", "Can you play me a song?"),
            ("caller", "Sit with."),
            ("caller", "Hello."),
            # The unmatched entry is appended, never folded into a real turn.
            ("caller", "[Call connected. You speak first.]"),
        ])
        # Every line the caller actually said keeps the time it was heard.
        for text in ("Can you play me a song?", "Sit with.", "Hello."):
            got = next(t["t"] for t in r.data["turns"] if t["text"] == text)
            self.assertEqual(got, stamps[text],
                             f"{text!r} was moved onto another line's timestamp")

    def test_the_opening_prime_is_not_a_caller_turn(self):
        """It sits in the history as a `user` message because that is the only
        shape Gemini accepts a leading function call after — but the caller
        neither said nor heard it. Counting it inflates callerTurns, which is
        what `callback_min_turns` reads to decide whether a call was worth
        mentioning on air."""
        from call import lifecycle

        class _Item:
            def __init__(self, role, content):
                self.role, self.content = role, content

        class _Session:
            history = type("H", (), {"items": [
                _Item("user", lifecycle.CALL_OPENING_PRIME),
                _Item("assistant", "Yosemite FM, you're on with Dawn."),
                _Item("user", "Can you play me a song?"),
            ]})()

        got = lifecycle._transcript(_Session())
        self.assertEqual(got, [("assistant", "Yosemite FM, you're on with Dawn."),
                               ("user", "Can you play me a song?")])

    def test_problems_are_kept_with_the_call_that_had_them(self):
        r = self._a_call()
        r.problem("APIStatusError: gemini llm: client error 400")
        r.write()
        self.assertIn("400", self.record.recent()[0]["problems"][0]["what"])

    def test_old_calls_are_pruned(self):
        for i in range(self.record.KEEP + 8):
            self._a_call(room=f"callin-{i:012d}").write()
        self.assertLessEqual(
            len(list(self.record.CALLS_DIR.glob("*.json"))), self.record.KEEP)

    def test_writing_never_raises_into_the_call(self):
        # This runs during shutdown, just before the on-air handoff. A crash
        # here would cost that handoff for the sake of a diagnostic file.
        self.record.CALLS_DIR = Path("/nonexistent\x00/bad")
        r = self._a_call()
        r.write()                       # must not raise
        # And must actually have tried. Resting on "no exception" alone meant
        # this passed for a write() that returned early — or did nothing at
        # all — so it asserts the body ran and left the record complete.
        self.assertIn("endedAt", r.data)
        self.assertIn("durationSecs", r.data)
        self.assertEqual(self.record.recent(), [])   # nothing landed anywhere

    def test_a_runaway_call_cannot_write_an_unbounded_file(self):
        r = self._a_call()
        for i in range(self.record.MAX_TURNS + 200):
            r.turn("caller", f"line {i}")
        r.turn("caller", "x" * (self.record.MAX_TEXT + 500))
        r.write()
        c = self.record.recent()[0]
        self.assertLessEqual(len(c["turns"]), self.record.MAX_TURNS)
        self.assertTrue(all(len(t["text"]) <= self.record.MAX_TEXT for t in c["turns"]))


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
        reads = [t.name for t in self.registry.TOOLS
                 if t.gate == self.registry.READ]
        allowed = self.registry.mcp_allowlist({})     # nothing switched on
        self.assertEqual(sorted(allowed), sorted(reads))

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
            self.assertEqual(self.music.build_library_tools(
                cfg, None, self.actions.CallActions(0)), [])

            station_config.admin_credentials = lambda: ("dj", "secret")
            self.assertFalse(self.registry.library_search_needs_mcp())
            self.assertNotIn("subwave_search_library", self.registry.mcp_allowlist(cfg))
            self.assertEqual(len(self.music.build_library_tools(
                cfg, None, self.actions.CallActions(0))), 1)
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
        out = self.music._fmt_track({
            "title": "Open Eye Signal", "artist": "Jon Hopkins",
            "moods": ["hypnotic", "nocturnal"], "energy": 0.7,
        })
        self.assertIn("hypnotic", out)
        self.assertIn("nocturnal", out)
        self.assertIn("high energy", out)
        # And stays clean when the station sends nothing.
        self.assertNotIn("energy", self.music._fmt_track({"title": "T", "artist": "A"}))

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

        async def run():
            tools = self.control.build_call_control_tools(
                FakeCtx(), lambda: None, time.time() - 600)
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

        build_tts resolved the adapter against Path(__file__).parent, which
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
        provider, model, note = self.providers.effective_stt({"stt_provider": "deepgram"})
        self.assertEqual(provider, "google")  # no deepgram or openai key set
        self.assertIn("falling back", note)
        os.environ["OPENAI_API_KEY"] = "sk-x"
        provider, model, note = self.providers.effective_stt({"stt_provider": "deepgram"})
        self.assertEqual(provider, "openai")
        self.assertEqual(model, "gpt-4o-mini-transcribe")

    def test_effective_stt_rejects_cross_provider_model(self):
        provider, model, _ = self.providers.effective_stt(
            {"stt_provider": "local", "stt_model": "nova-3"}
        )
        self.assertEqual(provider, "local")
        self.assertEqual(model, "base.en")  # nova-3 is not a local model


class TestCallerIdentityCannotBeChosen(unittest.TestCase):
    """Who the server thinks you are decides your cooldown, your lockout
    bucket, and whose address gets banned. All three were a header away.

    X-Forwarded-For is a list the CLIENT starts and proxies append to, so its
    leftmost entry is whatever the caller typed. Reading it meant rotating the
    header sat at "4 tries left" forever, and writing someone else's address
    into it put THEM in cooldown.
    """

    def _key(self, peer, xff=None, trusted=""):
        from api import wire as api_wire

        old = api_wire._TRUSTED_PROXIES_RAW
        api_wire._TRUSTED_PROXIES_RAW = trusted
        try:
            headers = {"X-Forwarded-For": xff} if xff else {}
            return api_wire._caller_key(
                types.SimpleNamespace(headers=headers, remote=peer)
            )
        finally:
            api_wire._TRUSTED_PROXIES_RAW = old

    def test_a_direct_caller_cannot_claim_another_address(self):
        # The peer is on the public internet, so nothing it says about who it
        # is counts for anything.
        self.assertEqual(
            self._key("8.8.8.8", xff="10.0.0.1", trusted="10.99.99.99"),
            "8.8.8.8",
        )

    def test_a_trusted_proxy_is_believed_but_only_for_what_it_appended(self):
        # The client wrote "1.2.3.4"; the proxy appended what it actually saw.
        # The rightmost entry is the proxy's, so that is the one that counts.
        self.assertEqual(
            self._key("10.99.99.99", xff="1.2.3.4, 8.8.4.4",
                      trusted="10.99.99.99"),
            "8.8.4.4",
        )

    def test_an_untrusted_peer_with_no_header_still_resolves(self):
        self.assertEqual(self._key("8.8.8.8", trusted="10.99.99.99"),
                         "8.8.8.8")

    def test_the_default_trusts_a_private_peer_so_the_bundled_proxy_works(self):
        # caddy reaches the container over the docker bridge; without this the
        # per-caller limits collapse into one shared bucket for every caller.
        self.assertEqual(self._key("172.18.0.4", xff="8.8.4.4"),
                         "8.8.4.4")

    def test_the_default_does_not_trust_a_public_peer(self):
        self.assertEqual(self._key("8.8.4.4", xff="10.0.0.1"),
                         "8.8.4.4")


class TestStoredKeysStayHome(_TempStores):
    """A stored API key only ever travels to the host it is configured for.

    The panel can preview a URL before saving it, and that override reaches
    the code that builds the real provider — which attaches whatever key is
    stored. So a URL in a REQUEST could make this process post the OpenAI key,
    the TTS key or the station's admin password to any host the requester
    named. All three came back in the clear against a test listener, which
    turns the panel password into the plaintext of every key — the one thing
    storing them server-side is supposed to prevent.
    """

    def test_the_saved_host_is_credentialed(self):
        from api import credentials as api_credentials

        may, note = api_credentials._credentials_travel_to(
            "https://api.openai.com/v1", "https://api.openai.com")
        self.assertTrue(may)
        self.assertEqual(note, "")

    def test_an_unsaved_host_is_not(self):
        from api import credentials as api_credentials

        may, note = api_credentials._credentials_travel_to(
            "http://attacker.example/v1", "https://api.openai.com")
        self.assertFalse(may)
        self.assertIn("not the address in your saved settings", note)

    def test_supplying_nothing_leaves_the_saved_config_in_charge(self):
        from api import credentials as api_credentials

        may, _ = api_credentials._credentials_travel_to("", "https://api.openai.com")
        self.assertTrue(may)

    # Where each SDK ends up keeping the key, so the assertion is about what
    # will actually go out on the wire rather than what we passed in.
    KEY_ENV = {
        "openai": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "google": "GOOGLE_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }

    def _key_on(self, model):
        client = model._client
        for holder in (client, getattr(client, "_api_client", None)):
            if holder is None:
                continue
            for attr in ("api_key", "_api_key"):
                value = getattr(holder, attr, None)
                if isinstance(value, str) and value:
                    return value
        self.fail("could not find the api key on the built model")

    def test_withholding_does_not_fall_back_to_the_environment(self):
        # The failure this guards is subtle and per-SDK: these plugins take
        # api_key as either NotGivenOr[str] or str|None, and two of them treat
        # a falsy value as "read the environment" — so passing "" or None to
        # withhold a key would send it anyway. Every provider is checked
        # because getting one of them wrong leaks exactly one key, silently.
        from call.providers import WITHHELD_KEY, build_llm

        for provider, env_var in self.KEY_ENV.items():
            with self.subTest(provider=provider):
                os.environ[env_var] = f"{provider}-must-not-travel"
                model = build_llm(
                    {"llm_provider": provider, "llm_model": "",
                     "llm_base_url": "http://attacker.example/v1"},
                    use_stored_key=False,
                )
                self.assertEqual(self._key_on(model), WITHHELD_KEY)

    def test_the_normal_path_still_uses_the_stored_key(self):
        from call.providers import build_llm

        for provider, env_var in self.KEY_ENV.items():
            with self.subTest(provider=provider):
                os.environ[env_var] = f"{provider}-the-real-one"
                model = build_llm({"llm_provider": provider, "llm_model": ""})
                self.assertEqual(self._key_on(model), f"{provider}-the-real-one")

    def test_a_lookalike_openai_hostname_gets_no_key(self):
        # "api.openai.com" in base_url also matched
        # https://api.openai.com.example.net — a domain anyone can register.
        from tts_adapter import _is_openai_host

        self.assertTrue(_is_openai_host("https://api.openai.com/v1"))
        self.assertFalse(_is_openai_host("https://api.openai.com.example.net/v1"))
        self.assertFalse(_is_openai_host("https://notapi.openai.com.evil/v1"))

    def test_station_config_without_auth_reads_nothing_admin_only(self):
        import asyncio

        from station_config import StationConfig

        secrets_store.save({"subwave_admin_user": "u", "subwave_admin_pass": "p"})

        async def go():
            sc = StationConfig(base_url="http://attacker.example", with_auth=False)
            try:
                # No credentials on the client, so the admin-only read is not
                # even attempted — it cannot leak what it never sends.
                self.assertFalse(sc._authed)
                self.assertEqual(await sc.settings(), {})
            finally:
                await sc.aclose()

        asyncio.run(go())


class TestFirstRunIsNotOpenToTheWeb(_TempStores):
    """Before a password is set the panel stays open, gated only by refusing
    foreign origins. That gate compared the Origin to the Host — which a
    rebound DNS name satisfies, because the browser sets both to the attacker's
    own name. A literal address cannot be rebound; a name can.
    """

    class _Req(dict):
        """Enough of a request for the gate: headers, host, and the dict-like
        slot handlers use to leave a caller-facing reason on."""

        def __init__(self, origin, host):
            super().__init__()
            self.headers = {"Origin": origin}
            self.host = host
            self.remote = "8.8.8.8"

    def _allowed(self, origin, host):
        from api import auth as api_auth

        return api_auth._write_allowed(self._Req(origin, host))

    def setUp(self):
        super().setUp()
        import admin_auth
        from api import auth as api_auth

        self._old_auth = admin_auth.AUTH_PATH
        admin_auth.AUTH_PATH = Path(self._tmp.name) / "auth.json"
        self._old_key = api_auth.ADMIN_KEY
        api_auth.ADMIN_KEY = ""

    def tearDown(self):
        import admin_auth
        from api import auth as api_auth

        admin_auth.AUTH_PATH = self._old_auth
        api_auth.ADMIN_KEY = self._old_key
        super().tearDown()

    def test_a_rebound_name_is_refused(self):
        self.assertFalse(self._allowed("http://evil.example", "evil.example"))

    def test_the_real_lan_address_still_works(self):
        self.assertTrue(self._allowed("http://192.168.1.10:8100", "192.168.1.10:8100"))

    def test_localhost_still_works(self):
        self.assertTrue(self._allowed("http://localhost:8100", "localhost:8100"))

    def test_a_named_origin_can_be_opted_into(self):
        from api import wire as api_wire

        old = api_wire.PANEL_ORIGINS
        api_wire.PANEL_ORIGINS = ["https://radio.example"]
        try:
            self.assertTrue(self._allowed("https://radio.example", "radio.example"))
        finally:
            api_wire.PANEL_ORIGINS = old

    def test_permission_to_embed_is_not_permission_to_configure(self):
        """CALLIN_ALLOWED_ORIGINS means 'this page may embed the widget and
        mint call tokens'. It must not also mean 'this page may read the
        settings and set the admin password' — the blast radius is API budget
        in one case and the controls in the other, and a permission that moves
        as a side effect of another one is the exact shape 0.9.61 removed."""
        from api import wire as api_wire

        old_embed = api_wire.ALLOWED_ORIGINS
        old_panel = api_wire.PANEL_ORIGINS
        api_wire.ALLOWED_ORIGINS = ["https://someone-elses-blog.example"]
        api_wire.PANEL_ORIGINS = []
        try:
            self.assertFalse(
                self._allowed("https://someone-elses-blog.example",
                              "someone-elses-blog.example"))
        finally:
            api_wire.ALLOWED_ORIGINS = old_embed
            api_wire.PANEL_ORIGINS = old_panel


class TestAnUnreadablePasswordStoreFailsClosed(_TempStores):
    """A password file that exists but will not open must not read as "no
    password has been set".

    Both used to come back as an empty dict, so is_set() went False,
    _auth_configured() went False, and the panel dropped into first-run mode —
    unauthenticated — with a perfectly good password sitting on disk. The gate
    fell open on a configuration error, which is the one thing a gate must
    never do.

    This is not a hypothetical. Running the container as a non-root user
    against a data/ whose files root wrote makes every store in it unreadable
    at once, and that is exactly the hardening step this release adds.
    """

    def setUp(self):
        super().setUp()
        import admin_auth

        self._old = admin_auth.AUTH_PATH
        admin_auth.AUTH_PATH = Path(self._tmp.name) / "admin-auth.json"
        self.admin_auth = admin_auth

    def tearDown(self):
        self.admin_auth.AUTH_PATH = self._old
        super().tearDown()

    def test_no_file_at_all_is_genuinely_unconfigured(self):
        self.assertIsNone(self.admin_auth.unreadable())
        self.assertFalse(self.admin_auth.is_set())

    def test_a_normal_store_reads_normally(self):
        self.admin_auth.set_password("a-real-password")
        self.assertIsNone(self.admin_auth.unreadable())
        self.assertTrue(self.admin_auth.is_set())
        self.assertTrue(self.admin_auth.verify("a-real-password"))

    def test_a_corrupt_store_counts_as_configured(self):
        self.admin_auth.AUTH_PATH.write_text("{not json", encoding="utf-8")
        self.assertIsNotNone(self.admin_auth.unreadable())
        # The point: configured, so the panel demands a password...
        self.assertTrue(self.admin_auth.is_set())
        # ...and nothing satisfies it, so it is shut rather than open.
        self.assertFalse(self.admin_auth.verify("anything"))
        self.assertFalse(self.admin_auth.verify(""))

    def test_the_reason_names_the_file_and_the_way_back_in(self):
        self.admin_auth.AUTH_PATH.write_text("{not json", encoding="utf-8")
        why = self.admin_auth.unreadable()
        self.assertIn("admin-auth.json", why)
        # An operator staring at "wrong password" has no way to guess this.
        self.assertIn("CALLIN_ADMIN_KEY", why)

    def test_the_phone_does_not_swing_open_either(self):
        """The sharper half. front_access `auto` — the default — means "open
        until a guest code exists", so guest_is_set() answering False is what
        holds the line open. If an unreadable store answered False there, one
        bad file permission would open the phone to anyone, which is precisely
        the failure the explicit access modes exist to prevent."""
        self.admin_auth.set_guest_password("a-real-guest-code")
        self.assertTrue(self.admin_auth.guest_is_set())

        self.admin_auth.AUTH_PATH.write_text("{not json", encoding="utf-8")
        self.assertTrue(
            self.admin_auth.guest_is_set(),
            "an unreadable store read as 'no guest code', which opens the line")
        self.assertFalse(self.admin_auth.verify_guest("a-real-guest-code"))

    def test_the_gate_refuses_on_auto_when_the_store_is_unreadable(self):
        # End to end through the real gate, not just the store: `auto` is the
        # shipped default, so this is the path an ordinary deployment takes.
        from api import auth as api_auth

        settings_store.save({"front_access": "auto"})
        self.admin_auth.AUTH_PATH.write_text("{not json", encoding="utf-8")
        self.assertIsNotNone(api_auth._guest_check("", "1.2.3.4"))
        self.assertIsNotNone(api_auth._guest_check("any-code", "1.2.3.4"))


@unittest.skipUnless(hasattr(os, "getuid"), "POSIX modes only")
class TestWrittenFilesGetExplicitModes(_TempStores):
    """Everything written into data/ sets its own mode, rather than taking
    whatever the filesystem hands out.

    Found on a real deployment, not in theory: a Synology share creates files
    with mode 000 — no bits at all. Root ignores that, so for as long as the
    container ran as root nothing showed. The moment it ran as uid 1000, the
    app could not read its own settings.json even though it OWNED it, and
    chowning the directory did not help because the bits were never there.

    secrets.json and admin-auth.json were only ever spared because they chmod
    themselves. So now so does everything else.
    """

    def test_settings_are_readable_by_their_owner(self):
        settings_store.save({"llm_model": "gpt-4.1-mini"})
        mode = settings_store.SETTINGS_PATH.stat().st_mode & 0o777
        self.assertTrue(mode & 0o400, f"settings.json came out {mode:03o}")
        self.assertTrue(mode & 0o200, f"settings.json is not writable: {mode:03o}")

    def test_the_secret_stores_stay_owner_only(self):
        # The other half: fixing the readable ones must not loosen these.
        secrets_store.save({"openai_api_key": "sk-test"})
        self.assertEqual(secrets_store.SECRETS_PATH.stat().st_mode & 0o777, 0o600)

    def test_a_call_transcript_and_its_directory_are_reachable(self):
        from call import record

        old = record.CALLS_DIR
        record.CALLS_DIR = Path(self._tmp.name) / "calls"
        try:
            r = record.CallRecord("callin-abcdefghijkl", {"id": "p1", "name": "Wade"}, {})
            r.turn("caller", "hello")
            r.write("test")
            written = list(record.CALLS_DIR.glob("*.json"))
            self.assertTrue(written, "no transcript was written")
            # A directory with no execute bit cannot be listed by its owner,
            # so the transcripts would be write-only in practice.
            self.assertTrue(record.CALLS_DIR.stat().st_mode & 0o100)
            self.assertTrue(written[0].stat().st_mode & 0o400)
        finally:
            record.CALLS_DIR = old


class TestTheIdleClockDoesNotRunWhileTheDJIsHeldBack(unittest.TestCase):
    """"Still there?" must not be asked about a silence the DJ is causing.

    Seen on a real call (2026-08-05, room 1023dbeb3e28): the on-air DJ took the
    microphone at 10:29:47, the call DJ was held until 10:30:15, and the idle
    check-in fired at 10:30:11 — in the middle of the hold. The caller had done
    nothing wrong; the DJ was deliberately silent and then asked them why they
    were. The clock is already pinned while the DJ is speaking or thinking, but
    during a hold the session still reads as `listening`, because it is waiting
    on the broadcast rather than on the caller.
    """

    def _run(self, on_air: bool, seconds: float = 3.5):
        import asyncio
        import types

        from call import lifecycle

        replies = []

        class _Session:
            agent_state = "listening"

            def on(self, *a, **k):
                pass

            async def generate_reply(self, **kw):
                replies.append(kw)

            async def say(self, *a, **k):
                replies.append({"say": a})

        ctx = types.SimpleNamespace(add_shutdown_callback=lambda *a: None)
        air = types.SimpleNamespace(on_air=on_air)

        async def go():
            lifecycle.attach_idle_watch(
                ctx, _Session(),
                {"idle_prompt_secs": 1, "idle_max_nudges": 2}, air=air,
            )
            await asyncio.sleep(seconds)

        asyncio.run(go())
        return replies

    def test_no_check_in_while_the_broadcast_has_the_microphone(self):
        self.assertEqual(
            self._run(on_air=True), [],
            "the DJ asked the caller why it was quiet during its own hold")

    def test_the_check_in_still_fires_when_the_air_is_clear(self):
        # The other half — pinning it must not disable the feature outright.
        self.assertTrue(
            self._run(on_air=False),
            "the idle check-in stopped working when the air was clear")


class TestTheDataDirCheckCannotStopTheWorker(unittest.TestCase):
    """It runs at module scope in main.py, before the worker registers with
    LiveKit, so anything it raises means no calls at all. A diagnostic written
    to explain a broken data directory must not be able to break more than the
    directory did — the same trade record.write() makes when it refuses to cost
    the on-air handoff for the sake of a JSON file.

    Deliberately NOT platform-gated. The first version of this test lived in a
    POSIX-only class, was skipped on the author's machine, and reached CI
    broken — the third time in one afternoon that a skip hid a defect. The
    `hasattr(os, "getuid")` check moved inside the guarded function so the
    swallow itself runs everywhere and this test means something everywhere.
    """

    def test_a_failure_inside_it_is_swallowed_and_logged(self):
        class _Exploding:
            @property
            def parent(self):
                raise RuntimeError("the data directory is unreachable")

        original = settings_store.SETTINGS_PATH
        try:
            settings_store.SETTINGS_PATH = _Exploding()
            # assertLogs IS the assertion: check_data_dir must not raise, and
            # must have reached the swallow rather than returning early for
            # some unrelated reason.
            with self.assertLogs("callin.settings", level="DEBUG") as caught:
                settings_store.check_data_dir()
        finally:
            settings_store.SETTINGS_PATH = original
        self.assertTrue(
            any("data directory" in m for m in caught.output),
            f"something else was swallowed: {caught.output}")

    def test_a_healthy_directory_says_nothing(self):
        # The other half: it must not cry wolf on a directory that is fine.
        with self.assertNoLogs("callin.settings", level="ERROR"):
            settings_store.check_data_dir()


class TestTheSuiteIsNotQuietlyNotRunning(unittest.TestCase):
    """A test that passes because it never ran is worse than no test.

    Three file-mode tests were written `skipUnless(POSIX)`, so on the author's
    Windows box they reported success while containing a broken constructor —
    only CI, on Linux, ever executed them. That is the failure mode this
    guards: the suite looking green while part of it is inert.

    Deliberately narrow. It does not try to judge whether a test is any good;
    it checks the two things that make one silently worthless — never
    executing, and having no assertions at all.
    """

    def _classes(self):
        import inspect
        import test_sidecar

        # Leading underscore is this suite's marker for a shared fixture
        # (_TempStores), which is a base class and correctly has no tests.
        return [
            (name, obj) for name, obj in vars(test_sidecar).items()
            if inspect.isclass(obj) and issubclass(obj, unittest.TestCase)
            and not name.startswith("_")
        ]

    def test_every_test_class_actually_has_tests(self):
        empty = sorted(
            name for name, cls in self._classes()
            if not [m for m in dir(cls) if m.startswith("test")]
        )
        self.assertFalse(empty, f"test classes with no tests in them: {empty}")

    def test_every_test_asserts_something(self):
        import inspect
        import re

        silent = []
        for name, cls in self._classes():
            for attr in dir(cls):
                if not attr.startswith("test"):
                    continue
                try:
                    src = inspect.getsource(getattr(cls, attr))
                except (OSError, TypeError):
                    continue
                if not re.search(r"\bself\.(assert|fail)\w*\(", src):
                    silent.append(f"{name}.{attr}")
        self.assertFalse(
            sorted(silent),
            f"tests that assert nothing, so they cannot fail: {sorted(silent)}")

    @unittest.skipUnless(hasattr(os, "getuid"), "POSIX-only tests are the point")
    def test_the_posix_only_tests_are_reachable_somewhere(self):
        # On POSIX — which is CI, and the container — nothing may be skipped
        # for the POSIX reason. If this ever fails, a test is inert everywhere.
        import test_sidecar

        for name in ("TestWrittenFilesGetExplicitModes",):
            cls = getattr(test_sidecar, name)
            reason = getattr(cls, "__unittest_skip_why__", "")
            self.assertFalse(
                getattr(cls, "__unittest_skip__", False),
                f"{name} is skipped on POSIX too, so it never runs: {reason}")


class TestAConfigValueCannotNameAFileOnTheDisk(unittest.TestCase):
    """`tts_adapter` arrives in the BODY of /test/tts and /test/speed.

    The resolution was the same three lines copied into three modules, and all
    three read "join it to ADAPTER_DIR unless it is absolute" — so an absolute
    path went straight to open(), and ../ in a relative one walked out of the
    directory before exists() ever looked. A request could name any file on the
    disk and learn whether it existed and whether it parsed as JSON; in
    first-run mode that needs no password.

    Same family as the withheld-key fix: configuration that ARRIVES in a
    request was being treated as operator intent.
    """

    def test_an_absolute_path_is_refused(self):
        from tts_adapter import resolve_adapter

        for hostile in ("/etc/passwd", "/data/secrets.json",
                        "C:\\Windows\\win.ini"):
            with self.subTest(path=hostile):
                self.assertIsNone(resolve_adapter(hostile))

    def test_traversal_is_refused(self):
        from tts_adapter import resolve_adapter

        for hostile in ("../../etc/passwd", "../secrets.json",
                        "sub/dir/openai-cloud.json", "..\\..\\secrets.json"):
            with self.subTest(path=hostile):
                self.assertIsNone(resolve_adapter(hostile))

    def test_a_non_json_name_is_refused(self):
        from tts_adapter import resolve_adapter

        self.assertIsNone(resolve_adapter("openai-cloud.yaml"))
        self.assertIsNone(resolve_adapter("secrets"))

    def test_a_real_adapter_still_resolves(self):
        # The other half. Refusing everything would be a silent outage: the
        # DJ would fall back to the default adapter and sound wrong.
        from tts_adapter import ADAPTER_DIR, resolve_adapter

        shipped = sorted(p.name for p in ADAPTER_DIR.glob("*.json")
                         if not p.name.startswith("_"))
        self.assertTrue(shipped, "no adapters shipped — nothing to check")
        for name in shipped:
            with self.subTest(adapter=name):
                got = resolve_adapter(name)
                self.assertIsNotNone(got, f"{name} stopped resolving")
                self.assertTrue(Path(got).is_file())

    def test_the_deploy_time_env_escape_still_works(self):
        # TTS_ADAPTER_CONFIG is set by whoever runs the container, not by a
        # request, and pointing it at a mounted file outside the image is
        # supported. Honoured only when it matches that value exactly.
        from tts_adapter import resolve_adapter

        old = os.environ.get("TTS_ADAPTER_CONFIG")
        os.environ["TTS_ADAPTER_CONFIG"] = "/mnt/custom/adapter.json"
        try:
            self.assertEqual(resolve_adapter("/mnt/custom/adapter.json"),
                             "/mnt/custom/adapter.json")
            # ...and not for a different absolute path in the same request.
            self.assertIsNone(resolve_adapter("/etc/passwd"))
        finally:
            if old is None:
                os.environ.pop("TTS_ADAPTER_CONFIG", None)
            else:
                os.environ["TTS_ADAPTER_CONFIG"] = old


class TestCallerIdentitySurvivesTwoProxies(unittest.TestCase):
    """Taking the rightmost X-Forwarded-For entry is right for one proxy and
    wrong for two. With a CDN in front of the reverse proxy, the entry the
    proxy appended is the CDN's address — so every caller on earth shares one
    cooldown bucket and one lockout counter, and the per-caller limits stop
    being per-caller. Walk back through the hops we already trust instead."""

    def _key(self, peer, xff, trusted=""):
        import types

        from api import wire as api_wire

        old = api_wire._TRUSTED_PROXIES_RAW
        api_wire._TRUSTED_PROXIES_RAW = trusted
        try:
            return api_wire._caller_key(types.SimpleNamespace(
                headers={"X-Forwarded-For": xff}, remote=peer))
        finally:
            api_wire._TRUSTED_PROXIES_RAW = old

    def test_two_trusted_hops_still_find_the_caller(self):
        # client -> CDN(10.0.0.9) -> proxy(10.0.0.8) -> here.
        # The proxy appended the CDN; the CDN appended the caller.
        self.assertEqual(
            self._key("10.0.0.8", "8.8.4.4, 10.0.0.9", trusted="10.0.0.0/8"),
            "8.8.4.4")

    def test_one_hop_is_unchanged(self):
        # The case that already worked must keep working.
        self.assertEqual(
            self._key("172.19.0.1", "1.2.3.4, 8.8.4.4"), "8.8.4.4")

    def test_a_spoofed_private_address_cannot_hide_the_caller(self):
        # A client writing a private address of its own does not get to make
        # itself unattributable — the walk stops at the first untrusted entry.
        self.assertEqual(
            self._key("172.19.0.1", "10.0.0.5, 8.8.4.4"), "8.8.4.4")


class TestUploadedSoundsCannotFillTheVolume(unittest.TestCase):
    """Each file was capped at 2MB and the collection at nothing, so the same
    2MB could be uploaded until the volume filled — the volume the settings,
    the keys and the call records live on."""

    def test_the_caps_are_sane_for_five_sounds(self):
        from api import sounds as api_sounds

        self.assertGreaterEqual(api_sounds.MAX_SOUND_FILES, 5)
        self.assertLessEqual(api_sounds.MAX_SOUND_FILES, 100)
        self.assertLessEqual(
            api_sounds.MAX_SOUND_TOTAL_BYTES,
            api_sounds.MAX_SOUND_FILES * api_sounds.MAX_SOUND_BYTES,
            "the total cap is higher than the per-file cap allows, so it can "
            "never be the thing that stops an upload")


class TestOneBadTrackCannotSwallowThePrompt(unittest.TestCase):
    """Search results are capped at 8, but nothing capped the size of one.
    Every field goes into the prompt, where length is latency on every
    remaining turn and is paid for per token."""

    def test_a_giant_field_is_trimmed(self):
        from call.tools.music import _fmt_track

        out = _fmt_track({"title": "x" * 5000, "artist": "y" * 5000,
                          "album": "z" * 5000, "moods": ["m" * 900] * 9,
                          "id": "i" * 900}, with_id=True)
        self.assertLess(len(out), 700, f"one track rendered {len(out)} chars")

    def test_an_ordinary_track_is_unchanged(self):
        from call.tools.music import _fmt_track

        self.assertEqual(
            _fmt_track({"title": "Roads", "artist": "Portishead",
                        "album": "Dummy", "year": 1994}),
            '"Roads" by Portishead (Dummy, 1994)')


class TestSettingsThatAreOnlyWrongTogether(_TempStores):
    """Every field validated itself and nothing validated a pair, so a floor
    above its own ceiling saved without complaint and what happened afterwards
    was nobody's intention."""

    def test_a_hangup_floor_above_the_ceiling_is_refused(self):
        why = settings_store.complain(
            {"min_call_seconds": 600, "max_call_seconds": 300})
        self.assertIsNotNone(why)
        self.assertIn("600", why)

    def test_a_daily_cap_below_the_hourly_one_is_refused(self):
        why = settings_store.complain(
            {"calls_per_hour": 30, "calls_per_day": 10})
        self.assertIsNotNone(why)
        self.assertIn("meaningless", why)

    def test_endpointing_delays_the_wrong_way_round_are_refused(self):
        why = settings_store.complain(
            {"min_endpointing_delay": 3.0, "max_endpointing_delay": 1.0})
        self.assertIsNotNone(why)

    def test_the_pair_is_checked_against_what_is_already_stored(self):
        # A patch usually carries one half. Saving a ceiling under the floor
        # already on disk has to be caught too.
        settings_store.save({"min_call_seconds": 600})
        self.assertIsNotNone(settings_store.complain({"max_call_seconds": 300}))

    def test_sensible_pairs_still_save(self):
        self.assertIsNone(settings_store.complain(
            {"min_call_seconds": 60, "max_call_seconds": 600}))
        # 0 means "no limit" on the caps, so it can never be the smaller one.
        self.assertIsNone(settings_store.complain(
            {"calls_per_hour": 30, "calls_per_day": 0}))


class TestACallerCanBeToldNothingIsKept(_TempStores):
    """A transcript is both sides of a stranger's conversation, kept on the
    operator's disk. It is how a bad call gets diagnosed and the README says
    so — but until now there was no way to say no, and no way to say for how
    long. An operator who does not want that has to be able to have it."""

    def setUp(self):
        super().setUp()
        from call import record

        self.record = record
        self._old = record.CALLS_DIR
        record.CALLS_DIR = Path(self._tmp.name) / "calls"

    def tearDown(self):
        self.record.CALLS_DIR = self._old
        super().tearDown()

    def _a_call(self, room="callin-abcdefghijkl"):
        r = self.record.CallRecord(room, {"id": "p1", "name": "Wade"}, {})
        r.turn("caller", "hello")
        return r

    def test_retention_is_the_setting_not_the_constant(self):
        for i in range(8):
            self._a_call(f"callin-{i:012d}").write(keep=3)
        self.assertEqual(len(list(self.record.CALLS_DIR.glob("*.json"))), 3)

    def test_zero_does_not_mean_delete_everything(self):
        # Turning recording OFF is how you keep nothing; a 0 here would be a
        # misreading, not an instruction.
        self._a_call().write(keep=0)
        self.assertEqual(len(list(self.record.CALLS_DIR.glob("*.json"))), 1)

    def test_the_setting_exists_and_defaults_to_keeping_them(self):
        cfg = settings_store.load()
        self.assertIs(cfg["record_calls"], True)
        self.assertEqual(cfg["record_keep"], 40)


class TestTurnTakingDelaysAreOptOut(unittest.TestCase):
    """0 means "leave the SDK's tuned default alone", not "no delay". Passing a
    literal zero would make the DJ answer the instant the caller stopped making
    sound, which is not patience — it is interrupting."""

    def test_unset_passes_nothing_at_all(self):
        from call.session import turn_handling

        self.assertNotIn("endpointing", turn_handling({}))
        self.assertNotIn("endpointing", turn_handling(
            {"min_endpointing_delay": 0, "max_endpointing_delay": 0}))

    def test_a_real_value_is_passed_through(self):
        from call.session import turn_handling

        self.assertEqual(
            turn_handling({"min_endpointing_delay": 0.8})["endpointing"],
            {"min_delay": 0.8})

    def test_nonsense_is_ignored_rather_than_raised(self):
        from call.session import turn_handling

        self.assertNotIn(
            "endpointing", turn_handling({"min_endpointing_delay": "soon"}))


class TestTurnTakingSettingsReachTheCall(unittest.TestCase):
    """The three turn-taking settings were in the panel, documented, saved —
    and silently ignored on every call for as long as they have existed.

    `AgentSession` accepts `allow_interruptions`, `min_endpointing_delay` and
    `max_endpointing_delay` as arguments, but only reads them on the branch
    where `turn_handling` was NOT passed. We passed both, so the SDK took the
    dict and dropped the three on the floor: `allow_interruptions=False` still
    resolved to `enabled: True`, and endpointing stayed at the stock 0.3/2.5.

    That is why the DJ came back chopped mid-sentence on real calls —
    `min_duration` is half a second of SOUND, not words, and the station stream
    playing into the caller's room clears that bar. The documented remedy could
    not be applied because the setting never arrived.

    So this asserts against what the SDK RESOLVED, not against the dict we
    built. A test that checks our own argument shape is exactly what passed
    while this was broken.
    """

    def _resolved(self, cfg: dict):
        import asyncio
        import warnings

        from livekit.agents import AgentSession

        from call.session import turn_handling

        # Built inside a loop: AgentSession.__init__ calls get_event_loop(), so
        # constructing one at import-time depends on whichever test ran last
        # leaving a loop lying around. It passed alone and errored in the suite.
        async def build():
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return AgentSession(turn_handling=turn_handling(cfg)).options

        return asyncio.run(build())

    def test_turning_interruptions_off_actually_turns_them_off(self):
        self.assertIs(
            self._resolved({"allow_interruptions": False}).interruption["enabled"],
            False,
            "the caller can still talk over the DJ with the setting off — the "
            "operator's only remedy for the station bleeding into the mic",
        )
        self.assertIs(
            self._resolved({"allow_interruptions": True}).interruption["enabled"],
            True)

    def test_the_endpointing_delays_arrive_as_set(self):
        got = self._resolved(
            {"min_endpointing_delay": 2.5, "max_endpointing_delay": 9.0}
        ).endpointing
        self.assertEqual((got["min_delay"], got["max_delay"]), (2.5, 9.0))

    def test_unset_delays_leave_the_sdk_tuned_defaults_alone(self):
        stock = self._resolved({}).endpointing
        self.assertEqual((stock["min_delay"], stock["max_delay"]), (0.3, 2.5))

    def test_the_interruption_floor_is_raisable(self):
        # The fix for the chopped turns: how much SOUND it takes to stop the DJ.
        self.assertEqual(
            self._resolved({"min_interruption_secs": 1.6}).interruption["min_duration"],
            1.6)
        self.assertEqual(
            self._resolved({}).interruption["min_duration"], 0.5,
            "unset must leave the SDK's own floor alone")

    def test_preemptive_generation_is_still_off(self):
        # Folding three settings into the same dict must not lose the thing
        # that dict was originally there for: a speculative turn carrying a
        # tool call makes Gemini reject the whole conversation with a 400.
        self.assertIs(
            self._resolved({}).preemptive_generation["enabled"], False)


class TestOneSettingReplacingAnotherSaysSo(unittest.TestCase):
    """Writing an Opening line overrides Greeting style completely — the
    greeting code reads `cfg["greeting"] or the style default`. Showing both
    with nothing saying which wins is the shape 0.9.61 took out of
    front_access, and it was still here."""

    def test_greeting_style_hides_once_an_opening_line_exists(self):
        needs = settings_store.SCHEMA["greeting_style"].get("needs")
        self.assertEqual(needs, ("greeting", False))

    def test_the_widget_understands_that_rule(self):
        # The panel is what actually hides it, and it lives in another
        # language with no test runner — so this pins the one line that
        # implements it.
        js = (Path(__file__).parent.parent / "web-widget" / "panel.js").read_text(
            encoding="utf-8")
        self.assertIn("want === false", js,
                      "panel.js cannot honour a `needs` of False, so the field "
                      "would stay visible and keep looking like it works")


class TestNoSettingIsSmuggledThroughTheEnvironment(unittest.TestCase):
    """tts_mode was written into os.environ by four different modules purely so
    _default_adapter_path could read it back — a setting laundered through
    process-global state with no owner. In the token server that state is
    shared by every concurrent request, so two operators testing different
    backends raced each other."""

    def test_nothing_writes_tts_mode_into_the_environment(self):
        import re

        root = Path(__file__).parent
        offenders = []
        # The whole tree, not a list of directories: the /test/tts endpoint
        # this was written about has since moved into api/, and a guard that
        # has to be extended by hand every time a package appears is a guard
        # that eventually stops looking where the code is.
        for path in root.rglob("*.py"):
            if path.name == "test_sidecar.py" or "__pycache__" in path.parts:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                # Comments describing the old pattern are the point of the
                # comments — only an actual assignment counts.
                if line.strip().startswith("#"):
                    continue
                if re.search(r'os\.environ\[\s*[\"\']TTS_MODE[\"\']\s*\]\s*=', line):
                    offenders.append(f"{path.name}:{n}")
        self.assertEqual(offenders, [], f"tts_mode is being smuggled: {offenders}")

    def test_the_adapter_is_told_its_mode(self):
        from tts_adapter import _default_adapter_path

        self.assertIn("local", _default_adapter_path("local").name)
        self.assertIn("openai", _default_adapter_path("cloud").name)


class TestAnUnsignedWebhookCannotFillMemory(unittest.TestCase):
    """/hooks/station cannot be authenticated — the station does not sign its
    hooks — so its body is arbitrary, and it was stored whole, fifty deep, in a
    worker already running near the SDK's own memory warning line (observed at
    1076MB against a 1000MB threshold on a real call). Summarised now: the
    endpoint is a diagnostic list, and a trimmed rendering is all it was for."""

    def test_a_huge_body_is_not_retained_whole(self):
        from api import hooks as api_hooks

        before = len(api_hooks._hook_events)
        api_hooks._hook_events.append({
            "at": 0.0, "event": "track.changed",
            "data": {str(k)[:40]: str(v)[:120]
                     for k, v in list({"pad": "x" * 500_000}.items())[:12]},
        })
        stored = api_hooks._hook_events[-1]
        self.assertLessEqual(len(str(stored)), 4000,
                             "the whole body was kept")
        self.assertEqual(len(api_hooks._hook_events), before + 1)


# --- station webhook registration ------------------------------------------
# The station's admin API replaces the whole webhook array on every write, so
# registration is a read-modify-write against a list we share with the
# operator and with anything else they have wired up. Everything below defends
# one half of that: our row lands, and nobody else's is disturbed.


class _FakeResponse:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text or (json.dumps(body) if body is not None else "")

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body


class _FakeStation:
    """The station's admin API, as much of it as registration touches.

    Not a network fake for its own sake: the write is a whole-list replace, so
    a test that only checked the response would miss the thing that matters,
    which is what ended up in the list.
    """

    EVENTS = ["track.play", "dj.say", "dj.link", "request.received"]

    def __init__(self, rows=None, events=None, refuse=None, on_test=None):
        self.rows = [dict(r) for r in (rows or [])]
        self.events = self.EVENTS if events is None else list(events)
        self.refuse = refuse            # a _FakeResponse to answer writes with
        self.on_test = on_test          # called when a test fire is requested
        self.writes = []
        self.tests = []

    def __call__(self, user, password):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, path):
        return _FakeResponse(200, {"events": self.events, "webhooks": self.rows})

    async def post(self, path, json=None):
        if path.endswith("/test"):
            self.tests.append(path)
            if self.on_test is None:
                return _FakeResponse(200, {"ok": True})
            # A test fire may want to push at the receiver first, the way the
            # real station does before it answers.
            answer = self.on_test()
            return await answer if hasattr(answer, "__await__") else answer
        self.writes.append(json)
        if self.refuse is not None:
            return self.refuse
        self.rows = [dict(r) for r in (json or {}).get("webhooks") or []]
        return _FakeResponse(200, {"webhooks": self.rows})


class _FakeHookRequest:
    """Enough of an aiohttp request for the receiver, which only reads a body."""

    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


class _StationWebhooks(_TempStores):
    """Registration against a fake station, with the module state restored."""

    def setUp(self):
        super().setUp()
        from api import hooks as api_hooks
        import station_config

        self.hooks = api_hooks
        self.station_config = station_config
        self._old_state = dict(api_hooks._hook_state)
        self._old_client = api_hooks._admin_client
        self._old_creds = station_config.admin_credentials
        api_hooks._hook_state.clear()
        api_hooks._hook_state.update(
            registered=False, url="", id=api_hooks.HOOK_ID, station="",
            events=[], received=0, detail="not attempted")
        station_config.admin_credentials = lambda: ("op", "pw")
        os.environ["CALLIN_HOOK_URL"] = "http://192.0.2.7:8100/hooks/station"

    def tearDown(self):
        self.hooks._admin_client = self._old_client
        self.hooks._hook_state.clear()
        self.hooks._hook_state.update(self._old_state)
        self.station_config.admin_credentials = self._old_creds
        os.environ.pop("CALLIN_HOOK_URL", None)
        super().tearDown()

    def register(self, station):
        self.hooks._admin_client = station
        asyncio.run(self.hooks.register_station_webhook())
        return station


class TestOurWebhookRowKeepsItsIdentity(_StationWebhooks):
    """Registering sends a stable id, and that is the whole point of it.

    Without one the station mints a fresh id per registration, so this box
    moving to a new LAN address left its old row behind and added a second.
    The station caps the list at 16, after which registration fails for good —
    and the operator's only clue is a flat refusal.
    """

    def test_the_row_carries_our_id(self):
        station = self.register(_FakeStation())
        self.assertEqual([r["id"] for r in station.rows], [self.hooks.HOOK_ID])
        self.assertTrue(self.hooks._hook_state["registered"])

    def test_registering_again_does_not_add_a_second_row(self):
        station = self.register(_FakeStation())
        self.hooks._hook_state.update(registered=False, station="")
        self.register(station)
        self.assertEqual(len(station.rows), 1, station.rows)

    def test_an_unchanged_row_is_not_rewritten(self):
        station = self.register(_FakeStation())
        writes = len(station.writes)
        self.hooks._hook_state.update(registered=False, station="")
        self.register(station)
        self.assertEqual(len(station.writes), writes,
                         "a boot that changes nothing still wrote to the station")

    def test_a_new_address_moves_the_row_instead_of_adding_one(self):
        station = self.register(_FakeStation())
        os.environ["CALLIN_HOOK_URL"] = "http://192.0.2.9:8100/hooks/station"
        self.hooks._hook_state.update(registered=False, station="")
        self.register(station)
        self.assertEqual(len(station.rows), 1, station.rows)
        self.assertEqual(station.rows[0]["url"], "http://192.0.2.9:8100/hooks/station")

    def test_a_row_registered_before_we_sent_an_id_is_adopted(self):
        # What every existing deployment looks like on upgrade: our address,
        # an id the station chose. Matching on id alone would leave it there
        # and register a duplicate alongside it.
        station = _FakeStation(rows=[{
            "id": "wh_8f21", "url": "http://192.0.2.7:8100/hooks/station",
            "events": ["track.play"], "enabled": True,
        }])
        self.register(station)
        self.assertEqual(len(station.rows), 1, station.rows)
        self.assertEqual(station.rows[0]["id"], self.hooks.HOOK_ID)


class TestOtherWebhookRowsSurviveOurRegistration(_StationWebhooks):
    """The write replaces the whole array, so anything the operator wired up
    themselves is ours to carry through untouched.

    Including the sentinel the station substitutes for a stored auth header on
    read: it resolves that back by row id, so a row that round-trips unchanged
    keeps its credential — and one that loses its id does not.
    """

    def test_a_foreign_row_round_trips_byte_for_byte(self):
        other = {"id": "n8n_relay", "url": "https://example.invalid/hook",
                 "events": ["dj.say"], "enabled": True, "authHeader": "set"}
        station = self.register(_FakeStation(rows=[other]))
        kept = [r for r in station.rows if r["id"] == "n8n_relay"]
        self.assertEqual(kept, [other])

    def test_a_row_disabled_by_the_operator_is_not_switched_back_on(self):
        station = _FakeStation(rows=[{
            "id": self.hooks.HOOK_ID, "url": "http://192.0.2.7:8100/hooks/station",
            "events": list(self.hooks.WANTED_EVENTS), "enabled": False,
        }])
        self.register(station)
        self.assertFalse(station.rows[0]["enabled"], "we re-enabled our own row")
        # And the panel must not then claim push events are working.
        self.assertIn("disabled", self.hooks._hook_state["detail"])

    def test_adopting_a_row_never_costs_it_a_stored_credential(self):
        # The station resolves the redaction sentinel by row id, so renaming a
        # row to our preferred id would trade the operator's auth header for a
        # tidier name. The URL match finds it again either way.
        station = _FakeStation(rows=[{
            "id": "wh_8f21", "url": "http://192.0.2.7:8100/hooks/station",
            "events": ["track.play"], "enabled": True, "authHeader": "set",
        }])
        self.register(station)
        self.assertEqual(len(station.rows), 1, station.rows)
        self.assertEqual(station.rows[0]["id"], "wh_8f21")
        self.assertEqual(station.rows[0]["authHeader"], "set")

    def test_an_extra_subscription_on_our_row_is_kept(self):
        station = _FakeStation(rows=[{
            "id": self.hooks.HOOK_ID, "url": "http://192.0.2.7:8100/hooks/station",
            "events": ["request.received"], "enabled": True,
        }], events=_FakeStation.EVENTS + ["show.start"])
        station.rows[0]["events"].append("show.start")
        self.register(station)
        self.assertIn("show.start", station.rows[0]["events"])


class TestTheRegistrationShapeIsTheOneTheStationReads(_StationWebhooks):
    """`{"webhooks": [...]}` is the only shape there has ever been.

    This used to try a flat `{"url", "events"}` first. The handler reads
    `req.body.webhooks` and nothing else, and since SUB/WAVE 1.6.0 zod strips
    the unknown keys before it even gets there — so that attempt was answered
    200 and changed nothing, in both directions at once.
    """

    def test_every_write_is_the_whole_list(self):
        station = self.register(_FakeStation())
        self.assertTrue(station.writes)
        for body in station.writes:
            self.assertIn("webhooks", body, body)
            self.assertIsInstance(body["webhooks"], list)

    def test_the_gate_setting_is_never_touched(self):
        # trackPlayListenerGated saves independently, and sending it would
        # overwrite an operator's choice as a side effect of registering.
        station = self.register(_FakeStation())
        for body in station.writes:
            self.assertNotIn("trackPlayListenerGated", body)


class TestARefusedRegistrationSaysWhichFieldWasWrong(_StationWebhooks):
    """A refusal used to read "station did not accept either registration
    shape" whatever the cause — which is exactly the flat, unactionable error
    SUB/WAVE 1.6.0's field-level payload exists to replace."""

    def test_the_stations_own_sentence_reaches_the_panel(self):
        self.register(_FakeStation(refuse=_FakeResponse(
            400, {"error": "URL must start with http:// or https://",
                  "fieldErrors": {"webhooks.0.url": ["URL must start with http://"]}})))
        self.assertIn("URL must start with", self.hooks._hook_state["detail"])

    def test_a_field_error_alone_still_names_the_field(self):
        self.register(_FakeStation(refuse=_FakeResponse(
            400, {"fieldErrors": {"webhooks.0.id": ["id must be 3-32 characters"]}})))
        detail = self.hooks._hook_state["detail"]
        self.assertIn("webhooks.0.id", detail)
        self.assertIn("3-32", detail)

    def test_a_body_that_is_not_json_does_not_lose_the_status(self):
        self.register(_FakeStation(refuse=_FakeResponse(502, text="")))
        self.assertIn("502", self.hooks._hook_state["detail"])

    def test_a_refusal_stops_retrying_but_bad_credentials_do_not(self):
        self.register(_FakeStation(refuse=_FakeResponse(400, {"error": "no"})))
        self.assertTrue(self.hooks._hook_state.get("gave_up"))

        self.hooks._hook_state.pop("gave_up")
        self.register(_FakeStation(refuse=_FakeResponse(401, {"error": "nope"})))
        self.assertFalse(self.hooks._hook_state.get("gave_up"),
                         "a password the operator can fix is not a permanent no")


class TestWeOnlyAskForEventsTheStationKnows(_StationWebhooks):
    """The station validates the event list against an enum and refuses the
    WHOLE registration over one name it doesn't recognise. It advertises its
    own vocabulary on the same read we already make, so there is no reason to
    assert ours against it."""

    def test_an_event_the_station_dropped_is_not_sent(self):
        station = self.register(_FakeStation(events=["track.play", "dj.say"]))
        self.assertEqual(station.rows[0]["events"], ["dj.say", "track.play"])

    def test_a_station_that_advertises_nothing_still_gets_a_registration(self):
        station = self.register(_FakeStation(events=[]))
        self.assertEqual(sorted(station.rows[0]["events"]),
                         sorted(self.hooks.WANTED_EVENTS))

    def test_the_card_busts_for_exactly_what_we_subscribed_to(self):
        self.assertEqual(
            self.hooks._BUSTING_PREFIXES,
            frozenset(e.split(".")[0] for e in self.hooks.WANTED_EVENTS),
            "the events we ask for and the events that refresh the card drifted")


class TestPointingAtANewStationRegistersAgain(_StationWebhooks):
    """`registered` used to be true forever once one station had said yes, so
    changing the station address in the panel left the new one with no
    receiver and the card polling for good."""

    def test_a_changed_station_address_re_arms_registration(self):
        self.register(_FakeStation())
        self.assertFalse(self.hooks._registration_due())

        self.hooks._hook_state["station"] = "http://somewhere-else.invalid"
        self.assertTrue(self.hooks._registration_due())
        self.assertFalse(self.hooks._hook_state["registered"])

    def test_a_previous_refusal_does_not_follow_us_to_a_new_station(self):
        self.hooks._hook_state.update(station="http://old.invalid", gave_up=True)
        self.assertTrue(self.hooks._registration_due())
        self.assertNotIn("gave_up", self.hooks._hook_state)


class TestADeliveredPushIsProvedRatherThanAssumed(_StationWebhooks):
    """"Registered" only ever meant the station accepted a row.

    The receiver is a LAN address behind a NAS, so "the station cannot reach
    it" is the failure that actually happens — and it looks identical to
    working from the panel. The station's own test endpoint fires at one hook
    by id, which makes the whole path testable in both directions.
    """

    def _delivering(self):
        """A station that pushes at us before answering, as the real one does."""
        async def fire():
            await self.hooks.handle_station_hook(
                _FakeHookRequest({"event": "test", "t": "now"}))
            return _FakeResponse(200, {"ok": True})

        return _FakeStation(on_test=fire)

    def test_a_push_that_lands_is_reported_as_delivered(self):
        station = self.register(self._delivering())
        self.hooks._admin_client = station
        result = asyncio.run(self.hooks.fire_test_hook())
        self.assertTrue(result["ok"], result)
        self.assertIn("192.0.2.7", result["detail"])

    def test_a_station_that_cannot_reach_us_is_not_reported_as_working(self):
        station = self.register(_FakeStation())     # accepts, never pushes
        self.hooks._admin_client = station
        self.hooks._DELIVERY_WAIT = 0.05
        try:
            result = asyncio.run(self.hooks.fire_test_hook())
        finally:
            self.hooks._DELIVERY_WAIT = 3.0
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["fired"])
        self.assertIn("192.0.2.7", result["detail"])

    def test_a_row_deleted_at_the_station_re_arms_registration(self):
        station = self.register(_FakeStation(
            on_test=lambda: _FakeResponse(404, {"error": "webhook not found"})))
        self.hooks._admin_client = station
        result = asyncio.run(self.hooks.fire_test_hook())
        self.assertFalse(result["ok"])
        self.assertFalse(self.hooks._hook_state["registered"])
        self.assertTrue(self.hooks._registration_due())

    def test_a_station_without_the_endpoint_says_so(self):
        station = self.register(_FakeStation(
            on_test=lambda: _FakeResponse(404, text="Cannot POST /webhooks/x/test")))
        self.hooks._admin_client = station
        result = asyncio.run(self.hooks.fire_test_hook())
        self.assertIn("no webhook test endpoint", result["detail"])
        self.assertTrue(self.hooks._hook_state["registered"],
                        "an old station is not a reason to forget our row")

    def test_pushes_are_counted_rather_than_read_off_the_capped_list(self):
        # The event list is a deque with a maxlen, so its length saturates —
        # counting from it would silently stop noticing arrivals.
        before = self.hooks._hook_state["received"]
        for _ in range(3):
            asyncio.run(self.hooks.handle_station_hook(
                _FakeHookRequest({"event": "track.play"})))
        self.assertEqual(self.hooks._hook_state["received"], before + 3)


class TestBothSurfacesOfferTheSameControls(unittest.TestCase):
    """The call card's corner controls are the server's decision, not the
    stylesheet's.

    Before 0.9.95 they were three unrelated mechanisms in the widget, and the
    settings gear was hidden by a rule that existed only for embeds — so the
    call page and an embed offered different controls, which nobody had
    decided. Anything the widget subtracts from this it subtracts for a
    reason this side cannot see (a host page that pinned a theme, an embed
    with no panel loaded); it may never ADD one.
    """

    def test_the_help_button_follows_its_setting(self):
        from api import live as api_live

        self.assertTrue(api_live.corner_controls(
            {"show_caller_help": True})["help"])
        self.assertFalse(api_live.corner_controls(
            {"show_caller_help": False})["help"])

    def test_pinning_a_theme_takes_the_toggle_away(self):
        from api import live as api_live

        for pinned in ("light", "dark"):
            with self.subTest(theme=pinned):
                self.assertFalse(api_live.corner_controls(
                    {"widget_theme": pinned})["theme"],
                    "a pinned theme leaves nothing to toggle")

    def test_auto_and_inherit_keep_the_toggle(self):
        from api import live as api_live

        # "inherit" is not a pinned theme: on the standalone page, where
        # there is no host to inherit from, it behaves as auto.
        for choice in ("auto", "inherit", "", None):
            with self.subTest(theme=choice):
                self.assertTrue(api_live.corner_controls(
                    {"widget_theme": choice})["theme"])

    def test_the_widget_reads_the_keys_the_server_writes(self):
        # The widget subtracts from these by name. A rename on one side only
        # would silently hide a control rather than raising anything.
        from api import live as api_live

        call_js = (Path(__file__).parent.parent / "web-widget" / "call.js"
                   ).read_text(encoding="utf-8")
        for key in api_live.corner_controls({}):
            with self.subTest(key=key):
                self.assertIn(f"c.{key} !== false", call_js,
                              f"call.js never reads controls.{key}")


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


class TestTheDocsKeepUpWithTheCode(unittest.TestCase):
    """Documentation drift, caught the same way everything else here is.

    0.9.78 added a whole settings section and made call recording optional, and
    the README went on describing neither — the settings table had no row for
    Turn-taking, and "Diagnosing a call" still opened with "each call writes one
    file as it ends", which had just stopped being unconditionally true. Both
    were found by being asked, not by checking, which is the wrong order.

    Deliberately mechanical: it checks that a thing is *mentioned*, not that it
    is described well. A missing row is the failure that actually happens; bad
    prose is a review problem and this cannot judge it.
    """

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).parent.parent
        cls.readme = (root / "README.md").read_text(encoding="utf-8")
        cls.envex = (root / ".env.example").read_text(encoding="utf-8")

    def test_every_settings_section_is_in_the_readme_table(self):
        # The panel builds its sections from GROUPS; the README lists them by
        # title. A new section that nobody can find in the docs may as well be
        # the unreachable-setting bug one level up.
        missing = [title for _, _, title, _ in settings_store.GROUPS
                   if title.lower() not in self.readme.lower()]
        self.assertFalse(
            missing,
            f"settings sections with no mention in README.md: {missing}")

    def test_every_environment_variable_is_documented(self):
        # Only the CURRENT name of each field. Legacy aliases (DEEPGRAM_MODEL)
        # are deliberately undocumented — they exist so an old .env keeps
        # working, not so a new one copies them.
        wanted = set()
        for env_var, _ in settings_store.FIELDS.values():
            if isinstance(env_var, str) and env_var:
                wanted.add(env_var)
            elif isinstance(env_var, tuple) and env_var:
                wanted.add(env_var[0])
        missing = sorted(v for v in wanted if v not in self.envex)
        self.assertFalse(
            missing, f"env vars a setting reads but .env.example never names: "
                     f"{missing}")

    def test_the_shipped_compose_uses_the_data_directory_both_services_share(self):
        # They are one image in two containers and must see the same data/,
        # or a settings change never reaches the worker.
        compose = (Path(__file__).parent.parent / "docker-compose.yaml").read_text(
            encoding="utf-8")
        self.assertEqual(
            compose.count("./data:/data"), 2,
            "both python services must mount the same data directory")


class TestAVoiceTheBackendCannotSpeakIsNotSilence(unittest.TestCase):
    """Observed on air 2026-08-05, room e7f9ff6f8252, and the record caught it
    perfectly while nothing prevented it.

    The station maps Rosie (p_default1) to `zmcVlqmyk3Jpn5AVYcAL`, an
    ElevenLabs id, because that is what she is broadcast with. This service was
    pointed at local VibeVoice, which has -Cliff1, Lily, Delia1 and no such
    voice. Mirroring the station's voice is RIGHT — the call-in DJ should sound
    like the one on air — but the voice belongs to the station's TTS, not
    necessarily to ours.

    So the DJ wrote a perfectly good greeting, every TTS request 400'd eight
    times over, and the caller sat in silence for the whole call. The 0.9.20
    dead-air fallback was mute too, because it speaks through the same backend.

    A voice the backend does not have is not a reason to say nothing. It is a
    reason to say it differently and write down why.
    """

    def test_the_station_voice_is_kept_when_the_backend_has_it(self):
        from tts_adapter import pick_speakable_voice

        got, why = pick_speakable_voice("-Cliff1", ["-Brock1", "-Cliff1", "Lily"])
        self.assertEqual(got, "-Cliff1")
        self.assertEqual(why, "")

    def test_rosie(self):
        from tts_adapter import pick_speakable_voice

        got, why = pick_speakable_voice(
            "zmcVlqmyk3Jpn5AVYcAL", ["-Brock1", "-Cliff1", "Lily"])
        self.assertIn(got, ["-Brock1", "-Cliff1", "Lily"])
        self.assertIn("zmcVlqmyk3Jpn5AVYcAL", why)
        # The operator has to be able to act on it, so it says what to do.
        self.assertIn("Voice", why)

    def test_a_failed_lookup_never_changes_the_voice(self):
        # The one that would be worse than the bug: an empty list means "could
        # not find out", and treating that as "has none" would make a slow or
        # unreachable TTS server rewrite every DJ's voice.
        from tts_adapter import pick_speakable_voice

        got, why = pick_speakable_voice("zmcVlqmyk3Jpn5AVYcAL", [])
        self.assertEqual(got, "zmcVlqmyk3Jpn5AVYcAL")
        self.assertEqual(why, "")

    def test_asking_for_nothing_is_not_worth_a_warning(self):
        from tts_adapter import pick_speakable_voice

        got, why = pick_speakable_voice("", ["-Brock1", "Lily"])
        self.assertEqual(got, "-Brock1")
        self.assertEqual(why, "")

    def test_the_worker_and_the_panel_share_one_voice_lookup(self):
        # A panel showing one set of voices while the worker believes another
        # is how a call asks for a voice that is not there. Same function.
        from api import settings as api_settings
        from tts_adapter import available_voices

        self.assertIs(api_settings.tts_voice_list, available_voices)


class TestWhatTheBackendSaidReachesTheOperator(unittest.TestCase):
    """httpx renders a failed request as "Client error '400 Bad Request' for
    url ..." and stops. The body — which is where the backend explains itself —
    was thrown away at every one of the four places this file checks a status.

    That body is routinely the only actionable thing in the failure. A voice
    cloning engine refusing because a reference clip is longer than Whisper's
    30-second ceiling says exactly that, in the body, and the operator saw an
    opaque 400. /test/tts grew a hand-written guess at what a 400 probably
    meant precisely because the real answer never arrived.
    """

    def _said(self, response):
        import asyncio

        from tts_adapter import _backend_said

        return asyncio.run(_backend_said(response))

    def _resp(self, status=400, **kw):
        import httpx

        return httpx.Response(
            status, request=httpx.Request("POST", "http://tts.test/v1/audio/speech"), **kw)

    def test_a_json_detail_is_what_the_operator_reads(self):
        # The real shape from a FastAPI-based cloning server hitting the
        # 30-second transcription limit.
        said = self._said(self._resp(json={
            "detail": "You have passed more than 3000 mel input features "
                      "(> 30 seconds) which automatically enables long-form "
                      "generation which requires the model to predict timestamp "
                      "tokens..."}))
        self.assertIn("30 seconds", said)

    def test_the_other_two_envelopes_in_the_wild(self):
        self.assertIn("no such voice", self._said(self._resp(
            json={"message": "no such voice"})))
        # OpenAI nests it one level down.
        self.assertIn("model not found", self._said(self._resp(
            json={"error": {"message": "model not found", "type": "invalid_request"}})))

    def test_plain_text_survives_too(self):
        self.assertIn("Voice 'New-Walken' not found", self._said(
            self._resp(text="Voice 'New-Walken' not found in /voices.")))

    def test_a_wall_of_audio_is_not_an_explanation(self):
        # A backend that answers 4xx with a body of PCM would otherwise put
        # several hundred bytes of mojibake in the operator's error line.
        self.assertEqual("", self._said(self._resp(
            content=b"\x00\x01" * 4000, headers={"content-type": "audio/pcm"})))

    def test_it_cannot_run_away_with_the_error_line(self):
        from tts_adapter import _ERROR_BODY_CHARS

        said = self._said(self._resp(text="x" * 5000))
        self.assertLessEqual(len(said), _ERROR_BODY_CHARS)

    def test_a_good_response_is_never_even_read(self):
        # Not just "does not raise". On the streaming path the body IS the
        # caller's audio and it has not been consumed yet when the status
        # arrives — reading it to look for an explanation would swallow the
        # speech before aiter_bytes ever saw it.
        import asyncio

        import tts_adapter

        async def explode(_):
            raise AssertionError("read the body of a perfectly good response")

        real = tts_adapter._backend_said
        tts_adapter._backend_said = explode
        try:
            got = asyncio.run(tts_adapter._raise_for_status(
                self._resp(200, content=b"\x00\x00")))
        finally:
            tts_adapter._backend_said = real
        self.assertIsNone(got)

    def test_the_status_AND_the_reason_are_both_in_the_raise(self):
        import asyncio

        from livekit.agents import APIConnectionError

        from tts_adapter import _raise_for_status

        with self.assertRaises(APIConnectionError) as caught:
            asyncio.run(_raise_for_status(
                self._resp(400, json={"detail": "reference clip too long"})))
        message = str(caught.exception)
        self.assertIn("400", message)
        self.assertIn("reference clip too long", message)

    def test_the_hand_written_guess_yields_to_the_real_answer(self):
        # /test/tts carries a hint about cloud voice ids not being local ones.
        # It exists only because the body used to be thrown away. A backend
        # that names the voice in its own error has explained itself, and
        # stacking a guess underneath is noise.
        import pathlib

        source = pathlib.Path(
            __file__).with_name("api").joinpath("diagnostics.py").read_text(encoding="utf-8")
        self.assertIn('if "400" in msg and voice and voice not in msg:', source)

    def test_every_status_check_in_the_synthesis_path_uses_it(self):
        # Three checks in the path a caller's audio comes down — streaming,
        # buffered, and the json_url follow-up. A bare raise_for_status left
        # in any of them puts that path back to an opaque status, which is
        # the whole bug.
        import pathlib

        source = pathlib.Path(
            __file__).with_name("tts_adapter.py").read_text(encoding="utf-8")
        speaking = source.split("class AdapterChunkedStream", 1)[1]
        self.assertNotIn("raise_for_status()", speaking)
        self.assertEqual(3, speaking.count("await _raise_for_status("))


class TestVoiceDiscoveryIsNotHardcodedToOneShape(unittest.TestCase):
    """The adapter described how to SYNTHESIZE and never how to DISCOVER, so
    /v1/audio/voices and OpenAI's {"data":[{"id":...}]} were baked in.

    That is worse than it sounds, because an empty list means "could not find
    out" everywhere in tts_adapter. A backend that lists its voices perfectly
    well at a different path, or in a different shape, does not read as an
    error here — it silently switches off pick_speakable_voice, leaves the
    panel showing stock OpenAI names, and lets the station's voice go to a
    backend that never had it. All the machinery for that case exists and
    simply never engages.
    """

    def test_the_openai_shape_still_works(self):
        from tts_adapter import parse_voice_list

        self.assertEqual(
            ["alloy", "onyx"],
            parse_voice_list({"data": [{"id": "onyx"}, {"id": "alloy"}]}))

    def test_a_bare_list_of_names(self):
        from tts_adapter import parse_voice_list

        self.assertEqual(["Delia1", "Lily"], parse_voice_list(["Lily", "Delia1"]))

    def test_a_voices_key_with_either_thing_inside(self):
        from tts_adapter import parse_voice_list

        self.assertEqual(["Lily"], parse_voice_list({"voices": ["Lily"]}))
        self.assertEqual(["Lily"], parse_voice_list({"voices": [{"name": "Lily"}]}))

    def test_a_mapping_of_id_to_details(self):
        from tts_adapter import parse_voice_list

        self.assertEqual(
            ["Cliff1", "Lily"],
            parse_voice_list({"Lily": {"lang": "en"}, "Cliff1": {"lang": "en"}}))

    def test_an_error_envelope_does_not_become_a_voice_called_detail(self):
        # The reason the mapping branch insists every value is a dict.
        from tts_adapter import parse_voice_list

        self.assertEqual([], parse_voice_list({"detail": "not found"}))

    def test_nonsense_is_no_voices_rather_than_an_exception(self):
        from tts_adapter import parse_voice_list

        for junk in (None, 7, "", [], {}, [None, 3, {}], {"data": "nope"}):
            self.assertEqual([], parse_voice_list(junk))

    def test_the_adapter_chooses_the_path(self):
        from tts_adapter import ADAPTER_DIR, load_adapter

        # Unset, every existing deployment keeps the path it had.
        self.assertEqual(
            "/v1/audio/voices",
            load_adapter(ADAPTER_DIR / "openai-cloud.json")["voices_path"])

    def test_a_configured_path_is_the_one_that_gets_fetched(self):
        import asyncio
        import json as _json
        import tempfile
        from pathlib import Path

        import tts_adapter

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "elsewhere.json"
            path.write_text(_json.dumps({
                "endpoint_path": "/v1/audio/speech",
                "voices_path": "/voices",
            }), encoding="utf-8")

            asked = {}

            class FakeResponse:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    return ["New-Walken", "Delia1"]

            class FakeClient:
                def __init__(self, **kw):
                    pass

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def get(self, url):
                    asked["url"] = url
                    return FakeResponse()

            real = tts_adapter.httpx.AsyncClient
            tts_adapter.httpx.AsyncClient = FakeClient
            try:
                got = asyncio.run(tts_adapter.available_voices(
                    "http://tts.test", adapter_path=str(path)))
            finally:
                tts_adapter.httpx.AsyncClient = real

        self.assertEqual("/voices", asked["url"])
        self.assertEqual(["Delia1", "New-Walken"], got)

    def test_there_is_only_one_voice_lookup_left(self):
        # station_config carried a second copy: its own client, its own
        # hardcoded /v1/audio/voices, its own OpenAI-shaped parse. It answered
        # "no voices" for backends the panel next door listed fine.
        import pathlib

        source = pathlib.Path(
            __file__).with_name("station_config.py").read_text(encoding="utf-8")
        # Comments are allowed to name the path — that is where the incident
        # is written down. Code is not.
        code = "\n".join(line for line in source.splitlines()
                         if not line.lstrip().startswith("#"))
        self.assertNotIn("/v1/audio/voices", code)
        self.assertIn("available_voices", code)


class TestADeclaredSampleRateIsMeasuredNotTrusted(unittest.TestCase):
    """A sample rate is a label attached to the samples, not something carried
    in them. Declare 24000 for a backend producing 48000 and every line plays
    at half speed an octave down — and no component anywhere reports an error,
    because nothing is wrong except the label.

    It is easy to get wrong for a reason no documentation fixes: the same
    build of a local engine commonly reports one rate on a GPU and half of it
    on a CPU, so an adapter that is correct on the operator's box is silently
    wrong on the next person's.

    The obvious check — infer the rate from how fast the speech sounds — is a
    trap, and the band here is enormous because of it. A persona written to
    speak in fast clipped fragments produces a fraction of the audio a normal
    voice does for the same text; reasoning from one lands several octaves out
    with total confidence. So: measure from a wav header, and treat the
    inference as "worth checking" and never as an answer.
    """

    def _wav(self, rate: int) -> bytes:
        import struct

        fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
        return (b"RIFF" + struct.pack("<I", 36) + b"WAVE"
                + b"fmt " + struct.pack("<I", len(fmt)) + fmt
                + b"data" + struct.pack("<I", 0))

    def test_the_rate_comes_out_of_the_header(self):
        from tts_adapter import riff_sample_rate

        for rate in (8000, 16000, 24000, 44100, 48000):
            self.assertEqual(rate, riff_sample_rate(self._wav(rate)))

    def test_something_that_is_not_a_wav_measures_nothing(self):
        from tts_adapter import riff_sample_rate

        self.assertIsNone(riff_sample_rate(b""))
        self.assertIsNone(riff_sample_rate(b"\x00\x01" * 500))       # raw pcm
        self.assertIsNone(riff_sample_rate(b'{"detail": "no wav"}'))

    def test_agreement_is_reported_as_confirmed_not_as_silence(self):
        from api.diagnostics import _sample_rate_verdict

        note = _sample_rate_verdict(24000, 24000, "", "hello there", 1.0)
        self.assertIn("24000", note)
        self.assertIn("confirmed", note)

    def test_a_mismatch_says_the_number_to_change_it_to(self):
        from api.diagnostics import _sample_rate_verdict

        note = _sample_rate_verdict(24000, 48000, "", "hello there", 1.0)
        self.assertIn("MISMATCH", note)
        self.assertIn("48000", note)
        # Useless without telling the operator where the wrong number lives.
        self.assertIn("sample_rate", note)

    def test_which_way_round_the_speed_goes(self):
        # Got this backwards once and the note confidently said the opposite
        # of what the operator could hear. The player consumes `declared`
        # samples a second out of audio carrying `measured` of them, so
        # declaring half the real rate plays SLOW and low, not fast and high.
        from api.diagnostics import _sample_rate_verdict

        self.assertIn("0.5× speed", _sample_rate_verdict(
            24000, 48000, "", "hello", 1.0))
        self.assertIn("2× speed", _sample_rate_verdict(
            48000, 24000, "", "hello", 1.0))

    def test_an_unmeasurable_backend_with_plausible_speech_says_nothing(self):
        from api.diagnostics import _sample_rate_verdict

        # 44 characters in 3.7s — 11.8 a second, ordinary speech.
        self.assertEqual("", _sample_rate_verdict(
            24000, None, "no wav", "x" * 44, 3.73))

    def test_an_impossible_pace_is_raised_as_suspicion_only(self):
        from api.diagnostics import _sample_rate_verdict

        # 119 characters in 0.9s: 132 a second. Nothing speaks like that.
        note = _sample_rate_verdict(48000, None, "no wav", "x" * 119, 0.9)
        self.assertIn("⚠", note)
        # It must not read as a verdict. The one time this reasoning was
        # trusted outright it produced a rate four times too slow.
        self.assertIn("confirm", note.lower())

    def test_the_probe_needs_a_format_field_it_knows_the_name_of(self):
        # Asking for wav means writing into a field only the adapter can name.
        # Guessing "response_format" at a backend that does not have it would
        # turn a working test button into a 422.
        import asyncio

        import tts_adapter

        tts = tts_adapter.AdapterTTS.__new__(tts_adapter.AdapterTTS)
        tts._adapter = {"static_fields": {"num_steps": 4}, "method": "POST",
                        "endpoint_path": "/x", "request_field_map": {}}
        rate, why = asyncio.run(tts.probe_sample_rate())
        self.assertIsNone(rate)
        self.assertIn("format", why)


class TestEveryPersonaIsCheckedNotOnlyTheOneOnAir(unittest.TestCase):
    """pick_speakable_voice only ever sees the persona a call actually
    reached, so a persona whose voice the backend does not have stays
    invisible until someone rings in while that DJ is live.

    It falls back and says why, which is right — but it is the wrong moment to
    find out. The caller reaches the station's DJ in somebody else's voice,
    and mirroring the on-air voice was the entire point. The roster is already
    fetched for the panel; checking all of it moves the discovery to the
    button press.
    """

    def _audit(self, available, voices):
        import asyncio

        from api import diagnostics

        class FakeStationConfig:
            def __init__(self, *a, **kw):
                pass

            async def persona_voices(self):
                if isinstance(voices, Exception):
                    raise voices
                return voices

            async def aclose(self):
                pass

        real = diagnostics.StationConfig
        diagnostics.StationConfig = FakeStationConfig
        try:
            return asyncio.run(diagnostics._persona_voice_audit(available))
        finally:
            diagnostics.StationConfig = real

    def test_a_persona_nobody_has_called_yet_is_named(self):
        note = self._audit(
            ["-Cliff1", "Lily"],
            {"p_default1": "-Cliff1", "p_night": "New-Walken"})
        self.assertIn("p_night", note)
        self.assertNotIn("p_default1", note)

    def test_a_complete_roster_says_so_rather_than_nothing(self):
        # Silence would be indistinguishable from the check not running.
        note = self._audit(["-Cliff1", "Lily"],
                           {"p_default1": "-Cliff1", "p_night": "Lily"})
        self.assertIn("2 personas", note)

    def test_a_failed_voice_lookup_accuses_no_one(self):
        # Same rule as pick_speakable_voice: an empty list is "could not find
        # out". Reporting every persona as broken because the TTS server was
        # slow would be worse than not checking.
        self.assertEqual("", self._audit([], {"p_default1": "-Cliff1"}))

    def test_an_unreadable_station_does_not_fail_the_whole_pipeline_test(self):
        note = self._audit(["-Cliff1"], RuntimeError("station 401"))
        self.assertIn("station 401", note)


class TestTheCloseReasonIsReadable(unittest.TestCase):
    """0.9.76 mapped the SDK's close reason to plain words and assumed the enum
    stringified to its bare value. It does not: str() gives
    "CloseReason.USER_INITIATED". The mapping therefore never matched, and the
    first real call after it shipped wrote that whole repr into endedBecause —
    the raw thing the mapping existed to avoid showing.

    Caught by reading a real record (2026-08-05, 456758bdbbae), not by a test,
    which is the wrong order and is why this one exists.
    """

    def _reason(self, raw):
        import types

        from call import lifecycle

        ended = {"reason": ""}
        captured = {}
        session = types.SimpleNamespace(
            on=lambda name, fn: captured.__setitem__(name, fn))
        lifecycle.attach_close_reason(session, ended)
        captured["close"](types.SimpleNamespace(reason=raw))
        return ended["reason"]

    def test_the_qualified_enum_form_is_understood(self):
        self.assertEqual(
            self._reason("CloseReason.PARTICIPANT_DISCONNECTED"),
            "the caller hung up")

    def test_the_bare_value_is_understood_too(self):
        # Whichever the SDK hands over, since it has been both.
        self.assertEqual(
            self._reason("PARTICIPANT_DISCONNECTED"), "the caller hung up")

    def test_an_unknown_reason_is_passed_through_rather_than_swallowed(self):
        self.assertEqual(self._reason("CloseReason.SOMETHING_NEW"),
                         "CloseReason.SOMETHING_NEW")

    def test_a_broken_event_does_not_take_the_call_down(self):
        # This runs on the way out of a call, after the audio is done but
        # before the on-air handoff.
        self.assertEqual(self._reason(None), "")


class TestJoinTokensExpire(unittest.TestCase):
    def test_a_minted_token_is_short_lived(self):
        """A join token is the only thing between a stranger and an agent job.
        The door code and the usage limits are checked when it is MINTED, so a
        long-lived token is a line that can be reopened without passing either
        again. The SDK default is six hours."""
        from api import tokens as api_tokens

        self.assertLessEqual(api_tokens.TOKEN_TTL.total_seconds(), 300)


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
        for path in Path(__file__).parent.joinpath("call/tools").glob("*.py"):
            recorded.update(
                re.findall(r"actions\.note\(\s*[\"'](\w+)[\"']", path.read_text())
            )
        self.assertTrue(recorded, "found no actions.note() calls to check")
        self.assertEqual(
            sorted(recorded - set(CallActions.LABELS)), [],
            "an action kind is recorded but has no label, so the caller sees "
            "'Action completed' instead of what actually happened",
        )


class TestTheAirGuardHoldsTheCallDJBack(unittest.TestCase):
    """The call DJ and the on-air DJ are the same voice.

    Left alone they talk over each other and the whole broadcast hears it
    doubled. Everything here defends one rule: the gate is the single source
    of truth about whether the air is busy, so the reply gate, the on-air
    tools and the widget's chip can never disagree.
    """

    def _guard(self, station=None, **cfg):
        from call.air import OnAirGuard

        base = {"avoid_on_air_overlap": True, "on_air_quiet_secs": 30}
        base.update(cfg)
        return OnAirGuard(station or object(), base)

    def test_a_disabled_guard_never_makes_anyone_wait(self):
        import asyncio

        guard = self._guard(avoid_on_air_overlap=False)
        self.assertEqual(asyncio.run(guard.wait_until_clear()), 0.0)

    def test_our_own_action_closes_the_gate_before_the_station_log_catches_up(self):
        # Waiting for the poll to notice left a window in which the DJ carried
        # on talking over its own announcement — seen on a real call, right
        # after it had said it was going off to air something.
        guard = self._guard()
        self.assertTrue(guard._clear.is_set())
        guard.mark_on_air(25)
        self.assertTrue(guard.on_air)
        self.assertFalse(guard._clear.is_set())

    def test_dead_air_is_worse_than_an_overlap(self):
        # If the station has been "speaking" for longer than any real link,
        # the log is stale — let the call carry on rather than sit in silence.
        import asyncio

        guard = self._guard()
        guard.mark_on_air(600)
        waited = asyncio.run(guard.wait_until_clear(timeout=0.05))
        self.assertGreaterEqual(waited, 0.05)
        self.assertTrue(guard._clear.is_set(), "the caller was left in silence")

    def _watch(self, answers, stop_when):
        import asyncio

        class _Station:
            def __init__(self):
                self.left = list(answers)

            async def seconds_since_on_air_speech(self):
                return self.left.pop(0) if self.left else None

        class _Session:
            def __init__(self):
                self.said = []
                self.interrupted = 0

            def interrupt(self):
                self.interrupted += 1

            def say(self, text, **kw):
                self.said.append(text)

        async def _run():
            guard = self._guard(station=_Station())
            guard.POLL_SECS = 0.01
            session = _Session()
            task = asyncio.create_task(guard.watch(session))
            for _ in range(300):
                await asyncio.sleep(0.01)
                if stop_when(session):
                    break
            task.cancel()
            return session

        return asyncio.run(_run())

    def test_dialling_in_mid_link_does_not_cut_the_greeting_off(self):
        # The first pass closes the gate SILENTLY. Someone who dials in during
        # a link should have their first reply held, without the greeting being
        # interrupted by a hand-over line for a broadcast that was already
        # running when they picked up the phone.
        session = self._watch([1, 1, 1], stop_when=lambda s: False)
        self.assertEqual(session.said, [])
        self.assertEqual(session.interrupted, 0)

    def test_the_air_going_busy_mid_call_hands_over_out_loud(self):
        # Clear first (no transition), then busy — that one is not the first
        # pass, so the caller is told why the DJ has stopped.
        session = self._watch([None, 1, 1, 1], stop_when=lambda s: s.said)
        self.assertTrue(session.said, "the DJ went quiet without telling the caller")
        self.assertGreaterEqual(session.interrupted, 1)


class TestBackgroundWorkIsNotGarbageCollected(unittest.TestCase):
    """`asyncio.create_task` alone keeps only a weak reference, so a task with
    no other reference can be collected mid-flight. That showed up as action
    cards and on-air state changes going missing at random — worse than a
    feature that never existed, because it looks like it works."""

    def test_a_spawned_task_is_held_until_it_finishes_and_then_released(self):
        import asyncio

        from call import background

        async def _run():
            started, release = asyncio.Event(), asyncio.Event()

            async def work():
                started.set()
                await release.wait()

            task = background.spawn(work())
            await started.wait()
            held = task in background._background
            release.set()
            await task
            await asyncio.sleep(0)      # let the done-callback run
            return held, task in background._background

        held, still_held = asyncio.run(_run())
        self.assertTrue(held, "the task was not referenced while it ran")
        self.assertFalse(still_held, "finished tasks are never released")


class TestEndingACallDisconnectsTheCaller(unittest.TestCase):
    """ctx.shutdown() alone only ends the AGENT's job. The caller stayed
    connected to a DJ-less room — mic hot, timer running, "on the line"
    forever. Three things end calls (the DJ wrapping up, the idle watcher, the
    hard limit) and all three go through here, so this is the one place to
    get it right."""

    def _ctx(self, delete_raises=False):
        class _Room:
            name = "call-room"

        class _RoomApi:
            def __init__(self):
                self.deleted = []

            async def delete_room(self, req):
                if delete_raises:
                    raise RuntimeError("livekit unreachable")
                self.deleted.append(req)

        class _Ctx:
            def __init__(self):
                self.room = _Room()
                self.api = types.SimpleNamespace(room=_RoomApi())
                self.shutdown_reasons = []

            def shutdown(self, reason=None):
                self.shutdown_reasons.append(reason)

        return _Ctx()

    def test_the_room_is_deleted_so_the_caller_is_actually_disconnected(self):
        import asyncio

        from call.hangup import end_call

        ctx = self._ctx()
        asyncio.run(end_call(ctx, "wrapped up"))
        self.assertEqual(len(ctx.api.room.deleted), 1)
        self.assertEqual(ctx.api.room.deleted[0].room, "call-room")
        self.assertEqual(ctx.shutdown_reasons, ["wrapped up"])

    def test_the_agent_still_leaves_when_the_room_cannot_be_deleted(self):
        import asyncio

        from call.hangup import end_call

        ctx = self._ctx(delete_raises=True)
        asyncio.run(end_call(ctx, "time limit"))
        self.assertEqual(ctx.shutdown_reasons, ["time limit"],
                         "a failed room delete stranded the agent in the call")


class TestSearchingForWhatTheCallerActuallySaid(unittest.TestCase):
    """The station's search needs EVERY word to match, so the natural phrase a
    caller uses returns nothing. A caller heard "can't pull that from the
    racks" for a track the library holds three copies of."""

    def test_the_by_connector_is_stripped_before_reporting_a_miss(self):
        from call.tools.music import _query_variants

        self.assertEqual(
            _query_variants("Let It Be by The Beatles"),
            ["Let It Be by The Beatles", "Let It Be The Beatles", "Let It Be"],
        )

    def test_a_title_containing_by_survives_the_split(self):
        # Rightmost split, so "Stand by Me" is never torn in half.
        from call.tools.music import _query_variants

        variants = _query_variants("Stand by Me by Ben E. King")
        self.assertIn("Stand by Me", variants)
        self.assertNotIn("Stand", variants)

    def test_a_query_with_no_connector_is_tried_once(self):
        from call.tools.music import _query_variants

        self.assertEqual(_query_variants("Mr. Blue Sky"), ["Mr. Blue Sky"])


class TestAMoodIsNotASearch(unittest.TestCase):
    """A caller saying "something fun" wants the station's picker, not a title
    match — but the model reaches for the search tool anyway, and "fun"
    dutifully returns "Fun, Fun, Fun" by The Beach Boys. Observed on a real
    call. The guard is deliberately conservative: every meaningful word has to
    be a mood word, so real titles are untouched."""

    def test_a_pure_description_is_recognised(self):
        from call.tools.music import looks_like_a_vibe

        for q in ("something fun", "chill", "some upbeat music",
                  "play me something nice"):
            self.assertTrue(looks_like_a_vibe(q), q)

    def test_real_titles_containing_a_mood_word_are_left_alone(self):
        from call.tools.music import looks_like_a_vibe

        for q in ("Fun House by The Stooges", "Mr. Blue Sky",
                  "Heavy Metal by Sammy Hagar"):
            self.assertFalse(looks_like_a_vibe(q), q)

    def test_a_long_phrase_is_never_treated_as_a_vibe(self):
        from call.tools.music import looks_like_a_vibe

        self.assertFalse(looks_like_a_vibe("something fun and upbeat and happy too"))

    def test_an_empty_query_is_not_a_vibe(self):
        from call.tools.music import looks_like_a_vibe

        self.assertFalse(looks_like_a_vibe(""))
        self.assertFalse(looks_like_a_vibe(None))


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


class TestTheLogKeepsTheLinesThatMatter(unittest.TestCase):
    """Third-party chatter drowns the real events. The widget polls /live every
    20 seconds forever, and the panel's log viewer reads the same ring buffer,
    so unfiltered noise makes it useless."""

    def setUp(self):
        import log_setup

        log_setup.setup("tests", console=False)     # idempotent
        log_setup.RECENT.clear()

    def test_the_widgets_polling_is_dropped(self):
        import logging as _logging

        import log_setup

        access = _logging.getLogger("aiohttp.access")
        access.info('GET /live HTTP/1.1 200')
        access.info('GET /health HTTP/1.1 200')
        self.assertEqual(log_setup.recent_lines(), [])

    def test_real_requests_are_kept(self):
        import logging as _logging

        import log_setup

        _logging.getLogger("aiohttp.access").info('POST /token HTTP/1.1 200')
        self.assertTrue(any("/token" in line for line in log_setup.recent_lines()))

    def test_the_panel_can_read_recent_lines_without_docker(self):
        import logging as _logging

        import log_setup

        for i in range(5):
            _logging.getLogger("callin.test").info("event %d", i)
        lines = log_setup.recent_lines(3)
        self.assertEqual(len(lines), 3)
        self.assertIn("event 4", lines[-1])

    def test_setting_up_twice_does_not_double_every_line(self):
        # The token server's test endpoints import main.py, whose module-level
        # setup("worker") would otherwise add a second handler.
        import logging as _logging

        import log_setup

        before = len(_logging.getLogger().handlers)
        log_setup.setup("tests-again", console=True)
        self.assertEqual(len(_logging.getLogger().handlers), before)


class TestTheSttModelIsLoadedOnceForTheWholeProcess(unittest.TestCase):
    """Loading is slow enough that doing it per call would be audible, so one
    model is shared per (name, compute type) across every call in the process.
    The prewarm hook is synchronous on purpose: the Agents SDK calls
    stt.prewarm() without awaiting, so an async version silently never ran."""

    KEY = ("test-fake.en", "int8")

    def setUp(self):
        import local_stt

        # A sentinel in the cache means neither preload_sync nor the STT class
        # reaches for faster_whisper, so this test needs no model download.
        local_stt._models[self.KEY] = object()

    def tearDown(self):
        import local_stt

        local_stt._models.pop(self.KEY, None)

    def test_preloading_an_already_loaded_model_is_a_no_op(self):
        import local_stt

        before = dict(local_stt._models)
        local_stt.preload_sync("test-fake.en", "int8")
        self.assertEqual(local_stt._models, before)

    def test_prewarm_is_synchronous_so_the_sdk_actually_runs_it(self):
        import inspect

        import local_stt

        self.assertFalse(
            inspect.iscoroutinefunction(local_stt.LocalWhisperSTT.prewarm),
            "an async prewarm is never awaited by the SDK and silently does nothing",
        )
        stt_impl = local_stt.LocalWhisperSTT(model="test-fake.en", compute_type="int8")
        stt_impl.prewarm()      # must not try to download anything

    def test_it_declares_itself_non_streaming_so_the_sdk_wraps_it_with_the_vad(self):
        import local_stt

        caps = local_stt.LocalWhisperSTT(model="test-fake.en").capabilities
        self.assertFalse(caps.streaming)
        self.assertFalse(caps.interim_results)


class TestTheCallRecordHearsBothSides(unittest.TestCase):
    """`heard:` alone was not enough. It showed only the CALLER's side, so a
    report like "he wouldn't hang up" had to be reconstructed from tracebacks
    to work out what the DJ had actually said or tried."""

    def _attach(self):
        from call.lifecycle import attach_heard_logging

        class _Session:
            def __init__(self):
                self.handlers = {}

            def on(self, name, fn):
                self.handlers[name] = fn

        class _Record:
            def __init__(self):
                self.turns = []
                self.tools = []

            def turn(self, who, text):
                self.turns.append((who, text))

            def tool(self, name, result=""):
                self.tools.append((name, result))

        session, record, counter = _Session(), _Record(), {"n": 0}
        attach_heard_logging(session, counter, record)
        return session, record, counter

    def test_the_caller_is_recorded_and_counted(self):
        session, record, counter = self._attach()
        session.handlers["user_input_transcribed"](
            types.SimpleNamespace(transcript="play something loud", is_final=True))
        self.assertEqual(record.turns, [("caller", "play something loud")])
        self.assertEqual(counter["n"], 1)

    def test_a_partial_transcript_is_not_a_turn(self):
        session, record, counter = self._attach()
        session.handlers["user_input_transcribed"](
            types.SimpleNamespace(transcript="play some", is_final=False))
        session.handlers["user_input_transcribed"](
            types.SimpleNamespace(transcript="   ", is_final=True))
        self.assertEqual(record.turns, [])
        self.assertEqual(counter["n"], 0)

    def test_the_dj_is_recorded_too(self):
        session, record, _ = self._attach()
        session.handlers["conversation_item_added"](types.SimpleNamespace(
            item=types.SimpleNamespace(role="assistant", text_content="You're through.")))
        self.assertEqual(record.turns, [("dj", "You're through.")])

    def test_the_callers_own_words_are_not_attributed_to_the_dj(self):
        session, record, _ = self._attach()
        session.handlers["conversation_item_added"](types.SimpleNamespace(
            item=types.SimpleNamespace(role="user", text_content="hello?")))
        self.assertEqual(record.turns, [])

    def test_every_tool_lands_in_the_record_with_its_result(self):
        # The DJ saying it did something is a claim; this line is the receipt.
        session, record, _ = self._attach()
        session.handlers["function_tools_executed"](types.SimpleNamespace(
            function_calls=[types.SimpleNamespace(name="subwave_request_song",
                                                  call_id="c1")],
            function_call_outputs=[types.SimpleNamespace(call_id="c1",
                                                         output="Added to the queue")],
        ))
        self.assertEqual(record.tools, [("subwave_request_song", "Added to the queue")])

    def test_a_tool_with_no_output_is_still_recorded(self):
        session, record, _ = self._attach()
        session.handlers["function_tools_executed"](types.SimpleNamespace(
            function_calls=[types.SimpleNamespace(name="subwave_skip_track",
                                                  call_id="c9")],
            function_call_outputs=[],
        ))
        self.assertEqual(record.tools, [("subwave_skip_track", "")])


class TestWidgetServerContract(unittest.TestCase):
    """The widget is plain browser JS with no toolchain and no test harness of
    its own, so this is what guards it.

    Two ways it has broken before: a route renamed on the server while the
    widget kept calling the old path, and a DOM id changed in index.html while
    the widget kept reaching for the old one. Both leave a green suite and a
    widget that silently does nothing — the exact failure mode this project
    treats as a bug rather than a nitpick.

    Reads every file in web-widget/ rather than one named file, so the split
    into shared.js / call.js / panel.js did not quietly shrink what is covered.
    """

    @classmethod
    def setUpClass(cls):
        import re

        root = Path(__file__).parent.parent
        cls.sources = widget_js()
        cls.js = "\n".join(cls.sources.values())
        cls.html = (root / "web-widget" / "index.html").read_text(encoding="utf-8")
        server = (Path(__file__).parent / "token_server.py").read_text(encoding="utf-8")

        cls.routes = set(re.findall(
            r'router\.add_(?:get|post|put|delete)\(\s*"([^"]+)"', server))
        cls.fetched = set(re.findall(r"""fetch\(\s*['"`](/[^'"`?${]*)""", cls.js))
        cls.wanted_ids = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", cls.js)) | set(
            re.findall(r"getElementById\('([A-Za-z0-9_-]+)'\)", cls.js))
        cls.declared_ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', cls.html))
        # Some elements are built in JS when first needed rather than sitting
        # in the markup (the first-run banner, the password nudge).
        cls.built_ids = set(re.findall(
            r"\.id\s*=\s*['\"]([A-Za-z0-9_-]+)['\"]", cls.js))

    def test_the_scan_found_something_to_check(self):
        # A silently-empty scan would make every assertion below pass forever.
        self.assertGreater(len(self.routes), 10)
        self.assertGreater(len(self.fetched), 10)
        self.assertGreater(len(self.wanted_ids), 50)

    def test_the_page_loads_every_script_the_widget_is_split_into(self):
        # A file that exists but nothing loads is the split's own failure mode:
        # the code is right, the tests above still read it, and the browser
        # never sees it. Only files the call page is meant to load count.
        import re

        loaded = set(re.findall(r'<script src="/([\w.-]+\.js)"', self.html))
        orphans = sorted(set(self.sources) - loaded)
        self.assertEqual(
            orphans, [],
            f"these ship in web-widget/ but index.html loads none of them: {orphans}")

    def test_every_path_the_widget_calls_is_a_route_the_server_serves(self):
        static = {r.rstrip("/") for r in self.routes if "{" not in r}
        prefixes = [r.split("{")[0] for r in self.routes if "{" in r]
        missing = sorted(
            path for path in self.fetched
            if path.rstrip("/") not in static
            and not any(path.startswith(p) for p in prefixes)
        )
        self.assertEqual(
            missing, [],
            "the widget calls paths token_server.py does not serve — it will "
            f"404 with nothing to say so: {missing}",
        )

    def test_every_element_the_widget_reaches_for_exists(self):
        missing = sorted(self.wanted_ids - self.declared_ids - self.built_ids)
        self.assertEqual(
            missing, [],
            "the widget reads element ids that index.html does not declare and "
            f"never creates — those controls are dead: {missing}",
        )

    def test_the_widget_is_still_dependency_free(self):
        # No build step, no bundler, no node_modules. The moment the widget
        # needs one, everything above stops being enough and the deploy story
        # changes. The split into three files is script tags, not modules,
        # precisely so this stays true.
        root = Path(__file__).parent.parent
        self.assertFalse(
            list(root.glob("package.json")) + list((root / "web-widget").glob("package.json")),
            "a package.json appeared — the widget is meant to stay toolchain-free",
        )
        for name, src in self.sources.items():
            with self.subTest(file=name):
                self.assertNotIn("require(", src)
                self.assertNotIn("import ", src.split("//")[0][:200])


class TestTheConductHarnessCannotReachTheRealStation(unittest.TestCase):
    """`scripted_call.py` is run against the LIVE station, deliberately — it is
    the only way to check conduct, and its docstring promises that nothing is
    queued, nothing is announced and no segment runs.

    That promise is kept by one function, `muzzle_the_station()`, which swaps
    each writing StationClient method for a recorder. It is a hand-maintained
    list, and it had already fallen behind: `skip_track` and `dj_segment`
    arrived in 0.9.54, went into the tool registry, and were never added here —
    so with either switched on, a scripted run could cut the record the
    station's listeners were hearing. Deriving the list from station.py means
    the next write method cannot slip through the same gap.
    """

    @classmethod
    def setUpClass(cls):
        import ast
        import re

        here = Path(__file__).parent
        tree = ast.parse((here / "station.py").read_text(encoding="utf-8"))
        client = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.ClassDef) and n.name == "StationClient")

        cls.writes = set()
        for fn in client.body:
            if not isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in {"post", "put", "delete"}):
                    cls.writes.add(fn.name)

        harness = (here / "scripted_call.py").read_text(encoding="utf-8")
        cls.muzzled = set(re.findall(r"StationClient\.(\w+)\s*=", harness))

    def test_the_scan_found_the_writing_methods(self):
        # A regex that quietly matched nothing would make the real assertion
        # below pass forever.
        self.assertGreaterEqual(len(self.writes), 4)
        self.assertIn("dj_say", self.writes)

    def test_every_writing_station_method_is_muzzled(self):
        escaped = sorted(self.writes - self.muzzled)
        self.assertEqual(
            escaped, [],
            "scripted_call.py is documented as safe to run against a live "
            "station, but these StationClient methods write and are not "
            f"swapped for a recorder: {escaped}",
        )

    def test_the_two_station_wide_actions_are_covered_by_name(self):
        # Named explicitly as well as derived: these two reach every listener
        # rather than the caller, so they are the ones that must never regress.
        self.assertIn("skip_track", self.muzzled)
        self.assertIn("dj_segment", self.muzzled)


class TestTheRoutingTableIsInOnePlace(unittest.TestCase):
    """`token_server.py` is a map and nothing else: every handler lives in
    `api/`, and every route is registered in that one block.

    Two things depend on it holding. TestWidgetServerContract reads
    `token_server.py` alone to check that every path the widget fetches is served —
    a route registered inside `api/` would be invisible to it, and the widget
    would 404 with nothing to say so. And a handler nobody routes is the
    failure mode this codebase keeps producing in other forms: the control
    exists, the code is right, and there is no way to reach it.
    """

    @classmethod
    def setUpClass(cls):
        import ast

        here = Path(__file__).parent
        cls.server = (here / "token_server.py").read_text(encoding="utf-8")
        cls.modules = sorted((here / "api").glob("*.py"))
        cls.handlers = {}
        for path in cls.modules:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if (isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
                        and node.name.startswith("handle_")):
                    cls.handlers[node.name] = path.name

    def test_the_scan_found_the_package(self):
        # A scan that quietly matched nothing would make the rest pass forever.
        self.assertGreater(len(self.modules), 8)
        self.assertGreater(len(self.handlers), 20)

    def test_every_handler_in_the_package_is_routed(self):
        orphans = sorted(f"{mod}:{name}" for name, mod in self.handlers.items()
                         if name not in self.server)
        self.assertEqual(
            orphans, [],
            "these handlers exist and nothing serves them — either register "
            f"them in build_app() or delete them: {orphans}")

    def test_no_module_registers_routes_of_its_own(self):
        stray = sorted(p.name for p in self.modules
                       if "router.add" in p.read_text(encoding="utf-8"))
        self.assertEqual(
            stray, [],
            "routes registered outside token_server.py are invisible to the "
            f"widget's contract test: {stray}")

    def test_nothing_in_the_package_imports_the_server(self):
        # An import back the other way is how a split quietly becomes one
        # module in twelve files.
        back = sorted(p.name for p in self.modules
                      if "import token_server" in p.read_text(encoding="utf-8"))
        self.assertEqual(back, [], f"api/ must not depend on its caller: {back}")


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


class TestTheCallRecordSaysWhoRang(_TempStores):
    """The worker writes the transcript and never sees the browser that rang,
    so what we knew at mint time is merged in when /calls is served.

    It is the first question when a call connects and then hears nothing: an
    off-LAN caller with no media path looks identical to a silent one from
    inside the booth. The two halves live in different modules now — the mint
    records it, diagnostics attaches it — which is exactly the join that a
    refactor can drop without any route changing shape.
    """

    def test_what_we_knew_at_mint_time_reaches_the_panel(self):
        import asyncio
        import json

        import admin_auth
        import call.record
        from api import auth as api_auth
        from api import diagnostics as api_diagnostics
        from api import tokens as api_tokens

        api_tokens._mint_info["room-x"] = {
            "client": "Firefox on Windows",
            "network": "off-network",
            "ip": "203.0.113.9",
        }
        # No password anywhere, so the panel gate opens for a request with no
        # Origin — this is testing the merge, not the lock.
        old_auth, admin_auth.AUTH_PATH = admin_auth.AUTH_PATH, Path(self._tmp.name) / "a.json"
        old_key, api_auth.ADMIN_KEY = api_auth.ADMIN_KEY, ""
        real = call.record.recent
        call.record.recent = lambda n: [{"room": "room-x"}, {"room": "room-y"}]
        try:
            resp = asyncio.run(api_diagnostics.handle_calls(_FakeRequest()))
        finally:
            call.record.recent = real
            admin_auth.AUTH_PATH = old_auth
            api_auth.ADMIN_KEY = old_key
            api_tokens._mint_info.pop("room-x", None)

        calls = json.loads(resp.body)["calls"]
        self.assertEqual(calls[0]["caller"]["network"], "off-network")
        self.assertEqual(calls[0]["caller"]["client"], "Firefox on Windows")
        # A call we have no mint record for is left alone rather than given an
        # empty one, so the panel can tell "we don't know" from "same network".
        self.assertNotIn("caller", calls[1])


class TestStaleRecordsCanBeThrownAway(unittest.TestCase):
    """`record_keep` only trims as new calls arrive, so a deployment that has
    gone quiet keeps whatever it last had forever. After a run of test calls
    the panel is mostly conversations the operator has already read — and they
    are a caller's words, so "wait for enough new calls to age them out" is the
    wrong answer to wanting them gone."""

    def setUp(self):
        import call.record as record

        self._tmp = tempfile.TemporaryDirectory()
        self._old = record.CALLS_DIR
        record.CALLS_DIR = Path(self._tmp.name)

    def tearDown(self):
        import call.record as record

        record.CALLS_DIR = self._old
        self._tmp.cleanup()

    def test_clearing_removes_every_record_and_says_how_many(self):
        import call.record as record

        for n in range(3):
            (record.CALLS_DIR / f"2026080{n}-x.json").write_text("{}", encoding="utf-8")
        self.assertEqual(record.clear(), 3)
        self.assertEqual(list(record.CALLS_DIR.glob("*.json")), [])
        self.assertEqual(record.recent(), [])

    def test_clearing_an_empty_store_is_not_an_error(self):
        import call.record as record

        self.assertEqual(record.clear(), 0)

    def test_the_caller_context_goes_with_the_transcripts(self):
        # It lives in memory on the token server rather than in the record, so
        # clearing the files alone would leave the panel able to say which
        # browser and which network rang for a call that no longer exists.
        import asyncio
        import json

        import admin_auth
        from api import auth as api_auth
        from api import diagnostics as api_diagnostics
        from api import tokens as api_tokens

        api_tokens._mint_info["room-gone"] = {"client": "x", "network": "y", "ip": "z"}
        old_auth, admin_auth.AUTH_PATH = admin_auth.AUTH_PATH, Path(self._tmp.name) / "a.json"
        old_key, api_auth.ADMIN_KEY = api_auth.ADMIN_KEY, ""
        try:
            resp = asyncio.run(api_diagnostics.handle_clear_calls(_FakeRequest()))
        finally:
            admin_auth.AUTH_PATH = old_auth
            api_auth.ADMIN_KEY = old_key
            api_tokens._mint_info.pop("room-gone", None)

        self.assertTrue(json.loads(resp.body)["ok"])
        self.assertEqual(api_tokens._mint_info, {})

    def test_clearing_the_log_buffer_empties_the_viewer(self):
        import log_setup

        log_setup.RECENT.clear()
        for n in range(4):
            log_setup.RECENT.append(
                {"t": "12:00:00", "level": "INFO", "logger": "callin.test", "msg": str(n)})
        self.assertEqual(log_setup.clear(), 4)
        self.assertEqual(log_setup.recent_records(), [])


class TestNewCodeDoesNotArriveUntested(unittest.TestCase):
    """A new module with no tests is the way coverage rots — quietly, one file
    at a time, while the suite stays green and says nothing.

    The bar here is deliberately low: every module must be *reached* by the
    suite at all. It does not judge how well. It exists so that adding a file
    is a decision to test it rather than an oversight, and it adapts on its own
    — a module added tomorrow is covered by this rule the moment it lands.
    """

    def test_every_module_is_reached_by_the_suite(self):
        here = Path(__file__).parent
        suite_src = Path(__file__).read_text(encoding="utf-8")

        untested = []
        for path in sorted(here.rglob("*.py")):
            if path.name in ("test_sidecar.py", "__init__.py"):
                continue
            if "__pycache__" in path.parts or ".venv" in path.parts:
                continue
            rel = path.relative_to(here)
            dotted = str(rel.with_suffix("")).replace("\\", "/").replace("/", ".")
            if dotted not in suite_src and path.stem not in suite_src:
                untested.append(str(rel).replace("\\", "/"))

        self.assertEqual(
            untested, [],
            "these modules are never imported or named anywhere in the suite, so "
            "nothing here would notice if they broke. Write a test, or say in the "
            f"test file why they cannot have one: {untested}",
        )


class TestTheWrittenInstructionsStillDescribeTheCode(unittest.TestCase):
    """CLAUDE.md is loaded into every agent's context, so a stale path there is
    worse than no path — it sends the next person (or model) confidently to a
    file that moved. Prose cannot self-heal, but it can be made to fail loudly
    when the tree moves underneath it.

    Only source paths under agent-worker/ and web-widget/ are checked: those are
    tracked, so this holds in CI and inside the image. The long-form design docs
    are gitignored and deliberately not referenced this way.
    """

    def _claude_mds(self):
        root = Path(__file__).parent.parent
        return [p for p in (root / "CLAUDE.md",
                            root / "agent-worker" / "CLAUDE.md",
                            root / "web-widget" / "CLAUDE.md") if p.is_file()]

    def test_every_source_path_they_name_exists(self):
        import re

        docs = self._claude_mds()
        if not docs:
            self.skipTest("no CLAUDE.md in this checkout (not copied into the image)")

        root = Path(__file__).parent.parent
        # Every source filename in the tree, so a doc may name a module the way
        # a person would ("session.py") without spelling out its directory.
        present = {
            p.name
            for d in ("agent-worker", "web-widget")
            for p in (root / d).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        }

        missing = []
        checked = 0
        for doc in docs:
            base = doc.parent
            for ref in re.findall(r"`([A-Za-z0-9_./-]+\.(?:py|js|html|css))`",
                                  doc.read_text(encoding="utf-8")):
                if ref.startswith("/"):
                    continue        # a served route (/call.js), not a path on disk
                # Resolve as a path relative to the doc or the repo root, else
                # as a bare filename anywhere in the source tree.
                if (base / ref).exists() or (root / ref).exists() \
                        or Path(ref).name in present:
                    checked += 1
                    continue
                missing.append(f"{doc.name} -> {ref}")

        self.assertGreater(checked, 10, "found almost no paths to check — the "
                                        "scan regex has probably stopped matching")
        self.assertEqual(missing, [],
                         f"CLAUDE.md names source files that do not exist: {missing}")


class TestEverySkillWouldActuallyLoad(unittest.TestCase):
    """A skill with broken frontmatter does not error — it is simply never
    offered, which looks identical to the model choosing not to use it. That is
    the worst failure mode available: silent, and indistinguishable from
    working. Adapts on its own; a skill added tomorrow is checked by this.
    """

    def _skills(self):
        d = Path(__file__).parent.parent / ".claude" / "skills"
        return sorted(d.glob("*/SKILL.md")) if d.is_dir() else []

    def test_frontmatter_is_present_and_names_match_their_directories(self):
        import re

        skills = self._skills()
        if not skills:
            self.skipTest(".claude/skills not in this checkout (not copied into the image)")

        problems = []
        for skill in skills:
            text = skill.read_text(encoding="utf-8")
            if not text.startswith("---"):
                problems.append(f"{skill.parent.name}: no frontmatter block")
                continue
            name = re.search(r"^name:\s*(.+)$", text, re.M)
            desc = re.search(r"^description:\s*(.+)$", text, re.M)
            if not name or name.group(1).strip() != skill.parent.name:
                problems.append(
                    f"{skill.parent.name}: name field is "
                    f"{name.group(1).strip() if name else 'missing'}, which does not "
                    "match the directory, so the skill cannot be invoked by name")
            if not desc or len(desc.group(1).strip()) < 40:
                problems.append(
                    f"{skill.parent.name}: description missing or too short to "
                    "trigger on — it is the only part always in context")

        self.assertEqual(problems, [], f"skills that would not load correctly: {problems}")


class TestTheCommitGateIsStillWiredUp(unittest.TestCase):
    """A malformed .claude/settings.json does not raise — it silently disables
    every setting in that file, the pre-commit gate included. The failure looks
    exactly like a gate that decided everything was fine, which is the worst
    kind available."""

    def _settings(self):
        return Path(__file__).parent.parent / ".claude" / "settings.json"

    def test_it_is_valid_json_and_still_guards_commits(self):
        path = self._settings()
        if not path.is_file():
            self.skipTest(".claude/ not in this checkout (not copied into the image)")

        data = json.loads(path.read_text(encoding="utf-8"))   # raises if malformed
        commands = [
            hook.get("command", "")
            for entry in data.get("hooks", {}).get("PreToolUse", [])
            for hook in entry.get("hooks", [])
        ]
        gate = [c for c in commands if "test_sidecar" in c]
        self.assertTrue(
            gate, "no PreToolUse hook runs test_sidecar any more — commits are "
                  "no longer gated on the suite")

        # It must filter on its own stdin: the `if` field alone does not
        # restrict, so without this the gate runs on every single Bash call.
        self.assertIn("git commit", gate[0],
                      "the hook does not check that the command is a commit")


class TestNoFileGrowsWithoutSomebodyDeciding(unittest.TestCase):
    """Nothing in this repo has ever objected to a file getting longer, and it
    shows: app.js reached 3,354 lines and this suite 5,791, one reasonable
    commit at a time. No single one of those commits was wrong. That is the
    whole problem — a file only becomes unreadable in increments small enough
    that nobody stops.

    So the rule is not "files must be short". It is "a file above the ceiling
    must be a decision somebody wrote down". Everything below the ceiling is
    unaffected and always will be.

    Two kinds of decision, deliberately kept apart, because treating them the
    same produced friction with nothing on the other end of it:

    EXEMPT is "this file is meant to be long, and that is the right answer".
    A declaration table is the clear case: settings.py grows by a few lines
    every time the station gains a setting, and making that ordinary act come
    and edit a number in this file would be ceremony no reader ever benefits
    from. Exempt files are not measured, only justified.

    SPLITTING is debt: too long, known, and going to be dealt with. Those are
    ratcheted — the recorded number is the size when the entry was written, and
    the file may shrink freely but never grow past it. An entry whose file has
    come back under the ceiling must be deleted, so the list cannot drift into
    describing a problem that no longer exists.

    The distinction matters in the other direction too. A ceiling that only
    ever means "apologise" pushes toward splitting files to satisfy the number
    rather than to help a reader, which is how you end up with a file per
    function. Being long has to stay a legitimate permanent answer.
    """

    CEILING = 600

    # Long on purpose. Not measured — only required to still exist and still
    # say why.
    EXEMPT = {
        "agent-worker/settings.py":
            "mostly DEFAULTS and GROUPS — a declaration table, not logic. Long "
            "because the station has a lot of settings, and reading it top to "
            "bottom is how you find one. It is supposed to grow.",
        "agent-worker/api/diagnostics.py":
            "one module per job, and /test/* is genuinely one job: eight probes "
            "that all answer 'can this box reach that thing'. Splitting them "
            "would scatter one answer across eight files.",
    }

    # path -> (lines when the entry was written, what it is waiting to become).
    # Shrinking is always fine and the number should be lowered when it happens.
    # Growing past the recorded size means: split it, or raise the number in the
    # same commit and say in the message what made that the right call.
    SPLITTING = {
        "agent-worker/test_sidecar.py": (
            6169, "the whole suite in one file, appended to chronologically "
                  "since the first commit. Being split by subject into tests/."),
        "web-widget/call.js": (
            1107, "the call surface, out of the old app.js. Still above the "
                  "ceiling: the captions, meters and LiveKit wiring each want "
                  "their own file. Next after the panel."),
        "web-widget/panel.js": (
            2105, "the operator surface, out of the old app.js. Settings form, "
                  "the /test/* probes, uploads and the log and call viewers are "
                  "four separable jobs sharing one file. Being split."),
        "web-widget/style.css": (
            1094, "themes both surfaces. Splits when the panel gets its own "
                  "page and can take its own stylesheet with it."),
        "web-widget/index.html": (
            755, "the call page and the panel in one document. The panel moves "
                 "to its own page next."),
    }

    # Where shipped code lives. tools/ is developer scaffolding and docs are
    # prose, so neither is held to a source-file ceiling.
    ROOTS = ("agent-worker", "web-widget")
    SUFFIXES = (".py", ".js", ".css", ".html")

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).parent.parent
        cls.root = root
        cls.sizes = {}
        for name in cls.ROOTS:
            for path in sorted((root / name).rglob("*")):
                if not path.is_file() or path.suffix not in cls.SUFFIXES:
                    continue
                if "__pycache__" in path.parts or ".venv" in path.parts:
                    continue
                rel = str(path.relative_to(root)).replace("\\", "/")
                cls.sizes[rel] = len(
                    path.read_text(encoding="utf-8", errors="replace").splitlines())

    def test_the_scan_found_the_source_tree(self):
        # A scan that quietly matched nothing would make every check below pass
        # forever, which is the failure mode this suite keeps guarding against.
        self.assertGreater(len(self.sizes), 40,
                           "the file scan has stopped finding the source tree")

    def test_nothing_is_over_the_ceiling_without_a_decision(self):
        decided = set(self.EXEMPT) | set(self.SPLITTING)
        over = sorted(
            f"{path} ({n} lines)"
            for path, n in self.sizes.items()
            if n > self.CEILING and path not in decided
        )
        self.assertEqual(
            over, [],
            f"these are over the {self.CEILING}-line ceiling and nobody decided "
            "that was right. Split them; or add them to SPLITTING if that is "
            "coming, or to EXEMPT if being this long is the correct answer: "
            f"{over}")

    def test_nothing_is_being_split_and_exempt_at_once(self):
        # The two lists mean opposite things — "this is debt" and "this is
        # right". A file in both says nobody decided which.
        both = sorted(set(self.EXEMPT) & set(self.SPLITTING))
        self.assertEqual(both, [], f"listed as both debt and deliberate: {both}")

    def test_no_file_being_split_has_grown(self):
        grown = sorted(
            f"{path} was {was}, is now {self.sizes[path]}"
            for path, (was, _) in self.SPLITTING.items()
            if path in self.sizes and self.sizes[path] > was
        )
        self.assertEqual(
            grown, [],
            "these are on the list because they are too long and being dealt "
            "with, so the recorded size is a ceiling of its own. Shrink them, "
            "or raise the number in the same commit and say why that was the "
            f"right call: {grown}")

    def test_no_entry_outlives_the_thing_it_describes(self):
        # Three ways an entry goes stale: the file is gone, or it has come back
        # under the ceiling, or (for EXEMPT) it was never over it. Any of them
        # and the list has started describing a repo that isn't this one.
        stale = sorted(
            path for path in (set(self.EXEMPT) | set(self.SPLITTING))
            if path not in self.sizes or self.sizes[path] <= self.CEILING
        )
        self.assertEqual(
            stale, [],
            "these entries no longer describe anything — the file is gone or is "
            f"back under the ceiling. Delete them: {stale}")

    def test_every_entry_says_why(self):
        # An entry with no reason is indistinguishable from one added to make
        # the suite go green, which is precisely what this must not become.
        reasons = dict(self.EXEMPT)
        reasons.update({p: why for p, (_, why) in self.SPLITTING.items()})
        thin = sorted(path for path, why in reasons.items()
                      if len(why.strip()) < 40)
        self.assertEqual(
            thin, [],
            f"entries must say why the size is what it is, not merely that it "
            f"is: {thin}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
