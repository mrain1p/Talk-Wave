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
from ..background import spawn
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
    # Every one of these fields comes from the station and goes into the
    # prompt, where length is latency on every turn for the rest of the call
    # and is paid for per token. The count is capped at 8 results; nothing
    # capped the size of one, so a single malformed record — a title that is
    # really a description, a tag dump in an album field — could dwarf the
    # rest of the briefing. A track that needs more than this to name itself
    # is not one the DJ can read out anyway.
    def f(key: str, limit: int = 120) -> str:
        return str(t.get(key) or "")[:limit].strip()

    bits = f"\"{f('title') or '?'}\" by {f('artist') or '?'}"
    if f("album"):
        bits += f" ({f('album')}" + (f", {f('year', 12)})" if f("year", 12) else ")")
    # The station stores mood tags and an energy score per track and returns
    # them on every search hit. Dropping them left the DJ describing records it
    # had real information about purely from the title.
    feel = []
    moods = t.get("moods") or []
    if isinstance(moods, list) and moods:
        feel.extend(str(m)[:40] for m in moods[:3])
    energy = t.get("energy")
    if isinstance(energy, (int, float)):
        feel.append("high energy" if energy >= 0.66
                    else "low energy" if energy <= 0.33 else "mid energy")
    if feel:
        bits += " — " + ", ".join(feel)
    # The exact-queue tool needs the id the search returned. Without it in the
    # text the model has nothing to pass and silently falls back to guessing.
    if with_id and f("id", 64):
        bits += f"  [id: {f('id', 64)}]"
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


# One quick look before the tool answers — a station that resolves fast gets
# the title into the tool result itself, which is the best outcome.
_INLINE_POLL_SECS = 2.0

# How long the station gets to say what a request matched, once the inline
# poll has already missed. Observed on a real call (2026-08-08): the station
# matched "Spiders" by Moby some time after the tool's 2s look, so the DJ told
# the caller "something is lined up" and could not name it — the caller had to
# ask, and the DJ had to go digging through station state to answer. Waiting
# longer inline is the wrong fix: the tool return is latency the caller hears
# as silence. So the tool answers immediately and this schedule keeps looking
# in the background.
_LATE_MATCH_DELAYS = (3.0, 5.0, 8.0, 13.0, 21.0)

# How long the announcer waits for a quiet moment before giving up, in
# half-second beats. A caller mid-sentence, a DJ mid-reply or a broadcast
# hold must not be talked over for the sake of naming a song.
_QUIET_BEATS = 40


def _already_named(session, title: str) -> bool:
    """Did the DJ already tell the caller which track it is?

    Exactly what happened on the call that motivated all this: the caller
    asked, the DJ read the answer off station state, and a late announcement
    on top of that would read as the DJ forgetting the conversation.
    """
    needle = str(title or "").casefold()
    if not needle:
        return False
    try:
        items = list(session.history.items)[-12:]
    except Exception:
        return False
    for item in items:
        if getattr(item, "role", None) != "assistant":
            continue
        content = getattr(item, "content", None)
        text = content if isinstance(content, str) else (
            getattr(item, "text_content", "") or "")
        if needle in str(text).casefold():
            return True
    return False


