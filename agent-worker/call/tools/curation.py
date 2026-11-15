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
turns is how it ends up hearting the wrong song. Where a caller NAMES another
record (un-like, never-play), the name is resolved here too — never an id.
"""

from __future__ import annotations

import logging

from station import StationClient

from ..actions import CallActions
from .registry import library_search_needs_mcp
from .rows import _fmt_track, _has_words, _squash

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


def _row_id(row: dict) -> str:
    """A listing row's track id — the play log says `trackId`, search and
    /now-playing say `subsonic_id` or `id`."""
    return str(row.get("subsonic_id") or row.get("trackId") or row.get("id")
               or row.get("songId") or "")


def _answers_to(row: dict, title: str, artist: str) -> bool:
    """Is this row the record the caller NAMED?

    Strict on purpose, because the only caller of this bans what it finds:
    the title must BE the title (a remaster's bracketed suffix aside), not
    merely contain it — "Yesterday" must never resolve to "Yesterday Once
    More" because that happened to be the library's one hit. The artist,
    when given, must appear in the row's artist as whole words.
    """
    filed = str(row.get("title") or "")
    if not _squash(filed):
        return False
    bare = filed.split(" (")[0].split(" - ")[0]
    if title and _squash(title) not in (_squash(filed), _squash(bare)):
        return False
    return not artist or _has_words(row.get("artist"), artist)


async def _rows_answering(station: StationClient, title: str,
                          artist: str) -> tuple[list[dict], bool]:
    """Every distinct record that answers to the name, and whether the look
    was COMPLETE. Recent plays first ("that one you played earlier" is the
    usual ask), then the library by name. An artist alone searches the
    recent plays only — the whole library by one artist is not a candidate
    list for a permanent ban."""
    try:
        rows = list(await station.play_history(limit=30) or [])
    except Exception as e:                                     # noqa: BLE001
        log.debug("play history unreadable for a named ban: %s", e)
        rows = []
    for row in rows:
        row.setdefault("_recent", True)
    looked = True
    if title:
        found = await station.search_library(
            " ".join(p for p in (title, artist) if p))
        looked = found is not None
        rows += list(found or [])
    seen: dict[str, dict] = {}
    for row in rows:
        rid = _row_id(row) if isinstance(row, dict) else ""
        if rid and rid not in seen and _answers_to(row, title, artist):
            seen[rid] = row
    return list(seen.values()), looked


async def _target_to_ban(station: StationClient, title: str,
                         artist: str) -> tuple[dict, str, str]:
    """(track, id, why not) for a ban. Exactly one of id / why-not is set.

    No name is the record on air, as it always was. A NAME is resolved
    against the recent plays and the library, and banned only when exactly
    ONE record answers: this is the one permanent thing on a call line, so
    two candidates is a question for the caller, never a pick.
    """
    if not (title or artist):
        track, song_id = await _track_on_air(station)
        return track, song_id, "" if song_id else (
            "Nothing identifiable is on air, so there's nothing to ban. "
            "Say you can't see what's playing rather than guessing.")
    named = " by ".join(p for p in (f'"{title}"' if title else "", artist)
                        if p) if title else f"anything by {artist}"
    rows, looked = await _rows_answering(station, title, artist)
    if len(rows) == 1:
        return rows[0], _row_id(rows[0]), ""
    if len(rows) > 1:
        listed = "; ".join(
            _fmt_track(r) + (" (played recently)" if r.get("_recent") else "")
            for r in rows[:6])
        return {}, "", (
            f"NOTHING was banned: MORE THAN ONE record answers to {named} — "
            f"{listed}. A ban is permanent and you do NOT pick one for them. "
            "Ask the caller which they mean, then call this again with the "
            "title AND the artist. If two of these are the same song by the "
            "same artist they are separate copies a name cannot tell apart: "
            "say so, and ban it while it is playing instead.")
    if not looked:
        return {}, "", (
            f"NOTHING was banned: {named} is not in the recent plays and the "
            "library search did not answer — the read FAILED, which is not "
            "'no such record'. Say you couldn't look it up just now; do not "
            "say the station hasn't got it, and do not ban anything else in "
            "its place.")
    if not title:
        return {}, "", (
            f"NOTHING was banned: nothing by {artist} is in the recent plays, "
            "and an artist alone is not enough to ban from the whole library. "
            "Ask which song they mean.")
    return {}, "", (
        f"NOTHING was banned: no record answering to {named} is in the "
        "station's recent plays or its library under that exact title. A ban "
        "is permanent, so a near-miss is never taken instead — ask them for "
        "the exact title and the artist, or ban it while it is on air.")


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
        # A caller can NAME the record (review, 2026-11-15). The ban took no
        # argument, so "never play Africa again" or "ban that one you played
        # earlier" could only ever land on whatever was on air at that
        # moment — the wrong record, permanently — while the lift beside it
        # takes any id and the station's blocklist takes any track. Same
        # switch, same action budget, same receipt; see _target_to_ban for
        # the one rule a name adds.
        @lk_llm.function_tool(name="subwave_never_play_track")
        async def never_play_track(title: str = "", artist: str = "") -> str:
            """Ban a track from the station for good — it comes out of the
            queue and is never selected again. With NO arguments it bans the
            track playing RIGHT NOW ("never play this again", "take this off
            the station"). When the caller NAMES a different record — "never
            play Africa again", "ban that Toto one you played earlier" — pass
            its title, and the artist if they gave one (an artist alone finds
            it only among the recent plays). It bans only when exactly ONE
            record answers; otherwise it tells you what to ask. Use ONLY when
            the caller has clearly asked for exactly that. This is PERMANENT
            and affects every listener, so do not reach for it to skip
            something: skipping is subwave_skip_track. If they only mean they
            dislike it, say so back instead of banning it."""
            if actions.at_limit():
                return actions.refusal()
            title, artist = str(title or "").strip(), str(artist or "").strip()
            by_name = bool(title or artist)
            track, song_id, why_not = await _target_to_ban(station, title, artist)
            if not song_id:
                return why_not
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
            if by_name:
                # Banned off a NAME, so the receipt must be checkable by ear:
                # the caller hears exactly which record went, and the DJ holds
                # the one id that undoes it.
                return (
                    f"Done — {name} is on the never-play list and the station "
                    f"won't select it again.{extra} It was found by NAME, not "
                    "taken off the air: say back the title AND the artist so "
                    "the caller can catch a wrong one — to undo it, "
                    f"subwave_allow_track_again with id {song_id}. If it is "
                    "the record playing right now, this does NOT stop it. "
                    "Say what actually happened, and remember this is "
                    "permanent."
                )
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
