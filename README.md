<p align="center">
  <img src="docs/mark.svg" width="320" alt="Talk-Wave — the caller's voice and the DJ's, meeting in the middle">
</p>

<h1 align="center">Talk-Wave</h1>

## What it is

**A call-in phone line for your [SUB/WAVE](https://github.com/perminder-klair/subwave) AI radio station.** A listener presses one button in the browser and talks with whoever is live on air — and the DJ can act on the station mid-call: find a record by name or by how it *sounds*, queue it, take it back out again, put a shoutout on the broadcast.

The call is not the station speaking. It's this sidecar's own realtime voice agent wearing the live persona, and the station is only touched when the agent uses an allowlisted tool.

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

**[▶ Watch the demos in your browser](https://mrain1p.github.io/Talk-Wave/demos.html)** — all six on one page, streaming, no download. Or one at a time, each in its own tab:

▶ **[A real call](https://mrain1p.github.io/Talk-Wave/live-call.mp4)** — in-persona pickup, back-and-forth, and an acoustic request landing in the station's queue mid-call.

▶ **[A caller on the station's own air](https://mrain1p.github.io/Talk-Wave/on-air-music-request.mp4)** — the booth hands a caller the airwaves live, then plays their pick for everyone listening.

▶ **[A shout-out, broadcast](https://mrain1p.github.io/Talk-Wave/on-air-shout-out.mp4)** — a caller's dedication goes straight out on the broadcast, in the DJ's own voice around it.

▶ **[The text line](https://mrain1p.github.io/Talk-Wave/text-line.mp4)** — typed chat with whoever is on air: same brain, same tools, no microphone.

▶ **[The answering machine](https://mrain1p.github.io/Talk-Wave/voicemail-machine.mp4)** — the machine takes a message, and the swipe-up station player underneath it.

▶ **[A voicemail in ten seconds](https://mrain1p.github.io/Talk-Wave/voicemail-in-ten-seconds.mp4)** — the shortest possible loop: call, beep, say it, done.

## Features

**Three ways to reach the booth**
- **Live calls** — full-duplex voice with barge-in, live captions, and a DJ who knows the show it's on.
- **Voicemail** — greetings staged in each DJ's own voice; messages held for you, sent to the station, or AI-triaged. Or run the line as a **soundbite studio**: the caller records a take, reviews the transcript and exactly what sending will do, and their own voice airs with the DJ around it. See [Voicemail](docs/VOICEMAIL.md).
- **The text line** — the same DJ, typed. Works where WebRTC can't.

**A real station on the other end**
- Station tools through a hard allowlist — the audience-reaching ones off by default, the destructive ones never exposed.
- **On-air ducking** — while the broadcast DJ is talking, the call waits its turn. Nothing overlaps, nothing is lost.
- **Or quiet the station instead** — flip it the other way: the station's own idents, links and segments stand down while a call is live and return within seconds of it ending. Music never stops, and the operator's hand on the station's own Voice switch always wins. See [Live on air](docs/on-air.md#quieting-the-stations-own-dj).
- **Live on air** — a caller can go out on the broadcast itself: the conversation airs one finished turn at a time, a turn behind the room, with a pull-off-air button in your hand the whole way. See [Live on air](docs/on-air.md).
- **Open Lines** — the booth reaching out instead of in: the DJ puts a subject to the audience on air, invites them to weigh in, and then knows what it asked when somebody arrives on any of the three doors. It opens by finding out whether they came for the topic or for something else, and a request is never pushed aside for it. Off by default, and nothing airs until you press the button. See [Open Lines](docs/open-lines.md).
- Personas, voices and themes discovered live. Point it at another SUB/WAVE and it re-homes itself.
- Every caller action gets its own transcript line — the receipt behind whatever the DJ *says* it did. [The whole list](#what-a-caller-can-make-happen).

**Speech, both directions**
- **LLM**: OpenAI, Anthropic, Google, DeepSeek, OpenRouter, Requesty, Vercel AI Gateway — or your own box with no key at all: Ollama, any OpenAI-compatible server (llama.cpp, vLLM, LM Studio), or the station's locca. [What to run](docs/models.md) says which actually carry a call.
- **STT**: bundled Whisper (no key, no network) or cloud ears (Deepgram, OpenAI, Google). Echo cancellation on by default.
- **TTS**: any OpenAI-compatible endpoint via JSON adapters — ElevenLabs, Fish Audio, and the station's own `/speak` in the box.
- **Voice effects**: ten colours — telephone, CB, walkie-talkie, AM, megaphone, underwater, stadium PA, intercom, shortwave, lo-fi — per persona, with an intensity dial.

**A player people actually use**
- Installs to a phone like an app, and reads like one.
- A **pull-down station player** — cover art, the record's tags, what's up next, the DJ's own line on the segue, and likes and song requests wired straight to the booth.
- Embeds in two lines of HTML — inline card, launcher pill, docked bar, or pop-up. See [Embedding](docs/embedding.md).
- Themes: light, dark, match-the-page, or the station's live show colours. Every string on the card overridable.
- The card says how many are tuned in and offers the same heart any listener page has — both optional, both on by default.
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
- One search across every page — labels, help and each setting's own synonyms — that says where each answer lives.

## What a caller can make happen

Every action here is a real tool the DJ can reach — on a call, on the text line, or out of a voicemail — and every one is a switch you can turn off. Anything that changes the station leaves its own receipt card in the caller's transcript, so what happened is never only the DJ's word for it.

- **Just asking** — what's playing, the lyrics, what played earlier, the queue, the schedule, what's in the library, what the audience loves, or by feel: "something dreamy and cinematic". Reads only, changes nothing, no card.
- **Into the queue** — request a song, an album front to back, or a themed mix — and pull a queued track back out, or clear the lot.
- **The record on air** — like it, take the like back, or skip it.
- **Out to every listener** — a shout-out on air, a station segment (weather, news, a dedication), or a station beat and ident.
- **Still running after they hang up** — put another show on air, lock the station to a genre, ban a track for good — and undo every one of them.

Which of these a caller can reach at all is the [permission matrix](docs/settings.md#caller-permissions)'s business: each action goes to the least trusted tier that should have it, or to nobody.

## Documentation

The README is the short version. The detail lives here:

| | |
|---|---|
| **[Quick start](docs/quickstart.md)** | Nothing to a working call in ten minutes |
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

It fetches the stack, generates the LiveKit secret, detects your LAN address and starts everything. **Or do it by hand** — the whole stack is four files plus one empty `data/` folder, and each file is worth a look before you run it:

| File | What it is |
|---|---|
| [docker-compose.yaml](docker-compose.yaml) | The services — LiveKit, the worker, the web half, and the bundled Caddy TLS door |
| [Caddyfile](Caddyfile) | The TLS front door: the widget route **and** the `/rtc` WebSocket route |
| [.env.example](.env.example) → `.env` | The two variables below |
| [livekit.example.yaml](livekit.example.yaml) → `livekit.yaml` | The LiveKit keypair — paste in a fresh secret (the file shows the generator); the app reads it from here too |

```bash
cp .env.example .env
cp livekit.example.yaml livekit.yaml   # generate a fresh secret for it
mkdir -p data && chown -R 1000:1000 data && chmod -R u+rwX data
docker compose up -d
```

Images publish to `ghcr.io/mrain1p/talk-wave`; `:latest` tracks `main` and includes the widget. **`data/` must belong to uid 1000** — both processes run as it, and it IS the backup.

**Two variables in `.env`, and that is all it has to say:**

| Variable | What it is |
|---|---|
| **`HOST_IP`** | The docker host's LAN address. Drives LiveKit's advertised media address, the browser URL, and the webhook callback |
| **`SUBWAVE_STREAM_URL`** | The station's public `https://` stream — **set it first**: left blank it derives a plain-http URL that browsers silently block as mixed content, and the caller hears no station |

**Then open `https://<HOST_IP>:8443`** — the bundled Caddy TLS door, with a one-time certificate screen. Set the admin password, add an API key, run the pipeline check, press Call. Type passwords and keys only at `:8443`, never over plain `:8100`. Running your own reverse proxy instead? Replicate **both** Caddyfile routes — the widget *and* `/rtc` — or calls connect with no audio; [networking](docs/networking.md) has the details.

**First run, walked slowly: [docs/quickstart.md](docs/quickstart.md)** — the same path with the certificate screen, the admin password, pointing at the station, the pipeline check, and what to back up. Get a call working on your LAN before exposing anything — [security](docs/security.md) is the exposure checklist, and callers from outside your network need one router rule plus one compose line ([networking](docs/networking.md)).

### Local, no Docker (Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r agent-worker\requirements.txt
# put livekit-server.exe in bin\  (github.com/livekit/livekit/releases)
copy .env.example .env
copy livekit.example.yaml livekit.yaml
.\run-local.ps1        # stop with .\run-local.ps1 -Stop
```

## How it works

```
[browser mic] --WebRTC--> [livekit-server] --> [talkwave-worker]
                                                STT -> LLM -> TTS
                                                     |
                                          SUB/WAVE MCP (allowlisted)
```

Your voice reaches a speech-to-text engine, an LLM answers as the DJ who is on air right now, and a text-to-speech voice says it back — a full loop every turn, with the station's own tools attached. Everything runs on **your** hardware and **your** API keys (or fully local with Ollama and the bundled Whisper).

| Component | What it does |
|---|---|
| `livekit-server` | WebRTC media — one room per call |
| `talkwave-worker` | Resolves the persona, builds the prompt, runs STT → LLM → TTS with MCP tools attached |
| `talkwave-web` | Mints join tokens (the browser never sees LiveKit secrets), serves widget and panel, proxies station reads |
| `web-widget` | The call page — installable to a phone's home screen, or a compact embeddable card |

Inside the worker: one call is one `CallSession`; the tool allowlist is declared once, in `registry.py`, and the runtime surface and the panel's reference both derive from it; the prompt is assembled in `agent-worker/brain/`, with what the DJ *knows* and how it *behaves* in separate files; and anything that changes the station is a local wrapper, never a raw MCP call — which is what makes **Actions per call** a real ceiling. The path one sentence takes: [How a call actually works](docs/the-call.md).

## Privacy

**There is no third party anywhere in the path.** The author runs no servers at all, nothing phones home, and there is no telemetry.

**Station admin credentials are optional, and never leave your box.** Entering your SUB/WAVE admin login unlocks the advanced on-air features — putting a different show on air, running segments and skills, skipping tracks, mirroring persona voices. Without it, everything else still works.

- Entered by the operator into their **own self-hosted instance**.
- Stored server-side and **write-only** — the panel shows a fixed mask, never the value, never its length.
- Kept behind the instance's own admin password.
- Every caller-facing action they unlock is **off by default**, and individually permission-gated.

**Two things worth knowing as an operator:**

- **Caller audio** is processed by whichever speech and AI providers *you* configure — fully local with Ollama and the bundled Whisper, or on your own cloud keys.
- **Calls can be transcribed and stored** on your own server, with configurable retention. The card shows a Recording indicator whenever that is on.

And no caller reaches the broadcast stream unless you open the [on-air doors](docs/on-air.md) — shipped shut, chosen per caller, running a turn behind the room, with a pull-off-air button in your hand.

## License

MIT — free to use, tinker with, and build on. See [LICENSE](LICENSE).
