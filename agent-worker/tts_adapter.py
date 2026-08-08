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
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from livekit.agents import APIConnectionError, APIConnectOptions, tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr
from livekit.agents.utils import shortuuid

from log_setup import describe
from tts_pace import PaceMeter, seconds_of_pcm

log = logging.getLogger("callin.agent")

ADAPTER_DIR = Path(__file__).parent / "tts-adapters"


def _default_adapter_path(mode: str = "") -> Path:
    """The adapter to use when none was named.

    `mode` is passed in now rather than read back out of os.environ. Four
    different places used to write os.environ["TTS_MODE"] purely so this line
    could read it — a setting laundered through process-global state with no
    owner, and in the token server that state is shared by every concurrent
    request, so two operators testing different backends raced each other.
    The environment remains the fallback for a worker that has not been told.
    """
    explicit = os.environ.get("TTS_ADAPTER_CONFIG")
    if explicit:
        return Path(explicit)
    # Default matches settings.py ("cloud"); these disagreed previously, so a
    # caller that hadn't set TTS_MODE got the local adapter while the rest of
    # the app assumed cloud.
    mode = (mode or os.environ.get("TTS_MODE", "cloud")).lower()
    return ADAPTER_DIR / ("local-vibevoice.json" if mode == "local" else "openai-cloud.json")


def _is_openai_host(base_url: str) -> bool:
    """Is this URL actually OpenAI's own API, rather than something whose
    hostname merely contains that string?"""
    from urllib.parse import urlparse

    host = (urlparse(str(base_url or "")).hostname or "").lower()
    return host == "api.openai.com" or host.endswith(".api.openai.com")


def adapter_api_key(adapter: dict, base_url: str = "", allow_stored: bool = True) -> str:
    """The key this backend wants, from the environment.

    Most adapters describe an OpenAI-shaped endpoint and take TTS_API_KEY (or
    the OpenAI key, on an OpenAI host — the README promises one key covers
    cloud TTS). A vendor with a key of its own says so with `auth.key_env`,
    which is what lets ElevenLabs sit beside the generic adapters instead of
    needing the operator to paste the same key into TTS_API_KEY and lose the
    ability to use both.
    """
    if not allow_stored:
        return ""
    auth = adapter.get("auth") or {}
    named = str(auth.get("key_env") or "").strip()
    if named:
        return os.environ.get(named, "")
    key = os.environ.get("TTS_API_KEY", "")
    if not key and _is_openai_host(base_url):
        key = os.environ.get("OPENAI_API_KEY", "")
    return key


def adapter_headers(adapter: dict, api_key: str) -> dict:
    auth = adapter.get("auth", {"type": "none"})
    kind = auth.get("type", "none")
    if not api_key:
        return {}
    if kind == "bearer":
        return {"Authorization": f"Bearer {api_key}"}
    if kind == "header":
        return {auth.get("header_name", "X-API-Key"): api_key}
    return {}


