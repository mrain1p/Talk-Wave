"""A whole album, or a run of picks, queued as one action.

Before this, "have you got Rumours? could you play it all" had no honest answer:
the DJ could queue one exact pick at a time, each pick spending one action from
the per-call cap, and a model that fires several queue calls in one parallel
group is exactly the shape that double-queued two records on 2026-08-16. A
batch the caller asked for in one sentence should be one tool call and one
action, with the tool doing the fan-out where it can be guarded.

Three facts about the station shaped everything here:

- Its /dj/search matches ALBUM names (Navidrome files them into the same
  full-text blob as titles and artists), and every row says which album it
  came from — but not the track number, and not the album's id. The station
  holds both internally (subsonic.getAlbum returns a tracklist in order) and
  exposes neither over REST, so canonical running order cannot be promised.
  Rows do carry the library `path`, and a normally-ripped album's filenames
  lead with the track number, so path order IS album order for any library
  that was ripped rather than dumped. Used when every kept row has one;
  dropped without comment when not.
- Queueing rides POST /dj/queue-track, which the station does not rate-limit
  (the public request endpoint's 1-per-20s gate never sees it). So the caps
  here are the only pacing there is: ALBUM_MAX_TRACKS a batch, MIX_MAX_PICKS
  a mix, and a wall-clock budget so a station answering slowly cannot hold
  the caller hostage to thirty sequential timeouts.
- The station queues duplicates on purpose for its own operator (its #619
  dedup bypass), so the per-call ledger in CallActions.queued_ids is the only
  thing stopping a repeated "play the album" from taking sixty slots. Same
  guard, same reason, as subwave_queue_track.

Built by music.build_library_tools when the album switch is on and station
admin credentials exist — the same availability gate as the exact queue,
decided in music.py so the tests' one patch point keeps working.
"""

from __future__ import annotations

import logging
import re
import time

from station import StationClient

from ..actions import CallActions
from .rows import _drop_blocked, era_year

log = logging.getLogger("callin.agent")

# One album, at most. Long enough for the White Album (30); a compilation
# bigger than this gets its first 30 and the tool says so.
ALBUM_MAX_TRACKS = 30
# A "mix" is a handful the DJ curated, not a second album path.
MIX_MAX_PICKS = 8
# /dj/search's own per-page ceiling, and how many pages to wade through when
# an album name is also an artist name ("The Beatles" matches every Beatles
# track in the library, and the album's own rows are scattered through them).
_SEARCH_PAGE = 100
_SEARCH_PAGES = 4
# Wall clock for the queue fan-out. Each POST is ~100ms against a healthy
# station, so this is never felt — it exists for the unhealthy one, where
# thirty sequential 45s timeouts would otherwise be the caller's evening.
_BATCH_BUDGET_SECS = 25.0
# On a shelf listing, how many albums to read down a phone line.
_SHELF_MAX = 10


def _txt(value, limit: int = 120) -> str:
    return str(value or "").strip()[:limit]


def _squash(value) -> str:
    """A name as a caller says it: lowercased, punctuation dropped. "Sgt.
    Pepper's" and "sgt peppers" must meet in the middle, because one side of
    every comparison here is a music tag and the other came off a phone line
    through STT."""
    text = re.sub(r"['’]", "", str(value or "").casefold())
    return " ".join(re.sub(r"[\W_]+", " ", text).split())


# What a failed READ must say, in one place so neither tool can soften it.
# On 2026-08-19 two timed-out searches reached the same caller as "I don't
# have anything by Eminem" and "no Beatles albums on the shelf" — from a
# library holding over a hundred of one and the whole White Album of the
# other. They said "bullshit", and were right.
_READ_FAILED = (
    "The racks couldn't be READ just now — the station's search timed out. "
    "Nothing is missing and NOTHING was queued: this is a slow shelf, not an "
    "empty one. Say the racks are being slow in your own words, give it a "
    "moment, and try again — never tell the caller the library hasn't got "
    "something off the back of this."
)


def _group_by_album(rows: list) -> list[dict]:
    """Rows bucketed by album name, first-seen display casing kept."""
    groups: dict[str, dict] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = _txt(row.get("album"))
        if not name:
            continue
        g = groups.setdefault(name.casefold(), {"name": name, "rows": []})
        g["rows"].append(row)
    return list(groups.values())


