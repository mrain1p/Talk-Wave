"""The panel's test endpoints, and the records they explain.

These exercise the same code paths a real call uses, so a green result here
means the call will work rather than "the URL responded". All operator-only:
they cost money, they name hosts, and the call records are a transcript of
what callers said.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx
from aiohttp import web
from livekit import api

import secrets_store
import settings as settings_store
from api.auth import _write_allowed
from api.credentials import _credentials_travel_to
from api.env import LIVEKIT_API_KEY, LIVEKIT_API_SECRET
from api.hooks import _hook_state
from api.tokens import _mint_info
from api.wire import _cors
from station import StationClient
from station_config import StationConfig
from tts_adapter import available_voices as tts_voice_list
from tts_adapter import pick_speakable_voice, resolve_adapter

log = logging.getLogger("callin.token")


def _plain_error(e: BaseException) -> str:
    """The message an operator can act on.

    Python 3.11 exception groups stringify to "unhandled errors in a
    TaskGroup (1 sub-exception)" — httpx/anyio raise them for every connect
    failure and timeout — and a probe that shows the wrapper has told the
    operator nothing. The station test did exactly that on a real deployment
    (0.10.82); the real cause was one level down the whole time.
    """
    seen: list[str] = []

    def walk(x: BaseException) -> None:
        sub = getattr(x, "exceptions", None)
        if sub:
            for s in sub:
                walk(s)
            return
        # httpx's timeout family stringifies to NOTHING, so the fallback used
        # to be the bare class name — a probe answered a real operator with
        # the single word "ReadTimeout" (0.10.88). Name what actually
        # happened instead.
        worded = {
            "ReadTimeout": "timed out waiting for a reply — the other end "
                           "is answering too slowly",
            "ConnectTimeout": "timed out trying to connect — nothing "
                              "answered at that address in time",
            "WriteTimeout": "timed out sending the request",
            "PoolTimeout": "timed out waiting for a free connection",
        }
        msg = (str(x).strip() or worded.get(type(x).__name__)
               or type(x).__name__)
        if msg not in seen:
            seen.append(msg)

    walk(e)
    return "; ".join(seen[:3])


# --- test endpoints -------------------------------------------------------
# These exercise the same code paths a real call uses, so a green result here
# means the call will work rather than "the URL responded".

TEST_LINE = "You're on the air. What are we playing tonight?"

# Natural speech sits near 11 characters a second. The band either side of it
# here is deliberately enormous, because inferring a sample rate from speaking
# pace is a trap: a persona written to talk in fast clipped fragments produces
# a fraction of the audio a normal voice does for the same text, and a single
# measurement against one reads as several octaves out. Only a rate this far
# from plausible is worth raising, and even then only as "check it".
_CHARS_PER_SEC_FLOOR = 3.0
_CHARS_PER_SEC_CEILING = 30.0


async def _persona_voice_audit(available: list[str]) -> str:
    """Which DJs this TTS backend cannot speak as — all of them, not just the
    one currently on air.

    pick_speakable_voice only ever sees the persona a call actually reached,
    so a persona whose voice the backend does not have stays invisible until
    someone rings in while that DJ is live. It then falls back and says why,
    which is the right behaviour but the wrong moment to find out: the caller
    reaches the station's DJ in somebody else's voice, and the whole point of
    mirroring the on-air voice is gone.

    The roster is already fetched for the panel, so checking the whole of it
    turns "discovered on the first call to that DJ" into "discovered when the
    operator presses the button".
    """
    if not available:
        return ""            # lookup failed — not evidence about any persona
    sc = StationConfig()
    try:
        voices = await sc.persona_voices()
    except Exception as e:                                    # noqa: BLE001
        return f"could not read the persona roster to check it ({e})"[:160]
    finally:
        await sc.aclose()

    if not voices:
        return ""
    have = set(available)
    missing = sorted(pid for pid, voice in voices.items() if voice and voice not in have)
    if not missing:
        return f"all {len(voices)} personas have a voice this backend can speak"
    return (
        f"{len(missing)} of {len(voices)} personas use a voice this backend does "
        f"not have ({', '.join(missing[:6])}"
        f"{', …' if len(missing) > 6 else ''}) — a caller reaching one of those "
        f"DJs hears a substitute voice, not theirs. Either point this at the TTS "
        f"server the station uses, or add those voices to this one."
    )


def _sample_rate_verdict(
    declared: int, measured: int | None, why_not: str, text: str, audio_sec: float
) -> str:
    """Whether the rate in the adapter matches the audio the backend sent.

    A sample rate is a label attached to the samples rather than something
    carried in them, so getting it wrong produces no error anywhere: audio
    plays at the wrong speed and pitch and every component reports success.
    Declaring 24000 for a backend producing 48000 is half speed an octave
    down. The same engine commonly reports one rate on a GPU and half of it on
    a CPU, so the adapter that is correct on one host is silently wrong on the
    next — which is exactly the case documentation cannot fix and a
    measurement can.
    """
    if measured:
        if measured == declared:
            return f"sample rate {declared} Hz confirmed against the backend's own wav header"
        # Speed is declared/measured, not the other way round: the player
        # consumes `declared` samples a second from audio that carries
        # `measured` of them, so declaring HALF the real rate plays at half
        # speed and an octave down — the slow draggy DJ, not the chipmunk.
        speed = declared / measured if measured else 0
        return (
            f"✗ SAMPLE RATE MISMATCH — the adapter declares {declared} Hz and the "
            f"backend produced {measured} Hz. Playback will run at "
            f"{speed:.2g}× speed, pitched to match, and nothing anywhere will "
            f"report an error. Set audio.sample_rate to {measured} in the adapter."
        )

    if audio_sec > 0 and text:
        pace = len(text) / audio_sec
        if pace < _CHARS_PER_SEC_FLOOR or pace > _CHARS_PER_SEC_CEILING:
            likely = int(declared * pace / 11.0)
            return (
                f"⚠ Could not measure the sample rate ({why_not}), and the "
                f"declared {declared} Hz implies {pace:.0f} characters of speech "
                f"a second, against about 11 for natural speech. Something near "
                f"{likely} Hz would be plausible — but confirm by ear before "
                f"changing it, because a voice that speaks unusually fast or "
                f"slow will produce this reading while being perfectly correct."
            )
    return ""


async def handle_test_tts(request: web.Request) -> web.Response:
    """Synthesize one line and report whether the backend can keep up with a
    live call. The realtime factor is the number that matters: above 1.0 the
    buffer starves and playback gaps."""
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))
    secrets_store.apply_to_env()

    body = await request.json() if request.can_read_body else {}
    cfg = settings_store.permissions_for(settings_store.load(), "admin")
    saved_tts = cfg.get("tts_base_url") or ""
    cfg.update({k: v for k, v in body.items() if v not in (None, "")})

    import base64 as _b64
    import time as _time

    from tts_adapter import AdapterTTS

    # A previewed TTS URL does not get the stored key. See
    # _credentials_travel_to.
    may_send, cred_note = _credentials_travel_to(cfg.get("tts_base_url"), saved_tts)

    # `tts_adapter` arrives in the BODY of this request, so it names a file
    # only within tts-adapters/ — see tts_adapter.resolve_adapter.
    adapter_path = resolve_adapter(cfg.get("tts_adapter"))
    tts_mode = str(cfg.get("tts_mode", "cloud"))

    voice = cfg.get("tts_voice") or ""
    if not voice:
        sc = StationConfig()
        try:
            st = StationClient()
            try:
                persona = await st.resolve_live_persona()
            finally:
                await st.aclose()
            voice = await sc.voice_for(persona["id"])
        finally:
            await sc.aclose()

    tts = None
    try:
        tts = AdapterTTS(
            voice=voice,
            base_url=cfg.get("tts_base_url") or "",
            api_key=os.environ.get("TTS_API_KEY", "") if may_send else "",
            allow_stored_key=may_send,
            adapter_path=adapter_path,
            model=cfg.get("tts_model") or "",
            mode=tts_mode,
        )
        spoken = body.get("text") or TEST_LINE
        t0 = _time.perf_counter()
        first = None
        pcm = bytearray()
        stream = tts.synthesize(spoken)
        async for ev in stream:
            if first is None:
                first = _time.perf_counter() - t0
            pcm.extend(ev.frame.data.tobytes())
        await stream.aclose()

        wall = _time.perf_counter() - t0
        audio_sec = len(pcm) / 2 / tts.sample_rate if tts.sample_rate else 0

        # AFTER the timed run, never inside it — this is a second request and
        # folding it in would make first-audio and realtime factor lie.
        measured, why_not = await tts.probe_sample_rate()
        rate_note = _sample_rate_verdict(
            tts.sample_rate, measured, why_not, spoken, audio_sec)

        return _cors(
            request,
            web.json_response(
                {
                    "ok": True,
                    "voice": voice,
                    "note": cred_note,
                    "firstAudioMs": round((first or 0) * 1000),
                    "wallMs": round(wall * 1000),
                    "audioSec": round(audio_sec, 2),
                    "realtimeFactor": round(wall / audio_sec, 2) if audio_sec else None,
                    "sampleRate": tts.sample_rate,
                    "measuredSampleRate": measured,
                    "sampleRateNote": rate_note,
                    "pcmBase64": _b64.b64encode(bytes(pcm)).decode(),
                }
            ),
        )
    except Exception as e:
        msg = _plain_error(e)
        # By far the most common failure: a voice id from one backend sent to
        # the other (stock cloud names vs the local sample registry).
        #
        # Only when the backend did not already say so itself. This hint is a
        # guess, and it exists because the response body used to be discarded;
        # now that the body comes through, a backend that names the voice in
        # its own error has explained itself and appending a guess underneath
        # is noise.
        if "400" in msg and voice and voice not in msg:
            msg += (
                f"\n\nThe backend rejected voice '{voice}'. Cloud voices "
                f"(alloy, onyx, …) and local sample ids (-Trevor2, -Delia1, …) "
                f"are not interchangeable — reload the voice list after "
                f"switching backend."
            )
        # The note matters most HERE: withholding the key is the likeliest
        # reason a previewed endpoint answers 401, and without saying so the
        # operator reads it as their key being wrong.
        return _cors(request, web.json_response(
            {"ok": False, "voice": voice, "error": msg, "note": cred_note}))
    finally:
        if tts is not None:
            await tts.aclose()


def _model_names(payload) -> list[str]:
    """Names out of whatever shape a /models endpoint answers with.

    OpenAI-compatible servers say {"data": [{"id": …}]}, Ollama says
    {"models": [{"name": …}]}, and the odd local server returns a bare list.
    One parser, so the hint below works against all of them.
    """
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("models") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        return []
    names = []
    for row in rows:
        if isinstance(row, str):
            name = row
        elif isinstance(row, dict):
            name = row.get("id") or row.get("name") or ""
        else:
            continue
        name = str(name).strip()
        if name:
            names.append(name)
    return names


def _looks_like_no_such_model(err: str) -> bool:
    """Does this failure mean "the server doesn't route that model name"?"""
    e = str(err).lower()
    return ("404" in e or "no router" in e or "model_not_found" in e
            or "model not found" in e or "does not exist" in e)


async def _models_offered(base_url: str) -> list[str]:
    """Ask an OpenAI-compatible endpoint what it actually serves.

    Deliberately keyless: local routers answer /models unauthenticated, and a
    stored key must not travel to a host just because a test failed against
    it. If the server wants auth for its list, the hint is simply skipped.
    """
    import httpx

    url = base_url.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            r = await c.get(url)
            if r.status_code != 200:
                return []
            return _model_names(r.json())
    except Exception:
        return []


async def handle_test_llm(request: web.Request) -> web.Response:
    """One short completion, with a tool offered. Reports whether the model
    actually emits a tool call — plenty of local models answer fluently but
    never call tools, which shows up as a DJ that never submits a request."""
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))
    secrets_store.apply_to_env()

    body = await request.json() if request.can_read_body else {}
    cfg = settings_store.permissions_for(settings_store.load(), "admin")
    saved_llm = cfg.get("llm_base_url") or ""
    cfg.update({k: v for k, v in body.items() if v not in (None, "")})

    import time as _time

    from livekit.agents import llm as lk_llm

    from call.providers import build_llm

    # A previewed LLM endpoint does not get the stored key — otherwise the
    # test button posts it, in an Authorization header, to whatever host was
    # named in the request. See _credentials_travel_to.
    may_send, cred_note = _credentials_travel_to(
        cfg.get("llm_base_url"),
        saved_llm,
        settings_store.provider_base_urls().get(str(cfg.get("llm_provider", "")).lower(), ""),
    )

    try:
        model = build_llm(cfg, use_stored_key=may_send)

        @lk_llm.function_tool
        async def request_song(song: str) -> str:
            """Put a song into the station's request queue."""
            return "queued"

        ctx = lk_llm.ChatContext.empty()
        ctx.add_message(
            role="user",
            content="Please play Dreams by Fleetwood Mac. Use your tool to request it.",
        )

        t0 = _time.perf_counter()
        text, tool_calls, first = "", [], None
        stream = model.chat(chat_ctx=ctx, tools=[request_song])
        async for chunk in stream:
            if first is None:
                first = _time.perf_counter() - t0
            delta = getattr(chunk, "delta", None)
            if delta is None:
                continue
            if getattr(delta, "content", None):
                text += delta.content
            if getattr(delta, "tool_calls", None):
                tool_calls.extend(delta.tool_calls)
        await stream.aclose()

        return _cors(
            request,
            web.json_response(
                {
                    "ok": True,
                    "provider": cfg.get("llm_provider"),
                    "model": cfg.get("llm_model"),
                    "note": cred_note,
                    "firstTokenMs": round((first or 0) * 1000),
                    "totalMs": round((_time.perf_counter() - t0) * 1000),
                    "toolCalling": bool(tool_calls),
                    "reply": (text or "").strip()[:200],
                }
            ),
        )
    except Exception as e:
        err = _plain_error(e)
        # A model name the server does not route is a miss the server itself
        # can explain. Observed with llama-swap (llama.cpp's multi-model
        # router, 2026-08-08): it answers 404 "no router for requested model"
        # unless the model field exactly matches one of its configured names —
        # while clients that pick from /v1/models connect fine. So on a miss,
        # ask the same endpoint what it does offer and say so, instead of
        # leaving the operator to a support thread.
        base = str(cfg.get("llm_base_url") or "").strip()
        if base and _looks_like_no_such_model(err):
            names = await _models_offered(base)
            if names:
                shown = ", ".join(names[:12])
                more = f" (+{len(names) - 12} more)" if len(names) > 12 else ""
                err += (
                    f"\n\nThe server doesn't know '{cfg.get('llm_model')}'. "
                    f"It says it offers: {shown}{more}. The model field must "
                    "match one of these exactly — routers like llama-swap "
                    "pick the model by its name."
                )
        # With the note, because a withheld key is the likeliest reason a
        # previewed endpoint refuses us — and it is not the same problem as a
        # key that is missing or wrong.
        return _cors(request, web.json_response(
            {"ok": False, "error": err, "note": cred_note}))


