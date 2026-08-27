"""A call while it is running: answering, holding, going quiet, and ending.

Split out of test_sidecar.py; see tests/__init__.py.
"""

from __future__ import annotations

import asyncio
import types
import unittest
import settings as settings_store
import speech_filter
from tests.support import _TempStores


class TestASlowModelGetsRoomRatherThanFailing(unittest.TestCase):
    """A tester's calls all died on ollama/qwen2.5:7b (2026-08-13) while every
    stage in the panel was green or yellow. The SDK's default patience is 10s
    per attempt, and for a streamed completion that is the ceiling on TIME TO
    FIRST TOKEN — so a self-hosted model that is merely slow fails every turn.
    Nothing here is a preference; each number is what a caller can survive."""

    def test_a_self_hosted_provider_may_take_longer_and_retries_less(self):
        import llm_pace
        from call.providers import llm_conn_options

        for provider in llm_pace.SELF_HOSTED:
            opts = llm_conn_options({"llm_provider": provider})
            self.assertEqual(opts.timeout, 30.0, provider)
            # Retrying a local model that is still thinking queues the same
            # generation again; three retries buy nothing and cost the caller
            # half a minute of silence.
            self.assertEqual(opts.max_retry, 1, provider)

    def test_a_cloud_provider_keeps_the_sdks_own_defaults(self):
        from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

        from call.providers import llm_conn_options

        for provider in ("openai", "google", "anthropic", "deepseek", ""):
            opts = llm_conn_options({"llm_provider": provider})
            self.assertEqual(opts.timeout, DEFAULT_API_CONNECT_OPTIONS.timeout, provider)
            self.assertEqual(opts.max_retry, DEFAULT_API_CONNECT_OPTIONS.max_retry, provider)

    def test_the_budget_actually_reaches_the_session(self):
        # Source, because building an AgentSession needs a room, a worker and
        # three live providers. The failure this guards is silent in exactly
        # the way the original was: pass no conn_options and every deployment
        # quietly gets the cloud default back.
        import inspect

        from call.session import CallSession

        src = inspect.getsource(CallSession.start)
        self.assertIn("conn_options=SessionConnectOptions(", src)
        self.assertIn("llm_conn_options(self.cfg)", src)


class TestTheRecordSaysWhenTheCallerWasKeptWaiting(unittest.TestCase):
    """Slow-but-working had no symptom anywhere: no exception, nothing in the
    transcript, and an operator left saying calls "felt off". Same reasoning as
    the TTS pace meter, one leg earlier."""

    def setUp(self):
        import llm_pace

        self.llm_pace = llm_pace
        self.meter = llm_pace.ThinkMeter(label="ollama/qwen2.5:7b", budget=30.0)

    def test_a_model_that_keeps_up_says_nothing(self):
        for _ in range(4):
            self.meter.note(0.6)
        self.assertEqual(self.meter.report(), "")

    def test_a_call_with_no_turns_says_nothing(self):
        # A caller who hung up during the ring must not produce a verdict about
        # a model that was never asked anything.
        self.assertEqual(self.meter.report(), "")

    def test_the_pause_before_every_reply_is_named_and_counted(self):
        self.meter.note(0.4)
        self.meter.note(3.0)
        self.meter.note(5.0)
        said = self.meter.report()
        self.assertIn("2 of 3", said)
        self.assertIn("5.0s", said)          # the worst one
        self.assertIn("ollama/qwen2.5:7b", said)

    def test_a_turn_thrown_away_outranks_a_slow_one(self):
        # Both happened on the same call: the report has to lead with the one
        # the caller actually heard, which is the apology.
        self.meter.note(4.0)
        self.meter.gave_up()
        said = self.meter.report()
        self.assertIn("30s", said)
        self.assertIn("retried", said)

    def test_the_target_is_one_number_for_every_surface(self):
        # The module claims one target shared by the meter, the panel's help
        # and the pipeline verdict. Two of those live in files this test can
        # read; the third is this constant.
        from tests.support import REPO

        self.assertEqual(self.llm_pace.DESIRED_FIRST_TOKEN, 1.5)
        panel = (REPO / "web-widget" / "panel.js").read_text(encoding="utf-8")
        # The server sends the real number; this is the fallback the panel uses
        # if an older worker answers, and it must not disagree.
        self.assertIn("d.desiredMs || 1500", panel)


class TestTheRecordNamesWhichLegFailed(unittest.TestCase):
    """`LLMError type='llm_error' label='...'` was the operator's first sight of
    a failed call, and it says nothing about what to do."""

    def _err(self, kind, inner):
        return types.SimpleNamespace(type=kind, error=inner, recoverable=True)

    def test_a_model_out_of_time_is_told_apart_from_a_model_that_broke(self):
        from livekit.agents import APITimeoutError

        from call import lifecycle

        self.assertTrue(lifecycle._model_gave_up(self._err("llm_error", APITimeoutError())))
        self.assertFalse(lifecycle._model_gave_up(self._err("llm_error", ValueError("nope"))))
        # A voice that timed out is not the model running out of time, and the
        # fix for it is a different one.
        self.assertFalse(lifecycle._model_gave_up(self._err("tts_error", APITimeoutError())))

    def test_the_timeout_line_says_the_budget_and_what_the_caller_heard(self):
        import llm_pace
        from livekit.agents import APITimeoutError

        from call import lifecycle

        said = lifecycle._in_plain_words(
            self._err("llm_error", APITimeoutError()),
            llm_pace.ThinkMeter(budget=30.0),
        )
        self.assertIn("30s", said)
        self.assertIn("apology", said)

    def test_every_other_failure_still_names_its_leg(self):
        from call import lifecycle

        self.assertTrue(
            lifecycle._in_plain_words(self._err("tts_error", ValueError("no voice")))
            .startswith("The voice failed:"))
        self.assertTrue(
            lifecycle._in_plain_words(self._err("stt_error", ValueError("deaf")))
            .startswith("Speech-to-text failed:"))


class TestCallStructure(unittest.TestCase):
    """The call is an object with phases, not a 334-line function. These pin
    the seams so a future edit can't quietly put the call back in one place."""

    @classmethod
    def setUpClass(cls):
        from call import greeting, lifecycle
        from call.session import CallSession

        cls.CallSession = CallSession
        cls.lifecycle = lifecycle
        cls.greeting = greeting

    def test_entrypoint_only_decides_whether_to_answer(self):
        # main.py's job is wiring. If this grows again, the call has started
        # leaking back out of CallSession.
        import inspect

        import main

        body = inspect.getsource(main.entrypoint)
        # 40, up from 30 when voicemail arrived: routing a vm- room to the
        # answering machine is exactly the deciding-who-answers this function
        # is for. Call BEHAVIOUR appearing here is still the thing to refuse.
        self.assertLess(len(body.splitlines()), 40)
        self.assertIn("probe-", body)          # still refuses probe rooms
        self.assertIn("vm-", body)             # and routes the machine's rooms
        # prepare( rather than prepare(): the join coroutine rides in as its
        # argument now, so the phases stay separate but the first takes one.
        for phase in ("prepare(", "start()", "greet()"):
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

    def test_the_mcp_allowlist_fails_closed_on_empty(self):
        # The SDK reads an empty allowed_tools list as "no filter — expose
        # everything the station's MCP server offers", including destructive
        # tools. start() substitutes a sentinel that matches no real tool, so
        # an empty allowlist keeps the surface shut rather than opening it
        # (0.10.57 review).
        import inspect

        src = inspect.getsource(self.CallSession)
        self.assertIn("allowed_tools or [", src,
                      "an empty allowlist must fall back to a no-match sentinel")

    def test_the_slot_release_is_registered_before_anything_can_raise(self):
        # A call that raised in prepare() or early start() — a provider
        # misconfig fails EVERY call at build_llm — never reached the release
        # registration at the tail of start(), so the slot it minted sat held
        # for the 30-minute age-out and two such failures jammed the line
        # (0.10.57 review). Release and background-cancel are registered in
        # __init__ now, so they fire whatever raises later.
        import inspect

        src = inspect.getsource(self.CallSession)
        init = src[src.index("def __init__"):src.index("def prepare")]
        self.assertIn("release_call_slot", init,
                      "slot release must register in __init__, not start()")
        self.assertIn("background.cancel_all", init,
                      "background cancel must register in __init__ too")
        # And it is NOT still done a second time inside _on_shutdown.
        shut = src[src.index("_on_shutdown"):]
        self.assertNotIn("await lifecycle.release_call_slot", shut)

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

        src = inspect.getsource(self.greeting.greet)
        self.assertIn("user_input=", src)

        seen = {}

        class FakeSession:
            async def generate_reply(self, **kw):
                seen.update(kw)
            async def say(self, *a, **kw):
                pass

        asyncio.run(self.greeting.greet(FakeSession(), {}))
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
        from call import postmortem

        s = self._session(heard=0)
        postmortem._note_if_nothing_was_heard(s, 15.0, [("dj", "Evening, you're through.")])
        problems = s.record.data["problems"]
        self.assertEqual(len(problems), 1)
        what = problems[0]["what"]
        self.assertIn("No audio was ever received", what)
        self.assertIn("off-LAN", what)          # the likeliest cause, named
        self.assertIn("the DJ did speak", what)  # so a mic problem is separable

    def test_a_call_that_heard_the_caller_is_not_flagged(self):
        from call import postmortem

        s = self._session(heard=3)
        postmortem._note_if_nothing_was_heard(s, 90.0, [("caller", "hello"), ("dj", "hi")])
        self.assertEqual(s.record.data["problems"], [])

    def test_it_records_whether_the_dj_spoke_at_all(self):
        # A DJ that never spoke points at the pipeline; one that did points at
        # the caller's side. The record has to keep them apart.
        from call import postmortem

        s = self._session(heard=0)
        postmortem._note_if_nothing_was_heard(s, 12.0, [])
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

    def test_a_swallowed_greeting_still_speaks(self):
        # The SDK marks some LLM errors `recoverable` and swallows them:
        # generate_reply returns with no exception AND no reply. Observed
        # live (2026-08-11): three recoverable Gemini 504s, 43 seconds of
        # dead air, the caller said "Hello" into silence. The record is the
        # ground truth of whether the DJ spoke; empty means the canned
        # pickup goes out.
        import asyncio
        import types

        from call import greeting

        said = []

        class _Session:
            async def generate_reply(self, **kw):
                return None          # "recoverable" swallow: no raise, no reply

            async def say(self, *a, **k):
                said.append(a[0])

        record = types.SimpleNamespace(data={"turns": [], "tools": []})
        asyncio.run(greeting.greet(_Session(), {}, record=record))
        self.assertEqual(len(said), 1)
        self.assertIn("through to the booth", said[0])

        # And when the greeting DID land (a dj turn or a first word stamped),
        # no second voice barges in on top of it.
        said.clear()
        record = types.SimpleNamespace(
            data={"turns": [{"who": "dj", "text": "hi"}], "tools": []})
        asyncio.run(greeting.greet(_Session(), {}, record=record))
        self.assertEqual(said, [])

    def test_a_repeated_recoverable_error_apologises(self):
        # One recoverable error is the SDK's to absorb. A second inside the
        # window means "recoverable" is not recovering, and the caller must
        # hear the same apology a fatal error buys.
        import asyncio
        import types

        from call import lifecycle

        said = []
        handlers = {}

        class _Session:
            def on(self, name, fn):
                handlers[name] = fn

            async def say(self, *a, **k):
                said.append(a[0])

        async def go():
            lifecycle.attach_error_recovery(_Session())
            err = types.SimpleNamespace(recoverable=True)
            handlers["error"](types.SimpleNamespace(error=err, source="llm"))
            await asyncio.sleep(0.05)
            self.assertEqual(said, [], "one recoverable error is not an outage")
            handlers["error"](types.SimpleNamespace(error=err, source="llm"))
            await asyncio.sleep(0.05)

        asyncio.run(go())
        self.assertEqual(len(said), 1)
        self.assertIn("giving me trouble", said[0])

    def test_no_check_in_while_the_broadcast_has_the_microphone(self):
        self.assertEqual(
            self._run(on_air=True), [],
            "the DJ asked the caller why it was quiet during its own hold")

    def test_the_check_in_still_fires_when_the_air_is_clear(self):
        # The other half — pinning it must not disable the feature outright.
        self.assertTrue(
            self._run(on_air=False),
            "the idle check-in stopped working when the air was clear")


class TestNoStockPhraseIsPutInTheDJsMouth(unittest.TestCase):
    """"One second, let me have a look" — word for word, call after call.

    Two places were seeding it at once: a list of three stock lines in the
    worker that always reached for the first, and the prompt's own example
    ("Say it in your voice (\"let me have a look\")"). So Wade and Ash said the
    identical sentence, which is exactly as hollow as it sounds.

    It cannot be generated instead: this fires WHILE the model's turn is in
    flight, and a DJ asked to speak before acting speaks instead of acting.
    So it is the operator's wording or nothing, and nothing is the default.
    """

    def test_with_no_wording_the_dj_says_nothing(self):
        import asyncio
        import types

        from call import lifecycle

        said = []
        subs = {}

        class _Session:
            def on(self, name, fn):
                subs[name] = fn

            def say(self, text, **kw):
                said.append(text)

        ctx = types.SimpleNamespace(add_shutdown_callback=lambda *a: None)

        async def go():
            lifecycle.attach_working_line(
                ctx, _Session(), {"working_line_secs": 0.2}, air=None)
            fire = subs.get("agent_state_changed")
            if fire:
                fire(types.SimpleNamespace(new_state="thinking"))
            await asyncio.sleep(1.2)

        asyncio.run(go())
        self.assertEqual([], said)

    def test_the_prompt_stops_suggesting_the_phrase(self):
        # The stock list was only half of it — the conduct told the DJ the
        # words to use, so removing the list alone would have changed nothing.
        from brain import conduct

        self.assertNotIn("let me have a look", conduct.HOW_TO_TALK)


class TestTheGateDoesNotChatter(unittest.TestCase):
    """A one-second hold, then another five seconds later.

    Room f38cdab69cce, 2026-08-16, and the rows say it plainly:

        210.2  hold opened   callerLag=22.0  why=the station is on air
        211.2  hold closed   heldSecs=1.0    why=the estimate ran out
        216.2  hold opened   callerLag=22.0  why=the station is on air

    The voice had started at 193.7, runs 22.6s, and the caller hears it from
    +215.7 to +238 — so at the moment the gate shut they had not heard a word
    of it. Every other sizing here is an ESTIMATE (a word count off the log, a
    forecast ageing between polls), and an estimate that comes up short makes
    the gate chatter rather than merely end early.

    The push carries what the station actually knows, so it becomes the floor.
    """

    def _guard(self):
        import types

        from call.air import OnAirGuard

        g = OnAirGuard(types.SimpleNamespace(), {}, room=None)
        g._assumed_until = 0.0
        return g

    def test_a_push_outranks_the_word_count(self):
        # THE COLLAPSE. The log records when speech started and never when it
        # ended, so _log_says_busy guessed the length by counting the words of
        # the entry. When a push exists the station has already said how long
        # it runs, and two answers for one question is where the chatter came
        # from — whichever expires first shuts the gate.
        #
        # Three words, twenty-two seconds of audio: the word count would say
        # this finished almost immediately.
        import json
        import os
        import tempfile
        import time

        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "hook-air.json")
            # SAVE AND RESTORE, never pop: tests/__init__.py redirects this
            # path process-wide so no guard ever reads the repo's real
            # data/hook-air.json — and since the gate began priming itself
            # from the file at construction, EVERY guard built after a pop
            # would read it, not just the ones that ask for pushes. Popping
            # here is how six unrelated tests flipped on 2026-08-18.
            prev = os.environ.get("CALLIN_HOOK_AIR_PATH")
            os.environ["CALLIN_HOOK_AIR_PATH"] = p
            try:
                g = self._guard()
                g._last_buf = 22.0
                now = time.time()
                # Handed over 25s ago, so the caller is 3s into hearing it and
                # has ~20s left. The word count for "back after this" would
                # have called it finished long before.
                with open(p, "w", encoding="utf-8") as f:
                    json.dump({"at": now - 25, "v": 2, "phase": "speaking",
                               "durMs": 22600, "bufSecs": 22.0,
                               "text": "back after this"}, f)
                self.assertTrue(
                    g._log_says_busy((25.0, "back after this")),
                    "the word count closed the gate while the station was "
                    "still 20 seconds from finishing in the caller's ear")
            finally:
                if prev is None:
                    os.environ.pop("CALLIN_HOOK_AIR_PATH", None)
                else:
                    os.environ["CALLIN_HOOK_AIR_PATH"] = prev

    def test_with_no_push_the_word_count_still_answers(self):
        # A station too old to send the voice lifecycle must keep working.
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            # Restore the suite-wide redirect, never pop it away — see the
            # save/restore note in the test above.
            prev = os.environ.get("CALLIN_HOOK_AIR_PATH")
            os.environ["CALLIN_HOOK_AIR_PATH"] = os.path.join(td, "none.json")
            try:
                g = self._guard()
                self.assertEqual((0.0, 0.0), g.audible_window())
                self.assertTrue(g._log_says_busy((g.caller_lag(), "a line")))
            finally:
                if prev is None:
                    os.environ.pop("CALLIN_HOOK_AIR_PATH", None)
                else:
                    os.environ["CALLIN_HOOK_AIR_PATH"] = prev

    def test_the_stations_own_numbers_outlast_a_short_estimate(self):
        import time

        g = self._guard()
        now = time.time()
        g._assumed_until = now + 1.0            # the estimate that ran out
        g.hold_at_least_as_long_as(
            {"at": now - 16.5, "durMs": 22600, "bufSecs": 22.0})
        # Caller stops hearing it at start + 22 + 22.6.
        self.assertGreaterEqual(g._assumed_until, now + 27.0)

    def test_it_only_ever_lengthens(self):
        import time

        g = self._guard()
        now = time.time()
        g._assumed_until = now + 90.0
        g.hold_at_least_as_long_as({"at": now, "durMs": 3000, "bufSecs": 22.0})
        self.assertAlmostEqual(g._assumed_until, now + 90.0, delta=0.5)

    def test_a_push_with_no_duration_decides_nothing(self):
        g = self._guard()
        g.hold_at_least_as_long_as({"at": 1.0, "bufSecs": 22.0})
        self.assertEqual(0.0, g._assumed_until)
        g.hold_at_least_as_long_as(None)
        self.assertEqual(0.0, g._assumed_until)

    def test_a_push_that_forgot_the_buffer_uses_the_known_lag(self):
        # voice.end reports no buffer; 0.97.19 carries one forward, but a push
        # that still arrives without one must not size the hold as if the
        # caller were level with the encoder.
        import time

        g = self._guard()
        g._last_buf = 22.0
        now = time.time()
        g.hold_at_least_as_long_as({"at": now, "durMs": 4000, "bufSecs": 0})
        self.assertGreaterEqual(g._assumed_until, now + 26.0)


