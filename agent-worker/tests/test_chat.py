"""The text line: who gets in, what the clocks close, and what the typed
brain is told.

Split by subject like every module here; the WebSocket's frame-level gate
lives in api/chat.py, the conversation in chat/session.py, the register in
brain/conduct_chat.py.
"""

from __future__ import annotations

import asyncio
import os
import time
import types
import unittest

import settings as settings_store
from tests.support import AGENT_WORKER, _TempStores


class TestTheTextLineIsOriginGated(unittest.TestCase):
    """A WebSocket handshake is not subject to CORS, so the chat line — which
    spends model budget per turn — was reachable from any origin /token
    refuses (0.10.57 review). origin_allowed is the same policy applied where
    the handshake can see it."""

    def _req(self, origin=None, host="caller.example:8100"):
        headers = {"Host": host}
        if origin is not None:
            headers["Origin"] = origin
        return types.SimpleNamespace(headers=headers)

    # The allowlist became a live-read setting in 0.10.63 (allowed_origins()
    # reads settings on every check), so these drive it through the env layer
    # rather than poking a module constant that no longer exists.
    def test_same_origin_and_no_origin_are_allowed(self):
        from api.wire import origin_allowed

        old = os.environ.pop("CALLIN_ALLOWED_ORIGINS", None)
        try:
            self.assertTrue(origin_allowed(self._req(origin=None)))
            self.assertTrue(origin_allowed(
                self._req(origin="https://caller.example:8100")))
            self.assertFalse(origin_allowed(
                self._req(origin="https://evil.example")))
        finally:
            if old is not None:
                os.environ["CALLIN_ALLOWED_ORIGINS"] = old

    def test_a_named_origin_is_allowed_and_star_opens_it(self):
        from api.wire import origin_allowed

        old = os.environ.get("CALLIN_ALLOWED_ORIGINS")
        try:
            os.environ["CALLIN_ALLOWED_ORIGINS"] = "https://radio.example"
            self.assertTrue(origin_allowed(
                self._req(origin="https://radio.example")))
            self.assertFalse(origin_allowed(
                self._req(origin="https://evil.example")))
            os.environ["CALLIN_ALLOWED_ORIGINS"] = "*"
            self.assertTrue(origin_allowed(
                self._req(origin="https://evil.example")))
        finally:
            if old is None:
                os.environ.pop("CALLIN_ALLOWED_ORIGINS", None)
            else:
                os.environ["CALLIN_ALLOWED_ORIGINS"] = old


class TestTheTextLineHasADoor(_TempStores):
    """The gate mirrors /token deliberately: The Line outranks the mode, the
    ladder decides who, and a curl loop meets a ceiling — the station's own
    text surface took a real raid (2026-07-28), and these are that lesson.

    _TempStores, not TestCase: the guest-code check reads the auth store,
    and a code left there by an earlier test read as "code required"."""

    def _refusal(self, cfg, key=""):
        # The guest-code gate is another subject's test (secrets_and_auth);
        # stubbing it keeps THIS class about the chat ladder, and keeps a
        # code left in the shared auth store by an earlier test from
        # answering for every case here.
        from api import chat as api_chat

        req = types.SimpleNamespace(headers={}, remote="1.2.3.4",
                                    transport=None)
        old = api_chat._guest_check
        api_chat._guest_check = lambda key_, caller: None
        try:
            return api_chat._refusal(cfg, req, key)
        finally:
            api_chat._guest_check = old

    def test_a_disabled_line_refuses_before_anything_else(self):
        out = self._refusal({"chat_enabled": False})
        self.assertIn("text line", out)

    def test_the_kill_switch_outranks_the_mode(self):
        out = self._refusal({"chat_enabled": True, "calls_paused": True})
        self.assertIn("closed", out)

    def test_a_tier_the_caller_does_not_hold_is_refused(self):
        out = self._refusal({"chat_enabled": True, "allow_chat": "admin"})
        self.assertIn("this line", out)

    def test_an_open_line_lets_a_stranger_in(self):
        self.assertIsNone(self._refusal({"chat_enabled": True,
                                         "allow_chat": "open"}))


class TestTheFloodBrakeSurvivesAReconnect(unittest.TestCase):
    """The per-minute message brake used to live on the WebSocket, so a caller
    could burst, drop the socket, resume the same chat id for a fresh empty
    list, and burst again (0.10.57 review). It lives on the ChatSession now, so
    reconnecting is no cheaper — and the handler reads chat.msg_times, not a
    per-socket list."""

    def test_the_brake_state_is_on_the_chat_not_the_socket(self):
        from chat.session import ChatSession

        chat = ChatSession("abc123def456", "open")
        self.assertEqual(chat.msg_times, [])
        chat.msg_times.append(123.0)
        # A "reconnect" is the SAME object fetched again from the shelf, so
        # the recorded times are still there.
        self.assertEqual(chat.msg_times, [123.0])

    def test_the_handler_reads_the_chat_field(self):
        import inspect

        from api import chat as api_chat

        src = inspect.getsource(api_chat.handle_chat_ws)
        self.assertIn("chat.msg_times", src)
        # The old per-socket local is gone — no bare `msg_times` that isn't
        # `chat.msg_times`.
        self.assertNotIn("msg_times: list", src)
        self.assertNotIn(" msg_times[", src)   # bare local index; chat. has a dot


class TestOneAbuserIsSingledOut(_TempStores):
    """The text line is scriptable where a voice call is not, so the phone's
    per-IP cooldown and daily wallet had to reach chat too — the hourly and
    daily caps only stop a crowd; this stops one script. Resuming an open
    chat never faces any of them."""

    def _open(self, cfg, key=""):
        import types

        from api import chat as api_chat

        req = types.SimpleNamespace(headers={}, remote="9.9.9.9",
                                    transport=None)
        return api_chat._open_refusal(cfg, req)

    def setUp(self):
        super().setUp()
        from api import chat as api_chat

        api_chat._recent_opens.clear()
        api_chat._chat_caller_last.clear()

    def test_the_same_caller_waits_between_opens(self):
        cfg = {"chat_caller_cooldown_secs": 30}
        self.assertIsNone(self._open(cfg))          # first open allowed
        # Record it the way the hello branch does, then a second open refuses.
        import time

        from api import chat as api_chat
        api_chat._chat_caller_last["9.9.9.9"] = time.time()
        out = self._open(cfg)
        self.assertIsNotNone(out)
        self.assertIn("give it", out)

    def test_the_daily_wallet_closes_the_line(self):
        import time

        from api import chat as api_chat
        api_chat._recent_opens.extend([time.time()] * 5)
        self.assertIn("tomorrow", self._open({"chats_per_day": 5}))

    def test_zero_means_unlimited(self):
        self.assertIsNone(self._open({"chat_caller_cooldown_secs": 0,
                                      "chats_per_day": 0, "chats_per_hour": 0}))


