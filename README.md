# SUB/WAVE Call-In Sidecar

A listener presses one button, has a real back-and-forth conversation with
whoever is live on air, and the DJ can act on the real station mid-call.

The call is *not* SUB/WAVE speaking — it's this sidecar's own agent wearing the
live persona. The station only gets touched when the agent decides to act.

```
[browser mic] --WebRTC--> [livekit-server] --> [agent-worker]
                                                STT -> LLM -> TTS
                                                     |
                                          SUB/WAVE MCP (allowlisted)
```

## Run it locally

No Docker required — LiveKit ships a native binary for every platform.

First-time setup:

```powershell
# 1. Python env
python -m venv .venv
.\.venv\Scripts\pip install -r agent-worker\requirements.txt

# 2. LiveKit server binary -> bin\livekit-server.exe
#    https://github.com/livekit/livekit/releases (or `brew install livekit`)

# 3. Config
copy .env.example .env
copy livekit.example.yaml livekit.yaml   # then put a fresh secret in it
```

Then:

```powershell
.\run-local.ps1
```

Open <http://localhost:8100>, add an API key in the settings panel, run the
pipeline check, press Call. Stop with `.\run-local.ps1 -Stop`.

## It's a conversation, not a request form

One `ChatContext` lives for the whole call, so turn six knows what was said in
turn two. Silero VAD plus turn detection decide when the caller has finished a
thought — no push-to-talk. Barge-in is on, so talking over the DJ cuts it off
mid-sentence. Tools are things the DJ *does* during the conversation; the
caller can chat about the last track, drift into a story, and only later
mention what they want to hear.

Caller memory across calls is not in v1 — no `caller_id`, nothing persisted.
The schema is in `BUILD-INSTRUCTIONS.md` if it comes back.

## The settings panel

