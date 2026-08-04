"""Library search, requests, and exact queueing.

Local wrappers rather than raw MCP, because prompt guidance about phrasing
turned out to be soft: the model followed it most of the time, and a caller
heard "can't pull that from the racks" for a track the library holds three
copies of. These wrappers make good phrasing unnecessary — the tool itself
retries, and refuses a search that was never a search.
"""

from __future__ import annotations

import asyncio
import logging

from station import StationClient

from ..actions import CallActions
from .registry import library_search_needs_mcp

log = logging.getLogger("callin.agent")


def _query_variants(q: str) -> list[str]:
    """The station's search requires EVERY word to match, so the natural
    phrase "Let It Be by The Beatles" returns nothing — "by" appears in no
    title or artist. Try as given, then with the last " by " connector
    removed, then the left side alone. Rightmost split keeps titles that
    themselves contain "by" ("Stand by Me by Ben E. King") intact."""
    variants = [q]
    idx = q.lower().rfind(" by ")
    if idx > 0:
        variants.append(q[:idx] + " " + q[idx + 4:])
        variants.append(q[:idx])
    return variants


# Words that describe how music FEELS rather than what it's called. A caller
# saying one of these wants the station's picker, not a title match — but the
# model reaches for the search tool anyway, and "fun" dutifully returns
# "Fun, Fun, Fun" by The Beach Boys. Observed on a real call.
_VIBE_WORDS = {
    "fun", "upbeat", "happy", "sad", "chill", "chilled", "chillout", "relaxing",
    "calm", "mellow", "moody", "dark", "bright", "energetic", "energy", "hype",
    "party", "dance", "dancey", "slow", "slower", "fast", "faster", "romantic",
    "sexy", "angry", "aggressive", "soft", "loud", "quiet", "dreamy", "nostalgic",
    "uplifting", "feelgood", "feel-good", "summery", "wintry", "rainy", "sunny",
    "night", "nighttime", "morning", "driving", "workout", "study", "sleep",
    "groovy", "funky", "smooth", "heavy", "light", "epic", "emotional", "vibe",
    "vibes", "mood", "something", "anything", "good", "nice", "cool",
    # The station's own request-slip vocabulary, so the two agree on what
    # counts as a description.
    "sustained", "surprise", "random", "afternoon", "evening", "late-night",
    "latenight", "upbeat", "downbeat", "banger", "bangers", "classic",
    "classics", "oldies", "newer", "older", "similar", "this", "that",
}
# Filler that shouldn't count either way when judging a query.
_VIBE_FILLER = {"a", "an", "the", "some", "me", "for", "and", "or", "of", "to",
                "songs", "song", "music", "track", "tracks", "tune", "tunes",
                "play", "find", "get", "want", "like", "really", "very", "more"}


def looks_like_a_vibe(q: str) -> bool:
    """True when a search query describes a feeling rather than names a track.

    Deliberately conservative: it only fires when EVERY meaningful word is a
    mood word, so "Fun House by The Stooges" and "Mr. Blue Sky" are untouched.
    """
    import re as _re

    words = [w for w in _re.findall(r"[a-z'-]+", (q or "").lower())
             if w not in _VIBE_FILLER]
    if not words or len(words) > 4:
        return False
    return all(w in _VIBE_WORDS for w in words)


def _fmt_track(t: dict, with_id: bool = False) -> str:
    bits = f"\"{t.get('title', '?')}\" by {t.get('artist', '?')}"
    if t.get("album"):
        bits += f" ({t['album']}" + (f", {t['year']})" if t.get("year") else ")")
    # The station stores mood tags and an energy score per track and returns
    # them on every search hit. Dropping them left the DJ describing records it
    # had real information about purely from the title.
    feel = []
    moods = t.get("moods") or []
    if isinstance(moods, list) and moods:
        feel.extend(str(m) for m in moods[:3])
    energy = t.get("energy")
    if isinstance(energy, (int, float)):
        feel.append("high energy" if energy >= 0.66
                    else "low energy" if energy <= 0.33 else "mid energy")
    if feel:
        bits += " — " + ", ".join(feel)
    # The exact-queue tool needs the id the search returned. Without it in the
    # text the model has nothing to pass and silently falls back to guessing.
    if with_id and t.get("id"):
        bits += f"  [id: {t['id']}]"
    return bits


