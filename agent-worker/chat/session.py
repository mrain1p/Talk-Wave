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

The two times the booth types unprompted — the opening line and the nudge at
a quiet line — are in openers.py: one un-tooled pass each, no rounds, no caps,
no ledger.

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
from chat import openers
from call.asks import Asks
from call.stuck import Stuck
from call.withheld import Withheld
from promises import PROBLEMS, unbacked
from spoken_rules import check_after_failure, reads_as_a_refusal
from station import StationClient

log = logging.getLogger("callin.chat")

# One LLM pass may fan out into tools and come back; more than a few rounds
# in a single message means the model is wandering, not working.
MAX_TOOL_ROUNDS = 4

# The prompt carries this many of the newest turns. A resumable chat can
# outgrow any context window; the record keeps everything, the prompt keeps
# the conversation's living end.
PROMPT_TURNS = 30

# What the DJ said about an action, and whether it owes a receipt — the openers the chat
# conduct asks for by name ("hold on, let me dig through the racks"), and since 0.10.138 the
# finished tense too ("I've got that queued up for you"). Shared with the phone rather than
# copied: this file's copy had fallen four phrasings behind call/promise_guard.py's, so the
# same sentence was guarded on the phone and waved through here. See promises.py.
_NUDGE = {
    "promise": (
        "[You just told the caller you were about to do "
        "something. If it needs one of your tools, call "
        "it NOW. Do not type another word to them — they "
        "have already read that line, and a second copy "
        "of it reads as a stutter. If nothing actually "
        "needs doing, answer with nothing at all.]"),
    "claim": (
        "[You just told the caller that was already done, and no tool ran — so it is NOT "
        "done. If one of your tools does it, call it NOW, and type nothing to them first: "
        "they have already read it. If it comes back refused, or you have no tool for it, "
        "tell them plainly that it did not go through — do not leave them believing it "
        "did.]"),
    # The voice line has had this since 0.10.154 and the text line never got
    # it, though both share the classifier that produces the verdict. Worded
    # for typing rather than speaking, and — like its spoken twin — it never
    # asks for a tool: the station has already answered, the answer was no,
    # and the only thing owed is saying so.
    "refused": (
        "[The tool you just called came back REFUSED — the thing did not happen — and the "
        "line you just typed tells the caller it did, or that it is on its way. Do not call "
        "that tool again: you already have the station's answer, and asking twice does not "
        "change it. Say plainly, in your own voice and in the world, that it did not go "
        "through and why, and offer what you CAN do instead. Do not apologise twice or "
        "explain the machinery — one honest sentence and move on.]"),
}

def route_action_cards(mode: str, on_event):
    """Where a tool run's receipt card goes, by the action_cards setting.

    Returns (on_note, flush). on_note is handed to CallActions and fires the
    moment a tool acts; flush is called once the DJ's line has gone out.
    "before" emits immediately — the receipt leads the line that mentions it,
    which is how the phone's cards behave and how chat behaved through
    0.10.64. "after" (the default since 0.10.65, the operator's ask) holds
    the cards and flushes them behind the reply, so "queued it up" reads as
    the DJ speaking and the card as the paperwork. "off" drops the card —
    the action still runs and is still in the transcript's tools list; only
    the chat's furniture is withheld.
    """
    mode = (mode or "after").strip().lower()
    if mode == "before":
        return (lambda card: on_event({"type": "action", **card}),
                lambda: None)
    if mode == "off":
        return (lambda card: None), (lambda: None)
    held: list[dict] = []
    return (held.append,
            lambda: [on_event({"type": "action", **card}) for card in held])


