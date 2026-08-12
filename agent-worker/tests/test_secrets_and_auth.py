"""API keys and the two passwords. Everything here is about something not leaving, or somebody not getting in.

Split out of test_sidecar.py; see tests/__init__.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
import secrets_store
import settings as settings_store
from tests.support import _TempStores


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
    # will actually go out on the wire rather than what we passed in. Every
    # provider with a stored key is here — 0.9.122 added four and this list
    # not growing with them would have left their withhold-path unchecked.
    KEY_ENV = {
        "openai": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "google": "GOOGLE_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "requesty": "REQUESTY_API_KEY",
        "gateway": "AI_GATEWAY_API_KEY",
    }

    # The two aggregators refuse to build without a model on purpose (their
    # catalogues are namespaced and move, so there is no honest default) —
    # the key tests need one that looks like theirs.
    MODEL_FOR = {"requesty": "openai/gpt-4.1-mini", "gateway": "openai/gpt-4.1-mini"}

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
                    {"llm_provider": provider,
                     "llm_model": self.MODEL_FOR.get(provider, ""),
                     "llm_base_url": "http://attacker.example/v1"},
                    use_stored_key=False,
                )
                self.assertEqual(self._key_on(model), WITHHELD_KEY)

    def test_the_normal_path_still_uses_the_stored_key(self):
        from call.providers import build_llm

        for provider, env_var in self.KEY_ENV.items():
            with self.subTest(provider=provider):
                os.environ[env_var] = f"{provider}-the-real-one"
                model = build_llm({"llm_provider": provider,
                                   "llm_model": self.MODEL_FOR.get(provider, "")})
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
        import settings as settings_store
        from api import auth as api_auth

        # On a SET-UP line: since 0.10.78 an unconfigured deployment refuses
        # every door outright, so the open-until-a-code-exists behaviour
        # starts once the admin password does — and it is `auto` mode's
        # behaviour, which stopped being the default at 0.10.80 (fresh
        # installs are admin-only until opened).
        self.auth.set_password("admin password here")
        settings_store.save({"front_access": "auto"})
        # This class is not _TempStores, so put the mode back for the
        # module's other tests (blank pops the override).
        self.addCleanup(lambda: settings_store.save({"front_access": ""}))
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

        self.auth.set_password("admin password here")
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

        old_embed = os.environ.get("CALLIN_ALLOWED_ORIGINS")
        old_panel = api_wire.PANEL_ORIGINS
        os.environ["CALLIN_ALLOWED_ORIGINS"] = "https://someone-elses-blog.example"
        api_wire.PANEL_ORIGINS = []
        try:
            self.assertFalse(
                self._allowed("https://someone-elses-blog.example",
                              "someone-elses-blog.example"))
        finally:
            if old_embed is None:
                os.environ.pop("CALLIN_ALLOWED_ORIGINS", None)
            else:
                os.environ["CALLIN_ALLOWED_ORIGINS"] = old_embed
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
        # Since 0.10.80 a FRESH install defaults to admin-only — a line
        # starts closed and is opened as a decision — while a store from
        # before the change is stamped `auto` by _migrate, so a line that
        # took calls yesterday still takes them after an upgrade
        # (TestAnUpgradeClosesNoDoorAndHandsOutNoPower holds that half).
        # `auto` itself keeps its meaning: open until a code exists.
        import admin_auth

        self.assertEqual(settings_store.load()["front_access"], "admin")
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
        # The property worth having, widened at 0.10.78 (operator's ask):
        # until an admin password exists NO mode opens — including an
        # explicit "open". A line whose panel anyone can claim must not also
        # be a line anyone can ring; the stranger who could call could first
        # walk into /settings and own the deployment.
        import admin_auth
        from api import auth as api_auth

        # Isolated rather than relying on store state: this asserts the
        # no-password-configured branch specifically.
        real_admin, real_guest = api_auth._auth_configured, admin_auth.guest_is_set
        api_auth._auth_configured = lambda: False
        admin_auth.guest_is_set = lambda: False
        try:
            for mode in ("open", "auto", "guest", "admin"):
                with self.subTest(mode=mode):
                    reason = self._check(mode)
                    self.assertIsNotNone(reason, "an unset gate fell open")
                    self.assertIn("isn't set up yet", reason)
        finally:
            api_auth._auth_configured = real_admin
            admin_auth.guest_is_set = real_guest
