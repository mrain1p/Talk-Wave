"""The station's tool surface, described once.

Before this, the same seventeen tools were described in five places: four
allowlists in main.py (which tools exist, which a setting unlocks, which are
served locally, which are on-air) and a separate catalogue in settings.py for
the panel. A test reconciled them, which meant the test was load-bearing — it
was the only thing stopping the panel from telling an operator that callers
couldn't reach something they could.

Now there is one table. The allowlists and the panel catalogue are both
derived from it, so they cannot disagree, and adding a tool is one entry here
plus one function in the module that serves it.

Deliberately a leaf module: no imports from settings, the station client, or
LiveKit. Everything reads it; it reads nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

# How a tool reaches the model.
MCP = "mcp"        # served by the station's MCP server, through the allowlist
LOCAL = "local"    # served by a wrapper of ours (retries, guards, the ledger)
NONE = "none"      # never served at all

# What unlocks a tool. Anything else is a settings field name.
READ = "read"      # always available; reads cost nothing and reveal nothing
NEVER = "never"    # not exposed on a call line at any setting


@dataclass(frozen=True)
class Tool:
    """One station tool, as the caller's agent may (or may not) see it.

    `what` and `note` are operator-facing: they are what the panel's Station
    tools section prints, so they explain the choice rather than the mechanism.
    """

    name: str
    gate: str            # READ, NEVER, or the settings field that unlocks it
    served: str          # MCP, LOCAL or NONE
    what: str
    note: str = ""
    # True for a local wrapper that MCP can stand in for when the wrapper
    # cannot work. Only library search: our wrapper reads an admin-only REST
    # endpoint, so without station credentials it can only ever return
    # nothing, while the MCP tool needs no auth at all.
    mcp_fallback: bool = False
    # True when the tool cannot be BUILT AT ALL without station credentials,
    # as opposed to being built and failing honestly. Exact queueing needs the
    # track ids that only the credentialed search returns, so without
    # credentials there is nothing for it to queue — and the panel must not
    # claim it is available.
    needs_station_admin: bool = False


TOOLS: tuple[Tool, ...] = (
    # --- always on: reads -------------------------------------------------
    Tool("subwave_health", READ, MCP,
         "Is the station up and on air."),
    Tool("subwave_now_playing", READ, MCP,
         "The current track, station context and listener count."),
    Tool("subwave_station_state", READ, MCP,
         "The upcoming queue, recent history and the booth log."),
    Tool("subwave_schedule", READ, MCP,
         "Personas, shows and the weekly schedule.",
         "How much of this reaches the prompt is set under Station awareness."),
    Tool("subwave_session", READ, MCP,
         "The DJ's live session and its recent on-air transcript."),
    # The one locally-served read: the station publishes lyrics over public
    # REST (its current-track lyrics feature), not over MCP, so our wrapper
    # is the only way to serve it at all — not a guarded duplicate of an MCP
    # tool, which is what LOCAL otherwise means here.
    Tool("subwave_current_lyrics", READ, LOCAL,
         "The words the current track is singing, when the station holds them.",
         "Served by this sidecar over the station's public lyrics read. A "
         "station without that feature answers 404, which the DJ hears as "
         "'no lyrics on file' rather than as an error."),

    # --- music ------------------------------------------------------------
    Tool("subwave_request_song", "allow_requests", LOCAL,
         "Queues a track from a natural-language request — a title, a mood, an "
         "era, or 'something like this'. The DJ writes a spoken intro for it.",
         "Station limits: 1 per 20s, 8 per hour, and none while nobody is "
         "listening. Served by a local wrapper that retries awkward phrasing "
         "before reporting a miss."),
    Tool("subwave_request_status", "allow_requests", MCP,
         "Checks what the booth did with a request."),
    Tool("subwave_search_library", "allow_library_search", LOCAL,
         "Exact term search over the library — no LLM, no rate limit.",
         "Needs no credentials over MCP. With station admin credentials it is "
         "served by a local wrapper that also retries with the 'by' connector "
         "stripped.",
         mcp_fallback=True),
    # REST-only at the station (/dj/recent) — there is no MCP tool for it, so
    # like the lyrics read our wrapper is the only way to serve it at all.
    Tool("subwave_recent_tracks", "allow_library_search", LOCAL,
         "What's newly arrived in the library, newest first.",
         "Station admin credentials required — the station's recently-added "
         "read. Shares the library-search switch deliberately: both answer "
         "'what have you got', and an operator happy to expose one has no "
         "reason to hide the other.",
         needs_station_admin=True),
    Tool("subwave_queue_track", "allow_exact_queue", LOCAL,
         "Queues the exact track from a search result, by id — no re-matching, "
         "no DJ intro.",
         "Station admin credentials required. Off by default: it skips the "
         "request endpoint's rate limit, so Actions per call is the only thing "
         "pacing a caller.",
         needs_station_admin=True),
    Tool("subwave_cancel_queued_track", "allow_cancel_queue", LOCAL,
         "Takes a queued track back out before it airs.",
         "Station admin credentials required. The station refuses once the "
         "track has left its player's queue — on air or being prepared — and "
         "that refusal reaches the caller as 'too late for that one'. Off by "
         "default: it can cancel a track someone ELSE requested, which is the "
         "whole reason the station gives listeners no cancel of their own. "
         "Counts against Actions per call.",
         needs_station_admin=True),

    # --- discovery: the ways to explore a library that are not a name search -
    # Added 0.10.104 after a run of bad calls in which the DJ had one crude
    # word-match search and a blind resolver, and nothing else: it told a
    # caller a track was missing when the library held it one letter away,
    # and answered "something dreamy" by guessing. The station has had these
    # three surfaces the whole time.
    Tool("subwave_search_by_sound", "allow_sound_search", LOCAL,
         "Finds tracks by how they SOUND, from a description — \"dreamy "
         "cinematic strings, slow and sad\".",
         "Station admin credentials required, and the station's heavy "
         "analyzer: it embeds the phrase and matches it against analysed "
         "audio, so it only covers tracks that have been through that pass. "
         "Where a name search would return songs with 'dreamy' in the title, "
         "this returns songs that sound dreamy. A station without the "
         "analyzer answers plainly that it can't, rather than 'nothing "
         "found'.",
         needs_station_admin=True),
    Tool("subwave_more_like_this", "allow_sound_search", LOCAL,
         "The tracks that sit closest to a given one — the station's own "
         "\"what mixes well after this\".",
         "Station admin credentials required. Shares the sound-search switch: "
         "both answer \"more of this feeling\" off the same analysis, and an "
         "operator happy with one has no reason to withhold the other. "
         "Defaults to the track on air, so \"more like this\" needs no id.",
         needs_station_admin=True),
    Tool("subwave_browse_library", "allow_library_search", LOCAL,
         "Browses the library by mood, energy, genre, era or "
         "vocal/instrumental — no track name needed.",
         "Station admin credentials required. Shares the library-search "
         "switch: it answers the same \"what have you got\" question from the "
         "other end. Moods come from the station's own fixed vocabulary, "
         "which the tool hands to the DJ rather than letting it invent a word "
         "that matches nothing.",
         needs_station_admin=True),
    Tool("subwave_like_track", "allow_favorite", LOCAL,
         "Adds a like to the track playing now — the same heart a listener taps.",
         "No station credentials needed, and the lowest-harm action here: a "
         "like changes no one's audio, and it is exactly what a listener does "
         "from the app. The station gates it on its own Likes toggle and rate-"
         "limits it per caller. Off by default like every action, and it counts "
         "against Actions per call."),
    Tool("subwave_unlike_track", "allow_unfavorite", LOCAL,
         "Removes the OPERATOR's heart from the track playing now.",
         "Station admin credentials required — this is the operator's own "
         "curation heart, not the public listener like, and the public like has "
         "no un-like. It only means anything to a caller who signed in as the "
         "operator, so leave it at the admin tier. Counts against Actions per call.",
         needs_station_admin=True),

    # --- the broadcast ----------------------------------------------------
    Tool("subwave_dj_announce", "allow_announcements", LOCAL,
         "Puts a spoken line on air, rewritten in the persona's voice.",
         "Station admin credentials required. Waits for quiet air when overlap "
         "protection is on. The sound-effect stinger this tool accepts is "
         "never used."),
    Tool("subwave_list_skills", "allow_skills", MCP,
         "What segments this station actually has, so the DJ doesn't guess at "
         "names.",
         "Station admin credentials required. Also read once at call start, so "
         "the DJ knows its own show without spending a turn asking."),
    Tool("subwave_run_skill", "allow_skills", LOCAL,
         "Runs a segment on air — weather, news, dedication, story time.",
         "Station admin credentials required. The station rate-limits each "
         "segment, and decides whether one is due."),

    # --- station-wide, and off by default ---------------------------------
    # These reach every listener, not just the caller. They are opt-in for
    # that reason, and served by local wrappers rather than over MCP so the
    # per-call action cap applies — an MCP-served tool bypasses it entirely.
    Tool("subwave_skip_track", "allow_skip_track", LOCAL,
         "Force-ends whatever is playing, for everyone listening.",
         "Station admin credentials required. OFF by default: a stranger on "
         "the phone cutting off the track the rest of the audience is enjoying "
         "is a real cost, and the station's own API calls skip an operator "
         "override with no listener-facing equivalent. Counts against Actions "
         "per call.",
         needs_station_admin=True),
    Tool("subwave_dj_segment", "allow_dj_segment", LOCAL,
         "Fires a scripted segment: station ID, the hour, a link, guest banter, "
         "a programme beat.",
         "Station admin credentials required. OFF by default: this is "
         "programme furniture rather than a request, and the station documents "
         "that firing one explicitly bypasses its own frequency and budget "
         "gates — so Actions per call is the only thing pacing it.",
         needs_station_admin=True),

    # Not an MCP tool at all: the station exposes takeover over admin REST
    # only, so these two are ours end to end. They sit here because this table
    # is what the panel prints, and a capability a caller has which the
    # operator's tool list does not mention is exactly the drift this file
    # exists to prevent.
    Tool("subwave_takeover_show", "allow_takeover", LOCAL,
         "Pins a show over the schedule for a while — a different DJ, on air, "
         "for everyone.",
         "Station admin credentials required. OFF by default, and the furthest-"
         "reaching thing on a call line: an hour by default, and unlike every "
         "other action here its effect outlives the call. Takes over at the "
         "next track boundary, not instantly.",
         needs_station_admin=True),
    Tool("subwave_cancel_takeover", "allow_takeover", LOCAL,
         "Cancels a takeover and hands the schedule back.",
         "Same switch as the pin, deliberately: a caller who can undo the "
         "operator's own takeover is making a station-wide change too.",
         needs_station_admin=True),

    # --- never on a call line ---------------------------------------------
    Tool("subwave_refresh_playlist", NEVER, NONE,
         "Rebuilds the fallback auto-playlist for the current mood.",
         "Reshapes what everyone hears next, on one caller's say-so. It also "
         "takes no parameters, so it cannot be steered by a caller's mood."),
    Tool("subwave_list_sfx", NEVER, NONE,
         "The sound-effects library.",
         "Only useful for firing one."),
    Tool("subwave_play_sfx", NEVER, NONE,
         "Fires a stinger on air immediately, over the programme.",
         "Nothing to add to a call, plenty to disrupt on air."),
)

BY_NAME = {t.name: t for t in TOOLS}


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


def _unlocked(tool: Tool, cfg: dict) -> bool:
    """Is this tool's gate satisfied by the current settings?"""
    if tool.gate == NEVER:
        return False
    if tool.gate == READ:
        return True
    return bool(cfg.get(tool.gate))