async def handle_prompt_preview(request: web.Request) -> web.Response:
    """The exact system prompt the next caller's DJ will be given.

    Assembled the same way the worker does it, so what you read here is what
    the model actually gets — including the live station context and any house
    style you've set.
    """
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))

    import brain
    from call.tools import effective_tools

    cfg = settings_store.permissions_for(settings_store.load(), "admin")
    station = StationClient()
    try:
        snap = await station.snapshot()
        override = str(cfg.get("persona_override") or "").strip()
        roster = {p.get("id"): p for p in snap["personas"]}
        persona = roster.get(override) or station.persona_from(snap["dj"], snap["personas"])
        text = await brain.build_system_prompt(station, persona, snapshot=snap)
    finally:
        await station.aclose()

    et = effective_tools(cfg)
    return _cors(
        request,
        web.json_response(
            {
                "prompt": text,
                "persona": persona.get("name"),
                "chars": len(text),
                "approxTokens": len(text) // 4,
                # MCP allowlist + local wrappers — what the model actually sees.
                "tools": et["mcp"] + et["local"],
            }
        ),
    )


async def handle_speed_test(request: web.Request) -> web.Response:
    """Time every stage a single conversational turn passes through, then add
    them up.

    Individual stages can each look acceptable while the sum is not: a caller
    experiences STT + LLM + TTS end to end, and that total is what decides
    whether the DJ feels like a person or a kiosk. This measures the real code
    paths, not a synthetic benchmark.
    """
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))

    import time as _time

    secrets_store.apply_to_env()
    body = await request.json() if request.can_read_body else {}
    cfg = settings_store.permissions_for(settings_store.load(), "admin")
    saved_llm = cfg.get("llm_base_url") or ""
    saved_tts = cfg.get("tts_base_url") or ""
    cfg.update({k: v for k, v in (body or {}).items() if v not in (None, "")})

    # Same rule as the individual test buttons: a URL that arrived in this
    # request does not get the stored key. See _credentials_travel_to.
    llm_key_ok, llm_note = _credentials_travel_to(
        cfg.get("llm_base_url"),
        saved_llm,
        settings_store.provider_base_urls().get(str(cfg.get("llm_provider", "")).lower(), ""),
    )
    tts_key_ok, tts_note = _credentials_travel_to(cfg.get("tts_base_url"), saved_tts)

    stages: list[dict] = []

    def record(name, ms, note="", counts=True, estimate=False):
        stages.append({"name": name, "ms": round(ms), "note": note,
                       "counts": counts, "estimate": estimate})

    # --- station snapshot (once per call, before the greeting) ---
    st = StationClient()
    try:
        t0 = _time.perf_counter()
        snap = await st.snapshot()
        record("Station snapshot", (_time.perf_counter() - t0) * 1000,
               "per call, before the DJ picks up", counts=False)

        t0 = _time.perf_counter()
        persona = st.persona_from(snap["dj"], snap["personas"])
        import brain

        prompt = await brain.build_system_prompt(st, persona, snapshot=snap)
        record("Prompt assembly", (_time.perf_counter() - t0) * 1000,
               f"{len(prompt)} chars (~{len(prompt)//4} tokens, paid every turn)",
               counts=False)
    finally:
        await st.aclose()

    # The station stream is checked in the PIPELINE, not here: it is a health
    # question rather than a timing one, and the failure that matters — an
    # http stream blocked as mixed content on an https page — only happens in
    # the caller's browser, so that is the only place worth testing it.

    # --- STT ---
    # For the local engine this is MEASURED, not estimated: the TTS stage
    # below produces real speech audio, and we transcribe that. The record is
    # inserted here (index) so the display keeps call order even though the
    # measurement happens after TTS.
    stt_ms = 0.0
    stt_index = len(stages)
    stt_provider_name, stt_model_name = "", ""
    try:
        from call.providers import build_stt, effective_stt

        stt_provider_name, stt_model_name, _ = effective_stt(cfg)
        build_stt(cfg)
        if stt_provider_name != "local":
            stt_ms = 400.0
            record("Speech-to-text", stt_ms,
                   f"{stt_provider_name} — network round trip not measured",
                   estimate=True)
    except Exception as e:
        record("Speech-to-text", 0, f"failed: {e}"[:110])
        stt_provider_name = ""

    # --- LLM to first token ---
    llm_ms = 0.0
    try:
        from livekit.agents import llm as lk_llm

        from call.providers import build_llm

        model_obj = build_llm(cfg, use_stored_key=llm_key_ok)
        ctx = lk_llm.ChatContext.empty()
        ctx.add_message(role="user", content="Say one short sentence about the weather.")

        t0 = _time.perf_counter()
        first = None
        stream = model_obj.chat(chat_ctx=ctx)
        async for chunk in stream:
            if first is None:
                delta = getattr(chunk, "delta", None)
                if delta and getattr(delta, "content", None):
                    first = (_time.perf_counter() - t0) * 1000
        await stream.aclose()
        llm_ms = first or (_time.perf_counter() - t0) * 1000
        record("LLM first token", llm_ms, f"{cfg.get('llm_provider')} / {cfg.get('llm_model')}")
    except Exception as e:
        record("LLM first token", 0, f"failed: {e}"[:110])

    # --- TTS to first audio ---
    tts_ms = 0.0
    rtf = None
    try:
        from tts_adapter import AdapterTTS

        # Request-supplied, same as /test/tts — constrained to tts-adapters/.
        adapter_path = resolve_adapter(cfg.get("tts_adapter"))

        voice = cfg.get("tts_voice") or ""
        if not voice:
            sc = StationConfig()
            try:
                st2 = StationClient()
                try:
                    pv = await st2.resolve_live_persona()
                finally:
                    await st2.aclose()
                voice = await sc.voice_for(pv["id"])
            finally:
                await sc.aclose()

        # Say WHY, rather than letting it surface as a 400 from the backend.
        # This stage resolves the voice the on-air DJ actually uses, so it is
        # the one place that sees a station voice the backend cannot speak —
        # which is a silent call, and used to read here as an opaque TTS error.
        available = await tts_voice_list(
            cfg.get("tts_base_url") or "",
            adapter_path=adapter_path,
            mode=str(cfg.get("tts_mode", "")),
        )
        voice, voice_note = pick_speakable_voice(voice, available)
        if voice_note:
            record("Voice availability", 0, voice_note, counts=False)

        # Every persona, not only the one who happens to be on air now.
        roster_note = await _persona_voice_audit(available)
        if roster_note:
            record("Persona voices", 0, roster_note, counts=False)

        tts = AdapterTTS(voice=voice, base_url=cfg.get("tts_base_url") or "",
                         api_key=os.environ.get("TTS_API_KEY", "") if tts_key_ok else "",
                         allow_stored_key=tts_key_ok,
                         adapter_path=adapter_path, model=cfg.get("tts_model") or "",
                         mode=str(cfg.get("tts_mode", "cloud")))
        pcm = bytearray()
        tts_rate = 24000
        try:
            t0 = _time.perf_counter()
            first = None
            samples = 0
            stream = tts.synthesize("Evening. You're through to the booth, what can I do for you?")
            async for ev in stream:
                if first is None:
                    first = (_time.perf_counter() - t0) * 1000
                samples += ev.frame.samples_per_channel
                pcm.extend(ev.frame.data.tobytes())
            await stream.aclose()
            tts_rate = tts.sample_rate or 24000
            wall = (_time.perf_counter() - t0)
            audio_s = samples / tts.sample_rate if tts.sample_rate else 0
            rtf = round(wall / audio_s, 2) if audio_s else None
            tts_ms = first or 0
            record("TTS first audio", tts_ms,
                   f"{voice} · {rtf}x realtime" + ("  ⚠ slower than playback" if rtf and rtf >= 1 else ""))
        finally:
            await tts.aclose()
    except Exception as e:
        pcm = bytearray()
        record("TTS first audio", 0, f"failed: {e}"[:110])

    # --- local STT, measured on the real speech TTS just produced ---
    if stt_provider_name == "local" and pcm:
        try:
            from livekit import rtc as lk_rtc

            from local_stt import LocalWhisperSTT

            stt_obj = LocalWhisperSTT(model=stt_model_name or "base.en")
            t0 = _time.perf_counter()
            # prewarm() is synchronous (the Agents SDK calls it unawaited);
            # keep the blocking load off this event loop.
            await asyncio.to_thread(stt_obj.prewarm)
            load_ms = (_time.perf_counter() - t0) * 1000

            n = len(pcm) // 2
            clip = lk_rtc.AudioFrame(
                data=bytes(pcm), sample_rate=tts_rate,
                num_channels=1, samples_per_channel=n,
            )
            t0 = _time.perf_counter()
            ev = await stt_obj._recognize_impl(clip)
            stt_ms = (_time.perf_counter() - t0) * 1000
            heard = (ev.alternatives[0].text if ev.alternatives else "").strip()
            audio_len = n / tts_rate
            factor = stt_ms / 1000 / audio_len if audio_len else 0
            entry = {
                "name": "Speech-to-text", "ms": round(stt_ms),
                "note": f"{stt_model_name} · measured on {audio_len:.1f}s of real speech "
                        f"({factor:.2f}x realtime) · heard: “{heard[:60]}”",
                "counts": True,
            }
            stages.insert(stt_index, entry)
            if load_ms > 800:
                stages.insert(stt_index + 1, {
                    "name": "STT model load", "ms": round(load_ms),
                    "note": "one-off per process, prewarmed in the worker",
                    "counts": False,
                })
        except Exception as e:
            stages.insert(stt_index, {
                "name": "Speech-to-text", "ms": 0,
                "note": f"measurement failed: {e}"[:110], "counts": True,
            })

    turn_ms = stt_ms + llm_ms + tts_ms
    return _cors(
        request,
        web.json_response(
            {
                "ok": True,
                "stages": stages,
                "note": "  ".join(n for n in (llm_note, tts_note) if n),
                "turnMs": round(turn_ms),
                "realtimeFactor": rtf,
                "verdict": (
                    "Feels immediate." if turn_ms < 1200 else
                    "Natural enough." if turn_ms < 2000 else
                    "Noticeable pause between turns." if turn_ms < 3500 else
                    "Long enough that callers will talk over it."
                ),
            }
        ),
    )


