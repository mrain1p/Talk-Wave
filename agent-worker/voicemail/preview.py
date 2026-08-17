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


# Tools that only READ — a look at the library, the lyrics, the queue. A
# recorded message that trips one has nothing to AIR (there is no conversation
# to hand the answer back to), so a captured read stages nothing.
_READ_TOOLS = {
    "subwave_current_lyrics", "subwave_search_library", "subwave_recent_tracks",
    "subwave_search_by_sound", "subwave_more_like_this", "subwave_browse_library",
    "subwave_station_favourites", "subwave_already_played", "subwave_request_status",
}

# The plain-words line the caller approves, per tool. This IS the receipts
# discipline for the actions that carry no track to pin: the caller sees what
# SEND will do before they send it.
_LABELS = {
    "subwave_skip_track": lambda a: "Skip the track playing now (everyone hears the cut)",
    "subwave_like_track": lambda a: "Like the track playing now",
    "subwave_unlike_track": lambda a: "Un-like the track playing now",
    "subwave_never_play_track": lambda a: "Never play the current track again",
    "subwave_allow_track_again": lambda a: "Take the current track off the never-play list",
    "subwave_cancel_takeover": lambda a: "Cancel the show takeover — back to the schedule",
    "subwave_clear_genre_lock": lambda a: "Lift the genre lock",
    "subwave_dj_announce": lambda a: f"Announce on air: “{str(a.get('message') or '').strip()[:100]}”",
    "subwave_run_skill": lambda a: f"Run the {a.get('name') or 'station'} segment on air",
    "subwave_dj_segment": lambda a: f"Fire the {a.get('type') or 'station'} beat on air",
    "subwave_genre_lock": lambda a: f"Lock the station to {a.get('genres') or 'a genre'} for a while",
    "subwave_cancel_queued_track": lambda a: f"Take “{a.get('title') or a.get('id') or 'that track'}” back out of the queue",
    "subwave_queue_track": lambda a: f"Queue “{a.get('title') or 'the track'}”",
}


def _label_for(name: str, args: dict) -> str:
    fn = _LABELS.get(name)
    try:
        return fn(args) if fn else name.replace("subwave_", "").replace("_", " ")
    except Exception:                                         # noqa: BLE001
        return name.replace("subwave_", "").replace("_", " ")


async def build_action_tools(cfg: dict, station):
    """The SAME permission-gated tool surface a live call and the text line
    build, reused WHOLE — so the studio can stage any tool the caller's tier
    allows, and send can replay it through the exact wrapper the phone runs,
    rather than a fourth reimplementation that would drift. Returns
    (tools, actions); `actions` is the per-message ledger the wrappers write
    their receipts into. `cfg` must already be permissions_for-resolved.
    """
    from call.actions import CallActions
    from call.air import OnAirGuard
    from call.tools import (build_curation_tools, build_discovery_tools,
                            build_library_tools, build_on_air_tools)

    actions = CallActions(int(cfg.get("max_actions_per_call") or 0))
    # No live room to collide with, so the overlap guard is off — the same
    # shape the text line builds these in (chat/session.py builds a disabled
    # guard for exactly this reason).
    guard = OnAirGuard(station, {"avoid_on_air_overlap": False})
    skills = []
    if cfg.get("allow_skills"):
        try:
            skills = [s for s in (str(x.get("kind") or x.get("name") or "")
                                  for x in await station.list_skills()) if s]
        except Exception:                                     # noqa: BLE001
            skills = []
    tools = (
        build_library_tools(cfg, station, actions)
        + build_discovery_tools(cfg, station, actions)
        + build_curation_tools(cfg, station, actions)
        + build_on_air_tools(cfg, station, actions, guard, guarded=False,
                             skills=skills)
    )
    return tools, actions


