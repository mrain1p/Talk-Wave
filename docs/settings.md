# Settings reference

Every setting the panel exposes, what it falls back to, and what it changes about a call.

[← back to the README](../README.md)

---

Everything lives in the panel behind the gear, and changes apply to the **next caller** — no restarts. Every field carries its own help text.

> **Precedence is `data/settings.json` → environment → defaults.** Clearing a field in the panel means *fall through to the layer below*, not *set it to empty*.

| Page | What you set there |
|---|---|
| **Dashboard** | Nothing — it reads live state and acts. [Its own page](dashboard.md) |
| **Configuration** | The station, and the three engines: Brains, Voice, Ears |
| **Permissions & safety** | Who may call, what they may trigger, and the spend caps |
| **The booth** | What the DJ knows, how it behaves, and whether transcripts are kept |
| **Calls** | Greeting, turn-taking, closing, the station under the call, sounds, effects |
| **Voicemail** | The answering machine and the soundbite studio |
| **Texts** | The typed line's clocks, ceilings and opening behaviour |
| **On air** | The two doors to the broadcast, and ducking |
| **Players** | Everything the card shows, per surface — page and embed |
| **Reference** | What callers can ask, and the station's tool surface |
| **Diagnostics** | Pipeline check, speed test, recent calls, server logs |

## How the panel is laid out

The panel reads as **pages under one URL**: the search box lives in the masthead itself, the page picker sits under the coral rule as a sticky band, and exactly one page shows below it.

**The address carries the page** — `/settings#calls`, `#voicemail`, `#texts` — so a page survives a refresh and the browser's back button works. The search reads every page: typing shows the matching rows from everywhere, and clearing it returns to the page you were on.

### The dashboard is the landing page

[Its own page](dashboard.md) walks it in full. In short: the four station tiles (who is on air, station health, the brains/voice/ears chain, who may call), then **Transmission**, then the **Activity** charts.

**Transmission** is The Line's kill switch wearing a real toggle, over its three **Lines** — Live calls, Voicemail, Text line — each pairing a smaller switch with the tile for the traffic it produces, and a caption saying what the combination amounts to.

- Every switch **posts the moment it is pressed**.
- A paused line holds every door in amber and nothing answers — **the kill switch outranks the answering machine.**

Beside Transmission, **Notifications** names what still stands between this deployment and a working call. Each row jumps to its fix, any page holding a gap wears a coral pin in the picker, and an empty column says the line is ready.

### The Players page has furniture of its own

Three tabs, beside a **pinned live preview**:

| Tab | What is in it |
|---|---|
| **The card** | one element block per part of the card, in card order |
| **Behaviour** | nothing visual |
| **Embed** | the frame, and the copyable snippet |

The preview is **the real card in a frame**, following the form before anything is saved and resolved by the same code that answers a real caller — so it cannot drift from the thing it previews. It offers Page and Embed views, with the embed dressed as the Shape chosen under The frame.

- **Hovering a setting** outlines the element it controls on the card.
- **Clicking an element** on the card flashes the block that owns it.
- On a phone the preview docks to the bottom edge, with a chevron to fold it away.

---

## Configuration

### SUB/WAVE Station

Which station this answers for, the derived MCP endpoint, and the station admin credentials.

Those credentials unlock on-air messages, segments, programme beats, skips, show takeover, the back-to-air mention, and voice mirroring.

### Brains (LLM)

The LLM that thinks: provider, model, endpoint, temperature, and the provider API keys — OpenAI, OpenRouter, Anthropic, Google, DeepSeek, Requesty, Vercel AI Gateway.

- **Only providers you hold a key for are listed.** Ollama, an OpenAI-compatible server and the station's locca always appear, since they need none — and they lead the list, because a fresh install can actually pick them.
- Keys are stored server-side and never shown back. There is no separate Connections section.

Which models actually carry a call: [what to run](models.md).

### Voice (TTS)

Backend, adapter, endpoint, voice (default: mirrored per-persona from the station), and the speech keys — a dedicated TTS server key, ElevenLabs, Fish Audio. Adapters ship for OpenAI-compatible servers, ElevenLabs, Fish Audio, and SUB/WAVE's own Remote `/speak` contract.

Also here, the **voice effect**: ten colours on the DJ's voice — telephone, CB, walkie-talkie, AM, megaphone, underwater, stadium PA, intercom, shortwave, lo-fi — applied in the caller's browser only, with an **intensity dial** (0–100, full character down to a hint of radio) and a **Test with effect** button that plays the configured voice through it at that intensity.

