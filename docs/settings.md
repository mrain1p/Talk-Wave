# Settings reference

Every setting the panel exposes, what it falls back to, and what it changes about a call.

Settings live in three layers — `data/settings.json`, then the environment, then the built-in defaults. Clearing a field in the panel means *fall through to the layer below*, not *set it to empty*.

[← back to the README](../README.md)

---

## Settings reference

Everything lives in the panel behind the gear, and changes apply to the **next
caller**. Precedence is panel → `.env` → defaults; clearing a field falls
through. Every field carries its own help text.

| Group | Section | What it controls |
|---|---|---|
| Access | **Passwords** | Admin (controls), optional guest code (phone), and **who can call the booth** — automatic, open, guest code, or admin only. `CALLIN_ADMIN_KEY` is the recovery override |
| Connect | **Station** | Which SUB/WAVE this answers for, MCP endpoint, admin credentials |
| Connect | **API keys** | Provider keys, stored server-side, never shown back |
| Models & voice | **Brains** | LLM provider/model and STT. A local Whisper is baked in and used by default |
| Models & voice | **Voice** | TTS backend, URL, voice (default: mirrored per-persona from the station), adapter |
| Permissions & safety | **Caller permissions** | What a stranger may trigger, overlap protection, and the three station-wide switches (skip the current track, fire a programme beat, put a different show on air) that reach every listener — all off by default |
| Permissions & safety | **Usage controls** | Concurrency, hourly/daily caps, redial wait, actions per call, pause — the guard on API spend |
| Permissions & safety | **Speech hygiene** | Stage-direction stripping and the expletive filter |
| Call settings | **Call behaviour** | Who answers, greeting, how early the DJ may hang up and the hard limit, idle check-ins, tune-in, **station stream URL**, and whether call transcripts are kept at all |
| Call settings | **Turn-taking** | When the DJ decides you've finished speaking, and whether a caller may talk over it. The biggest lever on whether a call *feels* like a phone call |
| Call settings | **Station awareness** | How much live context the DJ carries; each item costs latency every turn |
| Call settings | **House style** | Steers on conversation, answering, sign-off; prompt preview with token budget |
| Call settings | **Back to air** | The one-line on-air mention after a call |
| Call settings | **Call sounds** | Sound set, uploads or URLs, previews, volume |
| Call settings | **Player settings** | What the card shows, answered separately for this page and for an embed; the Call button's label; theme; the post-call thumbs up/down; and the copyable iframe snippet |
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
