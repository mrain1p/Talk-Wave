"""Review-time intelligence: what the machine heard, and what send would do.

The point of previewing is the receipts discipline moved EARLIER: the action
shown to the caller is resolved all the way to a track id before they approve
it, and send executes exactly that record — never a re-interpretation of the
same words at air time. Two resolutions of one phrase is how a caller approves
Landslide and airs something else.

Transcription runs here too (the same STT the calls use), because the
transcript is half of what the caller reviews — and the mishears matter less
than they look: base.en garbled connective speech twice on test night and
still carried every content word ("Landslide by Fleetwood Mac", exactly).
A caller who sees a wrong transcript re-records; that loop is the corrector.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger("callin.voicemail")

NO_ACTION = {"kind": "none", "label": "No station action — the message just plays"}


async def transcribe(cfg: dict, wav_path) -> str:
    """One bounded read of the mastered clip through the configured STT.
    Empty on any failure — the caller sees a blank transcript and records
    again, which beats a 500 that strands their recording."""
    try:
        from livekit import rtc

        from call.providers import build_stt
        from voicemail.capture import read_wav

        pcm, rate = read_wav(wav_path)
        stt = build_stt(cfg)
        try:
            frame = rtc.AudioFrame(data=pcm, sample_rate=rate, num_channels=1,
                                   samples_per_channel=len(pcm) // 2)
            ev = await stt.recognize(frame)
            return (ev.alternatives[0].text or "").strip() if ev.alternatives else ""
        finally:
            try:
                await stt.aclose()
            except Exception:                                 # noqa: BLE001
                pass
    except Exception as e:                                    # noqa: BLE001
        log.warning("draft transcription failed: %s", e)
        return ""


async def resolve(station, cfg: dict, transcript: str, tier: str = "open") -> dict:
    """The ONE action this message asks for, resolved to something executable.

    The SAME tools and the SAME permission gates the live line and the text
    line use, resolved for THIS caller's tier exactly as a call is
    (settings.permissions_for). Only the medium differs: a call runs the tool
    the instant the model calls it; the soundbite STAGES the resolved action
    into the draft for the caller to approve, and send runs it. So the tool
    set is consistent across all three doors — a music ask rides
    allow_requests, an exact-id queue rides allow_exact_queue, a takeover
    rides allow_takeover — while the execution is not, because a preview has
    to show what WILL happen before it does.

    Every failure path lands on NO_ACTION — a message that airs with no side
    effect loses nobody anything, while a guessed side effect is the thing the
    preview exists to prevent.
    """
    import settings as settings_store

    text = " ".join(str(transcript or "").split())
    if not text:
        return dict(NO_ACTION)

    # Resolved for the caller's tier, the same call a live call makes at
    # pickup — so an action the tiers would not grant this caller is not on
    # offer to their message either, and "off" (truthy as a raw string) can
    # never read as on.
    cfg = settings_store.permissions_for(cfg, tier)
    music_ok = bool(cfg.get("allow_requests"))
    takeover_ok = bool(cfg.get("allow_takeover"))
    # Nothing this caller may set in motion: don't spend a completion to be
    # told so, and never wake the model on an empty offer.
    if not (music_ok or takeover_ok):
        return dict(NO_ACTION)

    try:
        from livekit.agents.llm import ChatContext

        from call.providers import build_llm

        llm = build_llm(cfg)
        # Only the actions this caller may actually take are offered — the same
        # discipline that keeps the live line from describing a tool it will
        # then refuse.
        options = []
        if music_ok:
            options.append(
                '  {"action": "queue", "query": "<what they want to hear — a '
                "title and/or artist, OR a description: a mood, a genre, an "
                "era, 'something like this'>\"}  when it asks for music of ANY "
                "kind, named or described\n")
        if takeover_ok:
            options.append(
                '  {"action": "takeover", "who": "<the DJ or show they '
                'named>"}  when it asks for a different DJ or show to come '
                "on\n")
        options.append('  {"action": "none"}  when it asks for none of these.')
        prompt = (
            "A radio station's soundbite line took this recorded message:\n"
            f"  {text[:600]}\n\n"
            "Pick ONE action. Answer with bare JSON only:\n"
            + "".join(options)
        )
        chunks = []
        chat_ctx = ChatContext()
        chat_ctx.add_message(role="user", content=prompt)
        try:
            async with llm.chat(chat_ctx=chat_ctx) as st:
                async for chunk in st:
                    delta = getattr(chunk, "delta", None)
                    if delta and getattr(delta, "content", None):
                        chunks.append(delta.content)
        finally:
            try:
                await llm.aclose()
            except Exception:                                 # noqa: BLE001
                pass
        raw = "".join(chunks).strip()
        verdict = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    except Exception as e:                                    # noqa: BLE001
        log.warning("draft triage failed (%s) — no action previewed", e)
        return dict(NO_ACTION)

    action = str(verdict.get("action") or "none").lower()
    if action == "takeover" and takeover_ok:
        who = str(verdict.get("who") or "").strip()
        return await _resolve_takeover(station, who) if who else dict(NO_ACTION)
    if action == "queue" and music_ok:
        return await stage_music_action(station, cfg, str(verdict.get("query") or ""))
    return dict(NO_ACTION)


async def stage_music_action(station, cfg: dict, query: str) -> dict:
    """A music ask, staged as the SAME queue-or-request the live line makes.

    This is the soundbite half of subwave_request_song / subwave_queue_track,
    minus the immediate execution: a named track the library holds is pinned
    to its id (kind "queue" — send airs exactly that record, the receipts
    discipline), and a mood, a genre, an era, "something like this", or a name
    the library misses becomes a request the station's picker resolves at send
    (kind "request"). An exact-id queue rides allow_exact_queue; everything
    else rides the request pipe — the same split the live tools draw.

    The mood check is the live line's own (rows.looks_like_a_vibe): a
    description never matches a title literally, so searching the library for
    it is a doomed round-trip. Skipping straight to the request pipe is why
    "play something a bit lighter" now stages a request instead of the "no
    action asked for" the old named-track-only prompt read it as (RID 280,
    2026-08-17).
    """
    from call.tools.rows import _query_variants, looks_like_a_vibe

    query = " ".join(str(query or "").split())
    if not query:
        return dict(NO_ACTION)

    # An exact-id pin only when the operator granted it AND the words name a
    # track. A description is sent to the request pipe unread — searching a
    # feeling by name is the round-trip the live line already learned to skip.
    if bool(cfg.get("allow_exact_queue")) and not looks_like_a_vibe(query):
        # The station's search needs EVERY word to match, so "Landslide by
        # Fleetwood Mac" — the way a caller actually says it — returns nothing;
        # the variants strip the "by" the same way the live wrapper does.
        hits = []
        for variant in _query_variants(query):
            try:
                hits = await station.search_library(variant)
            except Exception as e:                            # noqa: BLE001
                log.warning("draft preview search failed: %s", e)
                break
            if hits:
                break
        if hits:
            top = hits[0]
            track = {k: top.get(k) for k in ("id", "title", "artist", "album")
                     if top.get(k)}
            name = str(track.get("title") or "the track")
            if track.get("artist"):
                name += f" — {track['artist']}"
            return {"kind": "queue", "track": track, "label": f"Queue: {name}"}

    # A mood, no exact-queue grant, or the library missed: the request pipe,
    # which the station resolves its own way — the same fallback the live
    # line's request tool is. The label says "request" so an unfound record is
    # never promised as "Queue:".
    return {"kind": "request", "text": query,
            "label": f"Send as a request: “{query}”"}


async def _resolve_takeover(station, who: str) -> dict:
    """A named DJ or show, resolved to the show id send would actually pin.

    Reuses the live line's own matcher — exact id, exact name, unique
    substring, then the PEOPLE (a caller names the DJ, not the programme; the
    matcher refusing ambiguity is what keeps 'night' from picking one of two
    night shows). An unmatched name previews as no action WITH the reason,
    so the caller sees the miss before they send, not after.
    """
    try:
        from call.tools.broadcast import _match_show

        shows = (await station.schedule()).get("shows") or []
        personas = await station.personas()
    except Exception as e:                                    # noqa: BLE001
        log.warning("draft takeover resolve failed: %s", e)
        return dict(NO_ACTION)
    picked = _match_show(shows, who, personas)
    if not picked:
        return {"kind": "none",
                "label": f"No station action — couldn’t match “{who}” to a "
                         "DJ or show"}
    host = ""
    pid = str(picked.get("personaId") or "")
    for person in personas:
        if str(person.get("id") or "") == pid:
            host = str(person.get("name") or "").strip()
            break
    show = str(picked.get("name") or "that show").strip()
    return {"kind": "takeover", "showId": str(picked.get("id") or ""),
            "show": show, "who": host or who,
            "label": f"Put {host or show} on air — {show}, for the next hour"}