class TestTheTimelineSaysWhatTheGuardCouldSee(unittest.TestCase):
    """An empty air log meant two different things, and that cost a diagnosis.

    Room 113774ecedfa, 2026-08-16: five minutes, ZERO air rows, and the caller
    asking out loud "how are you talking on air and to me at the same time".
    Nothing in the record could distinguish "the station was quiet" from "the
    guard never saw it", and by the time anyone looked the containers had been
    recreated and the logs were gone.

    The station's djLog turned out to hold nothing newer than a 'whoosh' seven
    hours old. One baseline row would have said that on the spot.
    """

    def _log(self):
        from call.air_log import AirLog

        return AirLog()

    def test_a_quiet_station_is_recorded_as_watched_not_as_nothing(self):
        log = self._log()
        log.watching(None)
        self.assertEqual(1, len(log.rows))
        self.assertIn("none in the log", log.rows[0]["why"])

    def test_a_stale_log_shows_its_age(self):
        # The number is the finding: seven hours is a station that stopped
        # writing, not a station that stopped talking.
        log = self._log()
        log.watching((24936.0, "whoosh"))
        self.assertIn("24936s ago", log.rows[0]["why"])

    def test_a_failed_read_is_on_the_timeline_too(self):
        log = self._log()
        log.read_failed()
        self.assertIn("could not read the station", log.rows[0]["why"])
        self.assertIn("holding the gate", log.rows[0]["why"])


class TestTheCheckInDoesNotBlameTheCallerForOurOwnPause(unittest.TestCase):
    """"Still with me?" to somebody waiting on a dig the DJ announced.

    The clock already pauses while a tool is in flight. That test kept being
    right and useless: on 2026-08-16 the DJ said "I'm digging through the
    crates now", ran nothing at all, and asked "Still with me?" eighteen
    seconds later — and earlier the same evening, twenty seconds after four
    searches had come back with no answer ever spoken. Nothing was in flight
    either time, so is_working() was correctly False.

    A promise the DJ has not kept is still the DJ's turn. Capped, though: a DJ
    that promises and never delivers must not buy silence for the rest of the
    call.
    """

    def _run(self, prime, seconds: float = 3.5):
        import asyncio
        import types

        from call import lifecycle
        from call.actions import CallActions

        replies = []
        actions = CallActions(9)
        prime(actions)

        class _Session:
            agent_state = "listening"
            user_state = "listening"

            def on(self, *a, **k):
                pass

            async def generate_reply(self, **kw):
                replies.append(kw)

            async def say(self, *a, **k):
                replies.append({"say": a})

        ctx = types.SimpleNamespace(add_shutdown_callback=lambda *a: None)

        async def go():
            lifecycle.attach_idle_watch(
                ctx, _Session(),
                {"idle_prompt_secs": 1, "idle_max_nudges": 2},
                actions=actions)
            await asyncio.sleep(seconds)

        asyncio.run(go())
        return replies

    def test_a_promise_with_no_tool_still_counts_as_our_turn(self):
        self.assertEqual([], self._run(lambda a: a.promise_made()))

    def test_a_tool_in_flight_still_counts(self):
        self.assertEqual([], self._run(lambda a: a.mark_working(30)))

    def test_an_idle_caller_is_still_checked_on(self):
        self.assertTrue(self._run(lambda a: None))

    def test_an_action_landing_hands_the_turn_back(self):
        # The promise was kept, so the pause after it is the caller's again.
        def kept(a):
            a.promise_made()
            a.note("request", "Landslide")

        self.assertTrue(self._run(kept))

    def test_a_promise_never_kept_does_not_buy_silence_for_ever(self):
        import time

        from call.actions import CallActions

        a = CallActions(9)
        a.promised_at = time.time() - (CallActions.PROMISE_PATIENCE_SECS + 1)
        self.assertFalse(a.caller_is_waiting_on_us())


class TestTheCheckInDoesNotTalkOverTheCaller(unittest.TestCase):
    """"Still with me?" arriving on top of the caller's answer.

    The idle clock counts TRANSCRIPTS and not voice, on purpose: VAD rode the
    SDK's away-state and a TV or the station bleeding in kept the check-in from
    ever firing in a real room. The cost of that only shows on real audio —
    a transcript lands 8-11 seconds after the caller starts speaking on this
    deployment, so "nothing transcribed" and "nobody talking" are a whole
    sentence apart.

    Both calls of 2026-08-16 did it, on the goodbye: the caller began "that's
    everything, thanks" and the DJ came in over the top with "still with me?".

    So the caller's voice is a VETO on firing and never a reset of the clock,
    and the veto is capped — a room that reads as speaking for ever must still
    get its check-in, or this reintroduces the bug the transcript rule fixed.
    """

    def _run(self, user_state, seconds: float = 3.5):
        import asyncio
        import types

        from call import lifecycle

        replies = []

        class _Session:
            agent_state = "listening"

            def __init__(self):
                self.user_state = user_state

            def on(self, *a, **k):
                pass

            async def generate_reply(self, **kw):
                replies.append(kw)

            async def say(self, *a, **k):
                replies.append({"say": a})

        ctx = types.SimpleNamespace(add_shutdown_callback=lambda *a: None)

        async def go():
            lifecycle.attach_idle_watch(
                ctx, _Session(),
                {"idle_prompt_secs": 1, "idle_max_nudges": 2},
            )
            await asyncio.sleep(seconds)

        asyncio.run(go())
        return replies

    def test_a_quiet_caller_still_gets_checked_on(self):
        self.assertTrue(self._run("listening"),
                        "the check-in stopped firing at all")

    def test_the_check_in_waits_while_the_caller_is_mid_word(self):
        # 8 beats of half a second, so nothing may go out inside two seconds.
        self.assertEqual([], self._run("speaking", seconds=2.0))

    def test_a_room_that_never_goes_quiet_still_gets_one(self):
        # The failure mode the transcript-only clock was built to avoid: a
        # caller on speakerphone with the station bleeding back reads as
        # speaking indefinitely, and a veto with no ceiling would mute the
        # check-in for the whole call.
        self.assertTrue(self._run("speaking", seconds=7.0),
                        "a noisy room can suppress the check-in for ever")


class TestTheIdleClockDoesNotRunWhileTheDJIsWorking(unittest.TestCase):
    """"Still there?" must not be asked while the DJ is the one working.

    The Zeppelin call (2026-08-10): a request took a while to resolve in the
    background, the caller waited, and the idle watcher asked whether THEY were
    still there — while they were waiting on US. CallActions carries a working
    flag the late-match poller holds across the resolve, and the idle watcher
    must reset its clock while it is set, the same way it does during an on-air
    hold.
    """

    def _run(self, working: bool, seconds: float = 3.5):
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
        # Mirrors CallActions on both questions. A double carrying only
        # is_working stopped matching the interface at 0.97.15, and the way it
        # failed is worth remembering: the missing attribute raised inside the
        # watch loop, the task died, and the check-in went silent for the whole
        # call with nothing saying so.
        actions = types.SimpleNamespace(is_working=lambda: working,
                                        caller_is_waiting_on_us=lambda: working)

        async def go():
            lifecycle.attach_idle_watch(
                ctx, _Session(),
                {"idle_prompt_secs": 1, "idle_max_nudges": 2}, actions=actions,
            )
            await asyncio.sleep(seconds)

        asyncio.run(go())
        return replies

    def test_no_check_in_while_a_request_is_still_resolving(self):
        self.assertEqual(
            self._run(working=True), [],
            "the DJ asked the caller why it was quiet while IT was working")

    def test_the_check_in_still_fires_when_the_dj_is_idle(self):
        self.assertTrue(
            self._run(working=False),
            "the idle check-in stopped firing entirely")


class TestACallerWhoWasNeverHeardIsToldSo(unittest.TestCase):
    """A caller nothing has ever arrived from is a different problem from a
    caller who has gone quiet, and until 0.9.111 they got the same treatment.

    From the transcripts of 2026-08-06: four of the last six calls had
    `callerTurns: 0`. On one of them (18:01:41) the DJ greeted, said "Still
    with me?" 63 seconds later, and said nothing else for another 60 seconds
    until the caller gave up — the goodbye the code does generate was still
    two minutes away. Nothing it said ever named the actual problem, which the
    caller was the only person who could fix.

    Two changes, both defended here: the patient triple-length window does not
    apply when there is no answer coming, and both lines say what is wrong.
    """

    def _run(self, heard_n: int, seconds: float = 3.5, nudges: int = 2):
        import asyncio
        import types

        from call import lifecycle

        said = []

        class _Session:
            agent_state = "listening"

            def on(self, *a, **k):
                pass

            async def generate_reply(self, **kw):
                said.append(str(kw.get("instructions", "")))

            async def say(self, *a, **k):
                said.append(str(a[0]) if a else "")

        ctx = types.SimpleNamespace(
            add_shutdown_callback=lambda *a: None,
            api=types.SimpleNamespace(room=types.SimpleNamespace(
                delete_room=_noop_async)),
            room=types.SimpleNamespace(name="callin-test"),
            shutdown=lambda **k: None,
        )

        async def go():
            lifecycle.attach_idle_watch(
                ctx, _Session(),
                {"idle_prompt_secs": 1, "idle_max_nudges": nudges},
                heard={"n": heard_n},
            )
            await asyncio.sleep(seconds)

        asyncio.run(go())
        return said

    def test_the_first_line_names_the_microphone(self):
        lines = self._run(heard_n=0)
        self.assertTrue(lines, "nothing was said to a caller who was never heard")
        self.assertIn("microphone", lines[0].lower())

    def test_a_caller_who_has_been_talking_gets_the_gentle_version(self):
        # The other half: this must not turn every ordinary pause into a
        # lecture about somebody's microphone.
        lines = self._run(heard_n=4)
        self.assertTrue(lines)
        self.assertNotIn("microphone", lines[0].lower())

    def test_the_goodbye_says_why_when_nothing_was_ever_heard(self):
        # One nudge configured means the first strike IS the last, which is
        # the shortest path to the sign-off.
        lines = self._run(heard_n=0, nudges=1)
        self.assertTrue(lines)
        self.assertIn("microphone", " ".join(lines).lower())


class TestTheCallerIsToldSomebodyIsStillThere(unittest.TestCase):
    """A long wait with no sound is indistinguishable from a dead line.

    The operator, 2026-08-15: "when its attempting to do something it just
    pauses there until its done […] something like that's better than just
    waiting a bunch of time of not knowing if its doing anything at all besides
    thinking." Measured on the same box the same afternoon: "4 of 4 replies
    took longer than 1.5s to start (worst 9.1s, typical 6.5s)".

    The line is the WORKER's, never the model's — a DJ told to speak before
    acting speaks instead of acting, which is the failure promise_guard exists
    for — so nothing here may reach the conversation the model sees.
    """

    def _run(self, seconds=2.0, on_air=False, after=1, then=None):
        import asyncio
        import types

        from call import lifecycle

        said = []
        subs = {}

        class _Session:
            agent_state = "listening"

            def on(self, name, fn=None):
                subs[name] = fn

            def say(self, text, **kw):
                said.append((str(text), kw))

            async def generate_reply(self, **kw):
                said.append(("GENERATED", kw))

        ctx = types.SimpleNamespace(
            add_shutdown_callback=lambda *a: None,
            api=types.SimpleNamespace(room=types.SimpleNamespace(
                delete_room=_noop_async)),
            room=types.SimpleNamespace(name="callin-test"),
            shutdown=lambda **k: None,
        )
        air = types.SimpleNamespace(on_air=on_air)

        async def go():
            # The wording is the OPERATOR'S now, and an empty one keeps the
            # line silent: three stock phrases in the code meant every caller
            # heard the same "One second, let me have a look" in every
            # persona's voice.
            lifecycle.attach_working_line(
                ctx, _Session(),
                {"working_line_secs": after,
                 "working_line_text": "Hang on, digging.|Bear with me."},
                air=air)
            state = subs.get("agent_state_changed")
            if state:
                state(types.SimpleNamespace(new_state="thinking"))
            await asyncio.sleep(seconds)
            if then and state:
                state(types.SimpleNamespace(new_state=then))
                await asyncio.sleep(seconds)
            return said

        return asyncio.run(go())

    def test_it_says_one_line_over_a_long_wait(self):
        said = self._run()
        self.assertEqual(len(said), 1, "expected exactly one holding line")
        self.assertTrue(said[0][0].strip(), "the holding line was empty")

    def test_the_line_never_reaches_the_models_history(self):
        # An extra turn in the history is what Gemini 400s on when a tool call
        # follows it — the same reason the hand-over line is kept out.
        said = self._run()
        self.assertFalse(said[0][1].get("add_to_chat_ctx", True))

    def test_it_does_not_say_it_twice_for_one_wait(self):
        said = self._run(seconds=4.0)
        self.assertEqual(len(said), 1,
                         "a second holding line landed on the same wait")

    def test_it_stays_quiet_while_the_station_is_on_air(self):
        # That silence has its own line. Two explanations of one pause is
        # worse than none.
        self.assertEqual(self._run(on_air=True), [])

    def test_zero_switches_it_off(self):
        self.assertEqual(self._run(after=0), [])

    def test_a_new_wait_gets_its_own_line(self):
        # The DJ answered, then went away to work again: that is a second
        # wait, and the caller is owed the same courtesy.
        said = self._run(seconds=2.0, then="speaking")
        self.assertEqual(len(said), 1)


class TestTheCheckInWindowIsTheNumberTheOperatorTyped(unittest.TestCase):
    """A question in the DJ's last line must not lengthen the wait.

    It used to triple it — thinking time for a caller weighing up an answer,
    after a call where the DJ asked "Still with me?" twice while somebody chose
    between two versions of a track. The exception then ate the rule: this DJ
    hands the turn back at the end of nearly every line, so on the call that
    got it looked at (2026-08-15 15:27) all three DJ turns ended in a question
    mark and the tripled window was the only one a caller who had spoken could
    ever get. 20 seconds configured meant 60 seconds on the line; the caller
    waited 56 and gave up first.

    The test drives the OLD trigger on purpose: if a question-watching
    subscription ever comes back, this hands it a question and then holds the
    code to the configured window anyway.
    """

    def _first_check_in_after(self, question: str) -> float:
        import asyncio
        import time
        import types

        from call import lifecycle

        fired = []
        subs = {}

        class _Session:
            agent_state = "listening"

            def on(self, name, fn=None):
                subs[name] = fn

            async def generate_reply(self, **kw):
                fired.append(time.time())

            async def say(self, *a, **k):
                fired.append(time.time())

        ctx = types.SimpleNamespace(
            add_shutdown_callback=lambda *a: None,
            api=types.SimpleNamespace(room=types.SimpleNamespace(
                delete_room=_noop_async)),
            room=types.SimpleNamespace(name="callin-test"),
            shutdown=lambda **k: None,
        )

        async def go():
            started = time.time()
            lifecycle.attach_idle_watch(
                ctx, _Session(),
                {"idle_prompt_secs": 1, "idle_max_nudges": 2},
                heard={"n": 3},          # this caller HAS been heard
            )
            # The DJ's last line, ending in a question — the old path's
            # trigger. Nothing subscribes to this any more; if something does
            # again, it gets fed exactly what it is watching for.
            note = subs.get("conversation_item_added")
            if note:
                note(types.SimpleNamespace(item=types.SimpleNamespace(
                    role="assistant", text_content=question)))
            for _ in range(50):
                await asyncio.sleep(0.1)
                if fired:
                    break
            return (fired[0] - started) if fired else float("inf")

        return asyncio.run(go())

    # The watcher polls once a second, so a 1s window lands the check-in
    # somewhere in 1-2s and a tripled one could not arrive before 3. 2.5
    # separates them with room for a slow machine on either side.
    LIMIT = 2.5

    def test_a_question_does_not_buy_the_caller_a_longer_silence(self):
        waited = self._first_check_in_after("What can I do for you today?")
        self.assertLess(
            waited, self.LIMIT,
            "the check-in came after %.1fs on a 1s window — a question in the "
            "DJ's last line is lengthening the wait again" % waited)

    def test_a_line_with_no_question_is_unchanged(self):
        # The control: same window, same behaviour, so the test above is
        # measuring the question and not the clock.
        waited = self._first_check_in_after("That was Clairo, trailing off.")
        self.assertLess(waited, self.LIMIT)