def mcp_allowlist(cfg: dict, *, local_search_available: bool | None = None) -> list[str]:
    """The names handed to the MCP server.

    A locally-served tool is left off deliberately: exposing an unguarded
    duplicate would let the model reach the version without our retries, the
    overlap guard, or the per-call action ledger. The one exception is a local
    wrapper that cannot work — see `mcp_fallback`.

    `local_search_available` is worked out from the station credentials unless
    you say otherwise; the default is the correct answer, so a caller cannot
    get it wrong by forgetting.
    """
    if local_search_available is None:
        local_search_available = not library_search_needs_mcp()
    names = []
    for tool in TOOLS:
        if not _unlocked(tool, cfg):
            continue
        if tool.served == MCP:
            names.append(tool.name)
        elif tool.served == LOCAL and tool.mcp_fallback and not local_search_available:
            names.append(tool.name)
    return names


def local_tool_names(cfg: dict, *, local_search_available: bool | None = None) -> list[str]:
    """The tools our own wrappers serve, given the current settings."""
    if local_search_available is None:
        local_search_available = not library_search_needs_mcp()
    names = []
    for tool in TOOLS:
        if not _unlocked(tool, cfg) or tool.served != LOCAL:
            continue
        if tool.mcp_fallback and not local_search_available:
            continue      # MCP is standing in for it
        if tool.needs_station_admin and not local_search_available:
            continue      # cannot be built at all — don't claim it exists
        names.append(tool.name)
    return names


