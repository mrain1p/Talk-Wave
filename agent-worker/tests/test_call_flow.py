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
        actions = types.SimpleNamespace(is_working=lambda: working)

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
        self.assertFalse(guard._log_says_busy((30.0 + lag + 1, "")))

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

    def test_the_push_file_reads_back_as_evidence(self):
        # The web process writes the last verified voice push; the guard
        # reads it raw and judges it with _push_verdict. An absent file is
        # no evidence at all; a legacy (handoff-stamped) entry can prove the
        # air busy, never clear — exactly the old behaviour.
        import json
        import os
        import tempfile
        import time

        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "hook-air.json")
            os.environ["CALLIN_HOOK_AIR_PATH"] = p
            try:
                guard = self._guard()
                self.assertIsNone(guard._pushed_state())
                with open(p, "w", encoding="utf-8") as f:
                    json.dump({"at": time.time() - 3,
                               "text": "Back after this."}, f)
                verdict = guard._push_verdict(guard._pushed_state(), time.time())
                self.assertEqual(verdict[0], "busy")
                self.assertEqual(verdict[1], "Back after this.")
            finally:
                os.environ.pop("CALLIN_HOOK_AIR_PATH", None)

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
        self.assertFalse(
            guard._log_says_busy((31.0 + guard.HANDOFF_LAG_SECS, "")))
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

    def _wire(self, user_state, raises=None):
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
        lifecycle.attach_turn_commit(ctx, session)
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

    def _wire(self, record=None):
        from call import promise_guard

        handlers, replies = {}, []

        class _Session:
            def on(self, name, fn):
                handlers[name] = fn

            async def generate_reply(self, **kw):
                replies.append(kw)

        promise_guard.attach_promise_guard(_Session(), record)
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

    def test_the_hold_is_much_shorter_than_a_mid_call_one(self):
        # A caller held at pickup has no idea why: there is no conversation
        # yet for the widget's on-air chip to explain. Silence straight after
        # the ring reads as a failed call.
        from call.air import OnAirGuard
        from call.greeting import GREET_HOLD_SECS

        self.assertLess(GREET_HOLD_SECS, OnAirGuard.MAX_HOLD / 2)

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
        # It used to be sized as speech + whatever streamBufferSeconds the
        # station reported, on the reading that the caller is that far behind
        # the live edge. Measured 2026-08-13 and the premise did not hold: the
        # reported 22 is Icecast's BURST SIZE, and the plain `<audio>` element
        # the widget tunes a caller in with plays 2.3 seconds behind the
        # newest byte, not 22. What the old sizing bought was ~17 seconds of
        # silence after the DJ had already finished — read off a real record,
        # a 37.8s voice sizing a ~60s hold. So it is speech + ONE pad, and a
        # station claiming a large buffer no longer inflates it.
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
        self.assertAlmostEqual(held, 10.0 + g.duck_pad, delta=1.0)

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
        # 10s of words + ONE pad. The station reports a 22s buffer and it is
        # recorded — a diagnosis needs to see what the station claimed — but
        # it no longer sizes the hold; see tail().
        self.assertAlmostEqual(rows[0]["forSecs"], 10.0 + 4.5, delta=1.0)
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

