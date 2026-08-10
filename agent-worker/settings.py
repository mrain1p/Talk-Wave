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

    # --- what a caller may do, and which caller ---------------------------
    # These eight are TIERS, not booleans: "off", or the least-trusted caller
    # who gets it. A caller who typed nothing is `open`, one who typed the
    # guest code is `guest`, one who typed the admin password is `admin`, and
    # each tier includes the ones below it.
    #
    # They were booleans, which meant one answer for every caller who got
    # through the door: an operator who wanted a public line AND wanted to put
    # something on air from their own phone had to leave that switch on for
    # strangers too, or keep flipping it. The defaults below are exactly what
    # the booleans resolved to, so nothing changes for an existing station —
    # see _migrate.
    #
    # Reads are always on and are not listed here at all.
    "allow_requests":     (None, "open"),
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
    "allow_announcements": (None, "off"),
    "allow_library_search": (None, "open"),
    # Let a caller who has picked a track out of the search results have THAT
    # recording queued, rather than the words being resolved a second time.
    # Off by default: it bypasses the station's own request rate limit, so it
    # leans entirely on `max_actions_per_call` to keep one caller in check.
    "allow_exact_queue":  (None, "off"),
    # Off by default: this puts audio on air on the caller's say-so. Skills
    # are the station's own segments (weather, news, dedications, story
    # time…). Safe-ish, but a stranger triggers them. (Sound effects were
    # considered and deliberately not offered — stingers on a caller's
    # say-so add nothing to a call.)
    "allow_skills":       (None, "off"),
    # With skills on, the DJ may also OFFER one when the moment fits ("want
    # me to spin you a story?") instead of waiting to be asked.
    "offer_skills":       (None, False),

    # Station-wide, and off by default. Unlike a request, these two land on
    # everyone listening rather than on the caller who asked. Both are served
    # by local wrappers so "Actions per call" caps them — over MCP they would
    # have no ceiling at all.
    "allow_skip_track":   (None, "off"),
    "allow_dj_segment":   (None, "off"),
    # Further-reaching than either, and the only caller action whose effect
    # outlives the call: it puts a different show — a different DJ — on air for
    # an hour by default. Off by default for the obvious reason.
    "allow_takeover":     (None, "off"),

    # Broadcast hygiene, applied to every line on its way to the speaker —
    # independent of provider, model, or whether the prompt was obeyed.
    "strip_stage_directions": (None, True),
    "profanity_mode":     (None, "mask"),   # mask | drop | off
    "tts_dash_style":     (None, "pause"),  # pause | hyphen | keep
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

    # --- voicemail --------------------------------------------------------
    # A second, much smaller kind of call: greeting, beep, one caller
    # utterance through STT, delivered. Nothing is recorded as audio — the
    # transcript is the message. docs/VOICEMAIL.md is the design.
    # The line's mode, made explicit: live calls on or off. Off with
    # voicemail on is a voicemail-only line; off with voicemail off is a
    # closed line that says so.
    "live_calls_enabled":    (None, True),
    # The master switch, then when the machine answers. voicemail_when used
    # to carry both jobs with its 'never' option, and the operator read the
    # section as having no on/off at all.
    "voicemail_enabled":     (None, False),
    "voicemail_when":        (None, "closed"),
    # Who may use the machine, as a tier like every other caller permission.
    # Defaults open: switching voicemail on is already a decision, and the
    # door code still applies in front of this.
    "allow_voicemail":       (None, "open"),
    # The text line. Enabled is the master (the dashboard's third door);
    # the ceilings exist because a typed endpoint that spends LLM money is
    # scriptable with curl in a way a WebRTC call never was — the station's
    # own text surface took a real raid (2026-07-28) and grew the same
    # shape of gate.
    "chat_enabled":          (None, False),
    "allow_chat":            (None, "open"),
    "chat_idle_minutes":     (None, 30),
    "chat_max_messages":     (None, 60),
    "chat_max_hours":        (None, 12),
    "max_open_chats":        (None, 20),
    "chats_per_hour":        (None, 0),
    "chats_per_day":         (None, 0),
    "chat_caller_cooldown_secs": (None, 30),
    "chat_msgs_per_minute":  (None, 10),
    "voicemail_greeting":    (None, ""),
    "voicemail_greeting_mode": (None, "staged"),
    "voicemail_max_seconds": (None, 30),
    "voicemail_destination": (None, "hold"),
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
    # Whether the ring stops (soft fade) the moment the DJ answers. On is
    # how a phone behaves; off lets a long ring or jingle finish under the
    # DJ's hello. One-shots are never cut either way.
    "ring_cut_at_pickup":   (None, True),
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
    # Split from tune_in_on_call: counting as a LISTENER (which is what
    # makes the station accept requests) and actually HEARING the broadcast
    # are different wants, and volume 0 was carrying the second job as a
    # trick nobody could discover.
    "tune_in_audible":  (None, True),
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
    # A guest code typed on a shared machine outlives the person who typed
    # it. 0 keeps it until Sign out; anything else forgets it after that
    # many minutes, and the card offers a lock button to forget it now.
    # Hours, not minutes: "how long should a handed-out code last" is a
    # question with day-shaped answers. 0 = until Sign out.
    "guest_session_hours": (None, 24),
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
    # Push to talk: the caller's microphone is closed except while they hold
    # (or latch) the talk bar. ON by default since 0.10.9 — an open mic feeds
    # the DJ every TV and room noise behind the caller, and the bar release is
    # what commits the turn promptly instead of waiting out endpointing. A
    # beta tester's fresh install proved the old default the hard way: mic hot
    # from pickup and a spacebar that "didn't work", both of which were just
    # this switch being off. Open-mic is still a real choice, per surface.
    "show_push_to_talk":  (None, True),
    "embed_push_to_talk": (None, True),
    # A second button beside Call — the machine on offer even while a live
    # call is possible. Off by default; the policy alone still turns the
    # Call button INTO "Leave a message" where a live call is impossible.
    "show_voicemail_button":  (None, False),
    "embed_voicemail_button": (None, False),
    "show_chat_button":       (None, True),
    "embed_chat_button":      (None, False),
    # A colour on the DJ's voice, applied in the caller's browser only — the
    # broadcast never hears it. One answer for both surfaces: the effect is
    # part of the DJ's character, and a DJ who is CB on the page and clean in
    # an embed is two characters.
    "voice_effect":       (None, "none"),
    # How hard the effect leans in, 0-100. 100 is the effect as designed;
    # lower opens the filters back toward the clean voice.
    # 60 by default: every effect at full character read as a costume party;
    # 60 keeps the colour audible while the words stay comfortably in front.
    "voice_effect_level": (None, 60),
    # Shape, not visibility — so it is one answer for both surfaces rather
    # than a third column in a matrix of on/off switches. Round is the
    # default because a portrait in a circle reads as a person and a portrait
    # in a square reads as a thumbnail, and this one is a person.
    "avatar_style":       (None, "round"),
    # Which way out the DJ's voice goes on a phone. A browser puts a call with
    # a live microphone into the platform's voice-call audio session, and that
    # session routes to the EARPIECE — so a caller who was listening on
    # speaker, in a car or a kitchen, has to lift the phone to their head the
    # moment the DJ picks up. This is a phone-in to a radio station, not a
    # private call: loudspeaker is the right default. See routeAudio() in
    # call.js for what each platform actually lets us do about it.
    "default_to_speaker": (None, True),
    "show_dj_show":       (None, True),
    "embed_dj_show":      (None, True),
    "show_dj_tagline":    (None, True),
    "embed_dj_tagline":   (None, True),
    "show_now_playing":   (None, True),
    "embed_now_playing":  (None, True),

    # What the Call button says. One picker rather than the checkbox-plus-box
    # it replaced (see _migrate): those were two controls where only one could
    # win, with nothing on screen saying which — ticking "use the DJ's name"
    # left whatever you had typed sitting in an enabled text field that no
    # longer did anything.
    #
    #   default  "Call the DJ" — honest when the card shows whoever is on air
    #   name     "Call Francesca", re-resolved as the roster changes
    #   custom   whatever is in call_button_label
    "call_button_mode":      (None, "default"),
    # The card's fixed strings, blank = the built-in. All of them take
    # {station}, {dj}, {show}, {track} and {tagline}, filled live.
    "word_ringing":          (None, ""),
    "word_answering":        (None, ""),
    "word_online":           (None, ""),
    "word_recording":        (None, ""),
    "word_hangup":           (None, ""),
    "word_vm_button":        (None, ""),
    "word_ptt":              (None, ""),
    "word_closed":           (None, ""),
    "word_message_only":     (None, ""),
    "call_button_label":     (None, ""),

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
    # The machine's beep is the one SERVER-side sound: the worker plays it
    # into the room at pickup, so only an uploaded file applies — there is
    # no browser on that end to fetch a URL.
    "sound_vm_beep":    (None, ""),
    "call_volume":      (None, 100),
}

