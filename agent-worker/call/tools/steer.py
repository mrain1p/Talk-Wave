"""The steer: lean what the whole station picks and says, for a stretch.

SUB/WAVE 1.31's own control (`POST /steer`), the sibling of the takeover and
the thing a caller asks for most naturally of all — "something more upbeat for
the next hour", "90s hip hop till the next show", "run the Pokemon playlist for
a bit". A takeover puts a named DJ on air that listeners can hear; a steer is
quieter, leaning every later pick and every talk break with nothing on air to
say so. It opens with five picked tracks at the end of the record playing and
lets go when its window lapses — 15 to 720 minutes, or the next grid boundary.

Two things the station decides that this file only relays: starting a steer
ENDS a running takeover (routes/steer.ts), and a station older than 1.31 has
no route at all, which is reported as a capability gap rather than a failure —
the genre lock's posture, so the DJ says it can't instead of retrying or
pinning a show to fake it.

Its own module because broadcast.py sits one tool under the length ceiling;
build_on_air_tools calls in here the way music calls the playlist tool.
"""
from __future__ import annotations

from call.actions import CallActions
from station import StationClient

from .playlists import _candidates, _txt


def _window(asked: int) -> tuple[int, str]:
    """The minutes the station will take, and the correction to say if that
    is not what the caller asked for (the takeover's own idiom)."""
    want = int(asked or 0) or 60
    got = max(StationClient.STEER_MIN_MINUTES,
              min(StationClient.STEER_MAX_MINUTES, want))
    said = ""
    if got != want:
        said = (f" They asked for {want}; the desk only allows "
                f"{StationClient.STEER_MIN_MINUTES}–"
                f"{StationClient.STEER_MAX_MINUTES} minutes, so it's {got}. "
                "Say the real number.")
    return got, said


def _no_steer_here(actions: CallActions) -> str:
    # Not a failure and not the caller's fault: this station runs a release
    # without the control. "That didn't work" sends the DJ round again.
    actions.denied("unavailable", "a steer — this station's software "
                   "doesn't have one yet")
    return (
        "This station's software doesn't have a steer yet, so there is "
        "nothing to switch on. Tell the caller it isn't something you can do "
        "here — do NOT retry, and do NOT improvise it by pinning a show or "
        "queueing a run of records as if the station were steered. You can "
        "still take a request for one record in that direction."
    )


async def _resolve_playlist(station: StationClient, want: str) -> tuple:
    """(playlist id, its name, "") for a playlist the caller named, or
    (None, "", the sentence to hand the DJ) when it did not resolve. No name
    asked for resolves to nothing without a word."""
    if not want:
        return None, "", ""
    lists = await station.playlists()
    if lists is None:
        return None, "", ("I can't read the station's playlists right now, so I "
                          "can't steer by one. Steer by the caller's words "
                          "instead, or tell them plainly.")
    hits = _candidates(lists, want)
    if not hits:
        seen = ", ".join(f"\"{_txt(p.get('name'), 60)}\"" for p in lists[:6])
        return None, "", (f"No playlist called \"{want}\" on the station — it has "
                          f"{seen or 'none'}. If one of those is what the caller "
                          "means, call again with that exact name; otherwise "
                          "steer by their words alone.")
    if len(hits) > 1:
        seen = ", ".join(f"\"{_txt(p.get('name'), 60)}\"" for p in hits[:6])
        return None, "", ("More than one playlist answers to that — NOTHING "
                          f"steered yet: {seen}. Ask the caller which, then call "
                          "again with the exact name.")
    return (str(hits[0].get("id") or ""), _txt(hits[0].get("name"), 80) or want, "")