class TestTheSignOffIsHeardBeforeTheLineCloses(unittest.TestCase):
    """The DJ's last word was being cut off every time it hung up itself.

    From the transcripts of 2026-08-06 the final DJ turn was "Fair", "Right"
    and "I'll" — three calls, three one-word sign-offs, each followed at once
    by the room being deleted. The old wait slept a second and then broke out
    of its poll the moment the agent was NOT speaking, which it reads as "the
    goodbye has finished". One second after the tool call it means the
    opposite: the tool call and the sign-off come from the SAME model turn, so
    the agent is still thinking and has not started talking yet.

    So the wait is two phases — wait for speech to start, then wait for it to
    stop and stay stopped — and this is the test that says so.
    """

    def _run(self, states, start_grace=1.0, quiet=0.15):
        """`states` is what agent_state returns on successive reads."""
        import asyncio

        from call import hangup

        seq = list(states)
        reads = {"n": 0}

        class _Session:
            @property
            def agent_state(self):
                i = min(reads["n"], len(seq) - 1)
                reads["n"] += 1
                return seq[i]

        old = (hangup.SPEECH_START_GRACE, hangup.QUIET_CONFIRM,
               hangup.FINAL_BEAT, hangup.SPEECH_MAX)
        hangup.SPEECH_START_GRACE = start_grace
        hangup.QUIET_CONFIRM = quiet
        hangup.FINAL_BEAT = 0.0
        hangup.SPEECH_MAX = 3.0
        try:
            asyncio.run(hangup.await_sign_off(_Session()))
        finally:
            (hangup.SPEECH_START_GRACE, hangup.QUIET_CONFIRM,
             hangup.FINAL_BEAT, hangup.SPEECH_MAX) = old
        return reads["n"]

    def test_it_waits_through_thinking_for_speech_to_start(self):
        # The exact shape of the bug: thinking first, speaking after. A wait
        # that treats "not speaking" as "finished" returns during the
        # thinking run and the goodbye is cut off at the first syllable.
        reads = self._run(
            ["thinking"] * 4 + ["speaking"] * 6 + ["listening"] * 6)
        self.assertGreaterEqual(
            reads, 11,
            "the wait returned before the sign-off had finished playing")

    def test_it_returns_once_speech_has_stopped_and_stayed_stopped(self):
        # And it must not hang on forever afterwards — dead air at the end of
        # a call is the failure in the other direction.
        reads = self._run(["speaking"] * 3 + ["listening"] * 20)
        self.assertLess(reads, 24)

    def test_a_gap_between_sentences_is_not_the_end(self):
        # An agent between two sentences reads as not-speaking for a moment.
        # A single sample would take that for the end of the goodbye and hang
        # up in the middle of it.
        reads = self._run(
            ["speaking"] * 3 + ["listening"] + ["speaking"] * 5
            + ["listening"] * 6, quiet=0.5)
        self.assertGreaterEqual(
            reads, 10, "hung up in the gap between two sentences")

    def test_a_dj_that_says_nothing_does_not_hold_the_line(self):
        # The grace period is a ceiling, not a wait: a model that emitted the
        # tool call and no words must not leave the caller on an open line.
        import time

        started = time.time()
        self._run(["listening"] * 50, start_grace=0.5)
        self.assertLess(time.time() - started, 2.0)


class TestALineThatFailsToGenerateIsStillSpoken(unittest.TestCase):
    """The whole complaint being fixed is the DJ saying NOTHING.

    An idle goodbye that dies in the provider used to leave the caller
    listening to a line that then simply closed — `generate_reply` raised, the
    warning went to the log, and only the goodbye had a fallback. Now every
    line the DJ is made to say has a plain one behind it.
    """

    def _speak(self, explode: bool):
        import asyncio

        from call import lifecycle

        spoken = []

        class _Session:
            async def generate_reply(self, **kw):
                if explode:
                    raise RuntimeError("provider is having a day")
                spoken.append(("generated", kw.get("instructions")))

            async def say(self, text, *a, **k):
                spoken.append(("canned", text))

        asyncio.run(lifecycle._say_something(
            _Session(), "be charming about it", "Still with me?"))
        return spoken

    def test_the_generated_line_is_preferred(self):
        self.assertEqual(self._speak(explode=False)[0][0], "generated")

    def test_a_failed_generation_falls_back_to_something_audible(self):
        spoken = self._speak(explode=True)
        self.assertEqual(spoken, [("canned", "Still with me?")],
                         "the caller heard nothing at all")


class TestComingBackFromAirIsAnnounced(unittest.TestCase):
    """The hand-over line tells the caller to hold. Nothing told them the hold
    was over.

    So the DJ went quiet mid-conversation, came back, and then waited for the
    caller to speak first — which from the caller's end is indistinguishable
    from the line having dropped. `_come_back` closes that, and only for a
    caller who actually heard the hand-over: the gate also closes for someone
    who dialled in mid-link, and "I'm back" to them is a line about nothing.
    """

    def test_the_dj_says_it_is_back(self):
        import asyncio
        import types

        from call import comeback
        from call.air import OnAirGuard

        said = []

        class _Session:
            async def generate_reply(self, **kw):
                said.append(str(kw.get("instructions", "")))

        guard = OnAirGuard(types.SimpleNamespace(), {}, room=None)
        asyncio.run(comeback.come_back(guard, _Session()))
        self.assertTrue(said)
        self.assertIn("back", said[0].lower())

    def test_it_still_speaks_when_the_model_will_not(self):
        import asyncio
        import types

        from call import comeback
        from call.air import OnAirGuard

        spoken = []

        class _Session:
            async def generate_reply(self, **kw):
                raise RuntimeError("no")

            def say(self, text, **kw):
                spoken.append(text)

        guard = OnAirGuard(types.SimpleNamespace(), {}, room=None)
        asyncio.run(comeback.come_back(guard, _Session()))
        self.assertEqual(len(spoken), 1)
        self.assertIn("back", spoken[0].lower())

    def test_the_comeback_line_can_nod_at_what_went_out(self):
        # "I'm back" alone reads as if the trip to air never happened. The
        # words that went out ride along so the DJ can reference them in
        # passing — and they are consumed, so a later spell with no words
        # doesn't nod at something aired minutes earlier.
        import asyncio
        import types

        from call import comeback
        from call.air import OnAirGuard

        said = []

        class _Session:
            async def generate_reply(self, **kw):
                said.append(str(kw.get("instructions", "")))

        guard = OnAirGuard(types.SimpleNamespace(), {}, room=None)
        guard.aired_text = "Big shout to Dave from the call line."
        asyncio.run(comeback.come_back(guard, _Session()))
        self.assertIn("Dave", said[0])
        self.assertEqual(guard.aired_text, "")
        asyncio.run(comeback.come_back(guard, _Session()))
        self.assertNotIn("Dave", said[1])

    def test_the_comeback_knows_what_it_already_told_the_caller(self):
        # "Don't recap" cannot be obeyed by a model that has not been told
        # what would count as a recap. On 2026-08-16 the DJ said "I just sent
        # that shoutout for Marcus, and the Fleetwood Mac track is lined up"
        # on its way out, then came back and said both things again — with
        # "don't recap" already in the instruction.
        #
        # The operator's steer is that referring back is GOOD continuity and
        # only the verbatim repeat is wrong, so the line the DJ actually said
        # rides along to be named rather than the subject being forbidden.
        import asyncio
        import types

        from call import comeback
        from call.air import OnAirGuard

        said = []

        class _Session:
            async def generate_reply(self, **kw):
                said.append(str(kw.get("instructions", "")))

        guard = OnAirGuard(types.SimpleNamespace(), {}, room=None)
        guard.aired_text = "Big shout to Dave from the call line."
        guard.last_dj_line = "That shoutout for Dave is going out now."
        asyncio.run(comeback.come_back(guard, _Session()))
        self.assertIn("going out now", said[0],
                      "the come-back cannot avoid a repeat it never saw")
        self.assertIn("don't say it again", said[0])

    def test_the_air_watch_remembers_the_djs_last_line(self):
        # Same shape as the door's watch, and for the same reason: only the
        # event knows what was said, and by the time the come-back runs the
        # turn is long gone. The caller's own turns must not be mistaken for
        # the DJ's, or the come-back would avoid repeating the CALLER.
        import types

        from call import comeback

        handlers = {}

        class _Session:
            def on(self, name, fn):
                handlers[name] = fn

        guard = types.SimpleNamespace(last_dj_line="")
        comeback.attach_air_watch(_Session(), guard)
        fire = handlers["conversation_item_added"]

        fire(types.SimpleNamespace(item=types.SimpleNamespace(
            role="user", text_content="play me something loud")))
        self.assertEqual(guard.last_dj_line, "")
        fire(types.SimpleNamespace(item=types.SimpleNamespace(
            role="assistant", text_content="Lining that up for you now.")))
        self.assertEqual(guard.last_dj_line, "Lining that up for you now.")

    def test_the_djs_own_action_gets_a_comeback_line_too(self):
        # mark_on_air() sets `on_air` directly, so the watch loop never saw a
        # busy edge for the DJ's own announcements — `stepped_away` stayed
        # False and the DJ came back from its own segment saying nothing,
        # waiting on a caller it had told to hold. Reported 2026-08-08.
        import asyncio

        from call.air import OnAirGuard

        said = []

        class _Session:
            async def generate_reply(self, **kw):
                said.append(str(kw.get("instructions", "")))

        class _Station:
            # The log never notices our action — only the assumed window holds.
            async def on_air_speech(self):
                return None

        async def _run():
            guard = OnAirGuard(
                _Station(), {"avoid_on_air_overlap": True, "on_air_quiet_secs": 30})
            guard.POLL_SECS = 0.01
            # This test is about the come-back LINE, so every wall-clock
            # delay in the guard is compressed. Both of these are real
            # seconds in production and neither is what is being checked:
            # the settle window that rides out a banter break, and the
            # handoff lag that mark_on_air adds to its assumed-busy window.
            # Leaving the lag at its 2s constant made the air busy for 2.05s
            # of a 3s budget and the test failed on a slower CI runner while
            # passing here (2026-08-12).
            guard.SETTLE_SECS = 0.01
            guard.lag_secs = 0.0
            # …and the duck's close, 4.5 real seconds from 0.10.113. Left at
            # its true value it outlasts this test's whole budget, which is
            # how it passed on Windows (coarse sleep granularity) and failed
            # on the CI runner.
            guard.duck_pad = 0.01
            # The loop's own heartbeat, too — at its real 1s the test got
            # about three chances inside its budget, which is not a margin.
            guard.PUSH_TICK = 0.01
            task = asyncio.create_task(guard.watch(_Session()))
            await asyncio.sleep(0.03)
            guard.mark_on_air(seconds=0.05, spoken="Big shout to Dave.")
            for _ in range(300):
                await asyncio.sleep(0.01)
                if said:
                    break
            task.cancel()

        asyncio.run(_run())
        self.assertTrue(said, "the DJ came back from its own action silently")
        self.assertIn("back", said[0].lower())
        self.assertIn("Dave", said[0])


class TestTheStationClientOutlivesTheShutdownWork(unittest.TestCase):
    """The SDK runs shutdown callbacks CONCURRENTLY, and station.aclose was
    registered as its own — so the first tape soak (callin-ol-cd4e089a2eb0,
    2026-08-19) had the playout's intro AND outro die on a closed client
    while all nine clips aired fine over telnet. The client must close in
    _on_shutdown's own finally, after the relay's brackets and the handoff
    have spoken through it."""

    def test_aclose_runs_after_the_shutdown_work_not_beside_it(self):
        from tests.support import AGENT_WORKER

        src = (AGENT_WORKER / "call" / "session.py").read_text(
            encoding="utf-8")
        self.assertNotIn(
            "add_shutdown_callback(self.station.aclose)", src,
            "station.aclose is racing the shutdown work again")
        tail = src.split("async def _on_shutdown", 1)[1]
        head = tail[:tail.index("async def _shutdown_work")]
        self.assertIn("finally:", head)
        self.assertIn("await self.station.aclose()", head)