async def _surface_late_match(
    station: StationClient, rid: str, get_session=None, air=None, record=None,
    delays=None, actions=None,
) -> None:
    """Keep asking the station what a request matched, and pass it on.

    Runs in the background after request_song has already answered. If the
    match lands, the DJ volunteers it at the next quiet moment; if it never
    does, the record says so — a request whose title never surfaced is the
    thing an operator reads the transcript to find.
    """
    track: dict = {}
    position = None
    for delay in (_LATE_MATCH_DELAYS if delays is None else delays):
        # The caller is waiting on THIS, so hold the 'DJ is working' flag across
        # the whole poll — otherwise the idle watcher reads the quiet as the
        # caller having left and asks "still there?" (the Zeppelin call).
        if actions is not None:
            actions.mark_working(delay + 10)
        await asyncio.sleep(delay)
        try:
            st = await station.request_status(str(rid))
        except Exception:
            continue
        t = st.get("track") or st.get("matched") or {}
        if isinstance(t, dict) and t.get("title"):
            track, position = t, st.get("queuePosition")
            break

    if not track:
        if record:
            record.problem(
                "A request went in but the station never said what it matched, "
                "so the caller was never told the track. Either the station's "
                "resolver is slow past the polling window or the request "
                "matched nothing — check the station's request queue."
            )
        return

    if record:
        record.tool("subwave_request_song",
                    f"matched after the tool returned: {_fmt_track(track)}")

    session = get_session() if get_session else None
    if session is None:
        return
    if _already_named(session, track.get("title")):
        return

    # A quiet moment: the DJ is listening, the caller is not mid-word, and
    # the broadcast does not have the microphone. If one never comes, stay
    # quiet — the match is in the history-side receipts either way.
    for _ in range(_QUIET_BEATS):
        busy = (
            str(getattr(session, "agent_state", "") or "") != "listening"
            or str(getattr(session, "user_state", "") or "") == "speaking"
            or bool(getattr(air, "on_air", False))
        )
        if not busy:
            break
        await asyncio.sleep(0.5)
    else:
        return

    try:
        # The bracketed user turn is the same trick the greeting uses: a
        # generation prompted by instructions alone, on a history ending in
        # the DJ's own turn, is exactly the shape weak models answer by
        # re-saying that turn. The note is never spoken and never counts as
        # a caller turn — see handoff.is_prime.
        await session.generate_reply(
            user_input=(
                f"[The station has just resolved the earlier request: it "
                f"matched {_fmt_track(track)}. {_when_it_plays(position)}]"
            ),
            instructions=(
                "Pass the station's pick on to the caller — one short line in "
                "your own voice, and it's queued, not playing yet. If the "
                "conversation has moved on, drop it in lightly and come back "
                "to what you were talking about."
            ),
        )
    except Exception as e:
        log.debug("late-match announcement failed (harmless): %s", e)


