"""One call, from pickup to hangup.

Everything a single call needs to know about itself lives on this object:
which DJ answered, in what voice, with which tools, what it has been allowed
to do, and whether the broadcast currently has the microphone.

Why it exists: all of this used to be local variables and closures inside one
334-line function, which meant every new per-call feature — the action ledger,
the overlap guard, the wrap-up tool — was another edit to the same function
and another variable in the same scope. A call is a thing with state; this is
that thing.

The phases are deliberately separate and in the order the caller experiences
them:

    prepare()   everything the caller hears as ringing
    start()     put the DJ on the line
    greet()     say hello
"""

from __future__ import annotations

import logging
import os
import random
import time

from livekit.agents import AgentSession, JobContext, mcp
# Not re-exported from livekit.agents or livekit.agents.voice in this SDK — the
# dataclass only exists on the module that defines it.
from livekit.agents.voice.agent_session import SessionConnectOptions
from livekit.agents.voice.room_io import RoomOptions

import brain
import llm_pace
import settings as settings_store
import speech_filter
from log_setup import describe
import station_config as station_config_mod
from station import StationClient
from station_config import StationConfig
from tts_adapter import available_voices, pick_speakable_voice, resolve_adapter

from . import (asks as asks_mod, background, door, floor as floor_mod,
               greeting, handoff, lifecycle, postmortem, promise_guard)
from .actions import CallActions
from .air import CallAgent, OnAirGuard
from .air_log import AirLog
from .record import CallRecord
from .providers import build_llm, build_stt, build_tts, llm_conn_options
from .tools import (
    build_call_control_tools,
    build_curation_tools,
    build_discovery_tools,
    build_library_tools,
    build_on_air_tools,
    mcp_allowlist,
)

log = logging.getLogger("callin.agent")


