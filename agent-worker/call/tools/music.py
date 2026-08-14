"""Name search, requests, and the queue.

Local wrappers rather than raw MCP, because prompt guidance about phrasing
turned out to be soft: the model followed it most of the time, and a caller
heard "can't pull that from the racks" for a track the library holds three
copies of. These wrappers make good phrasing unnecessary — the tool itself
retries, and refuses a search that was never a search.

Searching by NAME is only one way into a library, and for a while it was the
only one the call line had. `discovery.py` holds the others, and `rows.py`
holds the shaping every one of them shares.
"""

from __future__ import annotations

import asyncio
import logging
import time

from station import StationClient

from ..actions import CallActions
from ..background import spawn
from .late_match import _surface_late_match
from .registry import library_search_needs_mcp
# Re-exported, not merely used: `music._fmt_track` is a name several tests and
# call sites already reach for, and moving the helpers to their own module is
# not a reason to make every one of them move too.
from .rows import (  # noqa: F401
    _blocked_reason, _drop_blocked, _fmt_track, _query_variants,
    looks_like_a_vibe,
)

log = logging.getLogger("callin.agent")

# How long a station refusal stands before the wrapper will pass another
# request through. Short enough that a caller who waits is not blocked, long
# enough to swallow a burst emitted in one turn.
_REFUSAL_HOLDS_SECS = 20.0


def _recent_refusal(state: dict) -> str:
    """The station's own words, if it refused us moments ago."""
    at = state.get("at") or 0.0
    if not at or time.monotonic() - at > _REFUSAL_HOLDS_SECS:
        return ""
    return str(state.get("why") or "")


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
    # One per call, closed over by request_song — see _recent_refusal.
    refusals: dict = {}

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
                    # Page detection reads the RAW count: the extra row is
                    # fetched to prove another page exists, and a blocked row
                    # still proves it.
                    raw = len(items)
                    items, withheld = _drop_blocked(items)
                    if not items:
                        # The library holds it; this station has decided never
                        # to air it. Saying "we haven't got it" would be a lie
                        # the caller can check, and sending the DJ off to
                        # re-phrase or to the request tool just walks it into
                        # the same refusal at the queue gate.
                        return (
                            f"Every match for '{attempt}' is on this station's "
                            "never-play list, so none of them can be queued. The "
                            "library HAS the music — don't tell the caller it "
                            "doesn't. Say it isn't one this station plays, and "
                            "offer to find something else."
                        )
                    note = "" if attempt == q else (
                        f" (matched on '{attempt}' — the library needs every word to match)"
                    )
                    lines = [_fmt_track(t, with_id=exact_queue)
                             for t in items[:PAGE]]
                    more = (f"\n…more beyond this page — call again with "
                            f"page={page + 1} if none of these are it"
                            ) if raw > PAGE else ""
                    joined = "\n".join(lines)
                    head = f"{len(lines)} result(s){note}"
                    if page > 1:
                        head += f", page {page}"
                    if withheld:
                        # Named rather than silent: the DJ hearing "8 results"
                        # for a search that really found ten, with two it may
                        # not offer, is how it ends up promising one anyway.
                        head += (f" ({withheld} more matched but are on the "
                                 "station's never-play list — do not offer them)")
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

        # Same switch and same credentials as the search above: both answer
        # "what have you got", so there is no separate toggle to forget.
        @lk_llm.function_tool(name="subwave_recent_tracks")
        async def recent_tracks() -> str:
            """What's NEW in the library — the most recently added tracks,
            newest first. Use when a caller asks what's new, what just came
            in, or what's worth a first spin. A read only: if they want one
            played, put it through as a request (or queue the exact pick if
            they choose from this list)."""
            items = await station.recent_tracks()
            if not items:
                return (
                    "The station didn't say what's new — the recently-added "
                    "shelf may be empty or unreachable right now. Tell the "
                    "caller you can't see the new arrivals; don't invent any."
                )
            items, _withheld = _drop_blocked(items)
            if not items:
                return (
                    "Everything on the new-arrivals shelf is on this station's "
                    "never-play list, so there is nothing here you can offer. "
                    "Say there's nothing new worth spinning rather than reading "
                    "out records that cannot be played."
                )
            # 8 lines, like a search page: enough to browse down a phone
            # line, small enough not to weigh on every later turn.
            lines = [_fmt_track(t, with_id=exact_queue) for t in items[:8]]
            return ("The newest arrivals in the library, newest first:\n"
                    + "\n".join(lines))

        tools.append(recent_tracks)

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

    # Deliberately NOT tied to exact_queue: a caller who can only request can
    # still change their mind, and the thing they want undone is usually the
    # request, not a pick from a search page.
    if cfg.get("allow_cancel_queue") and not library_search_needs_mcp():
        @lk_llm.function_tool(name="subwave_cancel_queued_track")
        async def cancel_queued_track(id: str = "", title: str = "") -> str:
            """Take a track back OUT of the queue before it airs — the caller
            changed their mind, or you queued the wrong one. Pass the id if
            you have it from a search result or a queue read; otherwise pass
            the title and it will be matched against what's actually queued.
            Cannot touch the track on air or the one being cued up next: for
            that, there is only skipping."""
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

        tools.append(cancel_queued_track)

    if cfg.get("allow_requests"):
        @lk_llm.function_tool(name="subwave_request_song")
        async def request_song(request: str, requester: str = "") -> str:
            """Ask the station to play something. THIS is the tool for a mood,
            a vibe, a genre or an era — "something fun", "upbeat", "music for
            a rainy night", "anything from the late seventies", "more like
            this". Pass the caller's own words; the station's picker matches
            them against the library properly, which a name search cannot do.
            Also takes a specific track ('Let It Be by The Beatles'). When in
            doubt between this and a name search, use this one. MUSIC ONLY:
            this queues a track, nothing else — it can never change the show,
            the DJ, or anything about the schedule, and must not be used to
            stand in for those."""
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

            # A refusal the station just gave is still true a second later.
            # The conduct says "don't retry a refusal" and on a real call
            # (2026-08-13) the DJ fired this four times, twice inside the same
            # second, collecting two identical rate-limit refusals — prompt
            # rules cannot govern a model that emits parallel tool calls, so
            # the wrapper holds the line instead.
            held = _recent_refusal(refusals)
            if held:
                return (
                    f"Still the same answer from the station: {held} Do NOT "
                    "send it again — you already have the reason, and asking "
                    "twice a second makes it worse. Tell the caller what the "
                    "station said, and either wait it out or use a tool that "
                    "isn't rate-limited."
                )

            res = await station.submit_request(text, requester or "")
            if res.get("error"):
                refusals["at"] = time.monotonic()
                refusals["why"] = str(res["error"])
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

    return tools


