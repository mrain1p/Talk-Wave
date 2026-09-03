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

from jsonstore import write_atomic

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

# --- re-exports: settings.py was peeled at 0.99.x (the maintainability
# plan, Batch 1). The caller-tier security ladder moved to caller_tiers.py
# and the panel/vocab presentation DATA to settings_schema.py, both pure
# leaves. These imports keep settings_store.<name> byte-identical for all
# 31 callers and every test — one definition each, no second read-site.
from caller_tiers import (  # noqa: F401
    TIERS, TIER_OFF, TIERED_PERMISSIONS, TIER_CHOICES,
    tier_reaches, normalise_tier, permission_reaches,
    tier_from_room, tier_from_vm_room, on_air_from_room, permissions_for,
    guest_door_open,
)
from settings_schema import (  # noqa: F401
    NAV_EXTRA_PAGES, SUPERGROUPS, GROUPS, GROUP_ALIASES, SCHEMA,
    RANDOM_PERSONA, STATIC_CHOICES, MODEL_CHOICES, LOCCA_BASE_URL_DEFAULT,
    LLM_PROVIDER_KEY, LLM_PROVIDER_LABELS, STT_PROVIDER_KEY,
    OPENAI_PROTOCOL_HOSTS, STT_MODEL_CHOICES, OPENAI_VOICES,
)

log = logging.getLogger("callin.settings")

SETTINGS_PATH = Path(
    os.environ.get("SETTINGS_PATH", Path(__file__).parent.parent / "data" / "settings.json")
)

# The one number both ends of the on-air duck are sized from. Imported rather
# than repeated: call/air.py owns the timing and this owns the default, and a
# 5 here against a 4.5 there is how the two ends drifted apart in the first
# place. From the timing LEAF, not call.air: air.py pulls the agents SDK in
# at module scope, and this import used to drag LiveKit into the web process
# for one float. air_timing exists to be importable anywhere; the guard
# stays as a belt for a box with a broken checkout.
try:
    from call.air_timing import DUCK_PAD_SECS as _DUCK_PAD
