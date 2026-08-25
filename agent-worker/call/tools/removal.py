"""Taking queued tracks back out: one, or a run of them as one action.

The single cancel moved here from music.py at 0.98.12, when its batch sibling
arrived and music.py was already brushing the size ceiling. The seam is
honest: everything here UNDOES queueing; nothing here searches, submits, or
puts anything in.

The batch exists because the album tool created an asymmetry: "play the whole
album" put twenty tracks in as ONE action, and taking them out cost one action
per track against the per-call cap. On the 2026-08-19 chat that surfaced it,
the DJ pulled four Eminem tracks honestly, hit the cap with four still queued,
and — refused by the ledger — described the cap as the scheduler fighting him
while claiming pulls that never ran. Bulk in earns bulk out.

Both tools ride the cancel switch and the same caution: the queue is shared,
so a clear-out can pull tracks OTHER callers asked for — at batch size, more
of them. That is the operator's grant to make, and it stays off by default.
"""

from __future__ import annotations

import logging
import time

from station import StationClient

from ..actions import CallActions
from .albums import _BATCH_BUDGET_SECS, _squash, _txt

log = logging.getLogger("callin.agent")

# One clear-out, at most. The same ceiling as an album going in: the two are
# mirror images and should cost alike.
CLEAR_MAX_TRACKS = 30


