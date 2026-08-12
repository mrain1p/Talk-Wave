# Talk Wave

**A call-in phone line for your [SUB/WAVE](https://github.com/perminder-klair/subwave) AI radio station.** A listener presses one button in the browser and talks with whoever is live on air — and the DJ can act on the station mid-call: search the library, queue a request, put a shoutout on the broadcast.

The call is not the station speaking. It's this sidecar's own realtime voice agent wearing the live persona, and the station is only touched when the agent uses an allowlisted tool.

Please note this was created with use of AI. It is recommended to use it locally and only expose it externally if you know the risks and what you are doing.

<table>
<tr>
<td valign="top"><img src="docs/call.png" width="430" alt="A live call: the DJ mid-sentence, level meters running, live captions — including a library search happening in-conversation" /></td>
<td valign="top"><img src="docs/settings.png" width="210" alt="The settings panel, folded: every section header summarises its own state" /></td>
</tr>
</table>

▶ **[Watch a real call (2 min)](docs/talkwave-call.mp4)** — in-persona pickup, back-and-forth, and a Beatles request resolved against the live library.

## How it works

```
[browser mic] --WebRTC--> [livekit-server] --> [talkwave-worker]
                                                STT -> LLM -> TTS
                                                     |
                                          SUB/WAVE MCP (allowlisted)
```

Your voice reaches a speech-to-text engine, an LLM answers as the DJ who is on air right now, and a text-to-speech voice says it back — a full loop every turn, with the station's own tools attached. Everything runs on **your** hardware and **your** API keys (or fully local with Ollama and the bundled Whisper). The author runs no servers; nothing phones home.

## Features

**Three ways to reach the booth**
- **Live calls** — full-duplex voice with barge-in, live captions both ways, level meters, and a DJ who knows the show, the recent tracks, and what it just said on air.
- **Voicemail** — an answering machine with per-persona greetings staged in each DJ's own voice; messages are held for you or delivered to the station, and an AI triage can turn one into a request or an on-air mention. See [Voicemail](docs/VOICEMAIL.md).
- **The text line** — typed chat with the same DJ, same tools, same receipts, over a plain WebSocket — it works where WebRTC can't and keeps working while the media server is down.

**A real radio station on the other end**
- The DJ's tools come from the station's MCP server through a hard allowlist: requests, search, queueing, announcements, segments — with the audience-reaching ones off by default and the destructive ones never exposed at any setting.
- **On-air ducking**: the call DJ and the broadcast DJ are the same voice, so while the station has the microphone the caller's replies queue instead of talking over the air — and the card says why the line went quiet.
- Personas, show cards, voices and themes are discovered live from the station. Point Talk Wave at another SUB/WAVE and it re-homes itself.
- Every action a caller triggers gets its own transcript line — the DJ *saying* it did something is a claim; that line is the receipt.

**Speech, both directions**
- **LLM**: OpenAI, Anthropic, Google, DeepSeek, OpenRouter, Requesty, Vercel AI Gateway — or Ollama on your own network with no key at all. The same provider list the station offers, so one account runs both.
- **STT**: a bundled Whisper that needs no key and no network, or cloud ears (Deepgram, OpenAI, Google) when callers are misheard. Echo cancellation, noise suppression and auto-gain are on by default, so the station playing in the caller's room isn't transcribed back.
- **TTS**: any OpenAI-compatible endpoint via a JSON adapter — ElevenLabs and Fish Audio adapters ship in the box, plus one matching SUB/WAVE's own `/speak` so the call DJ uses the station's voice.
- **Voice effects and filters**: telephone, CB, shortwave, lo-fi and friends — a radio colour on the DJ's voice with an intensity dial, settable per persona.

**A player people actually use**
- The call page **installs to a phone like an app** (PWA) and reads like a real phone there — portrait up top, words in the middle, actions under your thumb.
- **Embeds** drop into any page with two lines of HTML, as an inline card, a floating launcher pill, a docked bar, or a pop-up button — previewed in the panel before you copy the snippet. See [Embedding](#embedding).
- **Themes**: light, dark, match-the-page, or the station's own show colours live from its `/themes` — and every fixed string on the card is overridable in your station's voice.
- **Call sounds**: ring, pickup, hold and hang-up from shipped sound sets — synthesized classics plus recorded public-domain line tones — every slot replaceable from a filterable shelf of clips and your own uploads.
- Push to talk (on by default, per surface), a thumbs up/down after the call, and in-character timeouts so silence never just hangs there.

**Gated like a phone line, not a demo**
- **Three tiers** — admin, guest code, open — and a per-feature permission matrix: every station action is granted to the least trusted caller who should have it, or nobody.
- Usage caps on concurrent calls, per hour, per day, redial wait and actions per call; a kill switch on the dashboard that outranks everything.
- Two PBKDF2 passwords with fail2ban-style lockouts, write-only API keys, signed short-lived call tokens, and a caller treated as an untrusted stranger by both the prompt and the code. See [Security](docs/security.md).

**An operator's panel that tells the truth**
- A dashboard that acts (kill switch, per-door toggles — posted instantly, no save) and reads (who's on air, station health, the call chain, activity charts).
- Settings as pages under one URL; every change applies to the **next caller**, no restart, and clearing a field falls back to your `.env`.
- Diagnostics that exercise the real code paths: a twelve-stage pipeline check that walks every leg of a call in order, a speed test, full call transcripts, live logs. Green means a call will work — not "the URL responded".

## Before you start

What a deployment actually needs, so nothing surprises you at step three:

- **A SUB/WAVE station, running.** Talk Wave is its companion phone line, not a standalone radio — personas, cards, voices and tools all come from the station.
- **A Docker host on the same network** — a NAS is fine; that is where this was built. (Windows-local without Docker also works, for development.)
- **One LLM API key** — or a local Ollama, which needs none. Calls spend **your** key: the usage caps exist so a stranger can't spend it for you, and they're on by default.
- **HTTPS is non-negotiable for calls** — browsers only grant the microphone to secure origins. The bundled Caddy gives you that on a LAN with zero config (one certificate screen, once); a real domain removes even that. See [networking](docs/networking.md).
- **LAN first.** Get a call working on your own network before exposing anything — and when you do expose it, [security](docs/security.md) is the checklist, with a guest code and the admin password set before the port opens.
- **Callers from outside your network** need one router rule (a single UDP port) and one compose line — [networking](docs/networking.md) walks it.

## Getting started

**The ten-minute version: [docs/quickstart.md](docs/quickstart.md)** — what you need, the five commands, first run, backup. The sections below are the same path with more said.

### Docker (recommended)

Images publish to `ghcr.io/mrain1p/talk-wave`; `:latest` tracks `main`. The image includes the widget. A deploy needs four files and one empty folder:

```
talk-wave/
├── docker-compose.yaml    # from this repo
├── Caddyfile              # from this repo — the TLS front door mounts it
├── .env                   # from .env.example — REQUIRED
├── livekit.yaml           # from livekit.example.yaml, with a fresh secret
└── data/                  # empty; the app fills it, and it IS the backup
```

```bash
cp .env.example .env
cp livekit.example.yaml livekit.yaml   # generate a fresh secret for it
mkdir -p data && chown -R 1000:1000 data && chmod -R u+rwX data
docker compose up -d
```

Both processes run as **uid 1000**, so `data/` has to belong to it — that is what the third line does. On filesystems that create files with no permission bits (Synology shares among them) the `chmod` is what lets the app read its own settings, so run both.

**`HOST_IP`** is the one deployment variable — the docker host's LAN address, driving LiveKit's advertised media address, the browser URL and the webhook callback. Otherwise `.env` only needs the LiveKit keypair; the rest is panel.

**`SUBWAVE_STREAM_URL`** should be the station's public `https://` stream. Left blank it derives from the station's own address, which is plain http on the LAN — and since the widget must be served over TLS for the microphone to work, the browser blocks that stream as mixed content and the caller hears no station. It fails silently, so set it first. A bare origin is enough; the station's published mounts are discovered, mp3 first.

**Open `https://<HOST_IP>:8443`**, the bundled Caddy TLS front door. Browsers only allow the microphone on HTTPS origins; the first visit shows a one-time certificate screen. Set the admin password, add an API key, run the pipeline check, press Call. Plain `http://<HOST_IP>:8100` works for everything except placing calls.

**Do you need the bundled Caddy?** Only for TLS, and only because the microphone requires it — it is the zero-config way to get an HTTPS origin on a LAN. If you already run a reverse proxy (Caddy, Traefik, nginx, a NAS's built in one), delete the `caddy` service and terminate TLS there instead — but replicate both routes from the `Caddyfile`, not just one: the widget to `talkwave-web:8100` **and** `/rtc` to `livekit-server:7880` (WebSocket). The `/rtc` half is the one people forget; without it the page loads, the call connects, and no audio ever flows. Set `LIVEKIT_PUBLIC_URL` to `wss://your-hostname` so the browser signals through the same origin.

### Local, no Docker (Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r agent-worker\requirements.txt
# put livekit-server.exe in bin\  (github.com/livekit/livekit/releases)
copy .env.example .env
copy livekit.example.yaml livekit.yaml
.\run-local.ps1        # stop with .\run-local.ps1 -Stop
```

## Privacy

**Station admin credentials are optional, and never leave your box.** Entering your SUB/WAVE admin login unlocks the advanced on-air features — putting a different show on air, running segments and skills, skipping tracks, mirroring persona voices. Without it, everything else still works. The credentials are entered by the operator into their **own self-hosted instance**, stored server-side and write-only (the panel shows only a fixed mask — never the value, never its length), behind the instance's own admin password — and every caller-facing action they unlock is off by default and individually permission-gated. There is no third party anywhere in the path: the author runs no servers at all.

Two things worth knowing as an operator: caller audio is processed by whichever speech and AI providers **you** configure (it runs fully local with Ollama and the bundled Whisper, or on your own cloud keys), and calls can be transcribed and stored **on your own server** with configurable retention — the card shows a Recording indicator while that's on. Nothing phones home, there is no telemetry, and Talk Wave never touches the broadcast stream.

## Documentation

The README is the short version. The detail lives here:

| | |
|---|---|
| **[Quick start](docs/quickstart.md)** | Nothing to a working call in ten minutes |
| **[Settings reference](docs/settings.md)** | Every setting, its default, and what it changes |
| **[Calling from outside your network](docs/networking.md)** | The topologies, the TLS front door, and why a call can connect with no audio |
| **[Security and privacy](docs/security.md)** | The exposure checklist, the two passwords, what is enforced |
| **[Troubleshooting](docs/troubleshooting.md)** | Known limits, reading a call back, logs and tests |
| **[Voicemail](docs/VOICEMAIL.md)** | The answering machine: staged greetings, where messages go, and what is deliberately never recorded |

## Under the hood

| Component | What it does |
|---|---|
| `livekit-server` | WebRTC media — one room per call |
| `talkwave-worker` | Resolves the persona, builds the prompt, runs STT → LLM → TTS with MCP tools attached |
| `talkwave-web` | Mints join tokens (the browser never sees LiveKit secrets), serves widget and panel, proxies station reads |
| `web-widget` | The call page — installable to a phone's home screen, or a compact embeddable card |

Inside the worker, one call is one `CallSession` and every file under `agent-worker/call/` is named after its job. The tool allowlist is declared **once** in `registry.py` — the runtime surface and the panel's reference both derive from it. The prompt is assembled in `agent-worker/brain/`: what the DJ **knows** and how it **behaves** are separate files that change for separate reasons. Anything that changes the station is a local wrapper rather than a raw MCP call, which is what makes **Actions per call** a real ceiling. Test buttons and the pipeline check measure rather than trust — sample rates are read from the backend's own audio headers, voices are checked for every persona, and when a backend refuses, what it actually said is what you read.

## Embedding

```html
<div id="subwave-callin"></div>
<script src="https://your-host/embed.js"></script>
```

Renders the compact card in an iframe with `allow="microphone"` set (its absence is the classic silent embed failure). The panel never ships in an embed.

**What the card shows in an embed is answered separately** from what it shows on the standalone page — see **What the card shows** in the panel. The host page usually has its own show heading and now-playing line, and a second copy of both inside the frame is noise. The settings gear is the one thing that is never offered here at any setting: an embed does not load the panel's code, so it would open nothing.

| Attribute | Effect |
|---|---|
| `data-theme="light\|dark\|inherit"` | The widget *starts* on this theme — `inherit` matches the host page's background (resolved before the frame loads, since a cross-origin frame can't see its parent) — but the viewer's toggle still works and their choice is remembered. The toggle cycles light → dark → the station's show colours (when the panel has them on offer) → match the page. Omit for OS preference |
| `data-lock-theme="true"` | Pin `data-theme` outright and remove the toggle, for a page that needs one look |
| `data-captions="ticker\|full\|off"` | Embeds default to `ticker` — latest line only, fading, so the widget stays short |
| `data-height="260px"` | Frame height for tight layouts |
| `data-compact="false"` | Full card instead of the compact one |
| `data-origin` | Widget origin when the script is served from elsewhere |
| `data-mode="launcher\|dock\|button"` | An off-the-shelf shape that *opens on a press* instead of sitting inline: `launcher` is a floating call pill in a page corner, `dock` a slim bar pinned across the bottom, `button` an inline button in the page flow that opens the card in a centred pop-up. All three name who answers (or say the line is closed) before they are pressed, and collapsing one never hangs up a call in progress. Pick one — and preview it — in the panel's **Embed** section |
| `data-position="left"` | Puts the launcher pill in the left corner (right is the default) |

**The station's own colours** are not an embed attribute — set **Player settings → Colours → "The station's own colours"** in the panel and every surface, embeds included, wears the on-air show's palette live from the station's `/themes` (a host's `data-theme` is only the starting point, so it does not block this). A host page can also push its own palette *and fonts* into the card over `postMessage` — see `web-widget/HOST-STYLE-GUIDE.md` — which repaints in place without dropping a call.

Any page you embed on can mint call tokens, so treat an embed as publishing the phone. Set a guest code if that isn't what you want.

## License

MIT — free to use, tinker with, and build on. See [LICENSE](LICENSE).