async def handle_test_env(request: web.Request) -> web.Response:
    """The links nothing else covers: is LiveKit up, is an agent worker
    actually registered to answer, and can the configured STT be built at all.

    STT is the least-testable leg — it needs live audio to exercise properly —
    so this at least catches a missing key or a bad provider/model combination
    before a caller discovers it."""
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))

    secrets_store.apply_to_env()
    body = await request.json() if request.can_read_body else {}
    cfg = settings_store.permissions_for(settings_store.load(), "admin")
    cfg.update({k: v for k, v in (body or {}).items() if v not in (None, "")})

    result: dict = {"ok": True}

    # --- LiveKit server + a registered worker ---
    lk_url = os.environ.get("LIVEKIT_URL", "ws://localhost:7880")
    http_url = lk_url.replace("wss://", "https://").replace("ws://", "http://").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(http_url + "/")
            result["livekit"] = {"ok": r.status_code < 500, "url": lk_url,
                                 "detail": f"HTTP {r.status_code}"}
    except Exception as e:
        result["livekit"] = {"ok": False, "url": lk_url, "detail": _plain_error(e)[:120]}
        result["ok"] = False

    # A registered worker is what actually answers the call.
    try:
        lkapi = api.LiveKitAPI(
            url=http_url,
            api_key=LIVEKIT_API_KEY,
            api_secret=LIVEKIT_API_SECRET,
        )
        try:
            await lkapi.room.list_rooms(api.ListRoomsRequest())
            result["livekitAuth"] = {"ok": True, "detail": "API credentials accepted"}
        finally:
            await lkapi.aclose()
    except Exception as e:
        detail = _plain_error(e)
        # The SDK's "either token, or api_key and api_secret, must be set"
        # names the symptom, not the deployment fix — a real operator's
        # adapted compose was missing the livekit.yaml mounts and this stage
        # left them to work that out alone (0.10.86).
        if "api_key and api_secret" in detail or "api_secret" in detail:
            detail = ("no LiveKit keypair reached this container — mount "
                      "./livekit.yaml:/etc/livekit.yaml:ro into BOTH talkwave "
                      "services (the shipped compose does), or set "
                      "LIVEKIT_API_SECRET in .env")
        result["livekitAuth"] = {"ok": False, "detail": detail[:200]}
        result["ok"] = False

    # What livekit.yaml says it will ADVERTISE — the flag the browser's own
    # media probe can only guess at. A deployment removed --node-ip from the
    # compose while the yaml still said use_external_ip: false: LiveKit
    # advertised its container address, every server stage passed, and media
    # had nowhere to flow (0.10.88). The parse can't see a --node-ip passed
    # on the command line, so the false/unset combination is a warning that
    # names both readings, never a failure.
    from api.env import rtc_flags

    flags = rtc_flags()
    if flags is not None:
        if flags["use_external_ip"]:
            result["rtc"] = {"ok": True, "detail":
                             "use_external_ip: true — the public address is "
                             "discovered and advertised"}
        elif flags["node_ip"]:
            result["rtc"] = {"ok": True, "detail":
                             f"node_ip pins {flags['node_ip']} — LAN-only by "
                             "design; for callers from anywhere remove it and "
                             "set use_external_ip: true"}
        else:
            result["rtc"] = {"ok": False, "detail":
                             "livekit.yaml has use_external_ip: false and no "
                             "node_ip — fine only when the compose passes "
                             "--node-ip (the shipped LAN default does). If "
                             "you removed --node-ip for public callers, set "
                             "use_external_ip: true in livekit.yaml, or "
                             "LiveKit advertises its container address and "
                             "media never flows"}

    # --- STT constructible with the configured provider/key ---
    try:
        from call.providers import build_stt, effective_stt

        provider, model, note = effective_stt(cfg)
        build_stt(cfg)  # surfaces missing credentials
        # Buildable with a valid model = the call will work. A note means the
        # selection didn't survive intact (missing key, or a model belonging to
        # another provider) and was corrected — worth surfacing, but not fatal.
        result["stt"] = {
            "ok": True,
            "provider": provider,
            "model": model,
            "note": note,
            "detail": f"{provider} · {model or 'default'}"
            + (f" — {note}" if note else ""),
        }
    except Exception as e:
        result["stt"] = {"ok": False, "provider": cfg.get("stt_provider"),
                         "detail": _plain_error(e)[:160]}
        result["ok"] = False

    # --- station admin credentials ---
    # Several tools are admin-gated (library search, announcements) and the
    # back-to-air handoff posts to /dj/say, which 401s without them. Probing
    # /listeners is a cheap, side-effect-free way to prove the credentials work.
    from station_config import has_admin

    if not has_admin():
        result["admin"] = {
            "ok": False,
            "detail": "not set — library search, on-air announcements and the "
                      "back-to-air handoff will all be refused",
        }
    else:
        base = settings_store.station_base_url()
        try:
            from station_config import admin_credentials

            user, password = admin_credentials()
            async with httpx.AsyncClient(timeout=6.0) as c:
                r = await c.get(f"{base}/listeners", auth=httpx.BasicAuth(user, password))
            if r.status_code == 401:
                result["admin"] = {"ok": False, "detail": "station rejected these credentials"}
            else:
                r.raise_for_status()
                result["admin"] = {"ok": True, "detail": "accepted by the station"}
        except Exception as e:
            result["admin"] = {"ok": False, "detail": _plain_error(e)[:120]}

    # --- listeners: the station refuses song requests when nobody is tuned in ---
    try:
        st = StationClient()
        try:
            np = await st.now_playing()
        finally:
            await st.aclose()
        count = ((np.get("listeners") or {}).get("current"))
        if count is None:
            count = ((np.get("context") or {}).get("listeners") or {}).get("count")
        result["listeners"] = {
            "count": count,
            "requestsOpen": bool(count),
            "detail": (f"{count} tuned in — requests are open" if count else
                       "nobody tuned in — the station will refuse song requests "
                       "until someone is listening"),
        }
    except Exception as e:
        result["listeners"] = {"count": None, "requestsOpen": False, "detail": _plain_error(e)[:120]}

    # --- keys the current configuration depends on ---
    need = []
    llm_p = str(cfg.get("llm_provider", "")).lower()
    if llm_p in ("openai", "google", "anthropic") and not secrets_store.get(f"{llm_p}_api_key"):
        need.append(f"{llm_p} (LLM)")
    if str(cfg.get("tts_mode")) == "cloud" and not (
        secrets_store.get("tts_api_key") or secrets_store.get("openai_api_key")
    ):
        need.append("cloud TTS")
    result["keys"] = {"ok": not need, "missing": need}
    result["webhook"] = dict(_hook_state)
    if need:
        result["ok"] = False

    return _cors(request, web.json_response(result))