class TestChatsEndInsteadOfAccumulating(_TempStores):
    """Resumable is not immortal: the idle clock, the message ceiling and
    the age cap each close a chat, and closing writes the record — the
    durable trace lives in the archive, not in process memory."""

    def _shelf(self):
        from chat.session import ChatShelf

        return ChatShelf()

    def test_a_fresh_id_opens_and_the_same_id_resumes(self):
        shelf = self._shelf()
        a = shelf.get_or_open(None, "open", {})
        b = shelf.get_or_open(a.id, "open", {})
        self.assertIs(a, b)

    def test_the_open_chat_ceiling_holds(self):
        shelf = self._shelf()
        cfg = {"max_open_chats": 1}
        first = shelf.get_or_open(None, "open", cfg)
        self.assertIsNotNone(first)
        self.assertIsNone(shelf.get_or_open(None, "open", cfg))

    def test_an_idle_chat_is_closed_and_written_down(self):
        import json
        from pathlib import Path

        from call import record as call_record

        shelf = self._shelf()
        chat = shelf.get_or_open(None, "open", {})
        chat.turns = [("caller", "hello"), ("dj", "hey there")]
        chat.last_active = time.time() - 3600 * 3
        settings_store.save({"record_calls": True})
        old_dir = call_record.CALLS_DIR
        call_record.CALLS_DIR = Path(self._tmp.name) / "calls"
        try:
            shelf.sweep({"chat_idle_minutes": 30})
        finally:
            fresh = call_record.CALLS_DIR
            call_record.CALLS_DIR = old_dir
        self.assertEqual(shelf.chats, {})
        recs = [json.loads(p.read_text(encoding="utf-8"))
                for p in fresh.glob("*.json")]
        chats = [r for r in recs if r.get("kind") == "chat"]
        self.assertEqual(len(chats), 1)
        self.assertEqual(chats[0]["turns"][0]["text"], "hello")

    def test_the_message_ceiling_counts(self):
        shelf = self._shelf()
        chat = shelf.get_or_open(None, "open", {})
        chat.turns = [("caller", "x")]
        chat.messages = 60
        shelf.sweep({"chat_max_messages": 60})
        self.assertEqual(shelf.chats, {})