# ---------------------------------------------------------------------------
# Caller tiers.
#
# Least trusted first, and each one includes everything below it. The tier is
# decided when the token is minted (api/auth.caller_tier) from what the caller
# actually typed, and travels to the worker inside the signed room name — so a
# caller cannot raise their own tier without a token they were not given.
#
#   open   got in without typing anything: the line has no code, or is on auto
#          with none set
#   guest  typed the guest code
#   admin  typed the admin password (which opens the phone as well as the panel)
# ---------------------------------------------------------------------------
TIERS = ("open", "guest", "admin")
TIER_OFF = "off"

# The permissions that carry a tier rather than a yes/no. Everything else in
# the perms group is a modifier — "confirm requests before sending" shapes how
# requests work, it is not a capability anyone is being granted — and asking
# who those apply to would be asking a question with no answer.
TIERED_PERMISSIONS = (
    "allow_voicemail",
    "allow_chat",
    "allow_requests",
    "allow_library_search",
    "allow_exact_queue",
    "allow_announcements",
    "allow_skills",
    "allow_skip_track",
    "allow_dj_segment",
    "allow_takeover",
)

# Offered to the panel so the three columns are named in one place.
TIER_CHOICES = [
    (TIER_OFF, "Off"),
    ("open", "Anyone who can call"),
    ("guest", "Callers with the guest code"),
    ("admin", "Admin only"),
]


def voicemail_policy(cfg: dict) -> str:
    """The machine's effective policy: 'never' unless the master switch is
    on, else the stored when. One resolver, because two call sites deciding
    "is voicemail on" independently is how they drift."""
    if not cfg.get("voicemail_enabled"):
        return "never"
    return str(cfg.get("voicemail_when") or "closed")


def tier_reaches(need: Any, have: str) -> bool:
    """Whether a caller at tier `have` clears a permission set to `need`.

    The one ladder. It was spelled out as a dict literal in tokens.py (the
    voicemail gate) and again in api/chat.py — the duplicate that drifts —
    and an unknown `need` fails CLOSED for the same reason normalise_tier
    does: a permission that grants itself on a typo cannot be walked back.
    """
    ladder = {"open": 0, "guest": 1, "admin": 2}
    need_s = str(need or "open")
    return need_s in ladder and ladder.get(have, 0) >= ladder[need_s]


def normalise_tier(value: Any) -> str:
    """Whatever is stored, as one of off/open/guest/admin.

    Tolerant on purpose: this reads a file an operator can edit by hand, and a
    value it cannot make sense of has to fail CLOSED. A permission that grants
    itself to strangers because the JSON said `"yes"` is the one mistake here
    that cannot be walked back — the caller has already been on air.
    """
    if isinstance(value, bool):
        return "open" if value else TIER_OFF
    text = str(value or "").strip().lower()
    if text in TIERS:
        return text
    if text in ("true", "1", "yes", "on", "all", "everyone"):
        return "open"
    return TIER_OFF


def permission_reaches(setting: Any, tier: str) -> bool:
    """Does a caller at `tier` get this permission?"""
    need = normalise_tier(setting)
    if need == TIER_OFF or tier not in TIERS:
        return False
    return TIERS.index(tier) >= TIERS.index(need)


def tier_from_room(room_name: str) -> str:
    """The caller's tier, read back out of the room the token was signed for.

    `callin-<o|g|a>-<12 hex>`. Anything else — a probe room, a room minted by
    a version of the token server that predates this, a name from somewhere
    else entirely — comes back as the LEAST trusted tier. Failing closed is
    the only safe direction: the alternative is an unrecognised name handing a
    stranger the operator's own permissions.
    """
    parts = str(room_name or "").split("-")
    if len(parts) >= 3 and parts[0] == "callin":
        for tier in TIERS:
            if parts[1] == tier[0]:
                return tier
    return "open"


def permissions_for(cfg: dict, tier: str) -> dict:
    """A copy of the settings with every tiered permission collapsed to a
    plain bool for one caller.

    Everything downstream — the tool registry, the prompt, the local wrappers —
    reads `cfg.get("allow_x")` as a truthy value and always has. Resolving here
    means none of it has to learn about tiers, and, more importantly, none of
    it can accidentally read the raw string: `"off"` is truthy, so a consumer
    that missed the change would have switched every permission ON.
    """
    out = dict(cfg)
    for field in TIERED_PERMISSIONS:
        out[field] = permission_reaches(cfg.get(field), tier)
    return out

# The panel's Station tools reference comes from the tool registry — the same
# table the worker derives its allowlists from, so the two cannot disagree
# about what a caller can reach.
def mcp_tools_payload() -> list[dict]:
    from call.tools.registry import catalogue

    return catalogue()

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
#
# Six headings, and each one answers a different question an operator arrives
# with. The shape before this had "Call settings" holding seven sections that
# ranged from what the DJ's voice does to how loud the ring tone is, and
# finding a sub-setting meant opening sections to see what was in them. The
# split that fixed it is speaking (The conversation) against everything that
# runs the call around the speaking (Running the line) — the two are looked
# for at different times and by different people.
SUPERGROUPS = [
    ("config",  "Configuration",        "The station, the keys, and what listens, thinks and speaks."),
    ("safety",  "Permissions & safety", "What a caller may set in motion, and the limits around it."),
    ("talk",    "The conversation",     "Who answers, how they speak, and what they know."),
    ("line",    "Running the line",     "How a call starts, how it ends, and what happens after."),
    ("card",    "The call card",        "What a caller sees — here, and on somebody else's page."),
    ("ref",     "Reference",            "What a caller may ask for, and what the station publishes."),
]