async def _noop_async(*a, **k):
    return None


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

    def test_an_end_from_before_our_action_does_not_close_our_hold(self):
        """The 2026-08-15 overlap, and the reason it sounded like the ducking
        was inverted.

        The DJ announces something, we open a hold for our own 28.5 seconds of
        it, and 0.2 seconds later a voice.end arrives — for the utterance that
        was ALREADY playing, because ours has not aired yet. That end used to
        wipe the hold, so the DJ said its back-from-air line and then talked
        straight over the announcement for the next half minute. Read off the
        operator's own box:

            17:46:13  hold opened  we put something on air  forSecs=28.5
            17:46:13  hold closed  voice.end                heldSecs=0.2
            17:46:13  dj said      back-from-air line
        """
        import time

        guard = self._guard()
        guard.mark_on_air(28.5, spoken="A big hello going out to Amelia")
        self.assertTrue(guard.on_air, "our own action did not open a hold")

        # The end of what was playing BEFORE we sent ours.
        self.assertTrue(guard._stale_end({"at": time.time() - 2}))
        # …and the end of ours, once the station has actually aired it.
        self.assertFalse(guard._stale_end({"at": time.time() + 30}))
        # A hold nobody opened is not protected — an ordinary voice.end after
        # an ordinary busy spell still closes the gate on the spot.
        fresh = self._guard()
        self.assertFalse(fresh._stale_end({"at": time.time() - 2}))

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

    def test_the_lag_rides_the_holds_tail(self):
        # Handoff-stamped evidence — the log poll, and pre-1.8 pushes — is
        # stamped when the audio is handed over, and the audible link runs a
        # couple of seconds behind it. Without the lag the hold released
        # while the link's last words were still airing (the operator's
        # "ends early", 0.10.69). A constant since 0.10.97, not a setting.
        from call.air import OnAirGuard

        guard = self._guard()
        lag = OnAirGuard.HANDOFF_LAG_SECS
        # A no-words entry holds quiet_secs (30) plus the lag.
        self.assertTrue(guard._log_says_busy((30.0 + lag - 1, "")))
        # Since 0.10.129 the window is [lag - handover, lag + words + pad]
        # in CALLER time, so the far edge moved out by the pad.
        self.assertFalse(
            guard._log_says_busy((30.0 + lag + guard.duck_pad + 1, "")))

    def test_a_gap_inside_a_banter_break_does_not_return_the_caller(self):
        # A banter break is several utterances back to back. Each voice.end
        # used to reopen the gate, so one break cost the caller a come-back
        # line and another hand-over line three times over (operator-reported
        # from a real call, 2026-08-12).
        #
        # That was fixed with a blanket 2s pad on every hold, which taxed every
        # link that HAD finished in order to bridge the ones that had not, and
        # only ever bridged gaps shorter than itself. Since 0.10.125 the return
        # is a cancellable task: the loop keeps watching while the DJ comes
        # back, and the next utterance cancels the return and leaves the hold
        # up. Any length of gap, and a finished link pays nothing.
        import asyncio

        from call.air import OnAirGuard

        guard = self._guard()

        async def scenario():
            started = asyncio.Event()
            cancelled = asyncio.Event()

            async def returning():
                started.set()
                try:
                    await asyncio.sleep(30)      # the DJ, mid-sentence
                except asyncio.CancelledError:
                    cancelled.set()
                    raise

            guard._comeback = asyncio.create_task(returning())
            await started.wait()
            # The station speaks again: this is the SAME break.
            same = guard._cancel_comeback()
            await asyncio.sleep(0)
            return same, cancelled.is_set()

        same, was_cancelled = asyncio.run(scenario())
        self.assertTrue(same, "a mid-return voice must read as the same break")
        self.assertTrue(was_cancelled, "the return kept talking over the air")
        # And the hand-over line is not said a second time — the caller was
        # never told the hold was over.
        self.assertIsNone(guard._comeback)


    def test_a_queued_voice_while_holding_bridges_the_break(self):
        # The bridge, and it is the station's own warning rather than a pad.
        # Measured on a real call 2026-08-13: two voice.queued landed while the
        # caller was already on hold, both forecast further out than the
        # hand-over window, so the verdict was None, the estimate ran out and
        # the line was RELEASED — then re-held five seconds later. One
        # continuous break cost the caller a return line and a second
        # hand-over line.
        import time

        from call.air import OnAirGuard

        guard = self._guard()
        now = time.time()
        # Forecast well beyond the hand-over window.
        entry = {"at": now, "v": 2, "phase": "queued", "voiceId": "b2",
                 "text": "the next part", "durMs": 6000,
                 "airAt": now + guard.handover_secs + 30}

        guard.on_air = False
        self.assertIsNone(OnAirGuard._push_verdict(guard, entry, now),
                          "a distant forecast must not gag a quiet line")

        guard.on_air = True
        verdict = OnAirGuard._push_verdict(guard, entry, now)
        self.assertIsNotNone(verdict, "the break was dropped mid-way again")
        self.assertEqual(verdict[0], "busy")

    def test_a_finished_link_pays_no_settle_tax(self):
        # The other half: with nothing in flight there is nothing to cancel,
        # so a link that really has finished releases on its own timing.
        from call.air import OnAirGuard

        guard = self._guard()
        guard._comeback = None
        self.assertFalse(guard._cancel_comeback())
        self.assertEqual(OnAirGuard.SETTLE_SECS, 0.0,
                         "the blanket pad is back; see comeback.py")

    def test_the_settle_window_cannot_invent_a_busy_spell(self):
        # It only ever EXTENDS one: a caller who dialled into quiet air must
        # not be held by a window that never had a voice behind it.
        import time

        guard = self._guard()
        guard.on_air = False
        self.assertFalse(guard._settle(False, time.time()))

    def test_the_handoff_lag_is_no_longer_an_operators_dial(self):
        # It was one until 0.10.97 and should not have been: nobody can
        # measure their mixer's handoff gap from the panel, and it sat in the
        # middle of the ducking list looking like a dial worth turning. A
        # stale value left in someone's settings.json must not resurrect it.
        from call.air import OnAirGuard

        import settings as settings_store

        self.assertNotIn("on_air_lag_secs", settings_store.FIELDS)
        self.assertNotIn("on_air_lag_secs", settings_store.SCHEMA)
        guard = self._guard(on_air_lag_secs=99)
        self.assertEqual(guard.lag_secs, OnAirGuard.HANDOFF_LAG_SECS)

    def test_our_own_action_holds_through_the_lag_too(self):
        import time

        from call.air import OnAirGuard

        guard = self._guard()
        guard.mark_on_air(10)
        self.assertGreaterEqual(guard._assumed_until,
                                time.time() + 9 + OnAirGuard.HANDOFF_LAG_SECS)

    def test_our_own_announcement_waits_for_the_caller_to_hear_it(self):
        # The one hold that gets no push is the one for our OWN action, and it
        # was the only one the caller's lag never shifted. The caller is
        # `caller_lag` seconds behind the live edge, so a shoutout sent now
        # does not START in their ear for that long — sizing the hold as words
        # + pad brings the DJ back while the caller is still waiting to hear a
        # word of it.
        #
        # Heard on the call of 2026-08-16 (room 72de3b8893fe), and the record
        # shows the whole shape: a 3.0s shoutout opened a 7.5s hold, the hold
        # closed on "the estimate ran out", the DJ said "I just sent that
        # shoutout for Marcus" — and the guard then had to open a SECOND hold
        # for another 12.0s once the push arrived carrying the real 22. The
        # caller heard the DJ announce the shoutout roughly seventeen seconds
        # before they heard the shoutout.
        import time

        guard = self._guard()
        guard._last_buf = 22.0                   # what this station reports
        now = time.time()
        guard.mark_on_air(3)
        # The caller starts hearing it at now+22 and stops at now+25; coming
        # back before that talks over it in the only ear that matters.
        self.assertGreaterEqual(
            guard._assumed_until, now + 22 + 3,
            "the DJ comes back before the caller has heard the announcement")

    def test_the_first_announcement_knows_the_stations_buffer(self):
        # `_last_buf` was only ever written by an incoming push, so the FIRST
        # thing a call puts on air sized its hold from the 2s fallback even
        # when the station had been saying 22 all along. On the 2026-08-16
        # call the push carrying bufSecs=22.0 and the hold opened with
        # bufSecs=2.0 are three milliseconds apart in the same timeline.
        #
        # The web process already writes the last verified push to disk for
        # exactly this kind of cross-process read, so there is no new state
        # here — the buffer is a property of the station's Icecast config,
        # not of this call, and a call that has seen no push yet should still
        # start from the last thing the station said.
        import json
        import os
        import tempfile
        import time

        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "hook-air.json")
            # Restore the suite-wide redirect, never pop it away — see the
            # save/restore note in TestTheGateDoesNotChatter.
            prev = os.environ.get("CALLIN_HOOK_AIR_PATH")
            os.environ["CALLIN_HOOK_AIR_PATH"] = p
            try:
                with open(p, "w", encoding="utf-8") as f:
                    json.dump({"at": time.time() - 30, "v": 2,
                               "phase": "clear", "bufSecs": 22.0}, f)
                guard = self._guard()
                self.assertEqual(
                    guard.caller_lag(), 22.0,
                    "the first hold of a call still assumes the 2s fallback")
            finally:
                if prev is None:
                    os.environ.pop("CALLIN_HOOK_AIR_PATH", None)
                else:
                    os.environ["CALLIN_HOOK_AIR_PATH"] = prev

    def test_the_push_file_reads_back_as_evidence(self):
        # The web process writes the last verified voice push; the guard reads
        # it raw and judges it with _push_verdict. An absent file is no
        # evidence at all, and a `speaking` entry proves the air busy.
        #
        # The pre-1.8 handoff-stamped shape (no "v", no phase) lost its branch
        # at 0.97.3 — the operator's call, "assume everything is up to date".
        # It proves nothing here now and falls through to the poll, which reads
        # the same station log that branch did.
        import json
        import os
        import tempfile
        import time

        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "hook-air.json")
            # Restore the suite-wide redirect, never pop it away — see the
            # save/restore note in TestTheGateDoesNotChatter.
            prev = os.environ.get("CALLIN_HOOK_AIR_PATH")
            os.environ["CALLIN_HOOK_AIR_PATH"] = p
            try:
                guard = self._guard()
                self.assertIsNone(guard._pushed_state())
                with open(p, "w", encoding="utf-8") as f:
                    json.dump({"at": time.time() - 3, "v": 2,
                               "phase": "speaking", "durMs": 9000,
                               "text": "Back after this."}, f)
                verdict = guard._push_verdict(guard._pushed_state(), time.time())
                self.assertEqual(verdict[0], "busy")
                self.assertEqual(verdict[1], "Back after this.")
                # And the retired generation: read back fine, judged as nothing.
                with open(p, "w", encoding="utf-8") as f:
                    json.dump({"at": time.time() - 3,
                               "text": "Back after this."}, f)
                self.assertIsNotNone(guard._pushed_state())
                self.assertIsNone(
                    guard._push_verdict(guard._pushed_state(), time.time()),
                    "a pre-1.8 push entry is still being judged")
            finally:
                if prev is None:
                    os.environ.pop("CALLIN_HOOK_AIR_PATH", None)
                else:
                    os.environ["CALLIN_HOOK_AIR_PATH"] = prev

    def test_a_forecast_holds_only_inside_the_handover_window(self):
        # SUB/WAVE 1.8's voice.queued can warn many seconds ahead. The whole
        # point of the warning is that the call keeps flowing through the
        # queue wait and hands over just before the voice lands — a 20s
        # warning must NOT gag the call for 20 seconds (the operator's ask).
        import time

        guard = self._guard(on_air_handover_secs=5)
        now = time.time()
        far = {"at": now, "v": 2, "phase": "queued", "voiceId": "v1",
               "text": "coming up", "durMs": 6000, "airAt": now + 20}
        self.assertIsNone(guard._push_verdict(far, now))
        # Inside the window in CALLER time: a clip airing in 4s is heard in
        # 4 + lag, so the hand-over is still lag seconds away. This is the
        # "stop ducking early" fix — the operator timed a hold that opened
        # seventeen seconds before the caller heard anything.
        near = dict(far, airAt=now + 4)
        self.assertIsNone(guard._push_verdict(near, now))
        verdict = guard._push_verdict(near, now + guard.caller_lag())
        self.assertEqual(verdict[0], "busy")
        self.assertIn("Hold that thought", verdict[2])
        # …and the hold releases once the forecast clip has played out AND
        # the duck's close has run — 6s of clip plus DUCK_PAD_SECS.
        from call.air import DUCK_PAD_SECS

        # Every bound is in CALLER time since 0.10.129 — airAt is the encoder's
        # instant and they hear it caller_lag later, so the whole window slides
        # rather than the close being padded. With no bufSecs on these entries
        # the lag falls back to HANDOFF_LAG_SECS.
        lag = guard.caller_lag()
        self.assertIsNone(guard._push_verdict(
            dict(far, airAt=now - (7 + DUCK_PAD_SECS + lag)), now))
        # Still held while the pad is running: releasing on the last syllable
        # puts the DJ back over the tail of its own link.
        self.assertEqual(
            guard._push_verdict(dict(far, airAt=now - (7 + lag)), now)[0],
            "busy")

    def test_measured_speech_is_held_for_its_length_plus_the_buffer(self):
        # voice.start is stamped at AIR time with the clip's measured length,
        # so the window is the clip — PLUS the caller's distance from the
        # live edge. Until 0.10.108 this test said "with no lag" and the code
        # obliged, which is precisely why the call DJ came back over the top
        # of every link: the encoder had finished, the caller had not.
        import time

        guard = self._guard()
        now = time.time()
        speaking = {"at": now - 5, "v": 2, "phase": "speaking", "bufSecs": 4.0,
                    "voiceId": "v2", "text": "x", "durMs": 6000}
        self.assertEqual(guard._push_verdict(speaking, now)[0], "busy")
        # 6s of clip + 4s of buffer: 8s in is still the caller's DJ talking,
        # which the old rule called clear.
        self.assertEqual(
            guard._push_verdict(dict(speaking, at=now - 8), now)[0], "busy")
        # Clear only once the clip AND the buffer AND the pad have run: the
        # caller starts hearing it 4s after the encoder did, so 12s in they
        # are still two seconds from the end of it.
        self.assertEqual(
            guard._push_verdict(dict(speaking, at=now - 12), now)[0], "busy")
        self.assertIsNone(guard._push_verdict(dict(speaking, at=now - 20), now))

    def test_a_measured_end_reads_as_clear_once_the_buffer_has_drained(self):
        # voice.end is still the one push that can prove the air QUIET — the
        # poll's word-sized estimate has no idea a link ran short. But it
        # proves it at the ENCODER, and the caller is behind that, so it
        # cannot mean "clear" until their buffer has run out.
        import time

        guard = self._guard()
        now = time.time()
        ended = {"at": now, "v": 2, "phase": "clear", "bufSecs": 5.0,
                 "voiceId": "v3", "text": ""}
        # 5s of buffer, then the pad: at +6 the caller is still hearing it.
        self.assertIsNone(guard._push_verdict(ended, now + 1))
        self.assertIsNone(guard._push_verdict(ended, now + 6))
        self.assertEqual(guard._push_verdict(ended, now + 11)[0], "clear")

    def test_the_hold_is_sized_to_what_the_station_is_saying(self):
        # One fixed number either reopened the gate while a minute-long
        # segment was still mid-delivery (the caller heard the DJ talk over
        # its own broadcast) or gagged the call for half a minute over a
        # one-line station ID. Both were heard on real calls, 2026-08-08.
        guard = self._guard()
        a_minute_of_words = " ".join(["word"] * 120)
        self.assertTrue(guard._log_says_busy((40.0, a_minute_of_words)))
        self.assertFalse(guard._log_says_busy((40.0, "Quick station ID.")))
        # No words at all falls back to on_air_quiet_secs (30 here), plus the
        # handoff lag that rides every poll-shaped verdict's tail.
        self.assertTrue(guard._log_says_busy((20.0, "")))
        self.assertFalse(guard._log_says_busy(
            (31.0 + guard.caller_lag() + guard.duck_pad, "")))
        self.assertFalse(guard._log_says_busy(None))

    def test_dead_air_is_worse_than_an_overlap(self):
        # If the station has been "speaking" for longer than any real link,
        # the log is stale — let the call carry on rather than sit in silence.
        import asyncio

        guard = self._guard()
        guard.mark_on_air(600)
        waited = asyncio.run(guard.wait_until_clear(timeout=0.05))
        # The claim is that the guard WAITED and then gave up at the ceiling
        # — not that Windows' timer honours 50ms under a loaded suite (it
        # returned 48.7ms once and 38.9ms the next week). A floor well above
        # zero proves the wait; the sub-second ceiling proves the giving up.
        self.assertGreaterEqual(waited, 0.02)
        self.assertLess(waited, 1.0)
        self.assertTrue(guard._clear.is_set(), "the caller was left in silence")

    def test_an_unconfirmed_action_holds_until_the_log_shows_it(self):
        # The Ash call, 2026-08-09 (room 6d2fa6e55de3): an announce came back
        # "slow to confirm", the sized hold ran from the tool's RETURN, and
        # the station aired the delivery after it expired — over the DJ's
        # next line. Sent-but-unconfirmed now waits for the log, not a guess.
        guard = self._guard()
        guard.mark_pending_air("hello out there")
        self.assertFalse(guard._clear.is_set())
        # A clean poll that shows nothing yet: still waiting.
        self.assertTrue(guard._assess(None, poll_failed=False))
        # The delivery reaching the log resolves the pending state, and from
        # there the words size the hold like any other busy spell.
        self.assertTrue(guard._assess((2.0, " ".join(["word"] * 60)), False))
        self.assertEqual(guard._pending_until, 0.0)
        self.assertFalse(guard._assess((999.0, "Quick station ID."), False))

    def test_a_failing_poll_holds_the_current_state(self):
        # A read that timed out cannot MOVE the gate: it holds whatever the last
        # clean read said. Air BUSY stays busy (the on-air DJ is most likely
        # still mid-link — this is what stops the Dawn/Ash overlap, where
        # "assume clear" doubled the voice). Air CLEAR stays clear (so a
        # quiet-but-slow station under congestion does NOT gag every reply — an
        # earlier version assumed busy on any failed read and added seconds to
        # every answer while the station struggled).
        guard = self._guard()
        # Clear to start: a failed poll leaves it clear.
        self.assertFalse(guard.on_air)
        self.assertFalse(guard._assess(None, poll_failed=True))
        # Now busy (our own action): a failed poll leaves it busy.
        guard.on_air = True
        self.assertTrue(guard._assess(None, poll_failed=True))
        # A clean read that shows nothing moves it back to clear.
        self.assertFalse(guard._assess(None, poll_failed=False))

    def test_the_pending_hold_expires_rather_than_gagging_the_call(self):
        import time as _time

        guard = self._guard()
        guard.mark_pending_air("hello")
        guard._pending_until = _time.time() - 0.1
        # A CLEAN read after the pending window releases the hold entirely — the
        # delivery never appeared, and the station is answering again.
        self.assertFalse(guard._assess(None, poll_failed=False))
        self.assertEqual(guard._pending_until, 0.0)
        # A still-FAILING poll after the pending window clears the pending
        # state and then holds the CURRENT gate (clear here) — it does not gag.
        guard.mark_pending_air("hello")
        guard._pending_until = _time.time() - 0.1
        guard.on_air = False
        self.assertFalse(guard._assess(None, poll_failed=True))
        self.assertEqual(guard._pending_until, 0.0)

    def _watch(self, answers, stop_when):
        import asyncio

        class _Station:
            def __init__(self):
                self.left = list(answers)

            async def on_air_speech(self):
                since = self.left.pop(0) if self.left else None
                # No words: the guard falls back to on_air_quiet_secs, which
                # keeps these scenarios about the edges rather than the hold.
                return None if since is None else (since, "")

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
        #
        # The "seconds since the log entry" must be inside the CALLER'S
        # audible window since 0.10.129: a one-second-old entry is not
        # something they can hear yet when the stream runs behind.
        session = self._watch([None, 6, 6, 6], stop_when=lambda s: s.said)
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


class TestTheBarReleaseEndsTheTurn(unittest.TestCase):
    """Push-to-talk's release used to only mute — the DJ then waited out its
    endpointing delay against a mic that was already shut, which a beta
    tester's side-by-side correctly called out. The widget announces the
    release (talkwave.turn-end) and the worker commits the turn — but only
    when the caller was actually mid-turn: committing silence would make
    the DJ answer nothing."""

    def _wire(self, user_state, raises=None, pacing=None):
        from call import lifecycle

        calls = []
        handlers = {}

        def commit(**kw):
            if raises:
                raise raises
            calls.append(kw)

        room = types.SimpleNamespace(on=lambda name, fn: handlers.update({name: fn}))
        ctx = types.SimpleNamespace(room=room)
        session = types.SimpleNamespace(user_state=user_state,
                                        commit_user_turn=commit)
        lifecycle.attach_turn_commit(ctx, session, pacing=pacing)
        return handlers["data_received"], calls

    def test_release_commits_only_a_turn_in_progress(self):
        packet = types.SimpleNamespace(topic="talkwave.turn-end")
        fire, calls = self._wire("speaking")
        fire(packet)
        self.assertEqual(1, len(calls))
        fire, calls = self._wire("listening")
        fire(packet)
        self.assertEqual([], calls)

    def test_other_topics_and_a_draining_session_stay_harmless(self):
        fire, calls = self._wire("speaking")
        fire(types.SimpleNamespace(topic="vm-beep"))
        self.assertEqual([], calls)
        # A session already closing raises RuntimeError; the handler shrugs
        # rather than letting one late release take the teardown down.
        fire, _ = self._wire("speaking", raises=RuntimeError("draining"))
        fire(types.SimpleNamespace(topic="talkwave.turn-end"))

    def test_a_committed_release_starts_the_pacing_wait(self):
        """The meter's blind spot on a held bar, closed at the commit.

        The hold claims the user turn and the SDK pins `user_state` while a
        claim is active, so the state transition the meter normally listens
        for never fires — four real PTT calls on 2026-08-18 wrote replyGap
        n=0 while tap-to-latch calls measured fine. The commit is the caller
        explicitly saying "your turn", which is the honest start of the wait.
        """
        from call.heard import HeardMeter

        m = HeardMeter()
        fire, calls = self._wire("speaking", pacing=m)
        fire(types.SimpleNamespace(topic="talkwave.turn-end"))
        self.assertEqual(1, len(calls))
        self.assertGreater(m._waiting_since, 0, "the wait started")
        m.dj_speaking()
        self.assertEqual(len(m.replies), 1,
                         "a held-bar turn finally produces a reply gap")

    def test_a_release_that_commits_nothing_measures_nothing(self):
        # No turn committed = no reply coming = nothing to time. Stamping
        # here would measure the idle ladder as if it were an answer.
        from call.heard import HeardMeter

        m = HeardMeter()
        fire, calls = self._wire("listening", pacing=m)
        fire(types.SimpleNamespace(topic="talkwave.turn-end"))
        self.assertEqual([], calls)
        self.assertEqual(m._waiting_since, 0.0)
        # And a draining session raising mid-commit stamps nothing either.
        m2 = HeardMeter()
        fire, _ = self._wire("speaking", raises=RuntimeError("draining"),
                             pacing=m2)
        fire(types.SimpleNamespace(topic="talkwave.turn-end"))
        self.assertEqual(m2._waiting_since, 0.0)

    def test_the_widget_announces_the_release(self):
        from tests.support import REPO

        js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertIn("talkwave.turn-end", js)
        # Only a real open-to-closed transition on a live call announces —
        # the voicemail machine has its own clock, and the initial
        # post-connect close is not a caller finishing a sentence.
        guard = js.split("talkwave.turn-end")[0][-700:]
        self.assertIn("wasOpen && !pttOpen && room && !vmCall", guard)


class TestCallReceiptCardsFollowTheLine(unittest.TestCase):
    """The action_cards setting reached the phone in 0.10.92 (it was
    chat-only before): "after" holds a tool's receipt card until the DJ's
    line commits, "before" publishes the moment the tool lands, "off"
    publishes nothing — and whatever the mode, the count and the taken list
    are untouched, so the record and the per-call ceiling never move with
    the furniture."""

    def _actions(self, mode):
        from call.actions import CallActions

        published = []

        async def _publish(payload, **kw):
            published.append(payload)

        room = types.SimpleNamespace(local_participant=types.SimpleNamespace(
            publish_data=_publish))
        return CallActions(5, room=room, mode=mode), published

    def test_after_holds_until_the_line_commits(self):
        async def _run():
            actions, published = self._actions("after")
            actions.note("request", "Donovan's Pub")
            await asyncio.sleep(0)
            self.assertEqual([], published,
                             "'after' must not publish before the DJ's line")
            actions.flush_cards()
            await asyncio.sleep(0)
            self.assertEqual(1, len(published))
            self.assertEqual(1, actions.count)
            # A second flush must not repeat the card.
            actions.flush_cards()
            await asyncio.sleep(0)
            self.assertEqual(1, len(published))
        asyncio.run(_run())

    def test_before_publishes_as_it_happens(self):
        async def _run():
            actions, published = self._actions("before")
            actions.note("request", "Donovan's Pub")
            await asyncio.sleep(0)
            self.assertEqual(1, len(published))
        asyncio.run(_run())

    def test_off_withholds_the_card_but_not_the_ledger(self):
        async def _run():
            actions, published = self._actions("off")
            actions.note("request", "Donovan's Pub")
            actions.flush_cards()
            await asyncio.sleep(0)
            self.assertEqual([], published)
            self.assertEqual(1, actions.count,
                             "off hides the furniture, never the ledger")
            self.assertEqual([("request", "Donovan's Pub")], actions.taken)
        asyncio.run(_run())

    def test_the_call_wires_the_setting_and_the_flush(self):
        # The mode default in CallActions is "before", so a missed wire would
        # silently keep the phone on the old order — pin both ends: the
        # session passes the setting in, and the lifecycle releases held
        # cards when a DJ line commits.
        import inspect

        from call import lifecycle
        import call.session as call_session

        src = inspect.getsource(call_session.CallSession.__init__)
        self.assertIn('cfg.get("action_cards")', src)
        wired = {}

        class _Session:
            def on(self, name, fn):
                wired[name] = fn

        flushed = []
        actions = types.SimpleNamespace(flush_cards=lambda: flushed.append(1))
        lifecycle.attach_card_flush(_Session(), actions)
        fire = wired["conversation_item_added"]
        fire(types.SimpleNamespace(item=types.SimpleNamespace(role="user")))
        self.assertEqual([], flushed, "a caller's line releases nothing")
        fire(types.SimpleNamespace(item=types.SimpleNamespace(role="assistant")))
        self.assertEqual([1], flushed)