class TestTheTypedBrainIsTheSameBrainInADifferentRegister(unittest.TestCase):
    """conduct_chat states rules for TYPING; the medium-independent blocks
    (triage, tool etiquette, the stranger rule) are imported from the spoken
    conduct rather than copied, so one edit fixes both mouths."""

    def test_typed_rules_carry_the_shared_blocks(self):
        from brain import conduct_chat

        text = conduct_chat.rules({})
        self.assertIn("# Running the call", text)      # shared triage
        self.assertIn("stranger", text)                # the safety floor
        self.assertIn("# How to type", text)           # the typed register
        self.assertIn("# Typed, not spoken", text)     # the two-places override

    def test_the_schedule_is_offered_as_a_table_only_when_the_roster_is_held(self):
        # "What's on?" reads as a wall in prose, so the typed DJ lays the
        # schedule out as a Markdown table the widget renders (operator ask,
        # 2026-08-10). But only when the DJ actually has the roster — the same
        # switches that put it in the briefing — or the rule is dead weight in
        # front of a DJ with no shows to list.
        from brain import conduct_chat

        self.assertNotIn("# When they ask what's on", conduct_chat.rules({}))
        for cfg in ({"context_schedule": True}, {"allow_takeover": True}):
            text = conduct_chat.rules(cfg)
            self.assertIn("# When they ask what's on", text)
            self.assertIn("Markdown table", text)
            # The example table proves the shape the widget parses — a Show
            # column. It must NOT demand Time/DJ, which chat cannot source
            # (its briefing gives names only), or the DJ fabricates them to
            # square the grid (top-down review, 2026-08-28).
            self.assertIn("| Show", text)
            self.assertIn("A single Show column is a fine table", text)
            self.assertIn("square off the grid", text)

    def test_typed_rules_drop_the_spoken_physics(self):
        from brain import conduct_chat

        text = conduct_chat.rules({})
        # The TTS stage-direction ban and the spoken closing ladder are
        # phone physics; their headings must not leak into the typed prompt.
        self.assertNotIn("# How to talk", text)
        self.assertNotIn("# Closing a call", text)

    def test_both_modes_carry_the_language_and_mimicry_guard(self):
        # SUB/WAVE's raid lesson (2026-07-28): a caller directing a language
        # switch or quoting text as an instruction is testing the line, not
        # making a request. The clause is always on, in BOTH mouths.
        from brain import conduct, conduct_chat

        for text in (conduct.rules({}), conduct_chat.rules({})):
            self.assertIn("language you answer in", text)
            self.assertIn("testing the line", text)

    def test_both_mouths_stay_in_character_but_dont_dodge_an_action(self):
        # The DJ must NOT break the fourth wall (an in-character deflection for
        # something like the on-air/on-call overlap is fine — it keeps the
        # fiction). What it must NOT do is invent an in-world reason to DODGE a
        # real action ("Wade's only on in the evening" to skip a takeover it can
        # actually do). Both mouths carry this.
        from brain import conduct, conduct_chat

        for text in (conduct.rules({}), conduct_chat.rules({})):
            self.assertIn("Stay in character", text)
            self.assertIn("fourth wall", text)
            self.assertIn("dodge", text.lower())
        # And the call list names a show/DJ change as a takeover to DO — but
        # only when the switch is on; the bare-cfg claim is the refusal (see
        # TestThePromptNeverPromisesATakeoverItCannotDo in test_brain).
        self.assertIn("TAKEOVER", conduct.rules({"allow_takeover": True}))

    def test_the_tool_loop_answers_and_runs_tools(self):
        from livekit.agents import llm as lk_llm

        from chat.session import ChatSession

        @lk_llm.function_tool(name="test_probe")
        async def probe(word: str) -> str:
            """Test tool."""
            return f"probe saw {word}"

        class _Stream:
            def __init__(self, chunks):
                self._chunks = list(chunks)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._chunks:
                    raise StopAsyncIteration
                return self._chunks.pop(0)

            async def aclose(self):
                pass

        class _Model:
            """First round calls the tool, second answers in words."""

            def __init__(self):
                self.rounds = 0

            def chat(self, chat_ctx=None, tools=None):
                self.rounds += 1
                if self.rounds == 1:
                    call = types.SimpleNamespace(
                        call_id="c1", name="test_probe",
                        arguments='{"word": "hello"}')
                    delta = types.SimpleNamespace(content="", tool_calls=[call])
                else:
                    delta = types.SimpleNamespace(content="all done",
                                                  tool_calls=[])
                return _Stream([types.SimpleNamespace(delta=delta)])

            async def aclose(self):
                pass

        chat = ChatSession("t1", "open")
        ctx = lk_llm.ChatContext.empty()
        ctx.add_message(role="user", content="run the probe")
        out = asyncio.run(chat._tool_loop(_Model(), ctx, [probe], lambda ev: None))
        self.assertEqual(out, "all done")
        # The tool's result made it into the context for round two — as TEXT
        # since 0.10.119, not as a function_call_output. Gemini 3 refuses to
        # replay a functionCall part it did not sign and does not sign them
        # all, so a structured replay cannot be built; what matters here is
        # that the RESULT reached the next round, not how it was wrapped.
        said = " ".join(str(getattr(i, "content", "")) for i in ctx.items)
        self.assertIn("probe saw hello", said)
        self.assertNotIn("function_call_output",
                         [getattr(i, "type", "") for i in ctx.items])

    def test_a_promise_with_no_tool_behind_it_gets_one_more_pass(self):
        # The chat conduct TELLS the DJ to say something before reaching for
        # a tool ("hold on, let me dig through the racks"). A round that was
        # only that line looked identical to a finished answer, so "let me
        # get that dedication sent right on down to the booth" ended the turn
        # and nothing was ever sent (operator-reported, 2026-08-12).
        from livekit.agents import llm as lk_llm

        from chat.session import ChatSession

        @lk_llm.function_tool(name="test_probe")
        async def probe(word: str = "") -> str:
            """Test tool."""
            return "sent"

        class _Stream:
            def __init__(self, chunks):
                self._chunks = list(chunks)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._chunks:
                    raise StopAsyncIteration
                return self._chunks.pop(0)

            async def aclose(self):
                pass

        class _Model:
            """Promises in round one, acts only when nudged in round two —
            the exact shape that used to ship as a finished answer."""

            def __init__(self):
                self.rounds = 0

            def chat(self, chat_ctx=None, tools=None):
                self.rounds += 1
                if self.rounds == 1:
                    delta = types.SimpleNamespace(
                        content="Let me get that dedication sent down "
                                "to the booth for you.",
                        tool_calls=[])
                elif self.rounds == 2:
                    call = types.SimpleNamespace(
                        call_id="c1", name="test_probe", arguments="{}")
                    delta = types.SimpleNamespace(content="", tool_calls=[call])
                else:
                    delta = types.SimpleNamespace(content=" That's away.",
                                                  tool_calls=[])
                return _Stream([types.SimpleNamespace(delta=delta)])

            async def aclose(self):
                pass

        model = _Model()
        chat = ChatSession("t2", "open")
        ctx = lk_llm.ChatContext.empty()
        ctx.add_message(role="user", content="dedicate it to my mate")
        out = asyncio.run(chat._tool_loop(model, ctx, [probe], lambda ev: None))
        said = " ".join(str(getattr(i, "content", "")) for i in ctx.items)
        # The tool result reaches round two as text now (0.10.119).
        self.assertIn("test_probe", said, "the promised tool never ran")
        self.assertIn("sent", said)
        # The caller keeps the line they already read, and the outcome.
        self.assertIn("dedication", out)
        self.assertIn("away", out)

    def test_ordinary_chat_is_not_given_an_extra_round(self):
        # The nudge costs a model round, so it fires only on the openers the
        # conduct asks for — never on a reply that was simply conversation.
        from promises import unbacked

        for promise in ("Let me dig through the racks",
                        "hold on, checking what we've got",
                        "On it — I'll get that queued"):
            self.assertEqual("promise", unbacked(promise), promise)
        for chatter in ("That's a grand one for a mate.",
                        "The Chieftains were on top form that year.",
                        "Nice — good taste."):
            self.assertEqual("", unbacked(chatter), chatter)

    def test_the_text_line_guards_the_same_words_the_phone_does(self):
        # These two lists were separate copies until 0.10.138 and had drifted:
        # the phone had gained "pulling up", "have a look", "dig out" and "dig
        # through" and the text line never did, so the same sentence was caught
        # on one surface and waved through on the other. One import now — this
        # test is here to fail if somebody re-copies it.
        import chat.session as chat_session
        from call import promise_guard

        self.assertIs(chat_session.unbacked, promise_guard.unbacked)

    def test_both_lines_can_nudge_every_verdict_the_classifier_returns(self):
        """Sharing the classifier is only half of parity — the nudges have to
        cover what it can say.

        `promises.unbacked` returns three verdicts. The phone has had a nudge
        for all three since 0.10.154; the text line carried two, and passing it
        a refusal would have looked up a key that was not there and taken the
        whole reply down with a KeyError. It never crashed only because nothing
        ever passed `refused=` on that surface — which was itself the bug.
        """
        import chat.session as chat_session
        from call import promise_guard

        verdicts = {"promise", "claim", "refused"}
        self.assertTrue(
            verdicts <= set(promise_guard._NUDGE),
            "the phone lost a verdict it used to answer for")
        missing = verdicts - set(chat_session._NUDGE)
        self.assertEqual(
            missing, set(),
            f"promises.unbacked can return {missing} and the text line has no "
            "nudge for it — the lookup raises and the caller's reply dies")

    def test_a_refusal_the_station_wrote_in_prose_is_still_a_refusal(self):
        # is_error catches a tool that RAISED. It says nothing about a
        # perfectly successful call whose content is the station saying no —
        # a rate limit, a blocklist — which is the commoner shape by far. Both
        # signals, or the guard is blind to whichever half it dropped.
        from spoken_rules import reads_as_a_refusal

        self.assertTrue(reads_as_a_refusal(
            "That didn't go through — the station refused it."))
        self.assertFalse(reads_as_a_refusal(
            "Added to the queue: Firestone. It is NOT playing yet."))


