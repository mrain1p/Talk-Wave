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
    "allow_announcements": (None, True),
    "allow_library_search": (None, True),
    # Off by default: this puts audio on air on the caller's say-so. Skills
    # are the station's own segments (weather, news, dedications, story
    # time…). Safe-ish, but a stranger triggers them. (Sound effects were
    # considered and deliberately not offered — stingers on a caller's
    # say-so add nothing to a call.)
    "allow_skills":       (None, False),
    # With skills on, the DJ may also OFFER one when the moment fits ("want
    # me to spin you a story?") instead of waiting to be asked.
    "offer_skills":       (None, False),

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
    "max_call_seconds": (None, 600),

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
    "tune_in_volume":   (None, 0),

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
    ("ref",     "Reference",            "What to expect, and how to put it on another page."),
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
    ("context",  "callcfg", "Station awareness",  "What the DJ knows before picking up."),
    ("style",    "callcfg", "House style",        "Light steers on top of the persona."),
    ("callback", "callcfg", "Back to air",        "What the station says after the call."),
    ("sounds",   "callcfg", "Call sounds",        "Ring, pickup and hang-up."),

    ("ask",      "ref",     "What callers can ask", "Driven by the permissions above."),
    ("embed",    "ref",     "Embed on another page", "Drop the widget into any page."),
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
        help="Any OpenAI-compatible speech endpoint."),
    "tts_voice": dict(group="voice", kind="select", label="Voice",
        help="Leave on default to use the station's own voice for whoever is live."),
    "tts_model": dict(group="voice", kind="text", label="TTS model",
        needs=("tts_mode", "cloud"),
        help="Provider-specific. Blank uses the adapter's default."),
    "tts_adapter": dict(group="voice", kind="select", label="Adapter",
        help="Describes a backend's request shape. Only needed for a non-standard API."),

    # --- permissions ---
    "allow_requests": dict(group="perms", kind="check", label="Take song requests",
        help="The station refuses these when nobody is tuned in."),
    "confirm_requests": dict(group="perms", kind="check", label="Confirm requests before sending",
        needs=("allow_requests", True),
        help="The DJ says the track back and gets a quick yes before submitting. "
             "Worth keeping on: the station has no way to cancel a request once "
             "it's in — a changed mind before the confirm costs nothing."),
    "allow_library_search": dict(group="perms", kind="check", label="Search the music library",
        help="Lets the DJ check a track exists before promising it. Needs admin credentials."),
    "allow_announcements": dict(group="perms", kind="check", label="Put messages on air",
        help="Hands a line to the on-air DJ to read in persona. Needs admin credentials."),
    # These two read as the same switch until you see them side by side. They
    # are not: one is about what a caller may ASK FOR, the other about whether
    # the DJ may BRING IT UP first.
    "allow_skills": dict(group="perms", kind="check", label="Run segments when asked",
        help="Weather, news, dedications, story time. The caller asks — \"what's the "
             "weather doing?\" — and the DJ runs the station's real segment on air. "
             "With this off the DJ has no way to run one at all. The station "
             "rate-limits each segment (25–60 min), so callers can't spam them."),
    "offer_skills": dict(group="perms", kind="check", label="…and let the DJ offer one",
        needs=("allow_skills", True),
        help="The other half: whether the DJ may raise a segment ITSELF when the "
             "moment fits — \"want me to spin you a story?\" — instead of only "
             "answering a request. With this off the DJ runs segments but never "
             "brings them up. Occasional by design, never a menu, and never a list "
             "of what's on offer."),

    # --- call behaviour ---
    "max_call_seconds": dict(group="call", kind="number", label="Hang up after (s)",
        help="Hard limit on call length. The DJ signs off in character first rather "
             "than the audio just stopping. 600 = ten minutes."),
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
    "tune_in_volume": dict(group="call", kind="number", label="Station volume",
        needs=("tune_in_on_call", True),
        help="How loud the broadcast sits behind the call, as a percentage. 0 keeps "
             "it silent — the caller still counts as a listener, they just don't "
             "hear it. 10–20 gives that on-the-phone-to-the-station feel without "
             "competing with the DJ's voice."),
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
    "greeting_style": dict(group="call", kind="select", label="Greeting style",
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
        help="An instruction to the DJ, not a script it reads out."),

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
    "callback_enabled": dict(group="callback", kind="check", label="Mention the call on air",
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

# Choices for the select fields that aren't populated from a live source.
STATIC_CHOICES = {
    "profanity_mode": [("mask", "Mask them (s—)"), ("drop", "Remove them"), ("off", "Leave them alone")],
    "greeting_style": [("inviting", "Warm ask — what's on your mind?"), ("in-world", "Mid-world — no question")],
    "sound_pack": [("classic", "Exchange — telephone network tones"),
                   ("phone", "Handset — a real phone in a room")],
}


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
        "fields": {
            name: {
                "group": meta["group"],
                "kind": meta["kind"],
                "label": meta["label"],
                "help": meta.get("help", ""),
                "placeholder": meta.get("placeholder", ""),
                "needs": list(meta["needs"]) if meta.get("needs") else None,
                "choices": STATIC_CHOICES.get(name),
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


def tts_mode() -> str:
    """Single source of truth for cloud-vs-local. Previously several modules
    read os.environ["TTS_MODE"] directly, which meant voice resolution could
    run before anything had written it and silently use the wrong registry."""
    return str(load()["tts_mode"]).lower()


def station_base_url() -> str:
    return str(load()["station_base_url"]).rstrip("/")


def station_mcp_url() -> str:
    cfg = load()
    explicit = str(cfg.get("station_mcp_url") or "").strip()
    return explicit or f"{str(cfg['station_base_url']).rstrip('/')}/mcp"


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
        tmp.replace(SETTINGS_PATH)

    log.info("settings updated: %s", ", ".join(sorted(patch)) or "(none)")
    return load()
