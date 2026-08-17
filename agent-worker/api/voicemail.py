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
    # The custom beep, tried for real. It is played by the WORKER at pickup,
    # so a file this container can't convert fails there silently — the tone
    # plays, a warning lands in the worker's log, and from the panel the
    # setting just looks ignored. That happened; answer the question here.
    beep = {"set": False}
    beep_name = str(cfg.get("sound_vm_beep") or "")
    if beep_name.startswith("upload:"):
        from api.sounds import SOUNDS_DIR
        from voicemail.capture import _wav_as_mono16

        beep = {"set": True, "name": beep_name[len("upload:"):]}
        try:
            beep["ok"] = bool(_wav_as_mono16(
                SOUNDS_DIR / beep["name"], 24000))
            if not beep["ok"]:
                beep["error"] = "the file contains no audio"
        except Exception as e:                                # noqa: BLE001
            beep["ok"] = False
            beep["error"] = str(e)[:160]
    return _cors(request, web.json_response({
        "personas": out,
        "messages": len(vm_deliver.held_messages()),
        "beep": beep,
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


# --- the soundbite line: record → review → send ----------------------------
# GUEST endpoints, not admin: the caller is the reviewer. The line is tiered
# — open callers are refused outright (the operator's line is code-gated and
# the card greys the door for strangers), and everything a draft holds is
# deleted on every exit (see voicemail/review.py, which owns the terms).

# Hard ceiling on one upload. The widget sends 16 kHz mono (≤ ~1 MB at the
# default 30s), but master() accepts any PCM WAV and a 44.1k stereo take of
# the same message is ~5 MB — read in chunks with our own cap rather than
# through aiohttp's 1 MB default, and refuse politely past it.
_UPLOAD_CEILING = 8 * 1024 * 1024


def _guest_refuse(request: web.Request) -> web.Response:
    if request.get("auth_error"):
        return _refuse(request)
    return _cors(request, web.json_response(
        {"error": "The booth doesn't take messages on this line."},
        status=403))


def _draft_gate(request: web.Request) -> bool:
    """Who may use the studio: the SAME tier door as the classic machine.

    The first build hard-refused the open tier — and the operator's own line
    is front_access=open with allow_voicemail=open, so their strangers could
    record a take and only learn at upload that nobody would accept it. The
    machine already has the answer (allow_voicemail: open/guest/admin, the
    ladder tokens.py walks for a vm mint); one line, one door, both flows.
    """
    from api.auth import _guest_ok, caller_tier

    if not _guest_ok(request):
        return False
    cfg = settings_store.load()
    return settings_store.tier_reaches(cfg.get("allow_voicemail"),
                                       caller_tier(request))


async def handle_vm_draft_create(request: web.Request) -> web.Response:
    """One recording in, one reviewable draft out: mastered, transcribed,
    and with the action send would take already resolved to real ids."""
    if not _draft_gate(request):
        return _guest_refuse(request)

    import tempfile
    from pathlib import Path

    from api.auth import caller_tier
    from voicemail import master as vm_master
    from voicemail import preview as vm_preview
    from voicemail import review as vm_review

    body = bytearray()
    async for chunk in request.content.iter_chunked(64 * 1024):
        body.extend(chunk)
        if len(body) > _UPLOAD_CEILING:
            return _cors(request, web.json_response(
                {"error": "that recording is too large"}, status=413))
    if not body:
        return _cors(request, web.json_response(
            {"error": "no audio arrived"}, status=400))

    import secrets_store

    secrets_store.apply_to_env()
    cfg = settings_store.load()
    ceiling = max(5, int(cfg.get("voicemail_max_seconds") or 30))

    fd, raw_name = tempfile.mkstemp(suffix=".wav")
    raw = Path(raw_name)
    mastered = raw.with_suffix(".mastered.wav")
    try:
        import os as _os

        with _os.fdopen(fd, "wb") as f:
            f.write(bytes(body))
        try:
            stats = vm_master.master(raw, mastered, ceiling)
        except ValueError as e:
            return _cors(request, web.json_response(
                {"error": str(e)}, status=400))
        except Exception as e:                                # noqa: BLE001
            log.warning("draft mastering failed: %s", e)
            return _cors(request, web.json_response(
                {"error": "that file is not audio this line can play"},
                status=400))
        draft = vm_review.create(mastered, stats, caller_tier(request))
    finally:
        # Both temps, whatever happened: a create() that failed after the
        # master leaves the clip in /tmp otherwise (it did, three times,
        # while the EXDEV bug 500'd this route).
        raw.unlink(missing_ok=True)
        mastered.unlink(missing_ok=True)

    transcript = await vm_preview.transcribe(
        cfg, vm_review.audio_path(draft["id"]))
    station = StationClient(base_url=cfg.get("station_base_url"))
    try:
        action = await vm_preview.resolve(station, cfg, transcript)
    finally:
        await station.aclose()
    draft = vm_review.annotate(draft["id"], transcript=transcript,
                               action=action)

    return _cors(request, web.json_response({
        "id": draft["id"],
        "transcript": transcript,
        "sttOk": bool(transcript),
        "stats": draft.get("stats") or {},
        "action": action,
    }))


async def handle_vm_draft_audio(request: web.Request) -> web.StreamResponse:
    """The caller playing their own take back before sending it."""
    if not _draft_gate(request):
        raise web.HTTPUnauthorized()
    from voicemail import review as vm_review

    draft_id = request.match_info.get("draft_id", "")
    path = vm_review.audio_path(draft_id) if vm_review.get(draft_id) else None
    if not path or not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={
        "Cache-Control": "no-store", "Content-Type": "audio/wav"})


async def handle_vm_draft_send(request: web.Request) -> web.Response:
    """The approved draft, on air — and gone from disk either way."""
    if not _draft_gate(request):
        return _guest_refuse(request)

    import secrets_store
    from voicemail import air as vm_air
    from voicemail import review as vm_review

    draft_id = request.match_info.get("draft_id", "")
    draft = vm_review.get(draft_id)
    if not draft:
        return _cors(request, web.json_response(
            {"error": "that draft has expired — record it again"}, status=404))

    secrets_store.apply_to_env()
    cfg = settings_store.load()
    station = StationClient(base_url=cfg.get("station_base_url"))
    try:
        result = await vm_air.deliver(station, cfg, draft)
    except Exception as e:                                    # noqa: BLE001
        log.warning("soundbite delivery crashed: %s", e)
        result = {"ok": False, "backend": "none",
                  "receipt": f"delivery crashed: {e}"}
    finally:
        await station.aclose()

    # The operator's record survives the draft: same list the classic
    # machine writes, labelled with how it went out.
    vm_deliver.hold(str(draft.get("transcript") or "(no transcript)"), "",
                    delivered=f"soundbite/{result.get('backend')}",
                    note=str(result.get("receipt") or "")[:300])
    # Sent or failed, the audio does not outlive the attempt: the caller
    # still has their voice; we should not.
    vm_review.delete(draft_id)

    status = 200 if result.get("ok") else 502
    return _cors(request, web.json_response(
        {"ok": bool(result.get("ok")),
         "backend": result.get("backend"),
         "receipt": result.get("receipt")}, status=status))


async def handle_vm_draft_delete(request: web.Request) -> web.Response:
    """Re-record and abandon both land here."""
    if not _draft_gate(request):
        return _guest_refuse(request)
    from voicemail import review as vm_review

    vm_review.delete(request.match_info.get("draft_id", ""))
    return _cors(request, web.json_response({"ok": True}))


async def handle_vm_air_clip(request: web.Request) -> web.StreamResponse:
    """The mixer's one fetch. Public by design — the mixer is curl on another
    network — so the token IS the credential: unguessable, ~2 minutes, burned
    by this claim (voicemail/review.py owns those rules)."""
    from voicemail import review as vm_review

    path = vm_review.claim_air_token(request.match_info.get("token", ""))
    if not path:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={
        "Cache-Control": "no-store", "Content-Type": "audio/wav"})