class TestTheTextLineFeelsLikeAConversation(_TempStores):
    """The text line was answering with silence until the caller typed, never
    confirming what an action actually did, and giving no sign it was
    composing — all reported as "requests go nowhere and it doesn't feel like
    a conversation". The booth greets first, reports outcomes, and shows a
    typing cue."""

    def test_report_the_outcome_rule_is_in_the_typed_prompt(self):
        from brain import conduct_chat

        text = conduct_chat.rules({})
        self.assertIn("Close the loop", text)
        # The concrete instruction that fixes "it never confirmed what
        # happened": the tool comes FIRST and the reply reports what it
        # actually said. It used to be the other way round — say a line, then
        # reach for the tool — and a round that was only the line looked
        # identical to a finished answer, so "let me get that dedication sent
        # down to the booth" ended the turn with nothing sent (operator,
        # 2026-08-12). Chat has no dead air to cover; the phone does, which
        # is why the spoken conduct still says it and TYPED_TOOLS_NOTE has to
        # take it back.
        self.assertIn("Reach for the TOOL first", text)
        self.assertIn("Never claim an outcome the tool has not given you", text)

    def test_the_typed_line_never_receives_the_speak_first_rule(self):
        # The spoken conduct tells the DJ to say a line BEFORE reaching for
        # the tool — right on a phone call, where silence is dead air, and
        # the exact instruction that broke the text line. The typed build
        # used to receive it and countermand it 12k characters later; a rule
        # plus its negation is the one thing a weak model reliably gets wrong
        # (2026-08-31 brain review), so the chat build now DROPS the section
        # (tool_speakfirst) instead of overriding it.
        from brain import conduct, conduct_chat

        spoken = conduct.rules({"allow_requests": True})
        typed = conduct_chat.rules({"allow_requests": True})
        self.assertIn("BEFORE you reach for the tool", spoken)
        self.assertNotIn("BEFORE you reach for the tool", typed)
        self.assertNotIn("does not apply", typed)
        self.assertIn("call the tool first", typed.lower())

    def test_the_defaults_greet_and_time_out(self):
        # A silent line reads as broken, so greeting is ON by default —
        # written in persona since 0.10.80 (the operator's fresh-install
        # review); the staged/canned line remains the fallback. And a
        # stalled model must not spin a typing dot forever, so a reply
        # timeout exists by default.
        self.assertEqual(settings_store.FIELDS["chat_greeting_mode"][1], "fresh")
        self.assertGreater(int(settings_store.FIELDS["chat_reply_timeout_secs"][1]), 0)

    def test_a_quiet_caller_is_nudged_once_by_default(self):
        # A chat that sits silent after its own last line reads as dead /
        # turn-based (operator's ask, 2026-08-10): the DJ nudges once, on by
        # default, after a natural pause. The method the WS idle timer calls
        # must exist.
        from chat.session import ChatSession

        self.assertIs(settings_store.FIELDS["chat_reprompt"][1], True)
        self.assertGreater(int(settings_store.FIELDS["chat_reprompt_secs"][1]), 0)
        self.assertTrue(hasattr(ChatSession("c1", "open"), "nudge"))

    def test_the_nudge_lands_as_one_dj_turn(self):
        # Drive nudge() end to end with a faked station, prompt and model: it
        # must stream a line and land it as a DJ turn (not vanish, not double).
        from chat import openers, session as chat_session
        import brain.assemble as assemble_mod
        import call.providers as providers_mod

        class _FakeStation:
            async def resolve_live_persona(self):
                return {"name": "Ash"}

            async def aclose(self):
                pass

        class _Stream:
            def __init__(self, text):
                self._chunks = [types.SimpleNamespace(
                    delta=types.SimpleNamespace(content=text, tool_calls=[]))]

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._chunks:
                    raise StopAsyncIteration
                return self._chunks.pop(0)

            async def aclose(self):
                pass

        class _Model:
            def chat(self, chat_ctx=None, tools=None):
                return _Stream("Still weighing it up?")

            async def aclose(self):
                pass

        async def _fake_prompt(*a, **k):
            return "SYS"

        orig = (openers.StationClient, assemble_mod.build_system_prompt,
                providers_mod.build_llm)
        openers.StationClient = _FakeStation
        assemble_mod.build_system_prompt = _fake_prompt
        providers_mod.build_llm = lambda cfg: _Model()
        try:
            chat = chat_session.ChatSession("n1", "open")
            chat.turns.append(("dj", "Hey, Ash here."))
            events = []
            asyncio.run(chat.nudge({}, events.append))
        finally:
            (openers.StationClient, assemble_mod.build_system_prompt,
             providers_mod.build_llm) = orig

        self.assertEqual(events[-1]["type"], "done")
        self.assertTrue(any("weighing" in (e.get("text") or "") for e in events))
        self.assertEqual(chat.turns[-1], ("dj", "Still weighing it up?"))

    def test_the_handler_nudges_on_a_receive_timeout(self):
        # The WS loop waits with a timeout while the ball is in the caller's
        # court and fires the nudge when it lands — verified as wiring so a
        # refactor can't quietly drop it (aiohttp re-raises asyncio.TimeoutError
        # from receive(timeout=), which this must catch).
        import inspect

        from api import chat as chat_api

        src = inspect.getsource(chat_api)
        self.assertIn("ws.receive(timeout=", src)
        self.assertIn("asyncio.TimeoutError", src)
        self.assertIn("chat.nudge", src)
        self.assertIn("chat_reprompt", src)
        # And it must not fire before the caller has said anything — nudging
        # right after the greeting reads as pushy (review, 2026-08-10).
        self.assertIn("caller_spoke", src)

    def test_a_canned_greeting_speaks_first_in_the_djs_name(self):
        from chat import openers, session as chat_session

        persona = {"name": "Dalia", "station": "SUB/WAVE"}

        class _FakeStation:
            async def resolve_live_persona(self):
                return persona

            async def aclose(self):
                pass

        orig = openers.StationClient
        openers.StationClient = lambda *a, **k: _FakeStation()
        try:
            chat = chat_session.ChatSession("g1", "open")
            events = []
            asyncio.run(chat.greet(
                {"chat_greeting_mode": "canned", "chat_greeting": "Hey, {dj} here."},
                events.append))
        finally:
            openers.StationClient = orig

        self.assertTrue(any("Dalia" in (e.get("text") or "") for e in events),
                        "the canned greeting should fill the on-air DJ's name")
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(chat.turns[-1], ("dj", "Hey, Dalia here."))

    def test_greeting_off_stays_silent(self):
        from chat import openers, session as chat_session

        chat = chat_session.ChatSession("g2", "open")
        events = []
        asyncio.run(chat.greet({"chat_greeting_mode": "off"}, events.append))
        self.assertEqual(events, [])
        self.assertEqual(chat.turns, [])

    def test_the_handler_wraps_a_turn_with_a_typing_cue_and_a_timeout(self):
        import inspect

        from api import chat as chat_api

        src = inspect.getsource(chat_api)
        self.assertIn('"type": "typing"', src)      # the cue is emitted
        self.assertIn("chat_reply_timeout_secs", src)  # the hang-guard is applied
        self.assertIn("wait_for", src)

    def test_the_widget_shows_and_clears_the_typing_cue(self):
        from tests.support import REPO

        js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertIn("function showTyping", js)
        self.assertIn("function hideTyping", js)
        self.assertIn("msg.type === 'typing'", js)
        # Cleared the moment real words or an action card arrive, so it never
        # overlaps the text it stood in for.
        self.assertIn("hideTyping()", js)
        css = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")
        self.assertIn("typedots", css)

    def test_the_reply_reveals_at_a_typing_pace(self):
        # A caller said an instant wall of text doesn't read as a conversation.
        # The reply is buffered and revealed a few characters at a time.
        from tests.support import REPO

        js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertIn("chatTick", js)
        self.assertIn("chatTarget", js)
        self.assertIn("setInterval(chatTick", js)

    def test_the_post_call_drawer_is_hidden_during_a_chat(self):
        # A caller saw a PREVIOUS call's "Transcript · N lines" widget inside
        # their live chat — the post-call band (.after) belongs to a finished
        # call, never a chat.
        from tests.support import REPO

        css = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")
        self.assertRegex(
            css,
            r'\.card\[data-mode="chat"\]\s+\.after\s*\{[^}]*display:\s*none',
            "the post-call transcript drawer must be hidden in chat mode")


