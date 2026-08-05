"""
TTS for the call-in agent — one canonical call, translated at request time
into whatever shape the target backend expects, per a JSON adapter config.

This is the v2 adapter design from BUILD-INSTRUCTIONS, implemented as a real
`livekit.agents.tts.TTS` subclass so it drops straight into an AgentSession.

Notes from probing the actual backends (2026-08-02):

  * The local VibeVoice server is ALREADY OpenAI-compatible
    (`POST /v1/audio/speech` taking `{model, input, voice,
    response_format, speed, stream}`). The `/speak` + `voice_id` contract
    guessed in the original `local-default.json` does not exist. So "local"
    and "cloud" are the same adapter shape with a different base URL.

  * VibeVoice generates at ~1.16x realtime with first audio at ~2.6s, so it
    CANNOT sustain a live call — playback starves. It is kept wired up here
    because it is the right voice for offline/on-air use and for testing,
    but a live call should point at a fast cloud endpoint. See README.

Streaming: when the adapter config sets `"stream": true` in static_fields and
the response is raw PCM, audio is pushed to the emitter chunk-by-chunk as it
arrives rather than buffered, which is what keeps time-to-first-audio low.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path

import httpx
from livekit.agents import APIConnectionError, APIConnectOptions, tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr
from livekit.agents.utils import shortuuid

log = logging.getLogger("callin.agent")

ADAPTER_DIR = Path(__file__).parent / "tts-adapters"


def _default_adapter_path() -> Path:
    explicit = os.environ.get("TTS_ADAPTER_CONFIG")
    if explicit:
        return Path(explicit)
    # Default matches settings.py ("cloud"); these disagreed previously, so a
    # caller that hadn't set TTS_MODE got the local adapter while the rest of
    # the app assumed cloud.
    mode = os.environ.get("TTS_MODE", "cloud").lower()
    return ADAPTER_DIR / ("local-vibevoice.json" if mode == "local" else "openai-cloud.json")


def load_adapter(path: str | Path | None = None) -> dict:
    p = Path(path) if path else _default_adapter_path()
    with open(p, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("method", "POST")
    cfg.setdefault("static_fields", {})
    cfg.setdefault("auth", {"type": "none"})
    cfg.setdefault("response", {"type": "raw_audio"})
    cfg.setdefault("audio", {"encoding": "pcm", "sample_rate": 24000, "num_channels": 1})
    return cfg


class AdapterTTS(tts.TTS):
    """A TTS backend described entirely by a JSON adapter config."""

    def __init__(
        self,
        *,
        voice: str,
        base_url: NotGivenOr[str] = NOT_GIVEN,
        api_key: NotGivenOr[str] = NOT_GIVEN,
        adapter_path: str | Path | None = None,
        model: str = "",
    ) -> None:
        self._adapter = load_adapter(adapter_path)
        audio = self._adapter["audio"]

        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=int(audio.get("sample_rate", 24000)),
            num_channels=int(audio.get("num_channels", 1)),
        )

        self._voice = voice
        self._model = model or self._adapter.get("default_model", "")
        self._base_url = (
            base_url if base_url is not NOT_GIVEN else os.environ.get("TTS_BASE_URL", "")
        ).rstrip("/")
        self._api_key = api_key if api_key is not NOT_GIVEN else os.environ.get("TTS_API_KEY", "")

        if not self._base_url:
            raise ValueError("TTS_BASE_URL is not set and no base_url was passed")

        # The README and the settings page both promise that a single OpenAI
        # key covers cloud TTS, and /test/env accepts it as satisfying the
        # requirement — so honour that here rather than 401ing on the
        # documented happy path. TTS_API_KEY still wins when set.
        if not self._api_key and "api.openai.com" in self._base_url:
            self._api_key = os.environ.get("OPENAI_API_KEY", "")

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(connect=5.0, read=180.0, write=10.0, pool=5.0),
        )

    @property
    def voice(self) -> str:
        return self._voice

    def update_voice(self, voice: str) -> None:
        """Swap voice between calls without rebuilding the session."""
        self._voice = voice

    def _build_body(self, text: str) -> dict:
        canonical = {"text": text, "voice": self._voice, "model": self._model}
        body: dict = {}
        for canonical_key, backend_key in self._adapter.get("request_field_map", {}).items():
            if backend_key is None:
                continue
            body[backend_key] = canonical.get(canonical_key)
        body.update(self._adapter.get("static_fields", {}))
        return body

    def _headers(self) -> dict:
        auth = self._adapter.get("auth", {"type": "none"})
        kind = auth.get("type", "none")
        if kind == "bearer" and self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        if kind == "header" and self._api_key:
            return {auth.get("header_name", "X-API-Key"): self._api_key}
        return {}

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> "AdapterChunkedStream":
        # Everything spoken passes through here, whatever the provider — so
        # this is the one place that can guarantee stage directions and
        # expletives never reach the speaker.
        import settings as settings_store
        from speech_filter import DEFAULT_PROFANITY, clean_for_speech

        cfg = settings_store.load()
        custom = str(cfg.get("profanity_words") or "").strip()
        words = (
            [w.strip() for w in custom.split(",") if w.strip()]
            if custom else DEFAULT_PROFANITY
        )

        spoken = clean_for_speech(
            text,
            strip_directions=bool(cfg.get("strip_stage_directions", True)),
            profanity_mode=str(cfg.get("profanity_mode", "mask")),
            profanity_words=words,
        )
        # Cleaning can empty a line completely — a model that answers with
        # nothing but "*shuffles records*" leaves an empty string once stage
        # directions are stripped, which is the correct result. Sending it on
        # is not: a TTS backend asked to say nothing errors, the agent retries
        # the same empty text until it gives up, and the caller hears the
        # dead-air fallback instead of the DJ. Observed on a real call — four
        # 500s in four seconds, all with an empty body.
        if not spoken.strip():
            log.info("nothing left to say after cleaning %r — not calling TTS", text[:60])
            return AdapterChunkedStream(
                tts=self, input_text="", conn_options=conn_options, silent=True)
        return AdapterChunkedStream(tts=self, input_text=spoken, conn_options=conn_options)

    async def aclose(self) -> None:
        await self._client.aclose()


class AdapterChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: AdapterTTS, input_text: str,
                 conn_options: APIConnectOptions, silent: bool = False):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts_impl = tts
        self._silent = silent

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        impl = self._tts_impl
        if self._silent:
            # An empty, well-formed segment. The session gets a normal
            # "finished speaking" rather than an error, so the turn completes
            # and the DJ carries on listening.
            output_emitter.initialize(
                request_id=shortuuid(),
                sample_rate=impl.sample_rate,
                num_channels=impl.num_channels,
                mime_type=impl._adapter["audio"].get("mime_type", "audio/pcm"),
                stream=False,
            )
            return
        adapter = impl._adapter
        body = impl._build_body(self.input_text)
        resp_cfg = adapter["response"]
        audio_cfg = adapter["audio"]
        mime = audio_cfg.get("mime_type", "audio/pcm")
        streaming = bool(body.get("stream")) and resp_cfg["type"] == "raw_audio"

        request_id = shortuuid()
        output_emitter.initialize(
            request_id=request_id,
            sample_rate=impl.sample_rate,
            num_channels=impl.num_channels,
            mime_type=mime,
            stream=streaming,
        )

        try:
            if streaming:
                # In stream mode the emitter requires an explicit segment
                # around the pushed audio.
                output_emitter.start_segment(segment_id=request_id)
                async with impl._client.stream(
                    adapter["method"],
                    adapter["endpoint_path"],
                    json=body,
                    headers=impl._headers(),
                ) as r:
                    r.raise_for_status()
                    async for chunk in r.aiter_bytes():
                        if chunk:
                            output_emitter.push(chunk)
                output_emitter.end_segment()
                return

            r = await impl._client.request(
                adapter["method"],
                adapter["endpoint_path"],
                json=body,
                headers=impl._headers(),
            )
            r.raise_for_status()

            kind = resp_cfg["type"]
            if kind == "raw_audio":
                output_emitter.push(r.content)
            elif kind == "json_field":
                output_emitter.push(base64.b64decode(r.json()[resp_cfg["field"]]))
            elif kind == "json_url":
                audio = await impl._client.get(r.json()[resp_cfg["field"]])
                audio.raise_for_status()
                output_emitter.push(audio.content)
            else:
                raise ValueError(f"unknown adapter response type: {kind}")

            output_emitter.flush()

        except httpx.HTTPError as e:
            raise APIConnectionError(f"TTS backend request failed: {e}") from e
