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


def write_call_entry(room: str, persona: dict, cfg: dict, message: str,
                     receipt: str, started: float) -> None:
    """One voicemail, in the same archive the live calls use.

    The operator asked for this shape: messages appear alongside calls in
    Recent calls, labelled as the machine's — `kind: voicemail` is what the
    viewer renders as the label — while the messages list under Voicemail
    stays the working queue. Transcript only, like every record here: there
    is no audio to keep and never was.
    """
    if not cfg.get("record_calls") or not str(message or "").strip():
        return
    try:
        from call.record import CallRecord

        tier = {"o": "open", "g": "guest", "a": "admin"}.get(
            (room.split("-") + [""])[1][:1], "open")
        rec = CallRecord(room, persona, cfg, tier=tier, started=started)
        rec.data["kind"] = "voicemail"
        rec.turn("dj", "[answering machine] "
                 + greetings.greeting_text_for(persona.get("id") or "", cfg, "",
                                               persona.get("name") or "the DJ"))
        rec.turn("caller", message)
        rec.tool("voicemail_delivery", receipt)
        rec.write(reason="voicemail", keep=int(cfg.get("record_keep") or 0))
    except Exception as e:                                    # noqa: BLE001
        log.warning("could not write the voicemail's call entry: %s", e)

log = logging.getLogger("callin.voicemail")

# 20ms of mono 16-bit at whatever rate the clip declares.
_FRAME_MS = 20

# How long the line stays quiet after a FINAL transcript before the message
# is considered finished. Deliberately generous: a caller mid-thought is
# not a finished caller, and the ceiling is the real bound either way.
# 3.5s cut real messages off at the first thinking pause — the operator's
# own test voicemails arrived as their opening phrase and nothing else.
_SETTLE_SECS = 6.0


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


# A beep that runs longer than this is a jingle holding the line hostage —
# the caller is waiting to be told to speak.
_BEEP_MAX_SECS = 8.0


