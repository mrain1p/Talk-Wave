# Wave Talk

**Live voice call-ins for a [SUB/WAVE] AI radio station.** A listener presses
one button in the browser, talks with whoever is live on air, and the DJ can
act on the station mid-call — search the library, queue a request, put a
shoutout on the broadcast.

The call is not the station speaking. It's this sidecar's own realtime voice
agent wearing the live persona, and the station is only touched when the agent
uses an allowlisted tool.

Please note this was created with use of AI. It is recommended to use it
locally and only expose it externally if you know the risks and what you are
doing.

<table>
<tr>
<td valign="top"><img src="docs/call.png" width="430" alt="A live call: the DJ mid-sentence, level meters running, live captions — including a library search happening in-conversation" /></td>
<td valign="top"><img src="docs/settings.png" width="210" alt="The settings panel, folded: every section header summarises its own state" /></td>
</tr>
</table>

▶ **[Watch a real call (2 min)](docs/wavetalk-call.mp4)** — in-persona pickup,
back-and-forth, and a Beatles request resolved against the live library.

```
[browser mic] --WebRTC--> [livekit-server] --> [agent-worker]
                                                STT -> LLM -> TTS
                                                     |
                                          SUB/WAVE MCP (allowlisted)
```

## Features

**The call**
- One button; whoever is live on air answers, in persona, aware of the show,
  recent tracks, and what it just said on the broadcast.
- Full-duplex with barge-in. Live captions both ways, state and level meters,
  call timer — every indicator driven by a real signal.
- Caller actions get their own transcript line. The DJ *saying* it did
  something is a claim; that line is the receipt.
