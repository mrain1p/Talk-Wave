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
- Test buttons exercise the real code paths: synthesize a line and hear it,
  check the model actually emits tool calls, list the exact tools a caller
  gets, and a full pipeline check that walks every leg in call order and
  reports the first thing that would break — plus a per-turn speed test.
- A prompt preview shows the exact system prompt the next caller's DJ will
  receive, with a token budget readout.

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

**Configuration precedence**: settings panel → `.env` → built-in defaults.
Clearing a panel field falls through to the layer below. Panel state
persists in `data/settings.json`; keys in `data/secrets.json` (plaintext on
disk — see Security).

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
LAN address of the docker host — the one deployment variable. It drives
LiveKit's advertised media address, the URL browsers connect to, and the
station webhook callback.

**Open `https://<HOST_IP>:8443`** — the bundled Caddy serves the widget and
LiveKit signalling over TLS, because browsers only allow the microphone on
HTTPS origins. First visit shows the standard self-signed-certificate
screen (Advanced → Proceed) once per browser, like any self-hosted admin
UI; after that the normal mic permission popup appears. Add an API key in
the settings panel, run the pipeline check, press Call. (Plain
`http://<HOST_IP>:8100` still works for everything except placing calls.)

**Yes, `.env` is required** (compose refuses to start without it), but only
the LiveKit entries genuinely need editing: `LIVEKIT_API_KEY` /
`LIVEKIT_API_SECRET` (matching `livekit.yaml`) and `LIVEKIT_PUBLIC_URL`
(what the *browser* reaches). Everything else — station URL, provider keys,
models — can be configured in the panel afterwards.

### Local, no Docker (Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r agent-worker\requirements.txt
# put livekit-server.exe in bin\  (github.com/livekit/livekit/releases)
copy .env.example .env
copy livekit.example.yaml livekit.yaml
.\run-local.ps1        # stop with .\run-local.ps1 -Stop
```

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
public, guarded by the usage limits instead. Stored as a salted PBKDF2 hash.
Wrong-password lockout: 5 failures per address → 5-minute cooldown; a second
round → banned until the app restarts. Locked out yourself? Set
`CALLIN_ADMIN_KEY` in the environment (always accepted, break-glass) or
restart the app to clear bans. Until a password is set, the panel shows a
standing nudge and stays open — fine on a trusted LAN, a choice you should
make deliberately.

**HTTP vs HTTPS.** Running on plain `http://` on your LAN is a fine default —
everything works except one thing: browsers refuse the *microphone* on
insecure origins, so calls can't be placed from other machines. The widget
detects this and shows a link to the fix instead of failing cryptically.
Your two options, in order of preference:
- **HTTPS front door** (bundled): open `https://<HOST_IP>:8443`, accept the
  one-time certificate screen. Best for anything other people use.
- **Browser flag** (single-machine testing only):
  `chrome://flags/#unsafely-treat-insecure-origin-as-secure`, add your
  widget origin, relaunch.
Note that a panel password sent over plain http is readable on the wire —
another reason the front door matters once you leave your own LAN.

Before exposing beyond your LAN:

1. `CALLIN_ALLOWED_ORIGINS` — set to your real origins (`*` lets any page
   read config endpoints and mint call tokens).
2. **Set the panel password** (above) — or `CALLIN_ADMIN_KEY` in the
   environment; either gates all settings/secrets/test access.
3. Fresh LiveKit keypair; `use_external_ip: true`; open the UDP range; put
   the browser-reachable `wss://` origin in `LIVEKIT_PUBLIC_URL`.
4. TLS reverse proxy in front of port 8100 (the mic requires a secure
   context off-localhost anyway).
5. Keep usage limits non-zero — every call spends real API money.
6. Know what's plaintext: `data/secrets.json` holds API keys unencrypted
   (0600 where the OS honours it). Protect the volume; never commit `.env`
   or `data/`.

## Troubleshooting

Run the **full pipeline check** first — it walks every leg in call order and
its failure messages name the fix. The classics:

- **Call hangs at "Ringing" while every server check passes** — LiveKit in
  docker is advertising its container IP as the media address. Set `HOST_IP`
  (which feeds `--node-ip`) and recreate the livekit container. The
  *Browser media path* stage exists precisely for this. If it still fails,
  check the host firewall allows UDP 50000–50100 and TCP 7881.
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
- **Webhooks registered to a `172.x` address** — same container-IP problem
  as above; `HOST_IP` fixes the callback URL too.
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

covers the speech filter, settings precedence, secrets handling, tool
allowlists, and prompt assembly.
