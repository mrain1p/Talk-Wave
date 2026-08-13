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
            # The example table proves the exact shape the widget parses.
            self.assertIn("| Time", text)

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
        # The tool's result made it into the context for round two.
        outputs = [i for i in ctx.items
                   if getattr(i, "type", "") == "function_call_output"]
        self.assertEqual(len(outputs), 1)
        self.assertIn("probe saw hello", outputs[0].output)

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
        outputs = [i for i in ctx.items
                   if getattr(i, "type", "") == "function_call_output"]
        self.assertEqual(len(outputs), 1, "the promised tool never ran")
        # The caller keeps the line they already read, and the outcome.
        self.assertIn("dedication", out)
        self.assertIn("away", out)

    def test_ordinary_chat_is_not_given_an_extra_round(self):
        # The nudge costs a model round, so it fires only on the openers the
        # conduct asks for — never on a reply that was simply conversation.
        from chat.session import _PROMISES_ACTION

        for promise in ("Let me dig through the racks",
                        "hold on, checking what we've got",
                        "On it — I'll get that queued"):
            self.assertTrue(_PROMISES_ACTION.search(promise), promise)
        for chatter in ("That's a grand one for a mate.",
                        "The Chieftains were on top form that year.",
                        "Nice — good taste."):
            self.assertFalse(_PROMISES_ACTION.search(chatter), chatter)


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

    def test_the_typed_line_takes_back_the_spoken_speak_first_rule(self):
        # _tools is imported verbatim from the spoken conduct and tells the DJ
        # to say a line BEFORE reaching for the tool — right on a phone call,
        # where silence is dead air, and the exact instruction that broke the
        # text line. The typed note must countermand it, or the prompt asks
        # for both.
        from brain import conduct, conduct_chat

        spoken = conduct.rules({"allow_requests": True})
        typed = conduct_chat.rules({"allow_requests": True})
        self.assertIn("BEFORE you reach for the tool", spoken)
        # Wrapped at ~76 columns like the rest of the prompt, so match a
        # fragment that cannot straddle the line break.
        self.assertIn("does not apply", typed)
        self.assertIn("Call the tool first", typed)

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
        from chat import session as chat_session
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

        orig = (chat_session.StationClient, assemble_mod.build_system_prompt,
                providers_mod.build_llm)
        chat_session.StationClient = _FakeStation
        assemble_mod.build_system_prompt = _fake_prompt
        providers_mod.build_llm = lambda cfg: _Model()
        try:
            chat = chat_session.ChatSession("n1", "open")
            chat.turns.append(("dj", "Hey, Ash here."))
            events = []
            asyncio.run(chat.nudge({}, events.append))
        finally:
            (chat_session.StationClient, assemble_mod.build_system_prompt,
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
        from chat import session as chat_session

        persona = {"name": "Dalia", "station": "SUB/WAVE"}

        class _FakeStation:
            async def resolve_live_persona(self):
                return persona

            async def aclose(self):
                pass

        orig = chat_session.StationClient
        chat_session.StationClient = lambda *a, **k: _FakeStation()
        try:
            chat = chat_session.ChatSession("g1", "open")
            events = []
            asyncio.run(chat.greet(
                {"chat_greeting_mode": "canned", "chat_greeting": "Hey, {dj} here."},
                events.append))
        finally:
            chat_session.StationClient = orig

        self.assertTrue(any("Dalia" in (e.get("text") or "") for e in events),
                        "the canned greeting should fill the on-air DJ's name")
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(chat.turns[-1], ("dj", "Hey, Dalia here."))

    def test_greeting_off_stays_silent(self):
        from chat import session as chat_session

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

        from chat import session as chat_session
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
        src = (AGENT_WORKER / "api" / "live.py").read_text(encoding="utf-8")
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
