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

    def test_no_check_in_while_the_broadcast_has_the_microphone(self):
        self.assertEqual(
            self._run(on_air=True), [],
            "the DJ asked the caller why it was quiet during its own hold")

    def test_the_check_in_still_fires_when_the_air_is_clear(self):
        # The other half — pinning it must not disable the feature outright.
        self.assertTrue(
            self._run(on_air=False),
            "the idle check-in stopped working when the air was clear")


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

        from call.air import OnAirGuard

        said = []

        class _Session:
            async def generate_reply(self, **kw):
                said.append(str(kw.get("instructions", "")))

        guard = OnAirGuard(types.SimpleNamespace(), {}, room=None)
        asyncio.run(guard._come_back(_Session()))
        self.assertTrue(said)
        self.assertIn("back", said[0].lower())

    def test_it_still_speaks_when_the_model_will_not(self):
        import asyncio
        import types

        from call.air import OnAirGuard

        spoken = []

        class _Session:
            async def generate_reply(self, **kw):
                raise RuntimeError("no")

            def say(self, text, **kw):
                spoken.append(text)

        guard = OnAirGuard(types.SimpleNamespace(), {}, room=None)
        asyncio.run(guard._come_back(_Session()))
        self.assertEqual(len(spoken), 1)
        self.assertIn("back", spoken[0].lower())


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