# (id, supergroup, title, blurb). Order within a supergroup is the order here.
#
# Sections without settings fields — Connections, the embed snippet, the two
# caller references — are listed too, so ordering lives in exactly one place.
# Their markup carries a matching data-group attribute.
GROUPS = [
    ("station",  "config", "SUB/WAVE Station", "Which station this answers for."),
    # There is no "Connections" section any more. It held every API key on one
    # screen, away from the provider dropdowns those keys decide the contents
    # of — so picking a model meant leaving the section, adding a key to a list
    # of eight, and coming back to see whether the provider had appeared. Each
    # key now lives in the section that spends it; see secrets_store.SECRET_GROUPS.
    ("brains",   "config", "Brains",        "AI — the model that thinks."),
    ("voice",    "config", "Voice",         "TTS — how the DJ sounds."),
    # Split out of Brains. Listening and thinking are configured at different
    # times, from different accounts, and one section holding both meant six
    # rows where four of them were about something else.
    ("ears",     "config", "Ears",          "STT — how the DJ hears."),

    # Access leads Permissions & safety — the operator's call: passwords and
    # door codes are questions about who may do things, and they read better
    # beside the permissions they guard than at the top of Configuration.
    # A fresh install still isn't stranded: the first-run password nudge is
    # its own banner at the top of the page, wherever this section sits.
    # Prose, not fields: what stands in front of the line, said once. The
    # operator asked for the layers to be explained where the doors are.
    ("secinfo",  "safety", "How the doors work", "The layers in front of the line."),
    ("security", "safety", "Access",        "Who opens this panel, and who can call."),
    ("perms",    "safety", "Caller permissions", "The station actions a caller can trigger."),
    ("usage",    "safety", "Usage controls",     "Generous limits that stop runaway use."),
    # Beside the usage caps rather than under Running the line — the hard
    # per-call ceiling is one more spend limit, and the operator went looking
    # for it here. The rest of what "Call length" used to hold (sign-off,
    # check-ins, the earliest hang-up) is conversation behaviour and lives in
    # Closing the call below.
    ("speech",   "safety", "Speech hygiene", "What never reaches the speaker."),

    # Knowledge first, voice second, then the call's shape in the order a
    # call has one: open, close, turn-take. The operator's ordering.
    ("context",  "talk",   "Station awareness",  "What the DJ knows before picking up."),
    ("style",    "talk",   "House style",        "Light steers on top of the persona."),
    ("call",     "talk",   "Greeting",           "Which DJ picks up, and how the call opens."),
    # Greeting's mirror: how a call ends, in character — the sign-off steer,
    # the idle check-ins, and how early the DJ may hang up were scattered
    # across House style and Call length, and the operator asked where the
    # closing settings were. A fair question deserves a section.
    ("turns",    "talk",   "Turn-taking",        "When the DJ decides you've finished."),
    ("closing",  "talk",   "Closing the call",   "How a call ends, in character."),


    # Was inside Caller permissions, where it read as a fourth station-wide
    # permission. It is not a permission at all: it decides what happens when
    # the call DJ and the on-air DJ are the same voice.
    # Its own switch, not a row inside Voicemail: whether the booth takes
    # live callers and whether the machine answers are two decisions, and
    # nesting one under the other implied a dependency neither has.
    ("onair",    "line",   "On-air ducking",      "The call DJ and the on-air DJ are one voice."),
    ("tunein",   "line",   "Tune the caller into the station",
     "Whether the caller counts as a listener, and whether they hear the broadcast."),
    ("callback", "line",   "Back-to-air commentary", "One line after the call — nothing more."),
    ("sounds",   "line",   "Call sounds",         "Ring, pickup and hang-up."),
    # Moved out of Voice: the effect shapes the CALL's sound, not the TTS
    # backend, and the operator kept looking for it here.
    ("effects",  "line",   "Voice effects",       "A radio colour on the DJ's voice."),
    ("record",   "line",   "Call transcripts",    "What is written to disk, and for how long."),
    # Its own section, not rows inside another — the operator's explicit call.
    ("voicemail", "line",  "Voicemail",           "When the booth can't pick up, the machine does."),
    ("chat",      "line",  "Text line",           "Typed chat with whoever is on air — same brain, no microphone."),

    ("player",   "card",   "Player settings",     "What the card shows, here and in an embed."),
    # Every fixed string on the card, overridable — so a station whose whole
    # page speaks in its own voice doesn't get "Ringing…" in ours. The
    # operator asked for it as a group.
    ("wording",  "card",   "Wording",             "What the card's buttons and states say."),
    ("embed",    "card",   "Embed on another page", "The snippet, and what it looks like."),

    ("ask",      "ref",    "What callers can ask", "Driven by the permissions above."),
    ("tools",    "ref",    "Station tools",        "Every tool the station publishes, and who can reach it."),
]