def _main_artist(rows: list) -> str:
    """Who introduces the album: the artist on most of its tracks, or
    "various artists" when nobody holds even half (a compilation)."""
    counts: dict[str, list] = {}
    for row in rows:
        artist = _txt(row.get("artist"))
        if artist:
            entry = counts.setdefault(artist.casefold(), [0, artist])
            entry[0] += 1
    if not counts:
        return "?"
    best = max(counts.values(), key=lambda e: e[0])
    return best[1] if best[0] * 2 >= len(rows) else "various artists"


def _album_year(rows: list) -> str:
    """The year the shelf line shows, under the station's era rule.

    Per track first (rows.era_year: the resolved original year wins, and a
    reissue suspect with no answer contributes nothing), then one year when
    the album agrees with itself and a span when it does not. A singles
    anthology's tracks resolve to their own recording years, and "1974-1978"
    is the true sentence about that shelf — the first track's file year was
    the reissue date presented as a fact.
    """
    years: list[int] = []
    fallback = ""
    for row in rows:
        y = era_year(row)
        if not y:
            continue
        try:
            years.append(int(y))
        except ValueError:
            # A year that is really a date ("1996-03-01") still names the
            # shelf better than silence — and it is exactly what _fmt_track
            # shows for the same rows, so dropping it here would have the two
            # surfaces disagree about the same metadata. First one wins.
            fallback = fallback or y
    if not years:
        return fallback
    lo, hi = min(years), max(years)
    return str(lo) if lo == hi else f"{lo}-{hi}"


def _shelf_line(group: dict) -> str:
    year = _album_year(group["rows"])
    bits = f"\"{group['name']}\" by {_main_artist(group['rows'])}"
    if year:
        bits += f" ({year})"
    return f"{bits} — {len(group['rows'])} track(s) on the shelf"


def _programme_length(rows: list) -> str:
    """The batch as airtime, when every track's length is known — one unknown
    and the total is a guess, which the DJ would speak as fact."""
    secs = 0
    for row in rows:
        d = row.get("duration")
        if not isinstance(d, (int, float)) or isinstance(d, bool) or d <= 0:
            return ""
        secs += int(d)
    mins = round(secs / 60)
    if mins < 2:
        return ""
    if mins < 60:
        return f"about {mins} minutes of programme"
    return f"about {mins // 60}h{mins % 60:02d} of programme"


async def _rows_for_query(station: StationClient, q: str) -> list | None:
    """One query, paged until the results thin out. None when the read itself
    failed — the difference between "no such record" and "couldn't look",
    which nothing above this may collapse (see station.search_library).

    Paging matters here in a way it doesn't for the 8-row search tool: a
    self-titled album's name matches every track by that artist, and the
    album's own rows may sit on page three of the flood.
    """
    rows: list = []
    for page in range(_SEARCH_PAGES):
        batch = await station.search_library(
            q, offset=page * _SEARCH_PAGE, limit=_SEARCH_PAGE)
        if batch is None:
            # A failed page is a failed lookup — unless earlier pages already
            # delivered, in which case partial rows are still real rows.
            return rows if rows else None
        rows.extend(batch)
        if len(batch) < _SEARCH_PAGE:
            break
    return rows


def _album_query_variants(album: str, artist: str) -> list[str]:
    """Queries to try for an album, most specific first.

    Punctuation is the reason this exists: asked for "The Beatles (The White
    Album)" — the library's own filed name — the station's search returns
    NOTHING (proven against the live library, 2026-08-19: the parenthesised
    words poison the whole match), and even "White Album" fails to find the
    rows. The artist alone always finds them, so it goes last: the candidate
    matcher then picks the album out of the artist's flood by name.
    """
    out: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        q = " ".join((q or "").split())
        if q and q.casefold() not in seen:
            seen.add(q.casefold())
            out.append(q)

    add(f"{album} {artist}")
    add(album)
    inside = re.findall(r"\(([^)]*)\)", album)
    outside = re.sub(r"\([^)]*\)", " ", album)
    for part in [outside] + inside:
        add(f"{part} {artist}")
        add(part)
    plain = _squash(album)
    add(f"{plain} {artist}")
    add(plain)
    add(artist)
    return out