class TestAPromisedActionActuallyHappens(unittest.TestCase):
    """"Let me have a look" and then nothing is the commonest broken call.

    Measured 2026-08-13 by driving the real brain through the triage sweep:
    of 33 turns where the DJ SPOKE a promise before acting, 30 emitted no
    tool call at all. The cause is our own conduct rule — "say what you're
    doing BEFORE you go quiet to do it" — which is right about dead air and
    wrong about these models, where narration and tool-calling compete for
    one turn and narration wins.

    The TEXT line has nudged for this since 0.10.65. The voice line, which is
    the primary surface, never did.
    """

    def _wire(self, record=None, actions=None):
        from call import promise_guard

        handlers, replies = {}, []

        class _Session:
            def on(self, name, fn):
                handlers[name] = fn

            async def generate_reply(self, **kw):
                replies.append(kw)

        promise_guard.attach_promise_guard(_Session(), record, actions)
        return handlers, replies

    @staticmethod
    def _said(text):
        return types.SimpleNamespace(
            item=types.SimpleNamespace(role="assistant", text_content=text))

    @staticmethod
    def _heard():
        return types.SimpleNamespace(is_final=True, transcript="play something")

    def test_a_promise_with_no_tool_call_gets_one_more_turn(self):
        async def go():
            handlers, replies = self._wire()
            handlers["user_input_transcribed"](self._heard())
            handlers["conversation_item_added"](self._said("Let me have a dig."))
            await asyncio.sleep(0.05)
            self.assertEqual(len(replies), 1)
            self.assertIn("call it NOW", replies[0]["user_input"])
            # And it must not tell the caller anything twice.
            self.assertIn("Do not say another word", replies[0]["user_input"])

        asyncio.run(go())

    def test_a_dj_that_actually_acted_is_left_alone(self):
        # The correct behaviour must never be interrupted: the model that
        # calls the tool and THEN speaks is the one we want.
        async def go():
            handlers, replies = self._wire()
            handlers["user_input_transcribed"](self._heard())
            handlers["function_tools_executed"](types.SimpleNamespace())
            handlers["conversation_item_added"](
                self._said("Let me have a look — right, got it, Africa by Toto."))
            await asyncio.sleep(0.05)
            self.assertEqual(replies, [])

        asyncio.run(go())

    def test_ordinary_conversation_is_not_nudged(self):
        async def go():
            handlers, replies = self._wire()
            handlers["user_input_transcribed"](self._heard())
            handlers["conversation_item_added"](
                self._said("That one always reminds me of a wet Tuesday."))
            await asyncio.sleep(0.05)
            self.assertEqual(replies, [])

        asyncio.run(go())

    def test_it_fires_at_most_once_per_caller_turn(self):
        # Otherwise a model that keeps promising loops against itself and the
        # caller never gets the floor back.
        async def go():
            handlers, replies = self._wire()
            handlers["user_input_transcribed"](self._heard())
            handlers["conversation_item_added"](self._said("Hold on a sec."))
            handlers["conversation_item_added"](self._said("Let me check."))
            await asyncio.sleep(0.05)
            self.assertEqual(len(replies), 1)
            # …and the next caller turn re-arms it.
            handlers["user_input_transcribed"](self._heard())
            handlers["conversation_item_added"](self._said("One moment."))
            await asyncio.sleep(0.05)
            self.assertEqual(len(replies), 2)

        asyncio.run(go())

    def test_the_record_says_why_the_extra_turn_happened(self):
        # An operator reading the transcript must not see a mystery turn.
        class _Record:
            def __init__(self):
                self.problems = []

            def problem(self, what):
                self.problems.append(what)

        async def go():
            rec = _Record()
            handlers, _ = self._wire(rec)
            handlers["user_input_transcribed"](self._heard())
            handlers["conversation_item_added"](self._said("I'll go and look."))
            await asyncio.sleep(0.05)
            self.assertTrue(rec.problems)
            self.assertIn("ran no tool", rec.problems[0])
            self.assertIn("proven tool routing", rec.problems[0])

        asyncio.run(go())

    def test_a_finished_claim_with_no_tool_call_is_caught_too(self):
        # The call this was found on (record ...084215, 2026-08-14): the caller
        # asked to change the DJ, the model pinned THE OVERLOOK, the caller said
        # "to duke", and the DJ answered this line with no tool call at all.
        # Cliff stayed on air and the caller was told Duke was coming. The
        # guard was watching for "let me" and never looked at the past tense.
        async def go():
            handlers, replies = self._wire()
            handlers["user_input_transcribed"](self._heard())
            handlers["conversation_item_added"](self._said(
                "I've got that queued up for you. Duke's show, The Granite, is "
                "on its way, and the station will be handing over the controls "
                "to him as soon as this track clears the deck."))
            await asyncio.sleep(0.05)
            self.assertEqual(len(replies), 1)
            self.assertIn("call it NOW", replies[0]["user_input"])
            # A claim is not a promise: the caller has already been told it is
            # done, so the repair is to make it true, and to own it if it is
            # not. Both halves are load-bearing.
            self.assertIn("it is NOT done", replies[0]["user_input"])
            self.assertIn("did not go through", replies[0]["user_input"])

        asyncio.run(go())

    def test_a_claim_that_really_did_act_is_left_alone(self):
        # The same sentence is CORRECT behaviour when the action landed, and it
        # is the commonest correct sentence on the line. Nudging it would cost a
        # turn on every successful action.
        from call.actions import CallActions

        async def go():
            actions = CallActions(9)
            handlers, replies = self._wire(actions=actions)
            handlers["user_input_transcribed"](self._heard())
            handlers["function_tools_executed"](types.SimpleNamespace())
            actions.note("takeover", "THE OVERLOOK")
            handlers["conversation_item_added"](self._said(
                "Got it. The switch is made — THE OVERLOOK is coming up once "
                "this record finishes."))
            await asyncio.sleep(0.05)
            self.assertEqual(replies, [])

        asyncio.run(go())

    def test_a_read_does_not_make_a_claim_true(self):
        """The hole this guard had until 0.10.146, found by the drill.

        `a cancel that comes too late`, one round in three, on the model the
        operator runs (2026-08-14): the DJ searched the library — a READ — and
        then said "That one's in. Got it lined up for you." Nothing was queued.
        The guard set tools_ran on ANY tool, so it saw a DJ that had done its
        job and stayed quiet. It is the Duke call with a search in front of it,
        and unlike the Duke call the caller has a receipt-shaped sentence to
        believe. The ledger is what tells the two apart: a search notes nothing.
        """
        from call.actions import CallActions

        async def go():
            actions = CallActions(9)           # nothing ever noted: a read only
            handlers, replies = self._wire(actions=actions)
            handlers["user_input_transcribed"](self._heard())
            handlers["function_tools_executed"](types.SimpleNamespace())
            handlers["conversation_item_added"](self._said(
                "That one's in. Got it lined up for you."))
            await asyncio.sleep(0.05)
            self.assertEqual(len(replies), 1,
                             "a search ran, nothing was queued, and the caller "
                             "was told it was — that has to be nudged")
            self.assertIn("it is NOT done", replies[0]["user_input"])

        asyncio.run(go())

    def test_a_promise_is_still_settled_by_any_tool_at_all(self):
        # The two rules are deliberately different. A promise is about DEAD AIR
        # — "let me have a dig" is honest the moment the DJ reaches for
        # anything, because something is now happening — so a read settles it.
        # Only a claim asks whether the thing was actually done.
        from call.actions import CallActions

        async def go():
            handlers, replies = self._wire(actions=CallActions(9))
            handlers["user_input_transcribed"](self._heard())
            handlers["function_tools_executed"](types.SimpleNamespace())
            handlers["conversation_item_added"](self._said(
                "Hold on, let me have a dig through the racks for you."))
            await asyncio.sleep(0.05)
            self.assertEqual(replies, [])

        asyncio.run(go())

    def test_an_action_from_an_earlier_turn_does_not_settle_this_one(self):
        # The ledger counts for the whole CALL and the question is per TURN, so
        # a caller's second request must not be excused by their first landing.
        from call.actions import CallActions

        async def go():
            actions = CallActions(9)
            handlers, replies = self._wire(actions=actions)
            handlers["user_input_transcribed"](self._heard())
            actions.note("request", "Africa")
            handlers["conversation_item_added"](self._said(
                "That's lined up — I've got it queued for you."))
            await asyncio.sleep(0.05)
            self.assertEqual(replies, [], "the first one really did land")

            handlers["user_input_transcribed"](self._heard())
            handlers["conversation_item_added"](self._said(
                "That's lined up too — I've got that one queued as well."))
            await asyncio.sleep(0.05)
            self.assertEqual(len(replies), 1,
                             "the second claim rode the first request's receipt")

        asyncio.run(go())

    def test_the_record_distinguishes_a_claim_from_a_promise(self):
        # A promise with no receipt is a dead line; a claim with no receipt is
        # something the caller cannot catch. The operator reading the record
        # should be able to tell which one happened.
        class _Record:
            def __init__(self):
                self.problems = []

            def problem(self, what):
                self.problems.append(what)

        async def go():
            rec = _Record()
            handlers, _ = self._wire(rec)
            handlers["user_input_transcribed"](self._heard())
            handlers["conversation_item_added"](
                self._said("I've got that queued up — it's on its way."))
            await asyncio.sleep(0.05)
            self.assertTrue(rec.problems)
            self.assertIn("ALREADY been done", rec.problems[0])

        asyncio.run(go())


class TestATurnTheCallerHasOvertakenIsDropped(unittest.TestCase):
    """"That's locked in!" answering "No, I don't want anything else."

    Room 113774ecedfa, 2026-08-16, and both turns carry the same second. The
    floor had already done its job — it serialised them — but serialising made
    the second one LATE, and a late repair is not merely delayed: it answers a
    moment nobody is in any more. Two collisions on that call, and this is what
    one of them sounded like.

    The judgement is the one MAX_WAIT_SECS already makes, on a better signal:
    the caller, rather than a clock.
    """

    def _floor(self):
        from call.floor import Floor

        return Floor()

    def test_a_turn_that_waited_through_the_caller_is_refused(self):
        import asyncio

        floor = self._floor()

        async def go():
            got = []
            async with floor.take("the first turn") as mine:
                got.append(mine)

                async def late():
                    async with floor.take("the promise nudge") as ok:
                        got.append(ok)

                task = asyncio.ensure_future(late())
                await asyncio.sleep(0.05)
                floor.caller_spoke()          # they moved on while it queued
            await task
            return got

        self.assertEqual([True, False], asyncio.run(go()))
        self.assertEqual(1, floor.stale)

    def test_a_turn_nobody_overtook_still_speaks(self):
        import asyncio

        floor = self._floor()

        async def go():
            got = []
            async with floor.take("the first turn") as mine:
                got.append(mine)

                async def late():
                    async with floor.take("the come-back") as ok:
                        got.append(ok)

                task = asyncio.ensure_future(late())
                await asyncio.sleep(0.05)
            await task
            return got

        self.assertEqual([True, True], asyncio.run(go()))
        self.assertEqual(0, floor.stale)

    def test_speech_from_BEFORE_the_turn_was_wanted_does_not_drop_it(self):
        # The whole point is "spoke while it queued". A caller who spoke first
        # is the reason the turn exists.
        import asyncio

        floor = self._floor()
        floor.caller_spoke()

        async def go():
            async with floor.take("the promise nudge") as mine:
                return mine

        self.assertTrue(asyncio.run(go()))
        self.assertEqual(0, floor.stale)

    def test_the_watch_only_counts_real_words(self):
        import types

        from call import floor as floor_mod

        floor = self._floor()
        handlers = {}
        floor_mod.attach_floor_watch(
            types.SimpleNamespace(on=lambda n, f: handlers.__setitem__(n, f)),
            floor)
        fire = handlers["user_input_transcribed"]
        fire(types.SimpleNamespace(is_final=True, transcript="   "))
        self.assertEqual(0.0, floor.last_caller_at)
        fire(types.SimpleNamespace(is_final=False, transcript="half a wo"))
        self.assertEqual(0.0, floor.last_caller_at)
        fire(types.SimpleNamespace(is_final=True, transcript="no, that's it"))
        self.assertGreater(floor.last_caller_at, 0.0)


class TestTwoOfTheDJsOwnTurnsNeverStartAtOnce(unittest.TestCase):
    """Nine things can make the DJ speak; three of them can start while another
    is already generating.

    The other six are already covered and reading them against each other is
    what showed it: the greeting is a one-shot at pickup, the late request match
    and the idle ladder both wait for `agent_state == "listening"` (false while
    anything is generating), and the hand-over line IS the air. That leaves the
    promise nudge, the come-back after a link, and the time-limit sign-off —
    each on its own clock, none aware of the others.

    A lock, and deliberately nothing more: deciding who SHOULD speak would be
    the director this stream has twice declined to build on the evidence.
    """

    def _floor(self):
        from call.floor import Floor

        return Floor()

    def test_the_second_turn_waits_rather_than_talking_over(self):
        async def go():
            floor = self._floor()
            order = []

            async def first():
                async with floor.take("first") as mine:
                    order.append(("first", mine))
                    await asyncio.sleep(0.05)
                    order.append(("first done", mine))

            async def second():
                await asyncio.sleep(0.01)
                async with floor.take("second") as mine:
                    order.append(("second", mine))

            await asyncio.gather(first(), second())
            self.assertEqual(
                [("first", True), ("first done", True), ("second", True)], order,
                "the second turn started before the first had finished")
            self.assertEqual(1, floor.collisions)

        asyncio.run(go())

    def test_an_uncontested_turn_notices_nothing(self):
        async def go():
            floor = self._floor()
            async with floor.take("only") as mine:
                self.assertTrue(mine)
            self.assertEqual(0, floor.collisions)
            self.assertEqual(0, floor.given_up)

        asyncio.run(go())

    def test_a_holder_that_waits_too_long_stays_quiet(self):
        # Being late is fine; arriving after the conversation has moved on is
        # not. False means "say nothing", never "say it anyway".
        from call import floor as floor_mod

        async def go():
            floor = self._floor()
            original, floor_mod.MAX_WAIT_SECS = floor_mod.MAX_WAIT_SECS, 0.05
            try:
                async with floor.take("holder"):
                    async with floor.take("latecomer") as mine:
                        self.assertFalse(mine)
            finally:
                floor_mod.MAX_WAIT_SECS = original
            self.assertEqual(1, floor.given_up)

        asyncio.run(go())

    def test_the_floor_is_released_even_when_the_turn_raises(self):
        # A lock that leaks on an exception would silence the DJ for the rest
        # of the call, which is far worse than the overlap it prevents.
        async def go():
            floor = self._floor()
            with self.assertRaises(RuntimeError):
                async with floor.take("doomed"):
                    raise RuntimeError("the provider fell over")
            async with floor.take("after") as mine:
                self.assertTrue(mine, "the floor was never given back")

        asyncio.run(go())

    def test_all_three_injectors_are_wired_to_it(self):
        import inspect

        from call import clocks, comeback, promise_guard
        import call.session as call_session

        for module in (promise_guard, comeback, clocks):
            self.assertIn("floor", inspect.getsource(module),
                          f"{module.__name__} can start a turn without asking")
        src = inspect.getsource(call_session.CallSession)
        self.assertIn("self.air.floor = self.floor", src,
                      "the come-back task is created inside the air guard, so "
                      "the guard is how it reaches the floor")

    def test_the_record_says_when_two_turns_collided(self):
        # A silent fix is one nobody can tell is load-bearing — or needless.
        import inspect

        from call import postmortem

        src = inspect.getsource(
            postmortem._note_if_two_turns_wanted_the_floor)
        self.assertIn("collisions", src)


