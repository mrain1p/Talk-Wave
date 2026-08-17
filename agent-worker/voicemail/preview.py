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


async def resolve(station, cfg: dict, transcript: str) -> dict:
    """The ONE action this message asks for, resolved to something executable.

    Same bounded-LLM shape as deliver._triage, narrowed to what the soundbite
    flow supports: queue a specific record, put a named DJ's show on air (only
    when the operator's allow_takeover switch — the SAME one the live line
    rides — is on), or nothing. Every failure path lands on NO_ACTION — a
    message that airs with no side effect loses nobody anything, while a
    guessed side effect is the thing the preview exists to prevent.
    """
    text = " ".join(str(transcript or "").split())
    if not text:
        return dict(NO_ACTION)

    takeover_ok = bool(cfg.get("allow_takeover"))
    try:
        from livekit.agents.llm import ChatContext

        from call.providers import build_llm

        llm = build_llm(cfg)
        prompt = (
            "A radio station's soundbite line took this recorded message:\n"
            f"  {text[:600]}\n\n"
            "Pick ONE action. Answer with bare JSON only:\n"
            '  {"action": "queue", "query": "<artist and/or title to search '
            'the library for>"}  when it asks for a specific piece of music\n'
            + ('  {"action": "takeover", "who": "<the DJ or show they '
               'named>"}  when it asks for a different DJ or show to come '
               "on\n" if takeover_ok else "")
            + '  {"action": "none"}  when it asks for neither.'
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

    if action != "queue":
        return dict(NO_ACTION)
    query = str(verdict.get("query") or "").strip()
    if not query:
        return dict(NO_ACTION)

    # The station's search needs EVERY word to match, so "Landslide by
    # Fleetwood Mac" — the way a caller actually says it — returns nothing.
    # The live line's wrapper already learned this; same variants, same
    # order. Found here by the first NAS probe of this prompt: the model's
    # query kept the "by" and a track the library holds five times over
    # previewed as a mere request.
    from call.tools.rows import _query_variants

    hits = []
    for variant in _query_variants(query):
        try:
            hits = await station.search_library(variant)
        except Exception as e:                                # noqa: BLE001
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
    # The library has no match: fall to the public request pipe, which the
    # station resolves its own way — and the label says so, because "Queue:"
    # would promise a record the search just failed to find.
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