def _number(cfg: dict, key: str) -> float:
    """A configured number, or 0 when it is unset or unreadable.

    0 means "leave the SDK's own default alone", not "no delay". Passing a
    literal zero would make the DJ answer the instant the caller stops making
    sound, which is not patience, it is interrupting — so an unset value has
    to pass nothing at all rather than pass zero.
    """
    try:
        return float(cfg.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def turn_handling(cfg: dict) -> dict:
    """Everything about who is talking, in ONE dict.

    It has to be one dict. `AgentSession` accepts `allow_interruptions`,
    `min_endpointing_delay` and `max_endpointing_delay` as separate arguments,
    but it only reads them on the branch where `turn_handling` was NOT passed:

        turn_handling = (
            _migrate_turn_handling(..., allow_interruptions=..., ...)
            if not is_given(turn_handling) else turn_handling
        )

    We passed both — `turn_handling` for preemptive generation, the three
    kwargs for turn-taking — so the SDK took the dict and dropped the rest on
    the floor. Every one of those three settings was in the panel, documented,
    saved, and doing nothing: `allow_interruptions=False` still resolved to
    `enabled: True`, and endpointing stayed at the stock 0.3/2.5.

    That is the whole explanation for the DJ being chopped mid-sentence on real
    calls. `min_duration` defaults to 0.5s of SOUND — not words — and with the
    station stream playing into the caller's room, half a second of the record
    they are listening to reads as an interruption. The operator's own remedy,
    the one `allow_interruptions`' help text names, could not be applied.
    """
    interruption: dict = {"enabled": bool(cfg.get("allow_interruptions", True))}
    hold = _number(cfg, "min_interruption_secs")
    if hold > 0:
        interruption["min_duration"] = hold

    endpointing: dict = {}
    for key, target in (("min_endpointing_delay", "min_delay"),
                        ("max_endpointing_delay", "max_delay")):
        value = _number(cfg, key)
        if value > 0:
            endpointing[target] = value

    out = {
        # Preemptive generation stays OFF. It starts a reply from a PARTIAL
        # transcript, and when that speculative turn contains a tool call the
        # final user turn lands after it — leaving a function call followed by
        # a user turn, which Gemini rejects outright:
        #   "Please ensure that function call turn comes immediately after a
        #    user turn or after a function response turn." (400)
        # The call then dies mid-conversation. It only surfaced once the
        # station tools were reachable and the DJ started calling them.
        "preemptive_generation": {"enabled": False},
        "interruption": interruption,
    }
    if endpointing:
        out["endpointing"] = endpointing
    return out


class CallSession:
    def __init__(self, ctx: JobContext) -> None:
        self.ctx = ctx
        # Re-read per call, so a settings change applies to the next caller
        # without restarting the worker.
        #
        # Resolved against THIS caller's tier before anything else touches it.
        # Every consumer below reads cfg.get("allow_x") as a truthy value and
        # always has; the raw stored value is now a tier string, and "off" is
        # truthy — so a cfg that reached a tool builder unresolved would switch
        # on every permission the operator had turned off. It is resolved once,
        # here, and there is no path to the tool builders that skips it.
        self.tier = settings_store.tier_from_room(ctx.room.name)
        self.cfg = settings_store.permissions_for(settings_store.load(), self.tier)
        self.station = StationClient()
        self.station_cfg = StationConfig()

        self.persona: dict = {}
        # The segments THIS DJ may run, narrowed in prepare(). Empty until then,
        # and empty is honest: with no station read there is nothing to offer.
        self.skills: list[str] = []
        self.voice = ""
        self.instructions = ""
        self.session: AgentSession | None = None

        self.actions = CallActions(self.cfg.get("max_actions_per_call"), room=ctx.room,
                                   mode=str(self.cfg.get("action_cards") or "after"))
        self.air = OnAirGuard(self.station, self.cfg, room=ctx.room)
        # One call's memory of whether the last line showed the caller the
        # door — see call/door.py. Cheap enough to always build.
        self.door = door.Door()
        # Who is allowed to start a turn — see call/floor.py. Attached to
        # the air guard the way air_log is, because the come-back task is
        # created inside the guard's watch loop.
        self.floor = floor_mod.Floor()
        self.air.floor = self.floor
        # What the caller asked for, and whether anything happened about
        # it. Records only — see call/asks.py.
        self.asks = asks_mod.Asks()

        self.started_at = time.time()
        self.heard = {"n": 0}
        # Why the call ended, as the SDK saw it. ctx.shutdown_reason is empty
        # when the caller simply hangs up, so the record could not tell "they
        # rang off" from "the line dropped" — which is the first thing you want
        # to know when someone reports a call cutting out.
        self.ended = {"reason": ""}
        # Filled in once the persona is known — see prepare().
        self.record: CallRecord | None = None
        # What the caller waited for the model, turn by turn. Built here rather
        # than in start() so the budget it reports is the one this call ran
        # under even if the call never gets that far.
        self.think = llm_pace.ThinkMeter(
            label=f"{self.cfg.get('llm_provider')}/{self.cfg.get('llm_model')}",
            budget=llm_pace.attempt_budget(self.cfg.get("llm_provider", ""))[0],
        )

        ctx.add_shutdown_callback(self.station.aclose)
        ctx.add_shutdown_callback(self.station_cfg.aclose)
        # Release the concurrency slot from __init__, NOT from _on_shutdown at
        # the tail of start(). A call that raises in prepare() or early start()
        # — a provider misconfig fails every call at build_llm — never reached
        # that registration, so the slot it minted sat held for the full
        # 30-minute age-out, and two such failures jammed the line for everyone
        # (0.10.57 review). Registered before anything can raise, so it always
        # fires. The SDK runs shutdown callbacks concurrently, but slot release
        # (a POST) and the record write (local disk) are independent, so order
        # doesn't matter; _on_shutdown no longer releases the slot itself.
        ctx.add_shutdown_callback(
            lambda: lifecycle.release_call_slot(ctx.room.name))
        # Cancel outstanding background tasks (the ~50s late-match poller) so
        # they can't write to the record after it is finalised. Registered
        # here so it fires even on an early-call failure.
        ctx.add_shutdown_callback(lambda: background.cancel_all())

    # -- ringing ----------------------------------------------------------
    async def prepare(self) -> None:
        """Resolve who answers and what they know. The caller hears every
        millisecond of this as ringing, which is why the station reads are one
        concurrent snapshot rather than six serial ones."""
        # The last of the four places that published tts_mode into os.environ.
        # It was here so voice resolution read the right registry — cloud and
        # local voices are not interchangeable — but station_config asks
        # settings.tts_mode(), which reads the settings file, and the adapter
        # is told its mode directly now. Nothing reads the variable any more.
        # The voices map does NOT depend on which persona answers — it is the
        # whole station's persona->voice mirror — so it rides in the SAME
        # concurrent wait as the snapshot instead of a serial read after it.
        # Serially it added a second station timeout's worth of ringing behind
        # the first; on the slow station a caller reported (12s+ to pick up),
        # that was most of the regression. voice_for() below then reuses the
        # /settings response this warmed (StationConfig caches per path), so it
        # costs no further network.
        import asyncio

        snap, _voice_map = await asyncio.gather(
            self.station.snapshot(with_skills=bool(self.cfg.get("allow_skills"))),
            self.station_cfg.persona_voices(),
        )
        self.persona = self._resolve_persona(snap)

        # The catalogue the station returned is every skill it HAS. Narrow it to
        # what this DJ may actually run before it reaches either the prompt or
        # the tool — the station's manual trigger is an operator override that
        # ignores its own enabled flag, so an un-narrowed list is a caller
        # running segments the operator switched off. Both consumers read the
        # same filtered list, because a prompt and a tool that disagree about
        # what exists is how the DJ ends up offering something it is then
        # refused. Rides the /settings read persona_voices just warmed.
        if snap.get("skills"):
            from station_config import runnable_skills

            assigned = await self.station_cfg.persona_skills(
                str(self.persona.get("id") or ""))
            snap["skills"] = runnable_skills(snap["skills"], assigned)
        self.skills = [str(s.get("name") or s.get("kind") or "")
                       for s in (snap.get("skills") or [])]

        # Whose voice this is, so the model writing "Francesca:" as a script
        # label never gets read out as part of the line.
        speech_filter.set_speaker(self.persona.get("name", ""))

        self.voice = (
            str(self.cfg.get("tts_voice") or "").strip()
            or await self.station_cfg.voice_for(self.persona["id"])
        )
        self.instructions = await brain.build_system_prompt(
            self.station, self.persona, snapshot=snap, cfg=self.cfg,
            # The clock mirror (djSpeakClock, SUB/WAVE 1.8) rides the same
            # cached /settings read voice_for() just warmed — free here.
            speak_clock=await self.station_cfg.speak_clock(),
        )
        self.record = CallRecord(self.ctx.room.name, self.persona, self.cfg,
                                 self.tier, started=self.started_at)
        # The ducking timeline rides with the guard and lands on the record at
        # the end — see call/air_log.py. Attached here rather than built in the
        # guard so a guard nobody is recording carries no cost.
        self.air.air_log = AirLog(since=self.started_at)

        # Checked against the backend BEFORE the first line, not discovered by
        # the caller. See tts_adapter.pick_speakable_voice — a voice the
        # backend does not have used to mean a call where the DJ never spoke
        # at all, with a green pipeline check, because that check tests the
        # CONFIGURED voice and never the one the on-air persona resolves to.
        self.voice, why = pick_speakable_voice(
            self.voice,
            await available_voices(
                str(self.cfg.get("tts_base_url") or "").strip(),
                adapter_path=resolve_adapter(self.cfg.get("tts_adapter")),
                mode=str(self.cfg.get("tts_mode", "")),
            ),
        )
        if why:
            log.warning("%s", why)
            self.record.problem(why)

    def _resolve_persona(self, snap: dict) -> dict:
        """One button, whoever is live answers — unless settings say otherwise."""
        override = str(self.cfg.get("persona_override") or "").strip()
        roster = {p.get("id"): p for p in snap["personas"] if p.get("id")}

        if override == settings_store.RANDOM_PERSONA and roster:
            persona = roster[random.choice(list(roster))]
            log.info("persona rolled for this call: %s (%s)",
                     persona.get("name"), persona.get("id"))
            return persona
        if override and override in roster:
            log.info("persona pinned by settings: %s", override)
            return roster[override]
        return self.station.persona_from(snap["dj"], snap["personas"])

    # -- picking up -------------------------------------------------------
    def _build_tools(self) -> tuple[list[str], list]:
        guarded = bool(self.cfg.get("avoid_on_air_overlap"))
        local = build_on_air_tools(
            self.cfg, self.station, self.actions, self.air, guarded=guarded,
            skills=self.skills,
        )
        # Same reason as the hang-up tool below: the session doesn't exist yet,
        # so the late-match announcer is handed a way to read it later.
        local += build_library_tools(
            self.cfg, self.station, self.actions,
            get_session=lambda: self.session, air=self.air, record=self.record,
        )
        local += build_discovery_tools(self.cfg, self.station, self.actions)
        local += build_curation_tools(self.cfg, self.station, self.actions)
        # The AgentSession doesn't exist yet — tools are built first — so the
        # hang-up tool is handed a way to read it later, not the thing itself.
        local += build_call_control_tools(
            self.ctx, lambda: self.session, self.started_at,
            float(self.cfg.get("min_call_seconds") or 0),
        )
        return mcp_allowlist(self.cfg), local

    async def start(self) -> None:
        """Build the voice session and put the DJ on the line."""
        allowed_tools, local_tools = self._build_tools()

        log.info(
            "call starting room=%s tier=%s persona=%s (%s) llm=%s/%s tts=%s voice=%s tools=%d",
            self.ctx.room.name, self.tier, self.persona["name"], self.persona["id"],
            self.cfg["llm_provider"], self.cfg["llm_model"],
            self.cfg["tts_mode"], self.voice,
            len(allowed_tools) + len(local_tools),
        )

        # Several station tools (search_library among them) are admin-gated and
        # are rejected outright without credentials, which surfaces mid-call as
        # the DJ saying it's "locked out of the controls".
        mcp_headers = station_config_mod.mcp_headers()
        if not mcp_headers:
            log.warning(
                "no station admin credentials — admin-gated tools like "
                "subwave_search_library will be refused during the call"
            )

        # Fail CLOSED on an empty allowlist. The SDK reads an empty list as
        # "no filter — expose every tool the station's MCP server offers",
        # including the destructive ones (skip_track, play_sfx, …). Today the
        # list is never empty (five READ tools are always MCP-served), so this
        # is defence in depth — but the failure direction is unsafe, and a
        # sentinel that matches no real tool keeps the surface shut if those
        # reads ever move (0.10.57 review).
        gated_tools = allowed_tools or ["__none__"]

        station_tools = mcp.MCPServerHTTP(
            url=settings_store.station_mcp_url(),
            transport_type="streamable_http",
            allowed_tools=gated_tools,
            headers=mcp_headers or None,
            # 7s, down from 15. This connect happens BEFORE the greeting, so on
            # a slow/overloaded station it sat 15s of silence in front of the
            # caller before the DJ said hello (measured 2026-08-10, a congested
            # NAS). The tools resolve fast on a healthy station; when they
            # don't, greeting late is worse than starting with the local tools
            # and the MCP catalogue arriving a moment into the call.
            client_session_timeout_seconds=7,
        )

        # MCPToolset rather than the session's mcp_servers argument, which is
        # deprecated: the toolset is just another entry in `tools`, so the
        # station's tools and our own wrappers arrive by the same route.
        toolset = mcp.MCPToolset(id="subwave", mcp_server=station_tools)

        self.session = AgentSession(
            stt=build_stt(self.cfg),
            llm=build_llm(self.cfg),
            tts=build_tts(self.cfg, self.voice),
            vad=self.ctx.proc.userdata["vad"],
            tools=[*local_tools, toolset],
            # One dict, and it must stay one dict — see turn_handling() for
            # what passing these alongside it silently did.
            turn_handling=turn_handling(self.cfg),
            # Only the LLM leg is overridden. STT and TTS keep the SDK's
            # defaults: they stream continuously, so a stall there shows up as
            # a gap rather than as a turn that never starts.
            conn_options=SessionConnectOptions(
                llm_conn_options=llm_conn_options(self.cfg)),
        )

        await self.session.start(
            agent=CallAgent(self.instructions, self.air, self.door),
            room=self.ctx.room,
            # RoomOptions replaces the deprecated RoomInputOptions/
            # RoomOutputOptions pair. close_on_disconnect keeps its meaning:
            # when the caller's browser goes, the session goes.
            room_options=RoomOptions(close_on_disconnect=True),
        )
        self._attach_behaviours()

    def _attach_behaviours(self) -> None:
        """Everything that runs for the life of the call."""
        import asyncio

        session, ctx, cfg = self.session, self.ctx, self.cfg

        # The broadcast hierarchy: while the on-air DJ has the microphone, the
        # call DJ waits. Started after the session so the watcher can interrupt
        # it.
        air_task = asyncio.create_task(self.air.watch(session))
        ctx.add_shutdown_callback(lambda: lifecycle.cancel(air_task))

        lifecycle.attach_close_reason(session, self.ended)
        lifecycle.attach_error_recovery(session, self.record, self.think)
        lifecycle.attach_think_pace(session, self.think)
        lifecycle.attach_first_word(session, self.record)
        lifecycle.attach_turn_commit(ctx, session)
        lifecycle.attach_heard_logging(session, self.heard, self.record)
        lifecycle.attach_card_flush(session, self.actions)
        # Attached after card_flush and before the idle watch on purpose: it
        # reacts to the same conversation_item_added, and the receipt cards
        # for a nudged tool must still land behind the DJ's line.
        promise_guard.attach_promise_guard(session, self.record, self.actions,
                                           air=self.air, floor=self.floor)
        door.attach_door_watch(session, self.door)
        asks_mod.attach_ask_watch(session, self.asks)
        lifecycle.attach_idle_watch(ctx, session, cfg, air=self.air,
                                    heard=self.heard, actions=self.actions)
        lifecycle.attach_time_limit(ctx, session, cfg, air=self.air,
                                    floor=self.floor)
        ctx.add_shutdown_callback(self._on_shutdown)

    async def greet(self) -> None:
        await greeting.greet(self.session, self.cfg, record=self.record,
                              air=self.air)

    # -- hanging up -------------------------------------------------------
    async def _on_shutdown(self) -> None:
        """Runs after the caller hangs up, so the station reflects the call."""
        duration = time.time() - self.started_at
        # Ours first — attach_time_limit and the idle goodbye set a real reason.
        # The SDK's close reason fills the gap they leave, which is every call
        # the caller ended themselves.
        reason = (getattr(self.ctx, "shutdown_reason", "") or ""
                  or self.ended.get("reason", ""))

        # One greppable line per call: what happened, at a glance.
        log.info(
            "call ended room=%s persona=%s duration=%.0fs caller_turns=%d "
            "llm=%s/%s tts=%s ended=%s",
            self.ctx.room.name, self.persona.get("name"),
            duration, self.heard["n"],
            self.cfg.get("llm_provider"), self.cfg.get("llm_model"),
            self.cfg.get("tts_mode"), reason or "-",
        )
        # Written before the on-air handoff, which makes an LLM call and can
        # fail — the record of the call must not depend on it succeeding.
        if self.record:
            try:
                # The session's committed history is the authoritative wording;
                # the live events only got the timing right.
                final = [
                    ("caller" if role == "user" else "dj", text)
                    for role, text in handoff.transcript(self.session, limit=400)
                ]
                self.record.finalise(final)
                if self.air.air_log:
                    self.air.air_log.write(self.record)
            except Exception as e:
                log.debug("could not finalise the transcript (keeping live text): %s", e)
                final = []
            postmortem.write_notes(self, duration, final)
            if self.cfg.get("record_calls", True):
                self.record.write(reason=reason,
                                  keep=int(self.cfg.get("record_keep") or 0))
            else:
                # Built in memory either way — the problems it collects are
                # what _note_if_nothing_was_heard writes into — but an operator
                # who turned this off gets nothing on disk.
                log.info("call transcripts are off — nothing written for %s",
                         self.ctx.room.name)

        # Slot release is its own shutdown callback now (registered in
        # __init__ so an early-call failure can't skip it) — not done here.
        await handoff.send_on_air_callback(
            self.session, self.station, self.persona, self.cfg
        )
