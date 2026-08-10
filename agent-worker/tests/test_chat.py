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