def parse_voice_list(data: object, prefer: str = "") -> list[str]:
    """Voice ids out of whatever shape the backend answered with.

    `prefer` names the field that IS the id when a backend's catalogue carries
    both an id and a display name. ElevenLabs is the case: its entries are
    `{voice_id, name, ...}`, only voice_id is addressable, and the default
    order below would pick `name` and hand the caller a list of labels that
    every synthesis request then 404s on.

    There is no standard here, and at least four shapes are in the wild:
    OpenAI's {"data": [{"id": ...}]}, a bare ["name", ...], {"voices": [...]}
    with either dicts or strings inside, and a mapping of id -> details.

    Reading only the first is worse than it sounds. An empty list means "could
    not find out" everywhere in this file, so a backend that answers its voice
    list perfectly well in the wrong shape does not read as "unknown voices" —
    it silently disables pick_speakable_voice, the panel's dropdown falls back
    to stock OpenAI names, and the station's voice goes to a backend that
    never had it. Tolerating the shapes costs nothing and means most new
    backends need no adapter entry at all.
    """
    if isinstance(data, dict):
        for key in ("data", "voices", "results", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            # A mapping of id -> details. Every value being a dict is what
            # distinguishes it from an error envelope like {"detail": "..."},
            # which would otherwise offer "detail" as a voice.
            if data and all(isinstance(v, dict) for v in data.values()):
                data = list(data.keys())

    if not isinstance(data, list):
        return []

    found: set[str] = set()
    for item in data:
        if isinstance(item, str):
            if item.strip():
                found.add(item.strip())
        elif isinstance(item, dict):
            for key in ([prefer] if prefer else []) + ["id", "name", "voice", "voice_id"]:
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    found.add(value.strip())
                    break
    return sorted(found)


async def available_voices(
    base_url: str,
    timeout: float = 6.0,
    adapter_path: str | Path | None = None,
    mode: str = "",
) -> list[str]:
    """What the TTS backend at `base_url` says it can actually speak in.

    An empty list means "could not find out" and never "has none" — the caller
    must treat those differently, because refusing to speak on a failed lookup
    would turn a slow TTS server into a silent call.

    Lives here rather than in token_server because the WORKER needs it too:
    the panel showing a voice list the worker never consults is how a call
    ends up trying a voice the backend does not have.

    The path comes from the adapter, because discovery is as backend-specific
    as synthesis and this file only ever described the second half. A backend
    that serves its list at /voices rather than /v1/audio/voices looked
    identical to one that was down.
    """
    if not base_url:
        return []
    # A trailing slash here produced `http://host:8001//v1/audio/voices`, which
    # some servers route and some 404 — so whether the panel could list voices
    # at all depended on a character nobody could see. AdapterTTS already
    # strips it; this was the one path that didn't.
    base_url = base_url.rstrip("/")
    path = "/v1/audio/voices"
    headers: dict = {}
    prefer = ""
    try:
        adapter = load_adapter(adapter_path, mode=mode)
        path = str(adapter.get("voices_path") or path)
        prefer = str(adapter.get("voices_id_field") or "")
        # Authenticated, because some catalogues are. ElevenLabs answers
        # /v1/voices with a 401 and no body without xi-api-key, and an empty
        # list here means "could not find out" — so the panel would have shown
        # eleven stock OpenAI voice names for a backend that has none of them,
        # and the first call would have failed on a voice that never existed.
        headers = adapter_headers(adapter, adapter_api_key(adapter, base_url))
    except Exception as e:                                    # noqa: BLE001
        # An unreadable adapter is the caller's problem to report, not a
        # reason to skip the lookup with the default path.
        log.info("adapter unreadable for voice discovery (%s)", describe(e))
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as c:
            r = await c.get(path, headers=headers)
            r.raise_for_status()
            return parse_voice_list(r.json(), prefer=prefer)
    except Exception as e:                                    # noqa: BLE001
        log.info("voice list unavailable from %s%s (%s)", base_url, path, e)
        return []


def pick_speakable_voice(wanted: str, available: list[str]) -> tuple[str, str]:
    """(voice to use, why it changed). An empty reason means it did not.

    The station tells us which voice each DJ uses ON AIR, and mirroring that is
    right — the call-in DJ should sound like the one broadcasting. But the
    station's voice belongs to the station's TTS, and this service may be
    pointed at a different one. Rosie's station voice is an ElevenLabs id;
    against local VibeVoice every request 400s, so the DJ generated a perfectly
    good greeting and the caller heard silence for the whole call. Even the
    dead-air fallback was mute, because it speaks through the same backend.

    A voice the backend does not have is therefore not a reason to say nothing.
    It is a reason to say it in a different voice and to write down why.
    """
    wanted = str(wanted or "").strip()
    if not available:
        return wanted, ""            # lookup failed — not evidence of anything
    if wanted and wanted in available:
        return wanted, ""
    fallback = available[0]
    if not wanted:
        return fallback, ""          # nothing asked for; nothing surprising
    return fallback, (
        f"The station uses voice {wanted!r} for this DJ, and the TTS backend "
        f"does not have it — speaking as {fallback!r} instead. Every line would "
        f"otherwise have failed and the caller would have heard nothing. Set "
        f"Voice under Models & voice to choose deliberately, or point this at "
        f"the TTS server the station itself uses."
    )


def resolve_adapter(value: str | None) -> str | None:
    """The adapter file a setting names, constrained to ADAPTER_DIR.

    `tts_adapter` reaches this from saved settings *and* from the body of
    /test/tts and /test/speed, which is a request. The resolution used to be
    the same three lines copied into three modules, and all three read "join
    it to ADAPTER_DIR unless it is absolute" — so an absolute path went
    straight to open(), and a relative one with ../ in it walked out of the
    directory before the exists() check ever looked. A request could name any
    file on the disk and learn whether it existed and whether it parsed as
    JSON, which in first-run mode needs no password at all.

    Same shape as _safe_sound_name: one flat directory, a known extension,
    nothing that can point elsewhere. The panel only ever offers a filename
    out of ADAPTER_DIR.glob("*.json"), so nothing legitimate is lost.

    The one exception is TTS_ADAPTER_CONFIG. That is set at deploy time by
    whoever runs the container, not by a request, and pointing it at a mounted
    file outside the image is a supported thing to do — so an absolute path is
    honoured when it is *exactly* that value and never otherwise.
    """
    name = str(value or "").strip()
    if not name:
        return None

    from_env = str(os.environ.get("TTS_ADAPTER_CONFIG") or "").strip()
    if from_env and name == from_env:
        return name

    if name != Path(name).name or not name.lower().endswith(".json"):
        log.warning(
            "ignoring tts adapter %r — it must be a .json filename in %s, "
            "with no path in it", name, ADAPTER_DIR,
        )
        return None
    candidate = ADAPTER_DIR / name
    try:
        if candidate.resolve().parent != ADAPTER_DIR.resolve():
            return None
    except OSError:
        return None
    return str(candidate) if candidate.is_file() else None


def load_adapter(path: str | Path | None = None, mode: str = "") -> dict:
    p = Path(path) if path else _default_adapter_path(mode)
    with open(p, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("method", "POST")
    cfg.setdefault("static_fields", {})
    cfg.setdefault("auth", {"type": "none"})
    cfg.setdefault("response", {"type": "raw_audio"})
    cfg.setdefault("audio", {"encoding": "pcm", "sample_rate": 24000, "num_channels": 1})
    cfg.setdefault("voices_path", "/v1/audio/voices")
    return cfg


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
            body[backend_key] = canonical.get(canonical_key)
        body.update(self._adapter.get("static_fields", {}))
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
        streaming = bool(body.get("stream")) and resp_cfg["type"] == "raw_audio"

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
                audio_bytes = base64.b64decode(r.json()[resp_cfg["field"]])
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
