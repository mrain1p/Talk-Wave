"""The station's one-press block queue, in and out.

SUB/WAVE 1.14 (its #1632) gave the studio what the album tool had been
hand-rolling since 0.98.10: POST /dj/queue-block queues a whole record from
any one of its track ids, in the record's own disc/track order, with the
never-play list applied by the station and every refusal NAMED, a cap that is
reported rather than silent, and a warning when the block outlasts the show on
air. DELETE /dj/queue/block/:id is the inverse — the unaired remainder out as
one action, with the station saying itself what was already too late.

Both halves live here, beside each other, because they are one feature seen
from two ends: what goes in as a block comes out as a block, and the id the
press returns is the handle the undo needs (CallActions.blocks carries it
across the call). albums.py still owns the READ half — which record the caller
means — and the receipt vocabulary every batch shares; removal.py still owns
the per-track clear. Each falls back to its per-track loop on a station
without the route, so nothing here is required for an older station to work.
"""

from __future__ import annotations

import logging

from station import StationClient

from ..actions import CallActions
from .albums import (
    _batch_report, _first_position, _main_artist, _programme_length,
)
from .rows import _squash, _txt

log = logging.getLogger("callin.agent")


def _runs_past(res: dict) -> str:
    """The station's warning that a block outlasts the show on air (#1632).

    WARN ONLY on its side — the block is in, and the incoming host's
    handover lands between two of its tracks — so the DJ needs to know, and
    the caller only if they would care.
    """
    warn = res.get("runsPastShowChange")
    if not isinstance(warn, dict):
        return ""
    try:
        mins = max(1, round(float(warn.get("bySec") or 0) / 60))
    except (TypeError, ValueError):
        return ""
    show = _txt(warn.get("show"), 80)
    into = f" into {show}" if show else ""
    return (f"It runs about {mins} minute(s) past the next show change{into} "
            "— the incoming host's handover will land between two of its "
            "tracks. Mention that only if the caller would care.")


def _skips(res: dict) -> tuple[list, int]:
    """What the station turned away, split into the two kinds a report says
    differently: (unplayable rows with their reason, never-play count).

    Its own function because it is three walks of one field and its caller
    sits on the complexity ceiling — the station's `skipped` entries carry
    `reason: 'blocked'` for the never-play list (a count is all the report
    wants) and anything else is a row with no playable file, which is named.
    """
    skipped = [s for s in (res.get("skipped") or []) if isinstance(s, dict)]
    blocked = sum(1 for s in skipped if s.get("reason") == "blocked")
    unplayable = [({"title": s.get("title")}, "no playable file")
                  for s in skipped if s.get("reason") != "blocked"]
    return unplayable, blocked


async def queue_as_block(station: StationClient, actions: CallActions,
                          group: dict, keep: list) -> str | None:
    """The album as ONE station press — POST /dj/queue-block (SUB/WAVE
    1.14, #1632) — or None to fall back to the per-track loop.

    The station does here what the loop only approximates: the record's
    own disc/track order (the loop infers it from filenames), the never-play
    list applied with every refusal NAMED, the cap reported. It falls back
    rather than failing on the two cases the loop still owns: a station
    without the route (404 — the pre-1.14 behaviour, byte for byte), and a
    record some of which this call already queued, because the station
    queues duplicates for an operator on purpose and only the loop can skip
    one track of thirty.
    """
    ids = [str(r.get("id") or "") for r in keep if r.get("id")]
    if not ids:
        return None
    name = group["name"]
    # PRESSED ALREADY, THIS CALL? A second ask by the same name used to fall
    # through to the per-track loop below, which does not skip a block it
    # never queued track-by-track — so "put Rumours on" twice queued the
    # record and then thirty duplicates of it.
    #
    # An EXACT label compare, not actions.block_id's loose one: that match
    # is deliberately generous because the caller paraphrases a name they
    # were given, which is right for an undo and wrong for a "have I done
    # this" guard — a run queued under "Eminem" would answer to "The Eminem
    # Show" and refuse a record nobody had pressed. `name` is the library's
    # own filed album name on both sides here, so exact is what it means.
    if any(_squash(label) == _squash(name) for label, _id in actions.blocks):
        return (f"\"{name}\" is ALREADY in the queue from earlier in this "
                "call — nothing further has been added, and nothing needs to "
                "be. Don't press it again or tell them it has just gone in: "
                "if they are asking, tell them it is still waiting its turn.")
    if any(i in actions.queued_ids for i in ids):
        return None
    actions.mark_working(6.0)
    res = await station.queue_block("album", track_id=ids[0])
    if res.get("unsupported"):
        return None
    if not res.get("ok"):
        why = _txt(res.get("error"), 140) or "the station refused it"
        return actions.station_refused(
            {"error": why}, f"None of \"{name}\" made it into the queue")
    unplayable, blocked = _skips(res)
    # A slow station's "sent but unconfirmed" carries no count; the rows we
    # sent are the honest best guess, and the report says it was slow.
    queued = len(ids) if res.get("unconfirmed") else int(res.get("queued") or 0)
    truncated = int(res.get("truncated") or 0)
    # A TRUNCATED PRESS HAS NO KNOWABLE MEMBERSHIP. The station queued 30 of
    # the 45 it found, in ITS own order, and reports only the count — so
    # marking all 45 as this call's claimed fifteen records that never went
    # in, and a later exact pick of one of them was refused as already
    # queued. Both id-shaped handles wait for a press that was not capped.
    if not truncated:
        actions.queued_ids.update(ids)
        actions.note_batch(name, ids)
    actions.note("album", f"\"{name}\" — {queued} tracks")
    # The other handle, and the exact one whatever the station dropped: its
    # own block id for the one-press cancel.
    if res.get("blockId"):
        actions.note_block(name, str(res["blockId"]))
    if unplayable:
        actions.denied("refused", f"{len(unplayable)} track(s) could not be "
                       "queued — no playable file")
    head = (f"Queued the album \"{name}\" by {_main_artist(group['rows'])}: "
            f"{queued} track(s)")
    if queued > 1:
        head += ", in the record's own running order"
    length = _programme_length(keep) if queued == len(keep) else ""
    if length:
        head += f" — {length}"
    if res.get("unconfirmed"):
        head += ". The station was slow to confirm, but it has gone through"
    head += ". It is NOT playing yet: it lines up behind what's already queued. "
    head += _first_position([(None, res.get("queuePosition"))])
    tail = _batch_report([None] * queued, unplayable, 0, 0,
                         withheld=blocked, dropped=truncated)
    return " ".join(b for b in (head, tail, _runs_past(res)) if b).strip()



