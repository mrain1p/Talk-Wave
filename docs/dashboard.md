# The dashboard

The landing page of the settings panel: what is standing right now, the switches that stop it, and the two strips that say what has been happening.

[← back to the README](../README.md) · the settings themselves are in the [settings reference](settings.md)

---

## How to read the page

Everything else at `/settings` is configuration you save. Everything here either reads back live state or acts the moment you press it.

| Two kinds of thing live here | What it does |
|---|---|
| **Tiles read** | a value, a note, a tone — **green** fine, **amber** worth a look, **coral** broken |
| **Control cards act** | every control posts the moment it is pressed |

**Nothing on the dashboard goes through Save**, so there is never a half-applied switch waiting on a button lower down.

The dashboard leads `/settings` — it is what the panel opens on — and its reads need the admin session, so before you sign in the tiles say so instead of showing numbers.

## The station strip

Four tiles across the top answer the four questions that decide whether anything below is worth configuring yet. Each one jumps to the section that changes its answer.

| Tile | Answers |
|---|---|
| **On air** | who is on the air right now, with their photo |
| **Station** | whether the SUB/WAVE station answers at all |
| **Brains · Voice · Ears** | the three legs of a call — the LLM, the TTS, the STT — each ready or not |
| **Who can call** | the access picture: the door tiers as they stand |

<details>
<summary><b>Why the photo is sometimes a drawn silhouette</b></summary>

The silhouette stands in until the real photo has actually loaded. A broken image on the dashboard reads as a fault, so a photo has to earn its place by loading.

</details>

## Transmission — the switch ladder

The switches read as a hierarchy, and the hierarchy is the rule:

> **A switch that is off takes every switch beneath it out of play.**

While **The line** — the top-level kill switch — is paused, everything under it dims to amber and stops taking presses: the three Lines and both Live-on-air doors.

**Amber means *held*, not *broken*.** Their positions are kept, their settings are not rewritten, and the moment the line reopens they stand exactly as you left them. While the line is paused nothing answers whichever way they point — the server refuses to mint a call at all — and the caption under the Transmission label says it plainly: *"The line is paused — nothing answers, whatever these say, until it reopens."*

### 1. The line

Spans the top. Open or Paused, one press either way. This is the switch that turns every caller away at once, and it outranks everything below it, the answering machine included.

### 2. Lines

The three ways through the door above, each pairing a switch with the tile for the traffic it produces.

| Line | Switched on, the card says | The tile beside it counts |
|---|---|---|
| **Live calls** | the permission counts as chips, and the fallback that answers when a live call cannot start | recent conversations, with failed calls and thumbs — click to open the records |
| **Voicemail** | who may use it, whether it is the fallback or always on, and where a message goes afterwards | messages taken, split into held for you and passed on — click to open the voicemail section |
| **Text line** | who may open the line, and how long a quiet chat stays open | — |

The permission chips count how many things a caller at each tier can actually ask for, read from the same switches the reference lists read. Fallback and always-on are different machines, and the card says which one you have.

### 3. Live on air

The phone-in's two doors — **Live Call** and **Aired Voicemails** — in the same grammar as the Lines above, each closing its own door without touching the other.

- The cluster stands only while the *Go live on the station* caller permission is on at all. With that permission off, the whole cluster **leaves the page** rather than greying.
- A door switched off here simply stays **private**: callers still talk to the DJ, they just don't reach the broadcast.
- The feature itself — the relay, the caller's switch, the wiring — has [a page of its own](on-air.md).

### 4. The pull

Beneath the two doors, the **On-air transmission** row carries one real button: **PULL OFF AIR**. It pulls whoever is live, and the turn in hand never airs.

- The panel does not poll for this. **The press itself asks the server** whether a phone-in is actually live — so pressed on a quiet line it says so and kills nothing, and it can never behead the next caller's first turn.
- The row's answer returns to its resting note after six seconds rather than sticking.

> The caption beside the Transmission label reads the combination out loud — *"Together: a phone with an answering machine"*, *"a voicemail-only line"*, *"both off — the line is closed, and the card tells callers so"* — so the ladder's net effect is written in one sentence rather than left to be inferred from switch positions.

## Notifications

The dashboard's other column names what still stands between this deployment and a working call.

- Every row is computed from the same live signals the tiles read, so **the two can never disagree**.
- Every row jumps to its fix.
- Any settings page holding one of these gaps wears a **coral pin** in the page picker, so the problem is visible from every page, not just this one.
- An empty column says the line is ready, rather than vanishing.

> **Clear dismisses what is listed right now, it fixes nothing.** It means "I have read these" — an item returns if its condition clears and then happens again.

## Activity

Four charts over the same records the calls viewer reads, plus the sampled listener series.

- Bucket them by **day, week or month**, and set how many buckets to show.
- Narrow the whole strip with the two pickers: which doors count (calls, texts, voicemail), and which ratings.
- A series that is not available shows an em-dash over an empty frame — **the charts never invent data**.