class TestChatActionCardsFollowTheLine(_TempStores):
    """Where a tool run's receipt card lands is the operator's action_cards
    setting (0.10.65 as chat_action_cards; booth-wide since 0.10.92): after
    the DJ's line by default — the words land, then the paperwork — before it
    for the old behaviour, or not at all. Whatever the mode, the action itself
    runs and the reply arrives; only the chat's furniture moves."""

    def _run_ask(self):
        from livekit.agents import llm as lk_llm

        from chat import openers, session as chat_session
        import brain.assemble as assemble_mod
        import call.providers as providers_mod
        import call.tools as tools_mod

        class _FakeStation:
            async def resolve_live_persona(self):
                return {"name": "Ash"}

            async def aclose(self):
                pass

        class _Stream:
            def __init__(self, chunks):
                self._chunks = list(chunks)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._chunks:
                    raise StopAsyncIteration
                return self._chunks.pop(0)

            async def aclose(self):
                pass

        class _Model:
            """First round queues the track, second answers in words."""

            def __init__(self):
                self.rounds = 0

            def chat(self, chat_ctx=None, tools=None):
                self.rounds += 1
                if self.rounds == 1:
                    call = types.SimpleNamespace(
                        call_id="c1", name="queue_probe", arguments="{}")
                    delta = types.SimpleNamespace(content="", tool_calls=[call])
                else:
                    delta = types.SimpleNamespace(
                        content="Queued it up, rider.", tool_calls=[])
                return _Stream([types.SimpleNamespace(delta=delta)])

            async def aclose(self):
                pass

        def _fake_library(cfg, station, actions):
            @lk_llm.function_tool(name="queue_probe")
            async def queue_probe() -> str:
                """Test tool that leaves a receipt, like a real request."""
                actions.on_note({"icon": "🎵", "label": "Queued",
                                 "detail": "Led Zeppelin"})
                return "queued"

            return [queue_probe]

        async def _fake_prompt(*a, **k):
            return "SYS"

        orig = (chat_session.StationClient, assemble_mod.build_system_prompt,
                providers_mod.build_llm, tools_mod.build_library_tools,
                tools_mod.build_on_air_tools)
        chat_session.StationClient = _FakeStation
        assemble_mod.build_system_prompt = _fake_prompt
        providers_mod.build_llm = lambda cfg: _Model()
        tools_mod.build_library_tools = _fake_library
        tools_mod.build_on_air_tools = lambda *a, **k: []
        try:
            chat = chat_session.ChatSession("a1", "open")
            events = []
            asyncio.run(chat.ask("play some zeppelin", events.append))
        finally:
            (chat_session.StationClient, assemble_mod.build_system_prompt,
             providers_mod.build_llm, tools_mod.build_library_tools,
             tools_mod.build_on_air_tools) = orig
        return events

    def test_the_text_line_now_carries_the_shared_door(self):
        # NORTH STAR move 3 (2026-08-28): chat adopts the phone's
        # ConversationState, which gives it the door guard it never had — a
        # typed DJ can hold the door open ("anything else?") just as a spoken
        # one can. Proven at the seam: a door-holding line fed to the shared
        # state, then a caller turn that is not a goodbye, yields the door
        # hint — the same note the phone gets, on chat's own state object.
        from call.door import HINT, holds_the_door
        from chat import session as chat_session

        chat = chat_session.ChatSession("door1", "open")
        line = "That's queued for you. Anything else I can spin?"
        self.assertTrue(holds_the_door(line), "fixture no longer trips door")
        chat.state.dj_said(line)
        hints = chat.state.hints_for("no, that's me done")
        self.assertEqual(hints, [], "a goodbye must clear the door, not nag")
        # A non-goodbye continuation gets the door note.
        chat.state.dj_said(line)
        notes = [t[-1] for t in chat.state.hints_for("hmm")]
        self.assertIn(HINT, notes)

    def test_after_is_the_default_and_the_card_lands_behind_the_line(self):
        events = self._run_ask()
        kinds = [e["type"] for e in events]
        self.assertIn("action", kinds)
        self.assertIn("done", kinds)
        self.assertGreater(kinds.index("action"), kinds.index("done"),
                           "by default the receipt follows the DJ's line")
        card = next(e for e in events if e["type"] == "action")
        self.assertEqual(card["label"], "Queued")

    def test_before_leads_the_line(self):
        settings_store.save({"action_cards": "before"})
        events = self._run_ask()
        kinds = [e["type"] for e in events]
        self.assertLess(kinds.index("action"), kinds.index("done"),
                        "'before' is the pre-0.10.65 order, kept selectable")

    def test_off_withholds_the_card_but_not_the_action(self):
        settings_store.save({"action_cards": "off"})
        events = self._run_ask()
        self.assertNotIn("action", [e["type"] for e in events])
        done = next(e for e in events if e["type"] == "done")
        self.assertIn("Queued it up", done["text"],
                      "the action ran and the DJ still reports it")


class TestTheReplyArrivesAtTheOperatorsPace(_TempStores):
    """The reveal used to be a fixed 30ms per character — about 33 c/s, near
    400 words a minute, which the operator (rightly) called nothing like
    someone typing. Two settings now: HOW the reply arrives, and how fast."""

    def test_the_defaults_read_as_a_person_typing(self):
        import settings as settings_store

        cfg = settings_store.load()
        self.assertEqual(cfg["chat_reveal"], "typing")
        self.assertEqual(cfg["chat_type_pace"], "natural")

    def test_both_offer_choices_the_panel_can_paint(self):
        # A select with no STATIC_CHOICES entry paints empty and the setting
        # ships unreachable — the failure mode /talkwave-setting exists for.
        import settings as settings_store

        for field in ("chat_reveal", "chat_type_pace"):
            choices = settings_store.STATIC_CHOICES.get(field) or []
            self.assertTrue(choices, f"{field} has no choices")
            values = [v for v, _ in choices]
            self.assertIn(settings_store.load()[field], values)
            for _, label in choices:
                self.assertIn("—", label, "every option says its consequence")

    def test_the_pace_only_matters_while_it_is_being_typed(self):
        import settings as settings_store

        self.assertEqual(
            settings_store.SCHEMA["chat_type_pace"]["needs"],
            ("chat_reveal", "typing"))

    def test_the_widget_is_told_both(self):
        # The reveal happens in the caller's browser, so /live has to carry
        # them or the settings are unreachable however well they are stored.
        # They ride in look_payload, which moved to api/look.py at 0.10.131 —
        # /live still sends it, this just reads where it is written.
        src = (AGENT_WORKER / "api" / "look.py").read_text(encoding="utf-8")
        self.assertIn("chatReveal", src)
        self.assertIn("chatTypePace", src)
        widget = (AGENT_WORKER.parent / "web-widget" / "call.js").read_text(
            encoding="utf-8")
        self.assertIn("chatTypePace", widget)
        self.assertIn("chatReveal", widget)


