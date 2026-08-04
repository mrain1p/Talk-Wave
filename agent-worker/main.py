"""
LiveKit Agents worker — the SUB/WAVE call-in DJ.

Flow: caller opens the widget -> widget asks the token server for a join token
-> LiveKit dispatches a job here -> this worker resolves whoever is live on
air, builds that persona's prompt from the station, and runs an STT -> LLM ->
TTS voice session with the station's own MCP tools attached.

Two deliberate "don't duplicate the station" choices:

  * Station actions go through the station's MCP server, not a re-wrapped REST
    client. Filtered by an allowlist, because a caller is an untrusted stranger
    driving the agent by voice and the station's MCP surface includes
    destructive tools (skip_track, play_sfx, queue_track, dj_segment,
    refresh_playlist). Those are never exposed on a call line.

  * Persona voice comes from the station's own config where readable
    (station_config.py), falling back to persona-voices.json only when the
    station won't say.

Settings are re-read at the start of every call, so changes made in the call
page take effect on the next caller without restarting this worker.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    RoomInputOptions,
    WorkerOptions,
    cli,
    mcp,
)
# All plugins are imported at module scope on purpose. LiveKit registers
# plugins at import time and requires that to happen on the main thread —
# importing them lazily inside build_stt()/build_llm() raises
# "Plugins must be registered on the main thread" once a job is running.
from livekit.agents.types import NOT_GIVEN
from livekit.plugins import anthropic, deepgram, google, openai, silero

import prompts
import secrets_store
import settings as settings_store
import station_config as station_config_mod
from station import StationClient
from station_config import StationConfig
from tts_adapter import AdapterTTS

load_dotenv(Path(__file__).parent.parent / ".env")

import log_setup

log_setup.setup("worker")
log = logging.getLogger("callin.agent")

def station_mcp_url() -> str:
    """Resolved per call, so re-homing the sidecar to another station from the
    settings page takes effect on the next caller."""
    return settings_store.station_mcp_url()

# Reads are always available to the agent; writes are toggled in settings.
READ_TOOLS = [
    "subwave_health",
    "subwave_now_playing",
    "subwave_station_state",
    "subwave_schedule",
    "subwave_session",
]

# Deliberately never exposed on a call line, regardless of settings:
# subwave_skip_track (a caller could cut off whatever is playing),
# subwave_queue_track (bypasses the request queue and its rate limits),
# subwave_play_sfx / subwave_list_sfx (stingers on a stranger's say-so),
# subwave_dj_segment and subwave_refresh_playlist (station-level programming).
OPTIONAL_TOOLS = {
    "allow_requests": ["subwave_request_song", "subwave_request_status"],
    # This list once carried four guesses at a sounds-like/semantic search
    # tool, pre-authorised in case the station shipped one. The published MCP
    # reference has no such tool, so they were names that could never match —
    # and an allowlist entry that matches nothing is indistinguishable from a
    # typo in one that should. Add it back when it actually exists.
    "allow_library_search": ["subwave_search_library"],
    "allow_announcements": ["subwave_dj_announce"],
    # The station's own segments — weather, news, dedications, story time.
    # list_skills is paired so the DJ knows what it can actually run rather
    # than guessing at names.
    "allow_skills": ["subwave_run_skill", "subwave_list_skills"],
}


# Tools that make the on-air DJ produce sound. Always served by local wrappers
# instead of raw MCP: the wrappers hold the overlap guard, and — the reason
# they're unconditional now — MCP's 15s session timeout turned a segment that
# was audibly playing into "that didn't work" for the caller. Running a segment
# legitimately takes longer than any read.
ON_AIR_TOOLS = {"subwave_dj_announce", "subwave_run_skill"}

# Served by local wrappers with retry/fallback logic (build_library_tools) —
# never exposed raw over MCP, or the model could reach the fragile version.
LOCALLY_WRAPPED = {"subwave_search_library", "subwave_request_song",
                   "subwave_queue_track"}


def library_search_needs_mcp() -> bool:
    """Whether library search has to go over MCP rather than our wrapper.

    The wrapper is better when it works — it retries with the "by" connector
    stripped — but it reads the station's REST /dj/search, which is admin-only.
    Without credentials it can only ever return nothing, which reaches the
    caller as "haven't got that one in the racks" for a track the library
    holds. The MCP tool needs no auth at all, so with no credentials the raw
    tool is strictly better than a wrapper that cannot succeed.
    """
    from station_config import admin_credentials

    user, password = admin_credentials()
    return not (user and password)


def build_allowed_tools(cfg: dict, *, guarded: bool = False) -> list[str]:
    """`guarded` is kept for callers that ask about the overlap guard, but the
    on-air tools come off the MCP list either way — they are always wrapped."""
    allowed = list(READ_TOOLS)
    for flag, tools in OPTIONAL_TOOLS.items():
        if cfg.get(flag):
            allowed.extend(tools)

    wrapped = set(LOCALLY_WRAPPED)
    if cfg.get("allow_library_search") and library_search_needs_mcp():
        wrapped.discard("subwave_search_library")
    return [t for t in allowed if t not in wrapped and t not in ON_AIR_TOOLS]


# Fire-and-forget tasks need a strong reference held somewhere, or the event
# loop's weak reference is the only one and the task can be garbage-collected
# mid-execution. For us that means an action card or an on-air state change
# that goes missing at random, which is worse than one that never existed.
_background: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


class CallActions:
    """Per-call record of what the caller actually made happen.

    Two jobs, both of which need the same ledger:

      * a ceiling on how much one call can set in motion, so a single caller
        can't fill the request queue or fire segment after segment;
      * a signal to the widget when an action really lands, so the caller sees
        "Song scheduled" as its own line in the transcript instead of having to
        take the DJ's word for it.

    Only SUCCESSFUL actions are counted and announced. An attempt the station
    refused costs the caller nothing and shows nothing.
    """

    # What the widget renders for each kind. Kept here rather than in the page
    # so a new action type can't ship with no label.
    LABELS = {
        "request": ("🎵", "Song request scheduled"),
        "announcement": ("📢", "Message sent to air"),
        "skill": ("🎙", "Station segment running"),
    }

    def __init__(self, limit: int, room=None) -> None:
        self.limit = max(0, int(limit or 0))
        self.count = 0
        self._room = room

    def at_limit(self) -> bool:
        return self.limit > 0 and self.count >= self.limit

    def refusal(self) -> str:
        """In-world, and explicit that this is the line's rule rather than the
        station refusing — otherwise the DJ invents a reason."""
        return (
            f"You've already put {self.count} things through for this caller, which "
            "is the limit for one call. Don't do any more of those — say warmly that "
            "you'll have to leave it there for this call and they're welcome to ring "
            "back. Do not blame the station or invent a technical reason."
        )

    def note(self, kind: str, detail: str = "") -> None:
        self.count += 1
        icon, label = self.LABELS.get(kind, ("✅", "Action completed"))
        log.info("caller action %d/%s: %s — %s", self.count, self.limit or "∞", kind, detail)
        if self._room is None:
            return
        try:
            import json as _json

            payload = _json.dumps({
                "type": "action", "kind": kind, "icon": icon,
                "label": label, "detail": detail,
            }).encode()
            # Fire-and-forget: a caption card is never worth delaying a tool
            # return (and so never worth failing the action over).
            _spawn(
                self._room.local_participant.publish_data(
                    payload, reliable=True, topic="wavetalk.action"
                )
            )
        except Exception as e:
            log.debug("action card publish failed (harmless): %s", e)


class OnAirGuard:
    """Shared "is the broadcast actually talking right now" state for one call.

    The call DJ and the on-air DJ are the same person. Left alone they talk
    over each other — the caller hears two of the same voice, and so does
    everyone listening to the station. This is the single place that decides
    whether the air is busy; the reply gate, the on-air tools and the widget's
    status chip all read it, so they cannot disagree with each other.

    "Busy" means ACTIVELY SPEAKING — not thinking, not queued. It's derived
    from when the station last logged on-air speech, held for
    `on_air_quiet_secs` (roughly how long a link runs), because the station
    tells us when a link STARTED, not when it finished.
    """

    POLL_SECS = 4.0     # a station read per call every 4s, not per turn
    MAX_HOLD = 45.0     # never leave a caller in silence longer than this

    def __init__(self, station: StationClient, cfg: dict, room=None) -> None:
        self.station = station
        self.room = room
        self.enabled = bool(cfg.get("avoid_on_air_overlap"))
        self.quiet_secs = float(cfg.get("on_air_quiet_secs") or 0)
        self.on_air = False
        self._clear = asyncio.Event()
        self._clear.set()

    def _publish(self, on_air: bool) -> None:
        """Tell the widget, so the caller sees "DJ is on air" rather than a
        DJ that has mysteriously gone quiet."""
        if self.room is None:
            return
        try:
            _spawn(
                self.room.local_participant.set_attributes(
                    {"wavetalk.onair": "1" if on_air else ""}
                )
            )
        except Exception as e:
            log.debug("on-air state publish failed (harmless): %s", e)

    async def wait_until_clear(self, timeout: float | None = None) -> float:
        """Block until the broadcast is quiet. Returns the seconds waited, so
        the caller can be told why there was a pause."""
        if not self.enabled or self._clear.is_set():
            return 0.0
        import time as _t

        started = _t.time()
        try:
            await asyncio.wait_for(self._clear.wait(), timeout or self.MAX_HOLD)
        except asyncio.TimeoutError:
            # Dead air is worse than an overlap. If the station has been
            # "speaking" for longer than any real link, assume the log is
            # stale and let the call carry on.
            log.warning("air still busy after %.0fs — letting the call continue",
                        timeout or self.MAX_HOLD)
            self._clear.set()
        return _t.time() - started

    async def watch(self, session: AgentSession) -> None:
        """Poll the station and flip the gate. Started as a task for the life
        of the call."""
        if not (self.enabled and self.quiet_secs > 0):
            return
        # The first pass runs immediately and silently: someone who dials in
        # mid-link should have the gate already closed (so their first reply
        # waits) without the greeting being cut off by a hand-over line for a
        # broadcast that was already running when they picked up the phone.
        first = True
        while True:
            try:
                since = await self.station.seconds_since_on_air_speech()
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.debug("on-air check failed (assuming clear): %s", e)
                since = None

            busy = since is not None and since < self.quiet_secs
            if busy != self.on_air:
                self.on_air = busy
                self._publish(busy)
                if busy:
                    self._clear.clear()
                    log.info("on-air DJ is speaking — holding the call DJ back")
                    if not first:
                        # Cut the call DJ off mid-sentence if need be: the whole
                        # point is that the broadcast never hears itself doubled.
                        try:
                            session.interrupt()
                            session.say(
                                "Hold on a second — let me let that go out on air first.",
                                allow_interruptions=False,
                            )
                        except Exception as e:
                            log.debug("could not hand over to air cleanly: %s", e)
                else:
                    self._clear.set()
                    log.info("air is clear — the call DJ has the floor again")
            first = False
            await asyncio.sleep(self.POLL_SECS)


class CallAgent(Agent):
    """The caller's DJ, with one addition: its replies wait for quiet air.

    Holding here rather than dropping input is deliberate. The caller's words
    are already transcribed and in the context by this point — only the REPLY
    is queued, so nothing they said is lost and they never have to repeat
    themselves just because the station was mid-link.
    """

    def __init__(self, instructions: str, guard: OnAirGuard) -> None:
        super().__init__(instructions=instructions)
        self._guard = guard

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        waited = await self._guard.wait_until_clear()
        if waited >= 2:
            log.info("held the caller's reply %.0fs while the on-air DJ was talking",
                     waited)


def build_on_air_tools(
    cfg: dict,
    station: StationClient,
    actions: CallActions,
    guard: OnAirGuard,
    guarded: bool = True,
) -> list:
    """On-air actions that keep the call and the broadcast from colliding.

    The call DJ and the on-air DJ are the same persona, so the two can end up
    talking at once. Rather than blocking the action (which just makes the DJ
    seem broken to the caller), these wait for the air to clear if it's busy,
    fire the action, then tell the agent to step back from the call while it
    plays — with a word to the caller either side, the way a real presenter
    would say "hold on, I'm on air".

    With the guard off the wrappers still stand in for the raw MCP tools —
    they're what keeps a slow-but-successful station action from being
    reported to the caller as a failure.
    """
    from livekit.agents import llm as lk_llm

    async def wait_for_clear_air() -> float:
        """Block until the on-air DJ stops. One source of truth (the guard),
        so a tool can't decide the air is clear while the reply gate thinks
        it's busy. Capped shorter than the guard's own limit — a caller
        waiting on an action they asked for needs an answer sooner."""
        if not guarded:
            return 0.0
        return await guard.wait_until_clear(timeout=20.0)

    def after_action(what: str, waited: float, unconfirmed: bool = False) -> str:
        note = f"Waited {waited:.0f}s for the air to clear. " if waited >= 2 else ""
        # A slow confirmation is not a failure: the station took the action,
        # it just hadn't finished answering. Say it went through.
        if unconfirmed:
            note += "The station was slow to confirm, but it has gone through. "
        if not guarded:
            return (
                f"{note}{what} is going out on air now, in your own voice. Tell "
                "the caller it's done, in your own words."
            )
        return (
            f"{note}{what} is going out on air now, in your own voice, and it runs "
            "roughly twenty seconds. You cannot be in two places at once: tell the "
            "caller briefly that you're on air for a moment, then stay quiet until "
            "it's done — do not talk over yourself. When it finishes, come back to "
            "them and pick the conversation up where you left it."
        )

    tools = []

    if cfg.get("allow_announcements"):
        @lk_llm.function_tool(name="subwave_dj_announce")
        async def announce(message: str, mode: str = "styled") -> str:
            """Put a short line on air, read by the on-air DJ in its own voice.
            Use for shoutouts, dedications, or anything from the call worth
            sharing with listeners."""
            if actions.at_limit():
                return actions.refusal()
            waited = await wait_for_clear_air()
            result = await station.dj_say(message, mode=mode, kind="callin")
            if not result.get("ok"):
                return (
                    f"That didn't go out: {result.get('error') or 'the station refused it'}. "
                    "Tell the caller plainly — do not claim it worked."
                )
            actions.note("announcement", message[:120])
            return after_action("Your announcement", waited, result.get("unconfirmed"))

        tools.append(announce)

    if cfg.get("allow_skills"):
        @lk_llm.function_tool(name="subwave_run_skill")
        async def run_skill(name: str) -> str:
            """Run one of the station's own segments on air by name — for
            example weather, news, dedication, shoutout, storytime."""
            if actions.at_limit():
                return actions.refusal()
            waited = await wait_for_clear_air()
            result = await station.run_skill(name)
            if not result.get("ok"):
                return (
                    f"That segment didn't run: "
                    f"{result.get('error') or 'the station refused it'}. "
                    "Tell the caller plainly — do not claim it worked."
                )
            actions.note("skill", name)
            return after_action(f"The {name} segment", waited, result.get("unconfirmed"))

        tools.append(run_skill)

    return tools


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


def _clock_start() -> float:
    import time as _t

    return _t.time()


def build_call_control_tools(ctx: JobContext, session_ref: dict, started_at: float) -> list:
    """Lets the DJ hang up, the way a presenter closes a call.

    Until now a finished conversation just sat there: the caller had said
    goodbye, the DJ had said goodbye, and the line stayed open until the idle
    watcher nudged twice or the hard limit hit. A real DJ says "anything else
    before I let you go?" and then ends it.

    Two guards, because a model that decides to hang up early is worse than
    one that lingers:
      * nothing can end a call in its first minute, whatever the model thinks;
      * the goodbye is allowed to finish playing before the room closes.
    """
    from livekit.agents import llm as lk_llm

    import time as _t

    MIN_CALL_SECS = 60.0
    ending = {"done": False}

    @lk_llm.function_tool(name="end_call")
    async def end_call(reason: str = "") -> str:
        """Hang up. Use ONLY once the caller has confirmed they're done — you
        asked if there was anything else and they said no, or they said
        goodbye. Say your sign-off in the same turn you call this; the line
        stays open long enough for it to play. Never use this to cut a
        conversation short."""
        elapsed = _t.time() - started_at
        if elapsed < MIN_CALL_SECS:
            return (
                "Too early to hang up — you've barely picked up. Stay with the "
                "caller and see what they actually want."
            )
        if ending["done"]:
            return "Already wrapping up — just finish your sign-off."
        ending["done"] = True

        async def _close() -> None:
            session = session_ref.get("session")
            # Let the sign-off play out. Poll rather than guess a duration: a
            # fixed sleep either clips a warm goodbye or leaves dead air after
            # a curt one.
            deadline = _t.time() + 20.0
            await asyncio.sleep(1.0)
            while _t.time() < deadline:
                if getattr(session, "agent_state", None) != "speaking":
                    break
                await asyncio.sleep(0.5)
            await asyncio.sleep(0.8)      # a beat after the last word
            await _end_call(ctx, f"the DJ wrapped up the call ({reason or 'done'})")

        _spawn(_close())
        return (
            "Right — say your goodbye now, one line, in character. The line closes "
            "as soon as you've finished speaking."
        )

    return [end_call]


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


def model_for(provider: str, requested: str, choices: dict, default: str) -> str:
    """Model names are provider-specific and must never survive a provider
    switch. Carrying one across produces a 404 on every single utterance —
    e.g. Deepgram's "nova-3" sent to OpenAI — which looks like the caller
    simply isn't being heard.
    """
    requested = (requested or "").strip()
    if requested and requested in choices.get(provider, []):
        return requested
    if requested:
        log.warning(
            "%s is not a %s model — using %s instead", requested, provider, default
        )
    return default


def effective_stt(cfg: dict) -> tuple[str, str, str]:
    """Resolve (provider, model, note) actually used, accounting for missing
    keys. Exposed so the settings page can show what will really run rather
    than what was merely selected."""
    wanted = str(cfg.get("stt_provider", "deepgram")).lower()
    requested = cfg.get("stt_model") or ""
    choices = settings_store.STT_MODEL_CHOICES
    note = ""

    if wanted == "local":
        provider, default = "local", "base.en"
    elif wanted == "deepgram" and os.environ.get("DEEPGRAM_API_KEY"):
        provider, default = "deepgram", "nova-3"
    elif wanted == "openai" or (wanted == "deepgram" and os.environ.get("OPENAI_API_KEY")):
        provider, default = "openai", "gpt-4o-mini-transcribe"
        if wanted == "deepgram":
            note = "no Deepgram key — using OpenAI STT"
    else:
        provider, default = "google", ""
        if wanted != "google":
            note = f"no usable key for {wanted} — falling back to Google STT"

    model = model_for(provider, requested, choices, default)
    if requested and model != requested:
        note = (note + "; " if note else "") + f"'{requested}' is not a {provider} model"
    return provider, model, note


def build_stt(cfg: dict):
    provider, model, note = effective_stt(cfg)
    if note:
        log.warning("STT: %s", note)

    if provider == "local":
        from local_stt import LocalWhisperSTT

        return LocalWhisperSTT(model=model, language="en")
    if provider == "deepgram":
        return deepgram.STT(model=model, language="en-US")
    if provider == "openai":
        return openai.STT(model=model, language="en")
    return google.STT(languages="en-US")


def build_llm(cfg: dict):
    provider = str(cfg.get("llm_provider", "openai")).lower()
    # Same hazard as STT: a model left over from another provider.  The
    # discovered lists are authoritative when available, so only drop a model
    # that clearly belongs to a different provider.
    model = cfg.get("llm_model") or None
    if model and provider in settings_store.MODEL_CHOICES:
        wrong = [p for p, ms in settings_store.MODEL_CHOICES.items()
                 if p != provider and model in ms]
        if wrong:
            log.warning("%s is a %s model, not %s — using the provider default",
                        model, wrong[0], provider)
            model = None
    base_url = str(cfg.get("llm_base_url") or "").strip()
    temperature = float(cfg.get("llm_temperature", 0.8))

    if provider == "ollama":
        # Ollama speaks the OpenAI protocol. Tool calling depends on the model
        # supporting it — qwen/llama3.1-class models do, many smaller ones
        # silently don't, which shows up as a DJ that never actually submits
        # a request. Use the Test button in settings to check.
        return openai.LLM.with_ollama(
            model=model or "llama3.1",
            base_url=base_url or os.environ.get(
                "OLLAMA_BASE_URL", "http://localhost:11434/v1"
            ),
            temperature=temperature,
        )

    if provider == "openrouter":
        # One key, ~340 models including free tiers. Its model listing is
        # public, so the settings page can populate before a key is entered.
        return openai.LLM.with_openrouter(
            model=model or "auto",
            api_key=os.environ.get("OPENROUTER_API_KEY"),
            temperature=temperature,
        )

    if provider == "openai":
        return openai.LLM(
            model=model or "gpt-4.1-mini",
            temperature=temperature,
            **({"base_url": base_url} if base_url else {}),
        )
    if provider == "google":
        return google.LLM(
            model=model or "gemini-2.5-flash",
            api_key=os.environ.get("GOOGLE_API_KEY"),
            temperature=temperature,
        )
    if provider == "anthropic":
        return anthropic.LLM(model=model or "claude-sonnet-5", temperature=temperature)

    raise ValueError(f"Unsupported llm_provider: {provider}")


def build_tts(cfg: dict, voice: str) -> AdapterTTS:
    adapter_path = cfg.get("tts_adapter") or None
    if adapter_path and not os.path.isabs(adapter_path):
        candidate = Path(__file__).parent / "tts-adapters" / adapter_path
        if candidate.exists():
            adapter_path = str(candidate)

    # settings.tts_mode drives the default adapter choice inside tts_adapter.
    os.environ["TTS_MODE"] = str(cfg.get("tts_mode", "cloud"))

    return AdapterTTS(
        voice=voice,
        base_url=cfg.get("tts_base_url") or os.environ.get("TTS_BASE_URL", ""),
        adapter_path=adapter_path,
        model=cfg.get("tts_model") or "",
    )


def prewarm(proc: JobProcess) -> None:
    """Load VAD once per worker process instead of once per call."""
    proc.userdata["vad"] = silero.VAD.load()

    # If local STT is selected, load the model now — otherwise the first
    # caller waits ~7s mid-call for it.
    try:
        cfg = settings_store.load()
        if str(cfg.get("stt_provider", "")).lower() == "local":
            from local_stt import preload_sync

            preload_sync(cfg.get("stt_model") or "base.en")
    except Exception as e:
        log.warning("local STT prewarm skipped: %s", e)


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    # Media-path probes from the pipeline check are real rooms, but answering
    # one with a full agent session would spend an LLM+TTS round on nothing.
    if ctx.room.name.startswith("probe-"):
        log.info("media-path probe %s — not starting an agent session", ctx.room.name)
        return

    # Keys entered in the settings page live in their own store; push them into
    # the environment before building providers, since the SDKs read env.
    secrets_store.apply_to_env()
    cfg = settings_store.load()

    # Publish the resolved mode before anything resolves a voice — the voice
    # registries for cloud and local are not interchangeable, and this used to
    # be set later (inside build_tts), so the first call of a session could
    # resolve a voice against the wrong one.
    os.environ["TTS_MODE"] = str(cfg.get("tts_mode", "cloud"))

    station = StationClient()
    station_cfg = StationConfig()
    ctx.add_shutdown_callback(station.aclose)
    ctx.add_shutdown_callback(station_cfg.aclose)

    # One button, whoever is live answers — unless a persona is pinned in
    # settings, which is mainly a testing affordance.
    # One concurrent snapshot instead of six serial reads — the caller hears
    # every millisecond of this as ringing before the DJ picks up.
    snap = await station.snapshot(with_skills=bool(cfg.get("allow_skills")))

    override = str(cfg.get("persona_override") or "").strip()
    roster = {p.get("id"): p for p in snap["personas"] if p.get("id")}
    if override == settings_store.RANDOM_PERSONA and roster:
        import random

        persona = roster[random.choice(list(roster))]
        log.info("persona rolled for this call: %s (%s)", persona.get("name"), persona.get("id"))
    elif override and override in roster:
        persona = roster[override]
        log.info("persona pinned by settings: %s", override)
    else:
        persona = station.persona_from(snap["dj"], snap["personas"])

    # Whose voice this is, so the model writing "Francesca:" as a script label
    # never gets read out as part of the line.
    import speech_filter

    speech_filter.set_speaker(persona.get("name", ""))

    persona_id = persona["id"]
    voice = str(cfg.get("tts_voice") or "").strip() or await station_cfg.voice_for(persona_id)

    instructions = await prompts.build_system_prompt(station, persona, snapshot=snap)

    # On-air actions and library actions are always served by local wrappers;
    # the overlap guard only decides whether they wait for quiet air first.
    guard_overlap = bool(cfg.get("avoid_on_air_overlap"))
    actions = CallActions(cfg.get("max_actions_per_call"), room=ctx.room)
    air = OnAirGuard(station, cfg, room=ctx.room)
    allowed_tools = build_allowed_tools(cfg, guarded=guard_overlap)
    local_tools = build_on_air_tools(cfg, station, actions, air, guarded=guard_overlap)
    local_tools += build_library_tools(cfg, station, actions)
    # The session doesn't exist yet — tools are built first — so the hang-up
    # tool reads it from a holder that entrypoint fills in below.
    session_ref: dict = {}
    local_tools += build_call_control_tools(ctx, session_ref, _clock_start())

    log.info(
        "call starting room=%s persona=%s (%s) llm=%s/%s tts=%s voice=%s tools=%d",
        ctx.room.name, persona["name"], persona_id,
        cfg["llm_provider"], cfg["llm_model"], cfg["tts_mode"], voice,
        len(allowed_tools) + len(local_tools),
    )

    # Several station tools (search_library among them) are admin-gated and
    # are rejected outright without credentials, which surfaces mid-call as the
    # DJ saying it's "locked out of the controls".
    mcp_headers = station_config_mod.mcp_headers()
    if not mcp_headers:
        log.warning(
            "no station admin credentials — admin-gated tools like "
            "subwave_search_library will be refused during the call"
        )

    station_tools = mcp.MCPServerHTTP(
        url=station_mcp_url(),
        transport_type="streamable_http",
        allowed_tools=allowed_tools,
        headers=mcp_headers or None,
        client_session_timeout_seconds=15,
    )

    idle_secs = int(cfg.get("idle_prompt_secs") or 0)

    session = AgentSession(
        stt=build_stt(cfg),
        llm=build_llm(cfg),
        tts=build_tts(cfg, voice),
        vad=ctx.proc.userdata["vad"],
        mcp_servers=[station_tools],
        tools=local_tools or NOT_GIVEN,
        preemptive_generation=True,
    )

    session_ref["session"] = session

    await session.start(
        agent=CallAgent(instructions, air),
        room=ctx.room,
        room_input_options=RoomInputOptions(close_on_disconnect=True),
    )

    # The broadcast hierarchy: while the on-air DJ has the microphone, the
    # call DJ waits. Started after the session so the watcher can interrupt it.
    air_task = asyncio.create_task(air.watch(session))
    ctx.add_shutdown_callback(lambda: _cancel(air_task))

    # When a provider gives up after all its retries (observed: Gemini
    # flash-lite 503ing under load), the caller must never get dead air.
    # The DJ can't THINK without the LLM, but it can still SPEAK — say()
    # drives the TTS directly, no model involved.
    import time as _time_mod

    last_sorry = {"t": 0.0}

    def _on_session_error(ev) -> None:
        err = getattr(ev, "error", None)
        log.warning("session error (source=%s): %s", getattr(ev, "source", "?"), err)
        if getattr(err, "recoverable", False):
            return
        if _time_mod.time() - last_sorry["t"] < 20:
            return
        last_sorry["t"] = _time_mod.time()

        async def _apologise() -> None:
            try:
                await session.say(
                    "The line's giving me trouble on my end — hang tight a "
                    "second, or try me again in a minute."
                )
            except Exception:
                pass  # if the voice is what failed, silence is unavoidable

        _spawn(_apologise())

    session.on("error", _on_session_error)

    # Every finalized caller utterance goes in the log. Without this, a
    # "the DJ didn't answer me" report is undiagnosable: a missing heard:
    # line means STT/VAD never caught the words; a heard: line with no
    # reply following points at the LLM/TTS leg.
    import time as _clock

    call_t0 = _clock.time()
    heard_count = {"n": 0}

    def _log_heard(ev) -> None:
        text = str(getattr(ev, "transcript", "") or "").strip()
        if text and getattr(ev, "is_final", True):
            heard_count["n"] += 1
            log.info("heard: %s", text[:160])

    session.on("user_input_transcribed", _log_heard)

    # --- silence handling -------------------------------------------------
    # A caller who goes quiet gets checked on in character, then let go. Dead
    # air on a phone call is worse than a graceful goodbye, and an abandoned
    # tab would otherwise hold a line open until the hard time limit.
    #
    # Silence means NO DISCERNIBLE LANGUAGE, deliberately not "no sound":
    # the SDK's away-state rides the VAD, and background noise — a TV, the
    # station bleeding in, room hiss — kept resetting it, so the check-in
    # never fired in any real room. Only a transcript with actual words
    # counts as the caller being present; the clock starts each time the
    # DJ finishes talking (a caller quietly listening isn't idle).
    if idle_secs > 0:
        import time as _time

        max_nudges = int(cfg.get("idle_max_nudges") or 0)
        state = {"last_words": _time.time(), "nudges": 0}

        def _on_transcript(ev) -> None:
            text = str(getattr(ev, "transcript", "") or "")
            if text.strip():
                state["last_words"] = _time.time()
                state["nudges"] = 0

        session.on("user_input_transcribed", _on_transcript)

        async def _idle_watch() -> None:
            while True:
                await asyncio.sleep(1.0)
                # The clock only runs while the DJ is actually LISTENING.
                # Pinning it during speaking/thinking means the count always
                # starts fresh the moment the DJ stops talking — a long
                # monologue can never expire the timer mid-sentence, which
                # used to fire a check-in on the heels of the DJ's own turn.
                if getattr(session, "agent_state", None) != "listening":
                    state["last_words"] = _time.time()
                    continue
                if _time.time() - state["last_words"] < idle_secs:
                    continue
                if state["nudges"] >= max_nudges:
                    continue
                state["nudges"] += 1
                state["last_words"] = _time.time()
                first = state["nudges"] == 1
                log.info("no words from the caller for %ss — check-in %d/%d",
                         idle_secs, state["nudges"], max_nudges)
                if first and max_nudges > 1:
                    try:
                        await session.generate_reply(instructions=(
                            "The caller has gone quiet. Check they're still there — "
                            "one short line in your own voice, warm, no more than a "
                            "few words. Don't repeat yourself or start a new topic."
                        ))
                    except asyncio.CancelledError:
                        return
                    except Exception as e:
                        log.warning("idle check-in failed: %s", e)
                else:
                    # Final strike: whatever happens, the line closes. A
                    # goodbye that fails to generate must not leave the
                    # caller holding a dead line forever.
                    try:
                        await session.generate_reply(instructions=(
                            "Still nothing from the caller. Say a brief goodbye in "
                            "character — you're letting them go and getting back to "
                            "the broadcast. One line, then stop."
                        ))
                    except asyncio.CancelledError:
                        return
                    except Exception as e:
                        log.warning("idle goodbye failed: %s", e)
                        try:
                            await session.say(
                                "I'll let you get back to it — call in any time."
                            )
                        except Exception:
                            pass
                    await asyncio.sleep(6)  # let the goodbye actually play
                    await _end_call(ctx, "caller went quiet")
                    return

        idle_task = asyncio.create_task(_idle_watch())
        ctx.add_shutdown_callback(lambda: _cancel(idle_task))

    # Runs after the caller hangs up, so the station reflects the call.
    async def _on_shutdown() -> None:
        # One greppable line per call: what happened, at a glance.
        log.info(
            "call ended room=%s persona=%s duration=%.0fs caller_turns=%d "
            "llm=%s/%s tts=%s",
            ctx.room.name, persona.get("name"), _clock.time() - call_t0,
            heard_count["n"], cfg.get("llm_provider"), cfg.get("llm_model"),
            cfg.get("tts_mode"),
        )
        await release_call_slot(ctx.room.name)
        await send_on_air_callback(session, station, persona, cfg)

    ctx.add_shutdown_callback(_on_shutdown)

    # `max_call_seconds` was a declared setting that nothing enforced. Wind the
    # call up in character rather than cutting the audio dead.
    max_seconds = int(cfg.get("max_call_seconds") or 0)
    if max_seconds > 0:
        async def _end_when_over_time() -> None:
            try:
                await asyncio.sleep(max_seconds)
            except asyncio.CancelledError:
                # Normal hangup cancelled the timer. Returning here matters:
                # a `finally` that called ctx.shutdown() would fire on EVERY
                # call, re-entering shutdown and logging the wrong reason.
                return

            log.info("call hit the %ss limit — signing off", max_seconds)
            try:
                await session.generate_reply(
                    instructions=(
                        "You're out of time. Thank the caller warmly, in one short "
                        "line, and say goodbye. Do not ask a question."
                    )
                )
                await asyncio.sleep(6)  # let the sign-off actually play
            except asyncio.CancelledError:
                return
            except Exception as e:
                # The session may already be closing; end the call regardless
                # rather than leaving an unhandled task exception behind.
                log.warning("sign-off before the time limit failed: %s", e)

            await _end_call(ctx, "call time limit reached")

        timeout_task = asyncio.create_task(_end_when_over_time())
        ctx.add_shutdown_callback(lambda: _cancel(timeout_task))

    # Both styles stay in persona and carry the show; the toggle is only
    # whether the DJ opens with an invitation or lets the caller lead.
    if str(cfg.get("greeting_style") or "inviting").lower() == "in-world":
        default_greeting = (
            "Pick up the call in character, mid-world — you were just on air. If "
            "something notable happened on the broadcast in the last little "
            "while, let it colour how you answer. One short line, the way a real "
            "DJ picks up mid-show. No question, no list of what you can do — "
            "just be there, and let them say why they called."
        )
    else:
        default_greeting = (
            "Pick up the call in character — you were just on air, and if "
            "something notable happened on the broadcast, let it colour the "
            "greeting. One short line, then invite them in with a single open "
            "question in your own voice: what's on their mind, or whether "
            "there's something they'd like to hear. One question, not a menu, "
            "and never a list of what you can do."
        )
    greeting = str(cfg.get("greeting") or "").strip() or default_greeting
    try:
        await session.generate_reply(instructions=greeting)
    except Exception as e:
        # A model outage at pickup used to mean the caller heard NOTHING
        # until they gave up. A canned line through the TTS keeps the call
        # alive — later turns may succeed once the provider recovers.
        log.warning("greeting failed (%s) — using a canned pickup", e)
        try:
            await session.say(
                "Hey — you're through to the booth. Bear with me a second, "
                "the line's a bit rough tonight. What can I do for you?"
            )
        except Exception:
            pass


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()


async def _end_call(ctx: JobContext, reason: str) -> None:
    """Actually hang up. ctx.shutdown() alone only ends the AGENT's job —
    the caller would stay connected to a DJ-less room, mic hot and timer
    running, looking 'on the line' forever. Deleting the room disconnects
    everyone; the widget hears it as a normal remote hangup."""
    log.info("ending call (%s)", reason)
    try:
        from livekit import api as lk_api

        await ctx.api.room.delete_room(lk_api.DeleteRoomRequest(room=ctx.room.name))
    except Exception as e:
        log.warning("room delete failed (%s) — agent will still leave", e)
    ctx.shutdown(reason=reason)


async def release_call_slot(room: str) -> None:
    """Tell the token server this call is over so its concurrency slot frees
    immediately. The widget sends the same beacon, but a crashed tab never
    does — and the worker's shutdown always runs, so with the default limit of
    two concurrent calls, this is what stops dead sessions blocking real
    callers for the 30-minute age-out."""
    import httpx

    base = os.environ.get(
        "CALLIN_INTERNAL_URL",
        f"http://localhost:{os.environ.get('TOKEN_SERVER_PORT', '8100')}",
    )
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            await c.post(f"{base}/call-ended", json={"room": room})
    except Exception as e:
        log.debug("slot release beacon failed (harmless, will age out): %s", e)


def effective_tools(cfg: dict) -> dict:
    """What the caller's agent actually gets: the MCP allowlist plus the local
    wrappers. Previews that report only build_allowed_tools() show a list that
    is neither what MCP serves nor what the model sees."""
    guard = bool(cfg.get("avoid_on_air_overlap"))
    waits = "waits for clear air" if guard else "no overlap guard"
    local = []
    if cfg.get("allow_library_search") and not library_search_needs_mcp():
        local.append("subwave_search_library (local: retry fallback)")
    if cfg.get("allow_requests"):
        local.append("subwave_request_song (local: pre-flight + status)")
    if cfg.get("allow_exact_queue") and not library_search_needs_mcp():
        local.append("subwave_queue_track (local: exact pick, counts against the call limit)")
    if cfg.get("allow_announcements"):
        local.append(f"subwave_dj_announce (local: {waits})")
    if cfg.get("allow_skills"):
        local.append(f"subwave_run_skill (local: {waits})")
    return {"mcp": build_allowed_tools(cfg, guarded=guard), "local": local}


def _transcript(session: AgentSession, limit: int = 24) -> list[tuple[str, str]]:
    """Flatten the call into (role, text) pairs, whatever shape the SDK's
    chat items happen to take."""
    turns: list[tuple[str, str]] = []
    try:
        items = list(session.history.items)
    except Exception:
        return turns

    for item in items[-limit:]:
        role = getattr(item, "role", None)
        if role not in ("user", "assistant"):
            continue
        content = getattr(item, "content", None)
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(c for c in content if isinstance(c, str))
        else:
            text = getattr(item, "text_content", "") or ""
        if text.strip():
            turns.append((role, text.strip()))
    return turns


async def send_on_air_callback(
    session: AgentSession, station: StationClient, persona: dict, cfg: dict
) -> None:
    """After the call, give the on-air DJ a passing mention of it.

    The point is continuity — a listener hears the same DJ refer to the call
    that just happened. It's deliberately one line: a mention, not a recap, and
    never a transcript. Nothing the caller said is repeated verbatim unless the
    DJ chooses to.
    """
    if not cfg.get("callback_enabled"):
        return

    turns = _transcript(session)
    caller_turns = sum(1 for role, _ in turns if role == "user")
    if caller_turns < int(cfg.get("callback_min_turns", 2)):
        log.info("skipping on-air handoff — only %d caller turn(s)", caller_turns)
        return

    max_words = int(cfg.get("callback_max_words", 30))
    extra = str(cfg.get("callback_instructions") or "").strip()

    convo = "\n".join(
        f"{'Caller' if role == 'user' else 'You'}: {text}" for role, text in turns
    )

    ask = (
        f"You are {persona.get('name', 'the DJ')}. The call just ended. Write ONE "
        f"line to say on air about it, under {max_words} words, in your own voice.\n\n"
        "Mention it the way a DJ passes over something between tracks — light, "
        "in character, moving on. Do not greet the audience, do not read out a "
        "summary, do not quote the caller word for word, and do not use their "
        "personal details beyond a first name. If they asked you something about "
        "yourself worth sharing, you may answer it briefly on air. If nothing "
        "about the call is worth mentioning, reply with exactly: SKIP\n"
    )
    if extra:
        ask += f"\nAlso: {extra}\n"
    ask += f"\nThe call:\n{convo}\n"

    try:
        from livekit.agents import llm as lk_llm

        ctx = lk_llm.ChatContext.empty()
        ctx.add_message(role="user", content=ask)

        # This runs during shutdown, when the session's own LLM may already be
        # tearing down. Fall back to a fresh client rather than losing the
        # handoff — it only gets one attempt per call.
        try:
            model = session.llm
            assert model is not None
        except Exception:
            model = build_llm(cfg)

        async def _compose() -> str:
            out = ""
            stream = model.chat(chat_ctx=ctx)
            async for chunk in stream:
                delta = getattr(chunk, "delta", None)
                if delta and getattr(delta, "content", None):
                    out += delta.content
            await stream.aclose()
            return out

        # Capped so a stalled provider can't eat the whole shutdown budget.
        text = await asyncio.wait_for(_compose(), timeout=25.0)
    except asyncio.TimeoutError:
        log.warning("on-air handoff compose timed out — skipping")
        return
    except Exception as e:
        log.warning("could not compose the on-air handoff: %s", e)
        return

    line = text.strip().strip('"')
    if not line or line.upper().startswith("SKIP"):
        log.info("on-air handoff skipped — nothing worth mentioning")
        return

    log.info("handing back to air: %s", line)
    # Fresh client: the session's StationClient may already be closed by an
    # earlier shutdown callback by the time this runs.
    fresh = StationClient()
    try:
        await fresh.dj_say(line, mode="styled", kind="callin")
    finally:
        await fresh.aclose()


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            # dev defaults to 0 idle processes, which means prewarm never runs
            # until a call arrives — so the first caller would sit through the
            # VAD and local-STT model load mid-conversation.
            num_idle_processes=1,
            # Loading the local STT model takes ~7s warm, and longer the first
            # time while it downloads. The 10s default kills the process
            # mid-load and the worker never becomes ready.
            initialize_process_timeout=180.0,
            # The back-to-air handoff (LLM compose + POST) runs during
            # shutdown; the 10s default could kill it mid-compose on a cold
            # model and the handoff only gets one chance per call.
            shutdown_process_timeout=60.0,
        )
    )
