# Settings reference

Every setting the panel exposes, what it falls back to, and what it changes about a call.

Settings live in three layers — `data/settings.json`, then the environment, then the built-in defaults. Clearing a field in the panel means *fall through to the layer below*, not *set it to empty*.

[← back to the README](../README.md)

---

## Settings reference

Everything lives in the panel behind the gear, and changes apply to the **next
caller**. Precedence is panel → `.env` → defaults; clearing a field falls
through. Every field carries its own help text.

Above every section sits the **Pause all calls** switch — the kill switch, kept
out of the sections so it can be reached without opening one — and a row of jump
links, one per group below.

| Group | Section | What it controls |
|---|---|---|
| Configuration | **SUB/WAVE Station** | Which station this answers for, the derived MCP endpoint, and the station admin credentials that unlock on-air messages, segments, programme beats, skips, show takeover, the back-to-air mention and voice mirroring |
| Configuration | **Brains** | AI — the LLM that thinks: provider, model, endpoint, temperature, and the provider API keys themselves (OpenAI, OpenRouter, Anthropic, Google, DeepSeek, Requesty, Vercel AI Gateway). Only providers you hold a key for are listed — Ollama always, since it needs none. Keys are stored server-side and never shown back; there is no separate Connections section |
| Configuration | **Voice** | TTS — backend, adapter, endpoint, voice (default: mirrored per-persona from the station), and the speech keys (a dedicated TTS server key, ElevenLabs, Fish Audio). Adapters ship for OpenAI-compatible servers, ElevenLabs, Fish Audio and SUB/WAVE's own Remote `/speak` contract. Also the **voice effect** (telephone / CB / walkie-talkie colour on the DJ's voice, applied in the caller's browser only) with an **intensity dial** (0–100 — full character down to a hint of radio), and a **Test with effect** button that plays the configured voice through it at that intensity |
| Configuration | **Ears** | STT — speech-to-text provider and model, listed on the same key rule, plus the Deepgram key. A local Whisper is baked in and used by default |
| Permissions & safety | **Access** | Call-in access (automatic, open, guest code, admin only), the admin password for this panel, and the optional guest code for the phone. `CALLIN_ADMIN_KEY` is the recovery override. A guest-code expiry (minutes) for shared machines — the card also offers a lock button to forget the code immediately |
| Permissions & safety | **Caller permissions** | What a caller may trigger, and **which caller** — each row is set to the least trusted tier that gets it (off / anyone / guest code / admin), including the three station-wide switches (skip the current track, fire a programme beat, put a different show on air) that reach every listener. See [security](security.md) |
| Permissions & safety | **Usage controls** | Concurrency, hourly/daily caps, redial wait, actions per call — the guard on API spend |
| The conversation | **Who answers** | Which DJ picks up, the greeting style or a written opening line, and whether the caller is asked their name |
| The conversation | **Turn-taking** | When the DJ decides you've finished speaking, and whether a caller may talk over it. The biggest lever on whether a call *feels* like a phone call |
| The conversation | **House style** | Steers on conversation, answering, sign-off; prompt preview with token budget |
| The conversation | **Station awareness** | How much live context the DJ carries; each item costs latency every turn |
| The conversation | **Speech hygiene** | Stage-direction stripping and the expletive filter |
| Running the line | **Call length** | How early the DJ may hang up, the hard limit, and idle check-ins. Also the line's mode: **Take live calls** on/off pairs with the voicemail switch below, so the line can be a phone, a phone with an answering machine, voicemail-only, or closed |
| Running the line | **Sharing the microphone** | Overlap protection: the call DJ and the on-air DJ are one voice |
| Running the line | **Tune the caller in** | Whether the caller's browser pulls the broadcast during a call, the **stream URL**, and how loud it sits behind the DJ |
| Running the line | **Back to air** | The one-line on-air mention after a call |
| Running the line | **Call sounds** | Sound set, uploads or URLs, previews, volume. Every dropdown names the default it stands for (“Default — the Exchange set's ring”); the **voicemail beep** lives here too — WAV uploads only, since the server plays it — with its own preview button and a verdict line saying whether the file converts |
| Running the line | **Voicemail** | The answering machine, with per-persona greeting lines (editable, playable, deletable) staged one at a time with live progress, and a per-tier caller permission. Destinations include **triage** — the model reads each message and picks a request, an on-air mention, or a station segment, bounded by the caller permissions: when it picks up (never / when a live call is impossible / always), the greeting, the per-message ceiling, and where messages go (held in the panel / a station request / handed to the on-air DJ). Greetings are **staged** — rendered once per persona in their own voice and re-rendered only when the text, voice or backend changes — or **fresh each call**, written in persona at pickup with the staged clip as the backup; the greeting text takes `{station}`, `{dj}` and `{show}`, and with nobody on air the machine answers as the station itself in your default voice. Every message lands in the panel's list whatever the delivery mode. Nothing is recorded as audio; the transcript is the message |
| Running the line | **Call transcripts** | Whether both sides of a call are written to disk at all, and how many are kept |
| The call card | **Player settings** | What the card shows, answered separately for this page and for an embed, with **a live preview of the real card** beside it that follows the form before you save; the DJ photo's shape; what the Call button says (“Call the DJ”, the live DJ's name, or your own words); colours — including **the station's own**, read from its `/themes` and following the on-air show; whether calls start on the **loudspeaker**; the post-call thumbs up/down; **push to talk** per surface — the caller's mic is closed except while they hold or latch the talk bar (space works on a keyboard); a **“Leave a message” button** per surface, so the machine is on offer beside Call |
| The call card | **Embed on another page** | The copyable iframe snippet, and a compact preview |
| Reference | **What callers can ask** | Derived from the permissions above, including what is never available |
| Reference | **Station tools** | The station's whole tool surface — the 17 MCP tools plus the two takeover actions we serve ourselves — and whether a caller can reach each |

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