def build_removal_tools(cfg: dict, station: StationClient,
                        actions: CallActions) -> list:
    """Both un-queue tools. Caller (music.build_library_tools) has already
    decided the cancel switch is on and the credentials exist."""
    from livekit.agents import llm as lk_llm

    @lk_llm.function_tool(name="subwave_cancel_queued_track")
    async def cancel_queued_track(id: str = "", title: str = "") -> str:
        """Take a track back OUT of the queue before it airs — the caller
        changed their mind, or you queued the wrong one. Pass the id if
        you have it from a search result or a queue read; otherwise pass
        the title and it will be matched against what's actually queued.
        Cannot touch the track on air or the one being cued up next: for
        that, there is only skipping. For several at once — an artist, an
        album, a list — use subwave_clear_from_queue instead."""
        if actions.at_limit():
            return actions.refusal()
        track_id = (id or "").strip()
        named = title or track_id
        if not track_id:
            # A title is what the DJ actually has after "no, not that one"
            # — it just said the name out loud. Resolve it against the
            # real queue rather than making the model produce an id it
            # never saw.
            needle = (title or "").strip().casefold()
            if not needle:
                return ("You need to say WHICH track to pull — a title or "
                        "an id. Ask the caller which one they mean.")
            state = await station.state()
            for item in (state.get("upcoming") or []):
                t = item if isinstance(item, dict) else {}
                if needle in str(t.get("title") or "").casefold():
                    # /state names it subsonic_id; /dj/search calls the
                    # same value id. Take either rather than depending on
                    # which read the DJ happened to come through.
                    track_id = str(t.get("subsonic_id") or t.get("id") or "")
                    named = t.get("title") or title
                    break
            if not track_id:
                return (
                    f"Nothing called \"{title}\" is in the queue — it may have "
                    "already played, or it never went in. Tell the caller that "
                    "plainly rather than saying you pulled it."
                )

        res = await station.cancel_queued_track(track_id)
        if res.get("reason") == "already-playing":
            return (
                f"Too late for \"{named}\" — it's already on air or cued up as "
                "the next thing out. It CANNOT be pulled now. Tell the caller "
                "straight; if they want it gone you can only skip it, and that "
                "cuts it off for everyone listening."
            )
        if not res.get("ok"):
            return (
                f"That didn't come out of the queue: "
                f"{res.get('error') or 'the station refused it'}. Tell the "
                "caller plainly — do NOT claim it's gone."
            )
        actions.note("cancel", f"\"{named}\"")
        return (
            f"\"{named}\" is out of the queue — it will not play. Say so, and "
            "if they wanted something in its place, put that in now."
        )

    @lk_llm.function_tool(name="subwave_clear_from_queue")
    async def clear_from_queue(artist: str = "", album: str = "",
                               titles: str = "", label: str = "") -> str:
        """Take SEVERAL waiting tracks out of the queue as ONE action —
        "remove all the Eminem", "clear that album out", "pull those three",
        "cancel that mix you just queued". Give an artist (everything of
        theirs waiting goes), an album name, titles one per line, or the
        `label` of a mix YOU queued earlier in this call. The track on air
        and the one cued up next cannot be pulled — the result names any it
        was too late for, and skipping is the only tool for those. For a
        single track use subwave_cancel_queued_track. The queue is shared:
        clear only what THIS caller asked to clear, never another caller's
        requests."""
        if actions.at_limit():
            return actions.refusal()
        want_artist = _squash(artist)
        want_album = _squash(album)
        want_titles = {_squash(t) for t in (titles or "").splitlines()
                       if _squash(t)}
        # A mix's label is not on any queue row — it only ever existed on the
        # receipt this call handed the caller — so it is resolved back to the
        # ids that went in under it before anything is matched.
        want_ids = set(actions.batch_ids(label))
        named_batch = str(label or "").strip() if want_ids else ""
        if not want_ids:
            # And the model does not reach for the new parameter first: on the
            # call that prompted this it put the label in `artist`, which is
            # the only field it had. That reading is now correct rather than
            # a dead end — try each free-text field as a label before giving
            # up, and only then fall through to matching it as an artist or an
            # album name. Costs nothing: this is a dict lookup on a list that
            # is at most a handful of entries long.
            for guess in (artist, album, titles):
                found = actions.batch_ids(guess)
                if found:
                    want_ids = set(found)
                    named_batch = str(guess or "").strip()
                    break
        # Asked on what was SAID, not on what resolved: a label that matches
        # no batch is a request this tool understood and could not fill, and
        # it earns the honest miss below rather than "you didn't say what".
        if not (want_artist or want_album or want_titles
                or str(label or "").strip()):
            return ("Say WHAT to clear — an artist, an album, titles one per "
                    "line, or the label of a mix you queued on this call. "
                    "Nothing was pulled.")

        state = await station.state()
        upcoming = [t for t in (state.get("upcoming") or [])
                    if isinstance(t, dict)]
        if not upcoming:
            return ("The queue has nothing waiting in it — nothing to clear. "
                    "Tell the caller it's already empty.")
        matches: list[tuple[str, str, bool]] = []
        for t in upcoming:
            t_artist = _squash(t.get("artist"))
            t_album = _squash(t.get("album"))
            t_title = _squash(t.get("title"))
            tid = str(t.get("subsonic_id") or t.get("id") or "")
            hit = ((tid and tid in want_ids)
                   or (want_artist and want_artist in t_artist)
                   or (want_album and t_album
                       and (want_album in t_album or t_album in want_album))
                   or (t_title and any(w in t_title or t_title in w
                                       for w in want_titles)))
            if not hit:
                continue
            if tid:
                matches.append((tid, _txt(t.get("title")) or "?",
                                t.get("sent") is True))
        if not matches:
            # A named batch that matches nothing is a DIFFERENT answer from a
            # name nobody recognises: those tracks did go in, on this call,
            # and the queue has since moved past them. Saying "it never went
            # in" about music the caller watched being queued is how the DJ
            # ends up arguing with someone who is right.
            if named_batch:
                return (
                    f"\"{named_batch}\" did go into the queue on this call, but "
                    "none of it is still waiting — it has already played or is "
                    "playing now. Nothing was pulled. Say that plainly: the "
                    "tracks were queued, they're just past pulling. Only a skip "
                    "ends what's on air, and that cuts it off for everyone."
                )
            asked = artist or album or label or "those titles"
            return (f"Nothing waiting in the queue matches \"{asked}\" — it "
                    "may have played already, or it never went in. Tell the "
                    "caller what you actually see; don't claim a clear-out.")

        # The cap keeps QUEUE order — the head-of-queue rows are the ones a
        # caller most urgently means and the only ones that can answer
        # "already-playing", so they must never be the ones the cap silences.
        # WITHIN the batch, `sent` (surfaced by upstream #1458) orders the
        # work: an unsent row is the controller's own held pick and cancels
        # instantly Node-side, a sent row is already in Liquidsoap's queue —
        # a telnet round-trip each — so the instant ones go first and a
        # budget that dies mid-run has cleared the most it could. The sort is
        # stable, so queue order holds within each half; an absent flag
        # counts as unsent, matching the station's own omitted-flag degrade.
        dropped = max(0, len(matches) - CLEAR_MAX_TRACKS)
        matches = matches[:CLEAR_MAX_TRACKS]
        matches.sort(key=lambda m: m[2])
        pulled: list[str] = []
        too_late: list[str] = []
        failed: list[str] = []
        left_unpulled = 0
        deadline = time.monotonic() + _BATCH_BUDGET_SECS
        for i, (tid, tname, _sent) in enumerate(matches):
            if time.monotonic() > deadline:
                left_unpulled = len(matches) - i
                break
            # The caller is waiting on us, not the other way round.
            actions.mark_working(6.0)
            res = await station.cancel_queued_track(tid)
            if res.get("ok"):
                pulled.append(tname)
            elif res.get("reason") == "already-playing":
                too_late.append(tname)
            else:
                failed.append(tname)

        if not pulled:
            if too_late and not (failed or left_unpulled):
                names = ", ".join(f"\"{n}\"" for n in too_late[:3])
                return (f"Too late for the lot — {names} are on air or cued "
                        "up next, and nothing else matched. They CANNOT be "
                        "pulled now; only a skip ends the one playing, and "
                        "that cuts it off for everyone listening.")
            why = "the station refused them" if failed else "time ran out"
            return (f"Nothing came out of the queue: {why}. Tell the caller "
                    "plainly — do NOT claim a clear-out happened.")

        what = named_batch or artist or album
        actions.note("clear", f"{len(pulled)} tracks"
                     + (f" — {_txt(what, 60)}" if what else ""))
        names = ", ".join(f"\"{n}\"" for n in pulled[:5])
        head = (f"Pulled {len(pulled)} track(s) out of the queue: {names}"
                + ("…" if len(pulled) > 5 else "") + ". They will not play.")
        bits = []
        if too_late:
            late = ", ".join(f"\"{n}\"" for n in too_late[:3])
            bits.append(f"Too late for {late} — on air or cued up next; only "
                        "a skip ends those, and it cuts them off for "
                        "everyone listening.")
        if failed:
            bits.append(f"{len(failed)} track(s) were refused by the station "
                        "and are STILL QUEUED — don't claim those are gone.")
        if left_unpulled:
            bits.append(f"The station answered slowly and time ran out with "
                        f"{left_unpulled} still queued — say plainly that "
                        "most of it is cleared but not all.")
        if dropped:
            bits.append(f"The clear-out was capped: {dropped} further "
                        "match(es) stayed queued. Say so if it matters.")
        bits.append("This whole clear-out cost ONE action against the "
                    "call's limit.")
        return head + " " + " ".join(bits)

    return [cancel_queued_track, clear_from_queue]
