"""The voicemail HTTP surface: staging greetings, and reading messages back.

Everything here is ADMIN — staging spends the operator's TTS money once per
persona, and the messages are strangers' words meant for the operator's eyes.
The clips themselves never travel over HTTP: the worker reads them off the
shared data/ volume at pickup.
"""

from __future__ import annotations

import logging

from aiohttp import web

import settings as settings_store
from api.auth import _write_allowed
from api.wire import _cors
from station import StationClient
from voicemail import deliver as vm_deliver
from voicemail import greetings

log = logging.getLogger("callin.voicemail")


def _refuse(request: web.Request) -> web.Response:
    return _cors(request, web.json_response(
        {"error": request.get("auth_error") or "not allowed",
         "authRequired": bool(request.get("auth_required"))}, status=401))


async def handle_voicemail_status(request: web.Request) -> web.Response:
    """What is staged, per persona, and whether it is current — so the panel
    can say "3 of 4 staged, Rosie's is stale" instead of a bare button."""
    if not _write_allowed(request):
        return _refuse(request)

    import secrets_store

    secrets_store.apply_to_env()
    cfg = settings_store.load()
    station = StationClient(base_url=cfg.get("station_base_url"))
    try:
        personas = await station.personas()
        dj = await station.live_dj()
    except Exception as e:                                    # noqa: BLE001
        personas, dj = [], {}
        log.info("voicemail status could not read the station: %s", e)
    finally:
        await station.aclose()

    station_name = str((dj or {}).get("station") or "")
    show_name = str((dj or {}).get("show") or (dj or {}).get("showName") or "")
    index = greetings.read_index()
    from station_config import StationConfig

    sc = StationConfig(base_url=cfg.get("station_base_url"))
    out = []
    for p in personas:
        pid = str(p.get("id") or "")
        if not pid:
            continue
        name = str(p.get("name") or pid)
        voice = await sc.voice_for(pid)
        text = greetings.greeting_text_for(pid, cfg, station_name, name,
                                           show_name)
        key = greetings.render_key(text, voice, str(cfg.get("tts_mode", "")),
                                  str(cfg.get("tts_adapter") or ""))
        entry = index.get(pid) or {}
        out.append({
            "id": pid, "name": name,
            # Everything the panel's per-persona row shows: the words the
            # clip speaks (editable), the voice it speaks them in, and
            # whether the words came from a per-persona override.
            "text": text,
            "voice": voice,
            "overridden": pid in greetings.read_overrides(),
            "staged": greetings.clip_path(pid).is_file(),
            "current": entry.get("key") == key and greetings.clip_path(pid).is_file(),
            "renderedAt": entry.get("renderedAt") or "",
        })
    # The station's own row — the voice that answers when nobody is on air.
    text = greetings.greeting_text_for(greetings.STATION_ID, cfg,
                                       station_name, "", show_name)
    voice = str(cfg.get("tts_voice") or "")
    key = greetings.render_key(text, voice, str(cfg.get("tts_mode", "")),
                              str(cfg.get("tts_adapter") or ""))
    entry = index.get(greetings.STATION_ID) or {}
    out.append({
        "id": greetings.STATION_ID, "name": "The station (no DJ live)",
        "text": text,
        "voice": voice or "default voice",
        "overridden": greetings.STATION_ID in greetings.read_overrides(),
        "staged": greetings.clip_path(greetings.STATION_ID).is_file(),
        "current": entry.get("key") == key
                   and greetings.clip_path(greetings.STATION_ID).is_file(),
        "renderedAt": entry.get("renderedAt") or "",
    })
    await sc.aclose()
    return _cors(request, web.json_response({
        "personas": out,
        "messages": len(vm_deliver.held_messages()),
    }))