def build_steer_tools(cfg: dict, station: StationClient,
                      actions: CallActions) -> list:
    """The two steer tools, or [] when the switch is off."""
    if not cfg.get("allow_steer"):
        return []
    from livekit.agents import llm as lk_llm

    tools: list = []

    @lk_llm.function_tool(name="subwave_steer")
    async def steer(direction: str = "", minutes: int = 60,
                    until_schedule: bool = False, playlist: str = "") -> str:
        """Lean what the WHOLE STATION plays and says for a while — "more
        upbeat for the next hour", "90s hip hop till the next show", "keep it
        mellow". `direction` is the lean in the caller's own words (a
        sentence, not a track). `playlist` is the name of one of the
        station's own playlists when they asked for one to run for a stretch;
        either or both. `minutes` defaults to an hour — pass more ONLY if they
        asked for longer. Pass `until_schedule=True` INSTEAD when they want it
        to run until the next show starts, and `minutes` is ignored. This
        changes what EVERYONE hears and outlasts the call, and starting it
        ENDS any show takeover that is running. One record in that direction
        is a REQUEST, not a steer."""
        if actions.at_limit():
            return actions.refusal()
        text = " ".join(str(direction or "").split())
        want_list = str(playlist or "").strip()
        if not text and not want_list:
            return ("Nothing to steer by. Ask the caller which way they want "
                    "the station leaned — a mood, a style, an era, or one of "
                    "the station's playlists — and try again.")
        playlist_id, pname, miss = await _resolve_playlist(station, want_list)
        if miss:
            return miss

        # Said before the write: the station ends a running takeover when a
        # steer starts, and a caller who asked for "more upbeat" has not asked
        # for the show change that comes with it. The DJ can only say so if it
        # knew what was pinned.
        pinned = ((await station.schedule()) or {}).get("override") or {}
        was_pinned = bool(pinned.get("showId") or pinned.get("until")
                          or pinned.get("expiresAt"))

        lean = text if not pname else (f"{text} (playlist: {pname})" if text
                                       else f"playlist: {pname}")
        if until_schedule:
            result = await station.steer(prompt=text, playlist_id=playlist_id,
                                         until="schedule-change")
            window, corrected = 0, ""
        else:
            window, corrected = _window(minutes)
            result = await station.steer(prompt=text, playlist_id=playlist_id,
                                         minutes=window)
        if result.get("unsupported"):
            return _no_steer_here(actions)
        if not result.get("ok"):
            return actions.station_refused(
                result, "That steer didn't go through. If the station said "
                "nothing is eligible, the lean is too narrow for this library "
                "— ask for a broader one")
        how_long = ("until the schedule would have changed anyway"
                    if until_schedule else f"for the next {window} minutes")
        actions.note("steer", f"{lean} {how_long}")
        queued = result.get("queued")
        opening = (f" The station has already lined up {int(queued)} opening "
                   "tracks for it." if isinstance(queued, int) and queued else "")
        ended = (" This ENDED the show takeover that was running — the "
                 "schedule's own show is back under the steer. Say so."
                 if was_pinned else "")
        return (
            f"Done — the station is steered toward \"{lean}\" {how_long}, "
            f"then it lets go by itself.{corrected}{opening}{ended} It takes "
            "hold at the end of the record playing now, not this second, so "
            "don't say it has already changed. Everyone listening is affected, "
            "and it keeps running after this call — say so in your own words, "
            "and don't promise any particular track."
        )

    tools.append(steer)

    @lk_llm.function_tool(name="subwave_clear_steer")
    async def clear_steer() -> str:
        """Lift a steer early and let the station pick freely again. Use
        when the caller asks to undo one, or asks for normal programming
        back. A steer is not a show takeover — that is
        subwave_cancel_takeover."""
        if actions.at_limit():
            return actions.refusal()
        status = await station.steer_status()
        if status and not status.get("steer"):
            return ("Nothing is steering the station right now — there's no "
                    "steer to lift. Say so rather than implying you undid "
                    "something. If a SHOW is pinned, that is a takeover: "
                    "subwave_cancel_takeover, and check they mean that.")
        result = await station.clear_steer()
        if result.get("unsupported"):
            return _no_steer_here(actions)
        if not result.get("ok"):
            return actions.station_refused(result, "That didn't lift")
        actions.note("steer lifted", "the station picks freely again")
        pulled = result.get("removed")
        tail = (f" {int(pulled)} of its opening tracks that hadn't aired were "
                "pulled from the queue." if isinstance(pulled, int) and pulled else "")
        return (
            f"Done — the steer is off and the station picks freely again.{tail} "
            "It lands at the end of the current record rather than this "
            "second. Say so in your own words."
        )

    tools.append(clear_steer)
    return tools
