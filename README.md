# Wave Talk

**Live voice call-ins for a [SUB/WAVE] AI radio station.** A listener presses one
button in the browser, has a real back-and-forth conversation with whoever is
live on air, and the DJ can act on the station mid-call — search the library,
queue a request, put a shoutout on the broadcast.

The call is not the station speaking: it's this sidecar's own realtime voice
agent wearing the live persona. The station only gets touched when the agent
decides to act, through an allowlisted tool surface.

Please note this was created with heavy use of AI. It is recommended to use it locally and only expose it externally if you know the risks and what you are doing.

<table>
<tr>
<td valign="top"><img src="docs/call.png" width="430" alt="A live call: the DJ mid-sentence, level meters running, live captions — including a library search happening in-conversation" /></td>
<td valign="top"><img src="docs/settings.png" width="210" alt="The settings panel, folded: every section header summarises its own state" /></td>
</tr>
</table>

▶ **[Watch a real call (2 min)](docs/wavetalk-call.mp4)** — in-persona
pickup, back-and-forth, and a Beatles request resolved against the live
library.

```
[browser mic] --WebRTC--> [livekit-server] --> [agent-worker]
                                                STT -> LLM -> TTS
                                                     |
                                          SUB/WAVE MCP (allowlisted)
```

## Features

**The call**
- One call button; whoever is live on air answers, in persona, aware of the
  current show, the last few tracks, and what it just said on the broadcast.
- Full-duplex conversation with barge-in — talking over the DJ cuts it off,
  like a phone call, not a voice memo.
- Live captions for both sides, listening/thinking/speaking/on-air state,
  audio level meters, call timer with cutoff warning — every indicator driven
  by a real signal, nothing simulated.
- Successful caller actions appear as their own line in the transcript
  ("🎵 Song request scheduled"), tinted apart from speech. The DJ saying it
  did something is a claim; that line is the receipt.
- Two synthesized sound sets — telephone-exchange tones, or a physical
  handset with a real bell and the receiver going down — covering ring,
  pickup, hold, hang-up and an engaged tone when the booth can't take the
  call. Any one can be replaced with an uploaded file or a URL.
- Reconnect handling, and graceful in-character timeouts for silent callers
  and over-long calls. A caller who is thinking gets longer than one who has
  simply gone quiet — being asked a question buys them three times the wait
  before anyone checks on them.
- The DJ closes the call itself: it notices when one has run its course,
  checks whether there's anything else, and hangs up. Guarded in code against
  ending one early — nothing can hang up in the first minute, whatever the
  model decides.
- Nobody has to answer. If the worker is down or never dispatched, the caller
  gets an engaged tone after 40 seconds rather than ringing forever.