class TestTheCallerIsNotShownTheDoorTwice(unittest.TestCase):
    """"A caller who asked for one song was shown the door three times on the
    way out" — the operator, and then the archive.

    Eight of the 162 DJ lines in the live archive end by asking whether the
    caller wants more, and not one of those callers had said they were
    finished: "I'll wait while it goes out" answered with "anything else you
    want to dig up while we're waiting?", and one call doing it three times
    while the caller talked about a friend having a rough week. `CLOSING` has
    four paragraphs against this and measured 1-in-3 on the closing set.

    The harm is the REPETITION, so the correction lands on the next turn rather
    than trying to unsay the line — see call/door.py for why the promise
    guard's shape does not transfer.
    """

    def _door(self):
        from call.door import Door

        return Door()

    def test_a_line_that_ends_by_asking_for_more_is_caught(self):
        door = self._door()
        for said in (
            "That's lined up — about ten minutes out. Anything else you want "
            "digging out while I'm in the racks?",
            "Sent that down to the booth. Anything else you're looking for "
            "tonight?",
            "Got it queued. Something else I can dig up for you?",
            "That's in. Are you all set?",
        ):
            door.dj_said(said)
            self.assertTrue(door.held, said)
            self.assertTrue(door.hint_for("yeah go on then"), said)

    def test_the_same_words_mid_line_are_ordinary_talk(self):
        # It is how a turn ENDS that shows someone the door. The words in the
        # middle of a sentence are just a DJ talking.
        door = self._door()
        for said in (
            "Anything else you fancy, just say — but first, this one's a "
            "belter and I want you to hear the intro.",
            "I'll keep an eye out for anything else by them tonight.",
            "That's queued up, about ten minutes out, right after the Waits.",
        ):
            door.dj_said(said)
            self.assertFalse(door.held, said)

    def test_a_caller_who_says_they_are_done_gets_the_question_honestly(self):
        # The conduct allows it ONCE, at the end — that is not the failure,
        # and correcting it there would be the opposite bug.
        door = self._door()
        for finished in ("that's everything, thanks", "no, that's me done",
                         "cheers, bye", "nothing else, take care"):
            door.dj_said("That's in. Anything else before I let you go?")
            self.assertEqual("", door.hint_for(finished), finished)

    def test_the_correction_is_consumed_not_left_hanging(self):
        # Holding the flag would steer a turn two turns downstream of the line
        # that earned it, which reads as the DJ being told off for nothing.
        door = self._door()
        door.dj_said("That's queued. Anything else you want?")
        self.assertTrue(door.hint_for("go on then"))
        self.assertEqual("", door.hint_for("what about some Fleetwood Mac"))

    def test_it_counts_how_often_it_had_to_step_in(self):
        # A silent fix is one nobody can tell is working. Once is ordinary;
        # three times is the prompt still pulling the other way.
        door = self._door()
        for _ in range(3):
            door.dj_said("Done. Anything else you need?")
            door.hint_for("yeah")
        self.assertEqual(3, door.corrections)

    def test_the_note_reaches_the_model_before_it_answers(self):
        # It has to land in the CONTEXT, on the SDK's own pre-reply hook — a
        # correction that arrives after the utterance can only add a line.
        from call.air import CallAgent, OnAirGuard
        from call.door import Door

        added = []

        class _Ctx:
            def add_message(self, role, content):
                added.append((role, content))

        door = Door()
        door.dj_said("That's queued. Anything else you want digging out?")
        guard = OnAirGuard(None, {"avoid_on_air_overlap": False})
        from call.state import ConversationState
        agent = CallAgent("instructions", guard,
                          ConversationState(door=door))
        asyncio.run(agent.on_user_turn_completed(
            _Ctx(), types.SimpleNamespace(text_content="go on then")))
        self.assertEqual(1, len(added), "the model was not told")
        role, content = added[0]
        self.assertEqual("system", role,
                         "a note from us must not be filed as the caller's words")
        self.assertIn("do not end this turn that way again", content.lower())

    def test_a_well_behaved_turn_costs_nothing(self):
        # The whole argument for a mechanism over standing prose: it is free on
        # every turn where the DJ behaved, which a paragraph can never be.
        from call.air import CallAgent, OnAirGuard
        from call.door import Door

        added = []

        class _Ctx:
            def add_message(self, role, content):
                added.append((role, content))

        door = Door()
        door.dj_said("That's lined up — right after the Waits, and there's a "
                     "live session on straight after that.")
        from call.state import ConversationState
        agent = CallAgent("instructions", OnAirGuard(
            None, {"avoid_on_air_overlap": False}),
            ConversationState(door=door))
        asyncio.run(agent.on_user_turn_completed(
            _Ctx(), types.SimpleNamespace(text_content="lovely")))
        self.assertEqual([], added)


class TestAFinishedCallStaysFinished(unittest.TestCase):
    """The two across-turn losses from the 2026-08-25 live harness call: the
    DJ said goodbye TWICE, and after an on-air hold interrupted the ended
    conversation it came back with "Alright, I'm back" to a caller who had
    already signed off — the call ran about a minute past its natural end.
    Every turn is judged alone, so no prompt sentence can hold "this call is
    over" across an interruption; call/arc.py owns that one fact, and these
    pin the fact surviving the two interruptions that lost it live."""

    def _arc(self):
        from call.arc import CallArc

        return CallArc()

    def test_one_goodbye_each_is_a_clean_ending_not_a_correction(self):
        arc = self._arc()
        self.assertEqual("", arc.hint_for("that's everything, thanks — bye!"))
        arc.dj_said("Take care of yourself — thanks for calling in.")
        self.assertEqual(0, arc.corrections)

    def test_a_second_farewell_is_steered_to_end_call(self):
        arc = self._arc()
        arc.hint_for("that's it from me, bye now")
        arc.dj_said("You take care now — goodbye!")
        note = arc.hint_for("bye!")
        self.assertIn("end_call", note)
        self.assertEqual(1, arc.corrections)

    def test_a_caller_who_changes_their_mind_reopens_the_call(self):
        # People say "that's everything" and then remember the thing they
        # actually rang about. A reopened call is a call, not a fault.
        arc = self._arc()
        arc.hint_for("that's everything, bye")
        arc.dj_said("Take care — goodbye!")
        self.assertEqual(
            "", arc.hint_for("oh wait, actually — got any Zeppelin?"))
        self.assertFalse(arc.ending)

    def test_a_mid_call_thats_it_from_the_dj_ends_nobodys_call(self):
        # SIGNALS_DONE is reused, not trusted alone: a DJ line can arm the
        # farewell flag spuriously ("that's it for the news"), and the GATE
        # is the protection — nothing fires until the CALLER has also said
        # goodbye, and their next real turn clears both flags.
        arc = self._arc()
        arc.dj_said("And that's it for the news — back to the records.")
        self.assertEqual("", arc.hint_for("nice, what's coming up next?"))
        self.assertFalse(arc.ending)

    def test_the_comeback_after_an_ended_call_signs_off_not_resumes(self):
        # The exact live shape: the announcement aired mid-goodbye, and the
        # come-back said "Alright, I'm back" to a caller who was gone.
        from call import comeback

        class _Sess:
            def __init__(self):
                self.instructions = ""

            async def generate_reply(self, instructions=""):
                self.instructions = instructions

        class _Guard:
            def __init__(self):
                self.aired_text = "A big hello to Marcus out there"
                self.last_dj_line = ""
                self.floor = None
                self.arc = None

        guard = _Guard()
        arc = self._arc()
        arc.hint_for("that's all from me, bye now")
        arc.dj_said("Goodbye — take care!")
        guard.arc = arc
        sess = _Sess()
        asyncio.run(comeback.come_back(guard, sess))
        self.assertIn("end_call", sess.instructions)
        self.assertNotIn("I'm back", sess.instructions)

        # And a call still in flight keeps the ordinary comeback.
        guard2 = _Guard()
        guard2.arc = self._arc()
        sess2 = _Sess()
        asyncio.run(comeback.come_back(guard2, sess2))
        self.assertIn("I'm back", sess2.instructions)

    def test_the_steer_reaches_the_model_on_the_reply_path(self):
        # Same insertion point and same filing rule as the door hint: a
        # system message, never words in the caller's mouth.
        from call.air import CallAgent, OnAirGuard
        from call.arc import CallArc

        added = []

        class _Ctx:
            def add_message(self, role, content):
                added.append((role, content))

        arc = CallArc()
        arc.hint_for("that's everything, bye")
        arc.dj_said("Take care now — goodbye!")
        guard = OnAirGuard(None, {"avoid_on_air_overlap": False})
        from call.state import ConversationState
        agent = CallAgent("instructions", guard,
                          ConversationState(arc=arc))
        asyncio.run(agent.on_user_turn_completed(
            _Ctx(), types.SimpleNamespace(text_content="bye then")))
        self.assertEqual(1, len(added))
        role, content = added[0]
        self.assertEqual("system", role)
        self.assertIn("end_call", content)


class TestOneStateObjectFeedsTheReplyPath(unittest.TestCase):
    """Move 1 of the conversation-engine convergence (MASTER-PLAN NORTH
    STAR): the reply path used to consult four guards by hand and the
    DJ-line event carried a watcher per guard. call/state.py now holds the
    standing order — stuck, then withheld, then door, then arc — and the
    fan-out. Nothing about any single guard changed; these pin the order and
    the plumbing, which are the only things that moved."""

    def test_the_standing_order_is_kept(self):
        from call.arc import CallArc
        from call.door import Door
        from call.state import ConversationState

        class _AlwaysFires:
            def __init__(self, note):
                self.note = note

            def hint_for(self, text):
                return self.note

        door = Door()
        door.dj_said("That's in. Anything else you want?")
        arc = CallArc()
        arc.hint_for("that's everything, bye")
        arc.dj_said("Take care — goodbye!")
        st = ConversationState(door=door, stuck=_AlwaysFires("[stuck]"),
                               withheld=_AlwaysFires("[withheld]"), arc=arc)
        notes = [n for _, n in st.hints_for("")]
        self.assertEqual(4, len(notes))
        self.assertEqual("[stuck]", notes[0])
        self.assertEqual("[withheld]", notes[1])
        self.assertIn("do not end this turn that way again", notes[2].lower())
        self.assertIn("end_call", notes[3])

    def test_one_watcher_feeds_every_line_reader(self):
        from call.arc import CallArc
        from call.door import Door
        from call.state import ConversationState

        st = ConversationState(door=Door(), arc=CallArc())
        st.dj_said("That's queued. Anything else you need?")
        self.assertTrue(st.door.held)
        st.dj_said("Take care now — goodbye!")
        self.assertTrue(st.arc.dj_farewell)

    def test_a_guardless_state_is_silent_and_cheap(self):
        from call.state import ConversationState

        st = ConversationState()
        st.dj_said("anything at all")
        self.assertEqual([], st.hints_for("anything at all"))


class TestEveryGeneratedTurnWaitsForTheBroadcast(unittest.TestCase):
    """Nine things can make the DJ speak, and they do not know about each other.

    The on-air hold hangs off `CallAgent.on_user_turn_completed`, which fires
    for CALLER turns only — so it covers the reply path and nothing else. Every
    other injector has had to remember to check the guard itself, and two of
    them did not: the promise nudge and the time-limit sign-off both generated
    straight over a live link. Found by reading the injectors against each other
    on 2026-08-14 (docs/the-call.md has the table).

    The nudge is the likelier of the two to be heard: it fires a second or so
    after the DJ says "let me have a dig", which is exactly when a queued link
    lands. Neither is reproduced as a live fault yet — this is the arithmetic
    being wrong in the same shape as the overlaps that were reproduced.
    """

    class _Guard:
        """Just enough OnAirGuard to answer "did you ask?"."""

        def __init__(self):
            self.asked = 0

        async def wait_until_clear(self, timeout=None):
            self.asked += 1
            return 0.0

    def test_the_promise_nudge_asks_before_it_speaks(self):
        from call import promise_guard

        async def go():
            handlers, replies = {}, []

            class _Session:
                def on(self, name, fn):
                    handlers[name] = fn

                async def generate_reply(self, **kw):
                    replies.append(kw)

            guard = self._Guard()
            promise_guard.attach_promise_guard(_Session(), None, None, air=guard)
            handlers["user_input_transcribed"](
                types.SimpleNamespace(is_final=True, transcript="play something"))
            handlers["conversation_item_added"](types.SimpleNamespace(
                item=types.SimpleNamespace(
                    role="assistant",
                    text_content="Hold on, let me dig that out for you.")))
            await asyncio.sleep(0.05)
            self.assertEqual(len(replies), 1, "the nudge did not fire at all")
            self.assertEqual(guard.asked, 1,
                             "the nudge generated without asking whether the "
                             "broadcast had the microphone")

        asyncio.run(go())

    def test_the_time_limit_signoff_asks_before_it_speaks(self):
        from call import lifecycle

        async def go():
            said = []
            shutdown = []

            class _Session:
                async def generate_reply(self, **kw):
                    said.append(kw)

                async def say(self, text, **kw):
                    said.append({"text": text})

            ctx = types.SimpleNamespace(
                add_shutdown_callback=lambda fn: None,
                shutdown=lambda reason="": shutdown.append(reason))
            guard = self._Guard()
            # A one-second limit, so the test does not wait out a real call.
            lifecycle.attach_time_limit(ctx, _Session(), {"max_call_seconds": 1},
                                        air=guard)
            await asyncio.sleep(1.4)
            self.assertTrue(said, "the sign-off never happened")
            self.assertEqual(guard.asked, 1,
                             "the sign-off generated without asking whether "
                             "the broadcast had the microphone")

        asyncio.run(go())

    def test_the_doc_names_every_module_that_can_speak(self):
        # The table in docs/the-call.md is the only place the nine are written
        # down together, and a tenth added quietly is how this happened twice.
        # test_docs holds the membership; this holds the SESSION's wiring, so
        # an injector that exists but is never handed the guard is caught here.
        import inspect

        import call.session as call_session

        src = inspect.getsource(call_session.CallSession._attach_behaviours)
        for wired in ("air=self.air", "attach_time_limit", "attach_idle_watch",
                      "attach_promise_guard"):
            self.assertIn(wired, src)
        # Both of the ones that used to skip the hold now take the guard by
        # name. Matched on the ARGUMENT rather than the whole call, so adding
        # another (the floor did) does not read as the guard going missing.
        limit = src.split("attach_time_limit(")[1].split(")")[0]
        self.assertIn("air=self.air", limit)


class TestTheWordsThatOweAReceipt(unittest.TestCase):
    """Which sentences the guard reads as owing a tool call, and which it lets by.

    Measured against every DJ line in the live archive on 2026-08-14 — 155 of them, across
    80 call and chat records. The finished-tense pattern matches three; two of those had
    genuinely run a tool (so the guard never fires on them) and the third is the Duke call.
    Nothing else in the corpus fires, which is the whole reason the pattern is two-part:
    a completion marker AND a station action, in one sentence.
    """

    def test_a_finished_claim_reads_as_a_claim(self):
        from promises import unbacked

        for said in ("I've got that queued up for you.",
                     "Got it. The switch is made — THE OVERLOOK is coming up.",
                     "Right, I've got that set up for you, it's lined up next.",
                     "Consider it done — that's added to the queue."):
            self.assertEqual("claim", unbacked(said), said)

    def test_a_line_that_both_promises_and_claims_is_treated_as_a_promise(self):
        # The softer nudge still gets the tool called, and telling a model it
        # claimed something it only offered to do invites an apology the caller
        # does not need.
        from promises import unbacked

        self.assertEqual("promise", unbacked("Got it, I'll queue that up now."))

    def test_ordinary_talk_about_the_schedule_owes_nothing(self):
        # These are the sentences that make a two-part pattern necessary. Each
        # carries half of it, and a one-part match would nudge on all of them —
        # spending a model turn, and inviting a tool call nobody asked for, in
        # the middle of ordinary conversation.
        from promises import unbacked

        for chatter in ("Got it — that's a fine choice for a wet Tuesday.",
                        "Coming up next is a bit of Billie Holiday.",
                        "That one's been on the air all week.",
                        "The Chieftains were on top form that year.",
                        "Done and dusted, that era of radio."):
            self.assertEqual("", unbacked(chatter), chatter)


