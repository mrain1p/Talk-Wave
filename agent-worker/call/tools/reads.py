"""The station reads a line without MCP can still make.

On calls the station's MCP server serves `subwave_now_playing` and
`subwave_station_state` through the allowlist. The text line carries no MCP
by design — and on 2026-08-27 that meant a caller asking for tracks "similar
to my current queue" was answered from a guess: the DJ had no way to LOOK,
missed the mark, spent half the call's action budget undoing the miss, and
finished by inventing a station rule ("it only holds one track at a time")
to explain a duplicate it could not see. It even reached for
subwave_station_state and was told "no such tool".

These wrappers serve the same two reads over the station REST client the
line already holds, under the same names, so every prompt rule that names
them holds on both mouths. Built only where MCP is absent — the chat —
because building them beside a live MCP session would put two tools on one
name. A call that loses its MCP handshake is still blind; giving calls this
fallback needs a name-clash answer first, and is recorded as its own piece
of work rather than half-done here.
"""

from __future__ import annotations

from livekit.agents import llm as lk_llm

from station import StationClient

from .registry import library_search_needs_mcp
from .rows import _fmt_track


def build_read_tools(cfg: dict, station: StationClient) -> list:
    """The two station reads, locally served. Empty without credentials:
    /state and /now-playing are admin REST here, so an uncredentialed
    wrapper could only ever answer nothing — and a tool that always answers
    nothing teaches the DJ the station is empty."""
    if library_search_needs_mcp():
        return []

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
    return tools