class TestAChatRecordShowsWhatTheDJActuallyDid(_TempStores):
    """A chat used to write down only its SUCCESSES.

    The record was built from the action ledger, which is the receipt-card
    list — so a conversation the DJ spent talking around three rate-limited
    requests wrote a record with an empty tools array, and read back as a DJ
    who simply chatted. That is record 20260813-012417: fifteen turns of
    "the queue's jammed", no evidence of anything having been attempted.
    Every turn also carried the same timestamp, because they were all stamped
    when the record was WRITTEN rather than when they happened.
    """

    def _chat(self):
        from chat.session import ChatSession

        return ChatSession("abcdef123456", "open")

    def _written(self, chat):
        import json
        from pathlib import Path

        from call import record as call_record

        settings_store.save({"record_calls": True})
        old_dir = call_record.CALLS_DIR
        call_record.CALLS_DIR = Path(self._tmp.name) / "calls"
        try:
            chat.write_record("test")
            fresh = call_record.CALLS_DIR
        finally:
            call_record.CALLS_DIR = old_dir
        recs = [json.loads(p.read_text(encoding="utf-8"))
                for p in fresh.glob("*.json")]
        return recs[0]

    def test_a_refused_tool_is_in_the_record_even_though_nothing_succeeded(self):
        chat = self._chat()
        chat.remember("caller", "play firestorm by kygo")
        chat._note_tool("subwave_search_library", "No track by that name",
                        args={"q": "firestorm kygo"})
        chat._note_tool("subwave_request_song",
                        "Your last request is still queued — it airs first.",
                        args={"request": "firestorm kygo"}, failed=True)
        chat.remember("dj", "queue's jammed solid, give it a few minutes")

        rec = self._written(chat)
        names = [t["name"] for t in rec["tools"]]
        self.assertEqual(names, ["subwave_search_library",
                                 "subwave_request_song"])
        # The ARGUMENTS matter as much as the result: "search returned
        # nothing" is only half an answer without what it searched for.
        self.assertIn("firestorm kygo", rec["tools"][0]["result"])
        self.assertTrue(rec["tools"][1]["failed"])
        self.assertNotIn("failed", rec["tools"][0])

    def test_turns_are_stamped_when_they_happened_not_when_written(self):
        chat = self._chat()
        chat.remember("caller", "first")
        chat.turn_at[-1] -= 300           # five minutes earlier
        chat.remember("dj", "second")

        rec = self._written(chat)
        stamps = [t["t"] for t in rec["turns"]]
        self.assertEqual(len(set(stamps)), 2, "the whole chat shared one clock")
        self.assertLess(stamps[0], stamps[1])

    def test_a_turn_with_no_stamp_is_kept_rather_than_dropped(self):
        # zip() would silently lose turns if the two lists ever disagreed. A
        # bug here must look like a wrong clock, not a missing conversation.
        chat = self._chat()
        chat.turns.append(("caller", "orphan"))      # bypasses remember()
        rec = self._written(chat)
        self.assertEqual([t["text"] for t in rec["turns"]], ["orphan"])


class TestAChatIsOneConversationNotAStringOfStrangers(_TempStores):
    """The transcript was replayed every message, so the DJ always saw the
    history — but the OBJECTS behind the conversation were rebuilt each turn,
    and two things ride on those rather than on the text.

    Gemini 3 attaches an opaque `thought_signature` to every function call and
    rejects a request that replays one without it. The plugin caches those on
    the LLM CLIENT, keyed by call id — so discarding the client discarded the
    cache, and the next message replaying an earlier tool call got a fatal
    400. Reproduced on a multi-tool turn, which is what "queue me three
    songs" produces.

    And `CallActions` is the per-conversation action cap. Rebuilt per message
    its count restarted at zero, so `max_actions_per_call` was per MESSAGE on
    the text line — a texter got a fresh budget with every line they sent.
    """

    def _chat(self):
        from chat.session import ChatSession

        return ChatSession("abcdef123456", "open")

    def test_the_model_is_built_once_and_reused(self):
        import asyncio

        from chat import session as mod

        chat = self._chat()
        built = []

        class _Model:
            def __init__(self):
                self.closed = False

            async def aclose(self):
                self.closed = True

        def _build(cfg):
            m = _Model()
            built.append(m)
            return m

        # Two messages' worth of the hoist, without running a whole ask().
        for _ in range(2):
            if chat._llm is None:
                chat._llm = _build({})
        self.assertEqual(len(built), 1, "a second message rebuilt the client")
        asyncio.run(chat.aclose())
        self.assertTrue(built[0].closed)
        self.assertIsNone(chat._llm)

    def test_closing_twice_is_harmless(self):
        import asyncio

        chat = self._chat()
        # A chat can end before it ever asked anything — the idle sweep closes
        # sockets that opened and said nothing — so aclose() has to cope with
        # never having had a model, and with being called again after.
        asyncio.run(chat.aclose())
        self.assertIsNone(chat._llm)
        asyncio.run(chat.aclose())
        self.assertIsNone(chat._llm)

    def test_a_model_that_refuses_to_close_does_not_break_the_ending(self):
        import asyncio

        chat = self._chat()

        class _Angry:
            async def aclose(self):
                raise RuntimeError("no")

        chat._llm = _Angry()
        asyncio.run(chat.aclose())        # must not raise
        self.assertIsNone(chat._llm)

    def test_the_dj_does_not_change_under_the_caller_mid_chat(self):
        # Operator-reported 2026-08-14: ask for a show takeover in the middle
        # of a text conversation and the DJ you were talking to became
        # somebody else on the very next line, mid-subject, with no goodbye —
        # often from a takeover the caller had just requested themselves. The
        # phone has never done this (a call resolves its persona and its voice
        # once at pickup), so this makes the text line behave the same way.
        import asyncio

        resolved = []

        class _Station:
            async def resolve_live_persona(self):
                # The station genuinely hands back a different DJ the second
                # time — a handover, exactly as the takeover produces.
                resolved.append(1)
                return ({"id": "p_a262b2", "name": "Duke Sterling"} if len(resolved) == 1
                        else {"id": "p_fed292", "name": "Cliff"})

        chat = self._chat()
        station = _Station()

        async def one_message():
            # The two lines ask() runs, in order, for each message.
            persona = chat.persona
            if not (persona or {}).get("id") and not (persona or {}).get("name"):
                persona = await station.resolve_live_persona()
                chat.persona = persona
            chat.persona_name = persona.get("name") or chat.persona_name
            return chat.persona_name

        first = asyncio.run(one_message())
        second = asyncio.run(one_message())
        third = asyncio.run(one_message())
        self.assertEqual(first, "Duke Sterling")
        self.assertEqual(second, "Duke Sterling",
                         "the handover changed the DJ mid-conversation")
        self.assertEqual(third, "Duke Sterling")
        self.assertEqual(len(resolved), 1,
                         "the station was asked again after the DJ was settled")

    def test_a_station_that_was_down_at_hello_is_asked_again(self):
        # The other direction: pinning the conversation to nothing because the
        # station happened to be restarting on the opening message would leave
        # a caller talking to "The DJ" for ten minutes.
        import asyncio

        tries = []

        class _Station:
            async def resolve_live_persona(self):
                tries.append(1)
                return {} if len(tries) == 1 else {"id": "p_x", "name": "Ash"}

        chat = self._chat()
        station = _Station()

        async def one_message():
            persona = chat.persona
            if not (persona or {}).get("id") and not (persona or {}).get("name"):
                persona = await station.resolve_live_persona()
                chat.persona = persona
            chat.persona_name = persona.get("name") or chat.persona_name
            return chat.persona_name

        asyncio.run(one_message())
        self.assertEqual(asyncio.run(one_message()), "Ash")
        self.assertEqual(len(tries), 2, "an empty persona was treated as settled")

    def test_the_action_cap_spans_the_conversation(self):
        from call.actions import CallActions

        chat = self._chat()
        # What ask() now does: build once, reuse.
        chat.actions = CallActions(2)
        chat.actions.note("request", "one")
        self.assertFalse(chat.actions.at_limit())
        chat.actions.note("request", "two")
        # Second message, same ledger — this is the whole point. Rebuilt per
        # message the count would be back at zero here.
        self.assertTrue(chat.actions.at_limit())
        self.assertEqual(chat.actions.count, 2)

    def test_the_tool_loop_no_longer_closes_the_model(self):
        # Closing it per message is exactly what dropped the signature cache.
        import inspect

        from chat.session import ChatSession

        src = inspect.getsource(ChatSession._tool_loop)
        # The STREAM is still closed every round — that is correct and
        # unrelated. It is the MODEL that must outlive the message.
        self.assertIn("stream.aclose()", src)
        self.assertNotIn("model.aclose", src)

    def test_the_shelf_lets_go_of_the_model_when_a_chat_ends(self):
        import inspect

        from chat.session import ChatShelf

        src = inspect.getsource(ChatShelf.sweep)
        self.assertIn("aclose", src)