def _tool_report(ran: list[tuple[str, str, bool]]) -> str:
    """What just ran, and what it said, for the model's next round.

    Bracketed like every other situation note in this codebase (the call's
    opening prime, the idle nudge, the late-match line) so the speech filter
    strips it if it ever reaches a voice, and so the model reads it as stage
    direction rather than as something the caller typed.

    The "don't call them again" line is doing real work: without a formal
    function_response the model has no structural signal that its call was
    executed, and will happily fire the same one a second time. Saying so
    plainly costs a sentence and MAX_TOOL_ROUNDS bounds the damage anyway.
    """
    lines = []
    for name, result, failed in ran:
        short = name.replace("subwave_", "")
        lines.append(f"- {short}{' FAILED' if failed else ''}: {result}")
    return ("[Your tools just ran. This is what they actually returned — "
            "answer the caller from THIS, not from what you expected:\n"
            + "\n".join(lines)
            + "\nDo not call these same tools again for this request. Reach "
              "for a DIFFERENT tool only if what came back genuinely needs "
              "one.]")


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
        # When each of those turns happened. Kept beside `turns` rather than
        # inside it so the prompt reader and the WS payload keep their (who,
        # text) shape; `remember()` is the only writer, which is what stops
        # the two drifting. Before this the record stamped every turn at
        # WRITE time, so a whole conversation shared one timestamp and the
        # pacing — the thing you read a bad chat back to see — was gone.
        self.turn_at: list[float] = []
        self.actions_log: list[tuple[str, str]] = []   # (tool, receipt)
        # Every tool the model actually called, with what came back:
        # (when, name, result, failed). The action ledger above is only the
        # SUCCESSES, because that is what a receipt card is for — so a chat
        # whose requests were all refused wrote a record with no tools in it
        # at all, and read as though the DJ had simply chatted. Observed
        # 2026-08-13, on the two chats that prompted all of this.
        self.tool_log: list[tuple[float, str, str, bool]] = []
        # Anything that went wrong that the caller was shielded from. Written
        # into the record's `problems`, which is the list the panel already
        # counts a conversation as failed by — so a broken provider shows up
        # in Needs attention instead of only in a log nobody keeps.
        self.problems: list[str] = []
        # What the caller has already had to ask, and whether they have told
        # the DJ it has this wrong — see call/stuck.py. Per conversation, like
        # everything else here.
        self.stuck = Stuck()
        # This line's withheld capabilities — see call/withheld.py. Built on
        # the first message (it needs the resolved cfg and the actions
        # ledger) and retuned each message after, because the text line
        # re-reads settings per message and the watcher must not card a
        # capability the operator just switched on.
        self.withheld: Withheld | None = None
        # What the caller asked for that a TOOL would have to satisfy, and
        # whether anything landed after. The phone has carried one since
        # 0.10.149; the text line never did, so its promise guard had nothing
        # but the DJ's wording to go on — see promises.unbacked.
        self.asks = Asks()
        self.persona_name = ""
        # The id as well as the name: a voice call records both, and a
        # record that knows only "Ash" cannot be grouped by persona the
        # way the calls beside it can (operator-spotted, 2026-08-12).
        self.persona_id = ""
        # The whole persona dict, resolved once and then held for the life of
        # the conversation — see ask(). None until the first message.
        self.persona: dict | None = None
        # One message in flight per chat: two tabs racing the same id would
        # interleave two tool loops through one transcript.
        self.lock = asyncio.Lock()
        # The flood brake lives on the CHAT, not the socket: it used to be a
        # per-connection list, so a caller could send a burst, drop the
        # socket, resume the same id for a fresh empty list, and burst again
        # (0.10.57 review). Persisting it here makes reconnecting no cheaper.
        self.msg_times: list[float] = []
        # Built once, for the whole conversation. Both of these used to be
        # rebuilt inside ask(), which looks harmless — the transcript is
        # replayed every message either way, so the DJ still sees the
        # history — but two things ride on the OBJECTS rather than on the
        # text, and both were being thrown away every turn:
        #
        #   * the LLM client caches Gemini 3's per-call `thought_signature`
        #     and re-injects it on later requests. Discard the client and
        #     the signature goes with it, so replaying an earlier tool call
        #     is rejected: "Function call is missing a thought_signature in
        #     functionCall parts" (400, fatal). Reproduced on a multi-tool
        #     turn — which is exactly what "queue me three songs" produces.
        #   * CallActions is the per-conversation action cap. Rebuilt per
        #     message its count restarts at zero, so `max_actions_per_call`
        #     was per MESSAGE on the text line and a texter got a fresh
        #     budget with every line they sent. The phone builds it once and
        #     was always right.
        #
        # The prompt and the persona are deliberately NOT hoisted: they carry
        # what is playing right now, and a chat that spans a handover is
        # supposed to change DJ (see ask()).
        self._llm = None
        self.actions = None

    def remember(self, who: str, text: str) -> None:
        """Record one turn and when it happened. The only writer of `turns`."""
        self.turns.append((who, text))
        self.turn_at.append(time.time())

    async def greet(self, cfg: dict, on_event) -> None:
        """The booth speaks first when a chat opens. See chat/openers.py."""
        await openers.greet(self, cfg, on_event)

    async def nudge(self, cfg: dict, on_event) -> None:
        """One line to keep a quiet line breathing. See chat/openers.py."""
        await openers.nudge(self, cfg, on_event)

    async def ask(self, text: str, on_event) -> None:
        """One caller message -> streamed DJ reply.

        `on_event(dict)` receives, in order: any number of
        {"type": "action", ...} cards (the same shape the call publishes on
        talkwave.action), any number of {"type": "delta", "text": ...}
        fragments, then {"type": "done", "text": <full reply>}.

        Settings are re-read here — the per-message equivalent of the
        per-call invariant. The PERSONA is not: it is resolved once, on the
        first message, and rides out the rest of the conversation.

        It used to be re-resolved every message, on the reasoning that the DJ
        should be whoever is on air now. In practice that meant a takeover
        mid-chat — often one the caller had just asked for themselves —
        swapped the person they were talking to between one line and the
        next, mid-subject, with no goodbye (operator-reported, 2026-08-14).
        The phone never did this: a call resolves its persona and its VOICE
        once at pickup and cannot change either without dropping the call, so
        "the same rule the phone follows" was the opposite of what the old
        comment here claimed. This makes the text line behave like the phone.

        The station keeps changing underneath either way — the record, the
        schedule and the palette all still follow it. Only who you are talking
        to is held.
        """
        from livekit.agents import llm as lk_llm

        from brain.assemble import build_system_prompt
        from call.actions import CallActions
        from call.air import OnAirGuard
        from call.providers import build_llm
        from call.tools import (apply_finder_dispatch, build_curation_tools,
                                build_discovery_tools, build_library_tools,
                                build_on_air_tools, build_read_tools)

        # Keys entered in the settings page live in their own store; push
        # them into the environment before building the model — the same
        # line the worker runs at job start, needed here because THIS
        # process (the token server) never ran it and the SDKs read env.
        import secrets_store
        secrets_store.apply_to_env()

        cfg = settings_store.permissions_for(settings_store.load(), self.tier)
        on_note, flush_cards = route_action_cards(
            str(cfg.get("action_cards") or "after"), on_event)
        station = StationClient()
        try:
            # Once per conversation. Re-resolved only if the first attempt
            # came back with nothing to hold on to — a station that was down
            # for the opening message must not pin the chat to "The DJ" for
            # the next ten minutes.
            persona = self.persona
            if not (persona or {}).get("id") and not (persona or {}).get("name"):
                persona = await station.resolve_live_persona()
                self.persona = persona
            self.persona_name = persona.get("name") or self.persona_name
            self.persona_id = persona.get("id") or self.persona_id
            prompt = await build_system_prompt(station, persona, cfg=cfg,
                                               mode="chat")

            if self.actions is None:
                self.actions = CallActions(
                    int(cfg.get("max_actions_per_call") or 0),
                    tier=self.tier)
            actions = self.actions
            # Re-pointed every message: the receipt sink belongs to THIS
            # request's websocket, while the ledger behind it does not.
            actions.on_note = on_note
            # Disabled guard: a typed DJ never needs holding off the air, but
            # the broadcast wrappers still want the shared clear-air API.
            guard = OnAirGuard(station, {"avoid_on_air_overlap": False})
            # The reads first: this line carries no MCP, and until 0.99.0
            # that meant no eyes at all — the 2026-08-27 exchange had the DJ
            # answering "similar to my current queue" from a guess and
            # reaching for a state read that wasn't there. See
            # call/tools/reads.py for why the same names serve both mouths.
            tools = build_read_tools(cfg, station) + \
                build_library_tools(cfg, station, actions) + \
                build_discovery_tools(cfg, station, actions) + \
                build_curation_tools(cfg, station, actions) + \
                build_on_air_tools(cfg, station, actions, guard, guarded=False)
            # Built last and off the list itself, so it can only ever route to
            # what the settings already allowed — see call/tools/finding.py.
            tools = apply_finder_dispatch(cfg, tools)

            ctx = lk_llm.ChatContext.empty()
            ctx.add_message(role="system", content=prompt)
            for who, said in self.turns[-PROMPT_TURNS:]:
                ctx.add_message(role="user" if who == "caller" else "assistant",
                                content=said)
            ctx.add_message(role="user", content=text)
            # Asked-again and told-you-are-wrong. The phone gets this through
            # CallAgent.on_user_turn_completed; the typed line has no SDK hook
            # to hang it on, so it goes in here — after the caller's own line,
            # as a system message, which is where the door hint sits on the
            # other surface. A note from us must never ride inside the
            # caller's message: that text reaches the written record, and it
            # would be a record of something they never said.
            #
            # This is the surface the 2026-08-20 failure happened on: seven
            # asks, one wrong answer, no mechanism anywhere that noticed. See
            # call/stuck.py.
            self.asks.heard(text)
            note = self.stuck.hint_for(text)
            if note:
                ctx.add_message(role="system", content=note)
                self.problems.append(PROBLEMS["stuck"])
            # Asked for something this line withholds: card + honest first
            # answer, same as the phone. See call/withheld.py.
            if self.withheld is None:
                self.withheld = Withheld(cfg, actions)
            else:
                self.withheld.actions = actions
                self.withheld.retune(cfg)
            note = self.withheld.hint_for(text)
            if note:
                ctx.add_message(role="system", content=note)

            self.remember("caller", text)
            self.messages += 1
            self.last_active = time.time()

            if self._llm is None:
                self._llm = build_llm(cfg)
            reply = await self._tool_loop(self._llm, ctx, tools, on_event)
            if not reply.strip():
                # The same promise the phone makes: a model outage never
                # becomes silence.
                reply = ("Line's a bit crackly at my end — say that again "
                         "for me?")
            self.remember("dj", reply)
            self.actions_log.extend(
                (kind, detail) for kind, detail in actions.taken)
            self.last_active = time.time()
            on_event({"type": "done", "text": reply,
                      "dj": self.persona_name})
        finally:
            # In the finally on purpose: a reply that failed AFTER a tool ran
            # still owes the caller the receipt for what actually happened.
            flush_cards()
            await station.aclose()

    async def _tool_loop(self, model, ctx, tools, on_event) -> str:
        """Stream the model, run any tools it calls, feed results back,
        repeat until it answers in words. The loop the voice SDK runs for a
        call, at chat size."""
        by_name = {t.info.name: t for t in tools}
        reply = ""
        nudge_left = True
        # Where the ledger stood when this message started, for the claim rule
        # in promises.unbacked. It is per conversation, so what matters is
        # whether it MOVED. Without it the text line nudged a claim that a
        # queue had genuinely just satisfied — the DJ was told "no tool ran, so
        # it is NOT done" about something it had done correctly, and spent a
        # turn apologising for it.
        acted_at = int(getattr(self.actions, "count", 0) or 0)
        # Did anything the model called come back refused, in ANY round of
        # this message? The voice line has nudged this since 0.10.154 — the
        # DJ tells the caller it landed after the station said no, which is
        # the failure the caller cannot catch, because they hang up believing
        # a record is coming that nobody queued. The text line shares the
        # classifier (`promises.unbacked`) and never passed it this third
        # flag, so the same sentence in a chat window went unchallenged.
        #
        # `_run_tool` has returned the flag all along; nothing downstream read
        # it. Same shape as the voice guard's own gap: the structured answer
        # was already in hand.
        refused_any = False
        # A paragraph break owed to the reader before the next round's words —
        # see the nudge below. Emitted as a delta so the live card and the
        # written record break in the same place.
        pending_break = False
        # Vet-before-show, the text line's advantage (2026-08-28): on the
        # phone a reply streams into TTS and a lie after a refusal can only
        # be graded once it has aired. Here nothing is on screen until we
        # send it — so a round generated AFTER a refusal is held back,
        # checked with the same rule the drill grades with, and rewritten
        # once if it claims the refused thing happened. The caller never
        # sees the lie. One rewrite only; a model that lies twice gets the
        # honest problems entry the operator already reads.
        vet_left = 1
        for _ in range(MAX_TOOL_ROUNDS):
            text_out, calls = "", []
            hold_stream = refused_any and vet_left > 0
            stream = model.chat(chat_ctx=ctx, tools=tools)
            async for chunk in stream:
                delta = getattr(chunk, "delta", None)
                if not delta:
                    continue
                if delta.content:
                    if pending_break and not text_out:
                        pending_break = False
                        reply += "\n\n"
                        if not hold_stream:
                            on_event({"type": "delta", "text": "\n\n"})
                    text_out += delta.content
                    if not hold_stream:
                        on_event({"type": "delta", "text": delta.content})
                if delta.tool_calls:
                    calls.extend(delta.tool_calls)
            if hold_stream and text_out.strip():
                if check_after_failure(text_out):
                    vet_left = 0
                    self.problems.append(PROBLEMS["claims-again"])
                    ctx.add_message(role="assistant", content=text_out)
                    ctx.add_message(role="user", content=(
                        "[Your reply claims the refused thing happened. It "
                        "did NOT — the station said no and the caller has "
                        "been shown that on a card. Rewrite the reply: say "
                        "plainly it didn't go through, then move on. Do not "
                        "mention this note.]"))
                    # The held text was never shown and never reaches
                    # `reply` — the accumulation below is skipped by this
                    # continue, so the record keeps only what the caller saw.
                    continue
                # Honest — release the held text as one delta.
                on_event({"type": "delta", "text": text_out})
            await stream.aclose()
            reply += text_out
            if not calls:
                # A promise with no tool call behind it is the one shape
                # this loop used to ship as a finished answer. The chat
                # conduct TELLS the DJ to say something before reaching
                # for a tool ("hold on, let me dig through the racks"),
                # and a round that is only that line looked identical to
                # a round that was done — so "let me get that dedication
                # sent down to the booth" ended the turn and nothing was
                # ever sent (operator-reported, 2026-08-12). One extra
                # pass, only when the words match the opener the conduct
                # asked for, and only once per message.
                # tools_ran stays False deliberately: this branch IS "the model
                # stopped without calling anything", so a promise here is
                # unbacked whatever an earlier round did. A round-1 search does
                # not make a round-2 "I'll get that queued" true.
                kind = unbacked(
                    text_out, tools_ran=False,
                    acted=int(getattr(self.actions, "count", 0) or 0) > acted_at,
                    refused=refused_any,
                    # An obligation is the caller's ask, not the DJ's wording.
                    owed=not self.asks.settled(
                        getattr(self.actions, "taken_at", []) or []),
                ) if (nudge_left and tools) else ""
                if kind:
                    nudge_left = False
                    # The operator reads these back, and until 0.98.16 could
                    # not: this list was declared, drained into the record and
                    # never once appended to, so every chat filed a clean
                    # sheet no matter what happened in it.
                    self.problems.append(PROBLEMS[kind])
                    ctx.add_message(role="assistant", content=text_out)
                    ctx.add_message(role="user", content=_NUDGE[kind])
                    # The nudge asks for a tool and no more words. When the
                    # model types anyway — which is most of the time — the
                    # second attempt used to be glued straight onto the first
                    # with nothing between them, mid-sentence: "...go in behind
                    # it?Ah, wait—my mistake". Held rather than emitted now, so
                    # a round that DOES comply and stays quiet doesn't leave a
                    # stray gap behind it.
                    pending_break = bool(text_out.strip())
                    continue
                # The nudge is spent; what is left is grading. A closing line
                # that still says a refused thing landed is the repeat
                # PROBLEMS["refused"] tells the operator to watch for — same
                # check the drill grades with, wired the same release as the
                # phone's (0.98.55, promise_guard._on_said).
                if (not nudge_left and refused_any and text_out.strip()
                        and check_after_failure(text_out)):
                    self.problems.append(PROBLEMS["claims-again"])
                break
            # Tool results go back as TEXT, not as function_call /
            # function_call_output parts.
            #
            # Gemini 3 signs a tool call with an opaque `thought_signature`
            # and refuses any later request that replays a functionCall part
            # without one — and it does not sign them all. Measured against
            # the live model: two calls in one response, ONE signature; three
            # calls, one signature. So a faithful structured replay is
            # impossible by construction, and the request dies:
            #
            #   400 Function call is missing a thought_signature in
            #   functionCall parts … `default_api:subwave_recent_tracks`
            #
            # Fatal, not retryable, and it took the whole reply with it: the
            # caller got "Line dropped a beat there — say that again for me?"
            # while the log held the real reason.
            #
            # Four shapes were tried against the real model before this one
            # (see the 0.10.119 commit): interleaved parts and grouped parts
            # both fail; replaying only the SIGNED call succeeds but the model
            # answers with nothing, having been shown half of what happened.
            # Text succeeds and answers properly.
            #
            # It is also the honest representation. Every tool here already
            # returns a sentence written for the model to read — "Added to the
            # queue: X. It is NOT playing yet" — never a JSON payload. Wrapping
            # prose in a function_call_output bought us provider-specific
            # fragility and no fidelity. And it is provider-AGNOSTIC: no
            # signatures, no ids, nothing to plumb, so Ollama and every other
            # local backend take the same path.
            if text_out.strip():
                ctx.add_message(role="assistant", content=text_out)
            ran: list[tuple[str, str, bool]] = []
            for call in calls:
                result, is_error = await self._run_tool(by_name, call)
                ran.append((call.name, str(result), is_error))
                # Both halves, like the voice guard: the raised flag is
                # authoritative about a tool that blew up, and the prose is
                # the only signal when the STATION said no — a rate limit or
                # a blocklist is a perfectly successful call whose content is
                # a refusal, and it raises nothing.
                if is_error or reads_as_a_refusal(str(result)):
                    refused_any = True
            ctx.add_message(role="user", content=_tool_report(ran))
        else:
            log.warning("chat %s: model still calling tools after %d "
                        "rounds — answering with what it has",
                        self.id, MAX_TOOL_ROUNDS)
        # No aclose() here any more: the model belongs to the CONVERSATION
        # now, not to this message. Closing it per message is what dropped
        # the thought-signature cache. ChatSession.aclose() owns it.
        return reply

    async def _run_tool(self, by_name, call) -> tuple[str, bool]:
        # Everything that leaves here is written down first. A voice call gets
        # this for free from the SDK's function_tools_executed event; chat has
        # no such event, so the only place that knows a tool ran is this
        # method — and until 0.10.104 it told nobody but the log, which does
        # not survive a container restart.
        tool = by_name.get(call.name)
        if tool is None:
            self._note_tool(call.name, "no such tool", failed=True)
            return f"no such tool: {call.name}", True
        try:
            kwargs = json.loads(call.arguments or "{}")
        except Exception:                                      # noqa: BLE001
            kwargs = {}
        try:
            out = await tool(**kwargs)
            log.info("chat %s tool: %s -> %.90s", self.id, call.name, out)
            self._note_tool(call.name, out, args=kwargs)
            return str(out), False
        except Exception as e:                                 # noqa: BLE001
            log.warning("chat %s tool %s failed: %s", self.id, call.name, e)
            self._note_tool(call.name, f"raised {type(e).__name__}: {e}",
                            args=kwargs, failed=True)
            return "that didn't work — tell them plainly, in your own words", True

    def _note_tool(self, name: str, result, args: dict | None = None,
                   failed: bool = False) -> None:
        """One line in the record for one tool call.

        The ARGUMENTS go in beside the result on purpose: reading a bad chat
        back, "search_library returned nothing" is only half an answer — the
        question is always what it searched FOR, and that is the difference
        between a library that lacks the track and a DJ that looked up the
        wrong words.
        """
        from call.record import with_args

        self.tool_log.append((time.time(), name, with_args(args, result), failed))

    async def aclose(self) -> None:
        """Let go of the conversation's model. Idempotent, and never allowed
        to raise — a chat ending is not a place to fail."""
        model, self._llm = self._llm, None
        if model is None:
            return
        try:
            await model.aclose()
        except Exception as e:                                 # noqa: BLE001
            log.debug("closing the chat model failed (harmless): %s", e)

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
                             {"id": self.persona_id,
                              "name": self.persona_name}, cfg,
                             tier=self.tier, started=self.started)
            rec.data["kind"] = "chat"
            for i, (who, said) in enumerate(self.turns):
                # zip() would silently drop turns if the two lists ever
                # disagreed; falling back to the start time keeps every turn
                # and makes a bug here look like a wrong clock, not a missing
                # conversation.
                at = self.turn_at[i] if i < len(self.turn_at) else self.started
                rec.turn(who, said, at=at)
            # The real tool log, not the action ledger: the ledger holds only
            # what SUCCEEDED, so a chat spent talking around three refusals
            # used to write a record with an empty tools list.
            for at, name, detail, failed in self.tool_log:
                rec.tool(name, detail, at=at, failed=failed)
            for what in self.problems:
                rec.problem(what)
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
        idle_cap = 60 * int(cfg.get("chat_idle_minutes") or 5)
        age_cap = 60 * int(cfg.get("chat_max_minutes") or 10)
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
                # The conversation owned an LLM client from 0.10.117; ending
                # the chat has to let go of it or every closed chat leaks one.
                # Spawned rather than awaited: sweep() is sync and called from
                # both an async hook and the tests, and a chat ending must not
                # become a place that can block or raise.
                try:
                    asyncio.get_running_loop().create_task(chat.aclose())
                except RuntimeError:
                    pass          # no loop (a test) — nothing was built either
                del self.chats[chat_id]
                log.info("chat %s closed: %s (%d msgs)", chat_id, reason,
                         chat.messages)


SHELF = ChatShelf()