def _candidates(groups: list[dict], album_q: str, artist_q: str) -> list[dict]:
    """Which album(s) the caller could mean. Punctuation-blind contains
    matching either way, because callers shorten names, libraries lengthen
    them ("Abbey Road" vs "Abbey Road (2019 Remaster)"), and neither side
    spells "Sgt. Pepper's" the way the other does."""
    want = _squash(album_q)
    cands = []
    for g in groups:
        have = _squash(g["name"])
        if want and have and (want == have or want in have or have in want):
            cands.append(g)
    if artist_q and len(cands) > 1:
        aw = _squash(artist_q)
        by_artist = [g for g in cands
                     if aw in _squash(_main_artist(g["rows"]))
                     or any(aw in _squash(r.get("artist"))
                            for r in g["rows"])]
        if by_artist:
            cands = by_artist
    if len(cands) > 1:
        exact = [g for g in cands if _squash(g["name"]) == want]
        if len(exact) == 1:
            cands = exact
    return cands


async def _queue_rows(station: StationClient, actions: CallActions,
                      rows: list) -> tuple[list, list, int, int]:
    """The fan-out: (queued rows+positions, refused rows+reasons, dupes
    skipped, left unqueued when the clock ran out)."""
    queued: list = []
    refused: list = []
    dupes = 0
    deadline = time.monotonic() + _BATCH_BUDGET_SECS
    for i, row in enumerate(rows):
        if time.monotonic() > deadline:
            return queued, refused, dupes, len(rows) - i
        # The fan-out runs for several seconds on a big album. The caller is
        # waiting on us, not the other way round — keep the idle watcher off
        # their back while it runs.
        actions.mark_working(6.0)
        tid = str(row.get("id") or "")
        if not tid:
            continue
        if tid in actions.queued_ids:
            dupes += 1
            continue
        res = await station.queue_track({
            "id": tid,
            "title": _txt(row.get("title")) or "?",
            "artist": _txt(row.get("artist")),
            "album": _txt(row.get("album")),
        })
        if res.get("ok"):
            actions.queued_ids.add(tid)
            queued.append((row, res.get("queuePosition")))
        else:
            refused.append((row, _txt(res.get("error"), 140) or "the station refused it"))
    return queued, refused, dupes, 0


def _first_position(queued: list) -> str:
    for _row, pos in queued:
        try:
            p = int(pos)
        except (TypeError, ValueError):
            continue
        if p <= 1:
            return "The first of them is next up, straight after the current track."
        return (f"The first of them is number {p} in the queue — roughly "
                f"{p * 3}-{p * 4} minutes away.")
    return ""


def _batch_report(queued: list, refused: list, dupes: int, unqueued: int,
                  withheld: int = 0, dropped: int = 0) -> str:
    """The honest footnotes every batch shares, in one place so neither tool
    can forget one. Each clause names something the DJ must not misreport."""
    bits = []
    if withheld:
        bits.append(f"{withheld} more track(s) matched but are on this "
                    "station's never-play list — they were NOT queued and "
                    "cannot be played; only mention them if asked.")
    if dupes:
        bits.append(f"{dupes} of them were ALREADY queued earlier in this "
                    "call and were not queued twice.")
    if refused:
        named = "; ".join(
            f"\"{_txt(r.get('title')) or '?'}\" ({why})" for r, why in refused[:3])
        bits.append(f"{len(refused)} track(s) were refused by the station: "
                    f"{named}. Don't claim those went in.")
    if unqueued:
        bits.append(f"The station answered slowly and time ran out with "
                    f"{unqueued} track(s) still unqueued — say plainly that "
                    "most of it is lined up but not all.")
    if dropped:
        bits.append(f"The batch was capped: {dropped} further track(s) were "
                    "not queued. Say so if it matters to the caller.")
    if queued:
        bits.append("This whole batch cost ONE action against the call's "
                    "limit, not one per track.")
    return " ".join(bits)


