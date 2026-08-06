"""
Runtime settings for the call-in agent.

The point of this module: the things you actually want to fiddle with between
calls — which model answers, which voice it uses, whether it's allowed to put
things on air — shouldn't require editing compose variables and restarting a
container. They live in a JSON file the worker re-reads at the start of every
call, and the full call page edits them over HTTP.

Precedence, highest first:
    1. data/settings.json   (what the settings UI writes)
    2. environment          (.env / compose)
    3. DEFAULTS below

Anything left blank in the UI falls through to the layer below, so clearing a
field is how you go back to the env/default rather than setting empty string.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("callin.settings")

SETTINGS_PATH = Path(
    os.environ.get("SETTINGS_PATH", Path(__file__).parent.parent / "data" / "settings.json")
)

# field -> (env var, built-in default). The env var may be a tuple of names,
# checked in order — used where a variable was renamed without breaking
# existing .env files.
FIELDS: dict[str, tuple[str | tuple[str, ...] | None, Any]] = {
    # Which station this sidecar answers for. Everything else — personas,
    # cards, voices, tools — is discovered from here, so pointing this at a
    # different SUB/WAVE instance re-homes the whole sidecar.
    "station_base_url": ("SUBWAVE_BASE_URL", "http://localhost:7700/api"),
    # Blank = derived as {station_base_url}/mcp.
    "station_mcp_url":  ("SUBWAVE_MCP_URL", ""),

    "llm_provider":     ("LLM_PROVIDER", "openai"),
    "llm_model":        ("LLM_MODEL", "gpt-4.1-mini"),
    "llm_base_url":     ("LLM_BASE_URL", ""),
    "llm_temperature":  (None, 0.8),

    "stt_provider":     ("STT_PROVIDER", "deepgram"),
    # DEEPGRAM_MODEL is the historical name from when Deepgram was the only
    # provider; STT_MODEL is what it means now that four providers share it.
    "stt_model":        (("STT_MODEL", "DEEPGRAM_MODEL"), "nova-3"),

    "tts_mode":         ("TTS_MODE", "cloud"),
    "tts_adapter":      ("TTS_ADAPTER_CONFIG", ""),
    "tts_base_url":     ("TTS_BASE_URL", "https://api.openai.com"),
    "tts_model":        (None, ""),
    # Blank = use the per-persona mapping in persona-voices.json.
    "tts_voice":        (None, ""),

    # Blank = whoever is live on air (the normal case). A persona id pins every
    # call to that DJ; RANDOM_PERSONA rolls one per call.
    "persona_override": (None, ""),

    # Tool permissions for the caller-facing agent. Reads are always on.
    "allow_requests":     (None, True),
    # Requests are irreversible — the station has no cancel endpoint — so the
    # cheap protection is confirming the track before it's submitted.
    "confirm_requests":   (None, True),
    # A mood ("something fun") is enough to act on, and the station's picker
    # resolves it — but sending it straight through means the caller never got
    # a say in WHICH fun. With this on, the DJ comes back with two or three
    # real options first. Costs one extra turn; makes the call a conversation
    # rather than a form.
    "shape_vague_requests": (None, False),
    # OFF by default from 0.9.89. Unlike a request, this lands on everyone
    # listening rather than on the caller who asked: a stranger hands the DJ a
    # line and the station reads it, in persona, to the whole audience. The
    # allowlist keeps the destructive tools off a call line and the conduct
    # prompt pushes back — but that is a model declining, not a gate refusing,
    # and a patient caller gets words onto the air. Defaulting it on meant
    # every deployment shipped that way without choosing it.
    "allow_announcements": (None, False),
    "allow_library_search": (None, True),
    # Let a caller who has picked a track out of the search results have THAT
    # recording queued, rather than the words being resolved a second time.
    # Off by default: it bypasses the station's own request rate limit, so it
    # leans entirely on `max_actions_per_call` to keep one caller in check.
    "allow_exact_queue":  (None, False),
    # Off by default: this puts audio on air on the caller's say-so. Skills
    # are the station's own segments (weather, news, dedications, story
    # time…). Safe-ish, but a stranger triggers them. (Sound effects were
    # considered and deliberately not offered — stingers on a caller's
    # say-so add nothing to a call.)
    "allow_skills":       (None, False),
    # With skills on, the DJ may also OFFER one when the moment fits ("want
    # me to spin you a story?") instead of waiting to be asked.
    "offer_skills":       (None, False),

    # Station-wide, and off by default. Unlike a request, these two land on
    # everyone listening rather than on the caller who asked. Both are served
    # by local wrappers so "Actions per call" caps them — over MCP they would
    # have no ceiling at all.
    "allow_skip_track":   (None, False),
    "allow_dj_segment":   (None, False),
    # Further-reaching than either, and the only caller action whose effect
    # outlives the call: it puts a different show — a different DJ — on air for
    # an hour by default. Off by default for the obvious reason.
    "allow_takeover":     (None, False),

    # Broadcast hygiene, applied to every line on its way to the speaker —
    # independent of provider, model, or whether the prompt was obeyed.
    "strip_stage_directions": (None, True),
    "profanity_mode":     (None, "mask"),   # mask | drop | off
    "profanity_words":    (None, ""),       # comma-separated; blank = built-in list

    # Free-text house style, layered on top of the persona rather than
    # replacing it. Blank means the persona alone decides.
    # `style_conversation` steers the call as a whole — pacing, initiative,
    # how the unexpected is handled — where the other two only shape the
    # answers and the exit.
    "style_conversation": (None, ""),
    "style_answering":    (None, ""),
    "style_signoff":      (None, ""),

    "greeting_style":   (None, "inviting"),   # inviting | in-world
    "greeting":         (None, ""),

    # Turn-taking. The SDK exposes these and nothing surfaced them, which left
    # the single biggest lever on whether a call FEELS like a phone call
    # unreachable. 0 on either delay means "leave the SDK's own default alone"
    # rather than "no delay" — the defaults are tuned and a zero would be a
    # worse answer than not answering.
    "min_endpointing_delay": (None, 0.0),
    "max_endpointing_delay": (None, 0.0),
    "allow_interruptions":   (None, True),
    "min_interruption_secs": (None, 0.0),

    # Whether both sides of a call are written to disk at all.
    #
    # On by default because it is how a bad call is diagnosed and the README
    # says so — but it is a transcript of a stranger's conversation, kept on
    # the operator's disk, and until now there was no way to say no. An
    # operator who does not want that must be able to have it, and be able to
    # say how long anything kept sticks around.
    "record_calls":     (None, True),
    "record_keep":      (None, 40),
    "max_call_seconds": (None, 600),
    # Floor on the DJ hanging up by itself. A model that decides a call is
    # finished after two words is worse than one that lingers, so nothing can
    # end a call before this. 0 removes the guard.
    "min_call_seconds": (None, 60),

    # Silence handling. A caller who stops talking shouldn't sit in dead air:
    # the DJ checks in, and if there's still nothing, winds the call up in
    # character rather than holding the line open.
    "idle_prompt_secs": (None, 20),   # 0 = never check in
    "idle_max_nudges":  (None, 2),    # check-ins before signing off

    # How much live station context to fold into the prompt. Every one of
    # these costs time-to-first-token on EVERY turn, not just at session
    # start, so they're tunable rather than fixed.
    # --- Usage controls ---------------------------------------------------
    # Every call costs real money (LLM + cloud TTS/STT) and airtime. Without a
    # limit, one open tab can mint tokens in a loop. These are deliberately
    # generous — the aim is to stop runaway use, not to ration callers.
    "calls_per_hour":       (None, 30),   # across everyone; 0 = unlimited
    "calls_per_day":        (None, 100),  # the hard wallet ceiling
    "caller_cooldown_secs": (None, 45),   # per caller, between calls
    "max_concurrent_calls": (None, 2),    # simultaneous live calls
    # Instant kill switch: the card still shows who's on air, but nobody
    # can start a call.
    "calls_paused":         (None, False),
    # How much one caller may set in motion in a single call — requests,
    # on-air messages and segments together. Stops one call filling the
    # queue; 0 = no cap.
    "max_actions_per_call": (None, 5),

    # Whether the DJ may ask the caller's name so the station can credit the
    # request on air. Off by default — being asked your name to request a song
    # is friction most callers don't want. A volunteered name is still used.
    "ask_caller_name":  (None, False),

    # The call agent wears the same persona as the on-air DJ. If it fires an
    # announcement or a segment while that DJ is mid-link, the station ends up
    # talking over itself. This holds those actions back until the air is
    # clear.
    "avoid_on_air_overlap": (None, True),
    "on_air_quiet_secs":    (None, 30),

    # The station refuses song requests when nobody is tuned in. A caller on
    # the line is engaged with the station but isn't pulling the stream, so
    # they don't count — which makes requests fail for the very people most
    # likely to make one. Tuning the caller's own browser in fixes that.
    # Volume 0 keeps the station out of the way of the DJ's voice.
    "tune_in_on_call":  (None, True),
    # 10%: audible enough that the caller can tell they're through to a live
    # station, quiet enough that it never competes with the DJ's voice — and,
    # on speakers, quiet enough not to bleed into the caller's own microphone
    # and be transcribed as if they had said it. Anything near 50 does both.
    "tune_in_volume":   (None, 10),
    # Blank derives the stream from station_base_url, which is right for a
    # LAN deployment and wrong for every other one: the widget is served over
    # TLS through a reverse proxy, and a browser refuses to load a plain-http
    # stream into an https page (mixed content) — silently, so the call simply
    # has no station behind it. A LAN address is also unreachable to a caller
    # who isn't on the LAN. Point this at the station's public https stream.
    "tune_in_url":      ("SUBWAVE_STREAM_URL", ""),

    # Who may reach the PHONE. The panel is always admin-only and is not
    # affected by this.
    #
    #   auto   open until a guest code is set, then required — what this has
    #          always done, kept as the default so upgrading changes nothing
    #   open   anyone who can load the page can call, code or no code
    #   guest  the guest code (or the admin password) is required
    #   admin  the admin password only — the phone is closed to callers
    #
    # `auto` exists rather than being tidied away because the alternative was
    # a default that silently stopped every existing deployment from taking
    # calls. The explicit modes are the ones that refuse when their password
    # is missing; auto is the one that reads the password to decide.
    "front_access":     (None, "auto"),

    # --- the widget itself, as a caller sees it --------------------------
    # A caller staring at one button has no idea a phone-in can do anything
    # beyond requests. This puts the same live reference the panel shows the
    # operator behind a button on the card — filtered to what is actually
    # switched on, so it can never promise something that would be refused.
    "show_caller_help": (None, True),
    # auto   follow the viewer's OS setting, with the in-widget toggle
    # light  / dark   force one, and hide the toggle
    # inherit  match the page the widget is embedded in
    "widget_theme":     (None, "auto"),

    # --- what the card shows, answered separately per surface -------------
    # The standalone page and an embed on somebody else's site are different
    # audiences looking at the same card. The page is the operator's own front
    # door and can afford to explain itself; an embed sits in a column beside
    # the host's own furniture, where a second copy of the show name is noise.
    # One answer for both meant every choice was a compromise, so each control
    # is asked twice. `show_*` is the page, `embed_*` is the frame.
    #
    # All default True, which is what both surfaces already did — turning one
    # off has to be somebody's decision, not an upgrade's.
    "embed_caller_help":  (None, True),
    "show_theme_toggle":  (None, True),
    "embed_theme_toggle": (None, True),
    # No `embed_settings_gear`. An embed never loads the panel's code, so a
    # gear there opens nothing — offering the operator a switch for it would
    # be offering a switch that does nothing whichever way it is set.
    "show_settings_gear": (None, True),
    "show_dj_avatar":     (None, True),
    "embed_dj_avatar":    (None, True),
    "show_dj_show":       (None, True),
    "embed_dj_show":      (None, True),
    "show_dj_tagline":    (None, True),
    "embed_dj_tagline":   (None, True),
    "show_now_playing":   (None, True),
    "embed_now_playing":  (None, True),

    # What the Call button says. Blank keeps "Call the DJ" — the honest label
    # when the card is showing whoever happens to be on air. `uses_name` swaps
    # it for the live DJ's own name, which reads better on a station whose
    # listeners know the roster, and re-resolves as the roster changes.
    "call_button_label":     (None, ""),
    "call_button_uses_name": (None, False),

    # After the line drops, ask the caller whether that went well. Two
    # buttons, stored against the call's own record, so a bad call can be
    # found and read rather than remembered. Off by default: it is a prompt
    # every caller sees, and an operator who is not going to read the answers
    # should not be collecting them.
    "ask_call_feedback": (None, False),

    # After the call, hand a short line back to the on-air DJ so the station
    # reflects that the call happened ("just had someone on about ..."). Kept
    # deliberately brief — a passing mention, not a recap.
    "callback_enabled":      (None, True),
    "callback_max_words":    (None, 30),
    "callback_instructions": (None, ""),
    "callback_min_turns":    (None, 2),

    "context_recent_tracks": (None, 3),   # tracks just played
    "context_booth_lines":   (None, 4),   # what's been said on air
    "context_upcoming":      (None, 2),   # what's queued next
    "context_schedule":      (None, False),  # what show is on later

    # Call sounds. Blank = the built-in synthesized sound for the chosen pack
    # (no asset needed); set a URL, or upload a file, to replace one.
    # `call_sounds` turns the lot off.
    "call_sounds":      (None, True),
    "sound_pack":       (None, "classic"),   # classic | phone
    "sound_ring":       (None, ""),
    "sound_pickup":     (None, ""),
    "sound_hold":       (None, ""),
    "sound_hangup":     (None, ""),
    "sound_failed":     (None, ""),
    "call_volume":      (None, 100),
}

# ---------------------------------------------------------------------------
# Field metadata — the single source of truth for how a setting is presented.
#
# Before this existed, every setting had to be declared in five places: here,
# one of four hand-maintained arrays in the page, the markup, a visibility
# rule, and a summary painter. Adding one meant editing all five and hoping.
# Now the page derives all of that from this table, so a new setting is one
# entry.
#
#   group     which section it belongs to
#   label     what the operator sees
#   help      one line on why it exists / what to pick — shown under the field
#   kind      text | number | check | select  (drives the input and the diff)
#   needs     (field, value) this depends on; hidden when unmet. `True` means
#             "any truthy value"
#   placeholder  what happens if the box is left EMPTY. Every free-text field
#             should say this — "blank" is a real setting with real behaviour,
#             and an empty box that doesn't explain itself reads as unfinished.
# ---------------------------------------------------------------------------

# Super-groups, in display order. The page renders these headers and orders
# every section beneath them from this table — it does not keep its own copy.
# "Configuration" used to hold five sections and a dozen buttons, which is
# where the clutter came from: connecting the station, entering keys and
# choosing models are three different jobs done at three different times.
SUPERGROUPS = [
    ("access",  "Access",               "Who can open this panel, and who can call the booth."),
    ("connect", "Connect",              "The station this answers for, and the keys to reach it."),
    ("models",  "Models & voice",       "What listens, what thinks, and how it sounds."),
    ("safety",  "Permissions & safety", "What a caller may trigger, and the limits around it."),
    ("callcfg", "Call settings",        "How a call runs and how the DJ talks."),
    ("ref",     "Reference",            "What a caller may ask for, and what the station publishes."),
]

# (id, supergroup, title, blurb). Order within a supergroup is the order here.
#
# Sections without settings fields — API keys, the caller reference, the embed
# snippet — are listed too, so ordering lives in exactly one place. Their
# markup carries a matching data-group attribute.
GROUPS = [
    ("security", "access",  "Passwords",          "Admin opens the controls; guest opens the phone."),

    ("station",  "connect", "Station",            "Which SUB/WAVE this answers for."),
    ("keys",     "connect", "API keys",           "Credentials, stored server-side."),

    ("brains",   "models",  "Brains",             "The models that listen and think."),
    ("voice",    "models",  "Voice",              "How the DJ sounds."),

    ("perms",    "safety",  "Caller permissions", "The station actions a caller can trigger."),
    ("usage",    "safety",  "Usage controls",     "Generous limits that stop runaway use."),
    ("speech",   "safety",  "Speech hygiene",     "What never reaches the speaker."),

    ("call",     "callcfg", "Call behaviour",     "How a call runs."),
    ("turns",    "callcfg", "Turn-taking",        "When the DJ decides you've finished."),
    ("context",  "callcfg", "Station awareness",  "What the DJ knows before picking up."),
    ("style",    "callcfg", "House style",        "Light steers on top of the persona."),
    ("callback", "callcfg", "Back to air",        "What the station says after the call."),
    ("sounds",   "callcfg", "Call sounds",        "Ring, pickup and hang-up."),
    # Was "Embed on another page", under Reference, and held nothing but the
    # snippet. Everything that decides what the card LOOKS like was scattered
    # through Call behaviour, where it sat between settings about how the DJ
    # talks. The snippet stayed here because the page you paste it into is the
    # second surface these answers are for.
    ("player",   "callcfg", "Player settings",    "What the card shows, on this page and in an embed."),

    ("ask",      "ref",     "What callers can ask", "Driven by the permissions above."),
    ("tools",    "ref",     "Station tools",       "Every tool the station publishes, and who can reach it."),
]

SCHEMA: dict[str, dict] = {
    # --- station ---
    "station_base_url": dict(group="station", kind="text", label="Station API",
        help="Everything else is discovered from here — personas, cards, voices, tools. "
             "Point it at a different SUB/WAVE to re-home the whole sidecar."),
    "station_mcp_url": dict(group="station", kind="text", label="MCP endpoint",
        help="Where the agent's tools come from. Derived as {Station API}/mcp unless set."),

    # --- brains ---
    "llm_provider": dict(group="brains", kind="select", label="LLM provider",
        help="Who thinks. Ollama and OpenRouter need a URL or key; local Ollama keeps "
             "everything on your network."),
    "llm_model": dict(group="brains", kind="select", label="Model",
        help="Read live from the provider. Faster models make the call feel more "
             "natural — anything over ~1.5s to first token sounds laggy."),
    "llm_base_url": dict(group="brains", kind="text", label="LLM URL",
        needs=("llm_provider", ("ollama", "openai", "openrouter")),
        help="Only for self-hosted or gateway endpoints."),
    "llm_temperature": dict(group="brains", kind="number", label="Temperature",
        help="Higher is more freewheeling. 0.8 suits a DJ; below 0.5 sounds clipped."),
    "stt_provider": dict(group="brains", kind="select", label="Speech-to-text",
        help="Nothing to set up: 'local' is included in this container and runs "
             "in-process — no key, no extra service, no network — so calls work out "
             "of the box. Switch to a cloud provider only if you want live "
             "word-by-word captions or better accuracy on names; those need a key."),
    "stt_model": dict(group="brains", kind="select", label="STT model",
        help="Leave it alone unless you have a reason. For local: base.en is the "
             "sensible default, tiny.en is faster, small.en is more accurate on names."),

    # --- voice ---
    "tts_mode": dict(group="voice", kind="select", label="TTS backend",
        help="'local' uses your VibeVoice persona voices but may be slower than "
             "realtime; 'cloud' is fast but won't match the on-air timbre."),
    "tts_base_url": dict(group="voice", kind="text", label="Server URL",
        help="Any OpenAI-compatible speech endpoint. This and the adapter below "
             "are separate settings and nothing makes them agree — pointing "
             "this at a new engine while leaving the old engine's adapter in "
             "place is the classic way to get audio at the wrong sample rate, "
             "which sounds broken and logs nothing. Press Test voice after "
             "changing either: it measures the rate rather than trusting it."),
    "tts_voice": dict(group="voice", kind="select", label="Voice",
        help="Leave on default to use the station's own voice for whoever is live."),
    "tts_model": dict(group="voice", kind="text", label="TTS model",
        needs=("tts_mode", "cloud"),
        help="Provider-specific. Blank uses the adapter's default."),
    "tts_adapter": dict(group="voice", kind="select", label="Adapter",
        help="Describes a backend end to end: the request shape, where it lists "
             "its voices, and what sample rate its audio really is. Only needed "
             "for a non-standard API — but it must match the server URL above, "
             "because a mismatched one fails quietly rather than loudly."),

    # --- permissions ---
    "allow_requests": dict(group="perms", kind="check", label="Take song requests",
        help="A title, an artist, a mood, an era, or 'more like this' — the station "
             "resolves it and writes a spoken intro. Its own limits apply: one "
             "request per 20 seconds, 8 per hour, and none at all while nobody is "
             "listening (see 'Tune the caller in' under Call behaviour)."),
    "confirm_requests": dict(group="perms", kind="check", label="Confirm requests before sending",
        needs=("allow_requests", True),
        help="The DJ says the track back and gets a quick yes before submitting. "
             "Worth keeping on: the station has no way to cancel a request once "
             "it's in — a changed mind before the confirm costs nothing."),
    "shape_vague_requests": dict(group="perms", kind="check",
        label="Offer options for a mood request",
        needs=("allow_requests", True),
        help="When a caller asks for a feeling rather than a track — \"something "
             "fun\", \"a bit of energy\" — the DJ comes back with two or three real "
             "directions before putting anything in: named artists or tracks it "
             "actually found, not an open \"what kind of fun?\". Off by default, "
             "because the fastest answer is to just play something; on, because "
             "letting the caller pick is what makes it a conversation. One round "
             "only either way — the DJ never asks twice."),
    "allow_library_search": dict(group="perms", kind="check", label="Search the music library",
        admin=True,
        help="Lets the DJ check a track really exists before promising it, and correct "
             "a caller who has the artist wrong. Works without station credentials; "
             "with them it also retries awkward phrasing like 'X by Y' before "
             "reporting a miss."),
    "allow_exact_queue": dict(group="perms", kind="check", label="Queue the exact track they picked",
        admin=True,
        needs=("allow_library_search", True),
        help="When a caller chooses a track from what the DJ found, queue THAT "
             "recording instead of sending the words back through the matcher — no "
             "more \"that's not the version I meant\". Off by default for one honest "
             "reason: it skips the station's request rate limit, so the only thing "
             "holding a caller back is Actions per call under Usage controls. Needs "
             "station admin credentials and library search."),
    "allow_announcements": dict(group="perms", kind="check", label="Put messages on air",
        admin=True,
        help="Hands a line to the on-air DJ to read in persona. Needs admin credentials."),
    # These two read as the same switch until you see them side by side. They
    # are not: one is about what a caller may ASK FOR, the other about whether
    # the DJ may BRING IT UP first.
    "allow_skills": dict(group="perms", kind="check", label="Run segments when asked",
        admin=True,
        help="Weather, news, dedications, story time. The caller asks — \"what's the "
             "weather doing?\" — and the DJ runs the station's real segment on air. "
             "With this off the DJ has no way to run one at all. The station "
             "rate-limits each segment (25–60 min), so callers can't spam them."),
    "offer_skills": dict(group="perms", kind="check", label="…and let the DJ offer one",
        admin=True,
        needs=("allow_skills", True),
        help="The other half: whether the DJ may raise a segment ITSELF when the "
             "moment fits — \"want me to spin you a story?\" — instead of only "
             "answering a request. With this off the DJ runs segments but never "
             "brings them up. Occasional by design, never a menu, and never a list "
             "of what's on offer."),
    "allow_skip_track": dict(group="perms", kind="check", admin=True,
        label="Let a caller skip the current track",
        help="Ends whatever is playing, for EVERYONE listening — not just the "
             "caller who asked. Off by default, and worth leaving off on a station "
             "with an audience: the station's own API treats skip as an operator "
             "override and offers no listener-facing equivalent. Needs station "
             "admin credentials, and each skip counts against Actions per call, "
             "which is the only thing pacing it."),
    "allow_dj_segment": dict(group="perms", kind="check", admin=True,
        label="Let a caller fire a programme beat",
        help="Station ID, the hour, a link, guest banter, a programme intro or "
             "outro. Different from segments above: this is the programme's own "
             "furniture rather than content someone asked for, and the station "
             "documents that firing one explicitly bypasses its own frequency and "
             "budget limits — so Actions per call is the only ceiling. Needs "
             "station admin credentials. Off by default."),
    "allow_takeover": dict(group="perms", kind="check", admin=True,
        label="Let a caller put a different show on air",
        help="The station's own takeover: pins a show ahead of the weekly "
             "schedule — a different DJ, for everyone listening — for an hour "
             "by default, longer if the caller asks. Read that again before "
             "turning it on: every other permission here is over inside a "
             "minute, and this one keeps running after the caller has hung up. "
             "The DJ can also cancel a takeover, including one YOU set from the "
             "station's own admin page. The switch lands at the end of the "
             "record playing at the time, not instantly. Needs station admin "
             "credentials, counts against Actions per call, and is off by "
             "default."),

    # --- call behaviour ---
    "max_call_seconds": dict(group="call", kind="number", label="Hang up after (s)",
        help="Hard limit on call length. The DJ signs off in character first rather "
             "than the audio just stopping. 600 = ten minutes."),
    "front_access": dict(group="security", kind="select",
        label="Who can call the booth",
        help="The panel is admin-only always — this is about the PHONE. "
             "Open lets anyone who loads the page call, which is right on a "
             "trusted network and an invitation to spend your API budget on a "
             "public one. Guest code requires the code you hand out (the admin "
             "password is accepted too, so you carry one). Admin only closes "
             "the phone to callers entirely — useful while you are still "
             "setting up. Choosing Guest or Admin without having set that "
             "password means nobody can call, and the panel says so."),
    # --- player settings: what the card shows, per surface ----------------
    # Every row here is asked twice, once for this page and once for an embed.
    # The panel lays them out as a two-column matrix, which is why the labels
    # are short: the column heading carries the surface, not the label.
    "show_caller_help": dict(group="player", kind="check",
        label="“What can I ask?” button",
        help="A small button on the card that opens the same live reference "
             "this panel shows you — filtered to the permissions actually "
             "enabled, so it can never suggest something the DJ would refuse. "
             "Most callers assume a phone-in only takes requests."),
    "embed_caller_help": dict(group="player", kind="check",
        label="“What can I ask?” button (embed)",
        help="The same button, in a frame on somebody else's page."),
    "show_theme_toggle": dict(group="player", kind="check",
        label="Light / dark toggle",
        help="Lets a viewer flip the card between light and dark and remembers "
             "the choice. Forcing a theme below hides this either way — there "
             "is nothing to toggle between."),
    "embed_theme_toggle": dict(group="player", kind="check",
        label="Light / dark toggle (embed)",
        help="Usually worth turning OFF: an embed inherits the host page's "
             "colours, and a caller flipping the card to light on a dark site "
             "gets a bright rectangle in the middle of it."),
    "show_settings_gear": dict(group="player", kind="check",
        label="Settings gear",
        help="The way into this panel from the call card. Turning it off does "
             "not secure anything — /panel is still reachable by URL and still "
             "asks for the admin password — it just stops advertising it to "
             "whoever is looking at the phone."),
    "show_dj_avatar": dict(group="player", kind="check", label="DJ photo",
        help="The persona's picture, served through this origin so it still "
             "loads from an https page off your network."),
    "embed_dj_avatar": dict(group="player", kind="check", label="DJ photo (embed)",
        help="Off if the host page already shows the same photo beside it."),
    "show_dj_show": dict(group="player", kind="check", label="Show name",
        help="The programme currently on air, above the tagline."),
    "embed_dj_show": dict(group="player", kind="check", label="Show name (embed)",
        help="Off if the host page already says what show is on."),
    "show_dj_tagline": dict(group="player", kind="check", label="DJ tagline",
        help="The persona's one-line blurb, as the station publishes it."),
    "embed_dj_tagline": dict(group="player", kind="check", label="DJ tagline (embed)",
        help="Off if the host page already says what show is on."),
    "show_now_playing": dict(group="player", kind="check", label="Now playing",
        help="The track on air right now. It updates on the card's own poll, "
             "which is every 20 seconds — slower than a host page's own ticker, "
             "so two of them side by side will briefly disagree."),
    "embed_now_playing": dict(group="player", kind="check", label="Now playing (embed)",
        help="Off if the host page already has a now-playing line."),
    "call_button_label": dict(group="player", kind="text", label="Call button",
        needs=("call_button_uses_name", False),
        placeholder="Call the DJ",
        help="What the button says before a call starts. Blank is “Call the "
             "DJ”, which is the honest label when the card shows whoever "
             "happens to be on air."),
    "call_button_uses_name": dict(group="player", kind="check",
        label="…or use the live DJ's name",
        help="Says “Call Francesca” instead, and follows the roster as the "
             "show changes. Reads better on a station whose listeners know "
             "the DJs by name; it replaces the label above rather than "
             "combining with it."),
    "ask_call_feedback": dict(group="player", kind="check",
        label="Ask how the call went",
        help="After the line drops, offers the caller a thumbs up or down. The "
             "answer is stored against that call's own transcript, so a bad "
             "call can be found and read back instead of remembered. Nothing "
             "else is collected and the caller can ignore it."),
    "widget_theme": dict(group="player", kind="select", label="Widget theme",
        help="How the call card is coloured, including the Call, mute and hang-up "
             "buttons. Auto follows the viewer's own light/dark setting and keeps "
             "the in-widget toggle. Light and dark force one and hide the toggle. "
             "Inherit matches the page the widget is embedded in — the right "
             "choice on a site with its own colours; on the standalone page it "
             "behaves as auto. An embed's own data-theme attribute still wins."),
    "min_call_seconds": dict(group="call", kind="number",
        label="Earliest the DJ may hang up (s)",
        help="The DJ ends calls itself once one has run its course, and this is the "
             "floor under that. 60 is the default because the opposite failure is "
             "worse: a model deciding a call is finished after two words, with the "
             "caller unable to tell being hung up on from the line dropping. Raise "
             "it if calls end too briskly; 0 removes the guard and lets the DJ close "
             "whenever it judges the conversation done. The hard limit above is the "
             "other end of the same range."),
    "idle_prompt_secs": dict(group="call", kind="number", label="Check in after (s)",
        help="Seconds without SPOKEN WORDS from the caller before the DJ asks if "
             "they're still there — background noise doesn't count, and the clock "
             "starts each time the DJ finishes talking. 0 never checks in."),
    "idle_max_nudges": dict(group="call", kind="number", label="Check-ins before hanging up",
        needs=("idle_prompt_secs", True),
        help="After this many unanswered check-ins the DJ says goodbye and gets back "
             "to the broadcast, rather than holding an empty line open."),
    "tune_in_on_call": dict(group="call", kind="check", label="Tune the caller in",
        help="Starts the live station stream in the caller's own browser once the "
             "DJ picks up — never while it's still ringing. Two reasons: the station "
             "refuses song requests when nobody is listening and a caller on the line "
             "doesn't otherwise count, and it sounds like a real phone-in, with the "
             "broadcast running quietly behind the conversation. Recommended."),
    "tune_in_url": dict(group="call", kind="text", label="Station stream URL",
        needs=("tune_in_on_call", True),
        help="Where the caller's browser pulls the broadcast from. Leave blank to "
             "derive it from the station address, which only works when the caller "
             "is on the same network AND the page is served over plain http. If the "
             "widget is behind TLS — anything through a reverse proxy — a browser "
             "silently refuses to load an http stream into an https page, and the "
             "call has no station behind it. Put the station's own https stream "
             "address here (https://listen.example.com/stream.mp3). The pipeline "
             "check tests it."),
    "tune_in_volume": dict(group="call", kind="number", label="Station volume",
        needs=("tune_in_on_call", True),
        help="How loud the broadcast sits behind the call, as a percentage. 10 is "
             "the default: enough to feel like a live station behind the DJ, not "
             "enough to fight with the voice. 0 keeps it silent — the caller still "
             "counts as a listener, they just don't hear it. Much above 20 and, on "
             "speakers, it bleeds into the caller's microphone and gets transcribed "
             "as though they had said it."),
    "avoid_on_air_overlap": dict(group="perms", kind="check", label="Pause the call while on air",
        help="The call DJ and the on-air DJ are the same voice. With this on, anything "
             "sent to air waits for the broadcast to go quiet, and the DJ steps back "
             "from the call while it plays — telling the caller either side rather "
             "than talking over itself."),
    "on_air_quiet_secs": dict(group="perms", kind="number", label="Air counts as busy for (s)",
        needs=("avoid_on_air_overlap", True),
        help="How long after the on-air DJ speaks before the air is treated as clear. "
             "A typical link runs 20-30 seconds."),

    "ask_caller_name": dict(group="call", kind="check", label="Ask the caller's name",
        help="Off by default — being asked your name to request a song is friction. "
             "A volunteered name is still used to credit them on air."),
    # Hidden once an opening line exists, because the opening line replaces it.
    # Two controls where only one can win, with nothing saying which, is the
    # shape 0.9.61 removed from `front_access` — the panel showed both and the
    # style silently stopped mattering the moment you typed anything below.
    "greeting_style": dict(group="call", kind="select", label="Greeting style",
        needs=("greeting", False),
        help="'Warm ask' picks up in persona and invites them in — what's on their "
             "mind, or something they'd like to hear. 'Mid-world' just answers the "
             "phone in character and lets the caller lead. Both carry the show; "
             "neither reads out a menu."),
    "persona_override": dict(group="call", kind="select", label="Who answers",
        help="Default is whoever is actually live on air — the honest answer, and "
             "what a listener expects. Pin one DJ to force every call to them "
             "(useful for testing a persona), or pick 'Random each call' to have a "
             "different DJ from the roster pick up each time."),
    "greeting": dict(group="call", kind="text", label="Opening line",
        placeholder="default: picks up in character and follows the greeting style above",
        help="An instruction to the DJ, not a script it reads out. Writing one "
             "REPLACES the greeting style above, which is why that field "
             "disappears once there is anything here."),

    "min_endpointing_delay": dict(group="turns", kind="number",
        label="Wait before replying (s)",
        help="How long the DJ waits after you stop making sound before it "
             "decides you have finished. Lower feels snappier and interrupts "
             "people who pause to think; higher feels patient and adds that "
             "much to every single reply. 0 leaves the SDK's tuned default "
             "alone, which is the right answer until a real call says otherwise."),
    "max_endpointing_delay": dict(group="turns", kind="number",
        label="Longest wait (s)",
        help="The ceiling on the above when someone is clearly mid-sentence — "
             "trailing off, or ending on a word that expects more. 0 leaves "
             "the default alone. Must not be below the minimum."),
    "min_interruption_secs": dict(group="turns", kind="number",
        needs=("allow_interruptions", True),
        label="Sound needed to interrupt (s)",
        help="How long a noise has to last before it stops the DJ mid-sentence. "
             "The SDK's default is half a second of SOUND — not words — so on a "
             "call where the station is playing into the room, half a second of "
             "the record the caller is listening to cuts the DJ off. Real calls "
             "came back with the DJ chopped into fragments because of it. Raise "
             "this on a speakerphone or with tune-in on; 0 leaves the SDK's "
             "default alone."),
    "allow_interruptions": dict(group="turns", kind="check",
        label="Let the caller talk over the DJ",
        help="On, a caller who starts talking stops the DJ mid-sentence, which "
             "is how a phone call works. Off, the DJ finishes what it was "
             "saying first — steadier on a speakerphone or a noisy line, where "
             "the station's own audio bleeding back in can read as the caller "
             "interrupting."),

    "record_calls": dict(group="call", kind="check", label="Keep call transcripts",
        help="Writes both sides of each call, every tool it used and the "
             "settings it ran under, to data/calls — which is how a bad call "
             "gets diagnosed. It is also a record of a stranger's conversation "
             "on your disk. Off writes nothing at all; Recent calls then only "
             "shows what is already there."),
    "record_keep": dict(group="call", kind="number", label="Transcripts to keep",
        needs=("record_calls", True),
        help="Older ones are deleted as new calls land. A transcript is a few "
             "kilobytes, so this is about how long a caller's words stay on "
             "your disk rather than about space."),

    # --- usage ---
    "max_concurrent_calls": dict(group="usage", kind="number", label="Simultaneous calls",
        help="Callers on the line at the same time. Each is a separate model session. 0 = no limit."),
    "calls_per_hour": dict(group="usage", kind="number", label="Calls per hour",
        help="Total calls per hour across everybody — the main guard against a runaway loop. 0 = no limit."),
    "calls_per_day": dict(group="usage", kind="number", label="Calls per day",
        help="The hard ceiling on what a day can cost. Set this if the page is "
             "reachable from the internet — the hourly limit alone still allows "
             "24x that in a day. 0 = no limit."),
    "calls_paused": dict(group="usage", kind="check", label="Pause all calls",
        help="Kill switch: the card still shows who's on air, but nobody can "
             "start a call. Takes effect immediately."),
    "max_actions_per_call": dict(group="usage", kind="number", label="Actions per call",
        help="How much one caller can set in motion in a single call — requests, "
             "on-air messages and segments together. At the limit the DJ says so "
             "warmly and keeps talking; it never sounds like an error. 0 = no cap."),
    "caller_cooldown_secs": dict(group="usage", kind="number", label="Redial wait (seconds)",
        help="How many seconds one caller waits before calling back. Set 0 while testing."),

    # --- speech hygiene ---
    "strip_stage_directions": dict(group="speech", kind="check", label="Strip stage directions",
        help="Models write *shuffles records* and (laughs); the voice reads them aloud. "
             "This removes them whatever the model does."),
    "profanity_mode": dict(group="speech", kind="select", label="Expletives",
        help="Applied to every spoken line, so it doesn't depend on the model behaving."),
    "profanity_words": dict(group="speech", kind="text", label="Word list",
        placeholder="default: the built-in broadcast list",
        help="Comma-separated. Blank uses the built-in list."),

    # --- house style ---
    "style_conversation": dict(group="style", kind="text", label="Conversation",
        placeholder="default: the persona sets the pace, the station keeps it moving",
        help="Steers the call as a whole — how much the DJ leads, how fast it moves, "
             "how it handles something it wasn't expecting. The other two only shape "
             "the answers and the exit. e.g. 'let the caller lead; don't fill silences'."),
    "style_answering": dict(group="style", kind="text", label="Answering",
        placeholder="default: answer as the persona would, at its own length",
        help="A light steer, not a character change. e.g. 'keep answers to two sentences'."),
    "style_signoff": dict(group="style", kind="text", label="Signing off",
        placeholder="default: wrap up in character, no fixed formula",
        help="e.g. 'mention what's coming up next before you hang up'."),

    # --- back to air ---
    "callback_enabled": dict(group="callback", kind="check", admin=True,
        label="Mention the call on air",
        help="One passing line between tracks after the caller hangs up. "
             "Needs admin credentials."),
    "callback_max_words": dict(group="callback", kind="number", label="Length (words)",
        needs=("callback_enabled", True),
        help="Short is better — it's a mention, not a recap."),
    "callback_min_turns": dict(group="callback", kind="number", label="Min caller turns",
        needs=("callback_enabled", True),
        help="Calls that never got going aren't worth mentioning."),
    "callback_instructions": dict(group="callback", kind="text", label="Extra steer",
        needs=("callback_enabled", True),
        placeholder="default: one passing mention, in character, no recap",
        help="Shapes the line. e.g. 'never name the caller' or 'tie it to the current track'."),

    # --- station awareness ---
    "context_recent_tracks": dict(group="context", kind="number", label="Recently played",
        help="Each item costs time-to-first-token on every turn, not just at the start."),
    "context_upcoming": dict(group="context", kind="number", label="Coming up",
        help="Lets the DJ answer 'what's next' without guessing."),
    "context_booth_lines": dict(group="context", kind="number", label="On-air chatter",
        help="Recent lines from the on-air DJ, so the call doesn't repeat them."),
    "context_schedule": dict(group="context", kind="check", label="Know the rest of the line-up",
        help="Adds the names of the station's OTHER shows, so the DJ can answer "
             "\"what's on after this?\" instead of guessing or refusing. It does not "
             "add times, other DJs' cards, or anything about the current show — "
             "that comes from the Show Card and is always on. Off by default because "
             "it costs prompt weight on every turn for a question most callers "
             "never ask."),

    # --- sounds ---
    "call_sounds": dict(group="sounds", kind="check", label="Play call sounds",
        help="Ringing while connecting, the line picking up, a hold click when the "
             "DJ steps onto the broadcast, hang-up, and an engaged tone when the "
             "booth can't take the call."),
    "sound_pack": dict(group="sounds", kind="select", label="Sound set",
        needs=("call_sounds", True),
        help="Both are generated in the browser — neither needs an audio file. "
             "'Exchange' is the classic telephone-network set; 'Handset' is a "
             "physical phone in a room: a real bell, the receiver lifting off the "
             "cradle, the clunk of it going back down."),
    "sound_ring": dict(group="sounds", kind="text", label="Ring",
        needs=("call_sounds", True),
        placeholder="default: the sound set above",
        help="Paste a URL, or upload a file, to replace this one sound."),
    "sound_pickup": dict(group="sounds", kind="text", label="Pick up",
        needs=("call_sounds", True), placeholder="default: the sound set above"),
    "sound_hold": dict(group="sounds", kind="text", label="On hold",
        needs=("call_sounds", True), placeholder="default: the sound set above",
        help="Played once when the DJ steps onto the broadcast mid-call."),
    "sound_hangup": dict(group="sounds", kind="text", label="Hang up",
        needs=("call_sounds", True), placeholder="default: the sound set above"),
    "sound_failed": dict(group="sounds", kind="text", label="Can't connect",
        needs=("call_sounds", True), placeholder="default: the sound set above",
        help="Engaged tone: the line is busy, the limit is reached, or the call "
             "couldn't connect."),
    "call_volume": dict(group="sounds", kind="number", label="Default volume",
        needs=("call_sounds", True), help="Starting playback volume for a call."),
}

# Sentinel value for persona_override: roll a different DJ from the roster on
# every call, rather than pinning one. Lives here so the worker and the panel
# agree on the spelling.
RANDOM_PERSONA = "__random__"

# The panel's Station tools reference comes from the tool registry — the same
# table the worker derives its allowlists from, so the two cannot disagree
# about what a caller can reach.
def mcp_tools_payload() -> list[dict]:
    from call.tools.registry import catalogue

    return catalogue()


# Choices for the select fields that aren't populated from a live source.
STATIC_CHOICES = {
    "profanity_mode": [("mask", "Mask them (s—)"), ("drop", "Remove them"), ("off", "Leave them alone")],
    "greeting_style": [("inviting", "Warm ask — what's on your mind?"), ("in-world", "Mid-world — no question")],
    "front_access": [
        ("auto", "Automatic — open until you set a guest code"),
        ("open", "Open — anyone who loads the page can call"),
        ("guest", "Guest code — callers need the code you share"),
        ("admin", "Admin only — the phone is closed to callers"),
    ],
    "widget_theme": [
        ("auto", "Auto — follow the viewer, keep the toggle"),
        ("light", "Light"),
        ("dark", "Dark"),
        ("inherit", "Inherit from the page it's embedded in"),
    ],
}


def _choices_for(name: str):
    """Static choices, except the sound packs — those are read from disk so a
    new pack is a folder in assets/sounds/ rather than an edit here."""
    if name == "sound_pack":
        import sounds

        return sounds.packs()
    return STATIC_CHOICES.get(name)


def schema_payload() -> dict:
    """Groups and fields for the settings UI, in display order."""
    return {
        "supergroups": [
            {"id": s, "title": t, "blurb": b} for s, t, b in SUPERGROUPS
        ],
        "groups": [
            {"id": g, "super": sup, "title": t, "blurb": b}
            for g, sup, t, b in GROUPS
        ],
        "mcpTools": mcp_tools_payload(),
        "fields": {
            name: {
                "group": meta["group"],
                "kind": meta["kind"],
                "label": meta["label"],
                "help": meta.get("help", ""),
                "placeholder": meta.get("placeholder", ""),
                "needs": list(meta["needs"]) if meta.get("needs") else None,
                # Whether this reaches the station through an endpoint that
                # needs its admin credentials. Without them the station
                # refuses and our client returns an empty list or a soft
                # failure, so from the panel a switched-on feature that never
                # happens is indistinguishable from one that is working.
                "admin": bool(meta.get("admin")),
                "choices": _choices_for(name),
            }
            for name, meta in SCHEMA.items()
        },
    }


# Curated model lists for the dropdowns. Free text is still accepted.
# "ollama" is filled in live from the server's own /api/tags — see
# token_server._ollama_models — because the useful list is whatever is
# actually pulled on that box, including the station's own DJ model.
MODEL_CHOICES = {
    "openai": ["gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini", "gpt-4o"],
    "openrouter": [],   # discovered live; the listing endpoint needs no key
    "google": ["gemini-2.5-flash", "gemini-2.5-pro"],
    "anthropic": ["claude-sonnet-5", "claude-haiku-4-5-20251001"],
    "ollama": [],
}

# Default endpoint per provider, offered by the settings UI as an autofill.
PROVIDER_BASE_URLS = {
    "ollama": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "",
    "google": "",
    "anthropic": "",
}

TTS_BASE_URLS = {
    "cloud": "https://api.openai.com",
    "local": os.environ.get("LOCAL_TTS_URL", "http://localhost:8001"),
}

STT_MODEL_CHOICES = {
    # In-process faster-whisper. No container, no key, no network — and CPU
    # only, which matters because the GPU is fully committed to VibeVoice.
    "local": ["base.en", "tiny.en", "small.en", "medium.en"],
    "deepgram": ["nova-3", "nova-2", "nova-2-phonecall"],
    # Uses the same OpenAI key as the LLM/TTS — the practical choice when
    # there's no Deepgram account, since Google STT needs a GCP service
    # account rather than a plain API key.
    "openai": ["gpt-4o-mini-transcribe", "gpt-4o-transcribe", "whisper-1"],
    "google": ["latest_long", "latest_short"],
}

# Stock voices for an OpenAI-compatible cloud endpoint.
OPENAI_VOICES = [
    "alloy", "ash", "ballad", "coral", "echo",
    "fable", "onyx", "nova", "sage", "shimmer", "verse",
]

_lock = threading.Lock()


def _coerce(value: Any, default: Any) -> Any:
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return value


def check_data_dir() -> None:
    """Say so at startup if the data directory cannot be used.

    Everything the operator sets lives in this one bind-mounted directory, and
    each store fails soft on its own: settings fall back to env/defaults,
    secrets come back empty, call records are not written. Three unrelated
    symptoms, none of which names the cause — and the cause is almost always
    the same single thing, ownership.

    It matters most on the upgrade to a non-root container (see the
    Dockerfile): files root wrote are not readable by the new runtime user, and
    the operator's first clue would otherwise be a panel that has forgotten
    everything. Both processes call this, because both read the same directory.

    Windows has no getuid and no meaningful mode bits here, so it is a no-op
    there — run-local.ps1 is not the deployment this protects.
    """
    try:
        _check_data_dir()
    except Exception as e:
        # This runs at module scope in the worker, before it registers with
        # LiveKit. A diagnostic that can stop the thing it is diagnosing is
        # worse than no diagnostic — same reasoning as record.write(), where a
        # crash would cost the on-air handoff for the sake of a JSON file.
        log.debug("could not check the data directory: %s", e)


def _check_data_dir() -> None:
    import admin_auth
    import secrets_store

    # Resolved before the platform gate on purpose, so the guard above is
    # reachable on every platform and the test for it means something
    # everywhere. When the gate came first, the only test for the guard was
    # POSIX-only, was skipped on the author's machine, and reached CI broken —
    # the third time in one afternoon that a skip hid a defect.
    data_dir = SETTINGS_PATH.parent
    if not hasattr(os, "getuid"):
        return                      # Windows: mode bits carry no meaning here
    if not data_dir.exists():
        return                      # first run; created on first write
    uid = os.getuid()
    # Both halves, always. Ownership alone is not enough: some filesystems
    # (Synology shares among them) create files with mode 000, which root
    # ignores and an owner cannot get past — so a chown that looks like it
    # worked still leaves everything unreadable. An operator handed half the
    # fix runs it, sees no change, and concludes the data is gone.
    fix = (f"on the HOST: chown -R {uid}:{uid} <dir> && chmod -R u+rwX <dir> "
           f"— <dir> is what is mounted here as {data_dir}")

    if not os.access(data_dir, os.W_OK | os.X_OK):
        log.error(
            "%s is not writable by uid %s — settings, keys and call records "
            "cannot be saved. %s", data_dir, uid, fix,
        )
    blocked = sorted(
        p.name for p in (SETTINGS_PATH, admin_auth.AUTH_PATH, secrets_store.SECRETS_PATH)
        if p.exists() and not os.access(p, os.R_OK)
    )
    if blocked:
        log.error(
            "unreadable in %s: %s — these exist but this process cannot open "
            "them, so what is in them is NOT in effect. Almost always owner or "
            "mode after the switch to a non-root container: %s",
            data_dir, ", ".join(blocked), fix,
        )


def _stored() -> dict:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        log.warning("settings file unreadable (%s) — falling back to env/defaults", e)
        return {}


def load() -> dict:
    """Fully resolved settings: stored over env over defaults."""
    stored = _stored()
    resolved: dict[str, Any] = {}

    for field, (env_var, default) in FIELDS.items():
        value = stored.get(field)
        if value is None or value == "":
            env_value = None
            for name in (env_var,) if isinstance(env_var, str) else (env_var or ()):
                candidate = os.environ.get(name)
                if candidate not in (None, ""):
                    env_value = candidate
                    break
            value = env_value if env_value not in (None, "") else default
        resolved[field] = _coerce(value, default)

    return resolved


def stored_only() -> dict:
    """Just the overrides, for round-tripping the UI without baking in
    whatever env happened to be set at read time."""
    stored = _stored()
    return {k: stored.get(k, "") for k in FIELDS}


# Fields that must be a URL or nothing. A real deployment had "Michael" in
# station_mcp_url — a browser autofilling a name into a text box — which meant
# the agent got NO station tools on any call and invented library results
# instead. The field accepted it silently and nothing downstream complained.
URL_FIELDS = ("station_base_url", "station_mcp_url", "llm_base_url", "tts_base_url")

_URLISH = re.compile(r"^(https?|wss?)://[^\s/?#]+", re.IGNORECASE)


def complain(patch: dict) -> str | None:
    """An operator-facing reason to refuse a settings write, or None.

    Checked on save so a typo is caught while someone is looking at the panel,
    rather than at 3am when a caller gets a DJ with no tools.
    """
    for field in URL_FIELDS:
        if field not in patch:
            continue
        value = str(patch[field] or "").strip()
        if not value:
            continue                     # blank clears the override — fine
        if not _URLISH.match(value):
            label = SCHEMA.get(field, {}).get("label", field)
            return (f"{label} must be a URL starting with http:// or https:// — "
                    f"got {value!r}. Leave it empty to use the default.")

    # Pairs. Every field validated itself and nothing validated two together,
    # so a floor above its own ceiling saved without complaint and the
    # behaviour after that was nobody's intention. Merged against what is
    # already stored, because a patch usually carries one half of a pair.
    return _complain_about_pairs({**load(), **{
        k: _coerce(v, FIELDS[k][1]) for k, v in patch.items()
        if k in FIELDS and v not in ("", None)
    }})


def _complain_about_pairs(cfg: dict) -> str | None:
    """Settings that are only wrong in company."""
    lo, hi = cfg.get("min_call_seconds", 0), cfg.get("max_call_seconds", 0)
    if lo and hi and lo > hi:
        return (f"The DJ cannot be told to wait {lo}s before hanging up and "
                f"also to hang up after {hi}s. Raise the hard limit, or lower "
                f"the earliest it may close.")

    hourly, daily = cfg.get("calls_per_hour", 0), cfg.get("calls_per_day", 0)
    if hourly and daily and daily < hourly:
        return (f"A daily cap of {daily} below the hourly cap of {hourly} makes "
                f"the hourly one meaningless — the day runs out first. Raise "
                f"the daily cap, or lower the hourly one.")

    lo_d, hi_d = cfg.get("min_endpointing_delay", 0), cfg.get("max_endpointing_delay", 0)
    if lo_d and hi_d and lo_d > hi_d:
        return (f"The shortest wait before replying ({lo_d}s) is longer than "
                f"the longest ({hi_d}s).")
    return None


def _sane_url(field: str, value: str) -> str:
    """Ignore a stored URL that isn't one, loudly.

    Belt and braces for configs that are ALREADY broken: validation on save
    can't help someone who saved rubbish before it existed, and falling back
    to the default beats handing an unusable URL to the agent.
    """
    value = str(value or "").strip()
    if value and not _URLISH.match(value):
        log.warning("ignoring %s=%r — that is not a URL; using the default", field, value)
        return ""
    return value


def tts_mode() -> str:
    """Single source of truth for cloud-vs-local. Previously several modules
    read os.environ["TTS_MODE"] directly, which meant voice resolution could
    run before anything had written it and silently use the wrong registry."""
    return str(load()["tts_mode"]).lower()


def station_base_url() -> str:
    resolved = _sane_url("station_base_url", load()["station_base_url"])
    return (resolved or FIELDS["station_base_url"][1]).rstrip("/")


def station_mcp_url() -> str:
    explicit = _sane_url("station_mcp_url", load().get("station_mcp_url"))
    return explicit or f"{station_base_url()}/mcp"


def save(patch: dict) -> dict:
    """Merge a partial update. Unknown keys are ignored; empty strings clear
    an override rather than storing a blank value."""
    with _lock:
        current = _stored()
        for key, value in patch.items():
            if key not in FIELDS:
                continue
            if value == "" or value is None:
                current.pop(key, None)
            else:
                current[key] = _coerce(value, FIELDS[key][1])

        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, sort_keys=True)
        # Set the mode explicitly rather than inheriting whatever the
        # filesystem hands out. On a Synology share the default for a newly
        # created file is 000 — no bits at all — which root ignores and a
        # normal user cannot read past. secrets.json and admin-auth.json were
        # only ever spared that because they chmod themselves; this file did
        # not, and it is why a non-root container could not read its own
        # settings. 0644: config, safe to copy or diff, unlike the other two.
        try:
            os.chmod(tmp, 0o644)
        except OSError:
            pass  # best effort; Windows ACLs don't map cleanly
        tmp.replace(SETTINGS_PATH)

    log.info("settings updated: %s", ", ".join(sorted(patch)) or "(none)")
    return load()