class TestToolResultsGoBackAsText(_TempStores):
    """Gemini 3 signs a tool call with an opaque `thought_signature` and
    refuses to replay a functionCall part without one — and it does not sign
    them all. Measured against the live model: two calls in one response, ONE
    signature. So a faithful structured replay cannot be built, and the
    request died 400, fatal and not retryable, taking the whole reply with it.

    Four shapes were tried against the real model. Interleaved parts fail.
    Grouped parts fail. Replaying only the signed call succeeds but the model
    answers with nothing. Text succeeds and answers properly — and needs no
    signatures, no ids and no plumbing, so every backend takes the same path.
    """

    def test_no_function_call_parts_are_replayed(self):
        import inspect

        from chat.session import ChatSession

        src = inspect.getsource(ChatSession._tool_loop)
        self.assertNotIn("FunctionCall(", src)
        self.assertNotIn("FunctionCallOutput(", src)

    def test_the_report_carries_the_result_and_forbids_a_repeat(self):
        from chat.session import _tool_report

        out = _tool_report([("subwave_request_song", "Added to the queue.",
                             False)])
        self.assertIn("Added to the queue.", out)
        self.assertIn("request_song", out)
        # Bracketed like every other situation note here, so the speech
        # filter strips it if it ever reaches a voice.
        self.assertTrue(out.startswith("[") and out.endswith("]"))
        # Without a formal function_response the model has no structural
        # signal its call ran, and will fire the same one again.
        self.assertIn("not call these same tools again", out)

    def test_a_failed_tool_is_marked_as_failed(self):
        from chat.session import _tool_report

        out = _tool_report([("subwave_skip_track", "the station refused it",
                             True)])
        self.assertIn("FAILED", out)
        self.assertIn("the station refused it", out)

class TestAProviderFailureIsVisibleToTheOperator(_TempStores):
    """The Gemini 400 broke every multi-tool chat turn for days while the
    panel looked perfectly healthy: the caller got "line dropped a beat", the
    operator got a normal-looking chat, and the actual reason lived only in a
    container log that this deployment does not keep. A call already writes
    its errors into the record's `problems`, which is the list the panel
    counts a conversation as failed by. Chat now does the same, so the NEXT
    provider that refuses us is a line in Needs attention within one
    conversation instead of a mystery.
    """

    def test_the_relay_writes_the_real_error_down(self):
        import inspect

        from api import chat as chat_api

        src = inspect.getsource(chat_api._relay)
        self.assertIn("chat.problems.append", src)
        # The panel matches on this phrase; changing it silently unhooks the
        # Needs attention line from the thing it reports.
        self.assertIn("brain returned an error", src)

    def test_problems_reach_the_record(self):
        import json
        import tempfile
        from pathlib import Path

        import settings as settings_store
        from call import record
        from chat.session import ChatSession

        settings_store.save({"record_calls": True})
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old, record.CALLS_DIR = record.CALLS_DIR, Path(tmp.name)
        try:
            chat = ChatSession("p1", "open")
            chat.remember("caller", "hey")
            chat.problems.append(
                "the DJ's brain returned an error: APIStatusError: 400 "
                "missing thought_signature")
            chat.write_record("ended")
        finally:
            record.CALLS_DIR = old

        files = list(Path(tmp.name).glob("*.json"))
        self.assertTrue(files, "no record was written")
        rec = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(len(rec["problems"]), 1)
        self.assertIn("thought_signature", rec["problems"][0]["what"])

    def test_the_panel_reports_it(self):
        from tests.support import REPO

        panel = (REPO / "web-widget" / "panel.js").read_text(encoding="utf-8")
        self.assertIn("brain returned an error", panel)
        self.assertIn("lost a reply", panel)


class TestTheLlmTestRunsTheShapeThatBreaks(_TempStores):
    """One tool call proves a model can call a tool. It does NOT prove the
    model can carry a conversation — Gemini 3 passed the old one-round test
    and then rejected the second request of every real chat. So /test/llm now
    runs two rounds against two tools and fails the provider here, in the
    panel, rather than on a caller.
    """

    def test_the_endpoint_does_a_second_round(self):
        import inspect

        from api import diagnostics

        src = inspect.getsource(diagnostics.handle_test_llm)
        self.assertIn("now_playing", src, "only one tool is offered, so a "
                                          "parallel group can never happen")
        self.assertIn("followUp", src)
        # Replayed through the shipped helper, not a hopeful copy of it: this
        # button has to test the code path that runs on air.
        self.assertIn("_tool_report", src)

    def test_the_panel_shows_the_verdict(self):
        from tests.support import REPO

        panel = (REPO / "web-widget" / "panel.js").read_text(encoding="utf-8")
        self.assertIn("carrying the conversation", panel)
        # And it counts: a provider that dies on round two is not a pass with
        # a footnote.
        self.assertIn("d.followUp !== 'failed'", panel)