def blocked_names() -> list[str]:
    """Tools never exposed on a call line, whatever the settings say."""
    return [t.name for t in TOOLS if t.gate == NEVER]


def effective_tools(cfg: dict) -> dict:
    """What the caller's agent actually gets, for the panel's tool test.

    Previews that report only the MCP allowlist show a list that is neither
    what MCP serves nor what the model sees.
    """
    local_ok = not library_search_needs_mcp()
    guard = bool(cfg.get("avoid_on_air_overlap"))
    waits = "waits for clear air" if guard else "no overlap guard"
    detail = {
        "subwave_search_library": "local: retry fallback",
        "subwave_request_song": "local: pre-flight + status",
        "subwave_queue_track": "local: exact pick, counts against the call limit",
        "subwave_cancel_queued_track": "local: refuses once it's on the way to air",
        "subwave_search_by_sound": "local: needs the station's audio analysis",
        "subwave_more_like_this": "local: neighbours of the track on air by default",
        "subwave_browse_library": "local: the station's own mood vocabulary",
        "subwave_dj_announce": f"local: {waits}",
        "subwave_run_skill": f"local: {waits}",
    }
    return {
        "mcp": mcp_allowlist(cfg, local_search_available=local_ok),
        "local": [
            f"{name} ({detail[name]})" if name in detail else name
            for name in local_tool_names(cfg, local_search_available=local_ok)
        ],
    }


def catalogue() -> list[dict]:
    """The panel's Station tools reference.

    Blocked tools are included on purpose: "what can a caller do" is only
    answerable if the things they can't do are visible too. Without them the
    permission list reads as though anything might be one toggle away.
    """
    return [
        {"name": t.name, "gate": t.gate, "what": t.what, "note": t.note}
        for t in TOOLS
    ]
