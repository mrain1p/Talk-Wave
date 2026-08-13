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
from .music import _fmt_track
from .registry import library_search_needs_mcp

log = logging.getLogger("callin.agent")

# One page, same size as a name search's. Enough for "was it one of these",
# short enough to read down a phone line, and every row is prompt weight paid
# for on every later turn of the call.
_PAGE = 8


def _fmt_neighbour(t: dict) -> str:
    """A neighbour row, with the two numbers that justify it being next.

    bpm and key are what make "this mixes well after that" a statement rather
    than a claim, and they are the DJ's own vocabulary — a DJ who can say "same
    tempo, and it's in the relative minor" sounds like one.
    """
    line = _fmt_track(t, with_id=True)
    extra = []
    bpm = t.get("bpm")
    if isinstance(bpm, (int, float)) and bpm:
        extra.append(f"{round(float(bpm))} bpm")
    if t.get("musicalKey"):
        extra.append(str(t["musicalKey"])[:12])
    return line + (f" [{', '.join(extra)}]" if extra else "")


def build_discovery_tools(cfg: dict, station: StationClient,
                          actions: CallActions) -> list:
    """The exploration tools, as far as the settings and the station allow.

    `actions` is taken but never noted against: these are reads, and charging
    a caller's action budget for looking would make the DJ stingy with the
    only thing that stops it guessing.
    """
    from livekit.agents import llm as lk_llm

    tools: list = []

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
            lines = [_fmt_track(t, with_id=True) for t in items]
            return (
                f"{len(lines)} track(s) that SOUND like \"{description}\":\n"
                + "\n".join(lines)
                + "\nThese are matched on the audio itself, so trust them over "
                "the titles. Offer one or two by name; queue the exact one they "
                "pick with subwave_queue_track."
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
            if not track_id:
                now = await station.now_playing()
                track = now.get("track") or now.get("current") or now
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
            head = (f"Tracks closest to \"{reference}\""
                    if reference else "Tracks closest to that one")
            return (
                head + ", by how they actually sound:\n"
                + "\n".join(_fmt_neighbour(t) for t in items[:_PAGE])
                + "\nQueue whichever they pick with subwave_queue_track."
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
            told the real words and can ask again."""
            d = await station.browse_library(
                moods=moods, energy=energy, genre=genre,
                year_from=year_from or None, year_to=year_to or None,
                vocal=vocal, limit=_PAGE)
            if not d:
                return ("Couldn't read the library just now. Say so plainly; "
                        "don't describe records you haven't seen.")
            rows = d.get("rows") or []
            vocab = [str(m) for m in (d.get("moodVocab") or [])]
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
                elif not moods:
                    hint = (" Try loosening it — one filter at a time — or put "
                            "the caller's own words in as a request instead.")
                return "Nothing in the library matches that combination." + hint
            total = d.get("total")
            head = f"{len(rows)} of {total} matching track(s)" if isinstance(
                total, int) else f"{len(rows)} matching track(s)"
            return (head + ":\n"
                    + "\n".join(_fmt_track(t, with_id=True) for t in rows)
                    + "\nQueue the one they pick with subwave_queue_track.")

        tools.append(browse_library)

    return tools
