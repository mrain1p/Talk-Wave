# Talk Wave

**A call-in phone line for your [SUB/WAVE](https://github.com/perminder-klair/subwave) AI radio station.** A listener presses one button in the browser and talks with whoever is live on air — and the DJ can act on the station mid-call: find a record by name or by how it *sounds*, queue it, take it back out again, put a shoutout on the broadcast.

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
- **Live calls** — full-duplex voice with barge-in, live captions, and a DJ who knows the show it's on.
- **Voicemail** — greetings staged in each DJ's own voice; messages held for you, sent to the station, or AI-triaged. See [Voicemail](docs/VOICEMAIL.md).
- **The text line** — the same DJ, typed. Works where WebRTC can't.

**A real station on the other end**
- Station tools through a hard allowlist — the audience-reaching ones off by default, the destructive ones never exposed.
- **On-air ducking** — while the broadcast DJ is talking, the call waits its turn. Nothing overlaps, nothing is lost.
- Personas, voices and themes discovered live. Point it at another SUB/WAVE and it re-homes itself.
- Every caller action gets its own transcript line — the receipt behind whatever the DJ *says* it did.

**Speech, both directions**
- **LLM**: OpenAI, Anthropic, Google, DeepSeek, OpenRouter, Requesty, Vercel AI Gateway — or your own box with no key at all: Ollama, any OpenAI-compatible server (llama.cpp, vLLM, LM Studio), or the station's locca. [What to run](docs/models.md) says which actually carry a call.
- **STT**: bundled Whisper (no key, no network) or cloud ears (Deepgram, OpenAI, Google). Echo cancellation on by default.
- **TTS**: any OpenAI-compatible endpoint via JSON adapters — ElevenLabs, Fish Audio, and the station's own `/speak` in the box.
- **Voice effects**: ten colours — telephone, CB, walkie-talkie, AM, megaphone, underwater, stadium PA, intercom, shortwave, lo-fi — per persona, with an intensity dial.

**A player people actually use**
- Installs to a phone like an app, and reads like one.
- Embeds in two lines of HTML — inline card, launcher pill, docked bar, or pop-up. See [Embedding](#embedding).
- Themes: light, dark, match-the-page, or the station's live show colours. Every string on the card overridable.
- Call sounds from shipped sets — synthesized classics, real public-domain line tones — every slot replaceable with your own.
- Push to talk, a post-call thumbs, and in-character timeouts so silence never just hangs there.

**Gated like a phone line, not a demo**
- Three caller tiers — admin, guest code, open — and a per-feature permission matrix: each action goes to the least trusted tier that should have it, or nobody.
- Usage caps on everything a call can cost, and a kill switch that outranks it all.
- PBKDF2 passwords with lockouts, write-only keys, two-minute call tokens — and a fresh install answers nobody until its admin password is set. See [Security](docs/security.md).

**An operator's panel that tells the truth**
- A dashboard that acts (toggles post instantly, no save) and reads (on air, station health, activity charts).
- Settings apply to the **next caller** — no restarts, ever.
- Diagnostics run the real code paths: green means a call will work, not "the URL responded".

## Before you start

What a deployment actually needs, so nothing surprises you at step three:

- **A SUB/WAVE station, running.** Talk Wave is its companion phone line, not a standalone radio — personas, cards, voices and tools all come from the station.
- **A Docker host on the same network** — a NAS is fine; that is where this was built. (Windows-local without Docker also works, for development.)
- **One LLM API key** — or a local Ollama, which needs none. Calls spend **your** key: the usage caps exist so a stranger can't spend it for you, and they're on by default. [What to run](docs/models.md) is the short version of which model and voice actually carry a call.
- **HTTPS is non-negotiable for calls** — browsers only grant the microphone to secure origins. The bundled Caddy gives you that on a LAN with zero config (one certificate screen, once); a real domain removes even that. See [networking](docs/networking.md).
- **LAN first.** Get a call working on your own network before exposing anything — and when you do expose it, [security](docs/security.md) is the checklist, with a guest code and the admin password set before the port opens.
- **Callers from outside your network** need one router rule (a single UDP port) and one compose line — [networking](docs/networking.md) walks it.

## Getting started

**The one-command version:**

```bash
curl -fsSL https://raw.githubusercontent.com/mrain1p/Talk-Wave/main/install.sh | bash
```

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

Both processes run as **uid 1000**, so `data/` has to belong to it — that is what the third line does. Skip it and the app can't read its own files: no setup ask, a locked panel, and the reason named at the login gate and in the logs.

**`HOST_IP`** is the one deployment variable — the docker host's LAN address, driving LiveKit's advertised media address, the browser URL and the webhook callback. That is all `.env` has to say: the LiveKit keypair is read from the mounted `livekit.yaml` (env still overrides), so the secret lives in one file. The rest is panel.

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
| **[What to run](docs/models.md)** | Which model and voice, ideal to minimal, and where a caller notices |
| **[Settings reference](docs/settings.md)** | Every setting, its default, and what it changes |
| **[Calling from outside your network](docs/networking.md)** | The topologies, the TLS front door, and why a call can connect with no audio |
| **[Security and privacy](docs/security.md)** | The exposure checklist, the two passwords, what is enforced |
| **[Troubleshooting](docs/troubleshooting.md)** | Known limits, reading a call back, logs and tests |
| **[Voicemail](docs/VOICEMAIL.md)** | The answering machine: staged greetings, where messages go, and what is deliberately never recorded |
| **[How a call actually works](docs/the-call.md)** | The path one sentence takes: the prompt and what it costs, the tool surface, how a request is triaged, and everything that can make the DJ speak |

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
