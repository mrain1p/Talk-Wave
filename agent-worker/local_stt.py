"""
Speech-to-text that runs inside the worker process. No container, no API key,
no network.

Why this exists: the cloud options each carry a cost you may not want — an API
key and per-minute billing (Deepgram, OpenAI), or a full GCP service account
(Google). This runs faster-whisper (CTranslate2) in-process on CPU, which
matters here specifically because the GPU is already fully committed to
VibeVoice — anything asking for VRAM would make both slower.

How it fits LiveKit: this is a *non-streaming* recogniser. The Agents SDK
wraps it in a StreamAdapter driven by the Silero VAD the worker already loads,
so speech is chunked at natural pauses and each chunk transcribed. That means
no interim results — the caption appears when you finish a sentence rather
than word by word — which is the honest tradeoff for having no dependencies.

Model sizing on CPU (int8), roughly:
    tiny.en   ~75MB   fastest, noticeably weaker on names and accents
    base.en   ~145MB  the sensible default for a phone-quality call
    small.en  ~480MB  better, and usually still under realtime
The model downloads once on first use and is cached under ~/.cache.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    stt,
    utils,
)
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr

log = logging.getLogger("callin.local_stt")

# One model per (name, compute type), shared by every call in this process.
# Loading is slow enough that doing it per call would be audible.
_models: dict[tuple[str, str], Any] = {}
_load_lock = asyncio.Lock()


async def _get_model(model: str, compute_type: str, threads: int):
    key = (model, compute_type)
    async with _load_lock:
        if key not in _models:
            from faster_whisper import WhisperModel

            log.info("loading local STT model %s (%s) — first run downloads it", model, compute_type)
            # Loading blocks; keep it off the event loop so the call doesn't stall.
            _models[key] = await asyncio.to_thread(
                WhisperModel,
                model,
                device="cpu",
                compute_type=compute_type,
                cpu_threads=threads,
            )
            log.info("local STT model %s ready", model)
    return _models[key]


def preload_sync(model: str = "base.en", compute_type: str = "int8", threads: int = 4) -> None:
    """Blocking load for the worker's prewarm hook, which runs before any call
    is dispatched. Without this the first caller waits ~7s for the model."""
    key = (model, compute_type)
    if key in _models:
        return
    from faster_whisper import WhisperModel

    log.info("prewarming local STT model %s", model)
    _models[key] = WhisperModel(
        model, device="cpu", compute_type=compute_type, cpu_threads=threads
    )
    log.info("local STT model %s ready", model)


class LocalWhisperSTT(stt.STT):
    """faster-whisper behind LiveKit's STT interface."""

    def __init__(
        self,
        *,
        model: str = "base.en",
        language: str = "en",
        compute_type: str = "int8",
        cpu_threads: int = 4,
        beam_size: int = 1,
    ) -> None:
        # streaming=False so the SDK wraps this with a VAD-driven StreamAdapter.
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=False, interim_results=False)
        )
        self._model_name = model
        self._language = language
        self._compute_type = compute_type
        self._cpu_threads = cpu_threads
        # beam_size=1 (greedy) is markedly faster and the accuracy difference
        # on short conversational turns is small.
        self._beam_size = beam_size

    @property
    def model(self) -> str:
        return self._model_name

    def prewarm(self) -> None:
        """Load the model before the first caller rather than during their
        turn. Synchronous on purpose: the Agents SDK calls `stt.prewarm()`
        without awaiting, so an async version silently never ran
        ("coroutine was never awaited" in the worker logs)."""
        preload_sync(self._model_name, self._compute_type, self._cpu_threads)

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        import numpy as np

        try:
            model = await _get_model(
                self._model_name, self._compute_type, self._cpu_threads
            )

            frame = utils.audio.combine_frames(buffer)

            # Downmix to mono first if needed, as int16.
            raw = np.frombuffer(frame.data, dtype=np.int16)
            if frame.num_channels > 1:
                raw = (
                    raw.reshape(-1, frame.num_channels)
                    .mean(axis=1)
                    .astype(np.int16)
                )

            if frame.sample_rate != 16000:
                # A naive np.interp here aliases everything above 8kHz down
                # into the speech band, which costs accuracy on exactly the
                # consonants that distinguish words. The SDK's resampler
                # filters properly.
                from livekit import rtc

                mono = rtc.AudioFrame(
                    data=raw.tobytes(),
                    sample_rate=frame.sample_rate,
                    num_channels=1,
                    samples_per_channel=len(raw),
                )
                resampler = rtc.AudioResampler(frame.sample_rate, 16000, num_channels=1)
                pieces = resampler.push(mono)
                pieces += resampler.flush()
                data = b"".join(bytes(p.data) for p in pieces)
                samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            else:
                samples = raw.astype(np.float32) / 32768.0

            lang = language if language is not NOT_GIVEN else self._language

            def run() -> str:
                segments, _info = model.transcribe(
                    samples,
                    language=lang or None,
                    beam_size=self._beam_size,
                    vad_filter=False,   # the SDK's VAD already segmented this
                    condition_on_previous_text=False,
                )
                return " ".join(s.text.strip() for s in segments).strip()

            text = await asyncio.to_thread(run)

        except Exception as e:
            raise APIConnectionError(f"local STT failed: {e}") from e

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(language=lang or "en", text=text)],
        )