async def handle_test_admin(request: web.Request) -> web.Response:
    """Prove the station admin credentials work, without running the whole
    pipeline. Accepts draft values in the body so a credential can be tested
    BEFORE saving it; falls back to the stored/env ones. Probes /listeners —
    admin-gated, read-only, side-effect free."""
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))

    body = await request.json() if request.can_read_body else {}
    from station_config import admin_credentials

    user = str((body or {}).get("subwave_admin_user") or "").strip()
    password = str((body or {}).get("subwave_admin_pass") or "").strip()
    draft = bool(user or password)
    if not (user and password):
        stored_user, stored_pass = admin_credentials()
        user = user or stored_user
        password = password or stored_pass
    if not (user and password):
        return _cors(request, web.json_response(
            {"ok": False, "detail": "no credentials to test — fill in both fields"}
        ))

    base = settings_store.station_base_url()
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{base}/listeners", auth=httpx.BasicAuth(user, password))
        if r.status_code == 401:
            detail = "station rejected these credentials"
            ok = False
        elif r.status_code == 429:
            detail = ("station login rate limiter is active — wait 15 minutes "
                      "(or restart the station) and test again; this does not "
                      "mean the credentials are wrong")
            ok = False
        else:
            r.raise_for_status()
            detail = ("accepted by the station"
                      + (" (draft — remember to save)" if draft else ""))
            ok = True
        return _cors(request, web.json_response({"ok": ok, "detail": detail}))
    except Exception as e:
        return _cors(request, web.json_response({"ok": False, "detail": _plain_error(e)[:140]}))