> A **cloud voice** (ElevenLabs, Fish Audio, or the station's Remote `/speak`) is warmer and quicker to first audio than a local model on CPU. And leaving the voice mirrored from the station keeps the call-in DJ sounding like the on-air one.

### Ears (STT)

Speech-to-text provider and model, listed on the same key rule, plus the Deepgram key. A local Whisper is baked in and used by default.

The caller's microphone is captured with **echo cancellation, noise suppression and automatic gain on by default** — so the station's own output playing in the caller's room isn't transcribed back, and on a speakerphone the DJ's voice stays out of the caller's transcript.

> The baked-in Whisper needs no key or network and is the right place to start. A **cloud STT** (Deepgram, OpenAI, Google) transcribes more accurately, and is worth switching to if callers are misheard — most likely from a phone in a noisy place.

---

## Permissions & safety

### How the doors work

A reference card, not settings: the three tiers (Admin always, Guest gate, Open), and the layers behind them — PBKDF2 hashes, fail2ban-style lockouts, write-only keys, signed short-lived tokens, the tool allowlist.

### Access

- Call-in access as three ticks, one choice apiece. Admin is always a door; **Guest code and Anyone are mutually exclusive** — an open line has no code door, and the code stops elevating.
- The admin password for this panel, and the optional guest code for the phone.
- `CALLIN_ADMIN_KEY` is the recovery override.
- A guest-code expiry (hours, default 24) for shared machines. The card also offers a lock button to forget the code immediately.

### Caller permissions

What a caller may trigger, and **which caller** — each row set to the least trusted tier that gets it: off / anyone / guest code / admin. The range, low-harm first:

- **Like the track on air** — the same heart any listener taps. Needs no station credentials.
- **Find music by how it sounds** — two discovery reads: a "sounds like" search over a description, and "more like this" off the track on air. They match the analysed audio rather than words in a title, change nothing, and cost nothing against Actions per call. Browsing by mood, genre and era rides the library-search row instead.
- **Take a track back out of the queue** — off by default: the queue is shared, so it can pull a record somebody else asked for, which is why the station gives its own listeners no cancel.
- **The three station-wide switches** — skip the current track, fire a programme beat, put a different show on air. These reach every listener.

Rows that need the station admin credentials carry a **Station admin** badge, coral until the credentials are stored — including **un-like the track**, the operator's own curation heart, admin only.

Tier defaults and the full risk picture: [security](security.md).

### Usage controls

Calls at once, per hour and per day, redial wait, actions per call — the guard on API spend.

### Call length

The hard ceiling on one call — one more spend limit, beside the others.

### Speech hygiene

Stage-direction stripping, and the expletive filter.

---

## The booth

### Station awareness

How much live context the DJ carries. **Each item costs latency every turn.**

### House style

Steers on conversation, answering and sign-off, plus a prompt preview with token budget.

**Action receipts** decide where the ✅ card a station action leaves lands — on calls, texts and voicemail alike:

| Setting | Where the receipt goes |
|---|---|
| after the DJ's line *(default)* | words first, then the paperwork |
| as it happens | the receipt leads the line |
| off | the action still runs and the record still lists it — only the card is withheld |

The machine has no DJ line, so its delivery receipt shows unless off.

### Transcripts

Whether both sides of a conversation — calls, texts and voicemails alike — are written to disk at all, and how many are kept.

---

## Calls

### Greeting

Which DJ picks up, the greeting style or a written opening line, and whether the caller is asked their name.

### Turn-taking

When the DJ decides you have finished speaking, and whether a caller may talk over it. **The biggest lever on whether a call *feels* like a phone call.**

### Closing the call

The greeting's mirror: the sign-off steer, the idle check-ins, and how early the DJ may hang up — how a call ends, in character.

### Tune the caller into the station

Whether the caller counts as a listener (which is what makes requests work), whether the broadcast is piped audibly into the call, the **stream URL**, and how loud it sits behind the DJ.

### Back-to-air commentary

The one passing line after a call ends — distinct from the announcements and segments a caller triggers mid-call, which run under their own permissions.

### Call sounds

Sound set, uploads or URLs, previews, volume, and a **sound board**: bundled clips (dial-up handshakes, the Wilhelm scream) and uploads in one table with lengths and an editable category per sound — a soft, filterable sound pack. Links to good free sources (freesound CC0, Pixabay) sit under it.

Every dropdown names the default it stands for ("Default — the Exchange set's ring").

The **voicemail beep** lives here too — WAV uploads only, since the server plays it — with its own preview button and a verdict line saying whether the file converts.

### Voice effects

The ten colours above on the DJ's voice, with its intensity dial (default 60) and Test with effect — applied in the caller's browser only.

A **Per-DJ effects** list gives any persona its own colour, saved as picked, with a Test in that DJ's own voice. The override rides `/live` the moment they are on air.

---

## Voicemail

### The machine

An **Enable voicemail** master switch, then:

- per-persona greeting lines — editable, playable, deletable — staged one at a time with live progress
- a per-tier caller permission
- when it picks up: never / when a live call is impossible / always
- the greeting, and the per-message ceiling
- where messages go: held in the panel, a station request, or handed to the on-air DJ

**Triage** is the fourth destination: the model reads each message and picks a request, an on-air mention, or a station segment, bounded by the caller permissions.

Greetings are **staged** — rendered once per persona in their own voice, re-rendered only when the text, voice or backend changes — or **fresh each call**, written in persona at pickup with the staged clip as the backup. The greeting text takes `{station}`, `{dj}` and `{show}`, and with nobody on air the machine answers as the station itself in your default voice.

> **Every message lands in the panel's list whatever the delivery mode.** Nothing is recorded as audio; the transcript is the message.

The full walkthrough, including the soundbite studio: [Voicemail](VOICEMAIL.md).

---

## Texts

### Text line

A **Take text chats** master switch (on the dashboard beside Live calls and Voicemail), then the clocks and ceilings for typed conversation with whoever is on air:

- how long quiet before a chat closes
- the per-chat message ceiling, and the longest a chat may live
- how many chats may be open at once, and new chats per hour and per day
- a **per-caller reopen wait** — the text line's Redial-wait, and scriptable where a call is not
- the per-minute message cap
- a **reply timeout**, so a stalled model can't leave a caller watching the typing dots forever

**Opening the line** — whether the booth **greets first** when a fresh chat connects: a canned line (instant, with `{station}`/`{dj}`/`{show}` filled), one **written in persona** each time, or off. *A text line that answers with silence reads as broken.*

Who may open the line is the **Text the booth** permission under Caller permissions.

Same brain, same tools and same receipts as the phone, over a plain WebSocket — no WebRTC, so **it works where a call cannot, and keeps working while the media server is down.**

- The DJ shows a **typing indicator** while composing.
- It **reports the real outcome** of any action it takes, rather than narrating success it hasn't confirmed.
- Chats resume per browser, and land in Recent conversations as `kind: chat`.
- **Nudge a quiet caller** (on by default, ~15s) keeps a chat feeling like a conversation rather than a turn-based move: when the caller has gone quiet with the ball in their court, the DJ sends one short in-persona line — never while the DJ is the one still owing a reply.

Where the ✅ receipt cards land is the booth-wide **Action receipts** setting under House style. The Line's pause switch closes this door too.

---

## On air

### Doors to air

The phone-in's two quick kills — **Calls may go on air** and **Voicemails may go on air** — one per door, so one closes without touching the other. The dashboard's Live-on-air cluster flips the same two switches.

**A soundbite airs as** either the DJ reading it (works on any deployment) or the caller's own voice (needs the mixer wiring from [the on-air page](on-air.md)).

Who may use the route at all — and the on-air window and delay around it — stays under Caller permissions, greyed while both doors here are shut.

### On-air ducking

Overlap protection: the call DJ and the on-air DJ are one voice.

| Station | How the hold works |
|---|---|
| **SUB/WAVE 1.8+** | Exact. The station warns that a voice is coming (`voice.queued`), the call keeps flowing until **Hand over before air** (default 5s) from the forecast landing, the DJ says its hand-over line and steps back, and the measured `voice.start`/`voice.end` bound the hold to the second |
| **Older** | The hold anchors on the handoff-stamped push and is sized from the words, with **Handoff-to-air lag** (default 2s, ignored for 1.8 evidence) riding the tail |

---

## Players

### Top corner

The small controls on the card's top edge, **each answered for this page and for an embed**:

- the "What can I ask?" button
- the light/dark toggle
- the settings gear — page only, since an embed never loads the panel's code
- a **"Sign in" button**, so a caller can enter the guest code or admin password to unlock more of what they can ask for
- the **link out of the card** — one more corner button going wherever you send it (your station's own page by default), with its label, icon and a per-surface Show it row, all hidden until the link itself is switched on

### Who's on air

The DJ block, per surface: the DJ photo with its **shape** right under it (round for a portrait, square to match a host page's artwork), the show name, the DJ tagline, and the now-playing line.

### The line box

Every call-state string in one place, in call order — Ringing, Answering, Connecting, Connected — waiting, On the line, Recording, Line closed, Message only, Call ended.

Overridable in the station's own voice, with `{station}` `{dj}` `{show}` `{track}` `{tagline}` filled live. **Focus a field and the preview card's line box shows that state** — your typed words, or the built-in default — until you click away. Also here: whether the transcript names the DJ.

### The talk bar

**Push to talk**, per surface, on by default — the caller's mic is closed except while they hold or latch the talk bar, and space works on a keyboard. Switch it off for an open mic from pickup. Plus what the bar says.

### The buttons

Everything about the three doors in one block:

- their **order** — drag or arrows, and the preview card reorders as you do it
- what the Call button says: "Call the DJ", the live DJ's name, or your own words
- a **per-door display** — a word tick and an icon tick each, so one door can read as a word and another as a bare icon on a tight embed. The icon is a line drawing in the button's own ink, not an emoji glyph, and **a door left with neither ticked falls back to its word, so it is never blank**
- whether the **"Text the booth"** and **"Leave a message"** buttons are offered per surface, so the machine and the text line sit beside Call. A busy call also offers the text line as a fallback, even where its permanent button is off
- the Hang up, Send and message-button wording

### Surface

Colours — including **the station's own**, read from its `/themes` and following the on-air show — and a **skin**.

> **Skins are experimental.** Sixteen looks for the card, from a switchboard and a rack unit to green phosphor, paper and Windows 95, each with its own palette and, on most, a small artefact that sits in the transcript box between calls and disappears the moment one starts. **A skin cannot change the card's size or controls, only its surface.**

### On the caller's phone

The Behaviour tab — nothing visual:

- whether calls start on the **loudspeaker**
- the card's **listener count** — "On air now · 2 listening", shown from one listener up, so a quiet hour never paints a zero
- its **heart button** — the same public like any listener page sends, beside the record on the card. Both on by default
- the swipe-up station player, and whether the page starts on it
- how loud the station plays under the answering machine

### After the conversation

The thumbs up/down, **per door**: ask after a call, after a text chat, and after a voicemail are three separate switches.

### The frame

The Embed tab — opening it flips the preview to the embed.

- **Allowed origins** — the comma-separated https origins that may embed the card and place calls on your API keys. `CALLIN_ALLOWED_ORIGINS` is the env baseline; empty means this page only; `*` is dev-only; a save applies on the next request with no restart.
- **Flush by default** — the embedded card draws no outline or sheet of its own and sits in whatever area the host gives it, with a tick to draw the main page's card outline back on.
- The copyable snippet, with a **Shape** picker: the inline card, a floating **launcher** pill in a corner, a **docked** bar across the bottom, or an inline **button** that opens the card in a centred pop-up — plus starting look and captions.
- The preview **wears the selected shape**. Pick a launcher and the preview shows the pill; press it and the card opens where that shape would put it, so you see what you are copying before you paste it.

---

## Reference

### What callers can ask

Derived from the permissions above, grouped by what the DJ does with the ask:

- **just talk / ask about the station** — reads and in-character answers, no permission needed
- **request music**
- **put something on the air** — reaches every listener
- **leave a message**

Each row shows which tier gets it, and the actions that are never available are listed at the end. The same grouped list is the caller's own "What can I ask?" popup.

### Station tools

**Station tools** is the station's whole surface — 36 tools in all: 7 handed straight through from the station's MCP server, 26 served by wrappers of ours (retries, guards, the action ledger), and 3 that are never on a call line at any setting — each row saying whether a caller can reach it.

Below the list, **How the DJ finds a record** sets out the five ways in and which kind of ask takes each, and a short note names what the station can do that the call line still doesn't use.

---

## Diagnostics

The last page: pipeline check, speed test, recent calls, server logs. The running version is stamped underneath.

## Call sounds: how a sound resolves

Each sound resolves in one order:

```
operator upload / URL   →   bundled asset   →   synthesized in the browser
```

**The last tier means no audio file has to exist anywhere.**

A pack is a folder in `assets/sounds/`:

```
assets/sounds/vintage/
  label.txt        optional — "Vintage — 1950s exchange"
  ring.mp3  pickup.mp3  hold.mp3  hangup.mp3  failed.mp3
```

It appears in **Sound set** automatically, with no code change.

- **Packs may be partial** — anything missing falls back to the synthesized sound, so one file is a valid pack.
- Prefer mp3.
- `classic` and `phone` are the built-in sets. A folder with either name supplies files for that set rather than creating a new one.
- Panel uploads override bundled assets, and `SOUND_ASSETS_PATH` bind-mounts packs without rebuilding.

Conventions in [assets/sounds/README.md](../assets/sounds/README.md).
