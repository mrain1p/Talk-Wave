"""The operator panel's presentation data, and the mirrored provider/vocab
tables — declarative only.

Peeled out of settings.py (the maintainability plan, Batch 1). This is the
~1,500 lines the panel renders from: SUPERGROUPS/GROUPS/SCHEMA describe the
settings surface, and the provider tables (MODEL_CHOICES, LLM_PROVIDER_*,
STT_*, OPENAI_*) mirror what the station offers. Pure data with no behaviour —
the resolver functions that READ it (schema_payload, _choices_for,
provider_base_urls, mcp_tools_payload, ...) stay in settings.py, because they
reach back into the store and moving them would form a cycle.

SCHEMA references TIERS, so this leaf imports it from caller_tiers; the
dependency runs one way (settings -> settings_schema -> caller_tiers).
"""
from caller_tiers import TIERS  # noqa: F401  (SCHEMA references it)


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
    # "On-air ducking" until 0.98.60 — the operator went looking for "where
    # do I suppress the on-air DJ during a call" and the jargon title did
    # not answer. The name now says BOTH directions the section holds.
    ("onair",    "air",    "Call vs broadcast",
     "When both would talk at once: the call pauses for the air, the station quiets for the call."),
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
    "onair":     "ducking mute volume suppress silence quiet the dj talk over overlap",
    "airdoors":  "broadcast live go live",
    "voicemail": "answering machine messages",
    "chat":      "text sms typing messages",
    "linebox":   "wording strings labels copy",
    "topcorner": "header controls",
    "whosonair": "avatar photo show tagline now playing",
    "feedback":  "rating thumbs survey",
    "station":   "subwave api url endpoint",
}


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
        help="Nothing is picked until you pick it — until then the DJ has no "
             "model, and the dashboard says so. Only providers with a key are "
             "listed; add one below and it appears here. Ollama needs none."),
    "llm_model": dict(group="brains", kind="select", label="Model",
        help="Read live from the provider. Over ~1.5s to first token the "
             "caller hears a pause before every reply; over 30s a call cannot "
             "be carried at all. Test it — the check uses a real call's "
             "prompt and tools."),
    "llm_base_url": dict(group="brains", kind="text", label="Endpoint",
        needs=("llm_provider", ("ollama", "openai", "openrouter",
                                "deepseek", "requesty", "gateway",
                                "openai-compatible", "locca")),
        placeholder="default: the provider's own address",
        help="Only for a self-hosted or gateway endpoint; required for "
             "'OpenAI-compatible', optional for locca. With one set, the "
             "Model list is read from it (“Test keys + reload models”) — "
             "servers like llama-swap only route model names they declare."),
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
        help="Nothing is picked until you pick it — until then the DJ has no "
             "voice. 'local' is your own speech server and can use the "
             "station's persona voices (Test voice measures its speed). "
             "'cloud' is fast but won't match the on-air timbre."),
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
        help="Two reads over the ANALYSED AUDIO rather than titles: a 'sounds "
             "like' search over a description, and 'more like this' off the "
             "track on air. Needs the station's analyzer — without it the DJ "
             "says it can't. Queues nothing and costs no action."),
    "allow_exact_queue": dict(group="perms", kind="select", tiered=True, label="Queue the exact track picked",
        admin=True,
        needs=("allow_library_search", TIERS),
        help="Queues the recording the caller chose out of the search results, "
             "rather than re-matching the words. Skips the station's request rate "
             "limit, so Actions per call is the only thing pacing it."),
    "allow_album_queue": dict(group="perms", kind="select", tiered=True, label="Queue albums and mixes",
        admin=True,
        needs=("allow_library_search", TIERS),
        help="Bulk queueing: a whole album, or a run of picks as one batch — "
             "30 tracks an album, 8 a mix, each batch counting once against "
             "Actions per call. The DJ only queues an album when the caller "
             "clearly wants the lot."),
    "allow_cancel_queue": dict(group="perms", kind="select", tiered=True, label="Take a track back out of the queue",
        admin=True,
        help="Lets a caller undo a request before it airs; once playing or "
             "cued next the station refuses and the DJ says so. Off by "
             "default — the queue is shared, so this can pull a record "
             "somebody else asked for."),
    "allow_favorite": dict(group="perms", kind="select", tiered=True, label="Like the track on air",
        help="Adds a like to the record playing now — the same heart a "
             "listener taps in the app: no station credentials, nobody's "
             "audio changes. The station gates and rate-limits it. Current "
             "track only; no public un-like."),
    "allow_unfavorite": dict(group="perms", kind="select", tiered=True, label="Un-like the track on air",
        admin=True,
        help="Removes the OPERATOR's own heart from the current record — the "
             "admin likes system, so it only means anything to a caller "
             "signed in as the operator. Needs station admin credentials."),
    "allow_announcements": dict(group="perms", kind="select", tiered=True, label="Put messages on air",
        admin=True,
        help="Hands a line to the on-air DJ to read in persona."),
    # These two read as the same switch until you see them side by side. They
    # are not: one is about what a caller may ASK FOR, the other about whether
    # the DJ may BRING IT UP first.
    "allow_skills": dict(group="perms", kind="select", tiered=True, label="Run segments when asked",
        admin=True,
        help="The station's own skills — weather, news, dedications, story "
             "time — run on air in the DJ's voice when asked. The manual "
             "trigger bypasses the station's frequency gates on purpose; "
             "Actions per call is the only pacing."),
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
    "allow_player_commands": dict(group="perms", kind="select", tiered=True,
        admin=True,
        label="Operator commands from the player",
        help="Who may drive the booth from the player's request line — "
             "one-shot typed commands through the same brain and tools the "
             "text line runs, each spending an LLM turn. The player switch "
             "that shows the mode at all is on the caller's-phone page."),
    "allow_dj_segment": dict(group="perms", kind="select", tiered=True, admin=True,
        label="Fire a programme beat",
        help="Station ID, the hour, a link, guest banter, an intro or outro — "
             "the programme's own furniture. Firing one bypasses the "
             "station's frequency and budget gates, so Actions per call is "
             "the only ceiling."),
    "allow_takeover": dict(group="perms", kind="select", tiered=True, admin=True,
        label="Put a different show on air",
        help="Pins a show over the weekly schedule — a different DJ, for "
             "everyone — an hour by default, landing at the end of the record "
             "playing. The only caller action that outlives the call; the DJ "
             "can also cancel one, including one you set yourself."),
    "allow_genre_lock": dict(group="perms", kind="select", tiered=True, admin=True,
        label="Lock the station to a genre",
        help="Holds the station to one genre or a few for a set window — 15 "
             "to 720 minutes, ending by itself — via the station's own "
             "genre-lock (an older SUB/WAVE answers that it can't). Quieter "
             "than a takeover: a narrowed playlist never announces itself on "
             "air."),
    "allow_on_air": dict(group="perms", kind="select", tiered=True, admin=True,
        label="Go live on the station",
        help="The phone-in: the conversation airs while it happens, one "
             "finished turn at a time. The card grows a Live-on-air toggle "
             "when a caller may choose it. Needs the mixer's telnet door — "
             "without it the call stays private, and the transcript says why."),
    # The window, the delay and the caller's sound live on the On air page
    # with the other airing choices (operator's ask, 2026-08-19, the same
    # move "When the call airs" made the day before): they describe how the
    # broadcast is delivered, not what a caller may do. The tier row alone
    # stays under Caller permissions.
    "on_air_max_seconds": dict(group="airdoors", kind="number", admin=True,
        label="On-air window", unit="sec", alias="duration length",
        needs=("allow_on_air", TIERS),
        help="How long one caller may hold the broadcast before the relay "
             "signs them off air and the call carries on privately. The "
             "station's own segments queue behind a live call, so shorter is "
             "kinder. Blank = 240."),
    "on_air_delay_secs": dict(group="airdoors", kind="number", admin=True,
        label="On-air delay", unit="sec",
        needs=("allow_on_air", TIERS),
        help="How long a finished turn is held before it airs — your "
             "take-back window; PULL OFF AIR kills any turn still inside it. "
             "2–30s, blank = 6, never 0. A caller with the station in earshot "
             "hears themselves ~22s later whatever you set."),
    "on_air_caller_sound": dict(group="airdoors", kind="select", admin=True,
        label="Caller sound on air",
        needs=("allow_on_air", TIERS),
        help="How a caller's voice is dressed before it airs. Clean keeps "
             "their real voice, levelled and de-rumbled — the default. Phone "
             "is the 300–3400 Hz radio-caller sound, on purpose. Applies to "
             "live phone-ins and studio soundbites alike."),
    # Lives on the On air page beside the other airing choices (operator's
    # ask, 2026-08-18) — it says how the broadcast is delivered, not what a
    # caller may do, so Caller permissions was the wrong shelf for it.
    "on_air_call_mode": dict(group="airdoors", kind="select", admin=True,
        label="When the call airs",
        needs=("allow_on_air", TIERS),
        help="Live airs each finished turn a few seconds behind the room. "
             "Live once heard holds the broadcast until the caller speaks. "
             "After the call tapes the whole thing and plays it on hang-up, "
             "killable entire until it does. The on-air window caps all "
             "three."),
    "allow_never_play": dict(group="perms", kind="select", tiered=True, admin=True,
        label="Ban a track for good", alias="block ban blocklist",
        help="Puts the track playing now on the station's never-play list: "
             "out of the queue and the fallback playlist, never selected "
             "again. The only PERMANENT caller action; nothing airs to say it "
             "happened. The same switch LIFTS a ban, including yours."),

    # --- call length ---
    "max_call_seconds": dict(group="closing", kind="number", label="Hang up after", unit="sec", alias="timeout duration length",
        help="Hard ceiling. The DJ signs off in character first rather than the "
             "audio just stopping. 600 = ten minutes."),
    "guest_session_hours": dict(group="security", kind="number",
        label="Guest code expires", unit="hours", alias="password code session expiry",
        help="Per device: each browser that typed the code runs its own "
             "clock. On a shared machine a code should not outlive its typist "
             "— the card forgets it after this long, with a lock button to "
             "forget it now. 0 remembers it until Sign out."),
    "front_access": dict(group="security", kind="select",
        label="Call-in access", alias="password login sign-in gate code",
        help="This is the PHONE — who may ring at all; what a caller may DO "
             "is per-tier under [Caller permissions](#perms). Guest code "
             "alone closes the line to strangers; with both ticked strangers "
             "still ring and a code-holder gets their own tier. The admin "
             "password opens everything."),
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
             "takes requests. One switch, both faces: the station player's own "
             "header carries the same button, and pressing it there steps back "
             "to the card to show the list."),
    "embed_caller_help": dict(group="topcorner", kind="check",
        label="“What can I ask?” button (embed)",
        help="The same button, in a frame on somebody else's page."),
    "chat_idle_minutes": dict(group="chat", kind="number",
        label="Close after quiet", unit="min", alias="timeout",
        help="A chat with nothing said for this long is over: the record is "
             "written and the id stops resuming. The widget keeps its side, "
             "so a returning caller simply starts a fresh conversation."),
    "chat_max_messages": dict(group="chat", kind="number",
        label="Messages per chat", alias="rate limit cap",
        help="A ceiling on one conversation, not a rate: hitting it closes "
             "the chat politely. 0 = no ceiling."),
    "chat_max_minutes": dict(group="chat", kind="number",
        label="Longest chat", unit="min",
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
        label="Reopen wait time", unit="sec", alias="rate limit throttle spam",
        help="How long ONE caller waits between opening chats — the text "
             "line's Redial wait. A text line is scriptable in a way a call "
             "is not, so this singles out one abuser where the hourly and "
             "daily caps only stop a crowd. Resuming an open chat never "
             "waits."),
    "chat_msgs_per_minute": dict(group="chat", kind="number",
        label="Messages per minute", alias="rate limit throttle cap",
        help="Per chat. A human types a handful; a script does not. The "
             "excess is refused in-world, not queued."),
    "chat_greeting_mode": dict(group="chat", kind="select",
        label="Open with a greeting",
        help="Whether the booth speaks first on a fresh text line — a silent "
             "line reads as broken. “Canned” sends the line below (instant, "
             "no model cost); “Written each time” has the DJ write one in "
             "persona; “Off” waits for the caller."),
    "chat_reveal": dict(group="chat", kind="select",
        label="Delivery",
        help="“As it's typed” reveals the DJ's words as they're written — the "
             "line reads as a person at a keyboard. “Typing cue, then the "
             "line” shows the dots while the booth composes and lands the "
             "reply whole — quicker to read, and better on a slow connection."),
    "chat_type_pace": dict(group="chat", kind="select",
        label="Typing pace",
        needs=("chat_reveal", "typing"),
        help="How fast the words appear. Normal is about a brisk human typist "
             "— the old speed ran at ~400 words a minute, which read as a "
             "machine. A long reply always lands within a few seconds "
             "whatever this says."),
    "chat_greeting": dict(group="chat", kind="text",
        label="Canned greeting",
        placeholder="You're through to the booth — what's on your mind?",
        help="The opening line for “Canned”. Blank uses a sensible default in "
             "the DJ's name. Takes {station}, {dj} and {show}, filled live."),
    "chat_reply_timeout_secs": dict(group="chat", kind="number",
        label="Reply timeout", unit="sec", alias="timeout",
        help="How long the DJ may take to answer one message before the line "
             "gives up and says so, so a stalled model never leaves the caller "
             "watching a typing dot forever. 0 = wait indefinitely."),
    "chat_reprompt": dict(group="chat", kind="check",
        label="Nudge a quiet caller",
        help="When the CALLER has gone quiet after the DJ's last message, the "
             "DJ sends ONE short in-persona line to keep the chat breathing — "
             "never \"are you still there?\", and never while the DJ still "
             "owes a reply. On by default."),
    "chat_reprompt_secs": dict(group="chat", kind="number",
        label="…after", unit="sec",
        needs=("chat_reprompt", True),
        help="How long a caller may be quiet, the ball in their court, before "
             "a nudge. 75 by default — under a phone typist's pace reads as "
             "pushy. At most two nudges per chat, and never while the DJ "
             "itself owes an action."),
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
        help="A corner button where a caller enters the guest code or admin "
             "password to UNLOCK more of what they can ask for. Shows only "
             "when a code is set and there is a higher tier to reach. Tiers "
             "are set under [Caller permissions](#perms)."),
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
        help="Comma-separated https origins that may embed the card and place "
             "calls on your API keys — add the site the snippet is pasted "
             "into; this page needs no entry. Applies on the next request, no "
             "restart. “*” lets any page spend your budget — dev only."),
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
        help="The mic stays closed except while the caller holds (or taps to "
             "latch) the talk bar — space on a keyboard. Better in a noisy "
             "room: the DJ never hears the TV. The permission is still asked "
             "once, at pickup. Off gives an open mic from pickup."),
    "embed_push_to_talk": dict(group="talkbar", kind="check",
        label="Push to talk (embed)", alias="microphone mic",
        help="The same bar, on the embedded card."),
    "voice_effect": dict(group="effects", kind="select", label="Voice effect",
        help="A radio colour on the DJ's voice, applied in the caller's "
             "browser — the broadcast never hears it. On phones it plays "
             "through the default output, so Speaker/earpiece has nothing to "
             "route while an effect is on. Hear it with 'Test with effect'."),
    "voice_effect_level": dict(group="effects", kind="number",
        label="Effect intensity", unit="%",
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
        help="A live microphone puts the phone into voice-call audio, which "
             "routes to the earpiece — out-loud music goes private when the "
             "DJ answers, wrong in a car. The caller can flip it mid-call. "
             "iOS Safari has no routing API; there the platform decides."),
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
        help="On, the DJ's lines in the card's live transcript carry their "
             "name (ASH) instead of DJ, following the show as it changes; the "
             "caller's lines stay YOU. The disk copy is under "
             "[Transcripts](#record)."),
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
        help="A thumbs up or down under the card when the conversation ends, "
             "stored against its own transcript so a bad one can be found and "
             "read back. Nothing else is collected. Chats only ask when the "
             "caller actually typed."),
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
        help="Drag to reorder the three doors, left to right, on this page "
             "and in an embed alike; a door you have switched off simply is "
             "not there. Hang up is not in the list — it replaces the whole "
             "row during a call."),
    "widget_skin": dict(group="surface", kind="select", label="Skin (experimental)", alias="color palette look",
        help="A different look for the card, here and in embeds alike. While "
             "a skin is on, Colours and the light/dark toggle have nothing to "
             "change — Default gives them back. Skins cannot touch the card's "
             "size or controls, so none can break a call."),
    "widget_theme": dict(group="surface", kind="select", label="Colours", alias="color palette",
        help="Auto follows the viewer and keeps the toggle. Light and dark force "
             "one and hide it. Inherit matches the page the widget is embedded "
             "in; on this page it behaves as auto."),
    "swipe_player": dict(group="phone", kind="check",
        label="Swipe-up station player", alias="mobile phone",
        help="The ribbon at the card's top pulls down the full station "
             "player. It plays the Stream URL from Calls → Tune the caller in "
             "— behind TLS, the public https stream. This page and the "
             "installed app only, never an embed. A call stops the music."),
    "show_listener_count": dict(group="phone", kind="check",
        label="Listener count on the card", alias="listeners audience",
        help="The ON AIR line adds how many are tuned in — the station's own "
             "count. Appears only when at least one listener is reported, so "
             "a quiet hour never paints a zero at a caller deciding whether "
             "to ring."),
    "show_track_like": dict(group="phone", kind="check",
        label="Heart button on the card",
        help="A small heart beside the record on air — the same public like "
             "any listener page sends, through the same per-listener limits "
             "the station already enforces. Works with or without the "
             "swipe-up player."),
    "player_skip_button": dict(group="phone", kind="check",
        needs=("swipe_player", True),
        label="Skip button on the player", alias="next track",
        help="A skip control beside the heart — station-wide, everyone "
             "hears the record end. The button only appears for callers "
             "whose tier clears \"Skip the current track\" in Permissions; "
             "this switch decides whether it is on the sheet at all."),
    "player_operator_mode": dict(group="phone", kind="check",
        needs=("swipe_player", True),
        label="Operator mode on the request line", alias="commands booth",
        help="The request box grows a second face: one-shot commands "
             "through the same brain and tools as the text line — \"queue "
             "X then a shoutout for Y\" — with the actions taken flashed "
             "back and listed under a Booth tab. Who may command is "
             "\"Operator commands\" in Permissions; each command spends an "
             "LLM turn like a text message."),
    "player_cast_button": dict(group="phone", kind="check",
        needs=("swipe_player", True),
        label="Cast button on the player", alias="chromecast airplay",
        help="The player's cast control — Chromecast on Chrome, AirPlay on "
             "Safari. Stays visible even while nothing is playing: pressing "
             "it starts the stream and opens the picker, and reopening the "
             "picker is how you switch speakers or stop casting. Hidden "
             "only on browsers with no casting at all."),
    "start_on_player": dict(group="phone", kind="select",
        label="Opens on",
        needs=("swipe_player", True),
        help="Which of the two faces a caller lands on; the other is always "
             "one swipe away, and the pull-down tab sits on whichever face "
             "is the visitor — the gesture follows the start you chose. "
             "Browsers still wait for one tap before any audio starts, "
             "whichever you pick — that is their rule, not a fault."),
    "vm_player_duck": dict(group="phone", kind="number",
        label="Player under the machine", unit="%", alias="loudness duck voicemail",
        help="While the machine rings, greets and records, the station plays "
             "underneath at this volume — Tune-in's own move. 10 by default; "
             "0 keeps the machine quiet; much above 20 it bleeds into the "
             "recording on speakers. Full volume returns at hang-up."),
    "min_call_seconds": dict(group="closing", kind="number",
        label="Earliest hang-up", unit="sec", alias="duration length",
        help="The floor under the DJ ending a call itself. 60 by default — a "
             "model deciding a call is over after two words is worse than one "
             "that lingers. 0 removes the guard."),
    "idle_prompt_secs": dict(group="closing", kind="number", label="Check in after", unit="sec", alias="timeout nudge",
        help="Seconds without SPOKEN WORDS before the DJ asks if they're still "
             "there. Background noise doesn't count. 0 never checks in."),
    "idle_max_nudges": dict(group="closing", kind="number", label="Check-ins before hanging up (count)",
        needs=("idle_prompt_secs", True),
        help="After this many unanswered check-ins the DJ signs off and gets back "
             "to the broadcast."),

    # --- tune the caller in ---
    "tune_in_on_call": dict(group="tunein", kind="check", label="Tune the caller in",
        help="Starts the station stream in the caller's browser at pickup, "
             "never while ringing. The station refuses requests when nobody "
             "is listening, and a caller on the line doesn't otherwise count. "
             "Recommended."),
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
             "station's own https stream (Admin → Connect → Stream URLs "
             "on stations 1.11+). The pipeline check tests it."),
    "tune_in_volume": dict(group="tunein", kind="number", label="Volume", unit="%", alias="loudness",
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
        help="Flips the station's Voice switch off while a phone-in is live, "
             "back on seconds after — idents and segments never talk over a "
             "call. Needs station admin credentials and a SUB/WAVE from July "
             "2026 or newer. Flip Voice on yourself mid-call and Talk Wave "
             "leaves it alone."),
    "on_air_handover_secs": dict(group="onair", kind="number",
        label="Hand over before air", unit="sec", alias="duck",
        needs=("avoid_on_air_overlap", True),
        help="The station warns when a voice is coming; the call flows until "
             "this close to air, then the DJ says its hand-over line and "
             "steps back. Under ~2s the gate closes silently. 5 suits the "
             "default mixer lead; lower it if the caller hears silence before "
             "the broadcast."),
    "working_line_secs": dict(group="turns", kind="number",
        label="Say something after", unit="sec",
        help="How long the DJ may be working on an answer before saying one "
             "short line so the caller knows somebody is there — covers the "
             "model thinking and a tool running. 0 keeps the line silent, and "
             "so does leaving the wording below empty."),
    "working_line_text": dict(group="turns", kind="text",
        placeholder="default: nothing — the DJ works in silence",
        label="…and say this",
        help="YOUR words, spoken while the DJ works; separate several with | "
             "and they take turns. Left empty nothing is said — the DJ cannot "
             "write this line itself, because a DJ told to speak before "
             "acting speaks INSTEAD of acting."),
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
        label="Wait before replying", unit="sec",
        help="How long the DJ waits after you stop making sound. Lower feels "
             "snappier and cuts off anyone who pauses to think; higher adds that "
             "much to every reply. 0 keeps the SDK's tuned default."),
    "max_endpointing_delay": dict(group="turns", kind="number",
        label="Longest wait", unit="sec",
        help="The ceiling on the above when someone is clearly mid-sentence. "
             "0 keeps the default. Must not be below the minimum."),
    "min_interruption_secs": dict(group="turns", kind="number",
        needs=("allow_interruptions", True),
        label="Sound needed to interrupt", unit="sec",
        help="How much SOUND — not words — stops the DJ mid-sentence. The "
             "SDK's half-second floor let the record cut the DJ off with "
             "tune-in on, so this ships at 0.8s. Raise on a speakerphone, "
             "lower if the DJ is slow to yield; 0 hands back to the SDK."),
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
    # Named as the dashboard card names it: the door pages quote this label
    # ("The switch is X, on the dashboard"), and "Take text chats" sent the
    # operator hunting for a switch that says "Text line" (settings review,
    # 2026-08-24). The old verb phrase stays findable via the alias.
    "chat_enabled": dict(group="chat", kind="check",
        label="Text line", alias="take text chats typed enable",
        help="Typed conversation with whoever is on air — same brain, same "
             "tools as the phone, over a plain WebSocket, so it works where "
             "calls cannot. The Line's pause switch closes this door too."),
    # Same move, and this one's help gave the game away: "everything below"
    # was on a different page from the switch.
    "voicemail_enabled": dict(group="voicemail", kind="check",
        label="Voicemail", alias="enable voicemail machine answering",
        help="The machine's master switch — everything else here applies "
             "only while this is on. Its beep lives with the sound board, "
             "under [Call sounds](#sounds)."),
    "voicemail_when": dict(group="voicemail", kind="select", label="Answer with voicemail",
        needs=("voicemail_enabled", True),
        help="'When a live call is impossible' turns a busy or off-air "
             "refusal into a message; the caps and redial wait still refuse — "
             "a message costs transcription. 'Always' makes the line "
             "voicemail-only: no LLM turns, the cheapest way to run it."),
    "allow_voicemail": dict(group="perms", kind="select", tiered=True,
        label="Leave a voicemail",
        help="Who may talk to the machine at all. [The machine](#voicemail) "
             "decides WHEN it answers; this decides WHO it answers for."),
    "allow_chat": dict(group="perms", kind="select", tiered=True,
        label="Text the booth",
        help="Who may open the text line at all. [Text line](#chat) "
             "holds its clocks and ceilings; this decides WHO gets in."),
    "live_calls_enabled": dict(group="usage", kind="check",
        label="Live calls", alias="take live calls answer phone",
        help="Off, the Call button becomes the machine's door (with voicemail "
             "on) or says the line is closed. Independent of Voicemail below "
             "— together the two switches are the line's mode: phone, phone "
             "with a machine, voicemail-only, closed."),
    # --- open lines ---
    "open_lines_enabled": dict(group="openlines", kind="check",
        label="Open Lines", alias="topic call-in talk discussion phone-in",
        help="The DJ puts a topic up on the broadcast and invites the audience "
             "to weigh in — then knows what it asked when somebody arrives. "
             "Off, nothing airs and the DJ is exactly as it is today."),
    "open_lines_source": dict(group="openlines", kind="select",
        label="Where the topic comes from", alias="premise",
        needs=("open_lines_enabled", True),
        help="The DJ invents one from tonight's show; a targeted direction "
             "hands it a named angle at random (guilty pleasure, night "
             "drive, cover verdict…) and it writes inside that — like a "
             "station skill; or take the next one off your shelf below if "
             "you want the question known in advance."),
    "open_lines_directions": dict(group="openlines", kind="text",
        label="Directions to draw from", alias="angles",
        needs=("open_lines_source", ["directions"]),
        placeholder="default: all of them",
        help="Comma-separated names to narrow the deck: guilty pleasure, "
             "first record, night drive, cover verdict, the skip, one lyric, "
             "live moment, got them through, undiscovered, hometown sound, "
             "dream duet, tonight's thread."),
    "open_lines_address": dict(group="openlines", kind="text",
        label="Call-in line", alias="url phone number address reach where",
        placeholder="leave blank to name no address",
        needs=("open_lines_enabled", True),
        help="Set this and the DJ DIRECTS listeners here on air, reading the "
             "address out with the invitation. Leave it blank when the "
             "audience is already looking at the card. Whatever you write is "
             "spoken aloud, so write it the way it should sound."),
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
        label="Air a follow-up after each conversation",
        alias="follow up feedback response tell report back answers",
        needs=("open_lines_enabled", True),
        help="When a conversation about the topic ends, the DJ goes back on "
             "air with what came of it — the position taken, never a name, "
             "never a quote. At most three per topic; a request is not a "
             "contribution. Off by default: more of the DJ on your broadcast."),
    "open_lines_guest_trigger": dict(group="openlines", kind="switch",
        label="Let signed-in listeners open a line",
        alias="guest player ribbon trigger start",
        needs=("open_lines_enabled", True),
        help="Puts the segment button in the player's ribbon for anyone with "
             "a guest code. Off by default and worth a thought — it is the "
             "only control on that page that reaches the broadcast, and a "
             "guest code travels more freely than an admin password."),
    "open_lines_min_listeners": dict(group="openlines", kind="number",
        label="Only with at least this many listeners", alias="audience empty",
        needs=("open_lines_enabled", True),
        help="Checked when a line opens and before each reminder, never in "
             "the middle — a topic that vanished because somebody closed a tab "
             "would strand whoever was already typing. No reported count "
             "counts as nobody: a cold station no longer solicits an empty "
             "room. 0 = open regardless (also the setting for a station that "
             "never reports its listeners)."),
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
             "calls — who may use the route and the voicemail door are "
             "untouched, and a phone-in already airing stops at its next "
             "clip. The dashboard's Live-on-air cluster flips this same "
             "switch."),
    "on_air_voicemail_enabled": dict(group="airdoors", kind="check",
        label="Voicemails may go on air",
        help="The message door's same kill: off, the ON AIR route stops "
             "offering the studio and every message is a private one for "
             "you."),
    "voicemail_greeting_mode": dict(group="voicemail", kind="select",
        label="Greeting comes from",
        help="Staged clips answer instantly. 'Fresh each call' writes a new "
             "line in the persona's voice at pickup — a model line plus a TTS "
             "render, a few seconds on slow backends — falling back to the "
             "staged clip, then the beep."),
    # The quoted default is the REAL one from voicemail/greetings.py, token
    # case included: the filler drops unknown placeholders silently, so a
    # panel that advertises {DJ} teaches operators a token that vanishes.
    "voicemail_greeting": dict(group="voicemail", kind="text", label="Greeting",
        placeholder="derived: “You've reached {station}. {dj} is on the air "
                    "right now — leave a request after the beep.”",
        help="Spoken in the on-air DJ's own voice, so it is staged ahead of "
             "time below. {station}, {dj} and {show} are filled per persona; "
             "with nobody on air the machine answers as the station. Changing "
             "this re-renders every clip on the next staging run."),
    "voicemail_max_seconds": dict(group="voicemail", kind="number",
        label="Message ceiling", unit="sec", alias="duration length",
        help="The hard stop on one message. STT runs for at most this long, "
             "which is what makes voicemail cheap to leave wide open."),
    "voicemail_flow": dict(group="voicemail", kind="select",
        label="What a message is, by default",
        help="Only matters while the ON AIR | OFF AIR switch is NOT on the "
             "card. With it up the caller chooses: OFF AIR is the machine (a "
             "private message, no audio kept), ON AIR the soundbite studio "
             "(record, review, aired with the DJ around it, audio deleted "
             "once aired)."),
    # vm_mixer_telnet and vm_air_base_url deliberately have no schema entry —
    # the station_mcp_url ruling (0.10.80, operator's) applied again on
    # 2026-08-17, the operator's own words: "if it's derived couldn't we just
    # remove it". Both derive correctly on any ordinary deployment
    # (broadcast:1234; http://HOST_IP:8100 — the probe-proven URL), and the
    # rare exception overrides them in settings.json or the environment.
    "vm_air_backend": dict(group="airdoors", kind="select",
        label="A soundbite airs as",
        help="'The DJ reads it' works anywhere. 'The caller's own voice' "
             "plays the recording on the station's voice channel, levelled — "
             "needs this container on the station's docker network; without "
             "it, it falls back to the DJ reading and the receipt says so. "
             "See the On Air docs."),
    "voicemail_destination": dict(group="voicemail", kind="select", label="Messages go",
        help="'Held for you' is the safe default — messages land below; "
             "nothing reaches the air without you. The rest act on the "
             "station and need its admin credentials. 'Triage' reads each "
             "message with the configured model and picks one action within "
             "the permissions above."),

    "record_calls": dict(group="record", kind="check", label="Keep transcripts", alias="history logs recording archive",
        help="Both sides of every conversation — calls, texts, voicemail — "
             "with the tools used and the settings it ran under, written to "
             "data/calls. How a bad conversation gets diagnosed, and also a "
             "stranger's words on your disk."),
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
    "caller_cooldown_secs": dict(group="usage", kind="number", label="Redial wait time", unit="sec", alias="rate limit throttle spam",
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
        help="The receipt card a station action leaves in the transcript. "
             "After the DJ's line reads as paperwork; as-it-happens fires the "
             "moment the tool does; off leaves the DJ's word as the only "
             "trace, though the record still lists it."),
    "style_signoff": dict(group="closing", kind="text", label="Signing off",
        placeholder="default: in character, no fixed formula",
        help="e.g. 'mention what's coming up next before you hang up'."),

    # --- back to air ---
    "callback_enabled": dict(group="callback", kind="check", admin=True,
        label="Mention the call on air",
        help="One passing line between tracks AFTER the caller hangs up, "
             "re-voiced by the station in the persona. This is the whole "
             "section — not the announcements or segments a caller triggers "
             "mid-call, which live under [Caller permissions](#perms)."),
    "callback_max_words": dict(group="callback", kind="number", label="Length", unit="words",
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
        help="The names of the station's OTHER shows, so \"what's on after "
             "this?\" gets an answer. Off by default: prompt weight on every "
             "turn for a rare question. Rides along while show takeovers are "
             "allowed — a DJ who can switch shows must recognise their names."),

    # --- sounds ---
    "call_sounds": dict(group="sounds", kind="check", label="Play call sounds",
        help="Ringing, the line picking up, a hold click when the DJ steps onto "
             "the broadcast, hang-up, and an engaged tone."),
    "sound_pack": dict(group="sounds", kind="select", label="Sound set",
        needs=("call_sounds", True),
        help="All generated in the browser — no files needed. 'Exchange' is the "
             "telephone network, 'Handset' a physical phone in a room, 'Arcade' "
             "an 8-bit cabinet, 'Starship' a hailing console. Changing it plays "
             "the new set's ring."),
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
        help="The beep before recording starts — the one sound the SERVER "
             "plays rather than the card, so it must be a .wav the server can "
             "read. Deliberately not governed by Play call sounds: it tells "
             "the caller to start talking. Unplayable falls back to the tone."),
    # Worker-played like the beep above, so it ignores "Play call sounds"
    # too — and it is deliberately NOT a seventh board slot: the six moments
    # are one-shot card sounds, this is a loop the booth plays, and
    # panel-sounds.py's SOUND_SLOTS assumes exactly six. The slot-menu
    # treatment is recorded as the follow-up if the experiment earns it.
    "sound_thinking": dict(group="sounds", kind="text", label="Thinking sound",
        placeholder="default: silence",
        help="Booth texture while the DJ works mid-call — looped only "
             "between hearing and speaking, on its own track so it can "
             "never leak on air. A file path the WORKER can read (e.g. "
             "/data/sounds/thinking.mp3), not a URL. A missing file costs "
             "a diagnostics note, never the call."),
    "call_volume": dict(group="sounds", kind="number", label="Default volume", unit="%", alias="loudness",
        needs=("call_sounds", True), help="Starting playback volume for a call."),
    "ring_cut_at_pickup": dict(group="sounds", kind="check",
        needs=("call_sounds", True),
        label="Ring yields at pickup",
        help="The ring fades out the moment the DJ answers — how a phone "
             "behaves, and recommended. Off lets a long ring or jingle finish "
             "underneath the DJ's hello. Short one-shots are never cut either "
             "way."),
}


# Sentinel value for persona_override: roll a different DJ from the roster on
# every call, rather than pinning one. Lives here so the worker and the panel
# agree on the spelling.
RANDOM_PERSONA = "__random__"


# Choices for the select fields that aren't populated from a live source.
STATIC_CHOICES = {
    "open_lines_source": [
        ("dj", "The DJ decides — invents a topic from tonight's show"),
        ("directions", "Targeted directions — a random named angle each time"),
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
        ("tape", "Turntable — the platter spins between calls"),
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