async def handle_test_station(request: web.Request) -> web.Response:
    """Station reachable, and how many MCP tools survive the allowlist."""
    # Same gate as every other test endpoint: without an admin key, a foreign
    # origin must not be able to read the station URL and tool list.
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))

    from livekit.agents import mcp as lk_mcp

    from call.tools import effective_tools
    secrets_store.apply_to_env()

    cfg = settings_store.permissions_for(settings_store.load(), "admin")
    # Resolved through the same sane-URL helpers every call uses — never the
    # raw stored/env values. A comment-poisoned SUBWAVE_MCP_URL ("# blank
    # derives …", the env_file inline-comment leak) sailed through the old
    # cfg.get() and this probe handed httpx a URL starting with '#' while a
    # real call, resolving via station_mcp_url(), worked fine — the exact
    # split that makes a diagnostic lie about the thing it diagnoses
    # (operator's NAS, 0.10.84).
    qbase = request.query.get("station_base_url")
    base = qbase or settings_store.station_base_url()
    mcp_url = request.query.get("station_mcp_url") or (
        f"{qbase.rstrip('/')}/mcp" if qbase else settings_store.station_mcp_url()
    )

    # Connect with the same guarded MCP list a real call uses, and report the
    # local wrappers alongside — the raw MCP view is not what callers get.
    et = effective_tools(cfg)
    allowed = et["mcp"]
    result: dict = {"ok": False, "mcpUrl": mcp_url, "stationUrl": base}

    station = StationClient(base_url=base)
    try:
        health = await station.health()
        result["station"] = bool(health)
        persona = await station.resolve_live_persona()
        result["liveDj"] = persona.get("name")
    finally:
        await station.aclose()

    from station_config import mcp_headers

    # The MCP URL is request-supplied, and mcp_headers() is the station's admin
    # password. Only send it to the station this instance is configured for.
    may_send, note = _credentials_travel_to(
        mcp_url, settings_store.station_mcp_url(), settings_store.station_base_url()
    )
    headers = mcp_headers() if may_send else {}
    if note:
        result["note"] = note
    result["adminAuth"] = bool(headers)
    server = lk_mcp.MCPServerHTTP(
        url=mcp_url,
        transport_type="streamable_http",
        allowed_tools=allowed,
        headers=headers or None,
        client_session_timeout_seconds=15,
    )
    try:
        await server.initialize()
        tools = await server.list_tools()
        mcp_names = [
            lk_mcp.get_raw_function_info(t).name if lk_mcp.is_raw_function_tool(t) else str(t)
            for t in tools
        ]
        result["tools"] = mcp_names + et["local"]
        result["toolCount"] = len(mcp_names) + len(et["local"])
        result["ok"] = True
    except Exception as e:
        result["error"] = _plain_error(e)
    finally:
        try:
            await server.aclose()
        except Exception:
            pass

    return _cors(request, web.json_response(result))