async def queue_artist_run(station: StationClient, actions: CallActions,
                           artist: str, count: int) -> str | None:
    """A run by ONE artist as ONE station press — POST /dj/queue-block
    {kind:'artist'} (SUB/WAVE 1.14, #1632) — or None on a station without
    the route, when the mix tool sends the DJ back to picking rows itself.

    The station chooses the run: its Last.fm-ranked top songs by the
    artist, or a walk of their albums where Last.fm has no coverage (most
    of a niche catalogue), never-play list applied and every refusal named.
    That beats what the DJ did by hand until 2026-09-17 — a search page,
    its own guess at which are the known ones, then N pushes — which is why
    "a few by one artist" comes here and a mix ACROSS artists still goes
    through picks. The station does not say WHICH tracks went in, and the
    receipt says so, because a DJ that reads "5 queued" and names five
    titles has invented four of them.
    """
    actions.mark_working(6.0)
    res = await station.queue_block("artist", artist=artist, limit=count)
    if res.get("unsupported"):
        return None
    if not res.get("ok"):
        why = _txt(res.get("error"), 140) or "the station refused it"
        return actions.station_refused(
            {"error": why}, f"Nothing by {artist} made it into the queue")
    queued = int(res.get("queued") or 0)
    if res.get("unconfirmed") and not queued:
        queued = count
    _unplayable, blocked = _skips(res)
    actions.note("mix", f"{queued} by {artist}")
    # The handle for "clear those Eminem tracks": the station's block id
    # under the name the caller used, and under the station's own label
    # when it differs, so either paraphrase finds it.
    if res.get("blockId"):
        for name in dict.fromkeys((artist, _txt(res.get("label"), 80))):
            actions.note_block(name, str(res["blockId"]))
    head = (f"Queued {queued} track(s) by {artist} in one press — the "
            "station's own pick of their best-known songs")
    if res.get("unconfirmed"):
        head += ". The station was slow to confirm, but it has gone through"
    head += (". The station did not name which tracks, so do NOT list "
             "titles — say how many are in and whose they are. None of it "
             "is playing yet: it lines up behind what's already queued. ")
    head += _first_position([(None, res.get("queuePosition"))])
    tail = _batch_report([None] * queued, [], 0, 0, withheld=blocked,
                         dropped=int(res.get("truncated") or 0))
    return " ".join(b for b in (head, tail, _runs_past(res)) if b).strip()


async def clear_as_block(station: StationClient, actions: CallActions,
                          *names: str) -> str | None:
    """A block this call queued, taken out with the station's own one press
    — DELETE /dj/queue/block/:id (SUB/WAVE 1.14, #1632) — or None for the two
    fall-throughs the per-track matcher owns: no name given resolves to a
    block, or the station's documented `nothing-left` 404 (none of it is
    still waiting, and the matcher then says so honestly).

    ANY OTHER non-ok answer is refused here and NOT fallen through. Until
    2026-09-17 a 5xx, a timeout or missing credentials read as "nothing
    left": the per-track name matcher then swept a SHARED queue and could
    pull another caller's tracks, and the station's own reason was lost on
    the way.

    Tried on each free-text field in turn for the same reason batch_ids is:
    the model puts the album's name wherever it has a field, and the name
    is the one handle the caller was actually given.
    """
    block = asked = ""
    for guess in names:
        block = actions.block_id(guess)
        if block:
            asked = str(guess or "").strip()
            break
    if not block:
        return None
    res = await station.cancel_queued_block(block)
    if not res.get("ok"):
        if res.get("reason") == "nothing-left":
            return None
        label = _txt(res.get("label"), 80) or asked or "that block"
        why = _txt(res.get("error"), 140) or "the station refused it"
        actions.denied("refused", f"\"{label}\" stayed queued — {why}")
        return (f"Could not pull \"{label}\": {why}. Nothing was pulled — do "
                "NOT claim a clear-out.")
    removed, kept = int(res.get("removed") or 0), int(res.get("kept") or 0)
    label = _txt(res.get("label"), 80) or "that block"
    if not removed:
        return (f"Too late for what's left of \"{label}\" — {kept} track(s) "
                "are on air or cued up next and CANNOT be pulled now. Only a "
                "skip ends the one playing, and that cuts it off for everyone "
                "listening. Nothing was pulled.")
    actions.note("clear", f"{removed} tracks — {label}")
    late = (f" {kept} track(s) of it were too late — on air or cued up next; "
            "only a skip ends those, and it cuts them off for everyone "
            "listening." if kept else "")
    return (f"Pulled {removed} track(s) of \"{label}\" out of the queue in one "
            f"go. They will not play.{late} This whole clear-out cost ONE "
            "action against the call's limit.")
