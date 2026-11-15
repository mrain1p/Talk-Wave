"""Which engine listens, thinks and speaks.

One module for the three provider choices, because they share a hazard: a
model name is provider-specific and must never survive a provider switch.
Carrying one across produces a 404 on every single utterance — Deepgram's
"nova-3" sent to OpenAI — which sounds exactly like the caller not being heard.

Plugins are imported at module scope on purpose. LiveKit registers them at
import time and requires that to happen on the main thread; importing them
lazily inside the builders raises "Plugins must be registered on the main
thread" once a job is running.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from pathlib import Path

from livekit.agents import llm as lk_llm
from livekit.agents._exceptions import (
    APIConnectionError, APIStatusError, APITimeoutError,
)
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, APIConnectOptions,
)
from livekit.plugins import anthropic, deepgram, google, openai

import llm_pace
import secrets_store
import settings as settings_store
from tts_adapter import AdapterTTS, resolve_adapter

log = logging.getLogger("callin.agent")


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
    elif wanted == "openai" and os.environ.get("OPENAI_API_KEY"):
        provider, default = "openai", "gpt-4o-mini-transcribe"
    elif wanted == "deepgram" and os.environ.get("OPENAI_API_KEY"):
        provider, default = "openai", "gpt-4o-mini-transcribe"
        note = "no Deepgram key — using OpenAI STT"
    elif wanted == "google" and os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        provider, default = "google", ""
    else:
        # The built-in Whisper, not Google (0.10.86): the old last resort
        # assumed Google application-default credentials exist, which is
        # never true on a fresh install — a real deployment's STT stage
        # failed with an ADC lecture while a working Whisper sat in the
        # container. Google STT wants a SERVICE ACCOUNT
        # (GOOGLE_APPLICATION_CREDENTIALS), not the Gemini API key, so a
        # google pick without one falls here too and the note says why.
        provider, default = "local", "base.en"
        if wanted == "google":
            note = ("Google STT needs service-account credentials "
                    "(GOOGLE_APPLICATION_CREDENTIALS), not the Gemini key — "
                    "using the built-in Whisper")
        elif wanted != "local":
            note = f"no usable key for {wanted} — using the built-in Whisper"

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


# Passed as the api_key when a caller has asked us NOT to send the stored one.
# A blank string makes some SDKs fall back to the environment, which is the
# thing being prevented, so it has to be a real (worthless) value.
WITHHELD_KEY = "withheld-by-talk-wave"


def build_llm(cfg: dict, *, use_stored_key: bool = True, fallback: bool = True):
    """The brain a call (or a text, or a voicemail) thinks with.

    `use_stored_key=False` builds the model without the operator's API key.
    Only the settings panel's test buttons pass this, and only when the base
    URL came from the request rather than from saved settings — a stored key
    must never be posted to a host the operator has not saved. See
    token_server._credentials_travel_to.

    `fallback=False` builds the primary leg alone. The panel's Test and bench
    buttons pass it: a test that quietly passed on the fallback would report
    a broken primary as working, which is the one thing a test must not do.

    With `llm_fallback_provider` set (blank by default — no deployment grows
    a net it did not ask for) the primary is wrapped in a FallbackLLM that
    carries the turn on the second leg when the first refuses outright. A
    fallback that cannot be BUILT — openai-compatible with no endpoint, say
    — costs a warning and leaves the primary standing alone, never the call.
    """
    primary = _build_one(cfg, use_stored_key=use_stored_key)
    if not fallback or not str(cfg.get("llm_fallback_provider") or "").strip():
        return primary
    second_cfg = fallback_cfg(cfg)
    try:
        second = _build_one(second_cfg, use_stored_key=use_stored_key)
    except Exception as e:                                     # noqa: BLE001
        log.warning("the fallback brain (%s) could not be built — the primary "
                    "stands alone: %s", leg_name(second_cfg), e)
        return primary
    return FallbackLLM(primary, second,
                       names=(leg_name(cfg), leg_name(second_cfg)))


def leg_name(cfg: dict) -> str:
    """`provider/model` the way the record and the log already spell it."""
    return f"{cfg.get('llm_provider') or ''}/{cfg.get('llm_model') or ''}"


def fallback_cfg(cfg: dict) -> dict:
    """The primary's config re-pointed at the fallback leg, so the one
    builder serves both — any provider the primary can be, the fallback can
    be, with the same keys, the same temperature and the same model-name
    hygiene. The Endpoint is the one field that does NOT carry across unless
    the two legs share a provider: llm_base_url names the PRIMARY's server,
    and sending Ollama's traffic to a llama.cpp box (or the other way round)
    is a 404 on every utterance of the leg that was meant to save the call."""
    out = dict(cfg)
    net = str(cfg.get("llm_fallback_provider") or "").strip().lower()
    out["llm_provider"] = net
    out["llm_model"] = str(cfg.get("llm_fallback_model") or "").strip()
    if net != str(cfg.get("llm_provider") or "").strip().lower():
        out["llm_base_url"] = ""
    return out


# The failures the fallback is FOR: the account, not the request, is what is
# wrong, so the next request will fail the same way and retrying is how six
# days of calls (2026-11-09 to -15) each died on the same "prepayment credits
# are depleted" 402. 401 and 403 are the same shape — a key revoked or a
# project disabled — and a connection that cannot be made at all (the host
# gone, DNS dead) is an outage no retry fixes inside one call.
FALLBACK_STATUSES = frozenset({401, 402, 403})


def needs_fallback(err) -> bool:
    """True when the primary's failure is one the second leg should carry.

    NOT a timeout: a model that is merely slow is llm_pace's problem and the
    operator's model choice, and the panel's help says so. The SDK wraps an
    exhausted retry loop in a plain APIConnectionError `from` the last
    attempt's error, so the cause is what says whether the attempts were
    timeouts (stay) or a dead host (switch)."""
    if isinstance(err, APIStatusError):
        return err.status_code in FALLBACK_STATUSES
    if isinstance(err, APITimeoutError):
        return False
    if isinstance(err, APIConnectionError):
        cause = err.__cause__
        if isinstance(cause, APITimeoutError):
            return False
        if isinstance(cause, APIStatusError):
            return cause.status_code in FALLBACK_STATUSES
        return True
    return False


@dataclasses.dataclass
class FallbackSwitch:
    """What the record is told when a call changes brains mid-call."""

    primary: str
    fallback: str
    error: Exception

    def in_plain_words(self) -> str:
        why = f"{type(self.error).__name__}: {self.error}"[:200]
        return (f"The brain ({self.primary}) refused the request — {why} — so "
                f"the fallback ({self.fallback}) carried the call from there.")


class FallbackLLM(lk_llm.LLM):
    """Two legs, one brain: the primary until it refuses outright, then the
    fallback for the rest of the session.

    Why a wrapper rather than the session's error event: by the time the
    session hears a non-recoverable LLM error the turn is lost and the caller
    gets the apology line. Switching INSIDE the stream re-issues the same
    turn on the second leg, so the caller hears a reply, from a different
    brain, and nothing else. The switch is sticky for the life of the object
    — a 402 does not clear itself between turns — and happens only before
    the first token: a reply that has started on one model is not finished
    by another.

    Errors from the legs are relayed as this object's own, which is where the
    AgentSession listens, except the one the switch absorbs. Metrics are not
    relayed: the base stream measures the forwarded chunks itself, so the
    panel's per-turn numbers are the leg that actually answered.
    """

    def __init__(self, primary, fallback, *, names=("", "")) -> None:
        super().__init__()
        self._primary = primary
        self._fallback = fallback
        self.primary_name, self.fallback_name = names
        self.switched = False
        self.switched_by: Exception | None = None
        self._label = primary._label
        primary.on("error", self._relay)
        fallback.on("error", self._relay)

    @property
    def active(self):
        return self._fallback if self.switched else self._primary

    @property
    def model(self) -> str:
        return self.active.model

    @property
    def provider(self) -> str:
        return self.active.provider

    def chat(self, *, chat_ctx, tools=None,
             conn_options=DEFAULT_API_CONNECT_OPTIONS,
             parallel_tool_calls=NOT_GIVEN, tool_choice=NOT_GIVEN,
             extra_kwargs=NOT_GIVEN):
        return _FallbackStream(
            self, chat_ctx=chat_ctx, tools=tools or [],
            conn_options=conn_options,
            passthrough=dict(parallel_tool_calls=parallel_tool_calls,
                             tool_choice=tool_choice, extra_kwargs=extra_kwargs))

    def prewarm(self, *, loop=None) -> None:
        self._primary.prewarm(loop=loop)

    async def aclose(self) -> None:
        await self._primary.aclose()
        await self._fallback.aclose()
        await super().aclose()

    def _relay(self, ev) -> None:
        # The one error the switch is about to absorb is not relayed: the
        # session would record it as a failed turn and say the apology line
        # over a reply the fallback is already producing.
        if not self.switched and needs_fallback(getattr(ev, "error", None)):
            return
        self.emit("error", ev)

    def _switch(self, err: Exception) -> None:
        if self.switched:
            return
        self.switched = True
        self.switched_by = err
        self._label = self._fallback._label
        ev = FallbackSwitch(self.primary_name, self.fallback_name, err)
        log.warning("brain fallback: %s", ev.in_plain_words())
        self.emit("fallback", ev)


class _FallbackStream(lk_llm.LLMStream):
    """One turn: drain the active leg into this stream; if the primary fails
    before its first chunk with a failure the net is for, switch and drain
    the fallback instead, in the same stream, so the SDK sees one reply."""

    def __init__(self, owner: FallbackLLM, *, chat_ctx, tools, conn_options,
                 passthrough: dict) -> None:
        self._owner = owner
        self._inner_conn = conn_options
        self._passthrough = passthrough
        # The LEGS retry (with the operator's own budget — llm_conn_options);
        # this shell must not retry on top of them, or a dead primary costs
        # the caller the budget squared before the fallback is tried.
        super().__init__(owner, chat_ctx=chat_ctx, tools=tools,
                         conn_options=dataclasses.replace(conn_options, max_retry=0))

    async def _drain(self, leg, forwarded: list) -> None:
        stream = leg.chat(chat_ctx=self._chat_ctx, tools=self._tools,
                          conn_options=self._inner_conn, **self._passthrough)
        try:
            async for chunk in stream:
                self._event_ch.send_nowait(chunk)
                forwarded.append(1)
        finally:
            await stream.aclose()

    async def _run(self) -> None:
        owner = self._owner
        if owner.switched:
            await self._drain(owner.active, [])
            return
        forwarded: list = []
        try:
            await self._drain(owner._primary, forwarded)
        except Exception as e:                                 # noqa: BLE001
            if forwarded or not needs_fallback(e):
                raise
            owner._switch(e)
        else:
            return
        await self._drain(owner._fallback, [])


def _build_one(cfg: dict, *, use_stored_key: bool = True):
    """One leg, from `llm_provider` / `llm_model` / `llm_base_url`. The
    fallback leg comes through here too, on fallback_cfg(cfg)."""
    provider = str(cfg.get("llm_provider") or "").lower()
    # Blank since 0.10.80's fresh-install defaults: nothing is picked until
    # the operator picks it. Refuse with the fix in the message — the bare
    # "Unsupported llm_provider:" below reads as a bug, not a gap.
    if not provider:
        raise ValueError(
            "no AI provider is configured — pick one under Configuration → "
            "Brains in the settings panel"
        )
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

    def key(env_var: str):
        """The stored key, or a worthless stand-in when withholding it.

        NOT_GIVEN — not None, which these SDKs read as "given, and empty" and
        reject — so the normal path keeps the plugins' own read-the-environment
        behaviour, including the error a missing key produces.
        """
        if not use_stored_key:
            return WITHHELD_KEY
        return os.environ.get(env_var) or NOT_GIVEN

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
            api_key=key("OPENROUTER_API_KEY"),
            temperature=temperature,
        )

    # DeepSeek and the two aggregators are OpenAI's wire protocol at a
    # different address, so the openai plugin drives all three — the same way
    # it already drives Ollama. No plugin, no SDK, no extra dependency: what
    # differs is a URL and which key. See settings.OPENAI_PROTOCOL_HOSTS.
    if provider in settings_store.OPENAI_PROTOCOL_HOSTS:
        host, fallback_model = settings_store.OPENAI_PROTOCOL_HOSTS[provider]
        chosen = model or fallback_model
        if not chosen:
            raise ValueError(
                f"{provider} has no default model — pick one under Brains. "
                "Its catalogue is namespaced and changes, so guessing an id "
                "here would fail on every reply instead of once, here."
            )
        env_var = secrets_store.SECRET_FIELDS[
            settings_store.LLM_PROVIDER_KEY[provider]]
        return openai.LLM(
            model=chosen,
            temperature=temperature,
            api_key=key(env_var),
            base_url=base_url or host,
        )

    if provider == "locca":
        # The station's own local runner — llama.cpp behind locca, speaking
        # the OpenAI protocol with no key. Unlike openai-compatible, a blank
        # Endpoint is not an error: locca has a well-known address on the
        # host, mirrored from the station's DEFAULT_LOCCA_BASE_URL, so the
        # operator who runs the station on locca picks the name and is done.
        return openai.LLM(
            model=model or NOT_GIVEN,
            temperature=temperature,
            api_key="not-needed",
            base_url=base_url
            or os.environ.get("LOCCA_BASE_URL")
            or settings_store.LOCCA_BASE_URL_DEFAULT,
        )

    if provider == "openai-compatible":
        # The operator's own server — llama.cpp, vLLM, LM Studio — matching
        # the station's provider of the same name. The endpoint IS the
        # configuration: there is no default address to fall back to, and
        # failing here with a sentence beats dialling nothing.
        if not base_url:
            raise ValueError(
                "openai-compatible needs an Endpoint under Brains — it is "
                "the address of your own server, and there is no default."
            )
        return openai.LLM(
            model=model or NOT_GIVEN,
            temperature=temperature,
            # Most such servers take no key, but the SDK insists on one; the
            # placeholder is what with_ollama does internally too. A server
            # that does check keys reads OPENAI_COMPAT_API_KEY.
            api_key=(key("OPENAI_COMPAT_API_KEY")
                     if os.environ.get("OPENAI_COMPAT_API_KEY")
                     else "not-needed"),
            base_url=base_url,
        )

    if provider == "openai":
        return openai.LLM(
            model=model or "gpt-4.1-mini",
            temperature=temperature,
            api_key=key("OPENAI_API_KEY"),
            **({"base_url": base_url} if base_url else {}),
        )
    if provider == "google":
        return google.LLM(
            model=model or "gemini-2.5-flash",
            api_key=key("GOOGLE_API_KEY"),
            temperature=temperature,
        )
    if provider == "anthropic":
        return anthropic.LLM(
            model=model or "claude-sonnet-5",
            api_key=key("ANTHROPIC_API_KEY"),
            temperature=temperature,
        )

    raise ValueError(f"Unsupported llm_provider: {provider}")


def llm_conn_options(cfg: dict) -> APIConnectOptions:
    """How long this provider may take before a turn is abandoned.

    The SDK's default — 10s per attempt, three retries — is a cloud number, and
    passing nothing meant every deployment got it. On a self-hosted box that is
    not patience, it is a hard ceiling on how long the model may take to produce
    its FIRST token, and a model that misses it fails every turn rather than
    running slowly. See llm_pace for the incident and the arithmetic.
    """
    timeout, retries = llm_pace.attempt_budget(cfg.get("llm_provider", ""))
    return APIConnectOptions(timeout=timeout, max_retry=retries)


async def stream_reply(model, ctx, *, tools=None, on_delta=None):
    """Drain ONE `model.chat` pass: accumulate the content (and the tool calls,
    when `tools` is given), then close the STREAM. Returns `(content, calls)`.

    The single-pass drain seven off-turn callers hand-rolled — greetings,
    premises, follow-ups, the quiz, voicemail triage/greeting, the on-air
    handoff — plus the tool-call read the voicemail draft preview does. It
    closes only the stream, NEVER the model: some callers BORROW an existing
    llm (the handoff uses the live session's) and must close it themselves, or
    not at all. Each caller keeps its own build_llm, error policy, `model.aclose`
    and parse — only the mechanical open/drain/close lives here now. (Not for
    chat/session's multi-round tool loop, nor /test/llm's deliberate mirror —
    those are different shapes on purpose.)
    """
    content = ""
    calls: list = []
    stream = (model.chat(chat_ctx=ctx, tools=tools) if tools
              else model.chat(chat_ctx=ctx))
    try:
        async for chunk in stream:
            delta = getattr(chunk, "delta", None)
            if not delta:
                continue
            piece = getattr(delta, "content", None)
            if piece:
                content += piece
                if on_delta:
                    on_delta(piece)
            if tools:
                tc = getattr(delta, "tool_calls", None)
                if tc:
                    calls.extend(tc)
    finally:
        await stream.aclose()
    return content, calls


def build_tts(cfg: dict, voice: str) -> AdapterTTS:
    # Blank since 0.10.85's defaults review, like the LLM: nothing is picked
    # until the operator picks it. Refusing with the fix beats limping into
    # the cloud shape with no key and failing mid-greeting.
    if not str(cfg.get("tts_mode") or ""):
        raise ValueError(
            "no voice backend is configured — pick one under Configuration → "
            "Voice in the settings panel"
        )
    # One resolver, in tts_adapter. This was three copies of the same three
    # lines — token_server twice and here — and all three let an absolute path
    # through to open(). It also used to resolve against Path(__file__) rather
    # than ADAPTER_DIR, which silently stopped finding the adapter when this
    # moved a directory down and crashed every call before the DJ spoke.
    adapter_path = resolve_adapter(cfg.get("tts_adapter"))


    return AdapterTTS(
        voice=voice,
        base_url=cfg.get("tts_base_url") or os.environ.get("TTS_BASE_URL", ""),
        adapter_path=adapter_path,
        model=cfg.get("tts_model") or "",
        # Passed, not smuggled through os.environ — see _default_adapter_path.
        mode=str(cfg.get("tts_mode", "cloud")),
    )