async def handle_voicemail_stage(request: web.Request) -> web.Response:
    """Render (or refresh) every persona's greeting and acknowledgement clip.

    Reports per persona, because a persona configured with a voice this TTS
    backend does not have will 400 — that has happened once already (Rosie's
    ElevenLabs id against local VibeVoice), and a staging job that says
    "3 ok, Rosie failed: voice not found" is the difference between a fix and
    a silent pickup a week later.
    """
    if not _write_allowed(request):
        return _refuse(request)

    import secrets_store
    from tts_adapter import AdapterTTS, resolve_adapter

    secrets_store.apply_to_env()
    cfg = settings_store.load()
    station = StationClient(base_url=cfg.get("station_base_url"))
    try:
        personas = await station.personas()
        dj = await station.live_dj()
    except Exception as e:                                    # noqa: BLE001
        await station.aclose()
        return _cors(request, web.json_response(
            {"error": f"could not read the station's personas: {e}"}, status=502))
    await station.aclose()

    station_name = str((dj or {}).get("station") or "")
    show_name = str((dj or {}).get("show") or (dj or {}).get("showName") or "")
    from station_config import StationConfig

    sc = StationConfig(base_url=cfg.get("station_base_url"))

    async def _render(text: str, voice: str) -> tuple[bytes, int]:
        tts = AdapterTTS(
            voice=voice,
            base_url=cfg.get("tts_base_url") or "",
            adapter_path=resolve_adapter(cfg.get("tts_adapter")),
            model=cfg.get("tts_model") or "",
            mode=str(cfg.get("tts_mode", "cloud")),
        )
        pcm = bytearray()
        try:
            stream = tts.synthesize(text)
            async for ev in stream:
                pcm.extend(ev.frame.data.tobytes())
        finally:
            await tts.aclose()
        return bytes(pcm), tts.sample_rate

    # One persona when asked, the roster when not — the panel stages one at
    # a time now, so the operator watches progress instead of wondering
    # whether the button took.
    only = str(request.query.get("persona") or "")
    results, ids = [], []
    # The roster plus the station itself: with no DJ on air the machine
    # answers in the operator's configured default voice rather than
    # borrowing whichever persona's clip sorts first — a named DJ who is
    # not actually there is a small lie the caller can hear.
    roster = list(personas) + [{
        "id": greetings.STATION_ID, "name": "The station (no DJ live)",
        "voice": str(cfg.get("tts_voice") or ""),
    }]
    for p in roster:
        pid = str(p.get("id") or "")
        if not pid:
            continue
        ids.append(pid)
        if only and pid != only:
            continue
        name = str(p.get("name") or pid)
        if pid == greetings.STATION_ID:
            voice = str(p.get("voice") or "")
            text = greetings.greeting_text_for(pid, cfg, station_name, "",
                                               show_name)
        else:
            voice = await sc.voice_for(pid)
            text = greetings.greeting_text_for(pid, cfg, station_name, name,
                                               show_name)
        key = greetings.render_key(text, voice, str(cfg.get("tts_mode", "")),
                                  str(cfg.get("tts_adapter") or ""))
        if not greetings.needs_render(pid, key):
            results.append({"id": pid, "name": name, "ok": True,
                            "skipped": True})
            continue
        try:
            pcm, rate = await _render(text, voice)
            if not pcm:
                raise ValueError("the backend returned no audio")
            greetings.write_clip(pid, key, text, voice, pcm, rate)
            a_pcm, a_rate = await _render(greetings.ACK_TEXT, voice)
            if a_pcm:
                import wave as _wave

                ack = greetings.ack_path(pid)
                ack.parent.mkdir(parents=True, exist_ok=True)
                with _wave.open(str(ack), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(int(a_rate))
                    w.writeframes(a_pcm)
            results.append({"id": pid, "name": name, "ok": True})
        except Exception as e:                                # noqa: BLE001
            results.append({"id": pid, "name": name, "ok": False,
                            "error": str(e)[:200]})
    if not only:
        greetings.drop_stale(ids)
    await sc.aclose()

    ok = sum(1 for r in results if r.get("ok"))
    log.info("voicemail staging: %d/%d personas ok", ok, len(results))
    return _cors(request, web.json_response({"ok": ok == len(results),
                                             "results": results}))


async def handle_voicemail_clip(request: web.Request) -> web.StreamResponse:
    """One staged greeting, as audio, for the panel's Play button. Admin —
    the clips are the operator's own renders, but the list of what exists is
    nobody else's business."""
    if not _write_allowed(request):
        raise web.HTTPUnauthorized()
    path = greetings.clip_path(request.match_info.get("persona_id", ""))
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={
        "Cache-Control": "no-store", "Content-Type": "audio/wav"})


async def handle_voicemail_clip_delete(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return _refuse(request)
    pid = request.match_info.get("persona_id", "")
    greetings.clip_path(pid).unlink(missing_ok=True)
    greetings.ack_path(pid).unlink(missing_ok=True)
    index = greetings.read_index()
    if index.pop(str(pid), None) is not None:
        greetings._write_index(index)
    log.info("voicemail greeting deleted: %s", pid)
    return _cors(request, web.json_response({"ok": True}))


async def handle_voicemail_override(request: web.Request) -> web.Response:
    """Set (or clear, with empty text) one persona's own greeting line."""
    if not _write_allowed(request):
        return _refuse(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    pid = request.match_info.get("persona_id", "")
    greetings.set_override(pid, str((body or {}).get("text") or ""))
    return _cors(request, web.json_response({"ok": True}))


async def handle_voicemail_messages(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return _refuse(request)
    return _cors(request, web.json_response(
        {"messages": vm_deliver.held_messages()}))


async def handle_voicemail_clear(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return _refuse(request)
    vm_deliver.clear_messages()
    return _cors(request, web.json_response({"ok": True, "messages": []}))