def _when_it_plays(position) -> str:
    """Turn a queue position into something a DJ would actually say.

    The station returns one; without it the DJ could only say "soon", which is
    how a caller ends up being told their song is on when it is four tracks
    away. A position is roughly 3-4 minutes a track.
    """
    try:
        pos = int(position)
    except (TypeError, ValueError):
        return "You don't know how far down the queue it is, so don't guess at a time."
    if pos <= 1:
        return "It's next up, so it plays after the current track."
    return (
        f"It's number {pos} in the queue — roughly {pos * 3}-{pos * 4} minutes away. "
        "You may tell them that."
    )


def build_library_tools(cfg: dict, station: StationClient, actions: CallActions) -> list:
    """Search and request as local tools with deterministic fallbacks.

    Prompt guidance about query phrasing turned out to be soft — the model
    followed it most of the time, and a caller heard "can't pull that from
    the racks" for a track the library holds three copies of. These wrappers
    make good phrasing unnecessary: the tool itself retries with the "by"
    connector stripped before ever reporting a miss.
    """
    from livekit.agents import llm as lk_llm

    tools = []

    # Exact queueing needs the ids that only the local search wrapper surfaces.
    exact_queue = bool(cfg.get("allow_exact_queue")) and not library_search_needs_mcp()

    # Without admin credentials this wrapper can only ever return nothing, so
    # the raw MCP tool takes its place (see library_search_needs_mcp).
    if cfg.get("allow_library_search") and not library_search_needs_mcp():
        @lk_llm.function_tool(name="subwave_search_library")
        async def search_library(q: str) -> str:
            """Look up a track BY NAME. This is a literal word match against
            titles and artists — nothing else. It cannot find a mood, a vibe,
            a genre or an era: searching "fun" returns songs with the word
            "fun" in the title, which is not what a caller asking for
            "something fun" wants. For anything descriptive use
            subwave_request_song, which resolves it properly. Use this only
            when the caller has named a track or an artist."""
            # Backstop for the prompt rule above: a mood word searched by name
            # returns titles containing that word, which reads to the caller
            # like the DJ is flipping through an index. Refuse and redirect
            # rather than hand back junk that looks like an answer.
            if looks_like_a_vibe(q):
                return (
                    f"'{q}' describes a feeling, and this tool only matches words in "
                    "titles and artist names — it would hand you songs with that word "
                    "in the title, not songs that feel that way. Use "
                    "subwave_request_song with the caller's own words instead; the "
                    "station picks properly from the whole library. Do not tell the "
                    "caller a search failed — nothing failed, you just used the wrong "
                    "tool. Put the request in."
                )
            for attempt in _query_variants(q):
                items = await station.search_library(attempt)
                if items:
                    note = "" if attempt == q else (
                        f" (matched on '{attempt}' — the library needs every word to match)"
                    )
                    lines = [_fmt_track(t, with_id=exact_queue) for t in items[:8]]
                    more = f" …and {len(items) - 8} more" if len(items) > 8 else ""
                    joined = "\n".join(lines)
                    return f"{len(items)} result(s){note}:\n" + joined + more
            # The catch-all for everything the vibe word list above misses —
            # and it misses plenty, because no list covers "sustained energy
            # vibes" or "something for late-night driving". A description
            # almost never matches a title literally, so an empty result on a
            # multi-word query is itself the signal that this was never a
            # name search. Deterministic, where a word list is a guess.
            hint = ""
            if len(q.split()) > 1 or looks_like_a_vibe(q):
                hint = (
                    " If that was a description rather than a title — a mood, an "
                    "era, an occasion, 'more like this' — then this was the wrong "
                    "tool and nothing is wrong with the library. Put it in as a "
                    "request with subwave_request_song, in the caller's own words, "
                    "and let the station pick. Do NOT tell the caller you couldn't "
                    "find anything."
                )
            return (
                "No track or artist by that name, even after loosening the "
                "phrasing." + hint
            )

        tools.append(search_library)

    if exact_queue:
        @lk_llm.function_tool(name="subwave_queue_track")
        async def queue_track(id: str, title: str, artist: str = "") -> str:
            """Queue THE EXACT track the caller picked from a search result.
            Use this — not a request — once they have chosen a specific track
            from what you found, passing the id shown beside it. Guarantees
            they get that recording rather than a re-match."""
            if actions.at_limit():
                return actions.refusal()
            res = await station.queue_track(
                {"id": id, "title": title, "artist": artist}
            )
            if not res.get("ok"):
                return (
                    f"That didn't go into the queue: "
                    f"{res.get('error') or 'the station refused it'}. "
                    "Tell the caller plainly — do not claim it worked."
                )
            actions.note("request", _fmt_track({"title": title, "artist": artist}))
            return (
                f"\"{title}\" is in the queue — the exact recording they picked. It "
                "is NOT playing yet: it comes up after what's already ahead of it. "
                f"{_when_it_plays(res.get('queuePosition'))} "
                "There's no auto-intro on this one, so introduce it yourself if you "
                "want it introduced."
            )

        tools.append(queue_track)

    if cfg.get("allow_requests"):
        @lk_llm.function_tool(name="subwave_request_song")
        async def request_song(request: str, requester: str = "") -> str:
            """Ask the station to play something. THIS is the tool for a mood,
            a vibe, a genre or an era — "something fun", "upbeat", "music for
            a rainy night", "anything from the late seventies", "more like
            this". Pass the caller's own words; the station's picker matches
            them against the library properly, which a name search cannot do.
            Also takes a specific track ('Let It Be by The Beatles'). When in
            doubt between this and a name search, use this one."""
            if actions.at_limit():
                return actions.refusal()
            text = request
            idx = request.lower().rfind(" by ")
            if idx > 0 and cfg.get("allow_library_search"):
                # Pre-flight: if the full phrase finds nothing but the
                # cleaned one does, submit the cleaned one — the station's
                # own resolver trips on the same matcher and substitutes a
                # different song rather than failing.
                if not await station.search_library(request):
                    cleaned = request[:idx] + " " + request[idx + 4:]
                    if await station.search_library(cleaned):
                        text = cleaned

            res = await station.submit_request(text, requester or "")
            if res.get("error"):
                return f"The station couldn't take that request: {res['error']}"

            # Every success path below says QUEUED, never playing. Observed on
            # a real call: the DJ took a request and immediately introduced the
            # track on air as though it were spinning, minutes before it was.
            rid = res.get("requestId") or res.get("id")
            if rid:
                await asyncio.sleep(2.0)
                st = await station.request_status(str(rid))
                track = st.get("track") or st.get("matched") or {}
                ack = st.get("ack") or st.get("message") or st.get("reply") or ""
                if isinstance(track, dict) and track.get("title"):
                    actions.note("request", _fmt_track(track))
                    out = (
                        f"Added to the queue: {_fmt_track(track)}. It is NOT playing "
                        "yet — it comes up later in the running order, after what's "
                        f"on now. {_when_it_plays(st.get('queuePosition'))} "
                        "Tell the caller it's lined up, not that it's on."
                    )
                    return out + (f" Station says: {ack}" if ack else "")
                if ack:
                    actions.note("request", text[:120])
                    return f"It's in the queue, not on air yet. Station says: {ack}"
            actions.note("request", text[:120])
            return (
                "Request is in — the station is lining something up. It plays later "
                "in the running order, not now."
            )

        tools.append(request_song)

    return tools