class TestTheGreetingWaitsForTheOnAirDJ(unittest.TestCase):
    """Ringing in mid-link used to put two of the same voice on at once.

    Every other DJ turn has waited for clear air for versions; the greeting
    never did, because the watch loop's first pass was deliberately written to
    close the gate "without the greeting being cut off". That protected the
    greeting FROM the guard — so the caller was picked up straight over a live
    announcement and the audience heard both. Operator-reported, and
    reproducible by calling while the station is mid-link.
    """

    class _Air:
        def __init__(self, enabled=True, wait=0.0):
            self.enabled, self._wait, self.asked = enabled, wait, []

        async def wait_until_clear(self, timeout=None):
            self.asked.append(timeout)
            return self._wait

    class _Session:
        def __init__(self):
            self.replies = []

        async def generate_reply(self, **kw):
            self.replies.append(kw)

        async def say(self, *a, **k):
            pass

    def _greet(self, air, record=None):
        import asyncio

        from call import greeting

        s = self._Session()
        asyncio.run(greeting.greet(s, {}, record=record, air=air))
        return s

    def test_a_busy_broadcast_holds_the_greeting(self):
        from call.greeting import GREET_HOLD_SECS

        air = self._Air(wait=3.0)
        self._greet(air)
        self.assertEqual(air.asked, [GREET_HOLD_SECS])

    def test_the_hold_outlasts_the_callers_lag_but_not_the_ceiling(self):
        # The greet hold runs on CALLER time: a caller joining mid-link still
        # has their whole stream buffer of it to hear, so a cap under
        # MAX_CALLER_LAG times out on EVERY mid-link pickup by construction —
        # 12s did exactly that on room callin-o-643dc6d2993e (2026-08-18),
        # greeting 28s before the caller's copy of the link finished, while
        # the widget's hold chip was explaining the wait. The chip is also why
        # a longer cap is safe now: the wait is no longer unexplained silence.
        # MAX_HOLD stays above it — the mid-call ceiling is the outer bound.
        from call.air import OnAirGuard
        from call.greeting import GREET_HOLD_SECS

        self.assertGreater(GREET_HOLD_SECS, OnAirGuard.MAX_CALLER_LAG)
        self.assertLess(GREET_HOLD_SECS, OnAirGuard.MAX_HOLD)

    def test_the_buffer_cap_and_the_hold_ceilings_agree(self):
        # The receiver clamps every push to MAX_STREAM_BUFFER_SECS, and both
        # hold ceilings are sized against it — the 2026-08-23 upstream review
        # found the station's settings field now goes to 60, and traced why
        # following it is NOT one line: a buffer past the greet hold times
        # out every mid-link pickup by construction, and one past MAX_HOLD
        # guarantees the duck reopens mid-link. Whoever raises the cap raises
        # the ceilings with it, and this is the test that makes that a
        # decision instead of an accident.
        from call.air import OnAirGuard
        from call.air_timing import MAX_STREAM_BUFFER_SECS
        from call.greeting import GREET_HOLD_SECS

        self.assertGreaterEqual(GREET_HOLD_SECS, MAX_STREAM_BUFFER_SECS)
        self.assertGreater(OnAirGuard.MAX_HOLD, MAX_STREAM_BUFFER_SECS)

    def test_the_advertised_buffer_primes_a_cold_guard_only(self):
        # A cold worker has seen no voice push, so the first duck fell back
        # to the 2s handoff lag — ~20s early against a station really at 22.
        # /now-playing carries the advertised figure and prepare() already
        # reads it; priming must fill the blank and never outrank a push.
        from call.air import OnAirGuard
        from call.air_timing import MAX_STREAM_BUFFER_SECS

        g = OnAirGuard(None, {"avoid_on_air_overlap": False})
        g._last_buf = 0.0
        g.prime_buffer(22)
        self.assertEqual(g.stream_buffer(), 22.0)
        # A push has spoken — the advertised figure no longer applies.
        g._last_buf = 7.0
        g.prime_buffer(22)
        self.assertEqual(g.stream_buffer(), 7.0)
        # And the same clamp as the receiver's: a hostile or broken figure
        # cannot hold a caller silent for a minute.
        g2 = OnAirGuard(None, {"avoid_on_air_overlap": False})
        g2._last_buf = 0.0
        g2.prime_buffer(99999)
        self.assertLessEqual(g2.stream_buffer(), MAX_STREAM_BUFFER_SECS)

    def test_the_guard_being_off_costs_nothing(self):
        air = self._Air(enabled=False)
        s = self._greet(air)
        self.assertEqual(air.asked, [])
        self.assertEqual(len(s.replies), 1)

    def test_no_guard_at_all_still_greets(self):
        # scripted_call and the tests call greet() with no air at all.
        s = self._greet(None)
        self.assertEqual(len(s.replies), 1)

    def test_giving_up_on_the_hold_is_recorded(self):
        # Timing out means the greeting DID go out over the top, which is the
        # thing this exists to prevent — the operator should be able to find
        # it without listening to the call.
        import types

        from call.greeting import GREET_HOLD_SECS

        record = types.SimpleNamespace(
            data={"turns": [{"who": "dj", "text": "hi"}], "tools": []},
            problems=[])
        record.problem = record.problems.append
        self._greet(self._Air(wait=GREET_HOLD_SECS), record=record)
        self.assertTrue(record.problems)
        self.assertIn("still speaking", record.problems[0])

    def test_a_short_wait_is_not_worth_a_problem_line(self):
        import types

        record = types.SimpleNamespace(
            data={"turns": [{"who": "dj", "text": "hi"}], "tools": []},
            problems=[])
        record.problem = record.problems.append
        self._greet(self._Air(wait=2.0), record=record)
        self.assertEqual(record.problems, [])

    def test_the_gate_is_primed_from_the_push_file_at_construction(self):
        # The watch loop's first pass closes the gate for a mid-link dial-in,
        # but create_task does not run it synchronously and the fast pickup
        # (0.97.77) made the greeting quicker than the scheduler. Room
        # callin-o-643dc6d2993e (2026-08-18): the push file showed a 26.7s
        # link mid-air when the guard was built, the loop opened the hold at
        # +2.9s — and the greeting had read the still-open gate at +2.7s, so
        # the DJ greeted over a broadcast the widget was telling the caller
        # to hold for. The same evidence must close the gate at construction.
        import asyncio
        import json
        import os
        import tempfile
        import time

        from call.air import OnAirGuard

        class _Station:
            async def on_air_speech(self):
                return None

        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "hook-air.json")
            # Restore the suite-wide redirect, never pop it away — see the
            # save/restore note in TestTheGateDoesNotChatter.
            prev = os.environ.get("CALLIN_HOOK_AIR_PATH")
            os.environ["CALLIN_HOOK_AIR_PATH"] = p
            try:
                # The link started at the encoder 25s ago, runs 26.7s, and
                # the caller is 22s behind — so they are 3s into hearing it
                # RIGHT NOW. (A push only seconds old is different: the
                # caller has not started hearing it yet, and outside the
                # hand-over window the verdict rightly calls that not-busy —
                # which is also why this entry is older than the buffer.)
                with open(p, "w", encoding="utf-8") as f:
                    json.dump({"at": time.time() - 25, "v": 2,
                               "phase": "speaking", "durMs": 26700,
                               "bufSecs": 22.0, "text": "Mid-link."}, f)
                guard = OnAirGuard(_Station(),
                                   {"avoid_on_air_overlap": True})
                self.assertTrue(
                    guard.on_air,
                    "a link mid-air at construction left the gate open")
                waited = asyncio.run(guard.wait_until_clear(timeout=0.1))
                self.assertGreater(
                    waited, 0.05,
                    "the greeting would not have been held")
                # And the guard being off skips the priming with the rest.
                off = OnAirGuard(_Station(), {})
                self.assertFalse(off.on_air)
            finally:
                if prev is None:
                    os.environ.pop("CALLIN_HOOK_AIR_PATH", None)
                else:
                    os.environ["CALLIN_HOOK_AIR_PATH"] = prev

    def test_a_wrongly_primed_gate_is_reopened_by_the_first_look(self):
        # The priming above reads one file with no poll behind it, so it can
        # be wrong — a voice.end the web process wrote moments later, a stale
        # entry. The watch loop's first pass must then take the busy-to-clear
        # edge and open the gate, not leave the caller held on a hunch.
        import asyncio
        import json
        import os
        import tempfile
        import time

        from call.air import OnAirGuard

        class _Station:
            async def on_air_speech(self):
                return None

        class _Session:
            def interrupt(self):
                pass

            def say(self, *a, **k):
                pass

        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "hook-air.json")
            # Restore the suite-wide redirect, never pop it away — see the
            # save/restore note in TestTheGateDoesNotChatter.
            prev = os.environ.get("CALLIN_HOOK_AIR_PATH")
            os.environ["CALLIN_HOOK_AIR_PATH"] = p
            try:
                # Audible in the caller's ears now — same arithmetic as the
                # priming test above.
                with open(p, "w", encoding="utf-8") as f:
                    json.dump({"at": time.time() - 25, "v": 2,
                               "phase": "speaking", "durMs": 26700,
                               "bufSecs": 22.0, "text": "Mid-link."}, f)
                guard = OnAirGuard(_Station(),
                                   {"avoid_on_air_overlap": True})
                self.assertTrue(guard.on_air)
                # The station stopped talking before the loop's first look.
                with open(p, "w", encoding="utf-8") as f:
                    json.dump({"at": time.time(), "v": 2, "phase": "clear",
                               "voiceId": "x", "bufSecs": 22.0}, f)

                async def _run():
                    guard.PUSH_TICK = 0.01
                    guard.POLL_SECS = 0.01
                    guard.duck_pad = 0.01
                    task = asyncio.create_task(guard.watch(_Session()))
                    for _ in range(200):
                        await asyncio.sleep(0.01)
                        if not guard.on_air:
                            break
                    task.cancel()
                    return guard.on_air

                self.assertFalse(
                    asyncio.run(_run()),
                    "the first look did not reopen a wrongly primed gate")
            finally:
                if prev is None:
                    os.environ.pop("CALLIN_HOOK_AIR_PATH", None)
                else:
                    os.environ["CALLIN_HOOK_AIR_PATH"] = prev


class TestAHoldAlwaysEnds(unittest.TestCase):
    """A hold nobody can end is worse than an overlap.

    `mark_pending_air` shuts the gate until the station's log shows an action
    it may never log. That was tolerable while it only meant the DJ stayed
    quiet — from 0.10.107 the caller's microphone is disabled too, and a real
    call sat muted until it was abandoned: "he went off air to say it but
    never released the microphone back to me. that's a bug that kills it."
    """

    def _guard(self):
        from call.air import DUCK_PAD_SECS, OnAirGuard

        g = OnAirGuard.__new__(OnAirGuard)
        g.quiet_secs = 30.0
        g.lag_secs = OnAirGuard.HANDOFF_LAG_SECS
        g.handover_secs = 0.0
        g._last_buf = 0.0
        g._assumed_until = 0.0
        g._pending_until = 0.0
        g.on_air = False
        # __new__ skips __init__, so the duck's close has to be set by hand.
        g.duck_pad = OnAirGuard.__dict__.get("duck_pad", DUCK_PAD_SECS)
        return g

    def test_the_unconfirmed_window_is_survivable(self):
        # 90s of a muted caller is not a hold, it is a dropped call that has
        # not admitted it yet.
        from call.air import OnAirGuard

        self.assertLessEqual(OnAirGuard.PENDING_CEILING, 30.0)

    def test_an_expired_pending_window_reopens_the_gate(self):
        import time

        g = self._guard()
        g._pending_until = time.time() - 1        # ceiling already passed
        self.assertFalse(g._assess(None, poll_failed=False))

    def test_our_own_action_outlasts_its_own_announcement(self):
        # The DJ said "right, I'm back" and its own announcement started a
        # beat later, over the top of it: the hold has to outlast the speech.
        #
        # The lag is BACK in this sizing, and the history is worth keeping
        # because this test has now argued both sides.
        #
        # 2026-08-13 removed it: the reported 22 is Icecast's burst SIZE, and
        # the widget's `<audio>` element measured 2.3 seconds behind the newest
        # byte, so padding by 22 bought ~17s of silence after the DJ had
        # finished. 0.10.129 overturned that measurement — `buffered.end -
        # currentTime` is buffer DEPTH, not distance behind the live edge, and
        # the operator's stopwatch against the actual audio said seventeen and
        # twenty. `caller_lag()` has returned the station's number ever since.
        #
        # What never followed was THIS sizing, and the two halves of the guard
        # were left disagreeing: the verdict path shifted its window by 22
        # while the ceiling for our own action assumed 2. Room 72de3b8893fe on
        # 2026-08-16 is that disagreement in one timeline — a 3.0s shoutout
        # opened a 7.5s hold, the DJ came back and said "I just sent that
        # shoutout", and the guard then opened a SECOND hold for 12.0s when
        # the push arrived with the real 22. The caller heard the confirmation
        # about seventeen seconds before the thing it confirmed.
        #
        # This is still a CEILING and still not the normal path: a voice.end
        # drops it on the spot, shifted by the same lag. Sizing it correctly
        # is what lets the measured close happen at the right moment instead
        # of after a premature return.
        import time

        from call.air import OnAirGuard

        g = self._guard()
        g._clear = __import__("asyncio").Event()
        g._clear.set()
        g.room = None
        g.stepped_away = False
        g.aired_text = ""
        g._last_buf = 22.0                       # what this station reports
        before = time.time()
        OnAirGuard.mark_on_air(g, seconds=10.0)
        held = g._assumed_until - before
        self.assertGreater(held, 10.0, "the hold ends before the DJ does")
        self.assertAlmostEqual(held, 22.0 + 10.0 + g.duck_pad, delta=1.0)

    def test_with_no_measurement_it_falls_back_rather_than_to_zero(self):
        from call.air import OnAirGuard

        g = self._guard()
        self.assertEqual(g.stream_buffer(), OnAirGuard.HANDOFF_LAG_SECS)
        g._last_buf = 7.0
        self.assertEqual(g.stream_buffer(), 7.0)


class TestTheOnAirFlagIsAValueNotAPresence(unittest.TestCase):
    """The card sat in "Working the booth" for the rest of a real call.

    2026-08-13, from the worker's own log: it held at 12:32:07, logged "air is
    clear" at 12:32:27, and the caller's card stayed on air for the remaining
    eighty seconds. The guard was right and the widget never heard it.

    The worker cleared the flag by setting the attribute to "", which LiveKit
    treats as DELETING it — and the widget only reacted when the key was
    present, so the clear was invisible. Both halves are fixed: the worker
    always sends a value, and the widget reads the value rather than testing
    for the key, which makes it right against an old worker too.
    """

    def test_the_worker_never_clears_by_deleting(self):
        import inspect

        from call import air

        src = inspect.getsource(air.OnAirGuard._publish)
        self.assertIn('"1" if on_air else "0"', src)
        self.assertNotIn('else ""', src)

    def test_the_widget_compares_the_value(self):
        from tests.support import REPO

        src = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertIn("p.attributes['talkwave.onair'] === '1'", src)
        # The presence test is what made a deletion invisible.
        self.assertNotIn("'talkwave.onair' in p.attributes", src)

class TestTheDuckWritesDownWhatItDid(_TempStores):
    """A transcript showed the DJ going quiet and coming back, with no way to
    tell whether the hold was early, late, or the right length in the wrong
    place. The operator's report — "goes off air earlier than needed and
    returns before the on air DJ even says a word" — was unanswerable from the
    record. It is not now.
    """

    def test_our_own_action_is_written_down_with_its_length(self):
        from call.air import OnAirGuard
        from call.air_log import AirLog

        g = object.__new__(OnAirGuard)
        g._assumed_until = 0.0
        g._clear = __import__("asyncio").Event()
        g._clear.set()
        g.on_air = False
        g.room = None
        g.duck_pad = 4.5
        g._last_buf = 22.0
        g.lag_secs = 2.0
        g.stepped_away = False
        g.aired_text = ""
        g.air_log = AirLog()
        OnAirGuard.mark_on_air(g, seconds=10.0, spoken="a line for the air")

        rows = g.air_log.rows
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["what"], "hold opened")
        self.assertEqual(rows[0]["why"], "we put something on air")
        # The caller's 22s lag, then 10s of words, then ONE pad. The row has to
        # show the whole window or a diagnosis reads a hold as over-long when
        # it is merely covering the distance the caller is behind — which is
        # how the double hold on 2026-08-16 was misread the first time.
        self.assertAlmostEqual(rows[0]["forSecs"], 22.0 + 10.0 + 4.5, delta=1.0)
        self.assertEqual(rows[0]["bufSecs"], 22.0)

    def test_the_timeline_starts_at_the_call(self):
        # The first real run replayed the receiver's whole history and wrote a
        # 25-MINUTE timeline for a 150-second call, carrying five station
        # utterances this caller was never connected for.
        import time

        from call.air_log import AirLog

        now = time.time()
        log = AirLog(since=now - 60)
        log.replay([{"at": now - 900, "event": "voice.queued"},
                    {"at": now - 10, "event": "voice.start"}])
        self.assertEqual([r["what"] for r in log.rows], ["station voice.start"])

    def test_a_station_push_records_when_the_caller_will_hear_it(self):
        # THE distinction the whole bug turns on: a voice.* timestamp is
        # stamped at the encoder and the caller is bufSecs behind it. A hold
        # opened well before audibleIn reaches zero is a duck that started
        # early — stated on the record rather than reconstructed by hand.
        import time

        from call.air_log import AirLog

        log = AirLog()
        log.station({"at": time.time(), "event": "voice.queued",
                     "phase": "queued", "voiceId": "5f4bf953",
                     "durMs": 17827, "bufSecs": 22.0})
        row = log.rows[0]
        self.assertEqual(row["what"], "station voice.queued")
        self.assertEqual(row["durSecs"], 17.8)
        # From the EVENT, not from the clock at write time: the guard records
        # in bursts at the hold's edges, and computing this against `now` gave
        # -1497s on the first real call.
        self.assertAlmostEqual(row["audibleIn"], 22.0, delta=0.2)

    def test_audible_in_survives_being_written_down_late(self):
        import time

        from call.air_log import AirLog

        log = AirLog(since=0)
        log.station({"at": time.time() - 300, "event": "voice.start",
                     "phase": "speaking", "bufSecs": 22.0})
        self.assertAlmostEqual(log.rows[0]["audibleIn"], 22.0, delta=0.2)

    def test_pushes_the_call_never_polled_are_folded_in(self):
        # The guard reads only the newest entry, so a whole queued/start/end
        # sequence between two polls was invisible. The receiver keeps a short
        # history for exactly this.
        import time

        from call.air_log import AirLog

        now = time.time()
        log = AirLog(since=now - 30)          # the call started 30s ago
        log.replay([{"at": now - 9, "event": "voice.queued", "phase": "queued",
                     "durMs": 6000, "bufSecs": 22.0},
                    {"at": now - 1, "event": "voice.end", "phase": "clear"}])
        self.assertEqual([r["what"] for r in log.rows],
                         ["station voice.queued", "station voice.end"])

    def test_it_reaches_the_record_and_is_bounded(self):
        from call.air_log import AirLog

        class _Rec:
            def __init__(self):
                self.data = {}

        log = AirLog()
        for _ in range(AirLog.LIMIT + 10):
            log.opened("the station is on air")
        rec = _Rec()
        log.write(rec)
        self.assertEqual(len(rec.data["air"]), AirLog.LIMIT)

    def test_a_broken_timeline_never_reaches_the_call(self):
        # A diagnostic that ends a call is worse than no diagnostic.
        from call.air_log import AirLog

        log = AirLog()
        log.station("not a dict")
        log.replay("not a list")
        log.write(None)
        self.assertEqual(log.rows, [])

class TestTheAirSplitHoldsItsShape(_TempStores):
    """air.py was raised past its ratchet three times in one day before the
    seam its SPLITTING entry had described since 0.10.113 was actually cut
    (0.10.127). This is what keeps it cut.

    The split is one-way by design: `air_verdict` reads evidence and knows
    nothing about the session, the room or the come-back; `air.py` keeps the
    live half. `air_timing` is a leaf holding the two names both need, because
    a constant living in the module that imports you is how a split becomes a
    circular import a week later.
    """

    def test_the_verdict_half_does_not_reach_back(self):
        from tests.support import AGENT_WORKER

        src = (AGENT_WORKER / "call" / "air_verdict.py").read_text(
            encoding="utf-8")
        self.assertNotIn("from .air import", src)
        self.assertNotIn("import air\n", src)
        # Nor does it touch anything live.
        for live in ("AgentSession", "session.", "self.room", "comeback"):
            self.assertNotIn(live, src, f"the verdict half reached for {live}")

    def test_the_timing_leaf_imports_nothing_of_ours(self):
        from tests.support import AGENT_WORKER

        src = (AGENT_WORKER / "call" / "air_timing.py").read_text(
            encoding="utf-8")
        self.assertNotIn("from .", src)
        self.assertNotIn("from call", src)

    def test_the_names_still_arrive_where_callers_expect_them(self):
        # Half the suite and three tool modules import these from call.air.
        from call.air import DUCK_PAD_SECS, OnAirGuard, speaking_secs

        self.assertEqual(DUCK_PAD_SECS, 4.5)
        self.assertGreater(speaking_secs("one two three four five", 30), 0)
        self.assertTrue(hasattr(OnAirGuard, "_push_verdict"))
        self.assertTrue(hasattr(OnAirGuard, "_settle"))



