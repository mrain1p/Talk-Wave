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
from .rows import _txt

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
    if not ids or any(i in actions.queued_ids for i in ids):
        return None
    actions.mark_working(6.0)
    res = await station.queue_block("album", track_id=ids[0])
    if res.get("unsupported"):
        return None
    name = group["name"]
    if not res.get("ok"):
        actions.denied("refused", f"\"{name}\" was refused by the station "
                       "and not queued")
        why = _txt(res.get("error"), 140) or "the station refused it"
        return (f"None of \"{name}\" made it into the queue: {why}. Tell the "
                "caller plainly — do NOT claim the album is lined up.")
    skipped = [s for s in (res.get("skipped") or []) if isinstance(s, dict)]
    blocked = [s for s in skipped if s.get("reason") == "blocked"]
    unplayable = [({"title": s.get("title")}, "no playable file")
                  for s in skipped if s.get("reason") != "blocked"]
    # A slow station's "sent but unconfirmed" carries no count; the rows we
    # sent are the honest best guess, and the report says it was slow.
    queued = len(ids) if res.get("unconfirmed") else int(res.get("queued") or 0)
    truncated = int(res.get("truncated") or 0)
    actions.queued_ids.update(ids)
    actions.note("album", f"\"{name}\" — {queued} tracks")
    # Both handles for taking it out again: the ids for the per-track clear
    # on an older station, the station's block id for the one-press cancel.
    actions.note_batch(name, ids)
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
                         withheld=len(blocked), dropped=truncated)
    return " ".join(b for b in (head, tail, _runs_past(res)) if b).strip()



async def clear_as_block(station: StationClient, actions: CallActions,
                          *names: str) -> str | None:
    """A block this call queued, taken out with the station's own one press
    — DELETE /dj/queue/block/:id (SUB/WAVE 1.14, #1632) — or None when no
    name given resolves to one, or nothing of it is still waiting (the
    per-track matcher then says so honestly).

    Tried on each free-text field in turn for the same reason batch_ids is:
    the model puts the album's name wherever it has a field, and the name
    is the one handle the caller was actually given.
    """
    block = ""
    for guess in names:
        block = actions.block_id(guess)
        if block:
            break
    if not block:
        return None
    res = await station.cancel_queued_block(block)
    if not res.get("ok"):
        return None
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