Open the gear on the full page (<http://localhost:8100>). Changes apply to the
**next caller** — no restart. Precedence:

```
settings panel  >  .env / compose  >  built-in defaults
```

Leaving a field on “Default” falls through to the layer below, so clearing is
how you revert. Stored overrides live in `data/settings.json` — only what you
actually changed is written.

Configurable there: which station, TTS backend + URL + voice + adapter, LLM
provider + model + URL + temperature, STT provider + model, which tools callers
can trigger, persona override, max call length, and the embed snippet.

**Assigning it to a station.** The Station section is what homes the sidecar.
Everything else — personas, DJ/Show cards, voices, tools — is discovered from
that URL, so pointing it at another SUB/WAVE instance re-homes the whole thing.
The MCP endpoint derives as `{station}/mcp` unless you override it. Admin
credentials stay in `.env`, not in the panel.

### API keys

Keys can be entered in the panel instead of editing `.env`. They're kept in
their own file (`data/secrets.json`), separate from `settings.json` so that
stays safe to copy or diff, and stored keys take precedence over `.env`.

How the masking works:

- The panel only ever receives *status* — set or not, where it came from, and
  the last four characters. **Key material never travels back to the browser.**
- Inputs are `password` type and start empty. **Blank means "leave unchanged"**,
  not "clear", so an untouched field can't wipe a working key. Clearing is a
  separate explicit button per key.
- After a save, inputs are emptied so nothing lingers in the DOM.
- Clearing reverts the process environment immediately, so a removed key stops
  being used right away rather than at the next restart.
- Only field names are ever logged, never values.

Tests use whatever is stored, so you can paste a key and immediately hit
*Test model* without restarting anything. When a test fails for want of a key,
the result offers a button that jumps straight to the right field.

### Model lists are discovered, not hardcoded

Each provider is asked what it actually offers, using the stored key:
OpenAI `/v1/models`, Google `/v1beta/models`, Anthropic `/v1/models`, Ollama
`/api/tags`. A baked-in list goes stale and produces confusing 404s on retired
models — which is exactly what happened with `gemini-2.0-flash`.

The curated lists in `settings.py` are only a fallback for before a key is
entered. The panel says which case you're looking at, and *Reload model lists*
re-reads them after adding a key.

Google's catalogue is filtered to text chat models — the raw list also contains
image, TTS, robotics, Lyria and research models that can't hold a conversation.

### Matching the station's model

`station_config.llm_config()` reads the station's own DJ model from
`GET /api/settings` and marks it in the model dropdown as *"same as the
station"*. This needs the station admin credentials; without them the panel
reports `source: unknown` rather than pretending to match.

Worth knowing before you pick it: if the station runs a large reasoning
model for its DJ, the GPU numbers below likely rule it out for live calls.
Matching the station exactly and answering a call in realtime can be, on
modest hardware, mutually exclusive.

One honest caveat: `data/secrets.json` is **plaintext on disk**, exactly like
`.env`. It's chmod 0600 where the OS honours that, which Windows largely
doesn't. This protects against keys leaking through the web UI, not against
someone who can already read the filesystem.

**Test buttons** exercise the same code paths a real call uses, so green means
the call will work rather than "the URL responded":

- *Test voice* — synthesizes a line, plays it back, and reports the realtime
  factor. Above 1.0 means playback will starve and gap.
- *Test model + tool calling* — reports time-to-first-token and whether the
  model **actually emitted a tool call**. Plenty of local models talk fluently
  about requesting a song and never call the tool; that shows up here rather
  than mid-call.
- *Test station + tools* — station reachability, who's live, and the exact tool
  list a caller gets.

The options payload is cached for 60s (it queries the station, TTS server and
Ollama); the reload buttons bypass the cache.

## What the call surface shows you

The card is meant to answer "what is it doing right now?" without reading logs.

**Station state is explicit.** Three distinct states rather than a blank card:
*On air now* (live DJ, avatar, show, current track), *Off air* (station answers,
nobody hosting — call button disabled), and *Station offline* (unreachable).

**During a call**, everything shown comes from a real signal:

| Indicator | Source |
|---|---|
| Listening / Thinking / Speaking chip | the `lk.agent.state` attribute the Agents SDK publishes |
| Caller level meter | WebAudio analyser on the published mic track |
| DJ level meter + avatar glow | WebAudio analyser on the subscribed DJ track |
| Live captions, both sides | the `lk.transcription` text stream |

Nothing is simulated — if the chip says *Thinking*, the agent is genuinely
between turns.

There's a volume slider (applies to DJ playback and the call sounds), and mute
greys out the caller meter so a muted mic can't look like a dead one.

**Call sounds** — ringback while connecting, a pickup blip when the DJ's audio
arrives, and a descending tone on hang-up. The defaults are synthesized in the
browser, so no audio files ship with the widget. Point *Ring* / *Pick up* /
*Hang up* at your own URLs in settings to replace them; each has a preview
button, and the whole lot can be switched off.

## When nobody is listening

The station refuses song requests while no one is tuned in — it answers with
*"The DJ's on autopilot — requests reopen when someone's tuned in."* A caller
on the line is engaged with the station but isn't pulling the audio stream, so
by default they don't count, which means **the people most likely to request
something were the ones who couldn't.**

Measured: fetching `/stream.mp3` moves `listeners.current` from 0 to 1
immediately. So the widget now tunes the caller's browser into the station for
the duration of the call (**Call behaviour → Tune the caller in**, on by
default, volume 0 so it stays silent behind the DJ). Requests then work.

Everything else works fine at zero listeners — conversation, library search,
on-air announcements and the back-to-air handoff are all unaffected. Only song
requests are gated.

The pipeline check reports the live listener count and warns when requests
would be refused.

## Back to air

After the caller hangs up, the DJ composes one short line about the call and
sends it to `/dj/say` in `styled` mode, so the station re-voices it in the
persona. It's a passing mention between tracks, not a recap — and it can answer
a question the caller asked about the DJ itself. A real example from testing:

> "That one was for a caller who wanted to slow the tempo down after a long
> haul—and honestly, after as many years as I've been parked in this booth,
> I feel that."

Configurable under **Back to air**: on/off, length in words, a minimum number
of caller turns (so a call that never got going isn't mentioned), and a
free-text steer added to the composing instruction. The model replies `SKIP`
when nothing was worth mentioning. Requires the station admin credentials.

## Admin credentials matter more than they look

Without `subwave_admin_user` / `subwave_admin_pass` (enter them under **API
keys**), the station refuses:

- `subwave_search_library` — so the DJ can't check whether a track exists
- `subwave_dj_announce` — so nothing can be put on air
- `/dj/say` — so the back-to-air handoff silently skips
- `/api/settings` — so persona voices fall back to the local file

The MCP connection sends them as a Basic `Authorization` header. Symptom when
missing: the DJ says it's "locked out of the controls" mid-call.


## Latency: where it actually goes

Measured, not guessed:

| Stage | Cost |
|---|---|
| Station `GET /dj` **cold** | **19.5s** (!) |
| Station `GET /dj` warm | ~16ms |
| Every other station read | ~20ms |
| LLM first token (Gemini flash-lite, warm) | ~500-700ms |
| LLM first token, cold | ~4s |
| TTS first audio (local VibeVoice) | ~2.5s |
| TTS realtime factor (local) | 0.97-1.66x |

**The dominant cost was not ours.** The station caches persona state lazily, so
the first `/dj` after a quiet spell took 19.5 seconds — and the caller hears all
of that as ringing. Worse, the station appears to serialise: while `/dj` was
cold, six *concurrent* reads all timed out behind it, so parallelism alone
couldn't help.

Three fixes, in order of impact:

1. **Keep-warm loop** (`token_server.keep_station_warm`) pings `/dj` every 45s.
   Observed absorbing a 9.0s cold read, after which calls see ~20ms. This is
   the one that matters.
2. **Last-known-good persona cache** — a slow or empty `/dj` falls back to the
   previous persona rather than degrading to a generic DJ with no character.
3. **Single concurrent snapshot** replaces six serial reads in the call path.

Remaining latency is mostly TTS first-audio (~2.5s local). Cloud TTS is the
lever there; the pipeline check reports the realtime factor either way.

## Connections: what's used, what isn't

The station exposes four surfaces. We use three:

| Surface | Used for | Status |
|---|---|---|
| **MCP** (17 tools) | everything the agent *does* mid-call | 9-11 allowlisted |
| **REST** (27 endpoints) | prompt context, avatars, `/dj/say` handoff | reads + one write |
| **Audio stream** | tuning the caller in so requests are accepted | in use |
| **Webhooks** | — | **not used yet** |

`GET /api/webhooks` reports the station can push `track.play`, `dj.say`,
`dj.link` and `request.received` to a URL we register, and none are registered.
That's the clearest remaining upgrade: the widget currently polls `/live` every
20 seconds, and `request.received` would let the DJ confirm a caller's request
landed *during* the call rather than hoping.

Newly wired as an opt-in permission: **skills** (37 station segments —
weather, news, dedications, story time, remembrance). They put audio on air
on a stranger's say-so, so they default to off.

Still deliberately never exposed: `skip_track`, `play_sfx`, `queue_track`,
`dj_segment`, `refresh_playlist`. (SFX was considered as a permission and
dropped — stingers on a caller's say-so add nothing to a call.)


## Speech-to-text with no container and no key

`stt_provider: local` runs faster-whisper (CTranslate2) **inside the worker
process** — no extra container, no API key, no network. CPU only, deliberately:
the GPU is fully committed to VibeVoice, so anything asking for VRAM makes both
slower.

Measured on a real 3.6s clip, `base.en` int8 on CPU:

```
model load (once, at startup)  7.3s
transcribe                     0.85s   = 0.23x realtime
result                         "Hey, you are on the air. What are we playing tonight?"
```

Model sizes: `tiny.en` (~75MB, fastest, weaker on names), `base.en` (~145MB,
the sensible default), `small.en` (~480MB), `medium.en`. The model downloads
once and is cached.

**Tradeoff:** it's a non-streaming recogniser, wrapped in LiveKit's
VAD-driven StreamAdapter. So there are no interim results — a caption appears
when you finish a sentence rather than word by word. That's the honest cost of
having no dependencies.

Two worker settings make this viable and are easy to miss:

- `num_idle_processes=1` — dev mode defaults to **0**, so `prewarm_fnc` never
  runs until a call arrives, and the first caller would sit through the model
  load mid-conversation.
- `initialize_process_timeout=180` — the 10s default kills the process during
  the model load and the worker never becomes ready.

## Keeping stage directions out of the audio

Models narrate themselves: *"\*Sound of shuffling through records.\*"*, "(laughs)",
"[pause]" — and the TTS reads it aloud, which instantly breaks the call. This is
handled in two places, because a prompt rule alone doesn't hold:

1. A prompt rule stating that everything written is spoken word for word.
2. `speech_filter.py`, which strips stage directions from every line on its way
   to synthesis, whatever the provider or model.

The same filter carries the expletive policy — mask (default), remove, or off,
with an optional custom word list. Both are word-boundary matched, and ordinary
parentheticals ("the set (which runs till two)") are left intact.

Settings → **House style** also has *View the assembled prompt*, which shows the
exact system prompt the next caller's DJ will receive.

## Widget and embed

Both the full page and the compact widget show the DJ's avatar, name, current
show, and live captions of both sides of the call. The compact variant drops
the tagline, track, and the entire settings panel — a host page can't expose
station controls by dropping in the widget.

```html
<div id="subwave-callin"></div>
<script src="http://localhost:8100/embed.js"></script>
```

`embed.js` renders an iframe and sets `allow="microphone"` for you — without it
getUserMedia fails silently, which is the single most common embed failure. The
widget origin must also not send `X-Frame-Options: DENY`. The snippet is
copyable from the settings panel, with a preview button.

## How it decides who answers

One call-in button, not one per DJ. Each call reads `GET /dj`, matches the name
back to `GET /personas` to recover the persona id, and uses that persona's
`soul` as the DJ Card and the active show's `topic` as the Show Card. Change
shows on the station and the next caller gets the new host.

## Don't duplicate the station

Two deliberate choices:

**Tools come from the station's MCP server**, not a re-wrapped REST client. It
already publishes 17 tools with schemas and descriptions. `station.py` keeps
only the reads used to build the prompt.

**A caller is an untrusted stranger driving the agent by voice**, so the tool
surface is allowlisted. Exposed: five reads, plus `request_song`,
`request_status`, `search_library`, `dj_announce`, `run_skill` (all but the
first toggleable in settings). Never exposed regardless of settings:
`skip_track`, `play_sfx`, `queue_track`, `dj_segment`, `refresh_playlist`.

Getting library search for free is why this was worth doing — the original plan
had `queue_exact_track` stubbed as impossible for v1.

**Persona voices** are read from the station's own config when
`SUBWAVE_ADMIN_USER`/`PASS` are set (`GET /api/settings`), falling back to
`persona-voices.json` otherwise. The panel tells you which is in effect.

## Hardware reality: VibeVoice and Ollama share one GPU

Measured on the NAS, and it shapes every model choice:

VibeVoice-Large-AWQ sits on `cuda` permanently and holds the card, leaving
Ollama a hard **~4.2GB ceiling** — the same 4.2GB appeared for every model
loaded, regardless of size.

| Condition | First token | Speed |
|---|---|---|
| a 14B reasoning model resident (13GB, spills to CPU) | 6.7s | 3.8 tok/s |
| `llama3.2` alone, 87% on GPU | **0.37s** | **27.3 tok/s** |

So a local LLM *is* viable for live calls, but only under that ceiling. A
14B reasoning model is doubly disqualified: it needs 13GB, and reasoning
models can spend tens of seconds emitting only `<think>` tokens with empty
`content`. Tool calling on `llama3.2` via Ollama is confirmed working.

### TTS

`tts_adapter.py` is a real `livekit.agents.tts.TTS` driven by a JSON config in
`tts-adapters/`, so a new backend is a config file, not code. It streams PCM.

The original `local-default.json` guessed a `POST /speak` + `voice_id`
contract. That endpoint doesn't exist — VibeVoice is *already* OpenAI-
compatible, so local and cloud are the same adapter with a different base URL.
The guessed file was deleted.

**Local VibeVoice cannot carry a live call**: first audio at 2.6s and it
generates at 1.16–1.66× realtime, so the buffer starves for the whole call.
It's not tuning — generation is slower than playback. It stays wired up because
it's the right voice for the `dj_announce` path, where the station synthesizes
off the realtime clock, and it's useful for testing.

Cloud TTS is the default for the live leg. The tradeoff: a stock voice won't
match the on-air timbre until voice cloning is done. Note that cloud voice ids
(`alloy`, `onyx`) and local sample ids (your own registered samples) are not
interchangeable — reload the voice list after switching backend.

## What's still needed

Enter these in the settings panel (API keys section) or in `.env`:

| Key | Why | Status |
|---|---|---|
| OpenAI | LLM + cloud TTS | **missing** |
| Deepgram | speech-to-text | **missing** — falls back to Google STT, which needs a GCP service account |
| Station admin user/pass | mirror the station's own voice config | optional |

**A call has now run end to end.** With an OpenAI key (STT) and a Google key
(LLM), the agent picked up as the live persona and opened with:

> "Evening. You're through to the late show. What's on your mind tonight?"

— in character as the live persona, naming the show it had read from the station moments
earlier. Persona resolution, card assembly, the MCP allowlist, LLM, TTS and
WebRTC are all confirmed working together, not just individually.

During that call LiveKit logged `flush audio emitter due to slow audio
generation` repeatedly — the engine independently reporting the VibeVoice
starvation predicted by the 1.16× realtime factor. Switch `tts_mode` to cloud
for a call without gaps.

Still unverified: caller-side speech (this machine has no microphone), and
conversation tuning — interruption sensitivity and pacing can only be judged
on a real call.

### If STT has no Deepgram key

`stt_provider` falls back to OpenAI (`gpt-4o-mini-transcribe`) when an OpenAI
key is present, since Google STT needs a GCP service account rather than a
plain API key. That fallback is what made the first end-to-end call possible.

## Notes

- **Mojibake repair.** Some station text is double-encoded (`—` arrives as
  `â€"`). `prompts.py` repairs it so the TTS doesn't read it aloud as noise.
- **One room per call**, so callers never land in each other's audio.
- `CALLIN_ALLOWED_ORIGINS=*` is dev-only. `CALLIN_ADMIN_KEY` (blank by default)
  requires `X-Admin-Key` on settings writes — set it before exposing this.

## Deploying with Docker

```bash
cp .env.example .env                     # fill in your station URL etc.
cp livekit.example.yaml livekit.yaml     # fresh keypair, use_external_ip: true
docker compose up -d                     # pulls ghcr.io/mrainone7p/wave-talk:latest
```

`docker compose up -d --build` builds from source instead of pulling. Images
are published by CI: `:latest` tracks `main`, `:beta` tracks a `beta` branch,
and `v*` git tags produce version tags. The image is fully self-contained
(widget included) — a deploy needs only the compose file, `.env`,
`livekit.yaml` and a `data/` directory.

Settings and API keys entered in the panel persist in `./data/` (mounted
into both containers — the worker and the panel must share it). The first
container start downloads the Whisper model if local STT is selected.

## Before exposing this beyond your LAN

Work through all of these — the defaults are deliberately open for local dev:

1. **`CALLIN_ALLOWED_ORIGINS`** — set to your real origins. `*` means any
   web page can read your config endpoints and mint call tokens.
2. **`CALLIN_ADMIN_KEY`** — set it. Settings/secrets writes then require the
   `X-Admin-Key` header, and the GET endpoints stop reflecting CORS to
   strangers.
3. **LiveKit keypair** — generate a fresh one in `livekit.yaml` + `.env`;
   never ship an example key. `use_external_ip: true`, open the UDP range,
   and put the browser-reachable `wss://` origin in `LIVEKIT_PUBLIC_URL`.
4. **Reverse proxy with TLS** in front of port 8100 (the mic requires a
   secure context on non-localhost origins anyway).
5. **Usage limits non-zero** (`max_concurrent_calls`, `calls_per_hour`,
   `caller_cooldown_secs`) — every call spends real API money.
6. **Know what's plaintext**: `data/secrets.json` holds API keys unencrypted
   (chmod 0600 where honoured). Protect the volume; never commit `.env` or
   `data/`.

## Logs

Local runs write timestamped rotating logs to `data/logs/` (`worker.log`,
`token-server.log`, `livekit.log`). Under docker, container stdout carries
the same lines (`docker compose logs -f agent-worker`); file logging is
disabled there via `LOG_TO_FILE=0` since it would only duplicate it. Watch a
live call with:

```bash
powershell -Command "Get-Content 'data\logs\worker.log' -Wait -Tail 20"
```