SCHEMA: dict[str, dict] = {
    # --- station ---
    "station_base_url": dict(group="station", kind="text", label="Station API",
        help="Personas, cards, voices and tools are all discovered from here. "
             "Point it at a different SUB/WAVE to re-home the whole sidecar."),
    "station_mcp_url": dict(group="station", kind="text", label="MCP endpoint",
        placeholder="derived: {Station API}/mcp",
        help="Where the agent's tools come from. Only set this if the station "
             "publishes MCP somewhere other than under its API."),

    # --- brains ---
    "llm_provider": dict(group="brains", kind="select", label="Provider",
        help="Only providers you have a key for are listed — add one below and "
             "it appears here. Ollama runs on your own network and needs none."),
    "llm_model": dict(group="brains", kind="select", label="Model",
        help="Read live from the provider. Over ~1.5s to first token sounds "
             "laggy on a call."),
    "llm_base_url": dict(group="brains", kind="text", label="Endpoint",
        needs=("llm_provider", ("ollama", "openai", "openrouter",
                                "deepseek", "requesty", "gateway",
                                "openai-compatible")),
        placeholder="default: the provider's own address",
        help="Only for a self-hosted or gateway endpoint. Required for "
             "'OpenAI-compatible' — it is the address of your own server. "
             "With one set, the Model list is read from it (hit “Reload "
             "model lists”) — servers like llama-swap only route model "
             "names they declare."),
    "llm_temperature": dict(group="brains", kind="number", label="Temperature",
        help="0.8 suits a DJ. Below 0.5 sounds clipped."),

    # --- ears ---
    "stt_provider": dict(group="ears", kind="select", label="Provider",
        help="'local' is in this container already — no key, no network — so "
             "calls work out of the box. A cloud provider buys word-by-word "
             "captions and better accuracy on names, and needs a key."),
    "stt_model": dict(group="ears", kind="select", label="Model",
        help="For local: base.en is the default, tiny.en is faster, small.en "
             "is better on names."),

    # --- voice ---
    "tts_mode": dict(group="voice", kind="select", label="Backend",
        help="'local' uses your VibeVoice persona voices but may be slower than "
             "realtime; 'cloud' is fast but won't match the on-air timbre."),
    "tts_base_url": dict(group="voice", kind="text", label="Endpoint",
        help="Any OpenAI-compatible speech endpoint. Press Test voice after "
             "changing this or the adapter: a mismatched pair produces audio at "
             "the wrong sample rate, which sounds broken and logs nothing."),
    "tts_voice": dict(group="voice", kind="select", label="Voice",
        help="Default uses the station's own voice for whoever is live."),
    "tts_model": dict(group="voice", kind="text", label="Model",
        needs=("tts_mode", "cloud"),
        placeholder="default: the adapter's own",
        help="Provider-specific."),
    "tts_adapter": dict(group="voice", kind="select", label="Adapter",
        help="Describes a backend end to end — request shape, voice list, real "
             "sample rate. Only needed for a non-standard API, and it must match "
             "the endpoint above."),

    # --- permissions ---
    "allow_requests": dict(group="perms", kind="select", tiered=True, label="Take song requests",
        help="A title, an artist, a mood, an era or 'more like this'. The station "
             "resolves it and writes the intro. Its own limits still apply: 1 per "
             "20s, 8 an hour, and none while nobody is listening."),
    "confirm_requests": dict(group="perms", kind="check", label="Confirm before sending",
        needs=("allow_requests", TIERS),
        help="The DJ says the track back and waits for a yes. The station cannot "
             "cancel a request once it is in; a changed mind before the confirm "
             "costs nothing."),
    "shape_vague_requests": dict(group="perms", kind="check",
        label="Offer options for a mood request",
        needs=("allow_requests", TIERS),
        help="For \"something fun\", the DJ comes back with two or three real "
             "tracks it found rather than playing the first match. Costs one turn, "
             "and it only ever asks once."),
    # Not admin=True, and that is the point: the panel used to tag this
    # "Station admin" with a tooltip saying it would quietly never happen
    # without credentials, which is false — the MCP tool needs no auth at all
    # (registry.mcp_fallback). Only the local wrapper's extra retry does.
    # admin="optional": the chip reads "Station admin — optional" and never
    # goes coral. A bare admin=True here would claim the tool dies without
    # credentials, which is false — see the note above.
    "allow_library_search": dict(group="perms", kind="select", tiered=True, label="Search the music library",
        admin="optional",
        help="Lets the DJ check a track exists before promising it. Works without "
             "station credentials; with them it also retries phrasing like "
             "'X by Y' before reporting a miss."),
    "allow_exact_queue": dict(group="perms", kind="select", tiered=True, label="Queue the exact track picked",
        admin=True,
        needs=("allow_library_search", TIERS),
        help="Queues the recording the caller chose out of the search results, "
             "rather than re-matching the words. Skips the station's request rate "
             "limit, so Actions per call is the only thing pacing it."),
    "allow_announcements": dict(group="perms", kind="select", tiered=True, label="Put messages on air",
        admin=True,
        help="Hands a line to the on-air DJ to read in persona."),
    # These two read as the same switch until you see them side by side. They
    # are not: one is about what a caller may ASK FOR, the other about whether
    # the DJ may BRING IT UP first.
    "allow_skills": dict(group="perms", kind="select", tiered=True, label="Run segments when asked",
        admin=True,
        help="The station's own skills — the same roster its Skills panel loads: "
             "weather, news, dedications, story time — run on air in the DJ's "
             "voice when the caller asks. Fired through the station's manual "
             "trigger, which bypasses its frequency gates and cooldowns on "
             "purpose, so Actions per call is the only pacing."),
    "offer_skills": dict(group="perms", kind="check", label="…and let the DJ offer one",
        admin=True,
        needs=("allow_skills", TIERS),
        help="Whether the DJ may raise a segment itself — \"want me to spin you a "
             "story?\" — instead of only answering a request. Never a menu."),
    "allow_skip_track": dict(group="perms", kind="select", tiered=True, admin=True,
        label="Skip the current track",
        help="Ends what is playing for EVERYONE listening, not just the caller who "
             "asked. The station treats skip as an operator override and offers no "
             "listener-facing equivalent. Counts against Actions per call."),
    "allow_dj_segment": dict(group="perms", kind="select", tiered=True, admin=True,
        label="Fire a programme beat",
        help="Station ID, the hour, a link, guest banter, an intro or outro — the "
             "programme's own furniture rather than something asked for. The station "
             "documents that firing one bypasses its own frequency and budget gates, "
             "so Actions per call is the only ceiling."),
    "allow_takeover": dict(group="perms", kind="select", tiered=True, admin=True,
        label="Put a different show on air",
        help="Pins a show over the weekly schedule — a different DJ, for everyone — "
             "for an hour by default. The only caller action that outlives the call: "
             "it keeps running after they hang up, and the DJ can also cancel a "
             "takeover you set yourself from the station's admin page. Lands at the "
             "end of the record playing at the time."),

    # --- call length ---
    "max_call_seconds": dict(group="closing", kind="number", label="Hang up after (s)",
        help="Hard ceiling. The DJ signs off in character first rather than the "
             "audio just stopping. 600 = ten minutes."),
    "guest_session_hours": dict(group="security", kind="number",
        label="Guest code expires (hours)",
        help="Per device: each browser that typed the code runs its own "
             "clock. On a shared or public machine, a typed code should not "
             "outlive its typist — the card forgets it after this long and "
             "shows a lock button to forget it immediately. 0 remembers it "
             "until Sign out."),
    "front_access": dict(group="security", kind="select",
        label="Call-in access",
        help="This is the PHONE — who may ring at all. What a caller may DO "
             "once through is separate and per-tier, feature by feature, "
             "under Caller permissions. The panel always needs the admin "
             "password, whichever of these you pick."),
    # --- player settings: what the card shows, per surface ----------------
    # Every row here is asked twice, once for this page and once for an embed.
    # The panel lays them out as a two-column matrix, which is why the labels
    # are short: the column heading carries the surface, not the label.
    "show_caller_help": dict(group="player", kind="check",
        label="“What can I ask?” button",
        help="Opens the same live reference this panel shows you, filtered to "
             "what is actually switched on. Most callers assume a phone-in only "
             "takes requests."),
    "embed_caller_help": dict(group="player", kind="check",
        label="“What can I ask?” button (embed)",
        help="The same button, in a frame on somebody else's page."),
    "chat_idle_minutes": dict(group="chat", kind="number",
        label="Close after quiet (min)",
        help="A chat with nothing said for this long is over: the record is "
             "written and the id stops resuming. The widget keeps its side, "
             "so a returning caller simply starts a fresh conversation."),
    "chat_max_messages": dict(group="chat", kind="number",
        label="Messages per chat",
        help="A ceiling on one conversation, not a rate: hitting it closes "
             "the chat politely. 0 = no ceiling."),
    "chat_max_hours": dict(group="chat", kind="number",
        label="Longest chat (hours)",
        help="However active, a chat this old is closed and written down. "
             "Resumable is not immortal."),
    "max_open_chats": dict(group="chat", kind="number",
        label="Open chats at once",
        help="Across all callers. Each open chat is a transcript in memory "
             "and a potential LLM spend; 0 = unlimited."),
    "chats_per_hour": dict(group="chat", kind="number",
        label="New chats per hour",
        help="Fresh conversations opened per hour, all callers together. "
             "0 = unlimited. Resuming an existing chat is never counted."),
    "chats_per_day": dict(group="chat", kind="number",
        label="New chats per day",
        help="The hard wallet ceiling on fresh chats, all callers together — "
             "the text line's equivalent of Calls per day. 0 = unlimited."),
    "chat_caller_cooldown_secs": dict(group="chat", kind="number",
        label="Reopen wait time (s)",
        help="How long ONE caller must wait between opening chats — the "
             "per-visitor brake the phone has as Redial wait. A text line "
             "is scriptable in a way a call is not, so this singles out one "
             "abuser where the hourly and daily caps only stop a crowd. "
             "Resuming an open chat never waits."),
    "chat_msgs_per_minute": dict(group="chat", kind="number",
        label="Messages per minute",
        help="Per chat. A human types a handful; a script does not. The "
             "excess is refused in-world, not queued."),
    "show_theme_toggle": dict(group="player", kind="check",
        label="Light / dark toggle",
        help="Forcing a theme below hides this either way — there is nothing "
             "to toggle between."),
    "embed_theme_toggle": dict(group="player", kind="check",
        label="Light / dark toggle (embed)",
        help="Usually worth off: a caller flipping the card to light on a dark "
             "host page gets a bright rectangle in the middle of it."),
    "show_settings_gear": dict(group="player", kind="check",
        label="Settings gear",
        help="The way into this panel from the card. Off secures nothing — "
             "/settings still answers by URL and still asks for the password — "
             "just stops advertising it."),
    "show_chat_button": dict(group="player", kind="check",
        label="“Text the booth” button",
        help="A third way in, beside Call: typed conversation with the "
             "on-air DJ. Needs the text line switched on under Running "
             "the line."),
    "embed_chat_button": dict(group="player", kind="check",
        label="“Text the booth” button (embed)",
        help="The same door on the embedded card. Off by default: three "
             "buttons crowd a 190px frame."),
    "show_voicemail_button": dict(group="player", kind="check",
        label="\u201cLeave a message\u201d button",
        help="A second button beside Call, so the machine is on offer even "
             "while the booth could pick up live. Voicemail itself has to be "
             "switched on under Running the line."),
    "embed_voicemail_button": dict(group="player", kind="check",
        label="\u201cLeave a message\u201d button (embed)",
        help="The same second button, on the embedded card."),
    "show_push_to_talk": dict(group="player", kind="check",
        label="Push to talk",
        help="The caller's mic stays closed except while they hold (or tap to "
             "latch) a talk bar — space works on a keyboard. Better control in "
             "a noisy room, and the DJ never hears a TV in the background. The "
             "mic permission is still asked once, at pickup. On by default; "
             "switch off for an open mic from pickup."),
    "embed_push_to_talk": dict(group="player", kind="check",
        label="Push to talk (embed)",
        help="The same bar, on the embedded card."),
    "voice_effect": dict(group="effects", kind="select", label="Voice effect",
        help="A radio colour on the DJ's voice, applied in the caller's "
             "browser — the broadcast never hears it. On phones it plays "
             "through the default output, so the Speaker/earpiece button has "
             "nothing to route while an effect is on. Hear it with 'Test "
             "with effect' below."),
    "voice_effect_level": dict(group="effects", kind="number",
        label="Effect intensity",
        # Every effect, not the first three — the dial vanished for anyone
        # picking a newer colour, which the operator read (fairly) as the
        # volume control disappearing. Operator-reported.
        needs=("voice_effect", ["telephone", "cb", "walkie", "am", "megaphone", "underwater", "stadium", "intercom", "shortwave", "lofi"]),
        help="0–100. 100 is the effect at full character; lower settles it "
             "toward the clean voice — 40 is a hint of radio rather than a "
             "costume. Test with effect uses this number."),
    "show_dj_avatar": dict(group="player", kind="check", label="DJ photo",
        help="Served through this origin, so it still loads from an https page "
             "off your network."),
    "embed_dj_avatar": dict(group="player", kind="check", label="DJ photo (embed)",
        help="Off if the host page already shows the same photo."),
    "default_to_speaker": dict(group="player", kind="check",
        label="Start calls on loudspeaker",
        help="A live microphone puts the phone into its voice-call audio mode, "
             "which routes to the earpiece — so music playing out loud goes "
             "quiet and private the moment the DJ answers, which is wrong in a "
             "car. The caller can flip it either way mid-call with the Speaker "
             "button. What the browser will actually allow varies: iOS Safari "
             "publishes no audio-routing API at all, so there the button asks "
             "and the platform decides."),
    "avatar_style": dict(group="player", kind="select", label="DJ photo shape",
        help="Applies wherever the photo is shown. Round suits a portrait and "
             "is what the card was built around; square matches a host page "
             "whose own artwork has corners."),
    "show_dj_show": dict(group="player", kind="check", label="Show name",
        help="The programme currently on air."),
    "embed_dj_show": dict(group="player", kind="check", label="Show name (embed)",
        help="Off if the host page already says what show is on."),
    "show_dj_tagline": dict(group="player", kind="check", label="DJ tagline",
        help="The persona's one-line blurb, as the station publishes it."),
    "embed_dj_tagline": dict(group="player", kind="check", label="DJ tagline (embed)",
        help="Off if the host page already carries it."),
    "show_now_playing": dict(group="player", kind="check", label="Now playing",
        help="Updates on the card's own 20-second poll, so it will briefly "
             "disagree with a host page's faster ticker."),
    "embed_now_playing": dict(group="player", kind="check", label="Now playing (embed)",
        help="Off if the host page already has a now-playing line."),
    "word_ringing": dict(group="wording", kind="text", label="Ringing",
        placeholder="default: Ringing…"),
    "word_answering": dict(group="wording", kind="text", label="Answering",
        placeholder="default: Answering…"),
    "word_online": dict(group="wording", kind="text", label="On the line",
        placeholder="default: On the line"),
    "word_recording": dict(group="wording", kind="text", label="Recording",
        placeholder="default: Recording…"),
    "word_hangup": dict(group="wording", kind="text", label="Hang up",
        placeholder="default: Hang up"),
    "word_vm_button": dict(group="wording", kind="text", label="Leave a message",
        placeholder="default: Leave a message"),
    "word_ptt": dict(group="wording", kind="text", label="Talk bar",
        placeholder="default: Tap to talk"),
    "word_closed": dict(group="wording", kind="text", label="Line closed",
        placeholder="default: Line closed"),
    "word_message_only": dict(group="wording", kind="text", label="Voicemail-only line",
        placeholder="default: Message only"),
    "call_button_mode": dict(group="player", kind="select", label="Call button",
        help="“Call the DJ” is the honest label when the card shows whoever "
             "happens to be on air. The DJ's name reads better on a station "
             "whose listeners know the roster, and follows it as the show "
             "changes."),
    "call_button_label": dict(group="player", kind="text", label="Button text",
        needs=("call_button_mode", "custom"),
        placeholder="Call the DJ",
        help="Shown only for the custom option above."),
    "ask_call_feedback": dict(group="player", kind="check",
        label="Ask how the call went",
        help="A thumbs up or down under the card once the line drops, stored "
             "against that call's own transcript so a bad one can be found and "
             "read back. Nothing else is collected."),
    "widget_theme": dict(group="player", kind="select", label="Colours",
        help="Auto follows the viewer and keeps the toggle. Light and dark force "
             "one and hide it. Inherit matches the page the widget is embedded "
             "in; on this page it behaves as auto."),
    "min_call_seconds": dict(group="closing", kind="number",
        label="Earliest hang-up (s)",
        help="The floor under the DJ ending a call itself. 60 by default: a model "
             "deciding a call is over after two words is worse than one that "
             "lingers, and the caller cannot tell it from the line dropping. "
             "0 removes the guard."),
    "idle_prompt_secs": dict(group="closing", kind="number", label="Check in after (s)",
        help="Seconds without SPOKEN WORDS before the DJ asks if they're still "
             "there. Background noise doesn't count. 0 never checks in."),
    "idle_max_nudges": dict(group="closing", kind="number", label="Check-ins before hanging up",
        needs=("idle_prompt_secs", True),
        help="After this many unanswered check-ins the DJ signs off and gets back "
             "to the broadcast."),

    # --- tune the caller in ---
    "tune_in_on_call": dict(group="tunein", kind="check", label="Tune the caller in",
        help="Starts the station stream in the caller's browser at pickup, never "
             "while ringing. The station refuses requests when nobody is listening "
             "and a caller on the line doesn't otherwise count — and it sounds like "
             "a real phone-in. Recommended."),
    "tune_in_audible": dict(group="tunein", kind="check",
        needs=("tune_in_on_call", True),
        label="Pipe the broadcast into the call",
        help="Off, the caller still counts as a listener — requests keep "
             "working — but hears only the DJ. On, the station plays "
             "underneath at the volume below."),
    "tune_in_url": dict(group="tunein", kind="text", label="Stream URL",
        needs=("tune_in_on_call", True),
        placeholder="default: derived from the station address (plain http only)",
        help="Behind TLS a browser silently refuses to load an http stream into an "
             "https page, and the call runs with no station behind it. Paste the "
             "station's own https stream. The pipeline check tests it."),
    "tune_in_volume": dict(group="tunein", kind="number", label="Volume (%)",
        needs=("tune_in_on_call", True),
        help="10 by default. 0 keeps it silent and the caller still counts as a "
             "listener. Much above 20 and, on speakers, it bleeds into their "
             "microphone and gets transcribed as if they had said it."),

    # --- sharing the microphone ---
    "avoid_on_air_overlap": dict(group="onair", kind="check", label="Pause the call while on air",
        help="Anything sent to air waits for the broadcast to go quiet, and the DJ "
             "steps back from the call while it plays — telling the caller either "
             "side rather than talking over itself."),
    "on_air_quiet_secs": dict(group="onair", kind="number", label="Air is busy for (s)",
        needs=("avoid_on_air_overlap", True),
        help="Fallback for when the station's log doesn't say what was spoken — "
             "when it does, the hold is sized to the words themselves. "
             "A typical link runs 20–30 seconds."),

    "ask_caller_name": dict(group="call", kind="check", label="Ask the caller's name",
        help="Off by default — being asked your name to request a song is friction. "
             "A volunteered name is still used to credit them on air."),
    # Hidden once an opening line exists, because the opening line replaces it.
    # Two controls where only one can win, with nothing saying which, is the
    # shape 0.9.61 removed from `front_access` — the panel showed both and the
    # style silently stopped mattering the moment you typed anything below.
    "greeting_style": dict(group="call", kind="select", label="Greeting style",
        needs=("greeting", False),
        help="'Warm ask' picks up in persona and invites them in. 'Mid-world' "
             "answers in character and lets the caller lead. Neither reads out "
             "a menu."),
    "persona_override": dict(group="call", kind="select", label="Who answers",
        help="Default is whoever is actually live on air. Pin one DJ to force "
             "every call to them, or roll a different one from the roster each "
             "call."),
    "greeting": dict(group="call", kind="text", label="Opening line",
        placeholder="default: in character, following the greeting style above",
        help="An instruction to the DJ, not a script it reads out. Writing one "
             "replaces the greeting style, which is why that field disappears."),

    "min_endpointing_delay": dict(group="turns", kind="number",
        label="Wait before replying (s)",
        help="How long the DJ waits after you stop making sound. Lower feels "
             "snappier and cuts off anyone who pauses to think; higher adds that "
             "much to every reply. 0 keeps the SDK's tuned default."),
    "max_endpointing_delay": dict(group="turns", kind="number",
        label="Longest wait (s)",
        help="The ceiling on the above when someone is clearly mid-sentence. "
             "0 keeps the default. Must not be below the minimum."),
    "min_interruption_secs": dict(group="turns", kind="number",
        needs=("allow_interruptions", True),
        label="Sound needed to interrupt (s)",
        help="The SDK's default is half a second of SOUND, not words — so with "
             "tune-in on, half a second of the record cuts the DJ off. Real calls "
             "came back chopped into fragments because of it. Raise it on a "
             "speakerphone; 0 keeps the default."),
    "allow_interruptions": dict(group="turns", kind="check",
        label="Let the caller talk over the DJ",
        help="On is how a phone call works. Off is steadier on a speakerphone, "
             "where the station's own audio bleeding back can read as the caller "
             "interrupting."),

    # --- transcripts ---
    # --- voicemail ---
    "chat_enabled": dict(group="usage", kind="check",
        label="Take text chats",
        help="The text line: typed conversation with whoever is on air, "
             "same brain and same tools as the phone, over a plain "
             "WebSocket — no WebRTC, so it works where calls cannot. "
             "The Line's pause switch closes this door too."),
    "voicemail_enabled": dict(group="usage", kind="check",
        label="Enable voicemail",
        help="The machine's master switch — everything below applies only "
             "while this is on."),
    "voicemail_when": dict(group="voicemail", kind="select", label="Answer with voicemail",
        needs=("voicemail_enabled", True),
        help="'When a live call is impossible' turns a busy or off-air "
             "line's refusal into a message instead of silence. Pause all "
             "calls closes the machine too — the kill switch outranks it — "
             "and the hourly and daily caps and the redial wait still "
             "refuse, on purpose: a message costs STT, and a robot "
             "redialling the machine is the robot the caps exist for. "
             "'Always' makes the line voicemail-only, the cheapest way to "
             "run it: no LLM turns at all."),
    "allow_voicemail": dict(group="perms", kind="select", tiered=True,
        label="Leave a voicemail",
        help="Who may talk to the machine at all. The Voicemail section "
             "decides WHEN it answers; this decides WHO it answers for."),
    "allow_chat": dict(group="perms", kind="select", tiered=True,
        label="Text the booth",
        help="Who may open the text line at all. The Text line section "
             "holds its clocks and ceilings; this decides WHO gets in."),
    "live_calls_enabled": dict(group="usage", kind="check",
        label="Take live calls",
        help="Off, the Call button becomes the machine's door (with "
             "voicemail on) or says the line is closed. Independent of "
             "Voicemail below — the two switches together are the line's "
             "mode: phone, phone with a machine, voicemail-only, or "
             "closed."),
    "voicemail_greeting_mode": dict(group="voicemail", kind="select",
        label="Greeting comes from",
        help="Staged clips answer instantly. 'Fresh each call' writes a new "
             "line in the persona's own voice at pickup — a model line plus a "
             "TTS render, a few seconds on slow backends — and falls back to "
             "the staged clip, then the beep, if it cannot make it in time."),
    # The quoted default is the REAL one from voicemail/greetings.py, token
    # case included: the filler drops unknown placeholders silently, so a
    # panel that advertises {DJ} teaches operators a token that vanishes.
    "voicemail_greeting": dict(group="voicemail", kind="text", label="Greeting",
        placeholder="derived: “You've reached {station}. {dj} is on the air "
                    "right now — leave a request after the beep.”",
        help="Spoken in the on-air DJ's own voice, so it is staged ahead of "
             "time below rather than generated while a caller waits. {station}, "
             "{dj} and {show} are filled in per persona; with nobody on air the "
             "machine answers as the station itself, in your default voice. "
             "Changing this re-renders every clip on the next staging run. "
             "Blank reads: \u201cYou've reached {station}. {dj} is on the air "
             "right now \u2014 leave a request after the beep.\u201d"),
    "voicemail_max_seconds": dict(group="voicemail", kind="number",
        label="Message ceiling (s)",
        help="The hard stop on one message. STT runs for at most this long, "
             "which is what makes voicemail cheap to leave wide open."),
    "voicemail_destination": dict(group="voicemail", kind="select", label="Messages go",
        help="'Held for you' is the safe default — messages land below, and "
             "nothing reaches the air without you. The rest act on the station "
             "and need its admin credentials. 'Triage' reads each message with "
             "the configured model and picks for itself: a song request, a "
             "line for the on-air DJ, or one of the station's segments — "
             "bounded by the caller permissions above, one action per message."),

    "record_calls": dict(group="record", kind="check", label="Keep call transcripts",
        help="Both sides of each call, the tools it used and the settings it ran "
             "under, written to data/calls — how a bad call gets diagnosed, and "
             "also a stranger's conversation on your disk."),
    "record_keep": dict(group="record", kind="number",
        label="How many transcripts to keep",
        needs=("record_calls", True),
        help="Older ones are deleted as new calls land. This is about how long a "
             "caller's words stay on your disk, not about space."),

    # --- usage ---
    "max_concurrent_calls": dict(group="usage", kind="number", label="Calls at once",
        help="Callers on the line at the same time. Each is a separate model session."),
    "calls_per_hour": dict(group="usage", kind="number", label="Calls per hour",
        help="Across everybody — the main guard against a runaway loop."),
    "calls_per_day": dict(group="usage", kind="number", label="Calls per day",
        help="The hard ceiling on what a day can cost. The hourly limit alone "
             "still allows 24× that."),
    "calls_paused": dict(group="usage", kind="check", label="Pause all calls",
        help="Kill switch. The card still shows who's on air, but nobody can "
             "start a call — the answering machine stays silent too. Takes "
             "effect immediately."),
    "max_actions_per_call": dict(group="usage", kind="number", label="Actions per call",
        help="Requests, on-air messages and segments together. At the limit the "
             "DJ says so warmly and keeps talking — never an error."),
    "caller_cooldown_secs": dict(group="usage", kind="number", label="Redial wait time (s)",
        help="How long one caller waits before calling back. 0 while testing."),

    # --- speech hygiene ---
    "strip_stage_directions": dict(group="speech", kind="check", label="Strip stage directions",
        help="Models write *shuffles records* and (laughs), and the voice reads "
             "them aloud. Removed whatever the model does."),
    "tts_dash_style": dict(group="speech", kind="select", label="Em dashes",
        help="Models love an em dash and voices stumble on it. Spoken as a "
             "breath by default; 'a plain dash' speaks it as \" - \"; "
             "'leave them' hands it to the voice backend as written."),
    "profanity_mode": dict(group="speech", kind="select", label="Expletives",
        help="Applied to every spoken line, so it never depends on the model "
             "behaving."),
    "profanity_words": dict(group="speech", kind="text", label="Word list",
        placeholder="default: the built-in broadcast list",
        help="Comma-separated."),

    # --- house style ---
    "style_conversation": dict(group="style", kind="text", label="Conversation",
        placeholder="default: the persona sets the pace",
        help="Steers the call as a whole — how much the DJ leads, how fast it "
             "moves. e.g. 'let the caller lead; don't fill silences'."),
    "style_answering": dict(group="style", kind="text", label="Answering",
        placeholder="default: as the persona would, at its own length",
        help="e.g. 'keep answers to two sentences'."),
    "style_signoff": dict(group="closing", kind="text", label="Signing off",
        placeholder="default: in character, no fixed formula",
        help="e.g. 'mention what's coming up next before you hang up'."),

    # --- back to air ---
    "callback_enabled": dict(group="callback", kind="check", admin=True,
        label="Mention the call on air",
        help="One passing line between tracks AFTER the caller hangs up, "
             "re-voiced by the station in the persona. This is the whole "
             "section: it is not the announcements or segments a caller "
             "triggers mid-call — those speak on air through their own "
             "permissions, under Caller permissions."),
    "callback_max_words": dict(group="callback", kind="number", label="Length (words)",
        needs=("callback_enabled", True),
        help="Short is better — a mention, not a recap."),
    "callback_min_turns": dict(group="callback", kind="number", label="Min caller turns",
        needs=("callback_enabled", True),
        help="Calls that never got going aren't worth mentioning."),
    "callback_instructions": dict(group="callback", kind="text", label="Extra steer",
        needs=("callback_enabled", True),
        placeholder="default: one passing mention, in character",
        help="e.g. 'never name the caller' or 'tie it to the current track'."),

    # --- station awareness ---
    "context_recent_tracks": dict(group="context", kind="number", label="Recently played songs",
        help="Each item costs time-to-first-token on EVERY turn, not just at "
             "the start. 0 leaves it out."),
    "context_upcoming": dict(group="context", kind="number", label="Coming-up songs",
        help="Lets the DJ answer 'what's next' without guessing."),
    "context_booth_lines": dict(group="context", kind="number", label="On-air chatter",
        help="Recent lines from the on-air DJ, so the call doesn't repeat them."),
    "context_schedule": dict(group="context", kind="check", label="Know the rest of the line-up",
        help="The names of the station's OTHER shows, so \"what's on after this?\" "
             "gets an answer. The current show is always known. Off by default: "
             "prompt weight on every turn for a question most callers never ask. "
             "Rides along regardless while show takeovers are allowed — a DJ who "
             "can be asked to switch shows has to recognise their names."),

    # --- sounds ---
    "call_sounds": dict(group="sounds", kind="check", label="Play call sounds",
        help="Ringing, the line picking up, a hold click when the DJ steps onto "
             "the broadcast, hang-up, and an engaged tone."),
    "sound_pack": dict(group="sounds", kind="select", label="Sound set",
        needs=("call_sounds", True),
        help="Both are generated in the browser — neither needs a file. "
             "'Exchange' is the telephone network; 'Handset' is a physical phone "
             "in a room."),
    "sound_ring": dict(group="sounds", kind="text", label="Ring",
        needs=("call_sounds", True),
        placeholder="default: the sound set above",
        help="What the caller hears while the line rings."),
    "sound_pickup": dict(group="sounds", kind="text", label="Pick up",
        needs=("call_sounds", True), placeholder="default: the sound set above",
        help="The click of the DJ answering."),
    "sound_hold": dict(group="sounds", kind="text", label="On hold",
        needs=("call_sounds", True), placeholder="default: the sound set above",
        help="Played once when the DJ steps onto the broadcast mid-call."),
    "sound_hangup": dict(group="sounds", kind="text", label="Hang up",
        needs=("call_sounds", True), placeholder="default: the sound set above",
        help="The receiver going down at either end."),
    "sound_failed": dict(group="sounds", kind="text", label="Can't connect",
        needs=("call_sounds", True),
        placeholder="default: the sound set above",
        help="Engaged tone: the line is busy, the limit is reached, or the call "
             "couldn't connect."),
    "sound_vm_beep": dict(group="sounds", kind="text", label="Voicemail beep",
        placeholder="default: the classic tone",
        help="The answering machine's beep — the one server-played sound; "
             "the formats note above applies double here. Unplayable falls "
             "back to the tone; the verdict shows below."),
    "call_volume": dict(group="sounds", kind="number", label="Default volume",
        needs=("call_sounds", True), help="Starting playback volume for a call."),
    "ring_cut_at_pickup": dict(group="sounds", kind="check",
        needs=("call_sounds", True),
        label="Ring yields at pickup",
        help="The ring fades out the moment the DJ answers — how a phone "
             "behaves, and recommended. Off lets a long ring or jingle "
             "finish underneath the DJ's hello. Short one-shots (the pickup "
             "click, a beep) are never cut either way."),
}

