# Voicemail

Part of the [Talk Wave](../README.md) documentation.

**Status: built as of 0.9.127**, to this design plus the operator's additions: its own
settings section, greeting clips cached against exactly what they were rendered from
(text + voice + backend), the offer appearing wherever a live call is refused, and an
acknowledgement clip ("Got it — I'll pass that on.") staged alongside each greeting.
Barge-in (open question 3) is answered YES — STT is wired before the greeting plays.
Hold messages get their own list (question 2), capped at 200 — AND each message writes a
call entry (`kind: voicemail`) so Recent calls shows the whole night in one place,
labelled as the machine's. The guest door code applies to voicemail exactly as it does
to live calls: the code buys the line, whichever way the line answers. The card tells
the caller, at the beep, that only the transcript is kept. The worker leg has unit
coverage but has **not yet answered a live call on a deployed stack** — leave
`voicemail_when` on never until one test message has gone through end to end.

## What it is

A second, much smaller kind of call. The line rings, the DJ picks up with an answering-machine
message in their own voice, there is a beep, the caller says one thing, and it goes in. No
conversation, no tools, no turn-taking — the whole interaction is one caller utterance long.

```
ring  →  "You've reached Yosemite FM. Francesca's on the air right now —
          leave a request after the beep."   ← the live DJ's voice
      →  BEEP
      →  caller speaks (30s ceiling)
      →  silence detected, or the ceiling hits
      →  "Got it — I'll pass that on."  →  hang up
      →  the message goes to the station as a request or an on-air line
```

**Nothing is recorded.** This was the operator's own correction to the original sketch and it
is what makes the feature small: the caller's audio goes through the STT that is already
running, and what is kept is the transcript, which is the only part anything downstream can
act on. There is no audio file, no player in the panel, no retention policy for a stranger's
voice, and no new privacy surface beyond the call transcripts that already exist.

## Why it is worth building

Every refusal is currently silence. Nobody on air, out of hours, three callers already on,
over the daily cap — the card says "Nobody to call" and that is the end of it. The person who
wanted to hear a song still wants to hear it. Voicemail turns each of those into something the
station receives. (The one refusal it does NOT answer through is Pause all calls: the kill
switch closes the whole line, machine included.)

It is also, unlike a live call, **cheap and bounded**: one STT leg, one pre-rendered greeting,
no LLM turn on the critical path, and a hard 30-second ceiling. A deployment nervous about API
spend can run voicemail wide open and live calls behind a guest code.

## The pieces

### 1. Staged greetings — the one genuinely new mechanism

The greeting must be in the voice of whoever is on air, and it must play **immediately**. Local
VibeVoice is slower than realtime on this deployment, so generating it at pickup would put a
multi-second gap exactly where a caller expects a recording to start instantly.

So it is rendered ahead of time, once per persona, and cached:

```
data/voicemail/
  p_ccc394.wav        one per persona id
  p_default2.wav
  index.json          { persona_id: {text, voice, renderedAt, sha} }
```

A **"Stage voicemail greetings"** button in the panel walks the station's persona roster, renders
each greeting through the configured TTS, and writes the clips. It reports per-persona success,
because a persona configured with an ElevenLabs voice id will 400 on local VibeVoice and the
operator needs to know which one — that has already happened once with Rosie.

`index.json` holds the text and voice each clip was rendered from, so the stage job can skip
what has not changed and re-render what has. Changing the greeting text, or a persona's voice,
invalidates only that entry.

Fallbacks, in order: the persona's clip → any staged clip → live TTS at pickup → the beep alone.
A missing clip must never mean a silent pickup.

### 2. The beep

Synthesized in the browser, like the existing ring and hangup sounds — `shared.js` already has
the sound engine and `sound_pack` already has "classic" and "phone". A 1kHz sine, 400ms, is the
whole of it. It is a widget-side sound, not a TTS artefact, so it costs nothing and cannot fail.

### 3. Capturing the message

One STT session, no agent, no LLM. It ends on whichever comes first:

- endpointing says the caller has stopped (the same `min/max_endpointing_delay` the call uses),
- 30 seconds (`voicemail_max_seconds`),
- the caller hangs up — in which case whatever was transcribed still counts.

An empty transcript is a hang-up, not a message: nothing is sent and nothing is logged beyond
the fact that the line rang.

### 4. Where the message goes

The operator picks, per deployment:

| Mode | What happens |
|---|---|
| `request` | The text goes to `station.submit_request()` — the same path a live caller's request takes, including the station's own rate limits |
| `air` | The text is passed to the on-air DJ as something to mention (`dj_say`, `kind="voicemail"`) — a dedication, a shout, a riff |
| `hold` | Nothing is sent. It lands in the panel for the operator to read and act on |

`hold` is the safe default. `air` puts a stranger's words in front of every listener and belongs
behind the same deliberation as `allow_announcements`, which is off by default for exactly that
reason. **`air` and `request` both need the station's admin credentials** and should carry the
same Station admin tag the permission switches now carry.

One message is one action, and it counts against `max_actions_per_call` in the same way.

### 5. When voicemail answers instead of a live call