async def _capture_tool_call(cfg: dict, text: str, tools: list):
    """Run ONE model pass over the message with the gated tools, and read back
    the tool it WOULD call — WITHOUT running it (the /test/llm probe's trick:
    read tool_calls off the stream, invoke nothing). The model that resolves a
    live caller's words to a tool call resolves a recorded message the same
    way; send is the only place anything runs. Returns (name, args) or None.
    """
    from livekit.agents.llm import ChatContext

    from call.providers import build_llm

    llm = build_llm(cfg)
    ctx = ChatContext.empty()
    ctx.add_message(role="system", content=(
        "A radio station's soundbite line took a recorded message from a "
        "caller. If it asks you to DO something you have a tool for, call that "
        "ONE tool with the caller's own words. If it is just a message with "
        "nothing to action, call no tool at all."))
    ctx.add_message(role="user", content=text[:600])
    calls: list = []
    try:
        stream = llm.chat(chat_ctx=ctx, tools=tools)
        async for chunk in stream:
            delta = getattr(chunk, "delta", None)
            if delta and getattr(delta, "tool_calls", None):
                calls.extend(delta.tool_calls)
        await stream.aclose()
    except Exception as e:                                    # noqa: BLE001
        log.warning("draft tool triage failed (%s) — no action previewed", e)
        return None
    finally:
        try:
            await llm.aclose()
        except Exception:                                     # noqa: BLE001
            pass
    if not calls:
        return None
    call = calls[0]
    try:
        args = json.loads(getattr(call, "arguments", "") or "{}")
    except Exception:                                         # noqa: BLE001
        args = {}
    return str(getattr(call, "name", "")), (args if isinstance(args, dict) else {})


async def resolve(station, cfg: dict, transcript: str, tier: str = "open") -> dict:
    """The action this message asks for, resolved through the SAME tools a
    live call and the text line use — the whole permission-gated surface, not
    a reimplemented subset.

    The medium is what differs, not the tools. A call runs the tool the instant
    the model calls it; the soundbite runs a CAPTURE pass here (the model picks
    the one tool it would call, and we read that call without running it) and
    STAGES it into the draft. Send replays exactly that call through the exact
    wrapper the phone runs. So which actions a message can set in motion is
    decided by permissions_for(cfg, tier) — the same gate a call reads at
    pickup — and by nothing else.

    Music and a takeover keep their pinned-receipt previews (a request to its
    resolved track, a show to its id) because those CAN be shown exactly; every
    other tool stages by name and args with a plain-words label the caller
    approves. A read, or no tool at all, stages nothing — a message that airs
    with no side effect loses nobody anything, which is the thing the preview
    exists to protect.
    """
    import settings as settings_store

    text = " ".join(str(transcript or "").split())
    if not text:
        return dict(NO_ACTION)

    # Resolved for THIS caller's tier, the same call a live call makes at
    # pickup. "off" is truthy as a raw string, so collapsing to a bool here is
    # what stops a switched-off action reading as on.
    cfg = settings_store.permissions_for(cfg, tier)
    tools, _actions = await build_action_tools(cfg, station)
    built = {t.info.name for t in tools}

    captured = await _capture_tool_call(cfg, text, tools)
    if not captured:
        return dict(NO_ACTION)
    name, args = captured

    # Airtight gate: route ONLY a tool this caller's tier actually built. A
    # captured read, an empty call, or a name that was never offered (a model
    # reaching for a tool it wasn't given) all stage nothing — the permission
    # set alone decides what a message can do, and nothing downstream re-grants
    # it. Send re-gates the same way (air._run_staged_tool).
    if not name or name not in built or name in _READ_TOOLS:
        return dict(NO_ACTION)

    if name == "subwave_request_song":
        return await stage_music_action(
            station, cfg, str(args.get("request") or text))
    if name == "subwave_takeover_show":
        who = str(args.get("show") or "").strip()
        return await _resolve_takeover(station, who) if who else dict(NO_ACTION)
    return {"kind": "tool", "name": name, "args": args,
            "label": _label_for(name, args)}


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
