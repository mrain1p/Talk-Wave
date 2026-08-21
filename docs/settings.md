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
| **Permissions & safety** | Who may call, and what they may trigger |
| **Reference** | What callers can ask, and the station's tool surface |
| **The booth** | What the DJ knows, how it behaves, and whether transcripts are kept |
| **Calls** | The call caps, greeting, turn-taking, closing, the station under the call, sounds, effects |
| **Voicemail** | The answering machine and the soundbite studio |
| **Texts** | The typed line's clocks, ceilings and opening behaviour |
| **On air** | The two doors to the broadcast, and ducking |
| **Players** | Everything the card shows, per surface — page and embed |
| **Diagnostics** | Pipeline check, speed test, recent calls, server logs |

## How the panel is laid out

The panel reads as **pages under one URL**: the search box lives in the masthead itself, the page picker sits under the coral rule as a sticky band, and exactly one page shows below it.

**The address carries the page or the section** — `/settings#calls` turns to a page, `/settings#turns` turns to Calls *and opens Turn-taking*. Both survive a refresh, both work with the back button, and both can be handed to somebody. Cross-references in help text ("under Caller permissions") are links to the section they name.

### The finder is the index

Typing in the masthead box searches **every page at once** and shows, above the results, how wide the hit is and which pages it reaches — *"12 settings on 4 pages — Permissions & safety · Calls · On air · Players"*. Each result carries its page name ahead of the section name, so a search teaches the layout instead of only answering the question.

- It reads labels, help lines, dropdown options, **section prose and buttons** (so "password" finds Access, whose control is a button), and each setting's own **synonyms** — "color", "avatar", "mute", "rate limit", "timeout", "spam" all land where you would expect.
- Matches start at a word boundary, so "rate" no longer finds *moderate* and *separate*.
- A result whose **prerequisite is off** is dimmed and marked *needs "<the switch>"*, and the switch is pulled into the results beside it — otherwise you can set a value, save, and watch nothing happen.
- A section reached through its prose shows with its settings still filtered: the answer is the section, not everything in it.

Clearing the box returns to the page you were on.

### One-section pages open themselves

Voicemail, Texts and Open Lines hold exactly one section each. Rather than making you turn to a page and then open the only thing on it, the page **is** the section: it arrives open, with no chevron. The summary stays for its blurb and its state chip.

### The dashboard is the landing page

[Its own page](dashboard.md) walks it in full. In short: the four station tiles (who is on air, station health, the brains/voice/ears chain, who may call), then **Transmission**, then the **Activity** charts.

**Transmission** is The Line's kill switch wearing a real toggle, over its three **Lines** — Live calls, Voicemail, Text line — each pairing a smaller switch with the tile for the traffic it produces, and a caption saying what the combination amounts to.

- Every switch **posts the moment it is pressed**.
- A paused line holds every door in amber and nothing answers — **the kill switch outranks the answering machine.**

Beside Transmission, **Notifications** names what still stands between this deployment and a working call. Each row jumps to its fix, any page holding a gap wears a coral pin in the picker, and an empty column says the line is ready.

### The Players page has furniture of its own

Three **bands** down one column, beside a **pinned live preview**:

| Band | What is in it |
|---|---|
| **The card** | one element block per part of the card, in card order |
| **Behaviour** | nothing visual |
| **Embed** | the frame, and the copyable snippet |

They were tabs until 0.98.22. Three tabs over six, two and one section hid two thirds of the page and put *Start calls on loudspeaker* four levels down — Players → Behaviour → On the caller's phone → row — where every other setting in the panel is three. As captions the grouping still reads and the whole page scrolls.

The preview is **the real card in a frame**, following the form before anything is saved and resolved by the same code that answers a real caller — so it cannot drift from the thing it previews. It offers Page and Embed views, with the embed dressed as the Shape chosen under Embed frame.

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

**Test hearing** speaks one known line with the configured voice and hears it back with the configured ear, reporting what it heard, what share of the words survived, and how long it took against the length of the clip. The sample is synthesized rather than recorded from your microphone, so there is no permission prompt and every run is the same sentence.

> **This is the only check that reaches a cloud ear.** The Speed test measures the built-in Whisper for real, but for Deepgram, OpenAI or Google it records a flat 400ms estimate and never calls them — so before this, a wrong or expired key gave a green panel and a green speed test, and the first symptom was a caller being misheard on air.

---

## Permissions & safety

### How the doors work

A reference card, not settings: the three tiers (Admin always, Guest gate, Open), and the layers behind them — PBKDF2 hashes, fail2ban-style lockouts, write-only keys, signed short-lived tokens, the tool allowlist.

### Access

