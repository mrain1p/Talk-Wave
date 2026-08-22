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

# The one number both ends of the on-air duck are sized from. Imported rather
# than repeated: call/air.py owns the timing and this owns the default, and a
# 5 here against a 4.5 there is how the two ends drifted apart in the first
# place. Guarded because settings.py must import on a box with no LiveKit —
# call.air pulls the agents SDK in at module scope.
try:
    from call.air import DUCK_PAD_SECS as _DUCK_PAD
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
    "chat_reprompt_secs":    (None, 20),
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
    # The card's own listener count and track heart. ON by default, unlike
    # the player: they are one line of text and one small button on furniture
    # the card already has, not a new surface — and both degrade to nothing
    # when the station won't say (no count) or the track line is empty (no
    # heart).
    "show_listener_count": (None, True),
    "show_track_like":    (None, True),
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
    "allow_on_air",
    "allow_requests",
    "allow_library_search",
    "allow_sound_search",
    "allow_exact_queue",
    "allow_album_queue",
    "allow_cancel_queue",
    "allow_favorite",
    "allow_unfavorite",
    "allow_announcements",
    "allow_skills",
    "allow_skip_track",
    "allow_dj_segment",
    "allow_takeover",
    "allow_genre_lock",
    "allow_never_play",
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

    `callin-<o|g|a>-<12 hex>`, with an optional `l` behind the tier letter
    (`callin-gl-…`) marking an on-air call — see on_air_from_room. Anything
    else — a probe room, a room minted by a version of the token server that
    predates this, a name from somewhere else entirely — comes back as the
    LEAST trusted tier. Failing closed is the only safe direction: the
    alternative is an unrecognised name handing a stranger the operator's own
    permissions.
    """
    parts = str(room_name or "").split("-")
    if len(parts) >= 3 and parts[0] == "callin":
        for tier in TIERS:
            if parts[1] in (tier[0], tier[0] + "l"):
                return tier
    return "open"


def on_air_from_room(room_name: str) -> bool:
    """Whether this call was minted as a live-on-air call.

    Rides the room NAME for the same two reasons the tier does: the name is
    inside the signed grant, so a caller cannot put themselves on air without
    a token nobody minted them, and the worker knows it the instant the job
    starts. The flag is one letter behind the tier so tier_from_room's exact
    matching still fails closed on anything unrecognised.
    """
    parts = str(room_name or "").split("-")
    return (len(parts) >= 3 and parts[0] == "callin"
            and len(parts[1]) == 2 and parts[1][1] == "l"
            and parts[1][0] in {t[0] for t in TIERS})


def opens_on_player(cfg: dict) -> bool:
    """Does the card open onto the station player rather than the phone?

    `start_on_player` was a tick and is a dropdown ("call" | "player"). The
    key kept its name and its truthiness, so a stored `true` from before the
    change is neither None nor "call" and still means the player — which is
    why there is no migration. One reader, so the card and any future caller
    cannot disagree about what an old settings.json meant.
    """
    return cfg.get("start_on_player") not in (None, False, "", "call")


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

# Super-groups, in display order. Since 0.10.62 each one is a PAGE of the
# panel — the widget shows one at a time behind /settings#<id> — and the page
# picker, the ordering and the wording all come from this table; the markup
# does not keep its own copy.
#
# The cut is by door where a door owns the answer, and universal where every
# door shares it (the operator's ask: "one for calls, one for voicemails, one
# for texts"). Calls, Voicemail and Texts each get their own page; the DJ's
# knowledge and house style stay on one shared page because the same brain
# answers all three doors — filing them under Calls would be a lie the moment
# a text chat used them, and a field cannot appear on two pages (one id, and
# byKind fills the first match it finds).
# The pages the schema does not own — panel.js builds both — listed here so
# the picker's whole order is readable in one place. `lead` stands before the
# super-groups in the strip, `tail` after them.
#
# An "All settings" index page stood here for one release and came out on the
# operator's call: every setting in one table with the page and section
# holding it. The finder already answers "where is the thing I can name", and
# a second, longer way to ask the same question was one more chip on the only
# map the panel has.
#
# The picker was BANDED during the 0.98.22 work and is flat again on the
# operator's call, before any of it shipped. Five labelled rows grouping the
# eleven pages by kind read as more furniture than map. The measurement that
# prompted it still stands — a 375px phone shows two of eleven chips, because
# the strip needs 1017px in 343px of room — so if the phone case is worth
# solving later, solve it without regrouping the pages.
NAV_EXTRA_PAGES = [
    ("dash", "Dashboard",   "lead"),
    ("diag", "Diagnostics", "tail"),
]

# (id, title, blurb).
SUPERGROUPS = [
    ("config",    "Configuration",        "The station, the keys, and what listens, thinks and speaks."),
    ("safety",    "Permissions & safety", "What a caller may set in motion, and the limits around it."),
    # Beside the permissions it reads back, rather than after Players. Its
    # blurb has always said it is driven by them, and the picker bands it
    # under Set up — two orders that disagree is one order nobody trusts.
    ("ref",       "Reference",            "What a caller may ask for, and what the station publishes."),
    # "The booth" at 0.98.22, closing the ambiguity the 2026-08-13 note left
    # open. That note said the word meant two things on one panel — this page
    # and the dashboard's switch cluster — and that if it ever read
    # ambiguously the CLUSTER was the one to rename. On review the cluster is
    # the honest one: it really is about transmission, three switches that
    # open and close the line. This page is not. It holds what the DJ knows,
    # how it speaks and what it writes down, which is the booth — the name
    # docs/settings.md has used for it all along, so the rename also ends a
    # disagreement between the code and its own documentation. The id stays
    # "dj": it is a hash address (/settings#dj), and changing it would break
    # bookmarks for a title change.
    ("dj",        "The booth",            "What the booth knows, how it speaks, and what it writes down."),
    ("calls",     "Calls",                "The live line — how a call opens, sounds and ends."),
    ("voicemail", "Voicemail",            "The machine — what it says, and where messages go."),
    ("texts",     "Texts",                "Typed chat with the booth — same brain, no microphone."),
    # The on-air feature's own page (operator's ask, 2026-08-18). The two
    # quick kills were dashboard-only controls with no settings row anywhere,
    # the ducking pair sat under Calls, and the soundbite's airing backend
    # under Voicemail — so "may a caller reach the broadcast" was answered
    # across three pages. The tier row and its dials deliberately STAY
    # under Caller permissions with every other permission; the panel greys
    # them while both doors here are shut. "When the call airs" moved the
    # other way (operator's ask, same day): it says how the broadcast is
    # delivered, not what a caller may do. The id is "air", not "onair" —
    # that string is already the ducking section's group id, and one word
    # holding two addresses is the Transmission lesson again.
    ("air",       "On air",               "The broadcast door — what goes out live, and on whose say-so."),
    # Its own page rather than a section under On air (operator's call). On
    # air answers "may a caller reach the broadcast"; this answers "what is
    # the station asking them", which is a different question with its own
    # settings, its own state and its own two buttons. Filing it under one
    # of the three doors would also have been wrong in the other direction:
    # a subject put up on air is answerable on all three.
    # Page id and section group id are both "openlines", which Voicemail
    # already does — the pair that must not collide is a group id against
    # ANOTHER group's page, the way "onair" would have.
    # Named "Segments" on the picker and placed after Players (operator, 
    # 2026-08-22): the tab is a place, and the place is the last thing you
    # set up before Diagnostics. The section heading inside it stays "Open
    # Lines Segment", which is the thing rather than the place.
    ("card",      "Players",              "What a caller sees — here, and on somebody else's page."),
    ("openlines", "Segments",             "The topics the station puts to its listeners."),
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
    # Beside the usage caps rather than on the Calls page — the hard
    # per-call ceiling is one more spend limit, and the operator went looking
    # for it here. The rest of what "Call length" used to hold (sign-off,
    # check-ins, the earliest hang-up) is conversation behaviour and lives in
    # Closing the call below.
    ("speech",   "safety", "Speech hygiene", "What never reaches the speaker."),

    # The booth page holds what every door shares. The same brain answers a
    # live call, writes the machine's fresh greetings and runs the text line,
    # so its knowledge and house style filed under any one door would go
    # stale the moment another door used them. Transcripts sit here too
    # (operator's call, 0.10.64): the records cover calls, chats and
    # voicemails alike, so "what the booth writes down" is the honest home.
    ("context",  "dj",     "Station awareness",  "What the DJ knows before picking up."),
    ("style",    "dj",     "House style",        "Light steers on top of the persona."),
    ("record",   "dj",     "Transcripts",   "Calls, texts and voicemails — what is written to disk, and for how long."),

    # Calls: the door and its ceilings, then the order a call has a shape in
    # — open, turn-take, close — then everything that runs around the
    # speaking.
    #
    # "Call limits" was "Usage controls" under Permissions & safety until
    # 0.98.22. The same concept was filed in three different places depending
    # on which door it belonged to: six chat caps on the Texts page,
    # voicemail's one ceiling on Voicemail, and the five call caps two pages
    # away under safety. By this file's own rule — cut by door where a door
    # owns the answer — the call caps were the odd ones out, and an operator
    # looking for "how many calls an hour" was looking at the Calls page.
    # Moving them also leaves Permissions & safety as Access + Caller
    # permissions + Speech hygiene, which is a page about one thing rather
    # than a page with a limits section bolted on.
    ("usage",    "calls",  "Call limits",        "Generous ceilings that stop runaway use."),
    ("call",     "calls",  "Greeting",           "Which DJ picks up, and how the call opens."),
    # Greeting's mirror: how a call ends, in character — the sign-off steer,
    # the idle check-ins, and how early the DJ may hang up were scattered
    # across House style and Call length, and the operator asked where the
    # closing settings were. A fair question deserves a section.
    ("turns",    "calls",  "Turn-taking",        "When the DJ decides you've finished."),
    ("closing",  "calls",  "Closing the call",   "How a call ends, in character."),
    ("tunein",   "calls",  "Station audio in the call",
     "Whether the caller counts as a listener, and whether they hear the broadcast."),
    ("callback", "calls",  "Back-to-air commentary", "One line after the call — nothing more."),
    ("sounds",   "calls",  "Call sounds",         "Ring, pickup and hang-up."),
    # Moved out of Voice: the effect shapes the CALL's sound, not the TTS
    # backend, and the operator kept looking for it here.
    ("effects",  "calls",  "Voice effects",       "A radio colour on the DJ's voice."),

    # Each door the booth doesn't answer live gets its own page, named for
    # the door — the operator's cut. The section was called "The machine" to
    # avoid a Voicemail section under a Voicemail page reading as a stutter;
    # at 0.98.22 it takes its noun back. A folded section shows its NAME and
    # nothing else, and a name that only decodes once you know where things
    # are is the wrong way round — the reader who needs the map most is the
    # one who cannot read it. The page is no longer a wrapper to stutter
    # against either: a one-section page now renders as the section itself.
    ("voicemail", "voicemail", "Voicemail machine", "When the booth can't pick up, the machine does."),
    ("chat",      "texts",  "Text line",          "Typed chat with whoever is on air — same brain, no microphone."),

    # The On air page: the phone-in's reach for the broadcast, gathered from
    # the three pages it was scattered across. Ducking has moved twice now —
    # out of Caller permissions (it read as a fourth station-wide permission,
    # and it is not a permission at all), then out of Calls: it is about the
    # one broadcast voice, not the call, so it lives beside the doors.
    ("airdoors", "air",    "Doors to air",        "The phone-in's two doors — close one without touching the other."),
    ("onair",    "air",    "On-air ducking",      "The call DJ and the on-air DJ are one voice."),
    # Open Lines sits on the On air page because that is what it is: the
    # booth reaching OUT to the broadcast, rather than a door reaching in.
    # It spans all three doors — a topic put up on air is answerable on the
    # phone, on the text line and on the machine — so filing it under any one
    # of them would have been the wrong cut.
    ("openlines", "openlines", "Open Lines Segment",
     "The DJ puts a topic up, and invites the audience in."),

    # The Players page reorganized to the operator's design handoff ("Players
    # Settings Reorganization", direction 1a): one block per card ELEMENT, in
    # card order top to bottom, so everything about a thing is together —
    # the old page gave one button four homes (shows / word-icon / order /
    # wording) and let the live preview scroll away. The preview is no
    # longer a section at all: it is the pinned column beside these, static
    # markup in panel.html. The first six groups are the THE CARD tab, the
    # next two BEHAVIOUR, the last EMBED — the tab map itself is CARD_TABS
    # in panel.js, because the schema owns order, not screen furniture.
    ("topcorner", "card",  "Top corner",          "The small controls on the card's top edge."),
    ("whosonair", "card",  "Who's on air",        "Photo, show, tagline, and the record playing."),
    # Every fixed call-state string, overridable — so a station whose whole
    # page speaks in its own voice doesn't get "Ringing…" in ours. Named for
    # what it holds rather than for the part of the card it paints (0.98.22):
    # nobody looking for the word "Ringing…" was going to guess "The line box".
    ("linebox",   "card",  "Call status wording", "What the card says in every state of a call."),
    ("talkbar",   "card",  "The talk bar",        "The caller's microphone control."),
    ("buttons",   "card",  "The buttons",         "The three doors — order, labels, word and icon."),
    ("surface",   "card",  "Card colours",        "Colours and skin for the whole card."),
    ("phone",     "card",  "On the caller's phone", "Nothing visual — how a call behaves in the hand."),
    ("feedback",  "card",  "After the conversation", "Whether the card asks how it went."),
    ("embed",     "card",  "Embed frame",         "The embed's options, and the snippet to paste."),

    ("ask",      "ref",    "What callers can ask", "Driven by the permissions above."),
    ("tools",    "ref",    "Station tools",        "Every tool the station publishes, and who can reach it."),
]

# Words an operator types into the finder that appear nowhere in a section's
# name or blurb. The panel searches these alongside the visible text, so a
# section can be found by what it IS as well as by what it is called — the
# whole point of the 0.98.22 finder pass, where "password" hid the section
# holding the password button and "color" found nothing at all.
#
# Per-FIELD synonyms live on the field, as `alias=` in SCHEMA. These are only
# for the section itself.
GROUP_ALIASES = {
    "security":  "password login sign-in admin key guest code lock",
    "perms":     "permissions allow abilities tools what callers can do",
    "usage":     "usage controls limits caps rate limit throttle spend budget",
    "speech":    "profanity swearing language filter censor",
    "record":    "logs history archive retention",
    "brains":    "llm ai model api key openai anthropic google",
    "voice":     "tts speech synthesis api key",
    "ears":      "stt transcription whisper deepgram api key",
    "sounds":    "ringtone audio beep",
    "surface":   "color palette theme skin",
    "phone":     "mobile handset",
    "embed":     "iframe snippet cors origins",
    "effects":   "color radio filter",
    "context":   "knowledge memory what the dj knows",
    "style":     "persona tone personality prompt",
    "turns":     "silence pause endpointing barge-in",
    "closing":   "hang up goodbye timeout duration",
    "tunein":    "stream listener broadcast audio",
    "onair":     "ducking mute volume",
    "airdoors":  "broadcast live go live",
    "voicemail": "answering machine messages",
    "chat":      "text sms typing messages",
    "linebox":   "wording strings labels copy",
    "topcorner": "header controls",
    "whosonair": "avatar photo show tagline now playing",
    "feedback":  "rating thumbs survey",
    "station":   "subwave api url endpoint",
}

SCHEMA: dict[str, dict] = {
    # --- station ---
    "station_base_url": dict(group="station", kind="text", label="SUB/WAVE station API",
        placeholder="default: SUBWAVE_BASE_URL from the environment",
        help="Personas, cards, voices and tools are all discovered from here. "
             "Point it at a different SUB/WAVE to re-home the whole sidecar."),
    # station_mcp_url deliberately has no schema entry (0.10.80, operator's
    # call): it is always derived as {station API}/mcp in practice, and a
    # panel row for a value nobody sets is one more box to mistrust — a real
    # deployment once had a browser autofill a NAME into it and every call
    # lost its tools. SUBWAVE_MCP_URL in the environment remains the escape
    # hatch for a station that publishes MCP somewhere unusual.

    # --- brains ---
    "llm_provider": dict(group="brains", kind="select", label="Provider", alias="api key llm ai",
        help="Nothing is picked until you pick it — the DJ has no model to "
             "think with until then, and the dashboard says so. Only providers "
             "you have a key for are listed — add one below and it appears "
             "here. Ollama runs on your own network and needs none."),
    "llm_model": dict(group="brains", kind="select", label="Model",
        help="Read live from the provider. Over ~1.5s to first token, the "
             "caller hears a pause before every reply; a self-hosted model "
             "that needs more than 30s cannot carry a call at all. Test it — "
             "the check measures with a real call's prompt and tools."),
    "llm_base_url": dict(group="brains", kind="text", label="Endpoint",
        needs=("llm_provider", ("ollama", "openai", "openrouter",
                                "deepseek", "requesty", "gateway",
                                "openai-compatible", "locca")),
        placeholder="default: the provider's own address",
        help="Only for a self-hosted or gateway endpoint. Required for "
             "'OpenAI-compatible' — it is the address of your own server. "
             "locca falls back to its usual host address when left blank. "
             "With one set, the Model list is read from it (hit “Test keys "
             "+ reload models”) — servers like llama-swap only route model "
             "names they declare."),
    "llm_temperature": dict(group="brains", kind="number", label="Temperature (0–2)",
        help="0.8 suits a DJ. Below 0.5 sounds clipped."),

    # --- ears ---
    "stt_provider": dict(group="ears", kind="select", label="Provider", alias="api key stt transcription",
        help="Built-in needs nothing and pays in CPU; a cloud ear needs a "
             "key and hears better — the notes above spell out the trade."),
    "stt_model": dict(group="ears", kind="select", label="Model",
        help="Smallest to largest — base.en is the default because it is "
             "light, not because it hears best. The ladder under the field "
             "spells out each model's trade."),

    # --- voice ---
    "tts_mode": dict(group="voice", kind="select", label="Backend", alias="api key tts speech",
        help="Nothing is picked until you pick it — the DJ has no voice to "
             "speak with until then, and the dashboard says so. 'local' points "
             "at your own OpenAI-compatible speech server — whatever you run — "
             "and can use the station's persona voices, but a small GPU may "
             "generate slower than realtime (Test voice measures it). 'cloud' "
             "is fast but won't match the on-air timbre."),
    "tts_base_url": dict(group="voice", kind="text", label="Endpoint",
        placeholder="default: TTS_BASE_URL from the environment",
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
    "allow_sound_search": dict(group="perms", kind="select", tiered=True, label="Find music by how it sounds",
        admin=True,
        help="Two reads that match the ANALYSED AUDIO rather than titles: a"
             " 'sounds like' search over a description, and 'more like this' off"
             " the track on air. They answer what a name search cannot. Needs the"
             " station's analyzer; without it the DJ says it can't, rather than"
             " reporting an empty library. Queues nothing and costs no action."),
    "allow_exact_queue": dict(group="perms", kind="select", tiered=True, label="Queue the exact track picked",
        admin=True,
        needs=("allow_library_search", TIERS),
        help="Queues the recording the caller chose out of the search results, "
             "rather than re-matching the words. Skips the station's request rate "
             "limit, so Actions per call is the only thing pacing it."),
    "allow_album_queue": dict(group="perms", kind="select", tiered=True, label="Queue albums and mixes",
        admin=True,
        needs=("allow_library_search", TIERS),
        help="Bulk queueing: a whole album, or a run of picks as one batch. The"
             " caps are this line's own — 30 tracks an album, 8 a mix — and each"
             " batch counts once against Actions per call. The DJ only queues an"
             " album when the caller clearly wants the lot; it never offers one"
             " unprompted."),
    "allow_cancel_queue": dict(group="perms", kind="select", tiered=True, label="Take a track back out of the queue",
        admin=True,
        help="Lets a caller undo a request before it airs; the station refuses"
             " once the track is playing or cued next, and the DJ says so. Off by"
             " default because the queue is shared — this can pull a record"
             " somebody else asked for. Also clears a whole run in one go,"
             " counting once against Actions per call."),
    "allow_favorite": dict(group="perms", kind="select", tiered=True, label="Like the track on air",
        help="Adds a like to the record playing now — the same heart a listener taps "
             "in the app, so it needs no station credentials and changes no one's "
             "audio. The station gates it on its own Likes toggle and rate-limits it "
             "per caller. Likes the CURRENT track only; there is no public un-like."),
    "allow_unfavorite": dict(group="perms", kind="select", tiered=True, label="Un-like the track on air",
        admin=True,
        help="Removes the OPERATOR's own heart from the current record (the admin "
             "likes system, not the public like above). Only means anything to a "
             "caller signed in as the operator, so keep it at the admin tier. Needs "
             "station admin credentials."),
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
    "allow_genre_lock": dict(group="perms", kind="select", tiered=True, admin=True,
        label="Lock the station to a genre",
        help="Holds the station to one genre or a few for a set window — 15 to"
             " 720 minutes, ending by itself. Uses the station's own genre-lock,"
             " so an older SUB/WAVE answers that it can't rather than failing."
             " Quieter than a takeover, which is the risk: a pinned show"
             " announces itself on air, a narrowed playlist does not."),
    "allow_on_air": dict(group="perms", kind="select", tiered=True, admin=True,
        label="Go live on the station",
        help="The phone-in: the caller's conversation airs while it happens, one"
             " finished turn at a time, about an exchange behind the room. The"
             " card grows a Live-on-air toggle when a caller may choose it. Needs"
             " the mixer's telnet door — without it the call quietly stays"
             " private, and the transcript says why."),
    # The window, the delay and the caller's sound live on the On air page
    # with the other airing choices (operator's ask, 2026-08-19, the same
    # move "When the call airs" made the day before): they describe how the
    # broadcast is delivered, not what a caller may do. The tier row alone
    # stays under Caller permissions.
    "on_air_max_seconds": dict(group="airdoors", kind="number", admin=True,
        label="On-air window (s)", alias="duration length",
        needs=("allow_on_air", TIERS),
        help="How long one caller may hold the broadcast before the relay signs "
             "them off air and the call carries on privately. The station's own "
             "segments queue behind a live call, so shorter is kinder to the "
             "programme. Blank = 240."),
    "on_air_delay_secs": dict(group="airdoors", kind="number", admin=True,
        label="On-air delay (s)",
        needs=("allow_on_air", TIERS),
        help="How long a finished turn is held before it airs: your take-back"
             " window, and roughly how far the broadcast runs behind the call."
             " PULL OFF AIR kills any turn still inside it. 2–30 seconds, blank ="
             " 6 — it cannot be 0, because a turn has to finish before the mixer"
             " can fetch it. A caller with the station in earshot hears"
             " themselves a stream-buffer later (~22s) whatever you set."),
    "on_air_caller_sound": dict(group="airdoors", kind="select", admin=True,
        label="Caller sound on air",
        needs=("allow_on_air", TIERS),
        help="How a caller's voice is dressed before it airs. Clean keeps their"
             " real voice, levelled and de-rumbled — the default, because the"
             " phone costume reads as bad audio on a modern stream rather than as"
             " a phone. Phone is the 300–3400 Hz radio-caller sound, for stations"
             " that want it on purpose. Applies to live phone-ins and studio"
             " soundbites alike."),
    # Lives on the On air page beside the other airing choices (operator's
    # ask, 2026-08-18) — it says how the broadcast is delivered, not what a
    # caller may do, so Caller permissions was the wrong shelf for it.
    "on_air_call_mode": dict(group="airdoors", kind="select", admin=True,
        label="When the call airs",
        needs=("allow_on_air", TIERS),
        help="Live airs each finished turn a few seconds behind the room. Live"
             " once heard holds the broadcast until the caller's first words, so"
             " a call where they never speak airs nothing — the start airs an"
             " exchange later, which is the price of that guarantee. After the"
             " call tapes the whole conversation and plays it on hang-up,"
             " killable entire until it does. The on-air window caps all three."),
    "allow_never_play": dict(group="perms", kind="select", tiered=True, admin=True,
        label="Ban a track for good", alias="block ban blocklist",
        help="Puts the track playing now on the station's never-play list: out"
             " of the queue, out of the fallback playlist, never selected again."
             " The only PERMANENT thing a caller can do, and nothing airs to say"
             " it happened. The same switch lets a caller LIFT a ban, including"
             " one you set."),

    # --- call length ---
    "max_call_seconds": dict(group="closing", kind="number", label="Hang up after (s)", alias="timeout duration length",
        help="Hard ceiling. The DJ signs off in character first rather than the "
             "audio just stopping. 600 = ten minutes."),
    "guest_session_hours": dict(group="security", kind="number",
        label="Guest code expires (hours)", alias="password code session expiry",
        help="Per device: each browser that typed the code runs its own "
             "clock. On a shared or public machine, a typed code should not "
             "outlive its typist — the card forgets it after this long and "
             "shows a lock button to forget it immediately. 0 remembers it "
             "until Sign out."),
    "front_access": dict(group="security", kind="select",
        label="Call-in access", alias="password login sign-in gate code",
        help="This is the PHONE — who may ring at all. What a caller may DO once"
             " through is separate and per-tier under [Caller"
             " permissions](#perms). The two ticks are independent: both, and"
             " strangers ring through while a code-holder becomes their own tier;"
             " Guest code alone closes the line to strangers; neither closes it"
             " to callers. The admin password opens everything regardless."),
    "guest_tier": dict(group="security", kind="check",
        label="Guest code tier", alias="password code",
        help="Whether a code you have set elevates the caller who types it. "
             "Off leaves the code stored but inert, which is how the guest "
             "pathway is switched off without deleting a code you want to "
             "keep."),
    # --- player settings: what the card shows, per surface ----------------
    # Every row here is asked twice, once for this page and once for an embed.
    # The panel lays them out as a two-column matrix, which is why the labels
    # are short: the column heading carries the surface, not the label.
    "show_caller_help": dict(group="topcorner", kind="check",
        label="“What can I ask?” button",
        help="Opens the same live reference this panel shows you, filtered to "
             "what is actually switched on. Most callers assume a phone-in only "
             "takes requests."),
    "embed_caller_help": dict(group="topcorner", kind="check",
        label="“What can I ask?” button (embed)",
        help="The same button, in a frame on somebody else's page."),
    "chat_idle_minutes": dict(group="chat", kind="number",
        label="Close after quiet (min)", alias="timeout",
        help="A chat with nothing said for this long is over: the record is "
             "written and the id stops resuming. The widget keeps its side, "
             "so a returning caller simply starts a fresh conversation."),
    "chat_max_messages": dict(group="chat", kind="number",
        label="Messages per chat", alias="rate limit cap",
        help="A ceiling on one conversation, not a rate: hitting it closes "
             "the chat politely. 0 = no ceiling."),
    "chat_max_minutes": dict(group="chat", kind="number",
        label="Longest chat (minutes)",
        help="However active, a chat this old is closed and written down — "
             "a visit, not a residency. Resumable is not immortal."),
    "max_open_chats": dict(group="chat", kind="number",
        label="Open chats at once", alias="rate limit throttle cap",
        help="Across all callers. Each open chat is a transcript in memory "
             "and a potential LLM spend; 0 = unlimited."),
    "chats_per_hour": dict(group="chat", kind="number",
        label="New chats per hour", alias="rate limit throttle cap",
        help="Fresh conversations opened per hour, all callers together. "
             "0 = unlimited. Resuming an existing chat is never counted."),
    "chats_per_day": dict(group="chat", kind="number",
        label="New chats per day", alias="rate limit throttle cap",
        help="The hard wallet ceiling on fresh chats, all callers together — "
             "the text line's equivalent of Calls per day. 0 = unlimited."),
    "chat_caller_cooldown_secs": dict(group="chat", kind="number",
        label="Reopen wait time (s)", alias="rate limit throttle spam",
        help="How long ONE caller must wait between opening chats — the "
             "per-visitor brake the phone has as Redial wait. A text line "
             "is scriptable in a way a call is not, so this singles out one "
             "abuser where the hourly and daily caps only stop a crowd. "
             "Resuming an open chat never waits."),
    "chat_msgs_per_minute": dict(group="chat", kind="number",
        label="Messages per minute", alias="rate limit throttle cap",
        help="Per chat. A human types a handful; a script does not. The "
             "excess is refused in-world, not queued."),
    "chat_greeting_mode": dict(group="chat", kind="select",
        label="Open with a greeting",
        help="Whether the booth speaks first when a caller opens a fresh text "
             "line — a silent line reads as broken. “Canned” sends the line "
             "below (instant, no model cost); “Written each time” has the DJ "
             "write one in persona at open; “Off” waits for the caller."),
    "chat_reveal": dict(group="chat", kind="select",
        label="Delivery",
        help="“As it's typed” reveals the DJ's words as they're written, which "
             "is what makes the line read as a person at a keyboard. “Typing "
             "cue, then the line” shows the three dots while the booth "
             "composes and lands the reply whole — quicker to read, and the "
             "better answer on a slow connection."),
    "chat_type_pace": dict(group="chat", kind="select",
        label="Typing pace",
        needs=("chat_reveal", "typing"),
        help="How fast those words appear. Normal is about a brisk human "
             "typist; the setting before this one ran at roughly 400 words a "
             "minute, which read as a machine. A long reply always lands "
             "within a few seconds whatever this says, so it can never crawl."),
    "chat_greeting": dict(group="chat", kind="text",
        label="Canned greeting",
        placeholder="You're through to the booth — what's on your mind?",
        help="The opening line for “Canned”. Blank uses a sensible default in "
             "the DJ's name. Takes {station}, {dj} and {show}, filled live."),
    "chat_reply_timeout_secs": dict(group="chat", kind="number",
        label="Reply timeout (s)", alias="timeout",
        help="How long the DJ may take to answer one message before the line "
             "gives up and says so, so a stalled model never leaves the caller "
             "watching a typing dot forever. 0 = wait indefinitely."),
    "chat_reprompt": dict(group="chat", kind="check",
        label="Nudge a quiet caller",
        help="Keeps a chat feeling like a conversation rather than a turn-based "
             "move: when the CALLER has gone quiet after the DJ's last message, "
             "the DJ sends ONE short in-persona line to keep it breathing — "
             "never \"are you still there?\", and never while the DJ is the one "
             "still owing a reply. On by default."),
    "chat_reprompt_secs": dict(group="chat", kind="number",
        label="…after how many seconds",
        needs=("chat_reprompt", True),
        help="How long a caller may be quiet, the ball in their court, before "
             "that one nudge. 15 is a natural pause; too short reads as pushy."),
    "show_theme_toggle": dict(group="topcorner", kind="check",
        label="Light / dark toggle",
        help="Forcing a theme below hides this either way — there is nothing "
             "to toggle between."),
    "embed_theme_toggle": dict(group="topcorner", kind="check",
        label="Light / dark toggle (embed)",
        help="Usually worth off: a caller flipping the card to light on a dark "
             "host page gets a bright rectangle in the middle of it."),
    # THE LINK OUT. Declared before its own visibility ticks so the panel
    # draws them under it, and every tick `needs` the switch above: an
    # operator cannot set where a button nobody can see would go, which is the
    # same shape the sound slots and the voicemail rows already use.
    "corner_link_enabled": dict(group="topcorner", kind="check",
        label="Link out of the card",
        help="One more button in the card's top corner, going wherever you "
             "send it — your station's own page by default. Off until you fill "
             "the address in below."),
    "corner_link_url": dict(group="topcorner", kind="text", label="Where it goes",
        needs=("corner_link_enabled", True),
        placeholder="default: your station's address",
        help="Left blank this follows the SUB/WAVE station this line answers "
             "for, so it keeps up if the station moves. Opens in a new tab, "
             "which for an embed means the host's page keeps its caller."),
    "corner_link_label": dict(group="topcorner", kind="text", label="What it says",
        needs=("corner_link_enabled", True),
        placeholder="The station",
        help="The tooltip, and what a screen reader announces. The button "
             "itself is the icon."),
    "corner_link_icon": dict(group="topcorner", kind="emoji", label="Icon", alias="logo emoji",
        needs=("corner_link_enabled", True),
        help="Pick one, or type any emoji. It sits beside the other corner "
             "controls, at their size and in their ink."),
    "show_corner_link": dict(group="topcorner", kind="check",
        label="Show it on this page",
        needs=("corner_link_enabled", True),
        help="The card at /. Both of these are greyed out until the link "
             "itself is switched on — there is nothing to show or hide yet."),
    "embed_corner_link": dict(group="topcorner", kind="check",
        label="Show it in an embed",
        needs=("corner_link_enabled", True),
        help="Worth keeping ON for an embed: a caller who found the card on "
             "somebody else's page has no other way back to you."),
    "show_settings_gear": dict(group="topcorner", kind="check",
        label="Settings gear",
        help="The way into this panel from the card. Off secures nothing — "
             "/settings still answers by URL and still asks for the password — "
             "just stops advertising it."),
    "show_chat_button": dict(group="buttons", kind="check",
        label="“Text the booth” button",
        help="A third way in, beside Call: typed conversation with the "
             "on-air DJ. Needs the text line switched on under Running "
             "the line."),
    "embed_chat_button": dict(group="buttons", kind="check",
        label="“Text the booth” button (embed)",
        help="The same door on the embedded card. Off by default: three "
             "buttons crowd a 190px frame."),
    # Per-feature button display: two ticks each (word, icon) for Call,
    # Leave-a-message and Text. At least one must be on for a door that is
    # offered — the widget falls back to the word if both are cleared, so a
    # blank button is impossible. Words edit under Wording; the icon is a
    # line drawing in the card's own ink, not an emoji glyph.
    "call_show_words": dict(group="buttons", kind="check",
        label="Call button — word",
        help="Show the Call button's WORDS (edit them under Wording)."),
    "call_show_emoji": dict(group="buttons", kind="check",
        label="Call button — icon", alias="icon logo",
        help="Show a phone icon on the Call button."),
    "vm_show_words": dict(group="buttons", kind="check",
        label="Leave-a-message button — word",
        help="Show the message button's WORDS (edit them under Wording)."),
    "vm_show_emoji": dict(group="buttons", kind="check",
        label="Leave-a-message button — icon", alias="icon logo",
        help="Show an envelope icon on the message button."),
    "chat_show_words": dict(group="buttons", kind="check",
        label="Text button — word",
        help="Show the Text button's WORDS (edit them under Wording)."),
    "chat_show_emoji": dict(group="buttons", kind="check",
        label="Text button — icon", alias="icon logo",
        help="Show a speech-bubble icon on the Text button."),
    "show_signin": dict(group="topcorner", kind="check",
        label="“Sign in” button",
        help="A corner button that lets a caller enter the guest code or the "
             "admin password to UNLOCK more of what they can ask for — the "
             "way to use per-tier permissions on a line anyone can reach. It "
             "shows only when a code is set and there is a higher tier to "
             "reach; it does nothing on a line where every permission is open "
             "to anyone. See [Caller permissions](#perms) to set the tiers."),
    "embed_signin": dict(group="topcorner", kind="check",
        label="“Sign in” button (embed)",
        help="The same corner button on the embedded card."),
    "embed_card_outline": dict(group="embed", kind="check",
        label="Draw the card outline", alias="iframe border",
        help="Off, the embed sits flush in whatever area the host page gives "
             "it — no border or sheet of its own, the page shows through. On, "
             "it carries the same outlined card as the main page."),
    "allowed_origins": dict(group="embed", kind="text", label="Allowed origins", alias="cors domain allowlist whitelist iframe",
        placeholder="default: no other site — the card works on this page only",
        help="Comma-separated https origins that may embed this card and place "
             "calls on your API keys (https://radio.example.com). The page you "
             "are reading this on needs no entry — add the site the snippet is "
             "pasted into. Applies to the next request, no restart. “*” lets "
             "every page on the internet spend your budget — dev only."),
    "show_voicemail_button": dict(group="buttons", kind="check",
        label="\u201cLeave a message\u201d button",
        help="A second button beside Call, so the machine is on offer even "
             "while the booth could pick up live. Voicemail itself has to be "
             "switched on on its own page."),
    "embed_voicemail_button": dict(group="buttons", kind="check",
        label="\u201cLeave a message\u201d button (embed)",
        help="The same second button, on the embedded card."),
    "show_push_to_talk": dict(group="talkbar", kind="check",
        label="Push to talk", alias="microphone mic",
        help="The caller's mic stays closed except while they hold (or tap to "
             "latch) a talk bar — space works on a keyboard. Better control in "
             "a noisy room, and the DJ never hears a TV in the background. The "
             "mic permission is still asked once, at pickup. On by default; "
             "switch off for an open mic from pickup."),
    "embed_push_to_talk": dict(group="talkbar", kind="check",
        label="Push to talk (embed)", alias="microphone mic",
        help="The same bar, on the embedded card."),
    "voice_effect": dict(group="effects", kind="select", label="Voice effect",
        help="A radio colour on the DJ's voice, applied in the caller's "
             "browser — the broadcast never hears it. On phones it plays "
             "through the default output, so the Speaker/earpiece button has "
             "nothing to route while an effect is on. Hear it with 'Test "
             "with effect' below."),
    "voice_effect_level": dict(group="effects", kind="number",
        label="Effect intensity (%)",
        # Every effect, not the first three — the dial vanished for anyone
        # picking a newer colour, which the operator read (fairly) as the
        # volume control disappearing. Operator-reported.
        needs=("voice_effect", ["telephone", "cb", "walkie", "am", "megaphone", "underwater", "stadium", "intercom", "shortwave", "lofi"]),
        help="0–100. 100 is the effect at full character; lower settles it "
             "toward the clean voice — 40 is a hint of radio rather than a "
             "costume. Test with effect uses this number."),
    "show_dj_avatar": dict(group="whosonair", kind="check", label="DJ photo", alias="avatar",
        help="Served through this origin, so it still loads from an https page "
             "off your network."),
    "embed_dj_avatar": dict(group="whosonair", kind="check", label="DJ photo (embed)", alias="avatar",
        help="Off if the host page already shows the same photo."),
    "default_to_speaker": dict(group="phone", kind="check",
        label="Start calls on loudspeaker", alias="loudspeaker speakerphone",
        help="A live microphone puts the phone into voice-call audio, which"
             " routes to the earpiece — so music playing out loud goes private"
             " the moment the DJ answers, which is wrong in a car. The caller can"
             " flip it either way mid-call. iOS Safari publishes no routing API,"
             " so there the platform decides."),
    "avatar_style": dict(group="whosonair", kind="select", label="DJ photo shape", alias="avatar",
        help="Applies wherever the photo is shown. Round suits a portrait and "
             "is what the card was built around; square matches a host page "
             "whose own artwork has corners."),
    "show_dj_show": dict(group="whosonair", kind="check", label="Show name",
        help="The programme currently on air."),
    "embed_dj_show": dict(group="whosonair", kind="check", label="Show name (embed)",
        help="Off if the host page already says what show is on."),
    "show_dj_tagline": dict(group="whosonair", kind="check", label="DJ tagline",
        help="The persona's one-line blurb, as the station publishes it."),
    "embed_dj_tagline": dict(group="whosonair", kind="check", label="DJ tagline (embed)",
        help="Off if the host page already carries it."),
    "show_now_playing": dict(group="whosonair", kind="check", label="Now playing",
        help="Updates on the card's own 20-second poll, so it will briefly "
             "disagree with a host page's faster ticker."),
    "embed_now_playing": dict(group="whosonair", kind="check", label="Now playing (embed)",
        help="Off if the host page already has a now-playing line."),
    "word_ringing": dict(group="linebox", kind="text", label="Ringing",
        placeholder="default: Ringing…",
        help="While the line rings, before the DJ picks up."),
    "word_answering": dict(group="linebox", kind="text", label="Answering",
        placeholder="default: Answering…",
        help="The moment the DJ picks up, before the first word."),
    "word_online": dict(group="linebox", kind="text", label="On the line",
        placeholder="default: On the line",
        help="For the length of the call, once both sides can hear."),
    "word_recording": dict(group="linebox", kind="text", label="Recording",
        placeholder="default: Recording…",
        help="While the machine is recording the caller's message."),
    "word_hangup": dict(group="buttons", kind="text", label="Hang up",
        placeholder="default: Hang up",
        help="On the button that ends a call."),
    "word_vm_button": dict(group="buttons", kind="text", label="Leave a message",
        placeholder="default: Leave a message",
        help="On the button that opens the machine."),
    "word_ptt": dict(group="talkbar", kind="text", label="Talk bar",
        placeholder="default: Tap to talk",
        help="On the bar the caller holds to talk."),
    "word_closed": dict(group="linebox", kind="text", label="Line closed",
        placeholder="default: Line closed",
        help="When no door is open — the card's resting state."),
    "word_message_only": dict(group="linebox", kind="text", label="Voicemail-only line",
        placeholder="default: Message only",
        help="When live calls are off but the machine is on."),
    "word_send": dict(group="buttons", kind="text", label="Send (text line)",
        placeholder="default: Send",
        help="On the text line's send button."),
    "word_connecting": dict(group="linebox", kind="text", label="Connecting",
        placeholder="default: Connecting…",
        help="While the call is being set up, before it rings."),
    "word_waiting": dict(group="linebox", kind="text", label="Waiting for the DJ",
        placeholder="default: Connected — waiting for the DJ…",
        help="Shown in the transcript area between the line connecting and "
             "the DJ's first word."),
    "word_ended": dict(group="linebox", kind="text", label="Conversation ended",
        placeholder="default: Call ended",
        help="Said when the line drops and when a text chat closes. The "
             "voicemail receipt keeps its own wording."),
    "transcript_dj_name": dict(group="linebox", kind="check",
        label="Name the DJ in the transcript", alias="transcript records",
        help="The transcript labels each line with who said it. On, the DJ's "
             "lines carry their name (ASH) instead of the generic DJ — which "
             "reads better on a station whose listeners know the roster, and "
             "follows the name as the show changes. The caller's own lines "
             "stay YOU either way. This is the card's LIVE transcript; what "
             "is written to disk is [Transcripts](#record)."),
    "call_button_mode": dict(group="buttons", kind="select", label="Call button",
        help="“Call the DJ” is the honest label when the card shows whoever "
             "happens to be on air. The DJ's name reads better on a station "
             "whose listeners know the roster, and follows it as the show "
             "changes."),
    "call_button_label": dict(group="buttons", kind="text", label="Custom words",
        needs=("call_button_mode", "custom"),
        placeholder="Call the DJ",
        help="Shown only for the custom option above."),
    # One row, a column per door — the panel prints THIS field's help for the
    # whole row (its .prow names it), so this one describes all three and the
    # other two carry the per-door caveat for anyone reading the schema.
    "ask_call_feedback": dict(group="feedback", kind="check",
        label="Ask how it went", alias="rating thumbs survey",
        help="A thumbs up or down under the card once the conversation ends, "
             "stored against its own transcript so a bad one can be found and "
             "read back. Nothing else is collected. Chats only ask when the "
             "caller actually typed something; leaving it off for voicemail "
             "keeps the machine's receipt as the last word, since “how was "
             "it?” over “message left” can read as fishing."),
    "ask_chat_feedback": dict(group="feedback", kind="check",
        label="Ask after a text chat", alias="rating thumbs survey",
        help="The same thumbs, offered when the caller ends a chat they "
             "actually typed in. Stored against the chat's transcript."),
    "ask_vm_feedback": dict(group="feedback", kind="check",
        label="Ask after a voicemail", alias="rating thumbs survey",
        help="The same thumbs after a message is left. Off keeps the "
             "machine's receipt as the last word — asking “how was "
             "it?” over “message left” can read as fishing."),
    "door_order": dict(group="buttons", kind="order", label="Button order",
        help="Drag to reorder the three doors on the card. This is the order "
             "they sit in left to right, on this page and in an embed alike; "
             "a door you have switched off simply is not there, and the rest "
             "close up. Hang up is not in the list — it replaces the whole "
             "row during a call and has nowhere else to be."),
    "widget_skin": dict(group="surface", kind="select", label="Skin (experimental)", alias="color palette look",
        help="A different look for the card, on this page and in embeds alike. "
             "A skin brings its own colours, so while one is on, the Colours "
             "setting and the viewer's light/dark toggle have nothing left to "
             "change — pick Default to get them back. Skins cannot change the "
             "card's size or its controls, only its surface, so none of them "
             "can break a call."),
    "widget_theme": dict(group="surface", kind="select", label="Colours", alias="color palette",
        help="Auto follows the viewer and keeps the toggle. Light and dark force "
             "one and hide it. Inherit matches the page the widget is embedded "
             "in; on this page it behaves as auto."),
    "swipe_player": dict(group="phone", kind="check",
        label="Swipe-up station player", alias="mobile phone",
        help="The ribbon at the card's top edge pulls down a full station "
             "player: cover art, what's playing, the queue, likes and song "
             "requests. It plays the Stream URL from Calls → Tune the caller "
             "in, so behind TLS that must be the station's public https "
             "stream. This page and the installed app only, never an embed. "
             "Starting a call or a recording stops the music."),
    "show_listener_count": dict(group="phone", kind="check",
        label="Listener count on the card", alias="listeners audience",
        help="The card's ON AIR line adds how many are tuned in — the same "
             "count the station's own player shows. Only appears when the "
             "station reports at least one listener, so a quiet hour never "
             "paints a zero at a caller deciding whether to ring."),
    "show_track_like": dict(group="phone", kind="check",
        label="Heart button on the card",
        help="A small heart beside the record on air — the same public like "
             "any listener page sends, through the same per-listener limits "
             "the station already enforces. Works with or without the "
             "swipe-up player."),
    "start_on_player": dict(group="phone", kind="select",
        label="Opens on",
        needs=("swipe_player", True),
        help="Which of the two faces a caller lands on; the other is always "
             "one swipe away. Browsers still wait for one tap before any "
             "audio starts, whichever you pick — that is their rule, not a "
             "fault."),
    "vm_player_duck": dict(group="phone", kind="number",
        label="Player under the machine (%)", alias="loudness duck voicemail",
        help="While the machine rings, greets and records, the station "
             "plays underneath at this volume — piped in even when the "
             "caller wasn't listening, the same move Tune-in makes on a "
             "call. 10 by default; 0 keeps the machine quiet. Much above "
             "20 and, on speakers, it bleeds into the recording. Music "
             "that was playing before returns to full volume when the "
             "machine hangs up. The machine itself is under "
             "[Voicemail machine](#voicemail)."),
    "min_call_seconds": dict(group="closing", kind="number",
        label="Earliest hang-up (s)", alias="duration length",
        help="The floor under the DJ ending a call itself. 60 by default: a model "
             "deciding a call is over after two words is worse than one that "
             "lingers, and the caller cannot tell it from the line dropping. "
             "0 removes the guard."),
    "idle_prompt_secs": dict(group="closing", kind="number", label="Check in after (s)", alias="timeout nudge",
        help="Seconds without SPOKEN WORDS before the DJ asks if they're still "
             "there. Background noise doesn't count. 0 never checks in."),
    "idle_max_nudges": dict(group="closing", kind="number", label="Check-ins before hanging up (count)",
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
    "tune_in_volume": dict(group="tunein", kind="number", label="Volume (%)", alias="loudness",
        needs=("tune_in_on_call", True),
        help="10 by default. 0 keeps it silent and the caller still counts as a "
             "listener. Much above 20 and, on speakers, it bleeds into their "
             "microphone and gets transcribed as if they had said it."),

    # --- sharing the microphone ---
    "avoid_on_air_overlap": dict(group="onair", kind="check", label="Pause the call while on air", alias="duck overlap",
        help="Anything sent to air waits for the broadcast to go quiet, and the DJ "
             "steps back from the call while it plays — telling the caller either "
             "side rather than talking over itself."),
    "quiet_station_on_calls": dict(group="onair", kind="select", admin=True,
        label="Quiet the station during calls", alias="mute duck volume loudness",
        help="Flips the station's own Voice switch off while a phone-in is live"
             " and back on within seconds of it ending, so idents, links and"
             " segments never talk over a call. Music and requests keep playing;"
             " a request just skips its spoken intro. Needs the station admin"
             " credentials and a SUB/WAVE from July 2026 or newer. Flip Voice"
             " back on yourself mid-call and Talk Wave leaves it alone."),
    "on_air_handover_secs": dict(group="onair", kind="number",
        label="Hand over before air (s)", alias="duck",
        needs=("avoid_on_air_overlap", True),
        help="The station warns when a voice is coming, sometimes many seconds "
             "ahead. The call keeps flowing until this close to air, then the "
             "DJ says its hand-over line and steps back — instead of holding "
             "the whole wait. Needs about 2s to get the line out; with less "
             "the gate closes silently. 5 suits the default mixer lead — lower "
             "it if the caller hears silence between the hand-over line and "
             "the broadcast."),
    "working_line_secs": dict(group="turns", kind="number",
        label="Say something after (s)",
        help="How long the DJ may be working on an answer before it says one "
             "short line so the caller knows somebody is still there. Covers "
             "the wait while the model thinks and a tool runs. 0 keeps the "
             "line silent until the answer is ready, and so does leaving the "
             "wording below empty."),
    "working_line_text": dict(group="turns", kind="text",
        placeholder="default: nothing — the DJ works in silence",
        label="…and say this",
        help="YOUR words, spoken while the DJ works. Separate several with | and"
             " they are used in turn. Left empty, nothing is said — the DJ cannot"
             " write this line itself, because a DJ told to speak before acting"
             " speaks INSTEAD of acting."),
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
        help="How much SOUND — not words — it takes to stop the DJ mid-sentence. "
             "The SDK's own floor is half a second, which with tune-in on means "
             "half a second of the record cuts the DJ off, and real calls came "
             "back chopped into single words. This ships at 0.8s for that reason. "
             "Raise it on a speakerphone, lower it if the DJ is slow to yield; "
             "0 hands the decision back to the SDK."),
    "allow_interruptions": dict(group="turns", kind="check",
        label="Let the caller talk over the DJ", alias="barge-in interrupt microphone",
        help="On is how a phone call works. Off is steadier on a speakerphone, "
             "where the station's own audio bleeding back can read as the caller "
             "interrupting."),

    # --- transcripts ---
    # --- voicemail ---
    # Filed with its own door, not with the call caps (0.98.24). It rode to
    # the Calls page when Usage controls became Call limits and moved there,
    # and nothing on screen looked wrong because the control is a dashboard
    # card — but the schema is what the finder reads, so it answered
    # "where is Take text chats" with
    # "Calls > Call limits", which is the one answer it exists to get right.
    "chat_enabled": dict(group="chat", kind="check",
        label="Take text chats",
        help="The text line: typed conversation with whoever is on air, "
             "same brain and same tools as the phone, over a plain "
             "WebSocket — no WebRTC, so it works where calls cannot. "
             "The Line's pause switch closes this door too."),
    # Same move, and this one's help gave the game away: "everything below"
    # was on a different page from the switch.
    "voicemail_enabled": dict(group="voicemail", kind="check",
        label="Enable voicemail",
        help="The machine's master switch — everything else here applies "
             "only while this is on. Its beep lives with the sound board, "
             "under [Call sounds](#sounds)."),
    "voicemail_when": dict(group="voicemail", kind="select", label="Answer with voicemail",
        needs=("voicemail_enabled", True),
        help="'When a live call is impossible' turns a busy or off-air refusal"
             " into a message instead of silence. The caps and the redial wait"
             " still refuse, on purpose — a message costs transcription. 'Always'"
             " makes the line voicemail-only, the cheapest way to run it: no LLM"
             " turns at all."),
    "allow_voicemail": dict(group="perms", kind="select", tiered=True,
        label="Leave a voicemail",
        help="Who may talk to the machine at all. [The machine](#voicemail) "
             "decides WHEN it answers; this decides WHO it answers for."),
    "allow_chat": dict(group="perms", kind="select", tiered=True,
        label="Text the booth",
        help="Who may open the text line at all. [Text line](#chat) "
             "holds its clocks and ceilings; this decides WHO gets in."),
    "live_calls_enabled": dict(group="usage", kind="check",
        label="Take live calls",
        help="Off, the Call button becomes the machine's door (with "
             "voicemail on) or says the line is closed. Independent of "
             "Voicemail below — the two switches together are the line's "
             "mode: phone, phone with a machine, voicemail-only, or "
             "closed."),
    # --- open lines ---
    "open_lines_enabled": dict(group="openlines", kind="check",
        label="Open Lines", alias="topic call-in talk discussion phone-in",
        help="The DJ puts a topic up on the broadcast and invites the audience "
             "to weigh in — then knows what it asked when somebody arrives. "
             "Off, nothing airs and the DJ is exactly as it is today."),
    "open_lines_source": dict(group="openlines", kind="select",
        label="Where the topic comes from", alias="premise",
        needs=("open_lines_enabled", True),
        help="The DJ invents one from the same material a station segment "
             "invents from — who is on air, the show, tonight's episode, what "
             "has just played. Or it takes the next one off your own shelf "
             "below, which is the choice to make if you want to know in "
             "advance what the station will ask."),
    "open_lines_address": dict(group="openlines", kind="text",
        label="Where to reach you, said on air", alias="url phone number line",
        placeholder="leave blank to name no address",
        needs=("open_lines_enabled", True),
        help="Read out with the invitation. Leave it blank when your audience "
             "is already looking at the card — a spoken address is for people "
             "hearing the stream somewhere else. Whatever you put here is what "
             "the DJ says, so write it the way it should sound."),
    "open_lines_minutes": dict(group="openlines", kind="number",
        label="How long a line stays open (min)", alias="duration window",
        needs=("open_lines_enabled", True),
        help="Then the DJ closes it on air, in character. A topic nobody took "
             "up still made the station sound like one that takes part."),
    "open_lines_reminder_minutes": dict(group="openlines", kind="number",
        label="Remind every (min)", alias="repeat nudge",
        needs=("open_lines_enabled", True),
        help="The DJ raises the open topic again during the window. 0 = "
             "announce once and say no more until it closes."),
    "open_lines_reminder_max": dict(group="openlines", kind="number",
        label="Most reminders per topic", alias="cap limit repeat",
        needs=("open_lines_enabled", True),
        help="The ceiling that actually protects the broadcast: a long window "
             "with a short interval is how a station ends up asking the same "
             "question nine times. 0 = no reminders."),
    "open_lines_followup": dict(group="openlines", kind="switch",
        label="Report back on air when somebody answers",
        alias="follow up feedback response tell",
        needs=("open_lines_enabled", True),
        help="When a conversation about the topic ends, the DJ goes back on "
             "air and says what came of it — the position taken, never a name "
             "and never a quote. Without this the loop is open at one end: "
             "listeners never learn the question was real or that anyone "
             "answered, so nobody else joins in. Off by default, because it "
             "puts more of the DJ on your broadcast. At most three per topic, "
             "and a request is not a contribution — those air nothing."),
    "open_lines_guest_trigger": dict(group="openlines", kind="switch",
        label="Let signed-in listeners start one",
        alias="guest player ribbon trigger",
        needs=("open_lines_enabled", True),
        help="Puts the segment button in the player's own ribbon for anyone "
             "holding a guest code, not just you. Off by default and worth "
             "thinking about: it is the only control on that page that reaches "
             "the broadcast, and a guest code is shared more freely than an "
             "admin password. Your own button on the dashboard is unaffected."),
    "open_lines_min_listeners": dict(group="openlines", kind="number",
        label="Only with at least this many listeners", alias="audience empty",
        needs=("open_lines_enabled", True),
        help="Checked when a line opens and before each reminder, never in "
             "the middle — a topic that vanished because somebody closed a tab "
             "would strand whoever was already typing. 0 = open regardless."),
    # kind="picks": a text-valued field whose control is drawn, like "order"
    # and "emoji". It saves, loads and diffs as a text field (panel.js folds it
    # into TEXT_FIELDS); the ticks beside it write the comma-separated ids. Not
    # kind="text", because a hidden input cannot show a placeholder and the
    # every-text-field-offers-its-default rule is right to insist on one.
    "open_lines_personas": dict(group="openlines", kind="picks",
        label="Only these DJs", alias="persona allowlist who",
        needs=("open_lines_enabled", True),
        help="Tick the ones that may open a line; none ticked means whoever is "
             "on air. Not every DJ on a station should be soliciting arguments, "
             "and the one on at 3am may not be the one you want doing it."),
    "open_lines_every_minutes": dict(group="openlines", kind="number",
        label="Open one automatically every (min)", alias="schedule auto cron",
        needs=("open_lines_enabled", True),
        help="0 = manual only, and that is the default: nothing reaches your "
             "listeners that you did not press the button for. Set it once you "
             "have heard a few go out and trust what the DJ comes up with."),
    "on_air_calls_enabled": dict(group="airdoors", kind="check",
        label="Calls may go on air",
        help="The phone-in door: off, the ON AIR route stops offering live "
             "calls without touching who may use the route or the voicemail "
             "door, and a phone-in already on the air stops at its next clip. "
             "The dashboard's Live-on-air cluster flips this same switch."),
    "on_air_voicemail_enabled": dict(group="airdoors", kind="check",
        label="Voicemails may go on air",
        help="The message door's same kill: off, the ON AIR route stops "
             "offering the studio and every message is a private one for "
             "you."),
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
        help="Spoken in the on-air DJ's own voice, so it is staged ahead of time"
             " below rather than generated while a caller waits. {station}, {dj}"
             " and {show} are filled in per persona; with nobody on air the"
             " machine answers as the station itself. Changing this re-renders"
             " every clip on the next staging run."),
    "voicemail_max_seconds": dict(group="voicemail", kind="number",
        label="Message ceiling (s)", alias="duration length",
        help="The hard stop on one message. STT runs for at most this long, "
             "which is what makes voicemail cheap to leave wide open."),
    "voicemail_flow": dict(group="voicemail", kind="select",
        label="What a message is, by default",
        help="Only matters while the ON AIR | OFF AIR switch is NOT on the "
             "card (the Go-live row off, or its voicemail door killed on "
             "the dashboard). With the switch up, the caller chooses: OFF "
             "AIR is the machine (a private message as text, no audio "
             "kept), ON AIR is the soundbite studio (record, review, aired "
             "with the DJ around it, audio deleted the moment it airs)."),
    # vm_mixer_telnet and vm_air_base_url deliberately have no schema entry —
    # the station_mcp_url ruling (0.10.80, operator's) applied again on
    # 2026-08-17, the operator's own words: "if it's derived couldn't we just
    # remove it". Both derive correctly on any ordinary deployment
    # (broadcast:1234; http://HOST_IP:8100 — the probe-proven URL), and the
    # rare exception overrides them in settings.json or the environment.
    "vm_air_backend": dict(group="airdoors", kind="select",
        label="A soundbite airs as",
        help="'The DJ reads it' works on any deployment. 'The caller's own"
             " voice' plays the recording on the station's voice channel,"
             " properly levelled, and needs this container joined to the"
             " station's docker network — without that it falls back to the DJ"
             " reading, and says so in the receipt. See the On Air setup docs for"
             " the network stanza."),
    "voicemail_destination": dict(group="voicemail", kind="select", label="Messages go",
        help="'Held for you' is the safe default — messages land below, and "
             "nothing reaches the air without you. The rest act on the station "
             "and need its admin credentials. 'Triage' reads each message with "
             "the configured model and picks for itself: a song request, a "
             "line for the on-air DJ, or one of the station's segments — "
             "bounded by the caller permissions above, one action per message."),

    "record_calls": dict(group="record", kind="check", label="Keep transcripts", alias="history logs recording archive",
        help="Both sides of every conversation — calls, texts and voicemail "
             "messages — with the tools the DJ used and the settings it ran "
             "under, written to data/calls. How a bad conversation gets "
             "diagnosed, and also a stranger's words on your disk."),
    "record_keep": dict(group="record", kind="number",
        label="How many transcripts to keep", alias="history logs retention archive",
        needs=("record_calls", True),
        help="Older ones are deleted as new ones land. This is about how long a "
             "caller's words stay on your disk, not about space."),

    # --- usage ---
    "max_concurrent_calls": dict(group="usage", kind="number", label="Calls at once", alias="rate limit throttle cap",
        help="Callers on the line at the same time. Each is a separate model session."),
    "calls_per_hour": dict(group="usage", kind="number", label="Calls per hour", alias="rate limit throttle cap",
        help="Across everybody — the main guard against a runaway loop."),
    "calls_per_day": dict(group="usage", kind="number", label="Calls per day", alias="rate limit throttle cap",
        help="The hard ceiling on what a day can cost. The hourly limit alone "
             "still allows 24× that."),
    # Filed with Access rather than with the call caps: it is not a cap, and
    # it is not call-only. Its own help says the machine goes quiet too, so
    # it outranks every door — and Access is already the section about who
    # may reach the line at all. "Nobody, right now" belongs there.
    "calls_paused": dict(group="security", kind="check", label="Pause all calls",
        help="Kill switch. The card still shows who's on air, but nobody can "
             "start a call — the answering machine and the text line go "
             "quiet too. Takes effect immediately."),
    "max_actions_per_call": dict(group="usage", kind="number", label="Actions per call", alias="rate limit cap",
        help="Requests, on-air messages and segments together. At the limit the "
             "DJ says so warmly and keeps talking — never an error."),
    "caller_cooldown_secs": dict(group="usage", kind="number", label="Redial wait time (s)", alias="rate limit throttle spam",
        help="How long one caller waits before calling back. 0 while testing."),

    # --- speech hygiene ---
    "strip_stage_directions": dict(group="speech", kind="check", label="Strip stage directions", alias="asterisks",
        help="Models write *shuffles records* and (laughs), and the voice reads "
             "them aloud. Removed whatever the model does."),
    "tts_dash_style": dict(group="speech", kind="select", label="Em dashes", alias="punctuation",
        help="Models love an em dash and voices stumble on it. Spoken as a "
             "breath by default; 'a plain dash' speaks it as \" - \"; "
             "'leave them' hands it to the voice backend as written."),
    "profanity_mode": dict(group="speech", kind="select", label="Expletives", alias="language swearing curse bad words filter",
        help="Applied to every spoken line, so it never depends on the model "
             "behaving."),
    "profanity_words": dict(group="speech", kind="text", label="Word list", alias="language swearing curse filter blocklist",
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
    # Filed under House style rather than any one door: the receipt is how the
    # booth presents an action wherever it acts, and it was found-then-lost as
    # a chat-only setting (the operator went looking for it under calls).
    "action_cards": dict(group="style", kind="select",
        label="Action receipts",
        help="The receipt card a station action leaves in the transcript — a"
             " queued request, a takeover, a beat — on every door. After the DJ's"
             " line reads as paperwork; as-it-happens fires the moment the tool"
             " does; off leaves the DJ's word as the only trace, though the"
             " action still runs and the record still lists it."),
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
             "permissions, under [Caller permissions](#perms)."),
    "callback_max_words": dict(group="callback", kind="number", label="Length (words)",
        needs=("callback_enabled", True),
        help="Short is better — a mention, not a recap."),
    "callback_min_turns": dict(group="callback", kind="number", label="Fewest caller turns to earn one",
        needs=("callback_enabled", True),
        help="Calls that never got going aren't worth mentioning."),
    "callback_instructions": dict(group="callback", kind="text", label="Extra steer",
        needs=("callback_enabled", True),
        placeholder="default: one passing mention, in character",
        help="e.g. 'never name the caller' or 'tie it to the current track'."),

    # --- station awareness ---
    "context_recent_tracks": dict(group="context", kind="number", label="Recently played songs (count)",
        help="Each item costs time-to-first-token on EVERY turn, not just at "
             "the start. 0 leaves it out."),
    "context_upcoming": dict(group="context", kind="number", label="Coming-up songs (count)",
        help="Lets the DJ answer 'what's next' without guessing."),
    "context_booth_lines": dict(group="context", kind="number", label="On-air chatter (lines)",
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
    "sound_ring": dict(group="sounds", kind="text", label="Ring", alias="ringtone",
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
    # STAYS in Call sounds, against the 0.98.24 review's own recommendation.
    # It is not a call sound — it is the machine's — and it is the only one
    # of the six that ignores "Play call sounds", which is what gave it away.
    # But the six moments are ONE board: six cards, one shared picker, one
    # shelf, and panel-sounds.py's SOUND_SLOTS assumes all six. Splitting it
    # out leaves a five-card board and a stray row, and trades a real loss of
    # coherence for a filing improvement. The group follows the CONTROL —
    # that is the same rule the door switches were moved to obey — and the
    # Voicemail machine section carries a link to it instead.
    "sound_vm_beep": dict(group="sounds", kind="text", label="Voicemail beep",
        placeholder="default: the classic tone",
        help="The beep before recording starts, and the one sound the SERVER "
             "plays rather than the card — so it must be a .wav the server "
             "can read. Deliberately not governed by Play call sounds: it "
             "tells the caller to start talking. Unplayable falls back to "
             "the tone."),
    "call_volume": dict(group="sounds", kind="number", label="Default volume (%)", alias="loudness",
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
    "open_lines_source": [
        ("dj", "The DJ decides — invents a topic from tonight's show"),
        ("shelf", "Off the shelf — the topics you wrote below"),
        ("quiz", "A quiz — the DJ sets a question it can mark"),
    ],
    "chat_greeting_mode": [
        ("canned", "Canned — the line below, instantly"),
        ("fresh", "Written each time — in persona at open"),
        ("off", "Off — wait for the caller to type first"),
    ],
    "start_on_player": [
        ("call", "The phone — the call card, with the player a swipe up (default)"),
        ("player", "The player — music first, the call button a swipe down"),
    ],
    "chat_reveal": [
        ("typing", "As it's typed — the words appear as the DJ writes them (default)"),
        ("dots", "Typing cue, then the line — three dots, then the reply lands whole"),
    ],
    "chat_type_pace": [
        ("slower", "Slower — an unhurried typist"),
        ("natural", "Normal — a brisk human typist (default)"),
        ("brisk", "Faster — quick, still readable as typing"),
        ("instant", "Instant — no reveal, the line appears at once"),
    ],
    "action_cards": [
        ("after", "After the DJ's line — words first, then the receipt (default)"),
        ("before", "As it happens — the receipt leads the line"),
        ("off", "Off — no cards; the DJ's word is the only trace"),
    ],
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
        ("custom", "Custom words…"),
    ],
    "avatar_style": [
        ("round", "Round — a portrait"),
        ("square", "Square — matches host artwork"),
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
    "voicemail_flow": [
        ("machine", "Answering machine — a message as text, no audio kept"),
        ("studio", "Soundbite studio — record, review, send to air"),
    ],
    # Option labels here are cut to what a 260px select can SHOW — the
    # operator's screenshot had "The caller's own voice — needs the mixe"
    # and every mode's consequence clause amputated mid-word. The clause the
    # label gives up already lives in the field's help, whole.
    "on_air_caller_sound": [
        ("clean", "Clean — their real voice (default)"),
        ("phone", "Phone — the radio-caller costume"),
    ],
    "on_air_call_mode": [
        ("live", "Live — just behind the room (default)"),
        ("heard", "Live, once the caller is heard"),
        ("after", "After the call — airs at hangup"),
    ],
    "quiet_station_on_calls": [
        ("off", "Off — the station talks as usual (default)"),
        ("on_air", "During on-air calls"),
        ("all", "During every call — on or off air"),
    ],
    "vm_air_backend": [
        ("dj-reads", "The DJ reads it — works everywhere"),
        ("caller-voice", "Their own voice — via the mixer"),
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
    # EXPERIMENTAL, and grouped the way an operator would look for them
    # rather than alphabetically. Every id here must have a matching block in
    # web-widget/skins.css; TestEverySkinOfferedActuallyExists fails the build
    # if one is offered that the stylesheet never draws.
    "widget_skin": [
        ("default", "Default — the card as it ships"),
        ("switchboard", "Switchboard — lamps and jack labels"),
        ("rack", "Rack unit — brushed steel and vents"),
        ("console", "Console strip — one channel of the desk"),
        ("shortwave", "Shortwave — wood and a lit dial"),
        ("tape", "Tape deck — the platter turns between calls"),
        ("terminal", "Terminal — green phosphor"),
        ("amber", "Amber CRT — the other phosphor"),
        ("datastream", "Datastream — phosphor, raining"),
        ("vault", "Vault — green tube behind a heavy frame"),
        ("arcade", "Arcade — 8-bit"),
        ("hud", "HUD — cyan instruments"),
        ("neon", "Neon — after dark, everything glowing"),
        ("glass", "Glass — frosted, light, floating"),
        ("dvd", "Screensaver — your show's name, bouncing"),
        ("blueprint", "Blueprint — drafting grid on navy"),
        ("paper", "Paper — a note left on the desk"),
        ("eink", "E-ink — greyscale, nothing moving"),
        ("mac", "Classic Mac — 1-bit and dithered"),
        ("win95", "Windows 95 — bevels and system grey"),
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
    # The station's locca (a llama.cpp it runs on the host). Same live
    # /v1/models discovery as openai-compatible — the difference is only that
    # a blank Endpoint falls back to locca's well-known address instead of
    # being an error.
    "locca": [],
    "ollama": [],
}

# Where the station's locca answers when the Endpoint field is left blank —
# mirrored from SUB/WAVE's DEFAULT_LOCCA_BASE_URL, which points at the host
# from inside a container. An explicit Endpoint always wins, and LOCCA_BASE_URL
# in the environment sits between the two, like OLLAMA_BASE_URL does.
LOCCA_BASE_URL_DEFAULT = "http://host.docker.internal:8080/v1"

# Which stored key each provider needs before it can answer a call at all.
# None means "needs no key of ours": Ollama runs on the operator's own network,
# and the local Whisper is compiled into this container.
#
# The panel offers only the providers whose key is present. Listing all five
# regardless meant a fresh install's Provider dropdown was four ways to
# configure a call that could not connect, and the failure arrived later, from
# a test button, as a 401 — rather than at the moment of choosing.
# Declaration order is dropdown order (operator's ask, 0.10.85): the LOCAL
# runners lead — they need no key and no account, so they are what a fresh
# install can actually pick — and the cloud vendors follow.
LLM_PROVIDER_KEY: dict[str, str | None] = {
    # Your own OpenAI-protocol server, like the station's own
    # openai-compatible provider: no managed key. If the server wants one
    # anyway, set OPENAI_COMPAT_API_KEY in the environment.
    "openai-compatible": None,
    # The station's own local runner — no key, like Ollama. Mirrored so an
    # operator whose station thinks on locca can point the call line at the
    # same box by picking the same name.
    "locca": None,
    "ollama": None,
    # The clouds. deepseek/requesty/gateway are the three SUB/WAVE offers
    # that this did not: a companion app that cannot point at the provider
    # the station already pays for makes the operator keep two accounts to
    # run one radio station.
    "openai": "openai_api_key",
    "openrouter": "openrouter_api_key",
    "google": "google_api_key",
    "anthropic": "anthropic_api_key",
    "deepseek": "deepseek_api_key",
    "requesty": "requesty_api_key",
    "gateway": "gateway_api_key",
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
    "locca": "locca (the station's local runner, no key)",
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
    # Smallest to largest, and the ORDER is part of the documentation: the
    # list used to lead with base.en (the default), which read as "the best
    # one" — the operator picked it believing exactly that (2026-08-17).
    # The panel labels each with its trade; keep the two in step.
    "local": ["tiny.en", "base.en", "small.en", "medium.en"],
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


# Fields that must be a URL or nothing. A real deployment had "Michael" in
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
