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
    playing something. Consolidating them did not fix the key list, though:
    `subsonic_id` is what the station actually sends and it was missing, so
    this returned "" on every call to a real station.

    Liking survived that because POST /like likes whatever is on air and the id
    is only a guard against the record changing mid-request. Un-liking needs it
    for /likes/song/:id/operator, so it refused with "nothing is playing to
    un-like right now" while the caller could plainly hear something playing —
    2026-08-16, twice in one call, on "Everyday" by Don McLean, which the same
    call had liked successfully forty seconds earlier.
    """
    np = await station.now_playing()
    track = (np or {}).get("nowPlaying") or {}
    return track, str(track.get("subsonic_id") or track.get("id")
                      or track.get("songId") or "")


async def _target_to_unlike(station: StationClient, actions, title: str,
                            artist: str) -> tuple[dict, str]:
    """Which song the caller means, in the order they are likely to mean it.

    Tying this to the current record was OUR restriction and never the
    station's: DELETE /likes/song/:id/operator takes any song id at all. A
    caller changing their mind usually does it a beat late — the record has
    moved on, or the DJ skipped it — so "un-like that one" has to survive the
    track changing underneath it. On 2026-08-16 a caller liked "Everyday" by
    Don McLean, asked twice to take it back, and was told nothing was playing.
    """
    named = " ".join(p for p in (title, artist) if p).strip()
    if named:
        try:
            for row in (await station.search_library(named) or [])[:1]:
                found = str(row.get("subsonic_id") or row.get("id") or "")
                if found:
                    return row, found
        except Exception as e:                                 # noqa: BLE001
            log.debug("could not resolve %r to un-like: %s", named, e)
    remembered = actions.last_liked
    if remembered and remembered[0]:
        return remembered[1], remembered[0]
    return await _track_on_air(station)


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
            record only; to take a heart back off, use subwave_unlike_track."""
            if actions.at_limit():
                return actions.refusal()
            track, song_id = await _track_on_air(station)
            # Remembered so "actually, un-like that" works after the record has
            # moved on — which is when a caller usually changes their mind.
            if song_id:
                actions.last_liked = (song_id, track)
            res = await station.like_track(song_id)
            if not res.get("ok"):
                return actions.station_refused(res, "That like didn't go through")
            name = _fmt_track(track) if track.get("title") else "the current track"
            # The no-op check FIRST: an already-liked track changed nothing,
            # so it must not spend a budget slot or fire a "Liked" receipt
            # card that says a fresh action landed (top-down review,
            # 2026-08-28). note() both bills and cards; a non-event does
            # neither. Same for the never-play/allow-again twins below, and
            # the same shape the queue family already uses.
            if res.get("alreadyLiked"):
                return (
                    f"Already liked — {name} was on the board already, so the count "
                    "didn't move. Say so warmly."
                )
            actions.note("like", name)
            count = res.get("count")
            tail = f" That's {count} now." if isinstance(count, int) and count else ""
            return f"Done — added a like to {name}.{tail} Say it back in your own voice."

        tools.append(like_track)

    if cfg.get("allow_unfavorite") and not library_search_needs_mcp():
        @lk_llm.function_tool(name="subwave_unlike_track")
        async def unlike_track(title: str = "", artist: str = "") -> str:
            """Take the operator's heart back off a song. Admin only.

            Undoes the OPERATOR's own curation heart — not a listener's public
            like, which cannot be undone. Name the track if they name one
            ("un-like that Don McLean one"); with no name it takes the heart
            off whatever you liked earlier in this call, or failing that off
            whatever is playing now. The record does NOT have to still be on
            air: the station un-likes any song by id."""
            if actions.at_limit():
                return actions.refusal()
            track, song_id = await _target_to_unlike(station, actions,
                                                     title, artist)
            if not song_id:
                return (
                    f"Couldn't find {title or 'that one'} to un-like — nothing "
                    "by that name in the library, and nothing playing to fall "
                    "back on. Ask them which record they mean."
                )
            res = await station.unlike_track(song_id)
            if not res.get("ok"):
                return actions.station_refused(res, "That didn't go through")
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
                return actions.station_refused(res, "That didn't go on the never-play list")
            if res.get("already"):
                # No-op — don't charge or card it (see like_track above).
                return (
                    f"{name} was already on the never-play list — nothing "
                    "changed, and it was never going to come round again "
                    "anyway. Say so plainly."
                )
            actions.note("never-play", name)
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
                return actions.station_refused(res, "That didn't come off the list")
            if res.get("already"):
                # No-op — don't charge or card it (see like_track above).
                return (
                    f"{name} wasn't on the never-play list in the first place, "
                    "so nothing changed. Say so rather than implying you undid "
                    "something."
                )
            actions.note("never-play lifted", name)
            return (
                f"Done — {name} is off the never-play list and the station can "
                "pick it again. It is NOT queued: this only makes it eligible. "
                "Say it back in your own words."
            )

        tools.append(allow_track_again)

    return tools