- Call-in access as three ticks, one choice apiece. Admin is always a door; **Guest code and Anyone are mutually exclusive** — an open line has no code door, and the code stops elevating.
- The admin password for this panel, and the optional guest code for the phone.
- `CALLIN_ADMIN_KEY` is the recovery override.
- A guest-code expiry (hours, default 24) for shared machines. The card also offers a lock button to forget the code immediately.
- **Pause all calls**, the kill switch — filed here from 0.98.24, because it is not a call cap and not call-only: it silences the machine and the text line too. The control itself stays the dashboard's Line switch.

### Caller permissions

What a caller may trigger, and **which caller** — each row set to the least trusted tier that gets it: off / anyone / guest code / admin. The range, low-harm first:

- **Like the track on air** — the same heart any listener taps. Needs no station credentials.
- **Find music by how it sounds** — two discovery reads: a "sounds like" search over a description, and "more like this" off the track on air. They match the analysed audio rather than words in a title, change nothing, and cost nothing against Actions per call. Browsing by mood, genre and era rides the library-search row instead.
- **Take a track back out of the queue** — off by default: the queue is shared, so it can pull a record somebody else asked for, which is why the station gives its own listeners no cancel.
- **The three station-wide switches** — skip the current track, fire a programme beat, put a different show on air. These reach every listener.

Rows that need the station admin credentials carry a **Station admin** badge, coral until the credentials are stored — including **un-like the track**, the operator's own curation heart, admin only.

Tier defaults and the full risk picture: [security](security.md).

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

### Call limits

Calls at once, per hour and per day, redial wait, actions per call — the guard on API spend, and the door's own state read back from the dashboard.

Called **Usage controls** and filed under Permissions & safety until 0.98.22. The same idea was in three places depending on the door — six chat caps on Texts, voicemail's ceiling on Voicemail, and these two pages away — so the call caps moved to the door that owns them.

### Greeting

Which DJ picks up, the greeting style or a written opening line, and whether the caller is asked their name.

### Turn-taking

When the DJ decides you have finished speaking, and whether a caller may talk over it. **The biggest lever on whether a call *feels* like a phone call.**

### Closing the call

The greeting's mirror: the hard ceiling on one call, the sign-off steer, the idle check-ins, and how early the DJ may hang up — how a call ends, in character.

### Station audio in the call

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

### Voicemail machine

An **Enable voicemail** master switch — the control is the dashboard's Voicemail line, and this page says whether it is open — then:

- per-persona greeting lines — editable, playable, deletable — staged one at a time with live progress
- a per-tier caller permission
- when it picks up: never / when a live call is impossible / always
- the greeting, and the per-message ceiling
- where messages go: held in the panel, a station request, or handed to the on-air DJ

**Triage** is the fourth destination: the model reads each message and picks a request, an on-air mention, or a station segment, bounded by the caller permissions.

Greetings are **staged** — rendered once per persona in their own voice, re-rendered only when the text, voice or backend changes — or **fresh each call**, written in persona at pickup with the staged clip as the backup. The greeting text takes `{station}`, `{dj}` and `{show}`, and with nobody on air the machine answers as the station itself in your default voice.

> **Every message lands in the panel's list whatever the delivery mode.** Nothing is recorded as audio; the transcript is the message.

The machine's **beep** is set with the other five sounds under [Call sounds](#call-sounds), because the six moments are one board. It is the only one of the six the server plays rather than the card, and deliberately the only one not governed by *Play call sounds* — it tells the caller to start talking.

The full walkthrough, including the soundbite studio: [Voicemail](VOICEMAIL.md).

---

## Texts

### Text line