class TestARefusedActionIsNotReportedAsDone(unittest.TestCase):
    """The guard was silenced by the very tool call that failed.

    Measured 2026-08-14 on `SCENARIO_SET=refusals`, both judged rounds out of
    two, with `say_the_true_thing` present in the prompt. The station refused
    the request — "The station couldn't take that request: Your last request is
    still queued" — and the DJ answered:

        "…it'll head out onto the airwaves just as soon as that track clears."
        "I've got it locked in to follow."

    Nothing was queued. The scenario scored PASS both times, because the grader
    listed the invented EXCUSES a DJ once used and this is an invented OUTCOME.

    Two separate holes let it through, which is why the fix is two lines rather
    than one. The first sentence is an assured FUTURE and `CLAIMS_DONE` is
    written for the past tense. The second matched `PROMISES_ACTION` and was
    then cleared by `tools_ran` — a promise is normally settled the moment the
    DJ reaches for any tool, which is right about dead air and exactly wrong
    here: the tool it reached for is the one that said no.
    """

    def test_a_claim_after_a_refusal_is_caught(self):
        from promises import unbacked

        for said in (
            "The station's still holding onto that last one — it'll head out "
            "onto the airwaves just as soon as that track clears.",
            "I'll get that in the queue for you the second the one lined up "
            "clears. I've got it locked in to follow.",
        ):
            self.assertEqual(
                "refused", unbacked(said, tools_ran=True, acted=False,
                                    refused=True), said)

    def test_the_refused_nudge_never_asks_for_a_retry(self):
        """Its own kind, because the claim nudge made this worse.

        Measured 2026-08-15 before this existed: the claim nudge opens "no tool
        ran — so it is NOT done", which is false when a tool ran and was
        refused, and goes on to say "call it NOW". So the DJ sent the same
        request again, twice, against a tool result reading "Do NOT send it
        again — you already have the reason". Three turns to reach the honest
        sentence and two forbidden retries the caller sat through.

        Nothing is left to call after a refusal. The station has answered.
        """
        from call.promise_guard import _NUDGE, _PROBLEM

        nudge = _NUDGE["refused"]
        self.assertNotIn("call it NOW", nudge)
        self.assertIn("Do not call", nudge)
        self.assertIn("did not go through", nudge)
        self.assertIn("refused", _PROBLEM["refused"].lower())

    def test_every_kind_unbacked_returns_has_a_nudge_and_a_problem(self):
        # A kind with no nudge is a KeyError on a live call, at the moment the
        # guard fires, which is the worst place to find one.
        from call.promise_guard import _NUDGE, _PROBLEM

        for kind in ("promise", "claim", "refused"):
            self.assertIn(kind, _NUDGE)
            self.assertIn(kind, _PROBLEM)

    def test_the_same_line_is_fine_when_the_action_really_landed(self):
        # The guard must not touch the honest receipt. "It's about six minutes
        # out" is what a caller wants to hear and what the tool result tells
        # the DJ to say; only the refusal makes it a lie.
        from promises import unbacked

        self.assertEqual("", unbacked(
            "That's lined up — about six minutes out.",
            tools_ran=True, acted=True))

    def test_owning_the_refusal_is_not_nudged(self):
        # The behaviour the nudge is trying to produce must not itself trip it,
        # or the guard fires forever on a DJ doing the right thing.
        from promises import unbacked

        for said in ("That didn't go through — the station's only taking one "
                     "at a time tonight.",
                     "No luck with that one, they've knocked it back."):
            self.assertEqual("", unbacked(said, tools_ran=True, acted=False,
                                          refused=True), said)

    def test_ordinary_talk_after_a_refusal_is_not_nudged(self):
        from promises import unbacked

        self.assertEqual("", unbacked(
            "Rich, dark, and bitter enough to wake the dead.",
            tools_ran=True, refused=True))

    def test_a_refusal_is_read_off_the_house_phrasing(self):
        from spoken_rules import reads_as_a_refusal

        self.assertTrue(reads_as_a_refusal(
            "The station couldn't take that request: Your last request is "
            "still queued — it airs first."))
        self.assertTrue(reads_as_a_refusal(
            "That didn't go out: the station refused it. Tell the caller "
            "plainly — do not claim it worked."))
        self.assertFalse(reads_as_a_refusal(
            '"Africa" by Toto is in the queue — it comes up after what is '
            "already ahead of it."))

    def test_the_live_guard_reads_the_tool_outputs(self):
        """The flag has to reach the guard, not just exist in the rule.

        `function_tools_executed` carries `function_call_outputs` alongside the
        calls, so the guard can see a refusal without any new plumbing — and
        this pins that it actually looks, because the rule above is inert if
        nobody sets `refused`.
        """
        import inspect

        from call import promise_guard

        src = inspect.getsource(promise_guard)
        self.assertIn("function_call_outputs", src)
        self.assertIn("reads_as_a_refusal", src)
        self.assertIn("refused=state[\"refused\"]", src)

    def test_a_claim_that_survives_the_nudge_is_recorded(self):
        """The repeat is graded now, not just described.

        PROBLEMS["refused"] tells the operator "repeats here mean the honesty
        rules are not reaching this model" — and until 0.98.55 a repeat lived
        only in the transcript, because the guard spends its one nudge and
        then skips every later line of the turn. The harness has graded this
        exact fault on every drill run (spoken_rules.check_after_failure);
        the live record never did, so the panel's "needs attention" count
        could not see the deployment failing the way the drill could.
        """
        from call import promise_guard
        from promises import PROBLEMS

        problems = []
        record = types.SimpleNamespace(problem=problems.append)
        handlers, replies = {}, []

        class _Session:
            def on(self, name, fn):
                handlers[name] = fn

            async def generate_reply(self, **kw):
                replies.append(kw)

        async def go():
            promise_guard.attach_promise_guard(_Session(), record, None)
            handlers["user_input_transcribed"](types.SimpleNamespace(
                is_final=True, transcript="play something fun"))
            handlers["function_tools_executed"](types.SimpleNamespace(
                function_call_outputs=[types.SimpleNamespace(
                    is_error=False,
                    output="The station couldn't take that request: rate "
                           "limited — one per 20s")]))
            handlers["conversation_item_added"](types.SimpleNamespace(
                item=types.SimpleNamespace(
                    role="assistant",
                    text_content="I've got it locked in to follow — on its "
                                 "way.")))
            await asyncio.sleep(0.05)
            self.assertIn(PROBLEMS["refused"], problems)
            # The extra turn arrives... and says it landed AGAIN.
            handlers["conversation_item_added"](types.SimpleNamespace(
                item=types.SimpleNamespace(
                    role="assistant",
                    text_content="That's lined up — it's coming up right "
                                 "after this one.")))
            await asyncio.sleep(0.05)
            self.assertIn(PROBLEMS["claims-again"], problems)
            # And owning it honestly after the nudge is NOT a fault.
            problems.clear()
            handlers["conversation_item_added"](types.SimpleNamespace(
                item=types.SimpleNamespace(
                    role="assistant",
                    text_content="That one didn't go through — they're only "
                                 "taking one at a time tonight.")))
            await asyncio.sleep(0.05)
            self.assertEqual(problems, [])

        asyncio.run(go())


class TestNothingInACallOverwritesSomethingElse(unittest.TestCase):
    """No attribute may be assigned twice in CallSession.__init__.

    0.97.65 added a pacing meter and called it `self.heard`, which was already
    the caller-turn counter — a plain `{"n": 0}` that three separate things
    read. The meter silently replaced it and every one of them broke: the
    heard-logging handler does `counter["n"] += 1`, the idle watch does
    `heard.get("n")`, and `_on_shutdown` logs `self.heard["n"]` BEFORE it
    writes the record. So a quiet caller was never checked on or let go, and
    every call raised on the way out and wrote no record at all.

    None of it failed a test. Both halves were individually correct and unit
    tested; the collision exists only in the assembled object, and the suite
    never assembles one because CallSession needs a live JobContext. It was
    found by placing a real call against the deployed container and noticing
    the record was missing.

    So this reads the source rather than the object — the only thing that
    works without a LiveKit job, and enough, because the fault was one name
    written twice in one function.
    """

    def test_no_attribute_is_assigned_twice_in_init(self):
        import ast

        from tests.support import AGENT_WORKER

        src = (AGENT_WORKER / "call" / "session.py").read_text(encoding="utf-8")
        cls = next(n for n in ast.parse(src).body
                   if isinstance(n, ast.ClassDef) and n.name == "CallSession")
        init = next(n for n in cls.body
                    if isinstance(n, ast.FunctionDef) and n.name == "__init__")
        seen: dict[str, int] = {}
        clashes: list[str] = []
        for node in ast.walk(init):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"):
                    if target.attr in seen:
                        clashes.append(f"self.{target.attr} (lines "
                                       f"{seen[target.attr]} and {node.lineno})")
                    seen[target.attr] = node.lineno
        self.assertEqual(
            clashes, [],
            "assigned twice in CallSession.__init__, so the second silently "
            "replaces the first and everything reading the first breaks at "
            f"runtime: {clashes}")


class TestTheBriefingStopsBeingWrongWhenTheStationMovesOn(unittest.TestCase):
    """The one disagreement docs/the-call.md still recorded: the briefing is
    frozen at pickup, max_call_seconds defaults to 300, and a track runs three
    to four minutes — so the DJ routinely discussed a record that had stopped
    playing.

    The fix rides plumbing that already existed: the guard's watch loop reads
    /state every POLL_SECS for the djLog and used to throw the current track
    away. A mid-call CHANGE now stages one sentence, and the reply path
    injects it as a system note — the same Gemini-safe insertion point the
    door hint uses, because a context push that GENERATES a turn perturbs the
    turn-taking, which is worse than a stale fact.
    """

    def _guard(self):
        from call.air import OnAirGuard

        return OnAirGuard(None, {"avoid_on_air_overlap": False})

    def test_the_first_sighting_is_the_briefings_track_and_stages_nothing(self):
        g = self._guard()
        g._note_track({"current": {"title": "Universe", "artist": "Laraaji"}})
        self.assertEqual(g.track_note, "",
                         "the briefing already covers the pickup track — a "
                         "note here is a sentence spent on nothing")

    def test_a_change_stages_the_new_truth(self):
        g = self._guard()
        g._note_track({"current": {"title": "Universe", "artist": "Laraaji"}})
        g._note_track({"current": {"title": "Dreams", "artist": "Fleetwood Mac"}})
        self.assertIn('"Dreams" by Fleetwood Mac', g.track_note)
        self.assertIn("out of date", g.track_note)

    def test_a_second_change_overwrites_the_first(self):
        # The caller only ever needs the newest truth; two stacked corrections
        # read as a DJ narrating its own paperwork.
        g = self._guard()
        g._note_track({"current": {"title": "A", "artist": "One"}})
        g._note_track({"current": {"title": "B", "artist": "Two"}})
        g._note_track({"current": {"title": "C", "artist": "Three"}})
        self.assertIn('"C"', g.track_note)
        self.assertNotIn('"B"', g.track_note)

    def test_an_empty_read_neither_stages_nor_forgets(self):
        # A timed-out /state hands back nothing; treating that as "the track
        # changed to nothing" would fire a note on every congested poll.
        g = self._guard()
        g._note_track({"current": {"title": "Universe", "artist": "Laraaji"}})
        g._note_track({})
        g._note_track(None)
        self.assertEqual(g.track_note, "")
        g._note_track({"current": {"title": "Dreams", "artist": "Fleetwood Mac"}})
        self.assertIn('"Dreams"', g.track_note,
                      "the baseline must survive a failed read in between")

    def test_the_note_reaches_the_model_and_is_consumed(self):
        from call.air import CallAgent, OnAirGuard

        added = []

        class _Ctx:
            def add_message(self, role, content):
                added.append((role, content))

        guard = OnAirGuard(None, {"avoid_on_air_overlap": False})
        guard._note_track({"current": {"title": "Universe", "artist": "Laraaji"}})
        guard._note_track({"current": {"title": "Dreams", "artist": "Fleetwood Mac"}})
        agent = CallAgent("instructions", guard)
        asyncio.run(agent.on_user_turn_completed(
            _Ctx(), types.SimpleNamespace(text_content="what's playing?")))
        self.assertEqual(1, len(added))
        role, content = added[0]
        self.assertEqual("system", role,
                         "a note from us must not be filed as the caller's words")
        self.assertIn('"Dreams"', content)
        self.assertEqual(guard.track_note, "", "consumed — a stale correction "
                         "repeated every turn is worse than the stale fact")

    def test_a_call_where_nothing_changes_costs_nothing(self):
        from call.air import CallAgent, OnAirGuard

        added = []

        class _Ctx:
            def add_message(self, role, content):
                added.append((role, content))

        guard = OnAirGuard(None, {"avoid_on_air_overlap": False})
        guard._note_track({"current": {"title": "Universe", "artist": "Laraaji"}})
        guard._note_track({"current": {"title": "Universe", "artist": "Laraaji"}})
        agent = CallAgent("instructions", guard)
        asyncio.run(agent.on_user_turn_completed(
            _Ctx(), types.SimpleNamespace(text_content="lovely")))
        self.assertEqual([], added)


class TestRingingRidesTheMintsHeadStart(_TempStores):
    """prepare() adopts the mint-time snapshot instead of re-asking the
    station, says which it did in the record, starts the MCP handshake under
    the ring rather than in front of the greeting, and carries the room join
    on the same wait. Every one of these was a serial leg a real caller heard
    as ringing first (2.5s measured healthy, 2026-08-18; 12s+ congested)."""

    SNAP = {"dj": {"name": "Dalia"}, "personas": [{"id": "p1", "name": "Dalia"}],
            "now_playing": {}, "state": {}, "session": {}, "schedule": {},
            "skills": []}

    class _FakeCtx:
        def __init__(self, name="callin-o-abcdef123456"):
            self.room = types.SimpleNamespace(name="")   # pre-join: nameless
            self.job = types.SimpleNamespace(
                room=types.SimpleNamespace(name=name))
            self.shutdown_callbacks = []

        def add_shutdown_callback(self, cb):
            self.shutdown_callbacks.append(cb)

    class _FakeMCPServer:
        built = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.initialized_called = False
            type(self).built.append(self)

        async def initialize(self):
            self.initialized_called = True

    def setUp(self):
        super().setUp()
        from pathlib import Path

        import brain as brain_mod
        import station as station_mod
        from call import session as session_mod

        self._mods = (brain_mod, station_mod, session_mod)
        self._olds = (brain_mod.build_system_prompt, station_mod._PERSONA_FILE,
                      session_mod.mcp, session_mod.available_voices,
                      session_mod.pick_speakable_voice)

        async def fake_prompt(*a, **k):
            return "prompt"

        async def fake_voices(*a, **k):
            return ["test-voice"]

        brain_mod.build_system_prompt = fake_prompt
        station_mod._PERSONA_FILE = Path(self._tmp.name) / "last-persona.json"
        self._FakeMCPServer.built = []
        session_mod.mcp = types.SimpleNamespace(MCPServerHTTP=self._FakeMCPServer)
        session_mod.available_voices = fake_voices
        session_mod.pick_speakable_voice = lambda v, voices: (v or "test-voice", "")

    def tearDown(self):
        brain_mod, station_mod, session_mod = self._mods
        (brain_mod.build_system_prompt, station_mod._PERSONA_FILE,
         session_mod.mcp, session_mod.available_voices,
         session_mod.pick_speakable_voice) = self._olds
        super().tearDown()

    def _session(self):
        from call.session import CallSession

        s = CallSession(self._FakeCtx())
        calls = {"snapshot": 0}
        snap = self.SNAP

        async def counting_snapshot(with_skills=False):
            calls["snapshot"] += 1
            return dict(snap)

        s.station.snapshot = counting_snapshot
        return s, calls

    def test_a_fresh_head_start_replaces_the_station_read(self):
        import station_prefetch

        s, calls = self._session()
        station_prefetch.store(self.SNAP, {},
                               with_skills=bool(s.cfg.get("allow_skills")))
        asyncio.run(s.prepare())
        self.assertEqual(0, calls["snapshot"],
                         "the mint already read the station for this call")
        self.assertEqual("prefetched", s.record.data["setup"]["snapshot"])
        self.assertEqual("Dalia", s.persona["name"])

    def test_no_head_start_means_the_worker_reads_as_before(self):
        s, calls = self._session()
        asyncio.run(s.prepare())
        self.assertEqual(1, calls["snapshot"])
        self.assertEqual("fetched", s.record.data["setup"]["snapshot"])

    def test_the_mcp_handshake_starts_under_the_ring(self):
        s, _ = self._session()
        asyncio.run(s.prepare())
        self.assertEqual(1, len(self._FakeMCPServer.built))
        self.assertTrue(self._FakeMCPServer.built[0].initialized_called,
                        "the connect must start in prepare, not start()")
        self.assertIs(s.station_tools, self._FakeMCPServer.built[0])

    def test_the_join_rides_the_ringing_and_finishes_with_it(self):
        s, _ = self._session()
        joined = {"done": False}

        async def connect():
            joined["done"] = True

        asyncio.run(s.prepare(connecting=connect()))
        self.assertTrue(joined["done"], "prepare must await the join it was handed")
        self.assertEqual("callin-o-abcdef123456", s.room_name,
                         "the dispatched name, known before the join")

    def test_a_failed_prepare_cancels_the_join(self):
        import brain as brain_mod

        s, _ = self._session()

        async def broken_prompt(*a, **k):
            raise RuntimeError("prompt assembly died")

        brain_mod.build_system_prompt = broken_prompt
        state = {"cancelled": False}

        async def connect():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise

        with self.assertRaises(RuntimeError):
            asyncio.run(s.prepare(connecting=connect()))
        self.assertTrue(state["cancelled"],
                        "an orphaned join would hold the room open with "
                        "nobody coming to answer it")
