"""The voicemail leg the worker runs: greeting, beep, one utterance, deliver.

No agent, no LLM, no tools — an STT session with a recording played in front
of it. Everything here is bounded: the greeting is a staged file, the beep is
maths, and the STT runs for at most `voicemail_max_seconds`.

Every stage fails soft toward the caller still being answered: a missing clip
means beep-only ("a missing clip must never mean a silent pickup"), a dead
STT means the ring is still logged, and delivery failing holds the message
for the operator rather than losing it.
"""

from __future__ import annotations

import asyncio
import logging
import math
import struct
import time
import wave
from pathlib import Path

from livekit import rtc
from livekit.agents import JobContext, stt as lk_stt

import settings as settings_store
from call.providers import build_stt
from station import StationClient
from voicemail import deliver as vm_deliver
from voicemail import greetings

log = logging.getLogger("callin.voicemail")

# 20ms of mono 16-bit at whatever rate the clip declares.
_FRAME_MS = 20

# How long the line stays quiet after a FINAL transcript before the message
# is considered finished. Deliberately generous: a caller mid-thought is
# not a finished caller, and 30s is the real ceiling either way.
_SETTLE_SECS = 3.5


def beep_pcm(sample_rate: int, freq: int = 1000, ms: int = 400,
             gain: float = 0.28) -> bytes:
    """The answering-machine beep — a shaped sine, no file anywhere."""
    n = int(sample_rate * ms / 1000)
    fade = max(1, int(sample_rate * 0.012))
    out = bytearray()
    for i in range(n):
        amp = gain
        if i < fade:
            amp *= i / fade
        elif i > n - fade:
            amp *= (n - i) / fade
        out += struct.pack("<h", int(32767 * amp * math.sin(
            2 * math.pi * freq * i / sample_rate)))
    return bytes(out)


def read_wav(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path.name} is not mono 16-bit")
        return w.readframes(w.getnframes()), w.getframerate()


async def _play(source: rtc.AudioSource, pcm: bytes, sample_rate: int) -> None:
    samples = int(sample_rate * _FRAME_MS / 1000)
    step = samples * 2
    for i in range(0, len(pcm) - step + 1, step):
        frame = rtc.AudioFrame(
            data=pcm[i:i + step], sample_rate=sample_rate,
            num_channels=1, samples_per_channel=samples,
        )
        await source.capture_frame(frame)


async def answer(ctx: JobContext) -> None:
    """One vm- room, start to finish."""
    cfg = settings_store.load()
    ceiling = max(5, int(cfg.get("voicemail_max_seconds") or 30))

    station = StationClient()
    persona = {"id": "", "name": ""}
    try:
        persona = await station.resolve_live_persona()
    except Exception as e:                                    # noqa: BLE001
        log.warning("voicemail could not resolve the live persona: %s", e)

    # --- the recording ----------------------------------------------------
    clip = greetings.staged_clip(persona.get("id") or "")
    pcm, rate = b"", 24000
    if clip:
        try:
            pcm, rate = read_wav(clip)
        except Exception as e:                                # noqa: BLE001
            log.warning("staged clip %s unreadable (%s) — beep only",
                        clip.name, e)
    else:
        log.warning("no staged voicemail greeting — answering with the beep "
                    "alone. Stage greetings from the panel's Voicemail section.")

    source = rtc.AudioSource(rate, 1)
    track = rtc.LocalAudioTrack.create_audio_track("vm-voice", source)
    await ctx.room.local_participant.publish_track(track)

    # --- the caller's audio into STT, from the moment we answer -----------
    # Wired BEFORE the greeting plays: a caller who knows the drill talks
    # over the machine, and losing their first words to the recording is the
    # answering-machine failure everyone has met.
    heard: list[str] = []
    last_event = {"at": time.monotonic(), "final": False}

    base_stt = build_stt(cfg)
    if not base_stt.capabilities.streaming:
        vad = (ctx.proc.userdata or {}).get("vad")
        base_stt = lk_stt.StreamAdapter(stt=base_stt, vad=vad)
    stt_stream = base_stt.stream()

    async def _pump_events() -> None:
        async for ev in stt_stream:
            if ev.type == lk_stt.SpeechEventType.FINAL_TRANSCRIPT:
                text = (ev.alternatives[0].text or "").strip() if ev.alternatives else ""
                if text:
                    heard.append(text)
                last_event.update(at=time.monotonic(), final=True)
            elif ev.type == lk_stt.SpeechEventType.INTERIM_TRANSCRIPT:
                last_event.update(at=time.monotonic())

    async def _pump_audio(remote: rtc.RemoteAudioTrack) -> None:
        async for frame_ev in rtc.AudioStream(remote):
            stt_stream.push_frame(frame_ev.frame)

    audio_pump: list[asyncio.Task] = []

    def _on_track(track_in, *_a) -> None:
        if isinstance(track_in, rtc.RemoteAudioTrack) and not audio_pump:
            audio_pump.append(asyncio.create_task(_pump_audio(track_in)))

    ctx.room.on("track_subscribed", _on_track)
    for p in ctx.room.remote_participants.values():
        for pub in p.track_publications.values():
            if pub.track and isinstance(pub.track, rtc.RemoteAudioTrack):
                _on_track(pub.track)

    events = asyncio.create_task(_pump_events())

    # --- greeting, then the beep ------------------------------------------
    try:
        if pcm:
            await _play(source, pcm, rate)
        await asyncio.sleep(0.25)
        await _play(source, beep_pcm(rate), rate)
    except Exception as e:                                    # noqa: BLE001
        log.warning("voicemail greeting playback failed: %s", e)

    # --- one message, bounded ---------------------------------------------
    started = time.monotonic()
    try:
        while True:
            await asyncio.sleep(0.25)
            if time.monotonic() - started >= ceiling:
                break
            quiet = time.monotonic() - last_event["at"]
            if heard and last_event["final"] and quiet >= _SETTLE_SECS:
                break
            # Nobody has said anything at all: give them a moment past the
            # beep, then stop holding an open line.
            if not heard and quiet >= max(8.0, _SETTLE_SECS * 2):
                break
    finally:
        for task in audio_pump:
            task.cancel()
        events.cancel()
        try:
            await stt_stream.aclose()
        except Exception:                                     # noqa: BLE001
            pass

    message = " ".join(heard).strip()

    # --- the acknowledgement, then down goes the receiver ------------------
    receipt = "nothing said — nothing delivered"
    if message:
        try:
            receipt = await vm_deliver.deliver(station, cfg, message,
                                               persona.get("name") or "")
        except Exception as e:                                # noqa: BLE001
            vm_deliver.hold(message, persona.get("name") or "",
                            note=f"delivery crashed: {e}")
            receipt = "held for the operator"
        ack = greetings.ack_clip(persona.get("id") or "")
        try:
            if ack and ack.is_file():
                a_pcm, a_rate = read_wav(ack)
                if a_rate == rate:
                    await _play(source, a_pcm, a_rate)
            else:
                await _play(source, beep_pcm(rate, freq=740, ms=180), rate)
                await _play(source, beep_pcm(rate, freq=880, ms=180), rate)
        except Exception:                                     # noqa: BLE001
            pass

    log.info("voicemail %s: %d words, %s", ctx.room.name,
             len(message.split()), receipt)
    try:
        await station.aclose()
    except Exception:                                         # noqa: BLE001
        pass
    ctx.shutdown(reason="voicemail complete")