`voicemail_when`, a select, because these are genuinely different policies and an operator
should have to choose one:

- `never` — the default; nothing changes for anyone upgrading
- `closed` — only when a live call is impossible: nobody on air, live calls switched off, or
  all concurrent slots taken. Not when the line is paused — the kill switch closes the machine
  too — and not past the caps, which refuse both kinds of call
- `always` — the line is voicemail-only, which is the cheap way to run this at all

The widget already knows all four "closed" conditions — it is what paints "Line closed" and
"Nobody to call" today — so the Call button becomes **Leave a message** rather than being
disabled. That is the whole user-facing change on the card.

## What it costs

| | Live call | Voicemail |
|---|---|---|
| LLM turns | one per exchange | none |
| TTS | every DJ line | one pre-rendered clip |
| STT | whole call | ≤30s |
| Ceiling | `max_call_seconds` (600) | 30s, hard |

## Settings this adds

| Field | Default | Group |
|---|---|---|
| `voicemail_when` | `never` | Call behaviour |
| `voicemail_greeting` | blank → derived from station and persona name | Call behaviour |
| `voicemail_max_seconds` | 30 | Call behaviour |
| `voicemail_destination` | `hold` | Call behaviour |

The derived greeting is *"You've reached {station}. {DJ} is on the air right now — leave a
request after the beep."* Blank means derived, per the settings invariant; a typed greeting
replaces it and the staging job re-renders every clip. Typed greetings and per-persona
overrides may use `{station}`, `{dj}` and `{show}` — filled at render time, with an empty or
unknown placeholder simply disappearing rather than crashing a pickup into the beep.

Two later decisions, both from operating it:

- **The station answers when nobody is on air.** The staging run also renders a station-level
  clip ("You've reached {station}…") in the operator's default voice, and the pickup fallback
  order is: this persona's clip → the station clip → any clip → the beep. A named DJ who is
  not actually there is a small lie the caller can hear.
- **`voicemail_greeting_mode`** chooses `staged` (instant, the default) or `fresh` — one model
  line written in the persona's own voice at pickup, budgeted at six seconds, with the staged
  clip as the backup for a model or TTS that cannot make it in time.
- **The ceiling closes the ROOM, not just the job.** `ctx.shutdown()` alone ends the agent and
  leaves the caller connected to an empty room with the timer counting — which an operator read,
  correctly, as the 30-second limit not being honored. The leg deletes the room the way a live
  call ends, and the card's timer counts against `voicemail_max_seconds` rather than the live
  call's limit.
- **The machine records what the caller sends, and push-to-talk applies to voicemail like any
  call** (the default — it follows the same per-surface switch). The card shows the talk bar the
  instant "Leave a message" is pressed, so there is always a mic control: a **tap latches** the
  mic open and the caller leaves a message exactly like an open mic, **holding** is momentary,
  and STT is wired before the greeting so their first words over the beep are kept. Only a card
  with push-to-talk switched off keeps the historic open mic from pickup. This is the reconciled
  answer to two opposite reports on the same day — "hold the bar" on a card that had *no visible
  bar* (mic shut, empty message), then "there is no way to talk, MIC OFF" once the bar was
  removed entirely. The bar is present AND the mic is push-to-talk; the tap-latch is what keeps a
  confused caller from leaving silence. The worker still announces the beep over the data channel
  (`vm-beep` topic); the widget uses it to flip the status line to "recording". The quiet clock
  restarts at the beep: it used to run from before the greeting, so the nobody-spoke window was
  spent before the caller could start, and the machine hung up almost the moment it beeped.
- **`sound_vm_beep`** replaces the synthesized beep with an uploaded WAV (Call sounds section).
  Server-played, so uploads only; wrong rate or shape falls back to the tone, never silence.

## Where the code would go

Deliberately **not** inside `call/session.py`. A voicemail is not a degraded call — it has no
agent, no tools, no `OnAirGuard`, no idle ladder — and threading a mode flag through the call
object would put a branch in every one of those.

```
agent-worker/
  voicemail/
    __init__.py
    greetings.py     staging: render one clip per persona, cache, invalidate
    capture.py       the STT-only leg and its ceiling
    deliver.py       request | air | hold
  api/
    voicemail.py     POST /voicemail/stage (admin), GET /voicemail/status
```

`main.py` routes a job to the voicemail handler or the call handler on the room name prefix —
`vm-` rather than `callin-` — which is decided when the token is minted and therefore already
known before anything connects.

## Open questions

1. **Does the station want a `kind` for this?** `dj_say(kind="callin")` exists; a
   `kind="voicemail"` would let the station's own prompt treat it differently ("we got a message
   earlier…"). Worth asking upstream rather than guessing.
2. **Should `hold` messages appear in the call transcripts, or their own list?** They are one
   line each, so a separate `data/voicemail/messages/` reads better than 40 near-empty call
   records — but that is a second thing for the panel to show.
3. **Barge-in on the greeting.** A caller who knows the message will talk over it. Allowing that
   means running STT during the greeting, which is most of the cost of not having a ceiling.
   Probably yes, but it is not free.