def build_album_tools(station: StationClient, actions: CallActions) -> list:
    """The two bulk tools. Caller (music.build_library_tools) has already
    decided the switch is on and the credentials exist."""
    from livekit.agents import llm as lk_llm

    @lk_llm.function_tool(name="subwave_queue_album")
    async def queue_album(album: str = "", artist: str = "") -> str:
        """Queue a WHOLE ALBUM — every track of it the library holds — as one
        action. Only when the caller clearly wants the full album played
        through ("play the whole thing", "all of it, start to finish"): a
        question like "do you have that album?" gets an answer, not thirty
        queued tracks, and you never offer a full album unprompted. Pass the
        album name, plus the artist when you know it. Call with ONLY the
        artist to list which of their albums are on the shelf — that reads
        and queues nothing."""
        album = (album or "").strip()
        artist = (artist or "").strip()
        if not album and not artist:
            return ("Name the album (and ideally the artist) to queue it, or "
                    "just the artist to see their shelf. Ask the caller which "
                    "album they mean.")

        if not album:
            # The shelf: a read, so it costs no action and ignores the cap.
            rows = await _rows_for_query(station, artist)
            if rows is None:
                return _READ_FAILED
            aw = _squash(artist)
            groups = [g for g in _group_by_album(rows)
                      if aw in _squash(_main_artist(g["rows"]))
                      or any(aw in _squash(r.get("artist"))
                             for r in g["rows"])]
            if not groups:
                return (f"Nothing on the shelf under \"{artist}\" — no albums "
                        "to list. Tell the caller plainly; a single-track "
                        "search may still find loose recordings.")
            groups.sort(key=lambda g: len(g["rows"]), reverse=True)
            lines = [_shelf_line(g) for g in groups[:_SHELF_MAX]]
            more = ("" if len(groups) <= _SHELF_MAX
                    else f"\n…and {len(groups) - _SHELF_MAX} more")
            return (f"Albums on the shelf for {artist} (a look only — NOTHING "
                    "has been queued):\n" + "\n".join(lines) + more +
                    "\nTo play one through, call this again with the album name.")

        if actions.at_limit():
            return actions.refusal()

        # Walk the query variants until one yields a CANDIDATE, not merely
        # rows: "White Album" happily returns a page of Christmas Albums, and
        # stopping there is how the right record two variants later never
        # gets found. The artist-alone variant at the end is the reliable
        # one for a punctuated filed name — see _album_query_variants.
        group = None
        groups: list[dict] = []
        read_failed = False
        for q in _album_query_variants(album, artist):
            rows = await _rows_for_query(station, q)
            if rows is None:
                read_failed = True
                break
            if not rows:
                continue
            groups = _group_by_album(rows) or groups
            cands = _candidates(groups, album, artist)
            if len(cands) == 1:
                group = cands[0]
                break
            if len(cands) > 1:
                lines = [_shelf_line(g) for g in cands[:6]]
                return ("More than one album answers to that — NOTHING queued "
                        "yet:\n" + "\n".join(lines) +
                        "\nAsk the caller which one, then call again with the "
                        "exact name and artist.")
        if group is None:
            if read_failed:
                return _READ_FAILED
            if groups:
                seen = ", ".join(f"\"{g['name']}\"" for g in groups[:6])
                return (f"No album called \"{album}\" in what came back — the "
                        f"search turned up tracks filed under {seen}. If one "
                        "of those is what the caller means, call again with "
                        "that exact name; otherwise tell them it isn't on the "
                        "shelf.")
            return (f"Nothing in the racks matching \"{album}\". Try the "
                    "artist alone to see their shelf, or tell the caller "
                    "plainly it isn't here — don't guess at a tracklist.")
        keep, withheld = _drop_blocked(group["rows"])
        if not keep:
            return (f"Every track of \"{group['name']}\" is on this station's "
                    "never-play list, so none of it can be queued. The "
                    "library HAS it — say it isn't one this station plays, "
                    "and offer something else.")
        # Path order is album order for a normally-ripped library (the
        # filenames lead with the track number). Claimed only when every
        # track has a path to sort by.
        paths = [_txt(r.get("path"), 300) for r in keep]
        in_order = all(paths)
        if in_order:
            keep = [r for _p, r in sorted(zip(paths, keep),
                                          key=lambda pair: pair[0].casefold())]
        dropped = max(0, len(keep) - ALBUM_MAX_TRACKS)
        keep = keep[:ALBUM_MAX_TRACKS]

        queued, refused, dupes, unqueued = await _queue_rows(
            station, actions, keep)
        if not queued:
            if dupes and not refused:
                return (f"\"{group['name']}\" is ALREADY in the queue from "
                        "earlier in this call — nothing further was added, "
                        "and nothing needs to be. Tell them it's still "
                        "waiting its turn.")
            why = refused[0][1] if refused else "the station refused it"
            if refused:
                actions.denied("refused", f"{len(refused)} track(s) were "
                               "refused by the station and not queued")
            return (f"None of \"{group['name']}\" made it into the queue: "
                    f"{why}. Tell the caller plainly — do NOT claim the "
                    "album is lined up.")

        actions.note("album",
                     f"\"{group['name']}\" — {len(queued)} tracks")
        head = (f"Queued the album \"{group['name']}\" by "
                f"{_main_artist(group['rows'])}: {len(queued)} track(s)")
        if in_order and len(queued) > 1:
            head += ", in the order the library files them"
        length = _programme_length([r for r, _p in queued])
        if length:
            head += f" — {length}"
        head += ". It is NOT playing yet: it lines up behind what's already queued. "
        head += _first_position(queued)
        if refused:
            # The receipt channel's refusal half, batch-shaped: one card
            # naming how many the station turned away.
            actions.denied("refused", f"{len(refused)} track(s) were "
                           "refused by the station and not queued")
        tail = _batch_report(queued, refused, dupes, unqueued,
                             withheld=withheld, dropped=dropped)
        return (head + " " + tail).strip()

    @lk_llm.function_tool(name="subwave_queue_mix")
    async def queue_mix(picks: str, label: str = "") -> str:
        """Queue a RUN of tracks you already picked — "a few Eminem songs",
        "a 90s rock mix", "queue both of those" — as one action. Build the
        run from real result rows first (name search, browse by genre/era,
        sound search, favourites), then pass one pick per line: the id from
        the row, a space, then its title. 2 to 8 picks; choose a spread
        yourself rather than copying a whole results page. `label` is two or
        three words for the caller's receipt ("90s rock mix"). For a single
        track use subwave_queue_track; for a complete album use
        subwave_queue_album. Never pass an id you did not get from a row."""
        if actions.at_limit():
            return actions.refusal()
        entries: list[tuple[str, str]] = []
        seen: set[str] = set()
        for line in (picks or "").splitlines():
            line = line.strip().lstrip("-*•").strip()
            if not line:
                continue
            head = line.split(",", 1)[0].strip()
            if "," in line and " " not in head:
                # A bare comma run of ids — tolerated, titles unknown.
                for tok in line.split(","):
                    tok = tok.strip()
                    if tok and tok not in seen:
                        seen.add(tok)
                        entries.append((tok, ""))
                continue
            parts = line.split(None, 1)
            tid = parts[0].strip().strip(",")
            title = parts[1].strip() if len(parts) > 1 else ""
            title = title.lstrip("—-:").strip().strip("\"")
            if tid and tid not in seen:
                seen.add(tid)
                entries.append((tid, title))
        if not entries:
            return ("No picks to queue. Pass one per line: the id from the "
                    "result row, a space, then the title. Nothing was queued.")
        dropped = max(0, len(entries) - MIX_MAX_PICKS)
        entries = entries[:MIX_MAX_PICKS]
        rows = [{"id": tid, "title": title or f"caller's pick {i + 1}"}
                for i, (tid, title) in enumerate(entries)]
        queued, refused, dupes, unqueued = await _queue_rows(
            station, actions, rows)
        if not queued:
            if dupes and not refused:
                return ("Every one of those is ALREADY in the queue from "
                        "earlier in this call — nothing was added twice. Tell "
                        "them it's all still waiting its turn.")
            why = refused[0][1] if refused else "the station refused them"
            if refused:
                actions.denied("refused", f"{len(refused)} track(s) were "
                               "refused by the station and not queued")
            return (f"None of those went into the queue: {why}. Tell the "
                    "caller plainly — do NOT claim the mix is lined up.")
        actions.note("mix", label or f"{len(queued)} picks")
        # The label is about to be said to the caller, so it has to remain
        # something they can ask us to undo — see CallActions.batches.
        actions.note_batch(label, [str(r.get("id") or "") for r, _p in queued])
        titles = ", ".join(f"\"{_txt(r.get('title'))}\"" for r, _p in queued[:3])
        head = (f"Queued {len(queued)} track(s)"
                + (f" as \"{label}\"" if label.strip() else "")
                + f": {titles}"
                + ("…" if len(queued) > 3 else "")
                + ". None of it is playing yet — it lines up behind what's "
                  "already queued. ")
        head += _first_position(queued)
        if refused:
            # The receipt channel's refusal half, batch-shaped: one card
            # naming how many the station turned away.
            actions.denied("refused", f"{len(refused)} track(s) were "
                           "refused by the station and not queued")
        tail = _batch_report(queued, refused, dupes, unqueued, dropped=dropped)
        return (head + " " + tail).strip()

    return [queue_album, queue_mix]
