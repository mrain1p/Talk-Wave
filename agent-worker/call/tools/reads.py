"""The station reads a line without MCP can still make.

On calls the station's MCP server serves `subwave_now_playing` and
`subwave_station_state` through the allowlist. The text line carries no MCP
by design — and on 2026-08-27 that meant a caller asking for tracks "similar
to my current queue" was answered from a guess: the DJ had no way to LOOK,
missed the mark, spent half the call's action budget undoing the miss, and
finished by inventing a station rule ("it only holds one track at a time")
to explain a duplicate it could not see. It even reached for
subwave_station_state and was told "no such tool".

These wrappers serve the same reads over the station REST client the line
already holds, under the same names, so every prompt rule that names them
holds on both mouths. Built only where MCP is absent: always on the chat,
and on a call ONLY when the MCP warm-up decisively failed (call/session.py
skips the dead toolset and builds these instead) — never beside a live MCP
session, which would put two tools on one name.
"""

from __future__ import annotations

from ..actions import CallActions
from station import StationClient

from .music import _when_it_plays
from .registry import library_search_needs_mcp
from .rows import _fmt_track


def build_read_tools(cfg: dict, station: StationClient,
                     actions: CallActions | None = None) -> list:
    """The station reads, locally served. Empty without credentials:
    /state and /now-playing are admin REST here, so an uncredentialed
    wrapper could only ever answer nothing — and a tool that always answers
    nothing teaches the DJ the station is empty."""
    if library_search_needs_mcp():
        return []
    from livekit.agents import llm as lk_llm

    tools = []

    @lk_llm.function_tool(name="subwave_now_playing")
    async def now_playing() -> str:
        """What is on air RIGHT NOW — the current record and how it is
        filed. Read this before any judgement that leans on the current
        track: "more like this", "does this fit what's on", "what's
        playing"."""
        now = await station.now_playing()
        track = None
        for key in ("nowPlaying", "track", "current"):
            candidate = (now or {}).get(key)
            if isinstance(candidate, dict):
                track = candidate
                break
        if track is None and isinstance(now, dict):
            track = now
        if not isinstance(track, dict) or not (
                track.get("title") or track.get("artist")):
            return (
                "Nothing identifiable came back for what's on air — the "
                "read failed or the booth is between records. That is NOT "
                "an answer about the music: don't guess at the current "
                "record, say you can't see the booth from here."
            )
        return (
            "On air right now: " + _fmt_track(track) + ". This is the "
            "record playing THIS moment — anything queued comes after it."
        )

    tools.append(now_playing)

    @lk_llm.function_tool(name="subwave_station_state")
    async def station_state() -> str:
        """The station's live queue — what is actually waiting to play, in
        order. Read this BEFORE answering anything about the queue, before
        adding to it under a caller's constraint ("no duplicates",
        "something like what's queued"), and whenever a caller says a track
        is or isn't in it — they can see the queue, and guessing against
        someone who can see is how the DJ ends up wrong out loud."""
        state = await station.state()
        upcoming = [t for t in ((state or {}).get("upcoming") or [])
                    if isinstance(t, dict)]
        if not upcoming:
            return (
                "The queue shows nothing waiting — or the read failed, and "
                "the two look the same from here. Say what you can actually "
                "see; don't declare the queue empty as a fact about the "
                "station if the caller says otherwise."
            )
        shown = upcoming[:12]
        lines = [f"{i}. " + _fmt_track(t) for i, t in enumerate(shown, 1)]
        extra = (f"\n…and {len(upcoming) - len(shown)} more behind these"
                 if len(upcoming) > len(shown) else "")
        return (
            "Waiting in the queue, in order:\n" + "\n".join(lines) + extra
            + "\nThis is the station's whole queue — every caller's picks "
            "and the station's own, not just this call's. Read it before "
            "promising positions, and before adding anything a caller asked "
            "to keep duplicate-free."
        )

    tools.append(station_state)

    if cfg.get("allow_requests"):
        @lk_llm.function_tool(name="subwave_request_status")
        async def request_status(requestId: str = "") -> str:
            """Where a song request stands — pass the requestId a request
            tool returned, or nothing at all to check the LAST request this
            call put in. Use when a caller asks whether their request went
            in, or when it will play."""
            rid = (requestId or "").strip() or getattr(
                actions, "last_request_id", "")
            if not rid:
                return (
                    "No request id to look up — this call hasn't put one "
                    "in, and none was given. If they mean an earlier call's "
                    "request, the queue itself is the honest place to look: "
                    "subwave_station_state."
                )
            st = await station.request_status(str(rid))
            if not st:
                return (
                    "The station couldn't say where that request stands — "
                    "the read failed. That is NOT a no: don't tell them it "
                    "was lost, say you can't see it from here just now."
                )
            track = st.get("track") or st.get("matched") or {}
            if isinstance(track, dict) and track.get("title"):
                return (
                    "That request is matched: " + _fmt_track(track) + ". "
                    "It is queued, not playing yet. "
                    + _when_it_plays(st.get("queuePosition"))
                )
            if str(st.get("status") or "").lower() == "pending":
                return (
                    "Still being matched in the booth — not queued yet, and "
                    "not lost. Check again in a little while, and don't "
                    "promise a title in the meantime."
                )
            return (
                "The station doesn't know that request any more — it was "
                "pruned or lost to a restart. Stop checking it; if the "
                "caller still wants the song, put the request in again."
            )

        tools.append(request_status)

    return tools
