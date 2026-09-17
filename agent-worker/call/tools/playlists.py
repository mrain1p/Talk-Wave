"""The station's own playlists, queued whole on the caller's ask.

Added 2026-09-17, off the 09-14 upstream pass's "unused reads": the station
publishes its operator playlists (/dj/playlists is in its Connect catalogue)
and each one's entries in the order the operator keeps them (/playlists/:id),
but has no one-press to queue one — so this is the album tool's per-track
loop pointed at a different source. It borrows the album module's receipt
vocabulary (the loop, the footnotes, the first-position line) the way
blocks.py does, and adds only what a playlist needs: which one the caller
means, and the honest answers when the station has none, the read was slow,
or the name fits two.

Rides the album switch (allow_album_queue): the same bulk grant, and a
playlist is the operator's own curation — an operator happy to let a caller
queue an album has decided the real question.
"""

from __future__ import annotations

from station import StationClient

from ..actions import CallActions
from .albums import (
    ALBUM_MAX_TRACKS, _READ_FAILED, _SHELF_MAX, _batch_report,
    _first_position, _programme_length, _queue_rows,
)
from .rows import _squash, _txt


def _candidates(lists: list[dict], name: str) -> list[dict]:
    """Which of the station's playlists the caller means: the exact name
    first, then either side containing the other — "the chill one" against
    "Sunday chill", the way batch_ids matches a label the caller paraphrased."""
    want = _squash(name)
    if not want:
        return []
    exact = [p for p in lists if _squash(p.get("name")) == want]
    if exact:
        return exact
    return [p for p in lists
            if want in _squash(p.get("name")) or _squash(p.get("name")) in want]


def _nothing_went_in(actions: CallActions, what: str, refused: list,
                     dupes: int) -> str:
    """The batch that queued nothing, said honestly: already there, or
    refused — never dressed up as lined up."""
    if dupes and not refused:
        return (f"{what} is ALREADY in the queue from earlier in this call — "
                "nothing further was added, and nothing needs to be. Tell "
                "them it's still waiting its turn.")
    # The house refusal idiom (CallActions.station_refused): one card, and
    # the pinned tail the refusal graders read — this site's own wording
    # matched none of them, so a refusal with a real reason read as a
    # success to spoken_rules.reads_as_a_refusal.
    why = refused[0][1] if refused else "the station refused it"
    return actions.station_refused({"error": why},
                                   f"None of {what} made it into the queue")


def _shelf(lists: list[dict]) -> str:
    """The look at the shelf: a read, so it costs no action."""
    lines = [f"- \"{_txt(p.get('name'), 80)}\" "
             f"({int(p.get('songCount') or 0)} tracks)"
             for p in lists[:_SHELF_MAX]]
    more = ("" if len(lists) <= _SHELF_MAX
            else f"\n…and {len(lists) - _SHELF_MAX} more")
    return ("The station's playlists (a look only — NOTHING has been "
            "queued):\n" + "\n".join(lines) + more +
            "\nTo play one through, call this again with its name.")


def build_playlist_tools(station: StationClient, actions: CallActions) -> list:
    """The playlist tool. Caller (music.build_library_tools) has already
    decided the album switch is on and the credentials exist."""
    from livekit.agents import llm as lk_llm

    @lk_llm.function_tool(name="subwave_queue_playlist")
    async def queue_playlist(name: str = "") -> str:
        """Queue one of the STATION'S OWN playlists — every track of it, in
        its own order — as one action. Only when the caller asks for one by
        name ("play the Sunday chill playlist", "put the late set on");
        never offer one unprompted. Call with NO name to list which
        playlists the station has — that reads and queues nothing."""
        name = (name or "").strip()
        lists = await station.playlists()
        if lists is None:
            return _READ_FAILED
        if not lists:
            return ("The station has no playlists of its own — nothing to "
                    "list or queue. Tell the caller plainly; an album or a "
                    "mix is the nearest thing.")
        if not name:
            return _shelf(lists)
        if actions.at_limit():
            return actions.refusal()
        hits = _candidates(lists, name)
        if not hits:
            seen = ", ".join(f"\"{_txt(p.get('name'), 60)}\"" for p in lists[:6])
            return (f"No playlist called \"{name}\" on the station — it has "
                    f"{seen}. If one of those is what the caller means, call "
                    "again with that exact name; otherwise say it isn't there.")
        if len(hits) > 1:
            seen = ", ".join(f"\"{_txt(p.get('name'), 60)}\"" for p in hits[:6])
            return ("More than one playlist answers to that — NOTHING queued "
                    f"yet: {seen}. Ask the caller which, then call again with "
                    "the exact name.")
        pname = _txt(hits[0].get("name"), 80) or name
        rows = await station.playlist_tracks(str(hits[0].get("id") or ""))
        if rows is None:
            return _READ_FAILED
        if not rows:
            return (f"\"{pname}\" is empty on the station — nothing to queue. "
                    "Say so plainly.")
        dropped = max(0, len(rows) - ALBUM_MAX_TRACKS)
        rows = rows[:ALBUM_MAX_TRACKS]
        queued, refused, dupes, unqueued = await _queue_rows(
            station, actions, rows)
        if not queued:
            return _nothing_went_in(actions, f"\"{pname}\"", refused, dupes)
        actions.note("playlist", f"\"{pname}\" — {len(queued)} tracks")
        # The name is about to be said to the caller, so it has to remain
        # something they can ask us to undo — see CallActions.batches.
        actions.note_batch(pname, [str(r.get("id") or "") for r, _p in queued])
        head = (f"Queued the station's playlist \"{pname}\": {len(queued)} "
                "track(s), in its own order")
        length = _programme_length([r for r, _p in queued])
        if length:
            head += f" — {length}"
        head += ". It is NOT playing yet: it lines up behind what's already queued. "
        head += _first_position(queued)
        if refused:
            actions.denied("refused", f"{len(refused)} track(s) were "
                           "refused by the station and not queued")
        tail = _batch_report(queued, refused, dupes, unqueued, dropped=dropped)
        return (head + " " + tail).strip()

    return [queue_playlist]
