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
import station_prefetch
from log_setup import describe
import station_config as station_config_mod
from station import StationClient
from station_config import StationConfig
from tts_adapter import available_voices, pick_speakable_voice, resolve_adapter

from onair import hush
from onair.relay import CallRelay

from . import (asks as asks_mod, background, clocks, comeback, door,
               floor as floor_mod, greeting, handoff, heard as heard_mod,
               lifecycle, postmortem, promise_guard, tee as tee_mod)
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
        # The DISPATCHED room name, not the rtc room's: this object is built
        # before ctx.connect() now (the join rides prepare()'s station wait),
        # and rtc.Room only learns its own name once connected — read early it
        # is "", which tier_from_room reads as the lowest tier. The job info
        # carries the name from the moment of dispatch. Test fakes carry only
        # ctx.room.name, hence the fallback.
        job_room = getattr(getattr(ctx, "job", None), "room", None)
        self.room_name = str(getattr(job_room, "name", "") or ctx.room.name)
        # Re-read per call, so a settings change applies to the next caller
        # without restarting the worker.
        #
        # Resolved against THIS caller's tier before anything else touches it.
        # Every consumer below reads cfg.get("allow_x") as a truthy value and
        # always has; the raw stored value is now a tier string, and "off" is
        # truthy — so a cfg that reached a tool builder unresolved would switch
        # on every permission the operator had turned off. It is resolved once,
        # here, and there is no path to the tool builders that skips it.
        self.tier = settings_store.tier_from_room(self.room_name)
        self.cfg = settings_store.permissions_for(settings_store.load(), self.tier)
        self.station = StationClient()
        self.station_cfg = StationConfig()
        # The station's MCP endpoint, built (and its connect started) in
        # prepare() so the handshake happens under the ringing, not in front
        # of the greeting.
        self.station_tools = None
        self._tools_warm = None
        self.allowed_tool_count = 0

        # The caller chose the on-air door AND this tier still clears it —
        # the mint already gated the choice inside the signed room name, but
        # the worker re-reads settings per call, so an operator who switched
        # the feature off between mint and pickup wins here. The relay itself
        # is built (and the transport preflighted) in start().
        self.on_air_asked = (settings_store.on_air_from_room(self.room_name)
                             and bool(self.cfg.get("allow_on_air")))
        self.relay: CallRelay | None = None
        self._tee: tee_mod.TeeHandle | None = None

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
        # What the caller waited for a REPLY, and what it cost when they talked
        # over one — see call/heard.py. Built here beside the think meter for
        # the same reason: a call that dies early still says what it measured.
        self.pacing = heard_mod.HeardMeter()

        # station.aclose is NOT its own shutdown callback any more. The SDK
        # runs shutdown callbacks CONCURRENTLY, and _on_shutdown keeps using
        # the client for the relay's brackets and the end-of-call handoff —
        # so the first tape soak (callin-ol-cd4e089a2eb0, 2026-08-19) had the
        # playout's intro AND outro die with "Cannot send a request, as the
        # client has been closed" while all nine clips aired fine over
        # telnet. The client now closes in _on_shutdown's finally, after
        # everything that speaks through it has finished.
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
        # self.room_name, not ctx.room.name: a call that dies before the join
        # completes shuts down with the rtc room still nameless, and a slot
        # released under "" is a slot that stays held.
        ctx.add_shutdown_callback(
            lambda: lifecycle.release_call_slot(self.room_name))
        # Cancel outstanding background tasks (the ~50s late-match poller) so
        # they can't write to the record after it is finalised. Registered
        # here so it fires even on an early-call failure.
        ctx.add_shutdown_callback(lambda: background.cancel_all())
        # A call that dies before it has a session drops its hush marker here;
        # a call that started keeps it until the tail of _on_shutdown instead,
        # because shutdown callbacks run CONCURRENTLY and this one must not
        # race the tape playout into un-quieting the station mid-reel. The
        # split is exact: _on_shutdown is only ever registered once a session
        # exists (_attach_behaviours), so each marker has one owner.
        ctx.add_shutdown_callback(self._hush_sweep)
        self._hush_task = None

    # -- ringing ----------------------------------------------------------
    async def prepare(self, connecting=None) -> None:
        """Resolve who answers and what they know. The caller hears every
        millisecond of this as ringing, which is why everything here rides ONE
        concurrent wait: the station snapshot, the voice map, the TTS voice
        list, the station's MCP handshake — and the room join itself, when the
        entrypoint hands its coroutine in. None of them needs another's
        answer, and every serial wait this list used to hold was measured on a
        real caller's ringing first (12s+ pickups, 2026-08-10)."""
        import asyncio
        import contextlib

        join = asyncio.create_task(connecting) if connecting is not None else None
        try:
            await self._resolve()
        except BaseException:
            # The join must not outlive a failed prepare: an orphaned connect
            # would hold the room open with nobody coming to answer it.
            if join is not None:
                join.cancel()
                with contextlib.suppress(BaseException):
                    await join
            raise
        if join is not None:
            await join
        if self.record:
            self.record.leg("prepared")

    async def _resolve(self) -> None:
        """The work of prepare(): who answers, in what voice, knowing what."""
        # The last of the four places that published tts_mode into os.environ.
        # It was here so voice resolution read the right registry — cloud and
        # local voices are not interchangeable — but station_config asks
        # settings.tts_mode(), which reads the settings file, and the adapter
        # is told its mode directly now. Nothing reads the variable any more.
        import asyncio

        # The MCP handshake starts NOW and finishes whenever it finishes.
        # It used to sit SERIAL in start(), in front of the greeting, where a
        # congested station could hold it for its whole 7s cap (measured
        # 2026-08-10). MCPToolset.setup() at session start awaits this same
        # connect if it is still in flight, skips it when it is done, and
        # retries it when the warm-up failed — so starting early can only
        # ever move the wait under the ringing, never add one.
        self.station_tools = self._station_server()
        self._tools_warm = asyncio.create_task(self._warm_station_tools())
        self.ctx.add_shutdown_callback(lambda: lifecycle.cancel(self._tools_warm))

        # Quiet the station's own DJ for the whole call, when the operator
        # asked for that on every call. Rides the ringing beside the other
        # station reads; never raises and never blocks the pickup — a station
        # that answers slowly costs the caller nothing, and the token server's
        # janitor finishes an assert this could not confirm. The on_air scope
        # engages in start() instead, only once the relay actually arms —
        # "quiet while broadcasting" must not fire for a call that falls back
        # private on a dead mixer. Held on self so it cannot be GC'd mid-write
        # (see api/tokens.py for that failure).
        if hush.scope(self.cfg) == "all":
            self._hush_task = asyncio.create_task(
                hush.engage(self.cfg, self.room_name))

        # The TTS voice list needs nothing the station knows, so it rides the
        # same wait — it was the one read still sitting serial at the tail of
        # prepare (behind pick_speakable_voice below).
        async def _speakable() -> list:
            return await available_voices(
                str(self.cfg.get("tts_base_url") or "").strip(),
                adapter_path=resolve_adapter(self.cfg.get("tts_adapter")),
                mode=str(self.cfg.get("tts_mode", "")),
            )

        # The voices map does NOT depend on which persona answers — it is the
        # whole station's persona->voice mirror — so it rides in the SAME
        # concurrent wait as the snapshot instead of a serial read after it.
        # voice_for() below then reuses the /settings response this warmed
        # (StationConfig caches per path), so it costs no further network.
        #
        # And when the token server prefetched this call's snapshot at mint
        # time (station_prefetch.py), both station reads are already answered:
        # the snapshot is adopted as-is and /settings is primed into the
        # cache, so the ringing carries no station wait at all.
        want_skills = bool(self.cfg.get("allow_skills"))
        recalled = station_prefetch.recall(with_skills=want_skills)
        if recalled is not None:
            snap, station_settings = recalled
            self.station_cfg.prime("/settings", station_settings)
            _voice_map, speakable = await asyncio.gather(
                self.station_cfg.persona_voices(), _speakable(),
            )
        else:
            snap, _voice_map, speakable = await asyncio.gather(
                self.station.snapshot(with_skills=want_skills),
                self.station_cfg.persona_voices(),
                _speakable(),
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
        self.record = CallRecord(self.room_name, self.persona, self.cfg,
                                 self.tier, started=self.started_at)
        # Which path the ringing took, so a slow-pickup report can tell a
        # missed head start from a slow station without a shell.
        self.record.setup_note(
            "snapshot", "prefetched" if recalled is not None else "fetched")
        # The ducking timeline rides with the guard and lands on the record at
        # the end — see call/air_log.py. Attached here rather than built in the
        # guard so a guard nobody is recording carries no cost.
        self.air.air_log = AirLog(since=self.started_at)

        # Checked against the backend BEFORE the first line, not discovered by
        # the caller. See tts_adapter.pick_speakable_voice — a voice the
        # backend does not have used to mean a call where the DJ never spoke
        # at all, with a green pipeline check, because that check tests the
        # CONFIGURED voice and never the one the on-air persona resolves to.
        self.voice, why = pick_speakable_voice(self.voice, speakable)
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

    def _station_server(self) -> mcp.MCPServerHTTP:
        """The station's MCP endpoint for this call. Built (and connected —
        see _warm_station_tools) during ringing; start() hands it to the
        toolset already open on a healthy station."""
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
        allowed_tools = mcp_allowlist(self.cfg)
        self.allowed_tool_count = len(allowed_tools)
        gated_tools = allowed_tools or ["__none__"]

        return mcp.MCPServerHTTP(
            url=settings_store.station_mcp_url(),
            transport_type="streamable_http",
            allowed_tools=gated_tools,
            headers=mcp_headers or None,
            # 7s, down from 15. This connect used to happen BEFORE the
            # greeting, so on a slow/overloaded station it sat 15s of silence
            # in front of the caller before the DJ said hello (measured
            # 2026-08-10, a congested NAS). It starts under the ringing now,
            # but the cap stays: greeting late is worse than starting with
            # the local tools and the MCP catalogue arriving a moment in.
            client_session_timeout_seconds=7,
        )

    async def _warm_station_tools(self) -> None:
        """Open the MCP session while the caller still hears ringing.

        Failure costs nothing here: MCPToolset.setup() checks the server at
        session start and retries the connect exactly where it used to run."""
        try:
            await self.station_tools.initialize()
        except Exception as e:                                  # noqa: BLE001
            log.info("station tools connect deferred to session start: %s",
                     describe(e))

    # -- picking up -------------------------------------------------------
    def _build_tools(self) -> list:
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
        return local

    async def start(self) -> None:
        """Build the voice session and put the DJ on the line."""
        import asyncio
        # The on-air relay opens BEFORE the session is built, for one reason:
        # the prompt. The DJ must be told it is live before its first word,
        # and the only honest moment to decide that is after the transport
        # preflight — a prompt written earlier would claim an air that a dead
        # mixer then never carries. The intro line airs during what the
        # caller still hears as ringing.
        if self.on_air_asked:
            relay = CallRelay(self.station, self.cfg, self.room_name,
                              tier=self.tier, record=self.record)
            if await relay.open():
                self.relay = relay
                # The overlap guard holds the call DJ while "the broadcast
                # talks" — but on this call the broadcast IS the call, and
                # the relay's own intro/outro must not gag the conversation
                # they bracket.
                self.air.enabled = False
                if self.record:
                    self.record.data["config"]["onAir"] = True
                # The window is NAMED, not just enforced. A DJ that does not
                # know how long its segment runs cannot pace one — and the
                # window is enforced regardless, so without this a live
                # phone-in simply stopped mid-thought whenever the clock ran
                # out. Radio does not end a segment that way.
                window = int(self.cfg.get("on_air_max_seconds") or 0) or 240
                # The on_air scope's moment: the relay is armed, this call IS
                # going to broadcast. (The "all" scope engaged back in
                # prepare(), where the greeting is still ahead.)
                if hush.scope(self.cfg) == "on_air":
                    self._hush_task = asyncio.create_task(
                        hush.engage(self.cfg, self.room_name))
                if self.relay.tape:
                    # Tape mode's truth is different, and the DJ must not
                    # claim "as it happens" on a call that airs at hangup —
                    # and there is no wrap cue to promise: the reel plays
                    # whole, so there is no live clock to be near the end of.
                    self.instructions += (
                        "\n\nThis call is being TAPED FOR AIR: the whole "
                        "conversation plays on the station the moment the "
                        "call ends. Keep it broadcast-clean and keep the "
                        "pace bright. Never read out private details — "
                        "numbers, addresses, codes — the listeners will hear "
                        f"everything the caller says. About {window // 60} "
                        "minute(s) of it can air — pace it so it lands "
                        "rather than stops.")
                else:
                    self.instructions += (
                        "\n\nThis call is LIVE ON AIR: the conversation is "
                        "broadcast on the station as it happens, a few "
                        "seconds behind. Keep it broadcast-clean and keep "
                        "the pace bright. Never read out private details — "
                        "numbers, addresses, codes — the listeners hear "
                        "everything the caller says. The segment runs about "
                        f"{window // 60} minute(s) — pace it so it lands "
                        "rather than stops, and you will be told when you "
                        "are near the end.")
            # A failed open already wrote why to the record; the call simply
            # proceeds as a private one.

        local_tools = self._build_tools()

        log.info(
            "call starting room=%s tier=%s persona=%s (%s) llm=%s/%s tts=%s voice=%s tools=%d",
            self.room_name, self.tier, self.persona["name"], self.persona["id"],
            self.cfg["llm_provider"], self.cfg["llm_model"],
            self.cfg["tts_mode"], self.voice,
            self.allowed_tool_count + len(local_tools),
        )

        # MCPToolset rather than the session's mcp_servers argument, which is
        # deprecated: the toolset is just another entry in `tools`, so the
        # station's tools and our own wrappers arrive by the same route. The
        # server it wraps was built — and its connect started — back in
        # prepare(), under the ringing; setup() reuses that session or, if the
        # warm-up failed, retries the connect here as it always did.
        toolset = mcp.MCPToolset(id="subwave", mcp_server=self.station_tools)

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
        if self.relay:
            # Both taps go in only once the session's own IO chain exists,
            # and before greet() — the greeting is the first DJ clip to air.
            self._tee = tee_mod.attach(self.session, self.relay)
        # AFTER the tee, deliberately, and not in _attach_behaviours with the
        # rest: the meter listens on session.output.audio, and on an on-air
        # call the tee has just REPLACED that object with its own. Attached
        # earlier it would be watching a chain nothing plays through any more,
        # and the barge-in half of the pair would read zero on exactly the
        # calls it matters most on.
        heard_mod.attach_heard(self.session, self.pacing, air=self.air)
        if self.record:
            self.record.leg("onLine")

    def _attach_behaviours(self) -> None:
        """Everything that runs for the life of the call."""
        import asyncio

        session, ctx, cfg = self.session, self.ctx, self.cfg

        # The broadcast hierarchy: while the on-air DJ has the microphone, the
        # call DJ waits. Started after the session so the watcher can interrupt
        # it.
        air_task = asyncio.create_task(self.air.watch(session))
        ctx.add_shutdown_callback(lambda: lifecycle.cancel(air_task))

        # Keep this call's hush marker fresh, when this call quieted the
        # station: the janitor reads a stopped heartbeat as a dead job and
        # restores the station's voice. Cancelled at shutdown like the rest;
        # _on_shutdown then runs a beat of its own so the tee drain and the
        # tape playout stay covered to the marker's last moment.
        if (hush.scope(cfg) == "all"
                or (self.relay and hush.scope(cfg) == "on_air")):
            hush_beat = asyncio.create_task(hush.heartbeat(self.room_name))
            ctx.add_shutdown_callback(lambda: lifecycle.cancel(hush_beat))

        lifecycle.attach_close_reason(session, self.ended)
        lifecycle.attach_error_recovery(session, self.record, self.think)
        lifecycle.attach_think_pace(session, self.think)
        lifecycle.attach_first_word(session, self.record)
        lifecycle.attach_turn_commit(ctx, session, pacing=self.pacing)
        lifecycle.attach_heard_logging(session, self.heard, self.record)
        lifecycle.attach_card_flush(session, self.actions)
        # Attached after card_flush and before the idle watch on purpose: it
        # reacts to the same conversation_item_added, and the receipt cards
        # for a nudged tool must still land behind the DJ's line.
        promise_guard.attach_promise_guard(session, self.record, self.actions,
                                           air=self.air, floor=self.floor)
        door.attach_door_watch(session, self.door)
        comeback.attach_air_watch(session, self.air)
        floor_mod.attach_floor_watch(session, self.floor)
        asks_mod.attach_ask_watch(session, self.asks)
        lifecycle.attach_idle_watch(ctx, session, cfg, air=self.air,
                                    heard=self.heard, actions=self.actions)
        lifecycle.attach_time_limit(ctx, session, cfg, air=self.air,
                                    floor=self.floor)
        # Says one short line over a long wait rather than leaving the caller
        # in silence. Given the air guard so it never explains a pause the
        # hand-over line has already explained.
        lifecycle.attach_working_line(ctx, session, cfg, air=self.air,
                                      actions=self.actions)
        # A no-op off air — seconds_left() is 0 unless a relay is actually
        # live — so it attaches unconditionally rather than behind a branch
        # that would then need testing twice.
        clocks.attach_on_air_wrap(ctx, session, self.relay, floor=self.floor)
        ctx.add_shutdown_callback(self._on_shutdown)

    async def greet(self) -> None:
        if self.record:
            self.record.leg("greeting")
        await greeting.greet(self.session, self.cfg, record=self.record,
                              air=self.air)

    # -- hanging up -------------------------------------------------------
    async def _hush_sweep(self) -> None:
        """The early-death half of the hush marker's removal — see the
        registration site in __init__ for why the started-call half lives at
        the tail of _on_shutdown instead."""
        if self.session is None:
            hush.call_ended(self.room_name)

    async def _on_shutdown(self) -> None:
        """Runs after the caller hangs up, so the station reflects the call."""
        import asyncio

        # The per-call heartbeat dies with the other shutdown callbacks
        # (they run concurrently), but everything below — the tee drain, a
        # whole tape playout, the record — still needs the hush marker
        # fresh, or the janitor would un-quiet the station mid-reel. So the
        # shutdown carries its own beat, which is what lets the staleness
        # ceiling be minutes instead of out-waiting the longest possible
        # playout (0.98.14; it was 600s for exactly that reason).
        beat = asyncio.create_task(hush.heartbeat(self.room_name))
        try:
            await self._shutdown_work()
        finally:
            # LAST, after the relay's brackets and the handoff have spoken
            # through it — see the note at the registration site in
            # __init__ for the tape soak this ordering fixes. A client that
            # fails to close is a leak, not a reason to lose the record.
            try:
                await self.station.aclose()
            except Exception:                                   # noqa: BLE001
                pass
            # After the playout above, deliberately: dropping the marker is
            # what frees the janitor to un-quiet the station, and the reel
            # must finish airing first. Local disk, so it cannot fail the
            # record the way a network call could. The beat stops first —
            # a heartbeat racing the unlink would resurrect the marker as
            # an orphan the janitor then has to wait out.
            lifecycle.cancel(beat)
            hush.call_ended(self.room_name)

    async def _shutdown_work(self) -> None:
        # The relay closes FIRST, inside this callback rather than as its own
        # (the SDK runs shutdown callbacks concurrently, and the off-air line
        # belongs inside the record that is written below): the caller's last
        # word is usually still mastering, so the tee drains before the relay
        # pushes its held tail and signs off air.
        if self.relay:
            try:
                if self._tee:
                    await self._tee.drain()
                await self.relay.close("the call ended")
            except Exception as e:                              # noqa: BLE001
                log.warning("the on-air relay did not close cleanly: %s", e)

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
            self.room_name, self.persona.get("name"),
            duration, self.heard["n"],
            self.cfg.get("llm_provider"), self.cfg.get("llm_model"),
            self.cfg.get("tts_mode"), reason or "-",
        )
        # PRINTED, not logged, for the same reason main.py prints its
        # banner: setup("worker", console=False) leaves log.info a file sink
        # and an in-memory ring, LOG_TO_FILE is off on a container deploy, and
        # the panel's log viewer reads the WEB process's ring — so a log line
        # here reaches nothing a `docker logs` can find. Verified the hard way
        # on 2026-08-18: this went out as log.info and grepping the deployed
        # worker for it returned nothing at all.
        #
        # The pair on one greppable line, so a harness run or a bad-call
        # report can be read without opening the record. Both halves or
        # neither — see call/heard.py for why they are never split up.
        paced = self.pacing.summary()
        if paced:
            gap, barge = paced.get("replyGap", {}), paced.get("bargeIn", {})
            print(
                f"call pacing room={self.room_name} "
                f"replies={gap.get('n', 0)} p50={gap.get('p50', 0):.2f}s "
                f"p90={gap.get('p90', 0):.2f}s worst={gap.get('worst', 0):.2f}s "
                f"| barge_ins={barge.get('n', 0)} "
                f"p50={barge.get('p50', 0):.2f}s "
                f"cut_off={len(paced.get('cutOff', []))}",
                flush=True)
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
                self.record.what_they_heard(self.pacing.summary())
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