# Sentinel value for persona_override: roll a different DJ from the roster on
# every call, rather than pinning one. Lives here so the worker and the panel
# agree on the spelling.
RANDOM_PERSONA = "__random__"



# Choices for the select fields that aren't populated from a live source.
STATIC_CHOICES = {
    "profanity_mode": [("mask", "Mask them (s—)"), ("drop", "Remove them"), ("off", "Leave them alone")],
    "tts_dash_style": [
        ("pause", "A breath — spoken as a short pause (default)"),
        ("hyphen", "A plain dash — spoken as “ - ”"),
        ("keep", "Leave them — the voice decides"),
    ],
    "greeting_style": [("inviting", "Warm ask — what's on your mind?"), ("in-world", "Mid-world — no question")],
    # Three levels, and `auto` is not one of them.
    #
    # It is still a valid stored value and still behaves exactly as it always
    # did — it is the default, and changing that would stop every existing
    # line from taking calls. But it is not a fourth kind of access, it is a
    # rule for picking between two of these, and offering it as a peer meant
    # the list read as four policies when there are three. The panel shows
    # whichever of the three `auto` currently resolves to.
    "front_access": [
        ("open", "Open — anyone who loads the page can call"),
        ("guest", "Guest code — callers type the code you share"),
        ("admin", "Admin only — the phone is closed to callers"),
    ],
    "call_button_mode": [
        ("default", "“Call the DJ”"),
        ("name", "The live DJ's name — “Call Francesca”"),
        ("custom", "Something else…"),
    ],
    "avatar_style": [
        ("round", "Round — a portrait"),
        ("square", "Square — a thumbnail"),
    ],
    "voice_effect": [
        ("none", "None — the voice as the backend made it"),
        ("telephone", "Telephone — narrow, like a real phone line"),
        ("cb", "CB radio — squeezed and a little dirty"),
        ("walkie", "Walkie-talkie — tight, crunchy, push-to-talk energy"),
        ("am", "AM radio — warm, narrow, late-night"),
        ("megaphone", "Megaphone — a loud hailer, harsh on purpose"),
        ("underwater", "Underwater — everything above a murmur is gone"),
        ("stadium", "Stadium PA — big room, hard consonants"),
        ("intercom", "Intercom — drive-thru squawk"),
        ("shortwave", "Shortwave — distant, fading in from somewhere"),
        ("lofi", "Lo-fi cassette — soft top, a little dust"),
    ],
    "voicemail_greeting_mode": [
        ("staged", "Staged clips — instant, rendered ahead of time"),
        ("fresh", "Fresh each call — in persona, staged clip as the backup"),
    ],
    "voicemail_when": [
        ("closed", "When a live call is impossible (busy, off air, live calls off)"),
        ("always", "Always — the line is voicemail-only"),
    ],
    "voicemail_destination": [
        ("hold", "Held for you — read them in this panel"),
        ("request", "Sent to the station as a song request"),
        ("air", "Handed to the on-air DJ to mention"),
        ("triage", "Triaged by the model — request, mention, or a segment"),
    ],
    "widget_theme": [
        ("auto", "Auto — follow the viewer, keep the toggle"),
        ("light", "Light"),
        ("dark", "Dark"),
        ("station", "The station's own colours"),
        ("inherit", "Inherit from the page it's embedded in"),
    ],
}


