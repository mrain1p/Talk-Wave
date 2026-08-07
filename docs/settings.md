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
| Configuration | **Access** | Call-in access (automatic, open, guest code, admin only), the admin password for this panel, and the optional guest code for the phone. `CALLIN_ADMIN_KEY` is the recovery override |
| Configuration | **Station** | Which SUB/WAVE this answers for, the derived MCP endpoint, and the station admin credentials that unlock on-air messages, segments, programme beats, skips, show takeover, the back-to-air mention and voice mirroring |
| Configuration | **Connections** | Provider API keys, stored server-side, never shown back |
| Configuration | **AI brains** | The LLM that thinks: provider, model, endpoint, temperature. Only providers you hold a key for are listed — Ollama always, since it needs none |
| Configuration | **Voice** | TTS backend, endpoint, voice (default: mirrored per-persona from the station), adapter |
| Configuration | **Ears** | Speech-to-text provider and model, listed on the same key rule. A local Whisper is baked in and used by default |
| Permissions & safety | **Caller permissions** | What a caller may trigger, and **which caller** — each row is set to the least trusted tier that gets it (off / anyone / guest code / admin), including the three station-wide switches (skip the current track, fire a programme beat, put a different show on air) that reach every listener. See [security](security.md) |
| Permissions & safety | **Usage controls** | Concurrency, hourly/daily caps, redial wait, actions per call — the guard on API spend |
| The conversation | **Who answers** | Which DJ picks up, the greeting style or a written opening line, and whether the caller is asked their name |
| The conversation | **Turn-taking** | When the DJ decides you've finished speaking, and whether a caller may talk over it. The biggest lever on whether a call *feels* like a phone call |
| The conversation | **House style** | Steers on conversation, answering, sign-off; prompt preview with token budget |
| The conversation | **Station awareness** | How much live context the DJ carries; each item costs latency every turn |
| The conversation | **Speech hygiene** | Stage-direction stripping and the expletive filter |
| Running the line | **Call length** | How early the DJ may hang up, the hard limit, and idle check-ins |
| Running the line | **Sharing the microphone** | Overlap protection: the call DJ and the on-air DJ are one voice |
| Running the line | **Tune the caller in** | Whether the caller's browser pulls the broadcast during a call, the **stream URL**, and how loud it sits behind the DJ |
| Running the line | **Back to air** | The one-line on-air mention after a call |
| Running the line | **Call sounds** | Sound set, uploads or URLs, previews, volume |
| Running the line | **Call transcripts** | Whether both sides of a call are written to disk at all, and how many are kept |
| The call card | **Player settings** | What the card shows, answered separately for this page and for an embed; what the Call button says (“Call the DJ”, the live DJ's name, or your own words); colours — including **the station's own**, read from its `/themes` and following the on-air show; the post-call thumbs up/down |
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
