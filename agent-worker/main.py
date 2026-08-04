"""
LiveKit Agents worker — the SUB/WAVE call-in DJ.

Flow: caller opens the widget -> widget asks the token server for a join token
-> LiveKit dispatches a job here -> this worker resolves whoever is live on
air, builds that persona's prompt from the station, and runs an STT -> LLM ->
TTS voice session with the station's own MCP tools attached.

Two deliberate "don't duplicate the station" choices:

  * Station actions go through the station's MCP server, not a re-wrapped REST
    client. Filtered by an allowlist, because a caller is an untrusted stranger
    driving the agent by voice and the station's MCP surface includes
    destructive tools (skip_track, play_sfx, queue_track, dj_segment,
    refresh_playlist). Those are never exposed on a call line.

  * Persona voice comes from the station's own config where readable
    (station_config.py), falling back to persona-voices.json only when the
    station won't say.

Settings are re-read at the start of every call, so changes made in the call
page take effect on the next caller without restarting this worker.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    RoomInputOptions,
    WorkerOptions,
    cli,
    mcp,
)
# All plugins are imported at module scope on purpose. LiveKit registers
# plugins at import time and requires that to happen on the main thread —
# importing them lazily inside build_stt()/build_llm() raises
# "Plugins must be registered on the main thread" once a job is running.
from livekit.agents.types import NOT_GIVEN
from livekit.plugins import anthropic, deepgram, google, openai, silero

import prompts
import secrets_store
import settings as settings_store
import station_config as station_config_mod
from station import StationClient
from station_config import StationConfig
from tts_adapter import AdapterTTS

load_dotenv(Path(__file__).parent.parent / ".env")

import log_setup

log_setup.setup("worker")
log = logging.getLogger("callin.agent")

def station_mcp_url() -> str:
    """Resolved per call, so re-homing the sidecar to another station from the
    settings page takes effect on the next caller."""
    return settings_store.station_mcp_url()

# Reads are always available to the agent; writes are toggled in settings.
READ_TOOLS = [
    "subwave_health",
    "subwave_now_playing",
    "subwave_station_state",
    "subwave_schedule",
    "subwave_session",
]

# Deliberately never exposed on a call line, regardless of settings:
# subwave_skip_track (a caller could cut off whatever is playing),
# subwave_queue_track (bypasses the request queue and its rate limits),
# subwave_play_sfx / subwave_list_sfx (stingers on a stranger's say-so),
# subwave_dj_segment and subwave_refresh_playlist (station-level programming).
OPTIONAL_TOOLS = {
    "allow_requests": ["subwave_request_song", "subwave_request_status"],
    # The extra names are pre-authorised for when the station exposes its
    # sounds-like/semantic search over MCP (it exists in the admin UI but
    # isn't on the API yet) — unknown names in an allowlist are harmless,
    # and this way it lights up for callers the moment the station ships it.
    "allow_library_search": ["subwave_search_library", "subwave_sounds_like",
                             "subwave_search_sounds_like", "subwave_audio_search",
                             "subwave_semantic_search"],
    "allow_announcements": ["subwave_dj_announce"],
    # The station's own segments — weather, news, dedications, story time.
    # list_skills is paired so the DJ knows what it can actually run rather
    # than guessing at names.
    "allow_skills": ["subwave_run_skill", "subwave_list_skills"],
}


# Tools that make the on-air DJ produce sound. When overlap protection is on
# these are served by local wrappers that check the air is clear first, so they
# come off the MCP allowlist to avoid exposing an unguarded duplicate.
ON_AIR_TOOLS = {"subwave_dj_announce", "subwave_run_skill"}

# Served by local wrappers with retry/fallback logic (build_library_tools) —
# never exposed raw over MCP, or the model could reach the fragile version.
LOCALLY_WRAPPED = {"subwave_search_library", "subwave_request_song"}


def build_allowed_tools(cfg: dict, *, guarded: bool = False) -> list[str]:
    allowed = list(READ_TOOLS)
    for flag, tools in OPTIONAL_TOOLS.items():
        if cfg.get(flag):
            allowed.extend(tools)
    allowed = [t for t in allowed if t not in LOCALLY_WRAPPED]
    if guarded:
        allowed = [t for t in allowed if t not in ON_AIR_TOOLS]
    return allowed


def build_on_air_tools(cfg: dict, station: StationClient) -> list:
    """On-air actions that keep the call and the broadcast from colliding.

    The call DJ and the on-air DJ are the same persona, so the two can end up
    talking at once. Rather than blocking the action (which just makes the DJ
    seem broken to the caller), these wait for the air to clear if it's busy,
    fire the action, then tell the agent to step back from the call while it
    plays — with a word to the caller either side, the way a real presenter
    would say "hold on, I'm on air".
    """
    from livekit.agents import llm as lk_llm

    quiet_needed = float(cfg.get("on_air_quiet_secs") or 0)
    max_wait = 20.0   # never leave the caller hanging longer than this

    async def wait_for_clear_air() -> float:
        """Block until the on-air DJ stops, up to max_wait. Returns how long
        we waited, so the agent can explain the pause."""
        waited = 0.0
        while waited < max_wait:
            since = await station.seconds_since_on_air_speech()
            if since is None or since >= quiet_needed:
                return waited
            pause = min(3.0, quiet_needed - since)
            await asyncio.sleep(pause)
            waited += pause
        return waited

    def after_action(what: str, waited: float) -> str:
        note = f"Waited {waited:.0f}s for the air to clear. " if waited >= 2 else ""
        return (
            f"{note}{what} is going out on air now, in your own voice, and it runs "
            "roughly twenty seconds. You cannot be in two places at once: tell the "
            "caller briefly that you're on air for a moment, then stay quiet until "
            "it's done — do not talk over yourself. When it finishes, come back to "
            "them and pick the conversation up where you left it."
        )

    tools = []

    if cfg.get("allow_announcements"):
        @lk_llm.function_tool(name="subwave_dj_announce")
        async def announce(message: str, mode: str = "styled") -> str:
            """Put a short line on air, read by the on-air DJ in its own voice.
            Use for shoutouts, dedications, or anything from the call worth
            sharing with listeners."""
            waited = await wait_for_clear_air()
            result = await station.dj_say(message, mode=mode, kind="callin")
            if not result:
                return "The station wouldn't take that just now — tell the caller plainly."
            return after_action("Your announcement", waited)

        tools.append(announce)

    if cfg.get("allow_skills"):
        @lk_llm.function_tool(name="subwave_run_skill")
        async def run_skill(name: str) -> str:
            """Run one of the station's own segments on air by name — for
            example weather, news, dedication, shoutout, storytime."""
            waited = await wait_for_clear_air()
            result = await station.run_skill(name)
            if not result.get("ok"):
                return (result.get("error") or "The station wouldn't run that one.")
            return after_action(f"The {name} segment", waited)

        tools.append(run_skill)

    return tools


def _query_variants(q: str) -> list[str]:
    """The station's search requires EVERY word to match, so the natural
    phrase "Let It Be by The Beatles" returns nothing — "by" appears in no
    title or artist. Try as given, then with the last " by " connector
    removed, then the left side alone. Rightmost split keeps titles that
    themselves contain "by" ("Stand by Me by Ben E. King") intact."""
    variants = [q]
    idx = q.lower().rfind(" by ")
    if idx > 0:
        variants.append(q[:idx] + " " + q[idx + 4:])
        variants.append(q[:idx])
    return variants


def _fmt_track(t: dict) -> str:
    bits = f"\"{t.get('title', '?')}\" by {t.get('artist', '?')}"
    if t.get("album"):
        bits += f" ({t['album']}" + (f", {t['year']})" if t.get("year") else ")")
    return bits


def build_library_tools(cfg: dict, station: StationClient) -> list:
    """Search and request as local tools with deterministic fallbacks.

    Prompt guidance about query phrasing turned out to be soft — the model
    followed it most of the time, and a caller heard "can't pull that from
    the racks" for a track the library holds three copies of. These wrappers
    make good phrasing unnecessary: the tool itself retries with the "by"
    connector stripped before ever reporting a miss.
    """
    from livekit.agents import llm as lk_llm

    tools = []

    if cfg.get("allow_library_search"):
        @lk_llm.function_tool(name="subwave_search_library")
        async def search_library(q: str) -> str:
            """Search the station's music library by title and/or artist.
            Returns matching tracks with album and year. Prefer the track
            title alone; the tool handles the rest."""
            for attempt in _query_variants(q):
                items = await station.search_library(attempt)
                if items:
                    note = "" if attempt == q else (
                        f" (matched on '{attempt}' — the library needs every word to match)"
                    )
                    lines = [_fmt_track(t) for t in items[:8]]
                    more = f" …and {len(items) - 8} more" if len(items) > 8 else ""
                    joined = "\n".join(lines)
                    return f"{len(items)} result(s){note}:\n" + joined + more
            return (
                "Nothing found for that, even after loosening the phrasing. "
                "Try the title alone, a different spelling, or the artist alone."
            )

        tools.append(search_library)

    if cfg.get("allow_requests"):
        @lk_llm.function_tool(name="subwave_request_song")
        async def request_song(request: str, requester: str = "") -> str:
            """Ask the station to play something. Vague is fine ('something
            slower', 'more like this') — the station resolves it. For a
            specific track, give title and artist."""
            text = request
            idx = request.lower().rfind(" by ")
            if idx > 0 and cfg.get("allow_library_search"):
                # Pre-flight: if the full phrase finds nothing but the
                # cleaned one does, submit the cleaned one — the station's
                # own resolver trips on the same matcher and substitutes a
                # different song rather than failing.
                if not await station.search_library(request):
                    cleaned = request[:idx] + " " + request[idx + 4:]
                    if await station.search_library(cleaned):
                        text = cleaned

            res = await station.submit_request(text, requester or "")
            if res.get("error"):
                return f"The station couldn't take that request: {res['error']}"

            rid = res.get("requestId") or res.get("id")
            if rid:
                await asyncio.sleep(2.0)
                st = await station.request_status(str(rid))
                track = st.get("track") or st.get("matched") or {}
                ack = st.get("ack") or st.get("message") or st.get("reply") or ""
                if isinstance(track, dict) and track.get("title"):
                    out = f"Queued: {_fmt_track(track)}."
                    return out + (f" Station says: {ack}" if ack else "")
                if ack:
                    return f"Station says: {ack}"
            return "Request is in — the station is lining something up."

        tools.append(request_song)

    return tools


def model_for(provider: str, requested: str, choices: dict, default: str) -> str:
    """Model names are provider-specific and must never survive a provider
    switch. Carrying one across produces a 404 on every single utterance —
    e.g. Deepgram's "nova-3" sent to OpenAI — which looks like the caller
    simply isn't being heard.
    """
    requested = (requested or "").strip()
    if requested and requested in choices.get(provider, []):
        return requested
    if requested:
        log.warning(
            "%s is not a %s model — using %s instead", requested, provider, default
        )
    return default


def effective_stt(cfg: dict) -> tuple[str, str, str]:
    """Resolve (provider, model, note) actually used, accounting for missing
    keys. Exposed so the settings page can show what will really run rather
    than what was merely selected."""
    wanted = str(cfg.get("stt_provider", "deepgram")).lower()
    requested = cfg.get("stt_model") or ""
    choices = settings_store.STT_MODEL_CHOICES
    note = ""

    if wanted == "local":
        provider, default = "local", "base.en"
    elif wanted == "deepgram" and os.environ.get("DEEPGRAM_API_KEY"):
        provider, default = "deepgram", "nova-3"
    elif wanted == "openai" or (wanted == "deepgram" and os.environ.get("OPENAI_API_KEY")):
        provider, default = "openai", "gpt-4o-mini-transcribe"
        if wanted == "deepgram":
            note = "no Deepgram key — using OpenAI STT"
    else:
        provider, default = "google", ""
        if wanted != "google":
            note = f"no usable key for {wanted} — falling back to Google STT"

    model = model_for(provider, requested, choices, default)
    if requested and model != requested:
        note = (note + "; " if note else "") + f"'{requested}' is not a {provider} model"
    return provider, model, note


def build_stt(cfg: dict):
    provider, model, note = effective_stt(cfg)
    if note:
        log.warning("STT: %s", note)

    if provider == "local":
        from local_stt import LocalWhisperSTT

        return LocalWhisperSTT(model=model, language="en")
    if provider == "deepgram":
        return deepgram.STT(model=model, language="en-US")
    if provider == "openai":
        return openai.STT(model=model, language="en")
    return google.STT(languages="en-US")


def build_llm(cfg: dict):
    provider = str(cfg.get("llm_provider", "openai")).lower()
    # Same hazard as STT: a model left over from another provider.  The
    # discovered lists are authoritative when available, so only drop a model
    # that clearly belongs to a different provider.
    model = cfg.get("llm_model") or None
    if model and provider in settings_store.MODEL_CHOICES:
        wrong = [p for p, ms in settings_store.MODEL_CHOICES.items()
                 if p != provider and model in ms]
        if wrong:
            log.warning("%s is a %s model, not %s — using the provider default",
                        model, wrong[0], provider)
            model = None
    base_url = str(cfg.get("llm_base_url") or "").strip()
    temperature = float(cfg.get("llm_temperature", 0.8))

    if provider == "ollama":
        # Ollama speaks the OpenAI protocol. Tool calling depends on the model
        # supporting it — qwen/llama3.1-class models do, many smaller ones
        # silently don't, which shows up as a DJ that never actually submits
        # a request. Use the Test button in settings to check.
        return openai.LLM.with_ollama(
            model=model or "llama3.1",
            base_url=base_url or os.environ.get(
                "OLLAMA_BASE_URL", "http://localhost:11434/v1"
            ),
            temperature=temperature,
        )

    if provider == "openrouter":
        # One key, ~340 models including free tiers. Its model listing is
        # public, so the settings page can populate before a key is entered.
        return openai.LLM.with_openrouter(
            model=model or "auto",
            api_key=os.environ.get("OPENROUTER_API_KEY"),
            temperature=temperature,
        )

    if provider == "openai":
        return openai.LLM(
            model=model or "gpt-4.1-mini",
            temperature=temperature,
            **({"base_url": base_url} if base_url else {}),
        )
    if provider == "google":
        return google.LLM(
            model=model or "gemini-2.5-flash",
            api_key=os.environ.get("GOOGLE_API_KEY"),
            temperature=temperature,
        )
    if provider == "anthropic":
        return anthropic.LLM(model=model or "claude-sonnet-5", temperature=temperature)

    raise ValueError(f"Unsupported llm_provider: {provider}")


def build_tts(cfg: dict, voice: str) -> AdapterTTS:
    adapter_path = cfg.get("tts_adapter") or None
    if adapter_path and not os.path.isabs(adapter_path):
        candidate = Path(__file__).parent / "tts-adapters" / adapter_path
        if candidate.exists():
            adapter_path = str(candidate)

    # settings.tts_mode drives the default adapter choice inside tts_adapter.
    os.environ["TTS_MODE"] = str(cfg.get("tts_mode", "cloud"))

    return AdapterTTS(
        voice=voice,
        base_url=cfg.get("tts_base_url") or os.environ.get("TTS_BASE_URL", ""),
        adapter_path=adapter_path,
        model=cfg.get("tts_model") or "",
    )


def prewarm(proc: JobProcess) -> None:
    """Load VAD once per worker process instead of once per call."""
    proc.userdata["vad"] = silero.VAD.load()

    # If local STT is selected, load the model now — otherwise the first
    # caller waits ~7s mid-call for it.
    try:
        cfg = settings_store.load()
        if str(cfg.get("stt_provider", "")).lower() == "local":
            from local_stt import preload_sync

            preload_sync(cfg.get("stt_model") or "base.en")
    except Exception as e:
        log.warning("local STT prewarm skipped: %s", e)


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    # Media-path probes from the pipeline check are real rooms, but answering
    # one with a full agent session would spend an LLM+TTS round on nothing.
    if ctx.room.name.startswith("probe-"):
        log.info("media-path probe %s — not starting an agent session", ctx.room.name)
        return

    # Keys entered in the settings page live in their own store; push them into
    # the environment before building providers, since the SDKs read env.
    secrets_store.apply_to_env()
    cfg = settings_store.load()

    # Publish the resolved mode before anything resolves a voice — the voice
    # registries for cloud and local are not interchangeable, and this used to
    # be set later (inside build_tts), so the first call of a session could
    # resolve a voice against the wrong one.
    os.environ["TTS_MODE"] = str(cfg.get("tts_mode", "cloud"))

    station = StationClient()
    station_cfg = StationConfig()
    ctx.add_shutdown_callback(station.aclose)
    ctx.add_shutdown_callback(station_cfg.aclose)

    # One button, whoever is live answers — unless a persona is pinned in
    # settings, which is mainly a testing affordance.
    # One concurrent snapshot instead of six serial reads — the caller hears
    # every millisecond of this as ringing before the DJ picks up.
    snap = await station.snapshot()

    override = str(cfg.get("persona_override") or "").strip()
    roster = {p.get("id"): p for p in snap["personas"]}
    if override and override in roster:
        persona = roster[override]
        log.info("persona pinned by settings: %s", override)
    else:
        persona = station.persona_from(snap["dj"], snap["personas"])

    persona_id = persona["id"]
    voice = str(cfg.get("tts_voice") or "").strip() or await station_cfg.voice_for(persona_id)

    instructions = await prompts.build_system_prompt(station, persona, snapshot=snap)

    # With overlap protection on, on-air actions are served by local wrappers
    # that check the broadcast is quiet first, so they come off the MCP list.
    guard_overlap = bool(cfg.get("avoid_on_air_overlap"))
    allowed_tools = build_allowed_tools(cfg, guarded=guard_overlap)
    local_tools = build_on_air_tools(cfg, station) if guard_overlap else []
    local_tools += build_library_tools(cfg, station)

    log.info(
        "call starting room=%s persona=%s (%s) llm=%s/%s tts=%s voice=%s tools=%d",
        ctx.room.name, persona["name"], persona_id,
        cfg["llm_provider"], cfg["llm_model"], cfg["tts_mode"], voice,
        len(allowed_tools) + len(local_tools),
    )

    # Several station tools (search_library among them) are admin-gated and
    # are rejected outright without credentials, which surfaces mid-call as the
    # DJ saying it's "locked out of the controls".
    mcp_headers = station_config_mod.mcp_headers()
    if not mcp_headers:
        log.warning(
            "no station admin credentials — admin-gated tools like "
            "subwave_search_library will be refused during the call"
        )

    station_tools = mcp.MCPServerHTTP(
        url=station_mcp_url(),
        transport_type="streamable_http",
        allowed_tools=allowed_tools,
        headers=mcp_headers or None,
        client_session_timeout_seconds=15,
    )

    idle_secs = int(cfg.get("idle_prompt_secs") or 0)

    session = AgentSession(
        stt=build_stt(cfg),
        llm=build_llm(cfg),
        tts=build_tts(cfg, voice),
        vad=ctx.proc.userdata["vad"],
        mcp_servers=[station_tools],
        tools=local_tools or NOT_GIVEN,
        preemptive_generation=True,
    )

    await session.start(
        agent=Agent(instructions=instructions),
        room=ctx.room,
        room_input_options=RoomInputOptions(close_on_disconnect=True),
    )

    # --- silence handling -------------------------------------------------
    # A caller who goes quiet gets checked on in character, then let go. Dead
    # air on a phone call is worse than a graceful goodbye, and an abandoned
    # tab would otherwise hold a line open until the hard time limit.
    #
    # Silence means NO DISCERNIBLE LANGUAGE, deliberately not "no sound":
    # the SDK's away-state rides the VAD, and background noise — a TV, the
    # station bleeding in, room hiss — kept resetting it, so the check-in
    # never fired in any real room. Only a transcript with actual words
    # counts as the caller being present; the clock starts each time the
    # DJ finishes talking (a caller quietly listening isn't idle).
    if idle_secs > 0:
        import time as _time

        max_nudges = int(cfg.get("idle_max_nudges") or 0)
        state = {"last_words": _time.time(), "nudges": 0}

        def _on_transcript(ev) -> None:
            text = str(getattr(ev, "transcript", "") or "")
            if text.strip():
                state["last_words"] = _time.time()
                state["nudges"] = 0

        def _on_agent_state(ev) -> None:
            # Count silence from the moment it's the caller's turn again.
            if getattr(ev, "new_state", None) == "listening":
                state["last_words"] = _time.time()

        session.on("user_input_transcribed", _on_transcript)
        session.on("agent_state_changed", _on_agent_state)

        async def _idle_watch() -> None:
            while True:
                await asyncio.sleep(1.0)
                if _time.time() - state["last_words"] < idle_secs:
                    continue
                if state["nudges"] >= max_nudges:
                    continue
                state["nudges"] += 1
                state["last_words"] = _time.time()
                first = state["nudges"] == 1
                log.info("no words from the caller for %ss — check-in %d/%d",
                         idle_secs, state["nudges"], max_nudges)
                try:
                    if first and max_nudges > 1:
                        await session.generate_reply(instructions=(
                            "The caller has gone quiet. Check they're still there — "
                            "one short line in your own voice, warm, no more than a "
                            "few words. Don't repeat yourself or start a new topic."
                        ))
                    else:
                        await session.generate_reply(instructions=(
                            "Still nothing from the caller. Say a brief goodbye in "
                            "character — you're letting them go and getting back to "
                            "the broadcast. One line, then stop."
                        ))
                        await asyncio.sleep(6)
                        ctx.shutdown(reason="caller went quiet")
                        return
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    log.warning("idle check-in failed: %s", e)

        idle_task = asyncio.create_task(_idle_watch())
        ctx.add_shutdown_callback(lambda: _cancel(idle_task))

    # Runs after the caller hangs up, so the station reflects the call.
    async def _on_shutdown() -> None:
        await release_call_slot(ctx.room.name)
        await send_on_air_callback(session, station, persona, cfg)

    ctx.add_shutdown_callback(_on_shutdown)

    # `max_call_seconds` was a declared setting that nothing enforced. Wind the
    # call up in character rather than cutting the audio dead.
    max_seconds = int(cfg.get("max_call_seconds") or 0)
    if max_seconds > 0:
        async def _end_when_over_time() -> None:
            try:
                await asyncio.sleep(max_seconds)
            except asyncio.CancelledError:
                # Normal hangup cancelled the timer. Returning here matters:
                # a `finally` that called ctx.shutdown() would fire on EVERY
                # call, re-entering shutdown and logging the wrong reason.
                return

            log.info("call hit the %ss limit — signing off", max_seconds)
            try:
                await session.generate_reply(
                    instructions=(
                        "You're out of time. Thank the caller warmly, in one short "
                        "line, and say goodbye. Do not ask a question."
                    )
                )
                await asyncio.sleep(6)  # let the sign-off actually play
            except asyncio.CancelledError:
                return
            except Exception as e:
                # The session may already be closing; end the call regardless
                # rather than leaving an unhandled task exception behind.
                log.warning("sign-off before the time limit failed: %s", e)

            ctx.shutdown(reason="call time limit reached")

        timeout_task = asyncio.create_task(_end_when_over_time())
        ctx.add_shutdown_callback(lambda: _cancel(timeout_task))

    # Both styles stay in persona and carry the show; the toggle is only
    # whether the DJ opens with an invitation or lets the caller lead.
    if str(cfg.get("greeting_style") or "inviting").lower() == "in-world":
        default_greeting = (
            "Pick up the call in character, mid-world — you were just on air. If "
            "something notable happened on the broadcast in the last little "
            "while, let it colour how you answer. One short line, the way a real "
            "DJ picks up mid-show. No question, no list of what you can do — "
            "just be there, and let them say why they called."
        )
    else:
        default_greeting = (
            "Pick up the call in character — you were just on air, and if "
            "something notable happened on the broadcast, let it colour the "
            "greeting. One short line, then invite them in with a single open "
            "question in your own voice: what's on their mind, or whether "
            "there's something they'd like to hear. One question, not a menu, "
            "and never a list of what you can do."
        )
    greeting = str(cfg.get("greeting") or "").strip() or default_greeting
    await session.generate_reply(instructions=greeting)


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()


async def release_call_slot(room: str) -> None:
    """Tell the token server this call is over so its concurrency slot frees
    immediately. The widget sends the same beacon, but a crashed tab never
    does — and the worker's shutdown always runs, so with the default limit of
    two concurrent calls, this is what stops dead sessions blocking real
    callers for the 30-minute age-out."""
    import httpx

    base = os.environ.get(
        "CALLIN_INTERNAL_URL",
        f"http://localhost:{os.environ.get('TOKEN_SERVER_PORT', '8100')}",
    )
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            await c.post(f"{base}/call-ended", json={"room": room})
    except Exception as e:
        log.debug("slot release beacon failed (harmless, will age out): %s", e)


def effective_tools(cfg: dict) -> dict:
    """What the caller's agent actually gets: the MCP allowlist plus the local
    wrappers. Previews that report only build_allowed_tools() show a list that
    is neither what MCP serves nor what the model sees."""
    guard = bool(cfg.get("avoid_on_air_overlap"))
    local = []
    if cfg.get("allow_library_search"):
        local.append("subwave_search_library (local: retry fallback)")
    if cfg.get("allow_requests"):
        local.append("subwave_request_song (local: pre-flight + status)")
    if guard:
        if cfg.get("allow_announcements"):
            local.append("subwave_dj_announce (local: waits for clear air)")
        if cfg.get("allow_skills"):
            local.append("subwave_run_skill (local: waits for clear air)")
    return {"mcp": build_allowed_tools(cfg, guarded=guard), "local": local}


def _transcript(session: AgentSession, limit: int = 24) -> list[tuple[str, str]]:
    """Flatten the call into (role, text) pairs, whatever shape the SDK's
    chat items happen to take."""
    turns: list[tuple[str, str]] = []
    try:
        items = list(session.history.items)
    except Exception:
        return turns

    for item in items[-limit:]:
        role = getattr(item, "role", None)
        if role not in ("user", "assistant"):
            continue
        content = getattr(item, "content", None)
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(c for c in content if isinstance(c, str))
        else:
            text = getattr(item, "text_content", "") or ""
        if text.strip():
            turns.append((role, text.strip()))
    return turns


async def send_on_air_callback(
    session: AgentSession, station: StationClient, persona: dict, cfg: dict
) -> None:
    """After the call, give the on-air DJ a passing mention of it.

    The point is continuity — a listener hears the same DJ refer to the call
    that just happened. It's deliberately one line: a mention, not a recap, and
    never a transcript. Nothing the caller said is repeated verbatim unless the
    DJ chooses to.
    """
    if not cfg.get("callback_enabled"):
        return

    turns = _transcript(session)
    caller_turns = sum(1 for role, _ in turns if role == "user")
    if caller_turns < int(cfg.get("callback_min_turns", 2)):
        log.info("skipping on-air handoff — only %d caller turn(s)", caller_turns)
        return

    max_words = int(cfg.get("callback_max_words", 30))
    extra = str(cfg.get("callback_instructions") or "").strip()

    convo = "\n".join(
        f"{'Caller' if role == 'user' else 'You'}: {text}" for role, text in turns
    )

    ask = (
        f"You are {persona.get('name', 'the DJ')}. The call just ended. Write ONE "
        f"line to say on air about it, under {max_words} words, in your own voice.\n\n"
        "Mention it the way a DJ passes over something between tracks — light, "
        "in character, moving on. Do not greet the audience, do not read out a "
        "summary, do not quote the caller word for word, and do not use their "
        "personal details beyond a first name. If they asked you something about "
        "yourself worth sharing, you may answer it briefly on air. If nothing "
        "about the call is worth mentioning, reply with exactly: SKIP\n"
    )
    if extra:
        ask += f"\nAlso: {extra}\n"
    ask += f"\nThe call:\n{convo}\n"

    try:
        from livekit.agents import llm as lk_llm

        ctx = lk_llm.ChatContext.empty()
        ctx.add_message(role="user", content=ask)

        # This runs during shutdown, when the session's own LLM may already be
        # tearing down. Fall back to a fresh client rather than losing the
        # handoff — it only gets one attempt per call.
        try:
            model = session.llm
            assert model is not None
        except Exception:
            model = build_llm(cfg)

        async def _compose() -> str:
            out = ""
            stream = model.chat(chat_ctx=ctx)
            async for chunk in stream:
                delta = getattr(chunk, "delta", None)
                if delta and getattr(delta, "content", None):
                    out += delta.content
            await stream.aclose()
            return out

        # Capped so a stalled provider can't eat the whole shutdown budget.
        text = await asyncio.wait_for(_compose(), timeout=25.0)
    except asyncio.TimeoutError:
        log.warning("on-air handoff compose timed out — skipping")
        return
    except Exception as e:
        log.warning("could not compose the on-air handoff: %s", e)
        return

    line = text.strip().strip('"')
    if not line or line.upper().startswith("SKIP"):
        log.info("on-air handoff skipped — nothing worth mentioning")
        return

    log.info("handing back to air: %s", line)
    # Fresh client: the session's StationClient may already be closed by an
    # earlier shutdown callback by the time this runs.
    fresh = StationClient()
    try:
        await fresh.dj_say(line, mode="styled", kind="callin")
    finally:
        await fresh.aclose()


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            # dev defaults to 0 idle processes, which means prewarm never runs
            # until a call arrives — so the first caller would sit through the
            # VAD and local-STT model load mid-conversation.
            num_idle_processes=1,
            # Loading the local STT model takes ~7s warm, and longer the first
            # time while it downloads. The 10s default kills the process
            # mid-load and the worker never becomes ready.
            initialize_process_timeout=180.0,
            # The back-to-air handoff (LLM compose + POST) runs during
            # shutdown; the 10s default could kill it mid-compose on a cold
            # model and the handoff only gets one chance per call.
            shutdown_process_timeout=60.0,
        )
    )