def _choices_for(name: str):
    """Static choices, except the sound packs — those are read from disk so a
    new pack is a folder in assets/sounds/ rather than an edit here."""
    if name == "sound_pack":
        import sounds

        return sounds.packs()
    # Every tiered permission offers the same four, named once. The panel
    # renders them as three columns and an unticked row rather than as a
    # dropdown, but it is one field with one value underneath.
    if SCHEMA.get(name, {}).get("tiered"):
        return list(TIER_CHOICES)
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
                # Three-valued since 0.9.155: True (dies without them),
                # "optional" (works, credentials only sharpen it), False.
                # bool() here flattened "optional" to True and the panel
                # showed a hard requirement that does not exist.
                "admin": meta.get("admin") if meta.get("admin") == "optional"
                         else bool(meta.get("admin")),
                # Rendered as three columns of checkboxes rather than a
                # dropdown: what an operator wants to see is which callers get
                # this, all three answers at once, down a page of permissions.
                "tiered": bool(meta.get("tiered")),
                "choices": _choices_for(name),
            }
            for name, meta in SCHEMA.items()
        },
        # The column headings, and the order they cascade in.
        "tiers": [{"id": t, "title": title} for t, title in
                  (("open", "Anyone"), ("guest", "Guest code"), ("admin", "Admin"))],
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
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    # The two aggregators are deliberately EMPTY rather than seeded with
    # plausible ids. Their catalogues are namespaced (`openai/gpt-4.1-mini`)
    # and move, and a guessed id here is not a shorter list — it is a 404 on
    # every single utterance, which sounds exactly like the DJ never speaking.
    # Both only appear at all once their key is stored, so /v1/models can
    # always be asked, and what it says is the only list worth showing.
    "requesty": [],
    "gateway": [],
    # The operator's own OpenAI-compatible server (llama.cpp, vLLM, LM
    # Studio). Discovered live from its /v1/models; there is nothing sensible
    # to curate for a server we have never seen.
    "openai-compatible": [],
    "ollama": [],
}