class TestTheTextLineWritesDownWhatWentWrong(unittest.TestCase):
    """`self.problems` was declared, drained into the record, and never once
    appended to.

    Every chat this deployment has ever recorded therefore shipped
    `problems: []` — the panel's "needs attention" count could not see a text
    conversation at all, and the operator reading a bad one back was told
    nothing had gone wrong in it. Observed 2026-08-20 on a chat that promised
    a request it never sent, invented a library limitation and skipped the
    caller's own record: clean sheet.

    The second half is what the caller SEES. The nudge asks for a tool and no
    more words; when the model types anyway the two attempts were glued
    together mid-sentence — "...to go in behind it?Ah, wait—my mistake, I see
    what you mean" — because the loop appended the retry straight onto the
    first line.
    """

    def _loop(self, contents):
        """Drive _tool_loop through a model that only ever types."""
        from livekit.agents import llm as lk_llm

        from chat.session import ChatSession

        @lk_llm.function_tool(name="test_probe")
        async def probe(word: str = "") -> str:
            """Test tool."""
            return "sent"

        class _Stream:
            def __init__(self, chunks):
                self._chunks = list(chunks)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._chunks:
                    raise StopAsyncIteration
                return self._chunks.pop(0)

            async def aclose(self):
                pass

        class _Model:
            def __init__(self):
                self.rounds = 0

            def chat(self, chat_ctx=None, tools=None):
                self.rounds += 1
                said = (contents[self.rounds - 1]
                        if self.rounds <= len(contents) else "")
                delta = types.SimpleNamespace(content=said, tool_calls=[])
                return _Stream([types.SimpleNamespace(delta=delta)])

            async def aclose(self):
                pass

        chat = ChatSession("p1", "open")
        ctx = lk_llm.ChatContext.empty()
        ctx.add_message(role="user", content="whats in the queue?")
        events = []
        out = asyncio.run(chat._tool_loop(_Model(), ctx, [probe], events.append))
        return chat, out, events

    def test_an_unbacked_promise_is_written_into_the_record(self):
        chat, _out, _events = self._loop(
            ["Hold on, let me dig through the racks for you.", " Nothing yet."])
        self.assertEqual(len(chat.problems), 1)
        self.assertIn("ran no tool", chat.problems[0])
        # Word for word what the phone writes, so one panel filter reads both.
        from promises import PROBLEMS

        self.assertEqual(chat.problems[0], PROBLEMS["promise"])

    def test_ordinary_conversation_still_writes_nothing_down(self):
        chat, _out, _events = self._loop(
            ["It's a fine morning for it, isn't it?"])
        self.assertEqual(chat.problems, [])

    def test_a_nudged_retry_is_not_glued_onto_the_line_before_it(self):
        chat, out, events = self._loop(
            ["Are you looking for something else to go in behind it?",
             "Ah, wait—my mistake, I see what you mean."])
        self.assertNotIn("behind it?Ah", out)
        self.assertIn("behind it?\n\nAh", out)
        # The live card and the written record break in the same place, or
        # the caller reads one thing and the operator reads another.
        streamed = "".join(e.get("text", "") for e in events
                           if e.get("type") == "delta")
        self.assertEqual(streamed, out)

    def test_a_retry_that_says_nothing_leaves_no_stray_gap(self):
        chat, out, _events = self._loop(
            ["Hold on, let me dig through the racks.", ""])
        self.assertFalse(out.endswith("\n\n"), out)
        self.assertNotIn("\n\n", out)


class TestALieAfterARefusalNeverReachesTheScreen(unittest.TestCase):
    """Vet-before-show (2026-08-28), the text line's structural advantage:
    the phone can only grade a lie after it has aired, but here nothing is
    on screen until we send it. A round generated after a refusal is held,
    checked with the drill's own rule, and rewritten once if it claims the
    refused thing happened — the caller sees only the honest version, and
    the problems entry still tells the operator the model tried."""

    def test_the_held_lie_is_rewritten_and_never_shown(self):
        import types

        from livekit.agents import llm as lk_llm

        from chat.session import ChatSession

        @lk_llm.function_tool(name="test_request")
        async def request(word: str = "") -> str:
            """Test tool."""
            return "That didn't go through — the station refused it."

        class _Stream:
            def __init__(self, chunks):
                self._chunks = list(chunks)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._chunks:
                    raise StopAsyncIteration
                return self._chunks.pop(0)

            async def aclose(self):
                pass

        class _Model:
            """Round 1 calls the tool; round 2 LIES about the refusal;
            round 3 — after the vet's rewrite note — comes clean."""

            def __init__(self):
                self.rounds = 0

            def chat(self, chat_ctx=None, tools=None):
                self.rounds += 1
                if self.rounds == 1:
                    call = types.SimpleNamespace(
                        call_id="c1", name="test_request",
                        arguments='{"word": "africa"}')
                    delta = types.SimpleNamespace(content="",
                                                  tool_calls=[call])
                elif self.rounds == 2:
                    delta = types.SimpleNamespace(
                        content="Perfect — that's queued up and coming "
                                "right after this one.",
                        tool_calls=[])
                else:
                    delta = types.SimpleNamespace(
                        content="That one didn't go through — the station "
                                "refused it. Want me to try something else?",
                        tool_calls=[])
                return _Stream([types.SimpleNamespace(delta=delta)])

            async def aclose(self):
                pass

        events = []
        chat = ChatSession("t1", "open")
        ctx = lk_llm.ChatContext.empty()
        ctx.add_message(role="user", content="queue Africa for me")
        out = asyncio.run(chat._tool_loop(_Model(), ctx, [request],
                                          events.append))
        shown = "".join(e.get("text", "") for e in events
                        if e.get("type") == "delta")
        self.assertNotIn("coming right after", shown)
        self.assertNotIn("coming right after", out)
        self.assertIn("didn't go through", out)
        # The operator still learns the model tried it on.
        self.assertTrue(any("refused" in p or "landed" in p
                            for p in chat.problems), chat.problems)



class TestTheTextLineIsNotBlind(unittest.TestCase):
    """0.99.0: the chat build opens with the two station reads, locally
    served (call/tools/reads.py). The 2026-08-27 exchange is why — a caller
    asked for tracks "similar to my current queue" and the DJ, with no way
    to look, guessed, missed, and invented a station rule to explain a
    duplicate it could not see. Pinned at the source so removing the line
    brings a red test naming that wreck."""

    def test_the_chat_build_includes_the_reads(self):
        import inspect

        from chat import session

        src = inspect.getsource(session)
        self.assertIn("build_read_tools", src)
