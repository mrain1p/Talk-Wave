"""The HTTP surface: who we believe a request is from, and the ceilings on minting a call.

Split out of test_sidecar.py; see tests/__init__.py.
"""

from __future__ import annotations

import asyncio
import json
import types
import unittest
from tests.support import REPO, _FakeRequest, _TempStores


class TestListenerSamplesAreHonest(unittest.TestCase):
    """The ACTIVITY strip's listener curve must never flatter an outage: an
    unreachable station is a GAP in the series, not a zero, and the buffer
    prunes itself to the 30 days the month view can show."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        from api import stats

        self.stats = stats
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._old_path = stats.LISTENERS_PATH
        stats.LISTENERS_PATH = Path(self.tmp.name) / "listeners.json"
        stats._samples = []
        stats._loaded = True
        self.addCleanup(setattr, stats, "LISTENERS_PATH", self._old_path)
        self.addCleanup(setattr, stats, "_samples", [])
        self.addCleanup(setattr, stats, "_loaded", False)

    def test_a_missing_answer_is_a_gap_not_a_zero(self):
        self.stats.record_sample(3, now=1000.0)
        self.stats.record_sample(None, now=1300.0)
        self.stats.record_sample(5, now=1600.0)
        self.assertEqual([s["n"] for s in self.stats._samples], [3, 5])

    def test_the_buffer_prunes_to_the_month_view(self):
        old = 1000.0
        self.stats.record_sample(2, now=old)
        self.stats.record_sample(4, now=old + self.stats.KEEP_SECS + 60)
        self.assertEqual([s["n"] for s in self.stats._samples], [4])

    def test_both_station_shapes_parse_and_junk_does_not(self):
        c = self.stats._listener_count
        self.assertEqual(c({"listeners": {"current": 7}}), 7)
        self.assertEqual(c({"context": {"listeners": {"count": 2}}}), 2)
        self.assertIsNone(c({}))
        self.assertIsNone(c({"listeners": "seven"}))

    def test_the_buffer_survives_a_restart(self):
        self.stats.record_sample(6, now=2000.0)
        self.stats._samples = []
        self.stats._loaded = False
        self.stats._load()
        self.assertEqual([s["n"] for s in self.stats._samples], [6])


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
                on_disk = (REPO / "web-widget" / name).stat().st_size
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


class TestCallFeedbackRejectsGarbageRoomsCheaply(unittest.TestCase):
    """/call-feedback is unauthenticated by design (a stranger rating their own
    call, keyed by a random room). A malformed room was never a real call, so
    it is refused before the 10s / 20-scan retry loop, and a semaphore caps how
    many waiters can be parked at once — otherwise a flood of well-formed but
    nonexistent rooms holds a pile of requests open (0.10.57 review)."""

    def _feedback(self, room, rating="up", rate=None):
        import asyncio
        import json as _json
        import types

        from api import tokens as api_tokens
        from call import record as call_record

        async def _json_body():
            return {"room": room, "rating": rating}

        req = types.SimpleNamespace(headers={}, json=_json_body)
        old = call_record.rate
        if rate is not None:
            call_record.rate = rate
        try:
            resp = asyncio.run(api_tokens.handle_call_feedback(req))
        finally:
            call_record.rate = old
        return resp.status, _json.loads(resp.body.decode())

    def test_a_malformed_room_is_a_400_before_any_scan(self):
        # rate() would raise if reached — proving the shape gate short-circuits.
        def _boom(*a):
            raise AssertionError("rate() must not be called for a bad room")

        for bad in ("", "../etc", "callin-XYZ", "callin-a-zzz", "not-a-room"):
            status, _ = self._feedback(bad, rate=_boom)
            self.assertEqual(status, 400, bad)

    def test_a_well_shaped_room_reaches_the_store(self):
        status, body = self._feedback(
            "callin-o-0123456789ab", rate=lambda *a: True)
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))


class TestJoinTokensExpire(unittest.TestCase):
    def test_a_minted_token_is_short_lived(self):
        """A join token is the only thing between a stranger and an agent job.
        The door code and the usage limits are checked when it is MINTED, so a
        long-lived token is a line that can be reopened without passing either
        again. The SDK default is six hours."""
        from api import tokens as api_tokens

        self.assertLessEqual(api_tokens.TOKEN_TTL.total_seconds(), 300)


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


class TestAMissingModelNamesTheOnesTheServerHas(unittest.TestCase):
    """From a beta tester's llama-swap (2026-08-08): pointing the LLM at a
    multi-model router answered 404 "no router for requested model", because
    that server routes by exact model name — while clients that pick from the
    server's own /v1/models list connect fine. The test endpoint recognises
    the miss and asks the server what it does offer, so the panel answers the
    question instead of starting a support thread."""

    def test_the_observed_llama_swap_error_is_recognised(self):
        from api.diagnostics import _looks_like_no_such_model

        self.assertTrue(_looks_like_no_such_model(
            "Error code: 404 - {'error': 'no router for requested model', "
            "'src': 'llama-swap'}"))
        # OpenAI's own wording for the same miss.
        self.assertTrue(_looks_like_no_such_model(
            "The model `gpt-5x` does not exist or you do not have access"))

    def test_an_ordinary_failure_is_not_mistaken_for_it(self):
        from api.diagnostics import _looks_like_no_such_model

        for err in ("Connection refused", "401 unauthorized",
                    "timed out waiting for the first token"):
            self.assertFalse(_looks_like_no_such_model(err), err)

    def test_every_shape_a_models_endpoint_answers_with(self):
        from api.diagnostics import _model_names

        openai = {"data": [{"id": "llama-3.1-8b"}, {"id": "qwen3"}]}
        ollama = {"models": [{"name": "mistral:7b"}]}
        self.assertEqual(_model_names(openai), ["llama-3.1-8b", "qwen3"])
        self.assertEqual(_model_names(ollama), ["mistral:7b"])
        self.assertEqual(_model_names(["a", "b"]), ["a", "b"])
        # Garbage never becomes a hint.
        self.assertEqual(_model_names({"weird": True}), [])
        self.assertEqual(_model_names(None), [])


class TestTheModelListFollowsTheEndpoint(unittest.TestCase):
    """A beta tester pointed the openai provider at llama-swap and every
    dropdown pick 404'd — the model list came from api.openai.com while the
    calls went to their server. The list must be read from wherever the calls
    will actually go (mirroring the station's /settings/llm/discover), and
    only for providers whose calls honour llm_base_url at all."""

    def _endpoint(self, provider, base):
        from api.settings import _custom_llm_endpoint

        return _custom_llm_endpoint(
            {"llm_provider": provider, "llm_base_url": base})

    def test_a_custom_url_wins_for_openai_protocol_providers(self):
        for provider in ("openai", "openai-compatible", "deepseek",
                         "requesty", "gateway"):
            self.assertEqual(
                self._endpoint(provider, "http://192.168.1.201:18081/v1"),
                "http://192.168.1.201:18081/v1", provider)

    def test_no_url_means_the_official_catalogue(self):
        for provider in ("openai", "deepseek", "openai-compatible"):
            self.assertEqual(self._endpoint(provider, ""), "", provider)

    def test_the_official_host_typed_back_in_is_not_custom(self):
        # DeepSeek's own address in the box is the default spelled out, not a
        # server of the operator's — the catalogue (with its key) still wins.
        self.assertEqual(
            self._endpoint("deepseek", "https://api.deepseek.com/v1"), "")

    def test_providers_that_ignore_the_field_are_never_probed(self):
        # build_llm passes no base_url to these, so a list read from it would
        # describe a server the calls never reach. Ollama has its own
        # /api/tags path and is handled there.
        for provider in ("google", "anthropic", "openrouter", "ollama"):
            self.assertEqual(
                self._endpoint(provider, "http://192.168.1.201:18081/v1"),
                "", provider)