except Exception:                                          # noqa: BLE001
    _DUCK_PAD = 4.5

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

    # Blank on purpose (0.10.80, the fresh-install defaults review): shipping
    # "openai" pre-picked read as a recommendation and hid the decision. A
    # fresh install now has no brain until the operator chooses one, and the
    # dashboard's needs column says exactly that. Existing deployments keep
    # working: docker installs carry LLM_PROVIDER in .env, and anyone who
    # ever touched the panel has it stored.
    "llm_provider":     ("LLM_PROVIDER", ""),
    "llm_model":        ("LLM_MODEL", ""),
    "llm_base_url":     ("LLM_BASE_URL", ""),
    "llm_temperature":  (None, 0.8),

    # The bundled Whisper (0.10.80): no key, no network, works out of the box
    # — which is what a default should do. Deepgram and the other cloud ears
    # are the upgrade for accuracy, not the entry fee. Docker installs that
    # set STT_PROVIDER in .env keep what they chose.
    "stt_provider":     ("STT_PROVIDER", "local"),
    # DEEPGRAM_MODEL is the historical name from when Deepgram was the only
    # provider; STT_MODEL is what it means now that four providers share it.
    "stt_model":        (("STT_MODEL", "DEEPGRAM_MODEL"), "base.en"),

    # Blank like the LLM (0.10.85, the same review): 'cloud' pre-picked
    # pointed at api.openai.com with no key and failed mid-greeting, which is
    # a worse first experience than being asked to choose. Docker installs
    # that set TTS_MODE in .env keep what they chose; _migrate stamps 'cloud'
    # into pre-0.10.85 stores so an upgrade changes nothing.
    "tts_mode":         ("TTS_MODE", ""),
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
    # The tier ladder got real defaults at 0.10.80 (the operator's
    # fresh-install review): ANYONE gets what stays on the call, GUESTS get
    # everything short of the station-wide switches, ADMIN alone holds what
    # reaches every listener at a stranger's say-so. Existing deployments
    # keep the old all-off grants — _migrate stamps them — so an upgrade
    # hands nothing out.
    #
    # Announcements land on air, but at guest tier only for callers the
    # operator has handed a code to — that handing-out is the consent step
    # the old off-default (0.9.89) existed to force.
    "allow_announcements": (None, "guest"),
    "allow_library_search": (None, "open"),
    # Let a caller who has picked a track out of the search results have THAT
    # recording queued, rather than the words being resolved a second time.
    # Guest tier: it bypasses the station's own request rate limit, so a
    # stranger still goes through the resolver and it leans on
    # `max_actions_per_call` to keep one code-holder in check.
    "allow_exact_queue":  (None, "guest"),
    # Bulk queueing: a whole album, or a run of picks, in one action. Guest
    # tier like the exact queue it is the plural of — but where one pick takes
    # one slot, one of these can take thirty, so it is its own grant and
    # _migrate stamps it off for every store that predates it.
    "allow_album_queue":  (None, "guest"),
    # Finding music by how it sounds, and by the station's own tags. Open, at
    # the same tier as library search, because these are reads that change
    # nothing and cost the station nothing a browse of its admin Library tab
    # doesn't: what they change is whether the DJ has to GUESS. Off, a caller
    # who describes what they want gets one blind request and whatever comes
    # back; on, the DJ can offer three real records by name.
    "allow_sound_search": (None, "open"),
    # An EXPERIMENT, and a mode rather than a capability: on, the six ways of
    # looking leave the DJ's tool list and one `subwave_find_music` takes
    # their place holding them, so the model reports what the caller said
    # instead of choosing a search. Nothing becomes reachable that was not
    # reachable before, and nothing stops being reachable — see
    # call/tools/finding.py. Off by default because it is unmeasured.
    #
    # Deliberately NO schema entry, and so no panel row: the same ruling as
    # station_mcp_url. This is a flag for an A/B the operator runs from
    # data/settings.json, not a control anyone should meet while reading the
    # panel — and a half-finished experiment offered as a switch is how one
    # gets left on. It becomes a panel row if and when the numbers say keep it.
    "single_lookup_tool": (None, False),
    # The post-landing wind-down (call/landed.py): a one-time steer when a
    # request lands, in place of CLOSING's measured-ineffective prose. Same
    # deal as the switch above — deliberately NO panel row until the closing
    # scenario set says keep it; a half-finished experiment offered as a
    # switch is how one gets left on.
    "closing_nudge": (None, False),
    # Taking a queued track back out. Guest tier rather than open: the queue is
    # shared, so this can cancel a record somebody ELSE asked for — which is
    # precisely why the station gives its listeners no cancel of their own. A
    # code-holder undoing their own mistake is the case it is for.
    "allow_cancel_queue": (None, "guest"),
    # The lowest-harm action there is: a like on the current record, exactly
    # what any listener taps in the app — no credentials, no audio changed.
    # Open: the station's own Likes toggle and per-IP rate limit are the
    # real gates, and this is exactly what any listener taps in the app.
    "allow_favorite":     (None, "open"),
    # The operator's own un-heart of the current track (admin likes system, not
    # the public like above, which has no un-like). Only coherent for a caller
    # signed in AS the operator, so it defaults to the admin tier and needs
    # station credentials to work at all.
    "allow_unfavorite":   (None, "admin"),
    # Guest tier: this puts audio on air on the caller's say-so. Skills are
    # the station's own segments (weather, news, dedications, story time…) —
    # safe-ish, so a code-holder may trigger them while a stranger may not.
    # (Sound effects were considered and deliberately not offered — stingers
    # on a caller's say-so add nothing to a call.)
    "allow_skills":       (None, "guest"),
    # With skills on, the DJ may also OFFER one when the moment fits ("want
    # me to spin you a story?") instead of waiting to be asked.
    "offer_skills":       (None, False),

    # Station-wide, so admin tier: these land on everyone listening rather
    # than on the caller who asked — the operator's own phone, nobody
    # else's. Both are served by local wrappers so "Actions per call" caps
    # them — over MCP they would have no ceiling at all.
    "allow_skip_track":   (None, "admin"),
    # The player's operator mode: one-shot typed commands through the chat
    # brain, no conversation. The tier that may command is decided here;
    # whether the mode is on the sheet at all is player_operator_mode.
    "allow_player_commands": (None, "admin"),
    "allow_dj_segment":   (None, "admin"),
    # Further-reaching than either, and the only caller action whose effect
    # outlives the call: it puts a different show — a different DJ — on air
    # for an hour by default. Admin, for the obvious reason.
    "allow_takeover":     (None, "admin"),
    # The same reach as a takeover and quieter: it narrows what the station is
    # allowed to play for a bounded window. Admin, and it needs a station new
    # enough to have the control at all (upstream #1404).
    "allow_genre_lock":   (None, "admin"),
    # The only PERMANENT thing on a call line. Everything else here is over
    # when the window lapses or the record ends; a never-play entry outlives
    # the call, the show and the operator's memory of the call, and nothing
    # goes out on air to say it happened. Admin.
    "allow_never_play":   (None, "admin"),
    # A caller's own conversation relayed to the station's air, one finished
    # turn at a time — the phone-in. Off by default everywhere: a stranger's
    # voice on the broadcast is a decision the operator makes, never a
    # default. The window cap bounds how long one caller may hold the voice
    # channel, because the station's own segments queue behind a live call.
    "allow_on_air":       (None, "off"),
    "on_air_max_seconds": (None, 240),
    "on_air_delay_secs":  (None, 6),
    # "clean" — full bandwidth at the clip's own rate, rumble out, levelled.
    # The phone-band costume is the option, not the default: the first
    # operator to hear themselves aired called it the worst their voice has
    # ever sounded on a phone call (2026-08-18), and the default changed with
    # their say-so. Deployments that never touched this get the new sound.
    "on_air_caller_sound": (None, "clean"),
    # "live" airs each finished turn seconds behind the room (the lag-by-one
    # relay); "after" tapes the whole conversation and plays it once the call
    # ends — the operator's ask (2026-08-18), and the mode where PULL OFF AIR
    # can kill the entire call before a word of it airs.
    "on_air_call_mode": (None, "live"),

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
    # NOT 0, unlike the two delays above, and for a reason that only shows up
    # on a real call. 0 here means "leave the SDK's floor alone" and that floor
    # is half a second of SOUND, not of words — so with tune-in on, half a
    # second of the record the caller is listening to counts as them
    # interrupting. Room 16:41 on 2026-08-16: three of the DJ's turns were a
    # single word ("Actually,", "Safe-rooted,", "I'm sorry,") on a box that had
    # never touched this setting. A default nobody can get right before their
    # first call is not a default, so this ships at a value that survives music
    # bleed and still yields to someone actually talking.
    "min_interruption_secs": (None, 0.8),
    # How long the DJ may work in silence before saying so. The model's own
    # time to first token runs 6.5s typical on this deployment, and a tool
    # call sits on top of that — long enough that the caller cannot tell a
    # thinking DJ from a dead line. 0 = stay silent until the answer is ready.
    "working_line_secs":     (None, 4),
    "working_line_text":     (None, ""),

    # Whether both sides of a call are written to disk at all.
    #
    # On by default because it is how a bad call is diagnosed and the README
    # says so — but it is a transcript of a stranger's conversation, kept on
    # the operator's disk, and until now there was no way to say no. An
    # operator who does not want that must be able to have it, and be able to
    # say how long anything kept sticks around.
    "record_calls":     (None, True),
    "record_keep":      (None, 1000),

    # --- voicemail --------------------------------------------------------
    # A second, much smaller kind of call: greeting, beep, one caller
    # utterance through STT, delivered. Nothing is recorded as audio — the
    # transcript is the message. docs/VOICEMAIL.md is the design.
    # The line's mode, made explicit: live calls on or off. Off with
    # voicemail on is a voicemail-only line; off with voicemail off is a
    # closed line that says so.
    "live_calls_enabled":    (None, True),
    # The Live-on-air cluster's two quick kills, one per door. They narrow
    # allow_on_air (the tier row stays the master): the dashboard flips
    # these without touching who may use the feature. On by default so
    # opening the tier row lights both doors at once.
    "on_air_calls_enabled":    (None, True),
    "on_air_voicemail_enabled": (None, True),

    # --- Open Lines ---------------------------------------------------
    # The DJ puts a topic up on the broadcast and invites the audience in.
    # Off by default and deliberately so: every announcement is an
    # audience-reaching write to the broadcast (`/dj/say` is tagged
    # `mutates-air` upstream), and a feature that starts talking to a
    # station's listeners the moment it is installed is not a default
    # anybody chose. Off, the assembled prompt is byte-identical to a build
    # without this feature — TestOpenLinesIsAdditive holds that.
    "open_lines_enabled":         (None, False),
    # Where the premise comes from. "dj" invents one from the same station
    # context a SUB/WAVE skill invents from — persona, show card, programme
    # intro, what has been played and said. "pool" reads the operator's own
    # list below, in order, so a station can put up exactly the topics it
    # wants and nothing else.
    "open_lines_source":          (None, "dj"),
    # Which targeted directions the "directions" source may draw from.
    # Comma-separated ids or label words from openlines/directions.py's
    # catalogue; blank = the whole catalogue. A list that matches nothing
    # falls back to everything rather than going silent.
    "open_lines_directions":      (None, ""),
    # Where to reach the booth, said on air. Blank = the DJ opens the topic
    # and names no address, which is right when the audience is already
    # looking at the card. Talk Wave supplies this at compose time, so what
    # the DJ reads out is always where Talk Wave actually answers.
    "open_lines_address":         (None, ""),
    # How long a line stays open before the DJ closes it, in character.
    "open_lines_minutes":         (None, 60),
    # The gap between reminders, and the hard ceiling on how many air in one
    # window. The ceiling is the setting that matters: a long duration with a
    # short interval is how a station ends up mentioning the same topic nine
    # times. 0 on either = no reminders at all.
    "open_lines_reminder_minutes": (None, 20),
    "open_lines_reminder_max":    (None, 2),
    "open_lines_followup":        (None, False),
    "open_lines_guest_trigger":   (None, False),
    # Nobody listening, nothing opens. Checked when a line opens and before
    # every reminder, never mid-window: a line that vanished because one
    # listener closed a tab would strand whoever is already typing. 0 =
    # ignore the listener count entirely.
    "open_lines_min_listeners":   (None, 1),
    # Which DJs may open a line. Persona IDs, comma-separated, written by the
    # picker in the panel; blank = any DJ on air. Matched against id OR name so
    # a list typed by hand before the picker existed keeps working.
    "open_lines_personas":        (None, ""),
    # Open a fresh line automatically this often. 0 = manual only, which is
    # the default: the button in the panel opens one for the duration above,
    # and nothing airs that the operator did not press.
    "open_lines_every_minutes":   (None, 0),
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
    "chat_idle_minutes":     (None, 5),
    "chat_max_messages":     (None, 60),
    # Minutes since 0.10.80 (was chat_max_hours — _migrate converts): a chat
    # is a phone-call-shaped visit, not a day-long session, and hour-shaped
    # answers kept dead chats alive holding their transcript in memory.
    "chat_max_minutes":      (None, 10),
    "max_open_chats":        (None, 20),
    "chats_per_hour":        (None, 0),
    "chats_per_day":         (None, 0),
    "chat_caller_cooldown_secs": (None, 30),
    "chat_msgs_per_minute":  (None, 10),
    # The booth opens the conversation when a fresh chat connects, the way the
    # machine greets a voicemail — a text line that answers with silence until
    # the caller types first reads as broken. "canned" formats the line below
    # (fast, no model cost); "fresh" writes one in persona at open; "off" waits
    # for the caller. The response ceiling in seconds is the hang-guard: a model
    # that never answers should not leave the caller watching a typing dot
    # forever.
    "chat_greeting_mode":    (None, "fresh"),
    # Where a tool run's receipt card lands, on every door that shows one —
    # was chat_action_cards (chat-only) until 0.10.92, when the operator asked
    # for one answer across calls, texts and voicemail. "after" is the default
    # and a deliberate behaviour change twice over (0.10.65 for chat, 0.10.92
    # for calls): the cards used to lead the reply, and a receipt before the
    # DJ has said a word reads as the paperwork interrupting the person.
    "action_cards":          (None, "after"),
    "chat_greeting":         (None, ""),
    "chat_reply_timeout_secs": (None, 45),
    # How the DJ's reply ARRIVES in the caller's browser. "typing" reveals it
    # as it is written, which is what makes a text line read as a person;
    # "dots" shows the typing cue and then lands the line whole.
    "chat_reveal":           (None, "typing"),
    # And how fast that reveal runs, in characters per second. The first
    # version was a fixed 30ms per character — about 33 c/s, roughly 400 wpm,
    # which is nobody typing (operator, 2026-08-12). "natural" is the default
    # and is deliberately slower than what shipped before it.
    "chat_type_pace":        (None, "natural"),
    # Keep a chat feeling like a conversation, not a turn-based move: when the
    # CALLER has gone quiet with the ball in their court, the DJ nudges once. On
    # by default; 0 or the switch off disables it. Never fires while the DJ is
    # the one still owing a reply.
    "chat_reprompt":         (None, True),
    # 75, up from 20 (brain review, 2026-08-31): twenty seconds is faster
    # than a phone typist composes a sentence, so the nudge kept landing on
    # people mid-thought. Well above typing pace, still short of "gone".
    "chat_reprompt_secs":    (None, 75),
    "voicemail_greeting":    (None, ""),
    # Fresh by default (0.10.80): a line written in persona at pickup, with
    # the staged clip as the instant fallback — the machine still answers
    # promptly on a slow backend, it just answers alive when it can.
    "voicemail_greeting_mode": (None, "fresh"),
    "voicemail_max_seconds": (None, 30),
    "voicemail_destination": (None, "hold"),
    # The soundbite studio (2026-08-17): the voicemail door repurposed into
    # record → review → send-to-air. "machine" keeps the classic answering
    # machine, so flipping a deployment is the operator's act, never an
    # upgrade's.
    "voicemail_flow": (None, "machine"),
    "vm_air_backend": (None, "dj-reads"),
    # Blank = the station's own default (broadcast:1234), reachable only when
    # talkwave-web shares the station's docker network. No panel row — see
    # the schema note; the env name is the promised override path.
    "vm_mixer_telnet": ("VM_MIXER_TELNET", ""),
    # Blank = derived from HOST_IP:8100 — the published port the mixer already
    # fetches music through (probe-proven). No panel row either.
    "vm_air_base_url": ("VM_AIR_BASE_URL", ""),
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
    "calls_per_hour":       (None, 20),   # across everyone; 0 = unlimited
    "calls_per_day":        (None, 100),  # the hard wallet ceiling
    "caller_cooldown_secs": (None, 20),   # per caller, between calls
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
    # The other side of the same negotiation: while a phone-in is live, the
    # STATION's auto-talk stands down — onair/hush.py flips the station's own
    # Voice switch off for the call and the token server's janitor restores
    # it. Off by default: it is a write to the station's settings, and that
    # stays the operator's decision (the invariant-1 exception, agreed
    # 2026-08-19).
    "quiet_station_on_calls": (None, "off"),
    # How many seconds before the broadcast voice actually lands the call DJ
    # hands over, when the station says it is coming (voice.queued, SUB/WAVE
    # 1.8): with a long warning the call keeps flowing and the DJ steps away
    # just ahead of the voice, instead of gagging the call for the whole
    # queue wait. Needs ~2s of warning to say the hand-over line; with less,
    # the gate closes silently. 0 = hand over the moment the warning arrives.
    # The duck's OPEN, and deliberately the same number as its close: the
    # guard's DUCK_PAD_SECS. Two ends of one gesture, so they are derived from
    # one constant rather than set to 5 here and 4.5 over there and left to
    # drift apart, which is how the ducking got inconsistent in the first
    # place. Zero disables the lead entirely — the hold then begins at the
    # moment the voice lands, with no warning to the caller.
    "on_air_handover_secs": (None, _DUCK_PAD),
    # Last, because it is the fallback: only an entry with no words at all
    # falls back to a fixed hold. The handoff lag that used to sit between
    # these two stopped being an operator's decision in 0.10.97 — see
    # OnAirGuard.HANDOFF_LAG_SECS.

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
    #   auto   open until a guest code is set, then required — the historical
    #          behaviour, still honoured for stores that carry it
    #   open   anyone who can load the page can call, code or no code
    #   guest  the guest code (or the admin password) is required
    #   admin  the admin password only — the phone is closed to callers
    #
    # Default `admin` since 0.10.80 (the operator's fresh-install review): a
    # new line starts closed and is OPENED as a decision, matching the
    # first-run lockdown one step further out. The 0.9.61 lesson still
    # holds — a default change must not close a line that was taking calls —
    # which is why _migrate stamps `auto` into any store that predates this:
    # existing deployments keep exactly the door they had.
    # A guest code typed on a shared machine outlives the person who typed
    # it. 0 keeps it until Sign out; anything else forgets it after that
    # many minutes, and the card offers a lock button to forget it now.
    # Hours, not minutes: "how long should a handed-out code last" is a
    # question with day-shaped answers. 0 = until Sign out.
    "guest_session_hours": (None, 24),
    "front_access":     (None, "admin"),
    # Whether a stored guest code ELEVATES whoever types it. Separate from the
    # door on purpose: "anyone can ring" and "code-holders are their own tier"
    # are two switches an operator sets independently, and inferring the second
    # from whether a code happens to exist meant the only way to turn the guest
    # pathway off was to delete the code (the operator's ask at 0.10.66, and
    # again on 2026-08-16 in the other direction — "guest can be on and anyone
    # can be off or vice versa").
    #
    # Defaults ON so an upgrade changes nothing: a deployment with a code set
    # keeps elevating it exactly as it did before this existed. It is not a
    # power being handed out — the code already opened that tier.
    "guest_tier":       (None, True),

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
    # EXPERIMENTAL. "default" is the card as it has always looked, so every
    # deployment that never touches this sees exactly what it saw before.
    "widget_skin":      (None, "default"),
    # Which way round the caller's three doors sit. The order the markup has
    # always had, so nothing moves for a deployment that never touches it.
    # Stored as a comma list of ids rather than three number fields: an order
    # is one value, and three numbers can disagree with each other.
    "door_order":       (None, "call,chat,vm"),

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
    # A link out of the card, to wherever the operator wants a caller to go —
    # the station's own page by default, since that is where a caller who came
    # in through an embed most often wants to end up. Off until it is set up:
    # a corner button that goes nowhere is worse than an empty corner, so the
    # two visibility ticks below are gated on the link existing at all.
    "corner_link_enabled": (None, False),
    "corner_link_url":     ("CALLIN_CORNER_LINK", ""),
    "corner_link_label":   (None, "The station"),
    # A DRAWN icon since 0.10.141, not an emoji: the emoji was the one
    # element on the card no theme and no skin could touch. A stored emoji
    # still renders as itself, so this changes only deployments that never
    # picked one — which is the point of the change.
    "corner_link_icon":    (None, "radio"),
    "show_corner_link":    (None, True),
    "embed_corner_link":   (None, True),
    # The station player: the ribbon at the card's top edge pulls it down
    # over the phone (full page and the installed app only, never an embed —
    # the host page there usually IS a player), playing the same stream
    # tune-in uses. Off by default, twice over: a gesture surface appearing
    # on every deployed card unasked is the 0.9.61 shape again, and without
    # a public https tune_in_url the player would open onto silence behind
    # TLS.
    "swipe_player":       (None, False),
    # The third card: the station's week. OFF by default for the same reason
    # as the player — a new surface on every deployed card unasked — and it
    # is one tick in the panel. Reads the station's public /schedule.
    "show_guide":         (None, False),
    # The card's own listener count and track heart. ON by default, unlike
    # the player: they are one line of text and one small button on furniture
    # the card already has, not a new surface — and both degrade to nothing
    # when the station won't say (no count) or the track line is empty (no
    # heart).
    "show_listener_count": (None, True),
    "show_track_like":    (None, True),
    # The player's cast control. ON by default and ALWAYS visible while on
    # (operator, 2026-09-01): it used to hide whenever the audio element was
    # gone — paused, parked, stopped — which is exactly when someone wants
    # the picker back to switch speakers or stop casting. The widget still
    # hides it on browsers with no casting API at all.
    "player_cast_button": (None, True),
    # The player's OPERATOR-side controls (2026-09-01): a skip button beside
    # the heart, and the request line's operator mode — one-shot commands
    # through the same brain and tool surface as the text line, with the
    # actions listed under a third queue-card tab. Both ship OFF: they are
    # doors onto admin-backed station writes, and which TIER may walk
    # through is the permission matrix's question (allow_skip_track,
    # allow_unfavorite, allow_player_commands) — these switches only decide
    # whether the furniture is on the sheet at all.
    "player_skip_button": (None, False),
    "player_operator_mode": (None, False),
    # Which face the page opens on. The player as the front page makes the
    # widget the station's app with a phone behind it; off keeps the phone
    # first. Audio still waits for the browser's one allowed tap either way.
    # Was a boolean until the operator asked for a dropdown; "call" is the
    # old False and "player" the old True, so a stored `true` still means the
    # player and nothing needs migrating.
    "start_on_player":    (None, "call"),
    # The player under the answering machine: instead of dying when the
    # studio takes the line, the music ducks to this percentage — the same
    # move tune-in makes under a call, at the same default. 0 keeps it
    # silent but running.
    "vm_player_duck":     (None, 10),
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
    # On for the page since 0.10.80: with the line defaulting to admin-only,
    # the sign-in chip is how the operator's own browser gets through — a
    # fresh install without it showed a door with no keyhole. Embeds stay
    # bare; a host page's visitors are strangers.
    "show_signin":            (None, True),
    "embed_signin":           (None, False),
    # An embed sits flush in whatever area its host gives it — no border, no
    # sheet of its own — unless the operator ticks the outline back on. The
    # main page always keeps its card; only the frame is asked.
    "embed_card_outline":     (None, False),
    # Which FOREIGN pages may frame the widget and spend the operator's budget
    # — mint call tokens, open the text line. Empty is same-origin only, the
    # right answer until the first embed. A panel setting since 0.10.63: the
    # allowlist lives beside the snippet it exists for, so allowing the site
    # you just built a snippet for is a save, not a container recreate.
    "allowed_origins":        ("CALLIN_ALLOWED_ORIGINS", ""),
    # How each door reads: its WORD, its ICON, or both — one answer per
    # feature, not one for the whole row. An operator wanted Call worded and
    # the two secondary doors as bare icons on a tight embed, which the old
    # single button_style could not express. Words on by default so an
    # existing card looks exactly as it did; the operator opts an icon in.
    # The widget shows the word if a feature ends up with neither ticked, so
    # a door is never blank.
    # The 0.10.80 default reading: Call keeps its word (it is the card's one
    # promise), the two secondary doors sit beside it as drawn icons — three
    # worded buttons in a row read as a form, not a phone.
    "call_show_words":        (None, True),
    "call_show_emoji":        (None, False),
    "vm_show_words":          (None, False),
    "vm_show_emoji":          (None, True),
    "chat_show_words":        (None, False),
    "chat_show_emoji":        (None, True),
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
    # The four the card said in its own words with no way to change them
    # (operator, 2026-08-12): the text line's send button, the line-dropped
    # note, the connecting status, and what the transcript area says while
    # the caller waits for the DJ's first word.
    "word_send":             (None, ""),
    "word_ended":            (None, ""),
    "word_connecting":       (None, ""),
    "word_waiting":          (None, ""),
    # Whether the transcript labels the DJ's lines "DJ" or with their name.
    "transcript_dj_name":    (None, False),
    "call_button_label":     (None, ""),

    # After the line drops, ask the caller whether that went well. Two
    # buttons, stored against the call's own record, so a bad call can be
    # found and read rather than remembered. On by default since 0.10.80
    # (the operator's fresh-install review): the answers feed the dashboard's
    # ratings filter and the calls viewer, and a deployment that never
    # collects them cannot tell a good night from a bad one.
    "ask_call_feedback": (None, True),
    # Per-door, not one switch: the operator asked to decide separately for
    # the text line and the machine — a thumbs prompt that reads fine after a
    # live call can read as fishing after a voicemail, and that is their call
    # to make, not ours.
    "ask_chat_feedback": (None, True),
    "ask_vm_feedback": (None, True),

    # After the call, hand a short line back to the on-air DJ so the station
    # reflects that the call happened ("just had someone on about ..."). Kept
    # deliberately brief — a passing mention, not a recap. Off by default
    # (0.10.80): it writes to the broadcast and needs station credentials, so
    # it should be chosen, not inherited.
    "callback_enabled":      (None, False),
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
    # Booth texture while the DJ is thinking mid-call: a file path visible
    # to the WORKER (e.g. /data/sounds/thinking.mp3), played on its own
    # room track only while the agent is between hearing and speaking.
    # Blank = silence, today's behaviour. Panel row since 0.99.15 (sounds
    # section, plain row — deliberately not a seventh board slot); the
    # slot-menu upload treatment stays the follow-up if the experiment
    # earns it.
    "sound_thinking":   (None, ""),
    "call_volume":      (None, 100),
}