A **Take text chats** master switch (the control is the dashboard's Text line; this page says whether it is open), then four named blocks — the section ran to fifteen rows under no headings until 0.98.24:

**Chat limits** — how many chats may be open at once, new chats per hour and per day, a **per-caller reopen wait** (the text line's Redial-wait, and scriptable where a call is not), the per-chat message ceiling and the per-minute message cap.

**When a chat ends** — the longest a chat may live, how long quiet before it closes, a **reply timeout** so a stalled model can't leave a caller watching the typing dots forever, and the nudge for a quiet caller.

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

**Quiet the station during calls** flips the collision the other way: instead of the call waiting for the station's voice, the station's own auto-talk — idents, time checks, links, segments, banter — stands down while a phone-in is live and returns within seconds of it ending. Off by default (it writes the station's own Voice switch, so it is yours to opt into); the other positions are **during on-air calls** and **during every call**. Needs the station admin credentials and a SUB/WAVE from July 2026 or newer — switched on without either, a banner under the row says exactly what is missing. Music, jingles and listener requests keep working throughout, and your own hand on the station's Voice switch always wins. The full truth table — what stands down, what a mid-call show changeover costs, and why a crash cannot leave the station mute — is on [the on-air page](on-air.md#quieting-the-stations-own-dj).

---

## Open Lines

### Open Lines

The booth reaching out instead of in: the DJ puts a subject to the audience on the broadcast, then knows what it asked when somebody arrives on any of the three doors. Full behaviour on [the Open Lines page](open-lines.md).

**Off by default, and manual by default.** The master switch alone airs nothing — a line opens when you press **Open a line now**, or on the cadence you set below. Every other row here is hidden until the switch is on.

| Setting | What it decides |
|---|---|
| **Where the topic comes from** | *The DJ decides* invents one from tonight's show — who is on air, the show card, the episode, what has played and been said. *Off the shelf* takes the next one from your own list below. *A quiz* has the DJ set a question **and its answer** before anything airs, about its own show rather than general trivia, so it can genuinely mark a caller right or wrong |
| **Your shelf of subjects** | Add with **+ Add**; aim each one at particular DJs with the picker on its row, or leave it open to all of them. The least recently used goes up next, so a subject you just typed is the next one out. Write the SUBJECT, not the words: the DJ says it in its own voice |
| **Where to reach you, said on air** | Read out with the invitation, exactly as typed — so write it the way it should sound. **Left blank it falls back to where this deployment actually answers** (from `LIVEKIT_PUBLIC_URL`), so you should not have to type an address Talk Wave already knows, and the two cannot drift apart. It only stays silent when that address is a bare IP or localhost, which is nothing a DJ can usefully read out |
| **How long a line stays open** | Then the DJ closes it on air, in character. Default 60 minutes — but **never past the end of the DJ's own programme**, because the show changing ends the line anyway and a countdown promising more was promising time the DJ did not have. In the last few minutes of a show it is not shortened at all: cutting a line that fine would air its invitation and its sign-off back to back |
| **Remind every** / **Most reminders per topic** | The DJ raises the subject again mid-window. The cap is the one that protects the broadcast: a long window with a short interval is how a station asks the same question nine times. 0 on either = no reminders |
| **Report back on air when somebody answers** | When a conversation about the topic ends, the DJ goes back on air and says what came of it. Off by default — it puts more of the DJ on your broadcast. The **position**, never a name and never a quote; at most three per topic; and a request is not a contribution, so those air nothing |
| **Only with at least this many listeners** | Checked when a line opens and before each reminder, never in the middle — a topic that vanished because somebody closed a tab would strand whoever was already typing. 0 = open regardless |
| **Only these DJs** | Chips and a dropdown: pick DJs to build the list, **All** / **None** for both extremes, none picked means whoever is on air. This paces the AUTOMATIC cadence only — **a press always runs**, because an operator holding the button has already made that decision. Separate from the per-subject aim above: this says who may open a line at all, that says which subjects suit whom |
| **Open one automatically every** | 0 = manual only, and that is the default: nothing reaches your listeners that you did not press a button for |

The **dashboard** carries the same control, under Transmission, shaped like the station's own Takeover box: pick a subject from the shelf or type one for this press only, and one action bar that reads **Put it up** or **Close it** depending on what is standing. It shows what is up, who opened it, how long is left and how many reminders are spent. The bar goes quiet for a moment after a press — opening and closing are opposite acts, and the box changes height underneath the cursor that just clicked.

The card under the buttons shows what is up right now — who opened it, how long is left, how many reminders are spent, and **the words that actually aired**, which is what a listener heard and what the DJ is being reminded of. Both buttons post immediately and never go through Save.

A refusal is not an error: **Open a line now** answers with the gate that stopped it (switched off, wrong DJ, nobody listening, empty list) rather than a failure box.

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

### Call status wording

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

### Card colours

Colours — including **the station's own**, read from its `/themes` and following the on-air show — and a **skin**.

> **Skins are experimental.** Sixteen looks for the card, from a switchboard and a rack unit to green phosphor, paper and Windows 95, each with its own palette and, on most, a small artefact that sits in the transcript box between calls and disappears the moment one starts. **A skin cannot change the card's size or controls, only its surface.**

### On the caller's phone

The Behaviour band — nothing visual:

- whether calls start on the **loudspeaker**
- the card's **listener count** — "On air now · 2 listening", shown from one listener up, so a quiet hour never paints a zero
- its **heart button** — the same public like any listener page sends, beside the record on the card. Both on by default
- the swipe-up station player, and **which face it opens on** — the phone with the player a swipe up, or the player with the call button a swipe down
- how loud the station plays under the answering machine

### After the conversation

The thumbs up/down, **per door**: ask after a call, after a text chat, and after a voicemail are three separate switches.

### Embed frame

The Embed band — the preview offers the embed view beside it.

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

**Station tools** is the station's whole surface — 37 tools in all: 7 handed straight through from the station's MCP server, 27 served by wrappers of ours (retries, guards, the action ledger), and 3 that are never on a call line at any setting — each row saying whether a caller can reach it.

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
