"""The text line: who gets in, what the clocks close, and what the typed
brain is told.

Split by subject like every module here; the WebSocket's frame-level gate
lives in api/chat.py, the conversation in chat/session.py, the register in
brain/conduct_chat.py.
"""

from __future__ import annotations

import asyncio
import time
import types
import unittest

import settings as settings_store
from tests.support import _TempStores


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
        # And the call list names a show/DJ change as a takeover to DO.
        self.assertIn("TAKEOVER", conduct.rules({}))

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
        # happened": wait for the tool result, and say plainly if it failed.
        self.assertIn("Wait for the tool's result", text)

    def test_the_defaults_greet_and_time_out(self):
        # A silent line reads as broken, so greeting is ON by default; and a
        # stalled model must not spin a typing dot forever, so a reply timeout
        # exists by default.
        self.assertEqual(settings_store.FIELDS["chat_greeting_mode"][1], "canned")
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
