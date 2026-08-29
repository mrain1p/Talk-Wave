"""The ways into a library that are not a name search.

Added 0.10.104, after reading a night of bad calls. The call line had exactly
two ways to find music: a literal word match on titles and artists, and a
blind request that the station's resolver answered out of sight. Both failed
the same evening, in ways a listener could see:

  * A caller asked for "Firestorm" by Kygo. The track is called *Firestone*,
    and the library holds it. The word match found the two unrelated tracks
    actually called Firestorm, and the DJ reported the caller's song missing.
    One letter. There is no fuzziness in /dj/search and there never will be —
    it is Subsonic's search3.
  * A caller asked for something dreamy. The only tool for that was a request,
    which is one blind shot through a rate-limited endpoint: the DJ cannot see
    what came back, cannot name it, cannot offer a choice.

The station could already answer both, and had been able to the whole time.
These three tools are that surface:

    search_by_sound   a description, matched against how tracks SOUND
    more_like_this    the station's own neighbours of a given track
    browse_library    mood / energy / genre / era, from the tagged index

All three are reads. Nothing here queues anything — the DJ finds a record with
these and then puts it in with the tools in `music.py`, which is what keeps
the per-call action ledger meaningful: browsing is not an action, queueing is.
"""

from __future__ import annotations

import logging

from station import StationClient

from ..actions import CallActions
from .registry import library_search_needs_mcp
from .rows import _drop_blocked, _fmt_track
from .vocabulary import (_ALL_GENRES, _ENERGY, _OFFER, _THIN, _VOCAL,
                         _close_genres, _one_of, _related_genres,
                         _same_genre)

log = logging.getLogger("callin.agent")

# One page, same size as a name search's. Enough for "was it one of these",
# short enough to read down a phone line, and every row is prompt weight paid
# for on every later turn of the call.
_PAGE = 8

