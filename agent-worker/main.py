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

import logging
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import JobContext, JobProcess, WorkerOptions, cli
# Imported at module scope on purpose: LiveKit registers plugins at import
# time and requires that to happen on the main thread. `call.providers` pulls
# in the rest for the same reason.
from livekit.plugins import silero

import secrets_store
import settings as settings_store
from call.session import CallSession

load_dotenv(Path(__file__).parent.parent / ".env")

import log_setup

# console=False: livekit's cli.run_app adds its own stdout handler to the root
# logger, so ours would be a second sink and every line appeared twice.
log_setup.setup("worker", console=False)
log = logging.getLogger("callin.agent")

from version import APP_VERSION

# The worker and the token server are the same image but separate containers,
# so a redeploy that recreates one and not the other leaves them skewed. That
# has happened, and it was invisible because only the token server ever said
# what it was.
log.info("wave-talk worker %s starting", APP_VERSION)

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
    """One job = one call. Everything about the call itself lives on
    CallSession; this only decides whether to answer at all."""
    await ctx.connect()

    # Media-path probes from the pipeline check are real rooms, but answering
    # one with a full agent session would spend an LLM+TTS round on nothing.
    if ctx.room.name.startswith("probe-"):
        log.info("media-path probe %s — not starting an agent session", ctx.room.name)
        return

    # Keys entered in the settings page live in their own store; push them into
    # the environment before building providers, since the SDKs read env.
    secrets_store.apply_to_env()

    call = CallSession(ctx)
    await call.prepare()    # the caller hears this as ringing
    await call.start()      # the DJ is on the line
    await call.greet()


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
