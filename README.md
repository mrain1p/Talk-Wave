<p align="center">
  <img src="docs/mark.svg" width="320" alt="Talk-Wave — the caller's voice and the DJ's, meeting in the middle">
</p>

<h1 align="center">Talk-Wave</h1>

## What it is

**A direct line into your [SUB/WAVE](https://github.com/perminder-klair/subwave) AI radio station.** A live call, text, or voicemail delivered straight to the booth — talk with your DJ or make music requests, off air or live on the broadcast. Features a built-in music player and a PWA that installs like an app and embeds anywhere.

Talk-Wave is a companion app with its own realtime voice agent wearing the persona and knowledge of your live DJ, and the station is only touched when the agent uses an allowlisted tool.

> **Note:** this was created with use of AI. It is recommended to use it locally, and only expose it externally if you know the risks and what you are doing.

<table>
<tr>
<td width="34%" valign="top"><img src="docs/call-desktop.png" alt="A live call in the station's own show theme: Wade mid-sentence, meters running, live captions, the talk bar open" /></td>
<td width="33%" valign="top"><img src="docs/player-web.png" alt="The station player, open as the page's front: the record playing with its art and tags, what's up next, the DJ's own line on the segue, and a request box wired to the booth" /></td>
<td width="33%" valign="top"><img src="docs/settings-desktop.png" alt="The operator's dashboard: who is on air, station health, the three lines with their switches, and the activity charts" /></td>
</tr>
<tr>
<td valign="top"><img src="docs/call-mobile.png" alt="The same live call on a phone" /></td>
<td valign="top"><img src="docs/settings-mobile.png" alt="The dashboard on a phone" /></td>
<td valign="top"><img src="docs/player-mobile.png" alt="The station player on a phone, in the light theme" /></td>
</tr>
</table>

**[▶ Watch the video demos in your browser](https://mrain1p.github.io/Talk-Wave/demos.html)**

## Features

**Three ways to reach the booth**
- **Live calls** — full-duplex voice with barge-in, live captions, and a DJ who knows the show it's on.
- **Voicemail** — greetings staged in each DJ's own voice; messages held, sent to the station, or AI-triaged. Or run the line as a **soundbite studio**: the caller records a take, reviews exactly what sending will do, and their own voice airs with the DJ around it. See [Voicemail](docs/VOICEMAIL.md).
- **The text line** — the same DJ, typed. Works where WebRTC can't.

**Gated where you need it** — three caller tiers (admin, guest code, open) under a per-feature [permission matrix](docs/settings.md#caller-permissions): each action goes to the least trusted tier that should have it, or to nobody. Usage caps on everything a call can cost, a kill switch that outranks it all, PBKDF2 passwords with lockouts, write-only keys, two-minute call tokens — and a fresh install answers nobody until its admin password is set. See [Security](docs/security.md).

**Control your station conversationally** — every action is a real tool the DJ can reach on any of the three lines, every one is a switch you can turn off, and anything that changes the station leaves a receipt card in the caller's transcript, so what happened is never only the DJ's word for it. A caller can **ask** about anything — what's playing, the lyrics, what played earlier, the queue, the schedule, the library, what the audience loves, or by feel: "something dreamy and cinematic" — **request** a song, an album front to back, or a themed mix, and pull any of it back out of the queue; **like, unlike or skip** the record on air; go **out to every listener** with a shout-out, a station segment (weather, news, a dedication), or a station beat and ident; and — still running after they hang up — put another show on air, lock the station to a genre, or ban a track for good, with an undo for each.

**A real station on the other end**
- Station tools through a hard allowlist — audience-reaching ones off by default, destructive ones never exposed — and every caller action gets its own transcript line, the receipt behind whatever the DJ *says* it did.
- **Live on air vs off air** — keep the conversation private with just the DJ, or take it out on the broadcast one finished turn at a time, with a pull-off-air button in your hand the whole way. While the broadcast DJ talks, the call waits its turn — nothing overlaps, nothing lost. Or flip it: the station's own idents, links and segments **stand down** while a call is live and return seconds after it ends; music never stops, and the operator's hand on the station's Voice switch always wins. See [Live on air](docs/on-air.md).
- **Open Line Segments** — the DJ puts a subject to the audience on air, and knows what it asked when somebody arrives on any of the three doors. Off by default; nothing airs until you press the button. See [Open Lines](docs/open-lines.md).
- Personas, voices and themes discovered live. Point it at another SUB/WAVE and it re-homes itself.

**Speech, both directions** — **LLM**: OpenAI, Anthropic, Google, DeepSeek, OpenRouter, Requesty, Vercel AI Gateway — or your own box with no key at all: Ollama, any OpenAI-compatible server, or the station's locca ([what to run](docs/models.md)). **STT**: bundled Whisper (no key, no network) or cloud ears (Deepgram, OpenAI, Google), echo-cancelled by default. **TTS**: any OpenAI-compatible endpoint via JSON adapters — ElevenLabs, Fish Audio, the station's own `/speak`. **Voice effects**: ten colours, telephone to lo-fi, per persona with an intensity dial.

**Player options** — installs to a phone like an app (PWA); a **pull-down station player** with cover art, the record's tags, what's up next, the DJ's own line on the segue, and likes and requests wired straight to the booth; embeds in two lines of HTML — card, pill, docked bar, or pop-up ([Embedding](docs/embedding.md)); themes light, dark, match-the-page, or the station's live show colours, every string overridable; a tuned-in count and a listener heart on the card; call sounds from shipped sets, every slot replaceable; push to talk, a post-call thumbs, and in-character timeouts so silence never just hangs there.

**Admin dashboard** — acts (toggles post instantly, no save) and reads (on air, station health, activity charts); settings apply to the **next caller**, no restarts, ever; diagnostics that run the real code paths, so green means a call will work; and one search across every page that says where each answer lives.

## Documentation

The README is the short version. The detail lives here:

| | |
|---|---|
| **[Quick start](docs/quickstart.md)** | Nothing to a working call in ten minutes |
| **[How it works](docs/how-it-works.md)** | The parts and the path: LiveKit, the worker, the web half, and what runs where |
| **[What to run](docs/models.md)** | Which model and voice, ideal to minimal, and where a caller notices |
| **[Settings reference](docs/settings.md)** | Every setting, its default, and what it changes |
| **[The dashboard](docs/dashboard.md)** | The panel's landing page: the station tiles, the Transmission switch ladder and its one rule, the pull, and the activity charts |
| **[Embedding](docs/embedding.md)** | The card on your own page: the two-line snippet, the shapes, and the per-surface columns |
| **[Calling from outside your network](docs/networking.md)** | The topologies, the TLS front door, and why a call can connect with no audio |
| **[Security and privacy](docs/security.md)** | The exposure checklist, the two passwords, what is enforced |
| **[Troubleshooting](docs/troubleshooting.md)** | Known limits, reading a call back, logs and tests |
| **[Voicemail](docs/VOICEMAIL.md)** | The answering machine and the soundbite studio: staged greetings, where messages go, and the terms on which anything is recorded |
| **[Live on air](docs/on-air.md)** | The phone-in on the broadcast: what listeners hear, the three consents, the pull, and the wiring both containers need |
| **[How a call actually works](docs/the-call.md)** | The path one sentence takes: the prompt and what it costs, the tool surface, how a request is triaged, and everything that can make the DJ speak |

## Getting started

**You need three things:** a running **SUB/WAVE station** (Talk Wave is its companion phone line, not a standalone radio) · a **Docker host on the same network** — a NAS is fine · **one LLM API key**, or a local Ollama which needs none ([what to run](docs/models.md)). HTTPS comes bundled — browsers only grant the microphone to secure origins, and the included Caddy provides one on a LAN with zero config.

**The one-command install:**

```bash
curl -fsSL https://raw.githubusercontent.com/mrain1p/Talk-Wave/main/install.sh | bash
```

It fetches the stack, generates the LiveKit secret, detects your LAN address and starts everything. The whole deployment is one [docker-compose.yaml](docker-compose.yaml) — four services: LiveKit for the call media, the worker (the DJ itself), the web half (tokens, widget, panel), and the bundled Caddy TLS door — plus a [Caddyfile](Caddyfile), a two-variable `.env`, a `livekit.yaml` keypair, and one `data/` folder owned by uid 1000 that IS the backup. Images publish to `ghcr.io/mrain1p/talk-wave`; `:latest` tracks `main` and includes the widget. Every step by hand, walked slowly: **[Quick start](docs/quickstart.md)**.

**Two variables in `.env`, and that is all it has to say:** `HOST_IP` — the docker host's LAN address, which drives LiveKit's advertised media address, the browser URL, and the webhook callback — and `SUBWAVE_STREAM_URL`, the station's public `https://` stream. **Set the stream URL first**: left blank it derives a plain-http URL that browsers silently block as mixed content, and the caller hears no station.

**Then open `https://<HOST_IP>:8443`** — the bundled Caddy TLS door, with a one-time certificate screen. Set the admin password, add an API key, run the pipeline check, press Call. Type passwords and keys only at `:8443`, never over plain `:8100`. Get a call working on your LAN before exposing anything — [security](docs/security.md) is the exposure checklist, and [networking](docs/networking.md) covers outside callers and running your own reverse proxy in front.

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

**There is no third party anywhere in the path** — nothing phones home, no telemetry.

**Station admin credentials are optional, and never leave your box.** Entering your SUB/WAVE admin login unlocks the advanced on-air features — putting a different show on air, running segments and skills, skipping tracks, mirroring persona voices. Without it, everything else still works.

- Entered by the operator into their **own self-hosted instance**.
- Stored server-side and **write-only** — the panel shows a fixed mask, never the value, never its length.
- Kept behind the instance's own admin password.
- Every caller-facing action they unlock ships **admin-only** — yours at the admin door, nobody else's until you say otherwise — and each is individually permission-gated.

**Two things worth knowing as an operator:**

- **Caller audio** is processed by whichever speech and AI providers *you* configure — fully local with Ollama and the bundled Whisper, or on your own cloud keys.
- **Calls are transcribed and stored** on your own server by default, with configurable retention — *Keep transcripts* in the settings panel turns it off or bounds how many are kept. Transcript only — a call's audio is never written to disk. (The one exception is the voicemail soundbite, which holds a caller's clip just long enough to air it, then deletes it.)

And no caller reaches the broadcast stream unless you open the [on-air doors](docs/on-air.md) — shipped shut, chosen per caller, running a turn behind the room, with a pull-off-air button in your hand.

## License

MIT — free to use, tinker with, and build on. See [LICENSE](LICENSE).