def voicemail_policy(cfg: dict) -> str:
    """The machine's effective policy: 'never' unless the master switch is
    on, else the stored when. One resolver, because two call sites deciding
    "is voicemail on" independently is how they drift."""
    if not cfg.get("voicemail_enabled"):
        return "never"
    return str(cfg.get("voicemail_when") or "closed")


def opens_on_player(cfg: dict) -> bool:
    """Does the card open onto the station player rather than the phone?

    `start_on_player` was a tick and is a dropdown ("call" | "player"). The
    key kept its name and its truthiness, so a stored `true` from before the
    change is neither None nor "call" and still means the player — which is
    why there is no migration. One reader, so the card and any future caller
    cannot disagree about what an old settings.json meant.
    """
    return cfg.get("start_on_player") not in (None, False, "", "call")


# The panel's Station tools reference comes from the tool registry — the same
# table the worker derives its allowlists from, so the two cannot disagree
# about what a caller can reach.
def mcp_tools_payload() -> list[dict]:
    from call.tools.registry import catalogue

    return catalogue()


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
        # The pages the schema does not own, and which end of the strip they
        # stand at.
        "navExtraPages": [{"id": i, "title": t, "where": w}
                          for i, t, w in NAV_EXTRA_PAGES],
        "groups": [
            {"id": g, "super": sup, "title": t, "blurb": b,
             # Search-only synonyms. Never rendered — the finder reads them
             # so a section can be found by what it is, not only by what it
             # is called.
             "alias": GROUP_ALIASES.get(g, "")}
            for g, sup, t, b in GROUPS
        ],
        "mcpTools": mcp_tools_payload(),
        "fields": {
            name: {
                "group": meta["group"],
                "kind": meta["kind"],
                "label": meta["label"],
                "help": meta.get("help", ""),
                # Worn after the field as a mono microcap ("60 MIN"), the
                # ledger's grammar — labels stopped carrying "(s)" at 0.98.58.
                "unit": meta.get("unit", ""),
                "placeholder": meta.get("placeholder", ""),
                # Words an operator might type for this setting that appear
                # in neither its label nor its help. Search-only; nothing
                # renders them.
                "alias": meta.get("alias", ""),
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


def tts_base_urls() -> dict:
    return {
        "cloud": "https://api.openai.com",
        "local": _sidecar_default("LOCAL_TTS_URL", 8001),
    }


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
    _lay_data_skeleton(data_dir)
    _warn_commented_env()
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


def _warn_commented_env() -> None:
    """Name env values that look like they swallowed an inline comment.

    docker compose's env_file format has no inline comments: everything after
    `=` is the value, `#` included. A real container came up with
    CALLIN_INTERNAL_URL holding half a sentence of English and nothing
    complained anywhere (0.10.82). .env.example keeps comments on their own
    lines now, but every .env copied before that carries the mines — this
    names them at boot instead of leaving each one to be found by symptom.

    The tell is whitespace-then-# inside the value ("value  # note"), not a
    bare # — a password may legitimately contain one.
    """
    import secrets_store

    names: set[str] = set()
    for env_var, _default in FIELDS.values():
        if isinstance(env_var, str) and env_var:
            names.add(env_var)
        elif isinstance(env_var, tuple):
            names.update(v for v in env_var if v)
    names.update(v for v in secrets_store.SECRET_FIELDS.values() if v)
    names.update(n for n in os.environ
                 if n.startswith(("CALLIN_", "LIVEKIT_", "SUBWAVE_")))
    suspect = sorted(
        n for n in names
        if re.search(r"\s#", os.environ.get(n, "")) or
        os.environ.get(n, "").lstrip().startswith("#")
    )
    if suspect:
        log.error(
            "these environment values appear to contain an inline comment — "
            "compose's env_file format keeps everything after '=' as the "
            "VALUE, '#' included: %s. Move the comment onto its own line in "
            ".env and recreate the containers.", ", ".join(suspect),
        )


def _lay_data_skeleton(data_dir) -> None:
    """One boot makes `ls data/` show the real shape.

    Only the DIRECTORIES — calls, sounds, voicemail — so the operator sees the
    structure on day one instead of folders appearing weeks apart as features
    first fire. The JSON stores stay lazy on purpose: their absence IS a state
    the app reads (no admin-auth.json means "no password yet" and drives the
    first-run banner; deleting it stays the documented reset), so each file
    appears the moment it first has something true to say. Failure is
    tolerable here — if the mount is unwritable, the ownership check below is
    the loud diagnosis, not this.
    """
    if not data_dir.exists():
        return                      # nothing mounted; nothing to lay out
    for name in ("calls", "sounds", "voicemail"):
        try:
            (data_dir / name).mkdir(exist_ok=True)
        except Exception:                                     # noqa: BLE001
            return


# Settings that were replaced rather than removed, and how to read an old file
# as though it had always been written the new way.
#
# The rule is that an upgrade may never change what a caller experiences. A
# renamed field with no migration would silently revert whatever the operator
# had chosen back to the built-in default, which is the same class of failure
# as a setting that does nothing — you find out from a caller.
# The settings store's generation. Bumped when a DEFAULT changes in a way an
# existing deployment must be insulated from (see the gated blocks in
# _migrate — each compares against its own literal generation, because a
# store stamped at rev 2 must not receive rev 2's stamps again when rev 3
# lands); save() marks every store it writes with THIS ceiling, which is
# what tells a store that merely never set a field apart from one written
# before the field's default moved.
STORE_REV = 6


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
    # 0.10.80: the longest-chat ceiling moved from hours to minutes. A stored
    # hours value keeps its real duration; a store that never set it falls to
    # the new default like everything else.
    if "chat_max_minutes" not in stored and "chat_max_hours" in stored:
        hours = _coerce(stored.get("chat_max_hours"), 0)
        if hours:
            stored["chat_max_minutes"] = int(hours) * 60
    # 0.10.80: fresh installs default to a closed, admin-only line with
    # tiered permission grants. An EXISTING deployment must keep exactly the
    # behaviour it had (the 0.9.61 rule), so the old defaults are stamped in
    # for every changed field a pre-0.10.80 store does not answer for itself
    # — a door must not close, and a power must not be handed out, because of
    # an upgrade. Gated on the store's generation marker, not on the file
    # existing: a store CREATED after this change also lacks these keys, and
    # stamping that one would hand a fresh install the old defaults the
    # moment it saved anything. save() writes the marker on every write, so
    # only stores that really predate 0.10.80 are ever stamped.
    if _coerce(stored.get("_rev"), 1) < 2:
        if "front_access" not in stored:
            stored["front_access"] = "auto"
        for field in ("allow_announcements", "allow_skills", "allow_exact_queue",
                      "allow_favorite", "allow_unfavorite", "allow_skip_track",
                      "allow_dj_segment", "allow_takeover"):
            if field not in stored:
                stored[field] = TIER_OFF
    # 0.10.85: tts_mode's default became blank (pick a backend), the same
    # move llm_provider made at 0.10.80 — and the same insulation: a store
    # from before this was running the cloud shape, and keeps it.
    if _coerce(stored.get("_rev"), 1) < 3:
        if "tts_mode" not in stored:
            stored["tts_mode"] = "cloud"
    # 0.10.104: the discovery tools arrive. The two READS (sound search and
    # its neighbours tool, browse rides the existing library-search switch)
    # follow the new default even on an old store — they change nothing, they
    # only stop the DJ guessing, and withholding them is what the bad calls
    # were made of. Cancelling a queued track is a WRITE that can pull a
    # record somebody else asked for, so it is stamped off: the 0.9.61 rule
    # is about powers, not about answers.
    if _coerce(stored.get("_rev"), 1) < 4:
        if "allow_cancel_queue" not in stored:
            stored["allow_cancel_queue"] = TIER_OFF
    # 0.10.132: the genre lock and the never-play ban. Both are POWERS, and the
    # 0.9.61 rule says a power is never handed out by an upgrade — an operator
    # who has never seen these settings must not find that tonight's caller can
    # narrow the station's playlist or ban a record from it permanently. Stamped
    # off; the panel is where they get turned on, deliberately.
    if _coerce(stored.get("_rev"), 1) < 5:
        for field in ("allow_genre_lock", "allow_never_play"):
            if field not in stored:
                stored[field] = TIER_OFF
    # 0.98.10: bulk queueing (albums and mixes). A POWER — one sentence can
    # fill an hour of the shared queue — so the 0.9.61 rule applies: stamped
    # off for every store that predates it, granted only from the panel.
    if _coerce(stored.get("_rev"), 1) < 6:
        if "allow_album_queue" not in stored:
            stored["allow_album_queue"] = TIER_OFF
    # 0.10.92: receipt placement stopped being chat-only — action_cards now
    # covers calls, texts and voicemail. A stored chat-era answer becomes the
    # operator's answer for every door; a store that never set it follows the
    # new default, which moves CALL cards behind the DJ's line (the operator's
    # ask, and the point of the change).
    if "action_cards" not in stored and "chat_action_cards" in stored:
        stored["action_cards"] = stored["chat_action_cards"]
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


def beneath() -> dict:
    """What every field would resolve to if the operator cleared it — env over
    defaults, with the stored layer removed.

    The panel needs this to describe its own blank option honestly. It was
    labelling it "Default — <the resolved value>", and the resolved value
    INCLUDES the operator's own choice: having picked Google, the blank read
    "Default — google", which says a default exists where none does. On a
    fresh install the same option correctly read "Not set — pick a provider",
    so the label was right exactly when nobody needed it (operator-reported,
    2026-08-16). Clearing a field means "fall through to the layer below", and
    this is that layer.
    """
    out: dict[str, Any] = {}
    for field, (env_var, default) in FIELDS.items():
        env_value = None
        for name in (env_var,) if isinstance(env_var, str) else (env_var or ()):
            candidate = os.environ.get(name)
            if candidate not in (None, ""):
                env_value = candidate
                break
        out[field] = _coerce(
            env_value if env_value not in (None, "") else default, default)
    return out


# Fields that must be a URL or nothing. A real deployment had "Gordon" in
# station_mcp_url — a browser autofilling a name into a text box — which meant
# the agent got NO station tools on any call and invented library results
# instead. The field accepted it silently and nothing downstream complained.
URL_FIELDS = ("station_base_url", "station_mcp_url", "llm_base_url",
              "tts_base_url", "vm_air_base_url")

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
        # Every write stamps the store's generation, so _migrate can tell a
        # store that never set a field from one written before that field's
        # default moved (the 0.10.80 stamps read this).
        current["_rev"] = STORE_REV

        # 0644 (the write_atomic default): config, safe to copy or diff,
        # unlike secrets/admin-auth. On a Synology share a new file arrives
        # mode 000, so this SETS the mode rather than merely tightening it.
        write_atomic(SETTINGS_PATH, current, indent=2, sort_keys=True)

    log.info("settings updated: %s", ", ".join(sorted(patch)) or "(none)")
    return load()
