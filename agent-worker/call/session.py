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
from livekit.agents.voice.room_io import RoomOptions

import brain
import settings as settings_store
import speech_filter
import station_config as station_config_mod
from station import StationClient
from station_config import StationConfig
from tts_adapter import available_voices, pick_speakable_voice, resolve_adapter

from . import lifecycle
from .actions import CallActions
from .air import CallAgent, OnAirGuard
from .record import CallRecord
from .providers import build_llm, build_stt, build_tts
from .tools import (
    build_call_control_tools,
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
        self.cfg = settings_store.load()
        self.station = StationClient()
        self.station_cfg = StationConfig()

        self.persona: dict = {}
        self.voice = ""
        self.instructions = ""
        self.session: AgentSession | None = None

        self.actions = CallActions(self.cfg.get("max_actions_per_call"), room=ctx.room)
        self.air = OnAirGuard(self.station, self.cfg, room=ctx.room)

        self.started_at = time.time()
        self.heard = {"n": 0}
        # Why the call ended, as the SDK saw it. ctx.shutdown_reason is empty
        # when the caller simply hangs up, so the record could not tell "they
        # rang off" from "the line dropped" — which is the first thing you want
        # to know when someone reports a call cutting out.
        self.ended = {"reason": ""}
        # Filled in once the persona is known — see prepare().
        self.record: CallRecord | None = None

        ctx.add_shutdown_callback(self.station.aclose)
        ctx.add_shutdown_callback(self.station_cfg.aclose)

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
        snap = await self.station.snapshot(
            with_skills=bool(self.cfg.get("allow_skills"))
        )
        self.persona = self._resolve_persona(snap)

        # Whose voice this is, so the model writing "Francesca:" as a script
        # label never gets read out as part of the line.
        speech_filter.set_speaker(self.persona.get("name", ""))

        self.voice = (
            str(self.cfg.get("tts_voice") or "").strip()
            or await self.station_cfg.voice_for(self.persona["id"])
        )
        self.instructions = await brain.build_system_prompt(
            self.station, self.persona, snapshot=snap
        )
        self.record = CallRecord(self.ctx.room.name, self.persona, self.cfg)

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
            self.cfg, self.station, self.actions, self.air, guarded=guarded
        )
        local += build_library_tools(self.cfg, self.station, self.actions)
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
            "call starting room=%s persona=%s (%s) llm=%s/%s tts=%s voice=%s tools=%d",
            self.ctx.room.name, self.persona["name"], self.persona["id"],
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

        station_tools = mcp.MCPServerHTTP(
            url=settings_store.station_mcp_url(),
            transport_type="streamable_http",
            allowed_tools=allowed_tools,
            headers=mcp_headers or None,
            client_session_timeout_seconds=15,
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
        )

        await self.session.start(
            agent=CallAgent(self.instructions, self.air),
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
        lifecycle.attach_error_recovery(session, self.record)
        lifecycle.attach_heard_logging(session, self.heard, self.record)
        lifecycle.attach_idle_watch(ctx, session, cfg, air=self.air,
                                    heard=self.heard)
        lifecycle.attach_time_limit(ctx, session, cfg)
        ctx.add_shutdown_callback(self._on_shutdown)

    async def greet(self) -> None:
        await lifecycle.greet(self.session, self.cfg)

    def _note_if_nothing_was_heard(self, duration: float, final: list) -> None:
        """Say so, in the record, when a call produced no caller audio.

        An off-LAN caller whose media path never establishes looks exactly like
        a healthy call from in here: the room is joined, the agent starts, the
        greeting plays, and then the line drops around fifteen seconds later
        with nothing received. That is what the first outside caller hit, and
        NOTHING in our own logs said so — the failure was only visible in
        LiveKit's ICE candidates and the caller's browser console.

        This can't distinguish a broken media path from a silent caller, so it
        doesn't pretend to. It records the shape and names the candidates in
        order of likelihood, which is what a future investigation needs.
        """
        if self.heard["n"] or not self.record:
            return

        dj_spoke = any(who == "dj" and text.strip() for who, text in final)
        log.warning(
            "no caller audio received room=%s duration=%.0fs dj_spoke=%s — "
            "media path, blocked microphone, or a silent caller",
            self.ctx.room.name, duration, dj_spoke,
        )
        self.record.problem(
            f"No audio was ever received from the caller ({duration:.0f}s on the "
            f"line, the DJ {'did' if dj_spoke else 'did not'} speak). Three "
            "things look like this from the booth: the caller was off-LAN and "
            "the media path never established, their microphone was blocked, "
            "or they genuinely said nothing. If they reported \"Could not "
            "connect\" after about fifteen seconds of ringing, it is the first "
            "— see off-LAN calling in the README."
        )

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
                    for role, text in lifecycle._transcript(self.session, limit=400)
                ]
                self.record.finalise(final)
            except Exception as e:
                log.debug("could not finalise the transcript (keeping live text): %s", e)
                final = []
            self._note_if_nothing_was_heard(duration, final)
            if self.cfg.get("record_calls", True):
                self.record.write(reason=reason,
                                  keep=int(self.cfg.get("record_keep") or 0))
            else:
                # Built in memory either way — the problems it collects are
                # what _note_if_nothing_was_heard writes into — but an operator
                # who turned this off gets nothing on disk.
                log.info("call transcripts are off — nothing written for %s",
                         self.ctx.room.name)

        await lifecycle.release_call_slot(self.ctx.room.name)
        await lifecycle.send_on_air_callback(
            self.session, self.station, self.persona, self.cfg
        )
