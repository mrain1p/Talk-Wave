"""What a caller can do to a record's STANDING, rather than to what plays next.

Split out of `music.py` at 0.10.132, when the never-play pair arrived and that
file went past the size ceiling. The seam is a real subject and not a size
convenience: nothing here changes the running order. A like, an un-like and a
never-play all leave the queue exactly as it was and change how the station
feels about a record afterwards — the like tools by a count, never-play by
removing it from selection for good.

They also share a shape the queue tools don't: every one of them acts on the
track playing RIGHT NOW, resolved here rather than passed in, because "this
one" is how a caller refers to it and asking the model to carry an id across
turns is how it ends up hearting the wrong song.
"""

from __future__ import annotations

import logging

from station import StationClient

from ..actions import CallActions
from .registry import library_search_needs_mcp
from .rows import _fmt_track

log = logging.getLogger("callin.agent")


async def _track_on_air(station: StationClient) -> tuple[dict, str]:
    """The record playing now, and its song id — one read, one shape.

    Four tools resolved this separately and two of them read a different key,
    which is how an action could land on nothing while the station was clearly
    playing something.
    """
    np = await station.now_playing()
    track = (np or {}).get("nowPlaying") or {}
    return track, str(track.get("id") or track.get("songId") or "")


def build_curation_tools(cfg: dict, station: StationClient,
                         actions: CallActions) -> list:
    """Likes, un-likes, and the never-play list."""
    from livekit.agents import llm as lk_llm

    tools: list = []

    if cfg.get("allow_favorite"):
        @lk_llm.function_tool(name="subwave_like_track")
        async def like_track() -> str:
            """Add a like to the track playing RIGHT NOW — the same heart a
            listener taps in the app. Use it when the caller says they love
            this one, or asks to favourite what's on. It likes the CURRENT
            record only: there is no way to like some other track from here,
            and no un-like, so don't offer either."""
            if actions.at_limit():
                return actions.refusal()
            track, song_id = await _track_on_air(station)
            res = await station.like_track(song_id)
            if not res.get("ok"):
                return (
                    f"That like didn't go through: "
                    f"{res.get('error') or 'the station refused it'}. "
                    "Tell the caller plainly — don't claim it worked."
                )
            name = _fmt_track(track) if track.get("title") else "the current track"
            actions.note("like", name)
            if res.get("alreadyLiked"):
                return (
                    f"Already liked — {name} was on the board already, so the count "
                    "didn't move. Say so warmly."
                )
            count = res.get("count")
            tail = f" That's {count} now." if isinstance(count, int) and count else ""
            return f"Done — added a like to {name}.{tail} Say it back in your own voice."

        tools.append(like_track)

    if cfg.get("allow_unfavorite") and not library_search_needs_mcp():
        @lk_llm.function_tool(name="subwave_unlike_track")
        async def unlike_track() -> str:
            """Remove the operator's heart from the track playing RIGHT NOW.
            Admin only. This undoes the OPERATOR's own curation heart on the
            current record — not a listener's public like, which cannot be
            undone. Use it when a signed-in operator asks to un-favourite what's
            on. Likes the current track only; there is no arbitrary song here."""
            if actions.at_limit():
                return actions.refusal()
            track, song_id = await _track_on_air(station)
            res = await station.unlike_track(song_id)
            if not res.get("ok"):
                return (
                    f"That didn't go through: "
                    f"{res.get('error') or 'the station refused it'}. "
                    "Tell the caller plainly — don't claim it worked."
                )
            name = _fmt_track(track) if track.get("title") else "the current track"
            actions.note("unlike", name)
            return f"Done — took the heart off {name}. Say it back in your own voice."

        tools.append(unlike_track)

    # --- never play this again --------------------------------------------
    # The furthest-reaching thing a caller can do to the LIBRARY, and the
    # counterpart to the takeover's reach over the schedule: a skip is over in
    # three minutes, this is permanent and silent. The station does more than
    # add a row — it pulls the track out of the upcoming queue and rebuilds the
    # fallback playlist — so "never again" really is never, for every listener,
    # from one caller's sentence. Off by default, admin tier, and the unblock
    # rides the same switch deliberately: a caller who can impose a permanent
    # judgement on the operator's library must be able to lift one too, or the
    # only way back is the operator noticing.
    if cfg.get("allow_never_play") and not library_search_needs_mcp():
        @lk_llm.function_tool(name="subwave_never_play_track")
        async def never_play_track() -> str:
            """Ban the track playing RIGHT NOW from the station for good — it
            comes out of the queue and is never selected again. Use ONLY when
            the caller has clearly asked for exactly that ("never play this
            again", "take this off the station"). This is PERMANENT and affects
            every listener, so do not reach for it to skip something: skipping
            is subwave_skip_track. If they only mean they dislike it, say so
            back instead of banning it."""
            if actions.at_limit():
                return actions.refusal()
            track, song_id = await _track_on_air(station)
            if not song_id:
                return (
                    "Nothing identifiable is on air, so there's nothing to ban. "
                    "Say you can't see what's playing rather than guessing."
                )
            name = _fmt_track(track) if track.get("title") else "the current track"
            res = await station.block_track(song_id)
            if not res.get("ok"):
                return (
                    f"That didn't go on the never-play list: "
                    f"{res.get('error') or 'the station refused it'}. "
                    "Tell the caller plainly — do not claim it worked."
                )
            actions.note("never-play", name)
            if res.get("already"):
                return (
                    f"{name} was already on the never-play list — nothing "
                    "changed, and it was never going to come round again "
                    "anyway. Say so plainly."
                )
            # The station drops it from the queue as part of the same write, so
            # a caller who asks "is it off now?" is owed a yes, not a "should
            # be". What it does NOT do is stop the record playing right now.
            purged = res.get("purged")
            extra = ""
            if isinstance(purged, int) and purged:
                extra = f" It also came out of the queue ({purged} spot(s))."
            return (
                f"Done — {name} is on the never-play list and the station won't "
                f"select it again.{extra} It does NOT stop the copy playing "
                "right now; that plays out unless you also skip it. Say what "
                "actually happened, and remember this is permanent."
            )

        tools.append(never_play_track)

        @lk_llm.function_tool(name="subwave_allow_track_again")
        async def allow_track_again(id: str = "") -> str:
            """Take a track back OFF the never-play list, so the station may
            select it again. With no id it lifts the ban on whatever is playing
            now. Use when a caller asks to undo a ban — including one they
            didn't set."""
            track_id = str(id or "").strip()
            name = "that track"
            if not track_id:
                track, track_id = await _track_on_air(station)
                if track.get("title"):
                    name = _fmt_track(track)
            if not track_id:
                return (
                    "No track id, and nothing identifiable on air to work from. "
                    "Ask which record they mean and search for it first."
                )
            if actions.at_limit():
                return actions.refusal()
            res = await station.unblock_track(track_id)
            if not res.get("ok"):
                return (
                    f"That didn't come off the list: "
                    f"{res.get('error') or 'the station refused it'}. "
                    "Tell the caller plainly — do not claim it worked."
                )
            actions.note("never-play lifted", name)
            if res.get("already"):
                return (
                    f"{name} wasn't on the never-play list in the first place, "
                    "so nothing changed. Say so rather than implying you undid "
                    "something."
                )
            return (
                f"Done — {name} is off the never-play list and the station can "
                "pick it again. It is NOT queued: this only makes it eligible. "
                "Say it back in your own words."
            )

        tools.append(allow_track_again)

    return tools
