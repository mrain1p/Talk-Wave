"""One typed conversation with the on-air DJ, and the shelf they all live on.

A ChatSession is the chat-mode sibling of call/session.py's CallSession,
minus everything that exists because of audio: no STT, no TTS, no VAD, no
room, no on-air hold (the DJ can type while the broadcast talks). What
remains is the same brain (assemble, mode="chat"), the same tool wrappers
with the same action ledger and caps, and the same record archive with
`kind: "chat"`.

The tool loop is hand-rolled against the LLM plugin — the same
`providers.build_llm` a call uses, so "same brain" is literal — because the
SDK's AgentSession text support is room-bound. Precedent for driving
`llm.chat()` directly is already in-tree: the voicemail fresh greeting and
the back-to-air handoff.

Resumable per browser: the widget holds the chat id in localStorage and the
transcript lives HERE, in memory, until the idle clock or a ceiling ends it.
In memory on purpose — a token-server restart ends every open chat, which is
honest, and the durable artefact is the record written at the end, not the
live transcript.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

import settings as settings_store
from station import StationClient

log = logging.getLogger("callin.chat")

# One LLM pass may fan out into tools and come back; more than a few rounds
# in a single message means the model is wandering, not working.
MAX_TOOL_ROUNDS = 4

# The prompt carries this many of the newest turns. A resumable chat can
# outgrow any context window; the record keeps everything, the prompt keeps
# the conversation's living end.
PROMPT_TURNS = 30

# The booth's opening line when nothing is configured — filled with the DJ's
# name so a fresh chat is greeted by whoever is actually on air.
DEFAULT_CHAT_GREETING = "You're through to the booth — {dj} here. What's on your mind?"


def _fill_greeting(text: str, persona: dict) -> str:
    """{station} {dj} {show} in the canned greeting, same tokens the card and
    the voicemail greeting fill."""
    return (text.replace("{station}", persona.get("station") or "the station")
                .replace("{dj}", persona.get("name") or "the DJ")
                .replace("{show}", persona.get("show") or "")).strip()


class ChatSession:
    def __init__(self, chat_id: str, tier: str) -> None:
        self.id = chat_id
        self.tier = tier
        self.started = time.time()
        self.last_active = time.time()
        self.messages = 0
        # (who, text) in order — "caller" / "dj". The record is written from
        # this, and the prompt reads its tail.
        self.turns: list[tuple[str, str]] = []
        self.actions_log: list[tuple[str, str]] = []   # (tool, receipt)
        self.persona_name = ""
        # One message in flight per chat: two tabs racing the same id would
        # interleave two tool loops through one transcript.
        self.lock = asyncio.Lock()

    async def greet(self, cfg: dict, on_event) -> None:
        """The booth speaks first when a fresh chat opens — a text line that
        answers with silence until the caller types reads as broken. "canned"
        formats the operator's line (no model cost); "fresh" writes one in
        persona. Either way it lands as the DJ's opening turn, streamed like
        any reply so the widget renders it identically.
        """
        mode = str(cfg.get("chat_greeting_mode") or "off")
        if mode == "off":
            return

        import secrets_store
        secrets_store.apply_to_env()

        station = StationClient()
        try:
            persona = await station.resolve_live_persona()
            self.persona_name = persona.get("name") or self.persona_name
            if mode == "fresh":
                text = await self._fresh_greeting(cfg, station, persona)
            else:
                text = _fill_greeting(
                    str(cfg.get("chat_greeting") or "").strip()
                    or DEFAULT_CHAT_GREETING, persona)
            text = (text or "").strip()
            if not text:
                return
            on_event({"type": "delta", "text": text})
            self.turns.append(("dj", text))
            self.last_active = time.time()
            on_event({"type": "done", "text": text, "dj": self.persona_name})
        finally:
            await station.aclose()

    async def nudge(self, cfg: dict, on_event) -> None:
        """The caller has gone quiet with the ball in their court. ONE short,
        in-persona line to keep the line breathing — a text chat that sits
        silent after its own last message feels dead and turn-based (operator's
        ask). Explicitly NOT "are you still there?": this is a conversation, not
        a roll call. No tools; fired once per silence by the WS idle timer."""
        import secrets_store
        secrets_store.apply_to_env()

        from livekit.agents import llm as lk_llm

        from brain.assemble import build_system_prompt
        from call.providers import build_llm

        station = StationClient()
        try:
            persona = await station.resolve_live_persona()
            self.persona_name = persona.get("name") or self.persona_name
            prompt = await build_system_prompt(station, persona, cfg=cfg,
                                               mode="chat")
            ctx = lk_llm.ChatContext.empty()
            ctx.add_message(role="system", content=prompt)
            for who, said in self.turns[-12:]:
                ctx.add_message(role="user" if who == "caller" else "assistant",
                                content=said)
            ctx.add_message(role="user", content=(
                "[The caller has gone quiet since your last message — they "
                "haven't typed for a little while. Type ONE short, warm line in "
                "your own voice to keep the conversation breathing: pick the "
                "thread back up, or lightly offer a next thing. NOT 'are you "
                "still there?' — this is a chat, not a roll call — and no "
                "question they must answer to stay. A light nudge, then leave "
                "it with them.]"))
            model = build_llm(cfg)
            out = ""
            try:
                stream = model.chat(chat_ctx=ctx)
                async for chunk in stream:
                    delta = getattr(chunk, "delta", None)
                    if delta and delta.content:
                        out += delta.content
                        on_event({"type": "delta", "text": delta.content})
                await stream.aclose()
            finally:
                try:
                    await model.aclose()
                except Exception:                              # noqa: BLE001
                    pass
            out = out.strip()
            if out:
                self.turns.append(("dj", out))
                self.last_active = time.time()
            on_event({"type": "done", "text": out, "dj": self.persona_name})
        finally:
            await station.aclose()

    async def _fresh_greeting(self, cfg, station, persona) -> str:
        """One short in-persona opener, written at open. The same prompt a
        reply uses, with no tools and a single instruction — the voicemail
        fresh greeting, at chat size."""
        from livekit.agents import llm as lk_llm

        from brain.assemble import build_system_prompt
        from call.providers import build_llm

        prompt = await build_system_prompt(station, persona, cfg=cfg,
                                           mode="chat")
        ctx = lk_llm.ChatContext.empty()
        ctx.add_message(role="system", content=prompt)
        ctx.add_message(role="user", content=(
            "[The caller just opened the text line and has not typed yet. "
            "Greet them first — one short, warm line in your own voice, no "
            "question needed, just open the door.]"))
        model = build_llm(cfg)
        out = ""
        try:
            stream = model.chat(chat_ctx=ctx)
            async for chunk in stream:
                delta = getattr(chunk, "delta", None)
                if delta and delta.content:
                    out += delta.content
            await stream.aclose()
        finally:
            try:
                await model.aclose()
            except Exception:                                  # noqa: BLE001
                pass
        return out.strip()

    async def ask(self, text: str, on_event) -> None:
        """One caller message -> streamed DJ reply.

        `on_event(dict)` receives, in order: any number of
        {"type": "action", ...} cards (the same shape the call publishes on
        talkwave.action), any number of {"type": "delta", "text": ...}
        fragments, then {"type": "done", "text": <full reply>}.

        Settings are re-read here — the per-message equivalent of the
        per-call invariant — and the persona is whoever is on air NOW, so a
        chat that spans a handover changes DJ mid-conversation, the same rule
        the phone follows.
        """
        from livekit.agents import llm as lk_llm

        from brain.assemble import build_system_prompt
        from call.actions import CallActions
        from call.air import OnAirGuard
        from call.providers import build_llm
        from call.tools import build_library_tools, build_on_air_tools

        # Keys entered in the settings page live in their own store; push
        # them into the environment before building the model — the same
        # line the worker runs at job start, needed here because THIS
        # process (the token server) never ran it and the SDKs read env.
        import secrets_store
        secrets_store.apply_to_env()

        cfg = settings_store.permissions_for(settings_store.load(), self.tier)
        station = StationClient()
        try:
            persona = await station.resolve_live_persona()
            self.persona_name = persona.get("name") or self.persona_name
            prompt = await build_system_prompt(station, persona, cfg=cfg,
                                               mode="chat")

            actions = CallActions(int(cfg.get("max_actions_per_call") or 0))
            actions.on_note = lambda card: on_event({"type": "action", **card})
            # Disabled guard: a typed DJ never needs holding off the air, but
            # the broadcast wrappers still want the shared clear-air API.
            guard = OnAirGuard(station, {"avoid_on_air_overlap": False})
            tools = build_library_tools(cfg, station, actions) + \
                build_on_air_tools(cfg, station, actions, guard, guarded=False)

            ctx = lk_llm.ChatContext.empty()
            ctx.add_message(role="system", content=prompt)
            for who, said in self.turns[-PROMPT_TURNS:]:
                ctx.add_message(role="user" if who == "caller" else "assistant",
                                content=said)
            ctx.add_message(role="user", content=text)

            self.turns.append(("caller", text))
            self.messages += 1
            self.last_active = time.time()

            reply = await self._tool_loop(build_llm(cfg), ctx, tools, on_event)
            if not reply.strip():
                # The same promise the phone makes: a model outage never
                # becomes silence.
                reply = ("Line's a bit crackly at my end — say that again "
                         "for me?")
            self.turns.append(("dj", reply))
            self.actions_log.extend(
                (kind, detail) for kind, detail in actions.taken)
            self.last_active = time.time()
            on_event({"type": "done", "text": reply,
                      "dj": self.persona_name})
        finally:
            await station.aclose()

    async def _tool_loop(self, model, ctx, tools, on_event) -> str:
        """Stream the model, run any tools it calls, feed results back,
        repeat until it answers in words. The loop the voice SDK runs for a
        call, at chat size."""
        from livekit.agents import llm as lk_llm

        by_name = {t.info.name: t for t in tools}
        reply = ""
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                text_out, calls = "", []
                stream = model.chat(chat_ctx=ctx, tools=tools)
                async for chunk in stream:
                    delta = getattr(chunk, "delta", None)
                    if not delta:
                        continue
                    if delta.content:
                        text_out += delta.content
                        on_event({"type": "delta", "text": delta.content})
                    if delta.tool_calls:
                        calls.extend(delta.tool_calls)
                await stream.aclose()
                reply += text_out
                if not calls:
                    break
                for call in calls:
                    ctx.insert(lk_llm.FunctionCall(
                        call_id=call.call_id, name=call.name,
                        arguments=call.arguments or "{}"))
                    result, is_error = await self._run_tool(by_name, call)
                    ctx.insert(lk_llm.FunctionCallOutput(
                        call_id=call.call_id, name=call.name,
                        output=result, is_error=is_error))
            else:
                log.warning("chat %s: model still calling tools after %d "
                            "rounds — answering with what it has",
                            self.id, MAX_TOOL_ROUNDS)
        finally:
            try:
                await model.aclose()
            except Exception:                                  # noqa: BLE001
                pass
        return reply

    async def _run_tool(self, by_name, call) -> tuple[str, bool]:
        tool = by_name.get(call.name)
        if tool is None:
            return f"no such tool: {call.name}", True
        try:
            kwargs = json.loads(call.arguments or "{}")
        except Exception:                                      # noqa: BLE001
            kwargs = {}
        try:
            out = await tool(**kwargs)
            log.info("chat %s tool: %s -> %.90s", self.id, call.name, out)
            return str(out), False
        except Exception as e:                                 # noqa: BLE001
            log.warning("chat %s tool %s failed: %s", self.id, call.name, e)
            return "that didn't work — tell them plainly, in your own words", True

    def write_record(self, reason: str) -> None:
        """The chat's durable trace, in the same archive the calls use —
        `kind: "chat"` is what the viewer renders as the label, exactly the
        voicemail pattern."""
        cfg = settings_store.load()
        if not cfg.get("record_calls") or not self.turns:
            return
        try:
            from call.record import CallRecord

            rec = CallRecord(f"chat-{self.id[-12:]}",
                             {"name": self.persona_name}, cfg,
                             tier=self.tier, started=self.started)
            rec.data["kind"] = "chat"
            for who, said in self.turns:
                rec.turn(who, said)
            for kind, detail in self.actions_log:
                rec.tool(kind, detail)
            rec.write(reason=reason, keep=int(cfg.get("record_keep") or 0))
        except Exception as e:                                 # noqa: BLE001
            log.warning("could not write chat record %s: %s", self.id, e)


class ChatShelf:
    """Every open chat, keyed by id, with the clocks that end them.

    The sweep runs from the API layer's periodic hook rather than a task per
    chat: a chat is idle state, not work.
    """

    def __init__(self) -> None:
        self.chats: dict[str, ChatSession] = {}

    def get_or_open(self, chat_id: str | None, tier: str,
                    cfg: dict) -> ChatSession | None:
        """An existing id resumes its chat; anything else opens a fresh one,
        subject to the ceiling on how many can be open at once."""
        chat = self.chats.get(chat_id or "")
        if chat is not None:
            return chat
        limit = int(cfg.get("max_open_chats") or 0)
        if limit and len(self.chats) >= limit:
            self.sweep(cfg)
            if len(self.chats) >= limit:
                return None
        fresh = ChatSession(uuid.uuid4().hex, tier)
        self.chats[fresh.id] = fresh
        return fresh

    def sweep(self, cfg: dict) -> None:
        """Close what the clocks say is over: the idle timeout, the message
        ceiling, and the hard age limit. Each closed chat writes its record."""
        now = time.time()
        idle_cap = 60 * int(cfg.get("chat_idle_minutes") or 30)
        age_cap = 3600 * int(cfg.get("chat_max_hours") or 12)
        msg_cap = int(cfg.get("chat_max_messages") or 0)
        for chat_id, chat in list(self.chats.items()):
            over = (now - chat.last_active > idle_cap
                    or now - chat.started > age_cap
                    or (msg_cap and chat.messages >= msg_cap))
            if over:
                reason = ("the chat reached its message limit"
                          if msg_cap and chat.messages >= msg_cap
                          else "the chat went quiet")
                chat.write_record(reason)
                del self.chats[chat_id]
                log.info("chat %s closed: %s (%d msgs)", chat_id, reason,
                         chat.messages)


SHELF = ChatShelf()
