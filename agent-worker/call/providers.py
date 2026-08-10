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

import logging
import os
from pathlib import Path

from livekit.agents.types import NOT_GIVEN
from livekit.plugins import anthropic, deepgram, google, openai

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


# Passed as the api_key when a caller has asked us NOT to send the stored one.
# A blank string makes some SDKs fall back to the environment, which is the
# thing being prevented, so it has to be a real (worthless) value.
WITHHELD_KEY = "withheld-by-wave-talk"


def build_llm(cfg: dict, *, use_stored_key: bool = True):
    """`use_stored_key=False` builds the model without the operator's API key.

    Only the settings panel's test buttons pass this, and only when the base
    URL came from the request rather than from saved settings — a stored key
    must never be posted to a host the operator has not saved. See
    token_server._credentials_travel_to.
    """
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


def build_tts(cfg: dict, voice: str) -> AdapterTTS:
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