def build_discovery_tools(cfg: dict, station: StationClient,
                          actions: CallActions) -> list:
    """The exploration tools, as far as the settings and the station allow.

    `actions` is taken but never noted against: these are reads, and charging
    a caller's action budget for looking would make the DJ stingy with the
    only thing that stops it guessing.
    """
    from livekit.agents import llm as lk_llm

    tools: list = []

    # The booth's own cross-call ledger — no station read, no credentials,
    # just our day-log file (call/daylog.py). Always built, like every READ:
    # any line's earlier calls can be asked about, and "did you cancel my
    # queue?" once got a per-call truth that was a global evasion
    # (2026-08-26, the Casino night's opening line).
    @lk_llm.function_tool(name="subwave_booth_log")
    async def booth_log() -> str:
        """What THIS booth has done to the station lately — queued,
        pulled, skipped, taken over — across ALL calls, newest first,
        with when and which door did it. Use when a caller asks about an
        EARLIER call: "did you cancel my queue?", "where's the song I
        asked for?", "who put this on?". Attribution is by door only (a
        caller, a guest-code caller, the operator's line) — never names.
        For what actually AIRED, use subwave_already_played instead."""
        from .. import daylog

        lines = daylog.as_lines(12)
        if not lines:
            return (
                "The booth's own log shows nothing in the last two days "
                "— no queueing, pulling, skipping or takeovers came from "
                "this line. If they're asking about an earlier call, "
                "that IS the answer: nothing was done from here. The "
                "station's own automatic picks are not in this log."
            )
        return (
            "What this booth has done lately, newest first — doors, "
            "never names:\n" + lines
            + "\nThe station's own automatic picks are NOT here — this "
            "is only what calls and the operator's line changed."
        )

    tools.append(booth_log)

    # Every tool here reads an admin-only station endpoint. Without credentials
    # the wrapper cannot succeed even once, and a tool that can only fail is
    # worse than an absent one — the model will keep reaching for it and
    # telling the caller the library is empty.
    if library_search_needs_mcp():
        return tools

    if cfg.get("allow_sound_search"):
        @lk_llm.function_tool(name="subwave_search_by_sound")
        async def search_by_sound(description: str) -> str:
            """Find music by how it SOUNDS, from a description — "dreamy
            cinematic strings, slow and sad", "warm fuzzy guitars", "sparse
            late-night piano". THIS is the tool for a vibe, a feeling, a mood
            or an atmosphere when the caller has NOT named a track. Unlike a
            name search it does not look at titles at all; it matches the
            actual audio. Prefer it over guessing, and over putting a blind
            request in, whenever the caller describes rather than names."""
            items = await station.search_by_sound(description, limit=_PAGE)
            if not items:
                # Empty has two completely different causes and the station
                # will tell us which: /library/coverage reports whether sound
                # search is available at all. When it says NO, the library's
                # contents are not the story and the DJ should stop implying
                # they might be. When it says yes, the vibe genuinely found
                # nothing and a re-word is worth a turn. None means the station
                # wouldn't say — treat that as "assume it works", which lands
                # on the cautious wording below either way.
                if await station.sound_search_available() is False:
                    return (
                        "This station has never had its music analysed for "
                        "sound, so this tool cannot work here at all — that is "
                        "a fact about the STATION, not about the library or "
                        "the caller's taste. Do not say there's nothing like "
                        "that and do not try this tool again this call. DO "
                        "THIS NOW, in the same turn: call subwave_request_song "
                        "with the caller's own words."
                    )
                # The station answers 503 when the analyzer is down or nothing
                # has been audio-analysed yet, and both arrive here as an empty
                # list. "Nothing sounds like that" would be a lie about the
                # music; this is honest about the machine without making the
                # DJ narrate infrastructure.
                return (
                    "Sound search came back with nothing. Either this station "
                    "hasn't had its music analysed for sound yet, or the "
                    "analyser is offline — this is NOT evidence that the "
                    "library lacks that kind of music, so don't tell the "
                    "caller it does. DO THIS NOW, in this same turn: call "
                    "subwave_request_song with the caller's own words and let "
                    "the station's picker handle it. Do not answer the caller "
                    "until you have — a sentence about looking, with no second "
                    "tool call behind it, leaves them with nothing."
                )
            items, withheld = _drop_blocked(items)
            if not items:
                return (
                    "Everything that sounds like that is on this station's "
                    "never-play list, so none of it can be queued. The music "
                    "EXISTS — don't tell the caller the library lacks it. Say "
                    "it isn't what this station plays, and offer to try a "
                    "different feel."
                )
            lines = [_fmt_track(t, with_id=True) for t in items]
            return (
                f"{len(lines)} track(s) that SOUND like \"{description}\""
                + (f" ({withheld} more matched but are never-play — do not "
                   "offer them)" if withheld else "") + ":\n"
                + "\n".join(lines)
                + "\nThese are matched on the audio itself, so trust them over "
                "the titles. Offer one or two by name; queue the exact one they "
                "pick with subwave_queue_track. If they left the choice to you, "
                "don't read the list back — pick ONE, queue it, and say what "
                "you went with."
            )

        tools.append(search_by_sound)

        @lk_llm.function_tool(name="subwave_more_like_this")
        async def more_like_this(id: str = "") -> str:
            """More tracks like a given one — the station's own judgement of
            what sits closest to it and mixes well after it. Use for "more
            like this", "something similar", "another one like that". With no
            id it uses the track on air, which is what "this" almost always
            means on a call."""
            track_id = (id or "").strip()
            reference = ""
            # A TITLE is not an id, and the station cannot tell you so: it
            # looks the string up, finds nothing, and the miss is indistin-
            # guishable from a real track with no neighbours on file. The DJ
            # then reads back the only explanation it was given — "may not
            # have been analysed yet" — and turns it into a story about the
            # station being stubborn. Observed 2026-08-20 with
            # id='Jupiter by Aoife O’Donovan': a track that was ON AIR minutes
            # earlier, reported to the caller as unknown to the archives.
            # Station ids never contain whitespace, so this costs nothing and
            # sends the model to the one tool that turns a title into an id.
            if track_id and any(c.isspace() for c in track_id):
                return (
                    f"\"{track_id}\" is a title, not a track id — nothing was "
                    "looked up. Ids come from a result row and have no spaces "
                    "in them. Search for it with subwave_search_library, take "
                    "the id off the row you want, and call this again with "
                    "that. Do NOT tell the caller the station doesn't know the "
                    "track: you haven't asked it yet."
                )
            if not track_id:
                now = await station.now_playing()
                # `nowPlaying` FIRST: that is the key the real station nests
                # the current record under (curation.py and reads.py both
                # read it, with the 2026-08-16 incident behind them). Without
                # it, "more like this" with no id — the docstring's primary
                # path, "what 'this' almost always means on a call" — falls to
                # `or now`, the whole envelope, whose top-level keys carry no
                # subsonic_id, and the DJ tells a caller the record plainly
                # playing "isn't identifiable". Found by the 2026-08-28
                # top-down review; the one test fed `track`, a key no real
                # station sends.
                track = (now.get("nowPlaying") or now.get("track")
                         or now.get("current") or now)
                if isinstance(track, dict):
                    track_id = str(track.get("subsonic_id")
                                   or track.get("id") or "")
                    reference = str(track.get("title") or "")
            if not track_id:
                return (
                    "Nothing identifiable is on air to work from, and no track "
                    "id was given. Ask the caller to name a record they like, "
                    "search for it, then call this with its id."
                )
            items = await station.tracks_like(track_id)
            if not items:
                return (
                    "The station has no neighbours on file for that one — it "
                    "may not have been analysed yet. DO THIS NOW, in this same "
                    "turn: put a request in describing what they're after. "
                    "Don't report a fault and don't stop at saying you'll look."
                )
            items, _withheld = _drop_blocked(items)
            if not items:
                return (
                    "Every neighbour of that one is on the station's never-play "
                    "list. Say so as taste rather than as a fault — this station "
                    "doesn't play them — and offer to look another way."
                )
            head = (f"Tracks closest to \"{reference}\""
                    if reference else "Tracks closest to that one")
            return (
                head + ", by how they actually sound:\n"
                + "\n".join(_fmt_track(t, with_id=True) for t in items[:_PAGE])
                + "\nQueue whichever they pick with subwave_queue_track. If "
                "they left the choice to you, don't read the list back — pick "
                "ONE, queue it, and say what you went with."
            )

        tools.append(more_like_this)

    if cfg.get("allow_library_search"):
        @lk_llm.function_tool(name="subwave_browse_library")
        async def browse_library(moods: str = "", energy: str = "",
                                 genre: str = "", year_from: int = 0,
                                 year_to: int = 0, vocal: str = "") -> str:
            """Browse the library by what music IS rather than what it's
            called: mood, energy, genre, era, or vocal vs instrumental. Use
            for "anything from the eighties", "something instrumental",
            "what jazz have you got", "calm stuff". energy is high, medium or
            low. vocal is 'vocal' or 'instrumental'. moods must come from the
            station's own list — if you pass one it doesn't use, you'll be
            told the real words and can ask again. Genre is free text and this
            library files hundreds of them, compounds included: if the caller
            says "instrumental jazz", TRY IT AS THE GENRE rather than
            splitting it into genre plus vocal — you'll be told what the
            station actually files either way."""
            # Resolved to the station's own words before anything is sent —
            # see _one_of. A value that cannot be resolved stops here.
            use_energy, complaint = _one_of(
                energy, _ENERGY, "energy", "low, medium or high")
            if complaint:
                return complaint
            use_vocal, complaint = _one_of(
                vocal, _VOCAL, "vocal", "'vocal' or 'instrumental'")
            if complaint:
                return complaint

            async def look(genre_word: str) -> dict:
                return await station.browse_library(
                    moods=moods, energy=use_energy, genre=genre_word,
                    year_from=year_from or None, year_to=year_to or None,
                    vocal=use_vocal, limit=_PAGE) or {}

            d = await look(genre)
            if not d:
                return ("Couldn't read the library just now. Say so plainly; "
                        "don't describe records you haven't seen.")
            rows = d.get("rows") or []
            vocab = [str(m) for m in (d.get("moodVocab") or [])]
            # The station matches a genre EXACTLY, so `jazz` is not `Jazz` and
            # returns zero of 54,841. The hint below has always handed the real
            # spelling back and asked for a second try — and the model does not
            # take it: observed 2026-08-19, asked for instrumental jazz before
            # 2000, told the caller "the library isn't letting me filter by
            # year" and then defended the invention when pushed. The two tracks
            # it should have found were there all along. Reading the list is
            # already the cost of a miss; spending the one extra call to try the
            # station's own spelling turns the dead end into the answer instead
            # of into a story about the machine.
            #
            # `library_genres` reads the whole list and truncates client-side,
            # so asking for all of them costs exactly what asking for 40 cost
            # — and 40 was hiding 854 of this library's 894 genres, including
            # Instrumental Jazz, Bebop and Shoegaze. Matching happens against
            # everything; only a handful is ever shown to the model.
            known: list[str] = []
            fixed = ""
            related: list[str] = []
            swapped = ""
            if genre and (not rows or len(rows) < _THIN):
                known = await station.library_genres(limit=_ALL_GENRES)
                fixed = _same_genre(genre, known)
                # What else the caller said, so a shelf carrying their own
                # word is offered before a commoner one that isn't.
                related = _related_genres(
                    fixed or genre, known,
                    prefer=[use_vocal] + (moods or "").split(","))
            if not rows and fixed:
                again = await look(fixed)
                if again.get("rows"):
                    d, rows = again, again.get("rows") or []
            if not rows and len(related) == 1:
                # One way to read it and one only — take it, and say so on the
                # receipt. More than one is a choice that belongs to the
                # caller, not to us.
                again = await look(related[0])
                if again.get("rows"):
                    d, rows = again, again.get("rows") or []
                    swapped = related[0]
            if not rows:
                # The mood vocabulary is fixed and small, and a caller's word
                # for a feeling is usually not one of the seventeen: asking for
                # "melancholy" matches 0 of 381,000 tracks because the station's
                # word is "reflective". Handing the vocabulary back is what
                # turns a dead end into a second try.
                hint = ""
                if moods and vocab:
                    hint = (" The station only files moods under these words: "
                            + ", ".join(vocab)
                            + ". If one of them is close to what the caller "
                            "means, try again with it — do NOT tell them the "
                            "library has nothing.")
                elif genre and related:
                    # The word IS in this library's vocabulary, just not on
                    # its own or not with these other filters. Naming what it
                    # IS filed under is the whole difference between a useful
                    # answer and "we haven't got any jazz".
                    shown = ", ".join(f"\"{g}\"" for g in related[:_OFFER])
                    seat = (f"\"{fixed}\" exists but nothing under it matches "
                            "the rest of this, and " if fixed else "")
                    hint = (f" {seat}this library also files {shown}"
                            + (f" and {len(related) - _OFFER} more like it"
                               if len(related) > _OFFER else "")
                            + ". Those are real music here. Offer one or two "
                            "by name and browse whichever they pick — do NOT "
                            "tell the caller there is none of it.")
                elif genre and fixed:
                    # The spelling was the problem and it has already been
                    # retried above — so the genre IS here and this exact
                    # COMBINATION is what's empty. Saying which is the whole
                    # difference between a useful answer and "we haven't got
                    # any jazz".
                    hint = (f" The station files that genre as \"{fixed}\" and "
                            "it HAS music under it — what's empty is this "
                            "combination, with the other filters on top. Say "
                            "that, not that the library has none, and offer to "
                            "drop the tightest filter (the year range, or "
                            "instrumental-only) rather than the genre.")
                elif genre and known:
                    # Not a spelling and not a compound — so either the word
                    # is close to one this library uses, or it genuinely has
                    # none. Hundreds of genres are filed, so the full list is
                    # useless to a model reading it down a phone line: the
                    # nearest few, or the commonest few, and never all 894.
                    near = _close_genres(genre, known)
                    if near:
                        hint = (" Nothing is filed under that exact word. The "
                                "closest this library has are: "
                                + ", ".join(f"\"{g}\"" for g in near)
                                + ". Offer one if it is what they meant — do "
                                "NOT tell them the library has none.")
                    else:
                        hint = (" Nothing here is filed under that word at "
                                "all. The commonest genres in this library "
                                "are: " + ", ".join(known[:_OFFER])
                                + ". Say what this station DOES have rather "
                                "than what it doesn't.")
                if not hint:
                    hint = (" Try loosening it — one filter at a time — or put "
                            "the caller's own words in as a request instead.")
                return "Nothing in the library matches that combination." + hint
            rows, withheld = _drop_blocked(rows)
            if not rows:
                return (
                    "Everything matching that is on the station's never-play "
                    "list, so none of it can be queued. The library HAS music "
                    "like that — say it isn't what this station plays rather "
                    "than that there's none of it."
                )
            total = d.get("total")
            head = f"{len(rows)} of {total} matching track(s)" if isinstance(
                total, int) else f"{len(rows)} matching track(s)"
            if withheld:
                head += (f" ({withheld} more matched but are never-play — do "
                         "not offer them)")
            tail = ("\nQueue the one they pick with subwave_queue_track. If "
                    "they left the choice to you, don't read the list back — "
                    "pick ONE, queue it, and say what you went with.")
            if swapped:
                # Never silently: the caller asked for one word and is being
                # shown another, and a DJ that does not say so is describing
                # records under a name nobody chose.
                tail = (f"\nThese are filed under \"{swapped}\", not "
                        f"\"{genre}\" — this library has no \"{genre}\" of its "
                        "own. Tell the caller which shelf you pulled them off "
                        "before you offer them." + tail)
            elif related:
                # A thin answer beside a fat neighbour is the shape that
                # started this: two tracks for Jazz+instrumental while
                # "Instrumental Jazz" sat there with 740. Only ever added when
                # the result was thin enough to be worth widening.
                tail = ("\nThin, and this library also files "
                        + ", ".join(f"\"{g}\"" for g in related[:_OFFER])
                        + " — worth a look if the caller wants more."
                        + tail)
            return (head + ":\n"
                    + "\n".join(_fmt_track(t, with_id=True) for t in rows)
                    + tail)

        tools.append(browse_library)

        @lk_llm.function_tool(name="subwave_station_favourites")
        async def station_favourites() -> str:
            """What THIS station's listeners have actually loved — the most
            hearted records, commonest first. Use for "what do people like on
            here", "what's popular", "play something everyone loves", or when a
            caller leaves the choice to you and you'd rather pick something the
            audience has already voted for than guess."""
            items = await station.liked_tracks(limit=_PAGE + 4)
            if not items:
                return (
                    "Nobody has hearted anything on this station yet, or the "
                    "likes list isn't readable. Say you haven't got a "
                    "favourites list to go on rather than inventing one."
                )
            items, _withheld = _drop_blocked(items)
            if not items:
                return (
                    "Every one of the station's most-liked records is on the "
                    "never-play list now. Say there's nothing there you can "
                    "spin rather than reading out records that can't be played."
                )
            return (
                "The station's most-liked records:\n"
                + "\n".join(_fmt_track(t, with_id=True) for t in items[:_PAGE])
                + "\nThese are the audience's picks, not yours — say so if you "
                "offer one. Queue whichever they choose with subwave_queue_track. "
                "But if the caller left the choice to YOU, this list is not a "
                "menu to read back: pick ONE, queue it now, and tell them what "
                "you went with and why. One quick taste-check question is fine; "
                "more than one is handing the decision back."
            )

        tools.append(station_favourites)

        @lk_llm.function_tool(name="subwave_already_played")
        async def already_played() -> str:
            """What has ALREADY aired, most recent first — the station's
            durable play log, not just the last couple of records. Use to
            answer "did you play X earlier?", "what was that one before?", or
            to avoid queueing something the station has just had on. This
            reaches back further than the recent history you were briefed
            with.

            DO NOT call this for the record on air NOW (your briefing, or
            subwave_now_playing), for what is coming up
            (subwave_station_state), or for what is new in the library
            (subwave_recent_tracks). Already-aired, on-air, coming-up and
            newly-added are four different questions and only this one is
            about the past."""
            rows = await station.play_history(limit=12)
            if not rows:
                return (
                    "The station's play log isn't readable just now, so you "
                    "can only go on the recent history you were given. Don't "
                    "claim a record did or didn't air beyond that."
                )
            lines = []
            for row in rows[:10]:
                line = _fmt_track(row)
                # Who asked for it, when the station knows: a caller ringing
                # back to ask whether their request aired is the commonest
                # reason this gets read, and "yes, yours" is the answer.
                who = str(row.get("requester") or "").strip()
                source = str(row.get("source") or "").strip()
                if who and who.lower() != "anon":
                    line += f"  (requested by {who[:40]})"
                elif source:
                    line += f"  ({source})"
                lines.append(line)
            return (
                "Already played, most recent first:\n" + "\n".join(lines)
                + "\nThis is what actually went out. If they ask whether "
                "something aired and it isn't here, say you can't see it "
                "rather than that it definitely didn't."
            )

        tools.append(already_played)

    return tools