# Which stored key each provider needs before it can answer a call at all.
# None means "needs no key of ours": Ollama runs on the operator's own network,
# and the local Whisper is compiled into this container.
#
# The panel offers only the providers whose key is present. Listing all five
# regardless meant a fresh install's Provider dropdown was four ways to
# configure a call that could not connect, and the failure arrived later, from
# a test button, as a 401 — rather than at the moment of choosing.
LLM_PROVIDER_KEY: dict[str, str | None] = {
    "openai": "openai_api_key",
    "openrouter": "openrouter_api_key",
    "google": "google_api_key",
    "anthropic": "anthropic_api_key",
    # The three SUB/WAVE offers that this did not. A companion app that cannot
    # point at the same provider the station is already paying for makes the
    # operator keep two accounts to run one radio station.
    "deepseek": "deepseek_api_key",
    "requesty": "requesty_api_key",
    "gateway": "gateway_api_key",
    # Your own OpenAI-protocol server, like the station's own
    # openai-compatible provider: no managed key. If the server wants one
    # anyway, set OPENAI_COMPAT_API_KEY in the environment.
    "openai-compatible": None,
    "ollama": None,
}

# Longer names for the dropdown. The bare id says what to type, not what it is
# — "gateway" in a list next to "google" is a coin toss.
LLM_PROVIDER_LABELS: dict[str, str] = {
    "openai": "OpenAI (GPT)",
    "openrouter": "OpenRouter (aggregator, ~340 models)",
    "google": "Google (Gemini)",
    "anthropic": "Anthropic (Claude)",
    "deepseek": "DeepSeek",
    "requesty": "Requesty (aggregator)",
    "gateway": "Vercel AI Gateway (aggregator)",
    "openai-compatible": "OpenAI-compatible (llama.cpp · vLLM · LM Studio)",
    "ollama": "Ollama (your own box, no key)",
}