def build_library_tools(cfg: dict, station: StationClient, actions: CallActions,
                        get_session=None, air=None, record=None) -> list:
    """Search and request as local tools with deterministic fallbacks.

    Prompt guidance about query phrasing turned out to be soft — the model
    followed it most of the time, and a caller heard "can't pull that from
    the racks" for a track the library holds three copies of. These wrappers
    make good phrasing unnecessary: the tool itself retries with the "by"
    connector stripped before ever reporting a miss.
    """
    from livekit.agents import llm as lk_llm

    tools = []

    # Always built — a read with no side effects, like the MCP reads, so the
    # gate vocabulary has no row for it. Registry: subwave_current_lyrics.
    @lk_llm.function_tool(name="subwave_current_lyrics")
    async def current_lyrics() -> str:
        """The words of the track playing right now. Use when a caller asks
        what the song says, what it means, or which line just played. Comes
        back empty for instrumentals and for stations that keep no lyrics —
        say so plainly, never invent lines."""
        d = await station.current_lyrics()
        lines = [str(l.get("text") or "").strip()
                 for l in (d.get("lines") or []) if isinstance(l, dict)]
        lines = [l for l in lines if l]
        if not lines:
            return ("No lyrics on file for the current track — an "
                    "instrumental, or the station has none indexed. Tell the "
                    "caller that; do not guess at words.")
        # Prompt budget: a full lyric sheet can outweigh the rest of the
        # briefing, and it is paid for on every later turn of the call.
        kept: list[str] = []
        spent = 0
        for line in lines:
            line = line[:160]
            if spent + len(line) > 2000:
                break
            kept.append(line)
            spent += len(line) + 1
        tail = "" if len(kept) == len(lines) else (
            f"\n…and {len(lines) - len(kept)} more lines not shown")
        return "The current track's lyrics:\n" + "\n".join(kept) + tail

    tools.append(current_lyrics)

    # Exact queueing needs the ids that only the local search wrapper surfaces.
    exact_queue = bool(cfg.get("allow_exact_queue")) and not library_search_needs_mcp()

    # Without admin credentials this wrapper can only ever return nothing, so
    # the raw MCP tool takes its place (see library_search_needs_mcp).
    if cfg.get("allow_library_search") and not library_search_needs_mcp():
        @lk_llm.function_tool(name="subwave_search_library")
        async def search_library(q: str, page: int = 1) -> str:
            """Look up a track BY NAME. This is a literal word match against
            titles and artists — nothing else. It cannot find a mood, a vibe,
            a genre or an era: searching "fun" returns songs with the word
            "fun" in the title, which is not what a caller asking for
            "something fun" wants. For anything descriptive use
            subwave_request_song, which resolves it properly. Use this only
            when the caller has named a track or an artist. Results come a
            page at a time; if the caller says none of these are the one,
            call again with the next page number."""
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
            # 8 to a page: enough for "was it one of these", small enough to
            # read down a phone line. One extra row is fetched purely to know
            # whether a next page exists — the station's own answer, not a
            # guess from a full page.
            PAGE = 8
            page = max(1, int(page or 1))
            for attempt in _query_variants(q):
                items = await station.search_library(
                    attempt, offset=(page - 1) * PAGE, limit=PAGE + 1)
                if items:
                    note = "" if attempt == q else (
                        f" (matched on '{attempt}' — the library needs every word to match)"
                    )
                    lines = [_fmt_track(t, with_id=exact_queue)
                             for t in items[:PAGE]]
                    more = (f"\n…more beyond this page — call again with "
                            f"page={page + 1} if none of these are it"
                            ) if len(items) > PAGE else ""
                    joined = "\n".join(lines)
                    head = f"{len(lines)} result(s){note}"
                    if page > 1:
                        head += f", page {page}"
                    return head + ":\n" + joined + more
            if page > 1:
                # An empty deeper page means the results ran out, not that the
                # phrasing needs loosening — don't send the DJ to the request
                # tool over a list it has already read to the caller.
                return (f"Nothing on page {page} — the earlier pages held "
                        "everything that matches.")
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
                await asyncio.sleep(_INLINE_POLL_SECS)
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
                # The station took the request but has not said what it
                # matched yet. Don't hold the tool return hostage to a slow
                # resolver — answer now, and keep asking in the background so
                # the DJ can name the pick when it lands.
                spawn(_surface_late_match(station, str(rid),
                                          get_session=get_session, air=air,
                                          record=record, actions=actions))
                if ack:
                    actions.note("request", text[:120])
                    return f"It's in the queue, not on air yet. Station says: {ack}"
            elif record:
                # No id means no way to ever ask what was matched — the
                # caller can only be told "something". Worth a problem line:
                # this is the call the operator reads back wondering why the
                # DJ never named the song.
                record.problem(
                    "The station accepted a request but returned no request "
                    "id, so what it matched can never be surfaced to the "
                    "caller."
                )
            actions.note("request", text[:120])
            return (
                "Request is in — the station is lining something up. It plays later "
                "in the running order, not now. You don't know yet which track the "
                "station will pick; if it resolves while you're still on the line, "
                "you'll be told — don't guess a title in the meantime."
            )

        tools.append(request_song)

    if cfg.get("allow_favorite"):
        @lk_llm.function_tool(name="subwave_like_track")
        async def like_track() -> str:
            """Add a like to the track playing RIGHT NOW — the same heart a
            listener taps in the app. Use it when the caller says they love
            this one, or asks to favourite what's on. It likes the CURRENT
            record only: there is no way to like some other track from here,
            and no un-like, so don't offer either."""
            if actions.at_limit():
                return actions.refusal()
            np = await station.now_playing()
            track = (np or {}).get("nowPlaying") or {}
            song_id = str(track.get("id") or track.get("songId") or "")
            res = await station.like_track(song_id)
            if not res.get("ok"):
                return (
                    f"That like didn't go through: "
                    f"{res.get('error') or 'the station refused it'}. "
                    "Tell the caller plainly — don't claim it worked."
                )
            name = _fmt_track(track) if track.get("title") else "the current track"
            actions.note("like", name)
            if res.get("alreadyLiked"):
                return (
                    f"Already liked — {name} was on the board already, so the count "
                    "didn't move. Say so warmly."
                )
            count = res.get("count")
            tail = f" That's {count} now." if isinstance(count, int) and count else ""
            return f"Done — added a like to {name}.{tail} Say it back in your own voice."

        tools.append(like_track)

    if cfg.get("allow_unfavorite") and not library_search_needs_mcp():
        @lk_llm.function_tool(name="subwave_unlike_track")
        async def unlike_track() -> str:
            """Remove the operator's heart from the track playing RIGHT NOW.
            Admin only. This undoes the OPERATOR's own curation heart on the
            current record — not a listener's public like, which cannot be
            undone. Use it when a signed-in operator asks to un-favourite what's
            on. Likes the current track only; there is no arbitrary song here."""
            if actions.at_limit():
                return actions.refusal()
            np = await station.now_playing()
            track = (np or {}).get("nowPlaying") or {}
            song_id = str(track.get("id") or track.get("songId") or "")
            res = await station.unlike_track(song_id)
            if not res.get("ok"):
                return (
                    f"That didn't go through: "
                    f"{res.get('error') or 'the station refused it'}. "
                    "Tell the caller plainly — don't claim it worked."
                )
            name = _fmt_track(track) if track.get("title") else "the current track"
            actions.note("unlike", name)
            return f"Done — took the heart off {name}. Say it back in your own voice."

        tools.append(unlike_track)

    return tools