- Every call is written down — see [Diagnosing a call](#diagnosing-a-call).

**Station integration**
- Tools come from the station's own MCP server, filtered through an
  allowlist. Callers can trigger: song requests (with an optional
  confirm-before-send step), library search, exact queueing of a track they
  picked out of the results (off by default), on-air announcements, and
  station segments (weather, news, dedications). Track skipping, sound
  effects and station programming are never exposed, regardless of settings —
  and the panel lists all 17 of the station's tools with the status of each,
  so the boundary is visible rather than something you discover by toggling.
- The DJ knows what it's playing, not just its name: genre, mood tags, BPM
  and key come through when the station has analysed the track, along with
  the live listener count — so "what is this?" gets a real answer.
- A queued request comes back with its queue position, so the DJ can say
  "third up, about ten minutes" instead of implying it's on now.
- Everything is discovered, not hardcoded: personas, DJ/Show cards, voices,
  and model lists are read live from the station and providers. Point the
  sidecar at a different SUB/WAVE instance and it re-homes itself.
- After a call ends, the on-air DJ can mention it in one passing line
  (composed by the LLM, re-voiced by the station in persona).
- Overlap protection (on by default): the call DJ and the on-air DJ are the
  same person, so while the station has the microphone the caller's replies
  queue rather than talk over the broadcast. Nothing the caller said is lost —
  only the reply waits — and the card shows an **On air** state so the pause
  reads as the station being busy, not the DJ being broken.
- Every call is a first call. The back-to-air line about the previous caller
  is kept out of the next caller's prompt, so a new caller is never greeted
  as a continuation — and the last caller's business stays theirs.
- The caller's browser is (optionally) tuned into the stream once the DJ
  picks up (never during ringing), so stations that refuse requests at zero
  listeners accept them, and the broadcast runs quietly behind the call.
- Station webhooks are registered automatically for push updates; the widget
  falls back to polling when they're unavailable.

**Operator panel**
- Every runtime choice lives in a settings panel on the call page: station,
  LLM/STT/TTS providers and models, caller permissions, usage limits, call
  behaviour, house style. Changes apply to the next caller — no restarts.
- API keys are entered in the panel and stored server-side; key material
  never travels back to the browser. Blank fields mean "unchanged", clearing
  is explicit, values are never logged.
- Test buttons exercise the real code paths — green means the call will
  work, not "the URL responded". The pipeline check walks every leg in call
  order and its failure messages name the fix.

**Safety & limits**
- Two levels of password, and they must differ. **Admin** opens the settings
  panel, the keys and the test buttons. **Guest** is an optional code that
  opens only the phone — the Call button, on the page and on any embed — so
  you can put the widget somewhere public without handing out the controls.
  Admin works as a guest code too, so an operator carries one password. Both
  are stored as salted PBKDF2 hashes; guest attempts are rate-limited in
  their own bucket, so a caller fumbling the code can't lock you out.
- Usage controls: max concurrent calls, calls per hour, calls per day, a
  per-caller redial cooldown, a cap on how many actions one call can set in
  motion, and a pause switch that closes the line at once. Refusals are
  phrased in-world ("the booth line is tied up"), never error codes, and are
  answered with an engaged tone rather than silence.
- Speech hygiene applied to every line on its way to the voice, whatever the
  model does: stage directions (*shuffles records*, (laughs), [pause]) are
  stripped, expletives masked/removed/allowed per your policy.
- The caller is treated as an untrusted stranger: the prompt says so, the
  tool allowlist enforces it, and cross-origin writes to the settings API
  are refused.

## Architecture

| Component | What it does |
|---|---|
| `livekit-server` | WebRTC media — mic in, DJ audio out, one room per call |
| `agent-worker` | LiveKit Agents worker: resolves the live persona, builds the prompt, runs the STT → LLM → TTS session with MCP tools attached |
| `token-server` | Mints join tokens (the browser never sees LiveKit secrets), serves the widget + settings panel, proxies station reads, runs tests |
| `web-widget` | The call page — full page with settings, or a compact embeddable card |

Inside the worker, one call is one `CallSession`. Everything it needs lives
under `agent-worker/call/`, each file named after its job:

| Module | Owns |
|---|---|
| `session.py` | What a call knows about itself, in the order the caller experiences it: `prepare()` (heard as ringing) → `start()` (the DJ is on the line) → `greet()` |
| `lifecycle.py` | One function per behaviour attached to a live session: dead-air recovery, transcript logging, the idle check-in, the time limit, the greeting, the on-air handoff |
| `tools/registry.py` | The station's whole tool surface described **once** — name, what unlocks it, whether MCP or one of our wrappers serves it. The allowlist and the panel's reference both derive from it |
| `tools/music.py`, `tools/broadcast.py`, `tools/control.py` | The wrappers themselves: the library and queue, anything that makes the on-air DJ speak, and ending the call |
| `providers.py` | Which engine listens, thinks and speaks |
| `air.py` | Whether the broadcast currently has the microphone — read by the reply gate, the on-air tools and the widget's status chip, so they can't disagree |
| `actions.py`, `record.py`, `hangup.py`, `background.py` | The per-call action ledger, the written record, ending a call the same way from all three places that do, and fire-and-forget tasks that survive |

`main.py` is wiring: connect, refuse probe rooms, run the three phases.
Adding a tool is one entry in `registry.TOOLS` plus one function.

**Providers** are pluggable per leg: LLM (OpenAI, Google, Anthropic,
OpenRouter, Ollama), STT (Deepgram, OpenAI, Google, or in-process
faster-whisper on CPU — no key, no network), TTS (any OpenAI-compatible
endpoint, local or cloud, described by a small JSON adapter config — a new
backend is a config file, not code).

**Performance posture**: the station's slow lazy-cache endpoint is kept warm
by a background ping; per-call reads are one concurrent snapshot; a
last-known-good persona cache covers station hiccups; the system prompt is
budgeted (~1.8k tokens) because every token is paid on time-to-first-token
every turn. Local TTS/STT are supported but measured honestly — the pipeline
check reports realtime factors and warns when a backend can't keep up with
live playback.

## Getting started

### Docker (recommended)

Images are published by CI: `ghcr.io/mrainone7p/wave-talk` — `:latest`
tracks `main`, version tags (`:0.9.0`) come from git tags. The image is
self-contained (widget included); a deploy needs four things:

```
wave-talk/
├── docker-compose.yaml    # from this repo
├── .env                   # from .env.example — REQUIRED, see below
├── livekit.yaml           # from livekit.example.yaml, with a fresh secret
└── data/                  # panel settings & keys persist here
```

```bash
cp .env.example .env
cp livekit.example.yaml livekit.yaml   # generate a fresh secret for it
docker compose up -d
```

**Set `HOST_IP`** (in `.env` or your stack GUI's environment panel) to the
LAN address of the docker host — the one deployment variable; it drives
LiveKit's advertised media address, the browser URL and the webhook
callback. Beyond that, `.env` only genuinely needs the LiveKit keypair
(matching `livekit.yaml`) — everything else can be set in the panel.

**Open `https://<HOST_IP>:8443`** — the bundled Caddy TLS front door.
Browsers only allow the microphone on HTTPS origins; the first visit shows
a one-time self-signed-certificate screen (Advanced → Proceed), then the
normal mic permission popup. Add an API key in the panel, run the pipeline
check, press Call. Plain `http://<HOST_IP>:8100` works for everything
except placing calls.

### Local, no Docker (Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r agent-worker\requirements.txt
# put livekit-server.exe in bin\  (github.com/livekit/livekit/releases)
copy .env.example .env
copy livekit.example.yaml livekit.yaml
.\run-local.ps1        # stop with .\run-local.ps1 -Stop
```

## Settings reference

Everything lives in the panel behind the gear on the call page (password
prompt once one is set; the login persists on that browser until Sign out).
Changes apply to the **next caller** — no restarts. Precedence: panel →
`.env` → built-in defaults; clearing a field falls through to the layer
below. Every field carries its own help text in the panel; this is the map:

Sections are grouped by the job you're doing, in the order you'd do it:

| Group | Section | What it controls |
|---|---|---|
| Access | **Passwords** | Admin (the controls) and the optional guest code (the phone). They must differ; `CALLIN_ADMIN_KEY` env is the recovery override |
| Connect | **Station** | Which SUB/WAVE this answers for (everything else is discovered from it), the MCP endpoint, and the station admin credentials — with save/test buttons |
| Connect | **API keys** | Provider keys (OpenAI, Google, Anthropic, OpenRouter, Deepgram, TTS), stored server-side, never shown back |
| Models & voice | **Brains** | LLM provider/model (lists read live from each provider) and speech-to-text. A local Whisper is baked in and used by default — no key, no extra service — so this needs nothing set to work |
| Models & voice | **Voice** | TTS backend (cloud/local), server URL, voice (default: mirrored per-persona from the station), adapter config |
| Permissions & safety | **Caller permissions** | What a stranger on the line may trigger: requests, library search, exact queueing of a track they picked, announcements, running segments and (separately) whether the DJ may offer one — plus whether a mood request comes back with options first, and on-air overlap protection |
| Permissions & safety | **Usage controls** | Concurrent calls, calls per hour and per day, per-caller redial wait, actions per call, and the pause switch — the guard on API spend |
| Permissions & safety | **Speech hygiene** | Stage-direction stripping and the expletive filter, applied to every spoken line regardless of model |
| Call settings | **Call behaviour** | Who answers (live DJ / a pinned persona / random each call), greeting style, time limits, idle check-ins, tuning the caller into the stream |
| Call settings | **Station awareness** | How much live context (recent tracks, queue, on-air chatter, the rest of the line-up) the DJ carries — each item costs latency every turn |
| Call settings | **House style** | Light steers on conversation, answering and sign-off, layered on the persona; prompt preview with token budget |
| Call settings | **Back to air** | The one-line on-air mention after a call ends |
| Call settings | **Call sounds** | The sound set, per-sound uploads or URLs, previews, default volume |
| Reference | **What callers can ask** | Live reference derived from the permissions above — including what is never available, and why |
| Reference | **Station tools** | All 17 tools the station publishes over MCP, what each does, and whether a caller can reach it. Follows the permission switches as you flip them |
| Reference | **Embed** | Copyable iframe snippet + compact preview |

Below the settings, a **Diagnostics** block of four collapsed rows, each with
its own run button: full pipeline check, speed test, recent calls and server
logs. See [Diagnosing a call](#diagnosing-a-call). The running version is
stamped underneath.

## Embedding

```html
<div id="subwave-callin"></div>
<script src="https://your-host/embed.js"></script>
```

Renders the compact card in an iframe with `allow="microphone"` set (its
absence is the classic silent embed failure). The settings panel never
ships inside an embed. Optional attributes:

| Attribute | Effect |
|---|---|
| `data-theme="light\|dark\|inherit"` | `light`/`dark` force a theme (and hide the widget's toggle). `inherit` reads the host page's own background and matches it — a cross-origin frame can't see the page it sits in, so this is resolved by `embed.js` before the frame loads. Omit for auto: viewer's OS preference + in-widget toggle |
| `data-captions="ticker\|full\|off"` | Embeds default to `ticker` — only the latest spoken line, fading after a few seconds, so the widget stays short. `full` restores the scrolling transcript |
| `data-height="260px"` | Frame height for tight layouts |
| `data-compact="false"` | Full card instead of the compact one |
| `data-origin` | Widget origin when the script is served from elsewhere |

## Security

**Two passwords, two jobs.** Both live in the panel under *Access →
Passwords*, and the store refuses to let them be the same.

**Admin** protects the panel, API keys and test buttons. Whoever holds it
controls the application and can spend your API keys. The login persists per
browser until Sign out. Until one is set, the panel shows a standing nudge
and stays open — fine on a trusted LAN, a choice you should make
deliberately.

**Guest** is optional and protects only the phone: the Call button, `/token`,
and every embed. Set one when the page is reachable from the internet and you
want only the people you gave the code to ringing the booth. There's no
username — the code is the whole thing. Admin is accepted as a guest code
too, so an operator carries one password, never two. Leave it empty and
anyone who can load the page can call (the usage limits are then the only
guard).

Both are stored as salted PBKDF2 hashes, never plaintext. Wrong-password
lockout: 5 failures per address → 5-minute cooldown; a second round → banned
until the app restarts. Guest failures are counted in their own bucket, so a
caller fumbling the code can never lock you out of the panel. Locked out
yourself? Set `CALLIN_ADMIN_KEY` in the environment (always accepted,
break-glass) or restart the app to clear bans.

Passwords travel with each request, so beyond your own LAN use the HTTPS
front door — over plain http they're readable on the wire.

Before exposing beyond your LAN:

1. `CALLIN_ALLOWED_ORIGINS` — set to your real origins (`*` lets any page
   read config endpoints and mint call tokens).
2. **Set the admin password**, and a **guest code** if the page is public
   (above).
3. Fresh LiveKit keypair; `use_external_ip: true` and the UDP range open
   for off-LAN callers.
4. Real TLS on the front door (a proper certificate instead of the
   self-signed one, e.g. via your own domain) so visitors see no warnings.
5. Keep usage limits non-zero — every call spends real API money. On a public
   page set **calls per day** and **actions per call** as well as the hourly
   limit: an hourly cap alone still permits 24× that in a day.
6. Know what's plaintext: `data/secrets.json` holds API keys unencrypted
   (0600 where the OS honours it). Protect the volume; never commit `.env`
   or `data/`.

## Troubleshooting

Run the **full pipeline check** first — it walks every leg in call order and
its failure messages name the fix. The classics:

- **Call hangs at "Ringing" while every server check passes** — LiveKit in
  docker is advertising its container IP as the media address. Set `HOST_IP`
  (which feeds `--node-ip`) and recreate the livekit container; the same
  cause shows up as webhooks registered to a `172.x` address. The *Browser
  media path* stage exists precisely for this. If it still fails, check the
  host firewall allows UDP 50000–50100 and TCP 7881.
- **"This page can't use the microphone"** — the page is on a plain
  `http://<lan-ip>` origin, where browsers refuse microphone capture
  (localhost is exempt, which is why local dev works). The widget shows this
  guidance itself, with a link to the bundled TLS page
  (`https://<HOST_IP>:8443`) — or use the Chrome flag for single-machine
  testing. The *Microphone* pipeline stage reports the same thing.
- **Locked out of the settings panel** — set `CALLIN_ADMIN_KEY` in the
  environment (always accepted) or restart the app (clears IP bans). To
  remove the password entirely, delete `data/admin-auth.json` and restart.
- **Station admin returns 429** — the station's login rate limiter, usually
  after repeated credential tests. Wait ~15 minutes (or restart the station
  container); it does not mean the credentials are wrong. The *Test admin
  access* button distinguishes the two.
- **Voice test 400s on a local TTS backend** — the voice id doesn't exist on
  that server (cloud names and local sample ids aren't interchangeable), or
  no voice is configured anywhere; *Reload voice list* after switching
  backend. With station admin credentials set, per-persona voices mirror
  from the station automatically.
- **Calls work on the LAN but not from outside** — signalling rides your
  reverse proxy, but *media* is a direct UDP connection to the address
  LiveKit advertises, which by default is your LAN IP. For outside callers:
  set `use_external_ip: true` in `livekit.yaml`, run the livekit service
  with `network_mode: host` (drop its `ports:` and `--node-ip`; point
  `LIVEKIT_URL` at the host's LAN IP), and forward **UDP 50000–50100** and
  **TCP 7881** on your router. Related: Chrome may ask LAN visitors to
  "connect to devices on your local network" — that's the browser's Private
  Network Access guard for a public page reaching a private IP; one-time
  and harmless.
- **Audio gaps on local TTS** — generation slower than playback. The voice
  test reports the realtime factor; above ~1.0, lower your TTS engine's
  inference steps or use cloud for the live leg.

## Diagnosing a call

**Start with Recent calls**, under *Diagnostics* at the bottom of the panel.
Each call writes one file as it ends — both sides of the conversation, every
tool the DJ used with its result, the config it ran under, and anything that
failed — rendered as one timeline:

```
2026-08-04 23:34:36  Dalia  ·  136s  ·  6 caller turns
  google/gemini-3.1-flash-lite  stt=local/base.en  tts=local
  ⚠ station 503 on /request
23:34:45 DJ      You're through to the booth — what's on your mind?
23:34:48 CALLER  Can you play something fun?
23:34:49   tool  subwave_request_song → Added to the queue
```

That last column is the point: the DJ *saying* it did something is a claim,
the tool line is the receipt. The config line ties a bad call to the setting
that caused it. Files live in `data/calls/`, newest 40 kept.

The other three diagnostics rows: **Full pipeline check** walks every leg in
call order and names the first thing that would break; **Speed test** reports
time to first audio per leg (over ~1.5s to first token sounds laggy);
**Server logs** shows this service's recent activity.

## Logs & tests

Local runs write timestamped rotating logs to `data/logs/` (worker,
token-server, livekit). Under Docker the same lines go to container stdout
(`docker compose logs -f <stack>-wavetalk-worker-1`), where the worker logs
its version at startup and every call as `heard:` / `said:` / `tool:` lines.
`/health` reports the running version — check both containers match, since
they ship as one image but run as two.

```bash
cd agent-worker && python -m unittest test_sidecar
```

covers the speech filter, settings precedence, secrets and password handling,
the lockout ladder, usage limits, the tool registry, prompt assembly, the call
record and the call's lifecycle seams. CI runs it before building an image, so
a failing suite never reaches `:latest`.
