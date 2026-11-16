"""API keys and the two passwords. Everything here is about something not leaving, or somebody not getting in.

Split out of test_sidecar.py; see tests/__init__.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import types
import unittest
from pathlib import Path
import secrets_store
import settings as settings_store
from tests.support import AGENT_WORKER, _TempStores


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

    def _voice_headers(self, allow_stored):
        """The headers the voice lookup actually puts on the wire."""
        import tts_adapter
        seen, ok = {}, types.SimpleNamespace(
            status_code=200, raise_for_status=lambda: None, json=lambda: ["D1"])

        class _Client:
            def __init__(self, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url, headers=None):
                seen.update(headers or {})
                return ok

        real, tts_adapter.httpx.AsyncClient = tts_adapter.httpx.AsyncClient, _Client
        try:
            asyncio.run(tts_adapter.available_voices(
                "http://typed-into-the-panel.example", allow_stored=allow_stored,
                adapter_path=tts_adapter.ADAPTER_DIR / "openai-cloud.json"))
        finally:
            tts_adapter.httpx.AsyncClient = real
        return seen

    def test_a_previewed_tts_host_never_sees_the_stored_key(self):
        # The panel previews a URL before saving it, and ?tts_base_url= rode
        # all the way into the voice lookup with the stored TTS key attached —
        # so a requester could name any host and read the key back off it. The
        # Test button beside that same box had obeyed the rule all along.
        os.environ["TTS_API_KEY"] = "tts-must-not-travel"
        self.assertEqual(self._voice_headers(allow_stored=False), {})
        self.assertIn("tts-must-not-travel", str(self._voice_headers(True)))

    def test_both_voice_lookups_decide_whether_the_key_may_travel(self):
        # Two call sites, and a third that just takes the default would be
        # this leak again, silently. Each has to say which it means.
        for mod in ("settings.py", "diagnostics.py"):
            src = (AGENT_WORKER / "api" / mod).read_text(encoding="utf-8")
            self.assertIn("allow_stored=", src.split("tts_voice_list(")[1][:400],
                          mod)

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

    def _as_a_share_that_hands_out_no_mode(self, write):
        """Run a writer the way a Synology share treats it: every new file
        arrives with mode 000 unless the writer sets one itself."""
        old = os.umask(0o777)
        try:
            write()
        finally:
            os.umask(old)

    def test_the_webhook_secret_stays_owner_only(self):
        # hook-secret.json IS the credential the receiver checks a push
        # against, and it was written 0644 — readable by every account on the
        # box — beside two stores that were 0600 (review, 2026-11-15).
        from api import hook_receiver

        hook_receiver._store_hook_secret("Bearer abc")
        self.assertEqual(
            hook_receiver._secret_path().stat().st_mode & 0o777, 0o600)

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_a_secret_already_on_disk_is_tightened_when_it_is_read(self):
        # The write-time mode never reaches a deployment whose secret was
        # minted before the fix: it is written once and kept. The operator's
        # own file was still 0644 on 1.0.1 (2026-11-16), so reading it is
        # what closes it.
        from api import hook_receiver

        path = hook_receiver._secret_path()
        path.write_text('{"authHeader": "Bearer old"}', encoding="utf-8")
        os.chmod(path, 0o644)
        self.assertEqual(hook_receiver._load_hook_secret(), "Bearer old")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_the_read_tightens_and_nothing_else(self):
        # Runs everywhere, Windows included: the tightening is in the read
        # path, guarded so a platform without the bits is left alone.
        src = (AGENT_WORKER / "api" / "hook_receiver.py").read_text(encoding="utf-8")
        load = src.split("def _load_hook_secret() -> str:")[1].split("\ndef ")[0]
        self.assertIn("_keep_the_secret_owner_only(path)", load)
        keep = src.split("def _keep_the_secret_owner_only(path: Path) -> None:")[1].split("\ndef ")[0]
        self.assertIn('os.name != "nt"', keep)
        self.assertIn("os.chmod(path, 0o600)", keep)

    def test_the_listener_history_is_readable_by_its_owner(self):
        from api import stats

        self._as_a_share_that_hands_out_no_mode(
            lambda: stats.record_sample(3, now=1000.0))
        mode = stats.LISTENERS_PATH.stat().st_mode & 0o777
        self.assertTrue(mode & 0o400, f"listeners.json came out {mode:03o}")
        self.assertTrue(mode & 0o200, f"listeners.json is not writable: {mode:03o}")

    def test_the_sound_categories_are_readable_by_their_owner(self):
        from unittest import mock

        from api import sounds as api_sounds

        path = Path(self._tmp.name) / "sound-meta.json"
        with mock.patch.object(api_sounds, "META_PATH", path):
            self._as_a_share_that_hands_out_no_mode(
                lambda: api_sounds._save_sound_meta({"ring.wav": {"category": "x"}}))
        mode = path.stat().st_mode & 0o777
        self.assertTrue(mode & 0o400, f"sound-meta.json came out {mode:03o}")


class _AuthGates(_TempStores):
    """A password store in a temp dir, an empty lockout table, an empty
    verdict cache, and no break-glass key unless the test sets one."""

    class _Req(dict):
        """Enough of a request for the gates: headers, a peer, a body, and the
        dict slot a gate leaves its caller-facing reason on."""

        def __init__(self, key="", ip="203.0.113.9", body=None):
            super().__init__()
            self.headers = {"X-Admin-Key": key} if key else {}
            self.remote = ip
            self.host = "box.local"
            self._body = body or {}

        async def json(self):
            return self._body

    IP = "203.0.113.9"

    def setUp(self):
        super().setUp()
        import admin_auth
        from api import auth as api_auth

        self.admin_auth, self.auth = admin_auth, api_auth
        self._old_auth_path = admin_auth.AUTH_PATH
        admin_auth.AUTH_PATH = Path(self._tmp.name) / "admin-auth.json"
        self._old_admin_key = api_auth.ADMIN_KEY
        api_auth.ADMIN_KEY = ""
        api_auth._auth_state.clear()
        admin_auth.forget_verified()
        # How MANY rounds a hash takes is not what any of these classes is
        # about, and at the real figure each stored password costs a tenth of
        # a second or more — these tests set dozens. The record carries its
        # own iteration count, so a cheap one verifies exactly the same way.
        self._old_iterations = admin_auth._ITERATIONS
        admin_auth._ITERATIONS = 1_000

    def tearDown(self):
        self.auth._auth_state.clear()
        self.auth.ADMIN_KEY = self._old_admin_key
        self.admin_auth.AUTH_PATH = self._old_auth_path
        self.admin_auth._ITERATIONS = self._old_iterations
        self.admin_auth.forget_verified()
        super().tearDown()

    def _ban(self, bucket):
        """Two full rounds of wrong passwords, the way an address earns it."""
        for _ in range(self.auth._AUTH_MAX_FAILS):
            self.auth._auth_fail(bucket)
        self.auth._auth_state[bucket]["cooldown_until"] = 0
        for _ in range(self.auth._AUTH_MAX_FAILS):
            self.auth._auth_fail(bucket)
        self.assertIn("blocked", self.auth._auth_gate(bucket) or "")


class TestTheBreakGlassKeyGetsPastALockout(_AuthGates):
    """`CALLIN_ADMIN_KEY` is the recovery for a locked-out address, and it did
    not work from one.

    admin_auth.py, the comment over the lockout and docs/security.md all said
    the environment key is "always accepted". Every gate returned the lockout
    reason BEFORE looking at the key, so the one address that needed the
    recovery was the one address refused it — and the only test of the key
    (test_the_break_glass_key_opens_them_again) never ran under a ban, so the
    promise was never once checked where it mattered (review, 2026-11-15).

    The other half is as load-bearing: ONLY the environment key passes. A
    stored password that walked through a ban would make the ban decorative.
    """

    def setUp(self):
        super().setUp()
        self.admin_auth.set_password("the-real-password")
        self.auth.ADMIN_KEY = "break-glass-key"

    def test_the_panel_opens_to_it_from_a_banned_address(self):
        self._ban(self.IP)
        self.assertTrue(self.auth._check_admin(self._Req("break-glass-key")))

    def test_the_stored_password_is_still_shut_out(self):
        self._ban(self.IP)
        req = self._Req("the-real-password")
        self.assertFalse(
            self.auth._check_admin(req),
            "a stored password walked through a ban — the lockout protects "
            "nothing if the thing being guessed is exempt from it")
        self.assertIn("blocked", req["auth_error"])

    def test_using_it_does_not_lift_the_ban(self):
        # Behind a proxy nobody named, the operator and the guesser share an
        # address. A break-glass request that cleared the record would hand
        # the guesser a fresh ten tries every time the operator's panel polled.
        self._ban(self.IP)
        self.assertTrue(self.auth._check_admin(self._Req("break-glass-key")))
        self.assertIn("blocked", self.auth._auth_gate(self.IP))
        self.assertFalse(self.auth._check_admin(self._Req("the-real-password")))

    def test_it_works_through_a_cooldown_too(self):
        for _ in range(self.auth._AUTH_MAX_FAILS):
            self.auth._auth_fail(self.IP)
        self.assertIn("try again", self.auth._auth_gate(self.IP))
        self.assertTrue(self.auth._check_admin(self._Req("break-glass-key")))
        self.assertFalse(self.auth._check_admin(self._Req("the-real-password")))
        self.assertIn("try again", self.auth._auth_gate(self.IP))

    def test_a_near_miss_is_refused_like_anything_else(self):
        self._ban(self.IP)
        for wrong in ("break-glass-ke", "break-glass-key ", "BREAK-GLASS-KEY", ""):
            with self.subTest(key=wrong):
                self.assertFalse(self.auth._check_admin(self._Req(wrong)))

    def test_a_key_that_is_not_ascii_is_wrong_not_a_crash(self):
        # compare_digest on two strs raises for a non-ASCII character, and a
        # header can carry one — that was a 500 from the login prompt.
        self.assertFalse(self.auth._check_admin(self._Req("brèak-gläss-kéy")))
        self.auth.ADMIN_KEY = "clé-de-secours"
        self.assertTrue(self.auth._check_admin(self._Req("clé-de-secours")))

    def test_no_environment_key_means_nothing_passes_the_gate(self):
        self.auth.ADMIN_KEY = ""
        self._ban(self.IP)
        for key in ("", "break-glass-key", "the-real-password"):
            with self.subTest(key=key):
                self.assertFalse(self.auth._break_glass(key))
                self.assertFalse(self.auth._check_admin(self._Req(key)))

    def _change_password(self, current):
        resp = asyncio.run(self.auth.handle_set_password(self._Req(
            body={"current": current, "new": "a-brand-new-password"})))
        return resp.status

    def test_the_password_can_be_reset_with_it_from_a_banned_address(self):
        # The recovery it exists for: forgot the password, fumbled it into a
        # ban, and the environment key is the way to set a new one.
        self._ban(self.IP)
        self.assertEqual(self._change_password("the-real-password"), 401)
        self.assertTrue(self.admin_auth.verify("the-real-password"))
        self.assertEqual(self._change_password("break-glass-key"), 200)
        self.assertTrue(self.admin_auth.verify("a-brand-new-password"))

    def test_the_phone_rings_for_it_from_a_locked_out_address(self):
        self.admin_auth.set_guest_password("door-code")
        settings_store.save({"front_access": "guest"})
        self._ban("guest:" + self.IP)
        for stored in ("door-code", "the-real-password"):
            with self.subTest(key=stored):
                self.assertIn("blocked",
                              self.auth._guest_check(stored, self.IP) or "")
        self.assertIsNone(self.auth._guest_check("break-glass-key", self.IP))
        self.assertIn("blocked", self.auth._auth_gate("guest:" + self.IP))


class TestAVerifiedKeyIsHashedOnce(_AuthGates):
    """One PBKDF2 per question, not one per request.

    Every keyed request — the panel's own fetches, a signed-in caller's
    20-second /live poll — ran 240,000 iterations on the event loop, about a
    tenth of a second on a NAS CPU, and /live is public: anyone could repeat a
    junk X-Call-Key and hold the single-threaded server still for each one
    (review, 2026-11-15). A verdict is now remembered briefly.

    Most of this class is about what the memory must NOT do: keep a password,
    outlive a password change, or let a flood of wrong keys evict a right one.
    """

    def setUp(self):
        super().setUp()
        from unittest import mock

        patcher = mock.patch.object(
            self.admin_auth, "_derive", wraps=self.admin_auth._derive)
        self.derive = patcher.start()
        self.addCleanup(patcher.stop)
        self.admin_auth.set_password("the-real-password")
        self.admin_auth.forget_verified()
        self.derive.reset_mock()

    def test_the_right_password_costs_one_hash(self):
        for _ in range(6):
            self.assertTrue(self.admin_auth.verify("the-real-password"))
        self.assertEqual(self.derive.call_count, 1)

    def test_a_repeated_wrong_key_costs_nothing_more(self):
        for _ in range(6):
            self.assertFalse(self.admin_auth.verify("junk-key"))
        self.assertEqual(self.derive.call_count, 1)

    def test_the_public_tier_lookup_stops_hashing_a_repeated_junk_key(self):
        # /live asks tier_for_key on every poll, of whatever X-Call-Key came.
        self.admin_auth.set_guest_password("door-code")
        self.admin_auth.forget_verified()
        self.derive.reset_mock()
        self.assertEqual(self.auth.tier_for_key("junk-key"), "open")
        first = self.derive.call_count
        self.assertGreaterEqual(first, 1)
        for _ in range(10):
            self.assertEqual(self.auth.tier_for_key("junk-key"), "open")
        self.assertEqual(self.derive.call_count, first)
        # And the tiers themselves are what they were.
        self.assertEqual(self.auth.tier_for_key("the-real-password"), "admin")
        self.assertEqual(self.auth.tier_for_key("the-real-password"), "admin")

    def test_a_hit_does_not_outlive_a_password_change(self):
        self.assertTrue(self.admin_auth.verify("the-real-password"))
        self.admin_auth.set_password("a-second-password")
        self.assertFalse(
            self.admin_auth.verify("the-real-password"),
            "the OLD password was still accepted after it was changed")
        self.assertTrue(self.admin_auth.verify("a-second-password"))

    def test_a_refusal_does_not_outlive_a_password_change_either(self):
        # The other direction: a password that was wrong a minute ago and has
        # just been SET must not be refused out of memory.
        self.assertFalse(self.admin_auth.verify("a-second-password"))
        self.admin_auth.set_password("a-second-password")
        self.assertTrue(self.admin_auth.verify("a-second-password"))

    def test_a_hit_does_not_outlive_the_file_changing_behind_our_back(self):
        # No writer of ours ran, so nothing called forget_verified(): the
        # verdict is bound to the stored record, and the record is new.
        self.assertTrue(self.admin_auth.verify("the-real-password"))
        self.admin_auth.AUTH_PATH.write_text(
            json.dumps(self.admin_auth._hash("set-by-another-process")),
            encoding="utf-8")
        self.assertFalse(self.admin_auth.verify("the-real-password"))
        self.assertTrue(self.admin_auth.verify("set-by-another-process"))
        self.admin_auth.AUTH_PATH.unlink()
        self.assertFalse(self.admin_auth.verify("set-by-another-process"))

    def test_every_writer_forgets_what_was_remembered(self):
        writers = (
            lambda: self.admin_auth.set_guest_password("door-code"),
            lambda: self.admin_auth.clear_guest_password(),
            lambda: self.admin_auth.set_password("a-second-password"),
        )
        for write in writers:
            self.admin_auth.verify("the-real-password")
            self.admin_auth.verify("junk-key")
            write()
            self.assertEqual(
                (self.admin_auth._verified, self.admin_auth._refuted), ({}, {}))

    def test_a_cleared_guest_code_stops_working_at_once(self):
        self.admin_auth.set_guest_password("door-code")
        self.assertTrue(self.admin_auth.verify_guest("door-code"))
        self.admin_auth.clear_guest_password()
        self.assertFalse(self.admin_auth.verify_guest("door-code"))

    def test_the_password_itself_is_never_kept(self):
        self.admin_auth.verify("the-real-password")
        self.admin_auth.verify("junk-key")
        kept = list(self.admin_auth._verified) + list(self.admin_auth._refuted)
        self.assertTrue(kept)
        for key in kept:
            self.assertIsInstance(key, bytes)
            self.assertEqual(len(key), 32)
            for secret in (b"the-real-password", b"junk-key"):
                self.assertNotIn(secret, key)
        # Keyed under a per-process secret, so the entry is not a plain hash
        # of the password that could be tested offline against a wordlist.
        import hashlib

        self.assertNotIn(hashlib.sha256(b"the-real-password").digest(), kept)

    def test_a_verdict_is_forgotten_when_its_time_is_up(self):
        self.assertTrue(self.admin_auth.verify("the-real-password"))
        for key in self.admin_auth._verified:
            self.admin_auth._verified[key] = 0.0
        self.assertTrue(self.admin_auth.verify("the-real-password"))
        self.assertEqual(self.derive.call_count, 2)

    def test_a_flood_of_junk_cannot_push_a_real_verdict_out(self):
        self.assertTrue(self.admin_auth.verify("the-real-password"))
        self.derive.reset_mock()
        # From here a real derivation never matches, so the only way the
        # right password can still read True is the remembered verdict.
        self.derive.side_effect = lambda *a, **k: "00"
        for i in range(self.admin_auth._REFUTED_MAX + 60):
            self.assertFalse(self.admin_auth.verify(f"junk-{i}"))
        self.assertLessEqual(len(self.admin_auth._refuted),
                             self.admin_auth._REFUTED_MAX)
        self.assertTrue(self.admin_auth.verify("the-real-password"))

    def test_the_tables_are_bounded(self):
        self.derive.side_effect = lambda *a, **k: "00"
        for i in range(self.admin_auth._REFUTED_MAX * 2):
            self.admin_auth.verify(f"junk-{i}")
        self.assertEqual(len(self.admin_auth._refuted),
                         self.admin_auth._REFUTED_MAX)
        self.assertLessEqual(self.admin_auth._VERIFIED_MAX, 256)

    def test_the_break_glass_key_is_never_remembered(self):
        # It is compared in api/auth.py and never reaches the cache — so
        # taking it out of the environment takes it away at once.
        self.auth.ADMIN_KEY = "break-glass-key"
        self.admin_auth.forget_verified()
        self.assertTrue(self.auth._key_valid("break-glass-key"))
        self.assertEqual(self.admin_auth._verified, {})
        self.auth.ADMIN_KEY = ""
        self.assertFalse(self.auth._key_valid("break-glass-key"))

    def test_the_lockout_still_counts_every_wrong_attempt(self):
        # The cache answers the hash, not the door: the same wrong password
        # five times is five strikes, exactly as before.
        said = []
        for _ in range(self.auth._AUTH_MAX_FAILS):
            req = self._Req("the-same-wrong-one")
            self.assertFalse(self.auth._check_admin(req))
            said.append(req["auth_error"])
        self.assertIn("4 tries left", said[0])
        self.assertIn("too many failed attempts", said[-1])
        self.assertEqual(self.derive.call_count, 1)


class TestLockoutRecordsAgeOutAndTheTableIsBounded(_AuthGates):
    """The lockout table kept one entry per failing address for the life of
    the process, and a strike never expired.

    Nothing but a successful login removed a record, so a scan from many
    addresses grew memory without limit, and a cooldown earned months ago
    still counted as the first of the two rounds that ban (review,
    2026-11-15). Records now age out a day after the LAST failure and the
    table has a ceiling — and the tests that matter most are the ones showing
    neither makes guessing cheaper inside the day.
    """

    def _round(self, bucket):
        said = ""
        for _ in range(self.auth._AUTH_MAX_FAILS):
            said = self.auth._auth_fail(bucket)
        return said

    def _age(self, bucket, secs):
        st = self.auth._auth_state[bucket]
        st["last"] -= secs
        st["cooldown_until"] = 0

    def test_a_strike_is_forgotten_a_day_after_the_last_failure(self):
        self.assertIn("try again", self._round(self.IP))
        self._age(self.IP, self.auth._AUTH_FORGET_SECS + 1)
        self.assertIsNone(self.auth._auth_gate(self.IP))
        self.assertNotIn(self.IP, self.auth._auth_state)
        # So the next round is a FIRST round: a cooldown, not a ban.
        self.assertIn("try again", self._round(self.IP))
        self.assertFalse(self.auth._auth_state[self.IP].get("banned"))

    def test_inside_the_day_nothing_is_forgiven(self):
        self.assertIn("try again", self._round(self.IP))
        self._age(self.IP, self.auth._AUTH_FORGET_SECS - 60)
        self.assertIn("blocked until the app restarts", self._round(self.IP))

    def test_every_failure_restarts_the_clock(self):
        # Measured from the LAST failure, so one guess every 23 hours never
        # ages a record out from under itself.
        self.auth._auth_fail(self.IP)
        self._age(self.IP, self.auth._AUTH_FORGET_SECS - 60)
        self.auth._auth_fail(self.IP)
        st = self.auth._auth_state[self.IP]
        self.assertEqual(st["fails"], 2)
        self.assertLess(time.time() - st["last"], 5)

    def test_a_ban_does_not_age_out(self):
        self._ban(self.IP)
        self.auth._auth_state[self.IP]["last"] -= self.auth._AUTH_FORGET_SECS * 30
        self.assertIn("blocked", self.auth._auth_gate(self.IP))

    def test_a_scan_from_many_addresses_cannot_grow_the_table(self):
        from unittest import mock

        with mock.patch.object(self.auth, "_AUTH_TABLE_MAX", 40):
            for i in range(400):
                self.auth._auth_fail(f"198.51.100.{i}")
            self.assertEqual(len(self.auth._auth_state), 40)
        self.assertLessEqual(self.auth._AUTH_TABLE_MAX, 10_000)

    def test_a_full_table_drops_the_stale_then_the_oldest_and_a_ban_last(self):
        from unittest import mock

        now = time.time()
        self.auth._auth_state.update({
            "banned": {"fails": 0, "strikes": 2, "banned": True, "last": now - 900},
            "stale": {"fails": 1, "strikes": 0,
                      "last": now - self.auth._AUTH_FORGET_SECS - 5},
            "older": {"fails": 2, "strikes": 0, "last": now - 600},
            "newer": {"fails": 3, "strikes": 1, "last": now - 10},
        })
        with mock.patch.object(self.auth, "_AUTH_TABLE_MAX", 4):
            self.auth._auth_fail("first-newcomer")
            self.assertNotIn("stale", self.auth._auth_state)
            self.assertEqual(len(self.auth._auth_state), 4)
            self.auth._auth_fail("second-newcomer")
            self.assertNotIn("older", self.auth._auth_state)
            # The address with a strike against it kept its strike...
            self.assertEqual(self.auth._auth_state["newer"]["strikes"], 1)
            # ...and the ban outlived everything that was not one.
            self.assertIn("banned", self.auth._auth_state)

    def test_an_address_already_in_the_table_never_evicts_anyone(self):
        from unittest import mock

        with mock.patch.object(self.auth, "_AUTH_TABLE_MAX", 3):
            for name in ("a", "b", "c"):
                self.auth._auth_fail(name)
            self.auth._auth_fail("a")
            self.assertEqual(sorted(self.auth._auth_state), ["a", "b", "c"])


class TestEveryDataWriterGoesThroughTheStoreIdiom(_TempStores):
    """The three writers under data/ that did not ask for a mode now do.

    TestWrittenFilesGetExplicitModes below checks the bits themselves, and
    only where the bits exist (POSIX). This is the half that runs everywhere:
    each writer hands its file to jsonstore.write_atomic — the one place the
    mode is set — and the webhook secret asks for owner-only (review,
    2026-11-15).
    """

    def _written(self, act):
        from unittest import mock

        import jsonstore

        with mock.patch.object(jsonstore, "write_atomic",
                               wraps=jsonstore.write_atomic) as spy:
            act()
        self.assertEqual(spy.call_count, 1, "the writer bypassed the idiom")
        return spy.call_args

    def test_the_webhook_secret_is_written_owner_only(self):
        from api import hook_receiver

        call = self._written(
            lambda: hook_receiver._store_hook_secret("Bearer abc"))
        self.assertEqual(call.args[0], hook_receiver._secret_path())
        self.assertEqual(
            call.kwargs.get("file_mode"), 0o600,
            "hook-secret.json is a credential — whoever reads it can forge a "
            "push the receiver accepts")
        self.assertEqual(hook_receiver._load_hook_secret(), "Bearer abc")

    def test_the_listener_history_is_written_through_it(self):
        from api import stats

        call = self._written(lambda: stats.record_sample(3, now=1000.0))
        self.assertEqual(call.args[0], stats.LISTENERS_PATH)
        # One compact line, as it always was: a month is thousands of rows.
        self.assertEqual(len(stats.LISTENERS_PATH.read_text("utf-8").splitlines()), 1)
        stats._samples, stats._loaded = [], False
        stats._load()
        self.assertEqual(stats._samples, [{"t": 1000, "n": 3}])

    def test_the_sound_categories_are_written_through_it(self):
        from unittest import mock

        from api import sounds as api_sounds

        path = Path(self._tmp.name) / "sound-meta.json"
        with mock.patch.object(api_sounds, "META_PATH", path):
            call = self._written(lambda: api_sounds._save_sound_meta(
                {"ring.wav": {"category": "bells"}}))
            self.assertEqual(call.args[0], path)
            self.assertEqual(api_sounds._sound_meta(),
                             {"ring.wav": {"category": "bells"}})
        self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_no_writer_under_api_opens_a_data_file_bare(self):
        # The shape the three had in common: a bare open(..., "w") or
        # write_text on a path under data/. Uploaded audio is bytes, not a
        # store, and is the one writer this leaves alone.
        for name in ("stats.py", "sounds.py", "hook_receiver.py"):
            src = (AGENT_WORKER / "api" / name).read_text(encoding="utf-8")
            with self.subTest(module=name):
                self.assertNotIn(".write_text(", src)
                self.assertNotIn('"w", encoding', src)


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