async def handle_calls(request: web.Request) -> web.Response:
    """Recent calls, both sides of each conversation.

    The worker writes these; this process only reads them. Operator-only —
    it's a transcript of what callers said.
    """
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))}, status=401))
    from call.record import recent

    # The worker writes the record and never sees the browser that called, so
    # what we knew at mint time is attached here rather than stored with it.
    calls = recent(20)
    for c in calls:
        known = _mint_info.get(c.get("room") or "")
        if known:
            c["caller"] = known
    return _cors(request, web.json_response({"calls": calls}))


async def handle_clear_calls(request: web.Request) -> web.Response:
    """Throw away every stored call record.

    `record_keep` only trims as new calls arrive, so a deployment that has gone
    quiet keeps whatever it last had indefinitely — and after a run of test
    calls the panel is mostly stale conversations you have already read. This
    is the operator saying so.

    The mint-time caller context goes with them. It lives in memory here rather
    than in the record, so clearing the records alone would leave the panel
    able to say which browser and which network rang for a call whose
    transcript no longer exists.
    """
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))}, status=401))
    from call.record import clear

    gone = clear()
    _mint_info.clear()
    log.info("call records cleared by the operator (%d removed)", gone)
    return _cors(request, web.json_response({"ok": True, "removed": gone}))


async def handle_clear_logs(request: web.Request) -> web.Response:
    """Empty the log viewer's buffer.

    In memory only — docker still holds its own copy of this process's stdout,
    so this clears what the panel shows rather than destroying the record.
    """
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))}, status=401))
    import log_setup

    gone = log_setup.clear()
    log.info("log buffer cleared by the operator (%d lines removed)", gone)
    return _cors(request, web.json_response({"ok": True, "removed": gone}))


async def handle_logs(request: web.Request) -> web.Response:
    """The web service's recent log lines, for the panel's log viewer —
    settings changes, tokens minted, station reads, webhook events. The
    call agent runs in its own container; its logs need
    `docker logs <worker container>`."""
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))
    import log_setup

    records = log_setup.recent_records(300)
    return _cors(request, web.json_response({
        "records": records,
        # The flattened form stays, so an older widget cached in a browser
        # keeps working rather than showing an empty box after an upgrade.
        "lines": log_setup.recent_lines(300),
        # What is actually present, so the filter offers real choices rather
        # than a fixed list of levels that may match nothing.
        "levels": sorted({r["level"] for r in records}),
        "sources": sorted({r["logger"] for r in records}),
    }))
