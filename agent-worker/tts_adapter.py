"""
TTS for the call-in agent — one canonical call, translated at request time
into whatever shape the target backend expects, per a JSON adapter config.

SYNTHESIS lives here: AdapterTTS, the canonical-call to backend translation,
and the speech-filter cleaning chokepoint every spoken line passes through.
The adapter config and VOICE DISCOVERY (which voices a backend can actually
speak) are tts_voices.py, re-exported below so callers import from one place.

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
the response is raw PCM, or server-sent JSON events carrying base64 PCM
(`sse_json`, Gemini's Interactions API), audio is pushed to the emitter
chunk-by-chunk as it arrives rather than buffered, which is what keeps
time-to-first-audio low.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from livekit.agents import APIConnectionError, APIConnectOptions, tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr
from livekit.agents.utils import shortuuid

from log_setup import describe
from tts_pace import PaceMeter, seconds_of_pcm
from tts_voices import (  # noqa: F401 — re-exported; see the module docstring
    ADAPTER_DIR,
    _default_adapter_path,
    _is_openai_host,
    adapter_api_key,
    adapter_headers,
    available_voices,
    load_adapter,
    parse_voice_list,
    pick_speakable_voice,
    resolve_adapter,
)

log = logging.getLogger("callin.agent")


_ERROR_BODY_CHARS = 400


async def _backend_said(r: httpx.Response) -> str:
    """The backend's own words for refusing, trimmed to fit an error line.

    httpx renders HTTPStatusError as "Client error '400 Bad Request' for url
    ..." and stops there; the body never appears. That body is routinely the
    only actionable thing in the failure — a reference clip over Whisper's
    30-second ceiling, a voice the server does not have, a model name it does
    not know — and discarding it is why /test/tts grew hand-written guesses at
    what a 400 probably meant.

    On the streaming path the body has not been read when the status arrives,
    so it has to be pulled explicitly: reading .text first raises
    ResponseNotRead and loses the real error behind a second one.
    """
    if str(r.headers.get("content-type", "")).startswith("audio/"):
        return ""
    try:
        await r.aread()
        text = r.text.strip()
    except Exception:                                         # noqa: BLE001
        return ""
    if not text:
        return ""
    # A streamed refusal arrives as server-sent events — Gemini's
    # Interactions API answers a bad voice with `event: error` — and the
    # reason is the JSON on its data line.
    if text.startswith(("event:", "data:")):
        text = next((ln[5:].strip() for ln in text.splitlines()
                     if ln.startswith("data:")), text)

    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        for key in ("detail", "message", "error"):
            value = data.get(key)
            if isinstance(value, dict):
                value = value.get("message") or value.get("detail")
            if isinstance(value, str) and value.strip():
                text = value.strip()
                break

    return " ".join(text.split())[:_ERROR_BODY_CHARS]


async def _raise_for_status(r: httpx.Response) -> None:
    """raise_for_status, except the operator gets told what was actually said."""
    if r.status_code < 400:
        return
    said = await _backend_said(r)
    raise APIConnectionError(
        f"TTS backend returned HTTP {r.status_code} for {r.request.url}"
        + (f" — {said}" if said else "")
    )


def _put(body: dict, path: str, value) -> None:
    """Set `value` at a dotted path — `input.0.content.0.text` — building the
    dicts and lists on the way. A flat name is the old `body[key] = value`, so
    every adapter written before nesting reads exactly as it did. Gemini's
    Interactions API is why: its text and voice live four levels down."""
    keys = path.split(".")
    node = body
    for i, key in enumerate(keys):
        if isinstance(node, list):
            key = int(key)
            node.extend({} for _ in range(key + 1 - len(node)))
        if i == len(keys) - 1:
            node[key] = value
            return
        kind = list if keys[i + 1].isdigit() else dict
        child = node[key] if isinstance(node, list) else node.get(key)
        if not isinstance(child, kind):
            child = node[key] = kind()
        node = child


def _get(obj, path: str):
    """The value at a dotted path, or None — the reading half of _put."""
    for key in path.split("."):
        if isinstance(obj, list) and key.isdigit() and int(key) < len(obj):
            obj = obj[int(key)]
        elif isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return None
    return obj


async def _push_sse_audio(r: httpx.Response, spec: dict, emitter) -> int:
    """Audio out of a stream of server-sent JSON events, as Gemini's
    Interactions API sends it: each `step.delta` event carries a slice of raw
    PCM as base64 at `delta.data`. `match` names the fields an audio event has
    (dotted paths, as in request_field_map) and `field` is where its bytes are.

    An event carrying `error` is the backend refusing mid-stream. A stream that
    ends with no audio at all is a failure too: passed off as success, the turn
    would complete in silence and nothing would retry it."""
    match = spec.get("match") or {}
    produced = 0
    async for line in r.aiter_lines():
        payload = line[5:].strip() if line.startswith("data:") else ""
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("error"):
            err = event["error"]
            said = err.get("message") if isinstance(err, dict) else err
            raise APIConnectionError(f"TTS backend stopped mid-stream — {said}")
        if any(_get(event, k) != v for k, v in match.items()):
            continue
        data = _get(event, spec.get("field", "data"))
        if isinstance(data, str) and data:
            chunk = base64.b64decode(data)
            produced += len(chunk)
            emitter.push(chunk)
    if not produced:
        raise APIConnectionError("TTS backend streamed no audio")
    return produced


def riff_sample_rate(data: bytes) -> int | None:
    """The rate a WAV declares in its fmt chunk, or None if it isn't a WAV."""
    if len(data) < 16 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    i = 12
    while i + 8 <= len(data):
        chunk_id = data[i:i + 4]
        size = int.from_bytes(data[i + 4:i + 8], "little")
        if chunk_id == b"fmt " and i + 16 <= len(data):
            return int.from_bytes(data[i + 12:i + 16], "little")
        i += 8 + size + (size % 2)
    return None


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
        allow_stored_key: bool = True,
        mode: str = "",
    ) -> None:
        """`allow_stored_key=False` synthesizes without the operator's key.

        Set by the panel's test button when the base URL came from the request
        rather than from saved settings: a stored key is only ever sent to the
        host it is configured for.
        """
        self._adapter = load_adapter(adapter_path, mode=mode)
        audio = self._adapter["audio"]

        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=int(audio.get("sample_rate", 24000)),
            num_channels=int(audio.get("num_channels", 1)),
        )

        self._voice = voice
        self._model = model or self._adapter.get("default_model", "")
        # A model saved for another backend — "tts-1" left in the box after
        # switching to Gemini — is a 400 on every line. An adapter that names
        # its models speaks a stranger's with its own default instead.
        known = self._adapter.get("models")
        if model and isinstance(known, list) and known and model not in known:
            log.warning("tts model %r is not one this adapter offers — using %r",
                        model, self._adapter.get("default_model", ""))
            self._model = self._adapter.get("default_model", "")
        # How this backend has kept up, over the whole call. One AdapterTTS is
        # built per call, so this needs no key and cannot mix two callers.
        self._pace = PaceMeter()
        self._base_url = (
            base_url if base_url is not NOT_GIVEN else os.environ.get("TTS_BASE_URL", "")
        ).rstrip("/")
        if not self._base_url:
            raise ValueError("TTS_BASE_URL is not set and no base_url was passed")

        # adapter_api_key carries the whole rule, including the one the README
        # and the settings page both promise — a single OpenAI key covers cloud
        # TTS, matched on the HOST rather than as a substring, because
        # `in self._base_url` also matched https://api.openai.com.example.net
        # and would have handed the OpenAI key to whoever owns example.net.
        self._api_key = (
            api_key if api_key is not NOT_GIVEN
            else adapter_api_key(self._adapter, self._base_url, allow_stored_key)
        )

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
            _put(body, backend_key, canonical.get(canonical_key))
        for key, value in self._adapter.get("static_fields", {}).items():
            _put(body, key, value)
        return body

    def _headers(self) -> dict:
        return adapter_headers(self._adapter, self._api_key)

    def _endpoint(self) -> str:
        """The path to POST to, with `{voice}` filled in if the adapter uses it.

        Most speech APIs take the voice in the body. ElevenLabs takes it in the
        URL — `/v1/text-to-speech/{voice_id}` — and there is no body field that
        will do instead, so an adapter that can only describe a fixed path
        cannot describe that vendor at all. One substitution covers it, and
        covers every other server built the same way.
        """
        path = str(self._adapter["endpoint_path"])
        if "{voice}" not in path:
            return path
        return path.replace("{voice}", quote(self._voice or "", safe=""))

    async def probe_sample_rate(
        self, text: str = "Testing, one two three."
    ) -> tuple[int | None, str]:
        """What the backend ACTUALLY sampled at, versus what the adapter claims.

        The rate is a label attached to the samples, not something carried in
        them: declare 24000 for a backend producing 48000 and every line plays
        at half speed an octave down, with nothing anywhere raising an error.
        It is the one adapter mistake that is completely silent, and it is easy
        to make — the same build of a local engine commonly reports 48000 on a
        GPU and 24000 on a CPU, so the adapter that is right on one host is
        wrong on the next.

        Asking for wav instead of pcm settles it: the RIFF header states the
        rate, so this is a measurement rather than an inference from how fast
        the speech sounds. That inference is the obvious check and it is a trap
        — a persona written to speak in fast clipped fragments produces a
        fraction of the audio a normal voice does for the same text, and
        reasoning from it lands several octaves wrong with total confidence.

        Returns (rate, note). A None rate means the probe could not be done,
        never that the declared rate is wrong; `note` says which.
        """
        # Only backends whose adapter names the format field can be asked for
        # a different format, and the value tells us the field is understood.
        static = self._adapter.get("static_fields", {})
        field = next(
            (k for k, v in static.items()
             if isinstance(v, str) and v.lower() in ("pcm", "wav", "mp3", "opus", "flac")),
            "",
        )
        if not field:
            return None, "the adapter does not declare an audio format field to vary"

        body = self._build_body(text)
        body[field] = "wav"
        body.pop("stream", None)      # a streamed wav has no header to read yet

        try:
            r = await self._client.request(
                self._adapter["method"], self._endpoint(),
                json=body, headers=self._headers(),
            )
            await _raise_for_status(r)
        except Exception as e:                                # noqa: BLE001
            return None, f"the backend would not produce wav to measure ({e})"

        rate = riff_sample_rate(r.content)
        if rate is None:
            return None, f"asked for wav and got {len(r.content)} bytes that are not a wav"
        return rate, ""

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
            dash_style=str(cfg.get("tts_dash_style") or "pause"),
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

    def pace_report(self) -> str:
        """What the call record should say about how this backend kept up."""
        return self._pace.report()

    async def aclose(self) -> None:
        await self._client.aclose()


class AdapterChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: AdapterTTS, input_text: str,
                 conn_options: APIConnectOptions, silent: bool = False):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts_impl = tts
        self._silent = silent

    def _note_pace(self, bytes_out: int, wall: float) -> None:
        """Feed one synthesised line to the pace meter. See tts_pace."""
        impl = self._tts_impl
        # Only raw samples convert to seconds; see seconds_of_pcm.
        if str(impl._adapter["audio"].get("encoding", "")).lower() != "pcm":
            return
        impl._pace.note(
            wall, seconds_of_pcm(bytes_out, impl.sample_rate, impl.num_channels))

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
        streaming = bool(body.get("stream")) and resp_cfg["type"] in ("raw_audio", "sse_json")

        request_id = shortuuid()
        output_emitter.initialize(
            request_id=request_id,
            sample_rate=impl.sample_rate,
            num_channels=impl.num_channels,
            mime_type=mime,
            stream=streaming,
        )

        started, produced = time.monotonic(), 0
        try:
            if streaming:
                # In stream mode the emitter requires an explicit segment
                # around the pushed audio.
                output_emitter.start_segment(segment_id=request_id)
                async with impl._client.stream(
                    adapter["method"],
                    impl._endpoint(),
                    json=body,
                    headers=impl._headers(),
                ) as r:
                    await _raise_for_status(r)
                    if resp_cfg["type"] == "sse_json":
                        produced = await _push_sse_audio(r, resp_cfg, output_emitter)
                    else:
                        async for chunk in r.aiter_bytes():
                            if chunk:
                                produced += len(chunk)
                                output_emitter.push(chunk)
                output_emitter.end_segment()
                self._note_pace(produced, time.monotonic() - started)
                return

            r = await impl._client.request(
                adapter["method"],
                impl._endpoint(),
                json=body,
                headers=impl._headers(),
            )
            await _raise_for_status(r)

            kind = resp_cfg["type"]
            if kind == "raw_audio":
                produced = len(r.content)
                output_emitter.push(r.content)
            elif kind == "json_field":
                found = _get(r.json(), resp_cfg["field"])
                if not isinstance(found, str) or not found:
                    raise APIConnectionError(
                        f"TTS backend answered without audio at {resp_cfg['field']!r}")
                audio_bytes = base64.b64decode(found)
                produced = len(audio_bytes)
                output_emitter.push(audio_bytes)
            elif kind == "json_url":
                audio = await impl._client.get(r.json()[resp_cfg["field"]])
                await _raise_for_status(audio)
                produced = len(audio.content)
                output_emitter.push(audio.content)
            else:
                raise ValueError(f"unknown adapter response type: {kind}")

            output_emitter.flush()
            self._note_pace(produced, time.monotonic() - started)

        except httpx.HTTPError as e:
            raise APIConnectionError(f"TTS backend request failed: {describe(e)}") from e