STT_PROVIDER_KEY: dict[str, str | None] = {
    "local": None,
    "deepgram": "deepgram_api_key",
    # Uses the same OpenAI key as the LLM and cloud TTS.
    "openai": "openai_api_key",
    "google": "google_api_key",
}


def providers_with_keys(mapping: dict[str, str | None], keep: str = "") -> list[str]:
    """The providers an operator could actually select, in declaration order.

    `keep` is always included even when its key is missing. Whatever is
    CONFIGURED has to stay in the list: dropping it would silently show a
    different provider as selected than the one the next call will use, which
    is a worse failure than offering one that needs a key.
    """
    import secrets_store

    out = [
        name for name, field in mapping.items()
        if field is None or secrets_store.get(field)
    ]
    if keep and keep not in out:
        out.append(keep)
    return out


def _sidecar_default(env_var: str, port: int, path: str = "") -> str:
    """A neighbouring service's URL, guessed from where the station is.

    `localhost` is the wrong guess and always was. This runs in a container,
    so localhost is the container itself — never where Ollama or a local TTS
    server lives — and the panel offered it as the autofill anyway, so the
    model list was permanently empty with `ollama model list unavailable at
    http://localhost:11434` in the log and nothing saying why.

    The station's own URL is the one address this deployment definitely knows
    and definitely resolves, and on every deployment this project targets the
    local model servers sit beside it. So default to the station's host with
    the neighbour's port. Still only a default: an explicit env var or a saved
    setting wins, and localhost remains the fallback for a dev machine with no
    station configured.
    """
    override = os.environ.get(env_var, "").strip()
    if override:
        return override
    host = ""
    try:
        from urllib.parse import urlparse

        host = urlparse(station_base_url()).hostname or ""
    except Exception:                                         # noqa: BLE001
        host = ""
    return f"http://{host or 'localhost'}:{port}{path}"


def provider_base_urls() -> dict:
    """Default endpoint per LLM provider, offered by the settings UI as an
    autofill. A function rather than a constant because the ollama default is
    derived from the configured station, which is not known at import time."""
    return {
        "ollama": _sidecar_default("OLLAMA_BASE_URL", 11434, "/v1"),
        "openrouter": "https://openrouter.ai/api/v1",
        "openai": "",
        "google": "",
        "anthropic": "",
        **{p: h for p, (h, _d) in OPENAI_PROTOCOL_HOSTS.items()},
    }


# Providers that are OpenAI's wire protocol at a different address, as
# (base URL, default model). One branch in call/providers.build_llm covers all
# of them and one /v1/models call populates their dropdowns; the only thing
# that differs is where, and which key. Kept here rather than in providers.py
# because the settings API needs the same addresses to discover the models.
#
# The two aggregators have NO default model on purpose. Their catalogues are
# namespaced (`openai/gpt-4.1-mini`) and move, so a stand-in id here would 404
# on every utterance while looking like a working configuration — the exact
# failure model_for() exists to prevent. No model means the call refuses with a
# sentence saying to pick one.
OPENAI_PROTOCOL_HOSTS: dict[str, tuple[str, str]] = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "requesty": ("https://router.requesty.ai/v1", ""),
    "gateway": ("https://ai-gateway.vercel.sh/v1", ""),
}


def tts_base_urls() -> dict:
    return {
        "cloud": "https://api.openai.com",
        "local": _sidecar_default("LOCAL_TTS_URL", 8001),
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


# Settings that were replaced rather than removed, and how to read an old file
# as though it had always been written the new way.
#
# The rule is that an upgrade may never change what a caller experiences. A
# renamed field with no migration would silently revert whatever the operator
# had chosen back to the built-in default, which is the same class of failure
# as a setting that does nothing — you find out from a caller.
def _migrate(stored: dict) -> dict:
    """Translate retired fields in place. Pure: the file is left alone until
    the next save, so a rollback still reads its own settings."""
    # 0.9.115: "…or use the live DJ's name" (a checkbox beside a text box, where
    # ticking it silently made the text box do nothing) became one picker.
    if "call_button_mode" not in stored:
        if _coerce(stored.get("call_button_uses_name"), False):
            stored["call_button_mode"] = "name"
        elif str(stored.get("call_button_label") or "").strip():
            stored["call_button_mode"] = "custom"
    # 0.9.116: the caller permissions became tiers. `true` meant "anyone who
    # got through the door", which is exactly `open`, so an existing station
    # behaves identically — and an operator who never opens the panel again
    # keeps precisely the permissions they had.
    for field in TIERED_PERMISSIONS:
        if field in stored and isinstance(stored[field], bool):
            stored[field] = "open" if stored[field] else TIER_OFF
    # 0.9.139: the guest-code expiry moved from minutes to hours — day-shaped
    # answers. A stored minutes value keeps its real duration, rounded up so
    # nobody's code expires EARLIER after an upgrade.
    if "guest_session_hours" not in stored and "guest_session_minutes" in stored:
        minutes = _coerce(stored.get("guest_session_minutes"), 0)
        stored["guest_session_hours"] = -(-int(minutes) // 60) if minutes else 0
    # 0.9.144: voicemail_when carried the off switch as its 'never' option;
    # now a checkbox does. A stored policy IS the operator's answer to both.
    if "voicemail_enabled" not in stored and "voicemail_when" in stored:
        stored["voicemail_enabled"] = stored["voicemail_when"] in ("closed", "always")
        if stored["voicemail_when"] == "never":
            stored["voicemail_when"] = "closed"
    return stored


def _stored() -> dict:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return _migrate(json.load(f))
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