- Synthesized ring, pickup, hold, hang-up and engaged tones, each replaceable
  — see [Call sounds](#call-sounds).
- In-character timeouts for silence and over-long calls; a caller who was just
  asked a question gets three times the usual wait. If no worker answers, an
  engaged tone after 40 seconds rather than endless ringing.
- The DJ closes the call itself, and code stops it hanging up in the first
  minute whatever the model decides.
- Every call is written down — see [Diagnosing a call](#diagnosing-a-call).

**Station integration**
- Tools come from the station's MCP server through an allowlist: requests
  (optionally confirmed first), search, exact queueing (off by default),
  announcements, segments. Track skipping, sound effects and station
  programming are **never** exposed at any setting, and the panel lists all 17
  tools with each one's status, so the boundary is visible.
- The DJ knows genre, mood, BPM, key and listener count where the station
  publishes them. A queued request returns its position, so "third up, about
  ten minutes" replaces implying it's playing now.
- Personas, cards, voices and model lists are discovered live. Point it at
  another SUB/WAVE and it re-homes itself.
- **Overlap protection** (default on): the call DJ and the on-air DJ are the
  same person, so while the station has the microphone the caller's replies
  queue rather than talk over the broadcast. Nothing is lost, and the card
  shows **On air** so the pause reads as the station being busy.
- Every call is a first call — the previous caller's business never reaches the
  next caller's prompt.
- The caller's browser is optionally tuned into the stream at pickup, so
  stations that refuse requests at zero listeners accept them.

**Operator panel**
- Every runtime choice lives behind the gear: station, providers, permissions,
  limits, call behaviour, house style. Changes apply to the next caller.
- API keys are stored server-side and never travel back to the browser.
- Test buttons exercise the real code paths — green means the call will work,
  not "the URL responded".

**Safety and limits** — two passwords (panel and phone, separately
rate-limited), usage caps on concurrency, hour, day, redial and actions, plus a
pause switch. Speech hygiene runs on every line before it reaches the voice.
Refusals are phrased in-world, never as codes. The caller is treated as an
untrusted stranger: stated in the prompt, enforced by the allowlist,
cross-origin writes refused. See [Security](#security).

## Architecture

| Component | What it does |
|---|---|
| `livekit-server` | WebRTC media — one room per call |
| `agent-worker` | Resolves the persona, builds the prompt, runs STT → LLM → TTS with MCP tools attached |
| `token-server` | Mints join tokens (the browser never sees LiveKit secrets), serves widget and panel, proxies station reads |
| `web-widget` | The call page — full page with settings, or a compact embeddable card |

Inside the worker, one call is one `CallSession` and every file under
`agent-worker/call/` is named after its job: the session and its lifecycle, the
tool registry and wrappers, the provider builders, the on-air gate, the action
ledger and the written record. `registry.py` describes the tool surface
**once** — the allowlist and the panel's reference both derive from it, so
adding a tool is one table entry plus one function.

The prompt is assembled in `agent-worker/brain/`: `briefing.py` is what the DJ
**knows** (now playing, recent, queue, its own on-air lines, segments),
`conduct.py` is how it **behaves** (momentum, triage, closing, tool etiquette,
safety), `assemble.py` joins both onto the persona and show cards. They change
for unrelated reasons — a new station field edits briefing, a bad call edits
conduct — so neither imports the other, and a test enforces it.

**Providers** are pluggable per leg: LLM (OpenAI, Google, Anthropic,
OpenRouter, Ollama), STT (Deepgram, OpenAI, Google, or in-process
faster-whisper on CPU — no key, no network), TTS (any OpenAI-compatible
endpoint, described by a JSON adapter, so a new backend is a config file).

**Performance**: the station's slow endpoint is kept warm by a background ping,
per-call reads are one concurrent snapshot, and the prompt is budgeted because
every token is paid on time-to-first-token every turn. Local TTS and STT are
measured honestly — the pipeline check reports realtime factors and warns when
a backend can't keep pace with playback.

## Getting started

### Docker (recommended)

Images publish to `ghcr.io/mrainone7p/wave-talk`; `:latest` tracks `main`. The
image includes the widget. A deploy needs four files:

```
wave-talk/
├── docker-compose.yaml    # from this repo
├── .env                   # from .env.example — REQUIRED
├── livekit.yaml           # from livekit.example.yaml, with a fresh secret
└── data/                  # panel settings and keys persist here
```

```bash
cp .env.example .env
cp livekit.example.yaml livekit.yaml   # generate a fresh secret for it
docker compose up -d
```

**`HOST_IP`** is the one deployment variable — the docker host's LAN address,
driving LiveKit's advertised media address, the browser URL and the webhook
callback. Otherwise `.env` only needs the LiveKit keypair; the rest is panel.

**`SUBWAVE_STREAM_URL`** should be the station's public `https://` stream. Left
blank it derives from the station's own address, which is plain http on the
LAN — and since the widget must be served over TLS for the microphone to work,
the browser blocks that stream as mixed content and the caller hears no
station. It fails silently, so set it first. A bare origin is enough; the
station's published mounts are discovered, mp3 first.

**Open `https://<HOST_IP>:8443`**, the bundled Caddy TLS front door. Browsers
only allow the microphone on HTTPS origins; the first visit shows a one-time
certificate screen. Add an API key, run the pipeline check, press Call. Plain
`http://<HOST_IP>:8100` works for everything except placing calls.

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

Everything lives in the panel behind the gear, and changes apply to the **next
caller**. Precedence is panel → `.env` → defaults; clearing a field falls
through. Every field carries its own help text.

| Group | Section | What it controls |
|---|---|---|
| Access | **Passwords** | Admin (controls) and optional guest code (phone). `CALLIN_ADMIN_KEY` is the recovery override |
| Connect | **Station** | Which SUB/WAVE this answers for, MCP endpoint, admin credentials |
| Connect | **API keys** | Provider keys, stored server-side, never shown back |
| Models & voice | **Brains** | LLM provider/model and STT. A local Whisper is baked in and used by default |
| Models & voice | **Voice** | TTS backend, URL, voice (default: mirrored per-persona from the station), adapter |
| Permissions & safety | **Caller permissions** | What a stranger may trigger, and overlap protection |
| Permissions & safety | **Usage controls** | Concurrency, hourly/daily caps, redial wait, actions per call, pause — the guard on API spend |
| Permissions & safety | **Speech hygiene** | Stage-direction stripping and the expletive filter |
| Call settings | **Call behaviour** | Who answers, greeting, time limits, idle check-ins, tune-in, **station stream URL** |
| Call settings | **Station awareness** | How much live context the DJ carries; each item costs latency every turn |
| Call settings | **House style** | Steers on conversation, answering, sign-off; prompt preview with token budget |
| Call settings | **Back to air** | The one-line on-air mention after a call |
| Call settings | **Call sounds** | Sound set, uploads or URLs, previews, volume |
| Reference | **What callers can ask** | Derived from the permissions above, including what is never available |
| Reference | **Station tools** | All 17 MCP tools and whether a caller can reach each |
| Reference | **Embed** | Copyable iframe snippet and preview |

Below settings, a **Diagnostics** block: pipeline check, speed test, recent
calls, server logs. The running version is stamped underneath.

### Call sounds

Each sound resolves in one order:

```
operator upload / URL   →   bundled asset   →   synthesized in the browser
```

The last tier means **no audio file has to exist anywhere**. A pack is a folder
in `assets/sounds/`:

```
assets/sounds/vintage/
  label.txt        optional — "Vintage — 1950s exchange"
  ring.mp3  pickup.mp3  hold.mp3  hangup.mp3  failed.mp3
```

It appears in **Sound set** automatically, no code change. Packs may be
partial — anything missing falls back to the synthesized sound, so one file is
a valid pack. Prefer mp3. `classic` and `phone` are the built-in sets; a folder
with either name supplies files for that set rather than creating a new one.
Conventions in [assets/sounds/README.md](assets/sounds/README.md). Panel
uploads override bundled assets; `SOUND_ASSETS_PATH` bind-mounts packs without
rebuilding.

## Calling from outside your network

Signalling rides your reverse proxy on 443, so the page loads for anyone.
**Audio doesn't.** Media goes direct to the address LiveKit advertises; if that
isn't reachable from the caller's network they get about fifteen seconds of
ringing and a dead line.

**LAN only — nothing to do.** The default.

**IPv6 — also nothing to do.** With `use_external_ip: true` LiveKit advertises
your public IPv6 address, and IPv6 has no NAT, so callers reach it directly
with no port forwarding. If your ISP gives you IPv6, off-network calling may
already work — worth knowing, because "it works from my phone" is then not
evidence that it works for everyone.

**IPv4 — one port.** Roughly half of internet users still have no IPv6, and
office wifi is frequently IPv4-only:

- forward **UDP 7882** (`rtc.udp_port`) to the LiveKit host — one rule, since
  LiveKit muxes every call over it. **TCP 7881** too as a fallback for networks
  that block UDP;
- set `use_external_ip: true`;
- **do not set `node_ip`** unless you know you need it. It overrides the public
  address STUN discovers, so a LAN value silently breaks every outside caller
  while working perfectly on your own network.

**The risk of opening that port.** It exposes LiveKit's media port to the
internet: yours to keep patched, and anyone who reaches it can attempt to
consume bandwidth. Only open it if you actually want outside callers. A public
port plus no guest code plus generous limits is an open invitation to spend
your API budget — pair it with a guest code and non-zero limits.

**Or don't self-host the media.** Point `LIVEKIT_URL`, `LIVEKIT_API_KEY`,
`LIVEKIT_API_SECRET` and `LIVEKIT_PUBLIC_URL` at a LiveKit Cloud project and
audio relays through them — no inbound ports, and it includes TURN, the only
thing that fixes restrictive corporate networks. Everything else stays local.

**Which tier are you on?** Run the pipeline check. *Browser media path* reports
the addresses the station offered and warns when the only public one is IPv6.

## Embedding

```html
<div id="subwave-callin"></div>
<script src="https://your-host/embed.js"></script>
```

Renders the compact card in an iframe with `allow="microphone"` set (its
absence is the classic silent embed failure). The panel never ships in an
embed.

| Attribute | Effect |
|---|---|
| `data-theme="light\|dark\|inherit"` | Force a theme, or `inherit` to match the host page's background (resolved before the frame loads, since a cross-origin frame can't see its parent). Omit for OS preference plus toggle |
| `data-captions="ticker\|full\|off"` | Embeds default to `ticker` — latest line only, fading, so the widget stays short |
| `data-height="260px"` | Frame height for tight layouts |
| `data-compact="false"` | Full card instead of the compact one |
| `data-origin` | Widget origin when the script is served from elsewhere |

Any page you embed on can mint call tokens, so treat an embed as publishing the
phone. Set a guest code if that isn't what you want.

## Security

**Two passwords, two jobs**, both under *Access → Passwords*, and the store
refuses to let them match.

**Admin** protects the panel, API keys and test buttons. Whoever holds it
controls the application and can spend your API keys. Until one is set the
panel stays open with a standing nudge — fine on a trusted LAN, but a
deliberate choice.

**Guest** is optional and protects only the phone: the Call button, `/token`,
and every embed. There's no username; the code is the whole thing. Admin is
accepted as a guest code, so an operator carries one password. Leave it empty
and anyone who loads the page can call, with usage limits as the only guard.

Lockout is 5 failures per address → 5-minute cooldown, a second round → banned
until restart, with guest failures counted separately. Locked out? Set
`CALLIN_ADMIN_KEY` (always accepted) or restart. Passwords travel with each
request, so beyond your LAN use the HTTPS front door — over plain http they are
readable on the wire.

**Before exposing beyond your LAN:**

1. `CALLIN_ALLOWED_ORIGINS` — set your real origins. `*` lets any page read
   config endpoints and mint call tokens.
2. Set the admin password, and a guest code if the page is public.
3. Fresh LiveKit keypair. Never deploy the example key.
4. Real TLS on the front door, so visitors see no certificate warnings.
5. Keep usage limits non-zero — every call spends real money. Set **calls per
   day** and **actions per call**, not just the hourly limit: an hourly cap
   alone still permits 24× that in a day.
6. Know what's plaintext: `data/secrets.json` holds API keys unencrypted.
   Protect the volume; never commit `.env` or `data/`.

## Known limitations

- **IPv4-only callers can't connect** without a forwarded port or a relay — see
  [Calling from outside your network](#calling-from-outside-your-network).
  Roughly half of users, and it fails silently from their side.
- **Local TTS may not keep pace with playback.** Above ~1.0× realtime, audio
  gaps mid-sentence. The speed test measures it.
- **One station per deployment** — everything is discovered from a single
  SUB/WAVE instance.
- **API keys are stored unencrypted on disk.** `data/secrets.json` is written
  `0600` where the OS honours it (Windows ACLs don't map cleanly) and kept
  separate from `settings.json`, so settings stay safe to copy or paste. Keys
  are never returned to the browser and never logged — but anyone who can read
  the volume can read them, so the volume is the real protection.
- **Two shared passwords, not user accounts.** One admin, one optional guest,
  each a single secret shared by everyone who has it. No per-person identity,
  so nothing attributes an action to a particular operator.
- **Recent calls keeps the newest 40.** A diagnostic aid, not an archive.
- **The panel is not built for hostile exposure.** It assumes an operator on a
  trusted network who has set a password.

## Troubleshooting

Run the **full pipeline check** first; it walks every leg in call order and
names the fix. The classics:

- **Hangs at "Ringing" while server checks pass** — LiveKit is advertising an
  address the browser can't reach. Set `HOST_IP` and recreate the container;
  the same cause shows as webhooks on a `172.x` address. Check the firewall
  allows **UDP 7882** and TCP 7881.
- **"This page can't use the microphone"** — the page is on plain
  `http://<lan-ip>`, where browsers refuse capture. Use the TLS page.
- **The DJ is there, the music isn't** — an `http://` stream on an `https://`
  page is blocked as mixed content, silently. Set **Station stream URL** to an
  https one; the *Station stream* stage says so outright.
- **Locked out of the panel** — set `CALLIN_ADMIN_KEY`, or restart to clear
  bans. To remove the password entirely, delete `data/admin-auth.json`.
- **Voice test 400s on local TTS** — the voice id doesn't exist on that server
  (cloud names and local ids aren't interchangeable). *Reload voice list* after
  switching backend.
- **Works on the LAN, not outside** — see [Calling from outside your
  network](#calling-from-outside-your-network). Chrome may also ask LAN
  visitors to "connect to devices on your local network"; that's Private
  Network Access, one-time and harmless.

## Diagnosing a call

**Start with Recent calls**, under *Diagnostics*. Each call writes one file as
it ends — both sides, every tool with its result, the config it ran under, and
anything that failed:

```
2026-08-04 23:34:36  Dalia  ·  136s  ·  6 caller turns
  google/gemini-3.1-flash-lite  stt=local/base.en  tts=local
  ⚠ station 503 on /request
23:34:45 DJ      You're through to the booth — what's on your mind?
23:34:48 CALLER  Can you play something fun?
23:34:49   tool  subwave_request_song → Added to the queue
```

The tool column is the point: the DJ saying it did something is a claim, that
line is the receipt. The config line ties a bad call to the setting that caused
it. Files live in `data/calls/`, newest 40 kept.

The other rows: **Full pipeline check** names the first thing that would break;
**Speed test** reports time to first audio per leg (over ~1.5s sounds laggy);
**Server logs** shows recent activity.

## Logs and tests

Local runs write rotating logs to `data/logs/`. Under Docker the same lines go
to container stdout, where the worker logs its version at startup and every
call as `heard:` / `said:` / `tool:` lines. `/health` reports the running
version — check both containers match, since they ship as one image but run as
two.

```bash
cd agent-worker && python -m unittest test_sidecar
```

covers the speech filter, settings precedence, secrets and passwords, the
lockout ladder, usage limits, the tool registry, prompt assembly, sound packs,
the call record and the call's lifecycle seams. CI runs it before building an
image, so a failing suite never reaches `:latest`.