def _wav_as_mono16(path: Path, want_rate: int) -> bytes:
    """Any ordinary PCM WAV, converted to what the line plays: mono, 16-bit,
    `want_rate`. The first uploaded beep in the wild was a 44.1kHz file, and
    rejecting it for its rate produced the worst kind of failure — the tone
    played, nothing said why, and the setting looked ignored."""
    with wave.open(str(path), "rb") as w:
        ch, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        frames = w.readframes(min(w.getnframes(),
                                  int(rate * _BEEP_MAX_SECS)))
    if width == 2:
        samples = list(struct.unpack("<%dh" % (len(frames) // 2), frames))
    elif width == 1:                       # unsigned 8-bit
        samples = [(b - 128) << 8 for b in frames]
    elif width == 4:                       # 32-bit int PCM
        samples = [v >> 16 for v in
                   struct.unpack("<%di" % (len(frames) // 4), frames)]
    else:
        raise ValueError(f"{width * 8}-bit WAV is not PCM this can play")
    if ch > 1:
        samples = [sum(samples[i:i + ch]) // ch
                   for i in range(0, len(samples) - ch + 1, ch)]
    if rate != want_rate and samples:
        # Linear resample: good enough for a beep, no dependencies.
        n = int(len(samples) * want_rate / rate)
        out = []
        for i in range(n):
            pos = i * (len(samples) - 1) / max(1, n - 1)
            lo = int(pos)
            hi = min(lo + 1, len(samples) - 1)
            frac = pos - lo
            out.append(int(samples[lo] * (1 - frac) + samples[hi] * frac))
        samples = out
    return struct.pack("<%dh" % len(samples),
                       *[max(-32768, min(32767, s)) for s in samples])


def custom_beep(cfg: dict, want_rate: int) -> bytes | None:
    """The operator's own beep, when they uploaded one and it can play.

    Server-side sound: the worker beeps into the room, so only an uploaded
    file applies — there is no browser here to fetch a URL. Anything wave
    can't read (mp3 under a .wav name, compressed WAV) fails soft to the
    synthesized tone, never to silence."""
    name = str(cfg.get("sound_vm_beep") or "")
    try:
        if name.startswith("upload:"):
            from api.sounds import SOUNDS_DIR

            path = SOUNDS_DIR / name[len("upload:"):]
        elif name.startswith("/sound-lib/"):
            # A bundled library clip — WAV by policy, shipped in the image.
            import sounds as sound_assets

            path = sound_assets.library_dir() / name[len("/sound-lib/"):]
        else:
            return None
        return _wav_as_mono16(path, want_rate) or None
    except Exception as e:                                    # noqa: BLE001
        log.warning("voicemail beep %s unplayable (%s) — using the tone",
                    name, e)
        return None


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


async def _fresh_greeting(cfg: dict, persona: dict) -> tuple[bytes, int] | None:
    """One model line in persona, rendered through the configured voice.

    The line is asked for, not templated — that is the point of fresh mode —
    but the template is the fallback for a model that answers strangely, and
    the caller of this owns the clock.
    """
    from call.providers import build_llm, build_tts
    from station_config import StationConfig

    text = greetings.greeting_text_for(
        persona.get("id") or "", cfg, "", persona.get("name") or "the DJ")
    soul = str(persona.get("soul") or "").strip()
    if soul:
        try:
            from livekit.agents.llm import ChatContext

            llm = build_llm(cfg)
            chat_ctx = ChatContext()
            chat_ctx.add_message(role="user", content=(
                "You are this radio DJ:\n" + soul[:900] + "\n\n"
                "Write your answering-machine greeting for a caller the "
                "booth could not take live. One spoken line, under 25 words, "
                "in your own voice, ending by telling them to leave a "
                "message after the beep. The line only — no quotes."))
            chunks = []
            async with llm.chat(chat_ctx=chat_ctx) as st:
                async for chunk in st:
                    delta = getattr(chunk, "delta", None)
                    if delta and getattr(delta, "content", None):
                        chunks.append(delta.content)
            await llm.aclose()
            line = " ".join("".join(chunks).split())
            if 8 <= len(line) <= 300:
                text = line
        except Exception as e:                                # noqa: BLE001
            log.info("fresh greeting line fell back to the template: %s", e)

    sc = StationConfig(base_url=cfg.get("station_base_url"))
    try:
        voice = await sc.voice_for(persona.get("id") or "")
    finally:
        await sc.aclose()
    tts = build_tts(cfg, voice)
    pcm = bytearray()
    try:
        stream = tts.synthesize(text)
        async for ev in stream:
            pcm.extend(ev.frame.data.tobytes())
    finally:
        await tts.aclose()
    return (bytes(pcm), tts.sample_rate) if pcm else None


async def answer(ctx: JobContext) -> None:
    """One vm- room, start to finish."""
    answered_at = time.time()
    cfg = settings_store.load()
    ceiling = max(5, int(cfg.get("voicemail_max_seconds") or 30))

    station = StationClient()
    persona = {"id": "", "name": ""}
    try:
        persona = await station.resolve_live_persona()
    except Exception as e:                                    # noqa: BLE001
        log.warning("voicemail could not resolve the live persona: %s", e)

    # --- the recording ----------------------------------------------------
    # 'Fresh each call' writes a line in the persona's own voice at pickup —
    # a model line plus a TTS render, budgeted hard, because the design
    # reason staging exists is that fresh is slow. Anything short of success
    # inside the budget falls back to the staged clip, then the beep.
    pcm, rate = b"", 24000
    if str(cfg.get("voicemail_greeting_mode") or "staged") == "fresh":
        try:
            fresh = await asyncio.wait_for(
                _fresh_greeting(cfg, persona), timeout=6.0)
            if fresh:
                pcm, rate = fresh
        except Exception as e:                                # noqa: BLE001
            log.info("fresh greeting missed its budget (%s) — using the "
                     "staged clip", e)

    clip = None if pcm else greetings.staged_clip(persona.get("id") or "")
    if clip:
        try:
            pcm, rate = read_wav(clip)
        except Exception as e:                                # noqa: BLE001
            log.warning("staged clip %s unreadable (%s) — beep only",
                        clip.name, e)
    elif not pcm:
        # Only when there is truly nothing to play. A bare `else` here fired
        # this warning every time the FRESH greeting succeeded (pcm full,
        # clip deliberately None) — a log that says beep-only while a
        # greeting plays sent a whole diagnosis down the wrong road.
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
        await _play(source, custom_beep(cfg, rate) or beep_pcm(rate), rate)
    except Exception as e:                                    # noqa: BLE001
        log.warning("voicemail greeting playback failed: %s", e)

    # Tell the widget the beep has sounded, so it can hold the caller's mic
    # CLOSED until this moment — the machine should not be able to hear
    # anyone before it says it is listening. Fail-soft: an old widget just
    # ignores the topic and keeps its previous behaviour.
    try:
        await ctx.room.local_participant.publish_data(
            b"beep", reliable=True, topic="vm-beep")
    except Exception as e:                                    # noqa: BLE001
        log.info("could not signal the beep to the widget: %s", e)

    # --- one message, bounded ---------------------------------------------
    # The quiet clock restarts HERE. It used to start when STT was wired —
    # before the greeting — so by the time the beep finished, the 8-second
    # nobody-spoke window had already elapsed and the machine hung up almost
    # immediately after beeping. Operator-reported, from a real attempt.
    last_event.update(at=time.monotonic(), final=False)
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

    write_call_entry(ctx.room.name, persona, cfg, message, receipt,
                     answered_at)
    log.info("voicemail %s: %d words, %s", ctx.room.name,
             len(message.split()), receipt)
    try:
        await station.aclose()
    except Exception:                                         # noqa: BLE001
        pass
    # Delete the ROOM, not just the job — ctx.shutdown() alone leaves the
    # caller connected to an agent-less room with the mic hot and the timer
    # counting, which the operator read (correctly) as the 30-second ceiling
    # not being honored. Same close the live leg uses; the widget hears it
    # as a normal remote hangup.
    try:
        from livekit import api as lk_api

        await ctx.api.room.delete_room(
            lk_api.DeleteRoomRequest(room=ctx.room.name))
    except Exception as e:                                    # noqa: BLE001
        log.warning("voicemail room delete failed (%s) — agent still leaves", e)
    ctx.shutdown(reason="voicemail complete")
