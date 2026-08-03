# SUB/WAVE Call-In Sidecar

Live voice call-ins for a [SUB/WAVE] AI radio station. A listener presses one
button in the browser, has a real back-and-forth conversation with whoever is
live on air, and the DJ can act on the station mid-call — search the library,
queue a request, put a shoutout on the broadcast.

The call is not the station speaking: it's this sidecar's own realtime voice
agent wearing the live persona. The station only gets touched when the agent
decides to act, through an allowlisted tool surface.

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
- Live captions for both sides, listening/thinking/speaking state, audio
  level meters, call timer with cutoff warning — every indicator driven by a
  real signal, nothing simulated.
- Ring / pickup / hang-up sounds (synthesized in-browser, replaceable with
  your own files), reconnect handling, graceful in-character timeouts for
  silent callers and over-long calls.

**Station integration**
- Tools come from the station's own MCP server, filtered through an
  allowlist. Callers can trigger: song requests (with an optional
  confirm-before-send step), library search, on-air announcements, and
  station segments (weather, news, dedications). Destructive tools —
  skip, direct queueing, SFX, programming — are never exposed, regardless
  of settings.
- Everything is discovered, not hardcoded: personas, DJ/Show cards, voices,
  and model lists are read live from the station and providers. Point the
  sidecar at a different SUB/WAVE instance and it re-homes itself.
- After a call ends, the on-air DJ can mention it in one passing line
  (composed by the LLM, re-voiced by the station in persona).
- Overlap protection: on-air actions wait for the broadcast to go quiet, and
  the DJ steps back from the call while its own voice is on air.
- The caller's browser is (optionally) tuned into the stream during the
  call, so stations that refuse requests at zero listeners accept them.
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
- Usage controls: max concurrent calls, calls per hour, per-caller redial
  cooldown. Refusals are phrased in-world ("all the lines are busy"), never
  error codes.
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

| Section | What it controls |
|---|---|
| **Station** | Which SUB/WAVE this answers for (everything else is discovered from it), the MCP endpoint, and the station admin credentials — with save/test buttons |
| **Security** | The panel password: set/change, sign out. `CALLIN_ADMIN_KEY` env is the recovery override |
| **API keys** | Provider keys (OpenAI, Google, Anthropic, OpenRouter, Deepgram, TTS), stored server-side, never shown back |
| **Brains** | LLM provider/model (lists read live from each provider) and speech-to-text, incl. the in-process local Whisper |
| **Voice** | TTS backend (cloud/local), server URL, voice (default: mirrored per-persona from the station), adapter config |
| **Caller permissions** | What a stranger on the line may trigger: requests, library search, announcements, station segments — plus on-air overlap protection |
| **Usage controls** | Concurrent calls, calls per hour, per-caller redial wait — the guard on API spend |
| **Speech hygiene** | Stage-direction stripping and the expletive filter, applied to every spoken line regardless of model |
| **Call behaviour** | Persona pinning, greeting style, time limits, idle check-ins, tuning the caller into the stream |
| **Station awareness** | How much live context (recent tracks, queue, on-air chatter) the DJ carries — each item costs latency every turn |
| **House style** | Light steers on answering/sign-off, layered on the persona; prompt preview with token budget |
| **Back to air** | The one-line on-air mention after a call ends |
| **Call sounds** | Ring/pickup/hang-up tones, custom files, default volume |
| **What callers can ask** | Live reference derived from the permissions above |
| **Embed** | Copyable iframe snippet + compact preview |

At the bottom: **Run full pipeline check** (11 stages in call order — each
failure message names its fix) and **Speed test** (per-turn latency
breakdown), with the running version stamped underneath.

## Embedding

```html
<div id="subwave-callin"></div>
<script src="https://your-host/embed.js"></script>
```

Renders the compact card in an iframe with `allow="microphone"` set (its
absence is the classic silent embed failure). Optional attributes:
`data-origin`, `data-theme="light|dark"`, `data-compact="false"`. The
settings panel never ships inside an embed.

## Security

**Panel password.** Set one in the settings panel (Security section). It
protects the panel, API keys and test buttons — the call card and embed stay
public, guarded by the usage limits instead. The login persists per browser
until Sign out (Security section). Stored as a salted PBKDF2 hash.
Wrong-password lockout: 5 failures per address → 5-minute cooldown; a second
round → banned until the app restarts. Locked out yourself? Set
`CALLIN_ADMIN_KEY` in the environment (always accepted, break-glass) or
restart the app to clear bans. Until a password is set, the panel shows a
standing nudge and stays open — fine on a trusted LAN, a choice you should
make deliberately.

The password travels with each request, so beyond your own LAN use the
HTTPS front door — over plain http it's readable on the wire.

Before exposing beyond your LAN:

1. `CALLIN_ALLOWED_ORIGINS` — set to your real origins (`*` lets any page
   read config endpoints and mint call tokens).
2. **Set the panel password** (above).
3. Fresh LiveKit keypair; `use_external_ip: true` and the UDP range open
   for off-LAN callers.
4. Real TLS on the front door (a proper certificate instead of the
   self-signed one, e.g. via your own domain) so visitors see no warnings.
5. Keep usage limits non-zero — every call spends real API money.
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
- **Audio gaps on local TTS** — generation slower than playback. The voice
  test reports the realtime factor; above ~1.0, lower your TTS engine's
  inference steps or use cloud for the live leg.

## Logs & tests

Local runs write timestamped rotating logs to `data/logs/` (worker,
token-server, livekit). Under Docker the same lines go to container stdout
(`docker compose logs -f agent-worker`). `/health` reports the running
version.

```bash
cd agent-worker && python -m unittest test_sidecar
```

covers the speech filter, settings precedence, secrets and password
handling, the lockout ladder, usage limits, tool allowlists, and prompt
assembly.
