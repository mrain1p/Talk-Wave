# Live on air

[← back to the README](../README.md)

A caller's conversation with the DJ, going out on the station's broadcast while it happens.

**The short version**

- The station has no live input, so a phone-in is a **relay**: each finished turn becomes a clip pushed onto the station's voice queue.
- Air runs **about one exchange behind** the room — and that lag is the dump button.
- **Three switches** have to agree before anyone airs. Any one of them alone says no.
- It needs **one compose change**: both Talk Wave containers joined to the station's network. See [The wiring](#the-wiring).

The switches live on the panel's own **On air** page, mirrored on [the dashboard](dashboard.md). The delivery cousin for recorded messages — the soundbite studio — is on [the voicemail page](VOICEMAIL.md).

---

## What listeners actually hear

Everything the station airs is a file its mixer fetches, so a live phone-in is a relay. Each finished utterance — the caller's and the DJ's — becomes a short clip on the station's voice queue, in conversation order. The queue is first-in-first-out and the music duck holds across back-to-back items, so what listeners hear is the conversation *tightened*: the model's thinking gaps don't air.

Two delays sit between the room and a listener's ears:

| Gap | How long | Why |
|---|---|---|
| Room → air | one turn, capped by the **On-air delay** (default 6s) | the relay holds one finished clip back so it stays killable |
| Air → listener | **~2.3 seconds** | ordinary stream buffering |

<details>
<summary><b>Why 2.3 seconds, and not the 22 the station reports</b></summary>

The station reports `streamBufferSeconds` as 22, and Icecast really does burst 22 seconds of audio on connect — but a browser throws most of that away. A plain `<audio>` element was measured sitting a rock-steady **2.3 seconds** behind the newest buffered byte for a whole run (2026-08-13, against the operator's own station; the working is in [`call/air_log.py`](../agent-worker/call/air_log.py)).

**22 describes the burst SIZE, not the playhead.** Anywhere a number is needed, `audibleIn` is recorded per push, so the gap can be read off a real call rather than argued from a config value that turns out to describe something else.

</details>

### The lag is not an apology, it is the dump button

Real phone-in radio runs on a deliberate delay for exactly this reason: **a turn that has not aired yet can still be killed.**

A turn airs when the one after it completes, **or** when the On-air delay expires — whichever comes first. That cap is what makes the delay a promise instead of an accident:

- every finished turn stays killable for exactly that long,
- the air runs about that far behind the room,
- and the mixer's queue stays fed, so the duck never lifts mid-conversation.

**On-air delay** sits beside the On-air window under Caller permissions (2–30 seconds). Longer buys a wider pull window, shorter buys tighter radio, and your ear is the instrument that picks it.

<details>
<summary><b>What it was like without the cap</b></summary>

The hold lasted whatever the next turn happened to take — the model thinking, then the DJ's whole answer playing out in the room. A caller's turn could sit unaired for twenty seconds while the broadcast filled the gap with music swells.

</details>

### Three ways a call can air

Set by **`When the call airs`**, under Caller permissions.

| Mode | What airs | PULL OFF AIR reaches | The cost |
|---|---|---|---|
| **Live** *(default)* | from the first clip, turn by turn | the turn still inside the delay window | can air an intro around a caller who is never heard |
| **Live once heard** *(0.98.9)* | from the caller's **first words** | the turn still inside the delay window | the start airs about one exchange late |
| **Tape** *(0.98.5)* | nothing until hangup, then the whole reel | the **entire tape**, at any moment of the call | listeners hear nothing until the call ends |

**The brackets around the segment are lazy on purpose.** The DJ's on-air intro — *a caller is coming on the air* — airs at the **first clip**, not when the call connects, and the thank-you outro airs only if something actually aired.

<details>
<summary><b>The call that got an announcement, a minute of nothing, and a thank-you to nobody</b></summary>

The first deployed test did it the other way and aired both brackets around a call whose media never arrived. Listeners got the announcement, a minute of silence, and a thank-you to a caller who was never there.

</details>

#### Live once heard

The first clip is usually the DJ's own hello, so plain live mode still airs an intro, a greeting and a sign-off around a caller whose media never arrives — the DJ talking to nobody, on the broadcast. This mode opens the segment at the caller's **first words** instead:

- the DJ's opening waits, unaired, until someone is provably there, then airs in order ahead of them;
- a call where the caller is never heard — dead media path, blocked microphone, plain silence — airs **nothing at all**.

The price is a start that airs about one exchange late, which is why it is a choice rather than the default. Pick it where that guarantee is worth the delay.

#### Tape mode

Nothing airs during the call, and the whole conversation plays the moment it ends — intro, the exchange in order, outro. What the wait buys is the dump promise inverted: **PULL OFF AIR at any moment of the call kills the entire tape before a word of it airs**, where live mode can only ever kill the turn still inside its delay window.

- The **On-air window** caps the reel's aired length the same way it caps a live segment.
- The stage frame tells the caller *"airs after you hang up"*, so the consent they give is the one that happens.
- The DJ is briefed that it is taping rather than live, so it does not claim otherwise on air.
- **A tape the caller never made it onto stays in the drawer, unconditionally.** At hangup the reel is known, so a one-sided recording — the DJ and nobody — airs nothing, and the transcript says why.
- The caller keeps the station under the call like any private caller would: the Tune-in bed plays on a taped call, where a live one must silence it. The stream mid-call is the caller's own conversation, a buffer behind.

### One at a time, and not forever

- **One phone-in holds the air at a time.** While one is live, the ON AIR route refuses the next caller until it ends.
- **On-air window** (default 240 seconds) bounds how long one caller may hold the broadcast. When it closes, the relay signs them off air and the call itself carries on privately.
- The station's own segments queue behind a live call, so **shorter is kinder to the programme**.

---

## Quieting the station's own DJ

Ducking manages the collision; this removes it. **Quiet the station during calls** (panel → Call vs broadcast) flips the station's own **Voice** switch off while a phone-in is live and back on within seconds of it ending, so idents, hourly time checks, between-track links, segments and banter never talk over a call. Three positions: **off** (default), **during on-air calls**, or **during every call** — a private caller hears the station through tune-in too, so quieting off-air calls is a real choice, not a technicality.

It needs two things: the **station admin credentials** (the same ones that mirror persona voices) and a **SUB/WAVE from July 2026 or newer** (v0.48.0's station-wide voice switch). Switched on without either, the panel shows a wiring banner saying exactly what is missing — the switch never fails silently.

**What keeps working while the station is quiet** — these are the station's own rules for its Voice switch, not ours:

| Still happens | Stands down |
|---|---|
| Music — picks never stop | Station idents and hourly time checks |
| Jingles (pre-rendered, on the mixer's own rotation) | Between-track DJ links |
| Listener requests — queued, with their text acknowledgement | The request's *spoken* intro |
| Your own manual pushes (`/dj/say`, segment buttons) | Auto segments (weather, news, facts) and banter breaks |
| This call's own clips — they ride the mixer directly | Programme episode beats |

**If a show changes over mid-call**: the show still starts on time — music steering, filters, the lot — but its boundary greeting is dropped (the station marks it aired rather than queue a stale hello), a programme episode's intro stays pending and opens the next hour instead, and an episode outro whose final-minutes window is entirely covered by a call is lost for that episode. The station's booth log says why each moment stood down ("station voice is off"), so a quiet stretch is legible from its own side.

**The operator always outranks the machine.** A station whose Voice you already keep off is left entirely alone — nothing to flip, nothing to restore. Flip Voice back on in the station's admin mid-call and Talk Wave notices, stands down, and does not touch it again. And expect to *see* it: the Voice toggle in the station's admin sits off while a call is up. That is the feature working, not a fault.

**A crash cannot leave the station mute.** Every call heartbeats a marker while it lives — through the tape playout too — and the token server restores the switch when no marker is fresh: within seconds of a normal hangup (after a taped call's playout finishes airing), within about three minutes of a worker that died mid-call, and on its first tick after a whole-stack restart. The restore is confirmed against the station and retried until it lands.

This is the one place Talk Wave **writes** a station setting — one boolean, merged field-by-field by the station's own settings route, verified from the station's echo, restored to exactly what it was.

---

## Three switches have to agree

Nobody airs by accident. Three separate consents stand between a caller and the broadcast, and **any one of them alone says no.**

| Switch | Where it lives | Who sets it |
|---|---|---|
| **Go live on the station** | Caller permissions → the tier row | operator |
| **Doors to air** | panel → **On air**, mirrored on the dashboard | operator |
| **The ON AIR route** | the call card's top edge | the caller |

**1. Go live on the station** — the tier row under Caller permissions: **off**, **anyone**, **guest code**, or **admin**. Off means nobody, the admin included. The route is minted into the signed room name at pickup, so **a caller cannot put themselves on air mid-call.**

**2. The two doors** — quick kills on the panel's On air page (**Doors to air**), mirrored by the dashboard's Live on air cluster and [documented with it](dashboard.md). One door each, so you can close one without touching the other:

| Door | What it needs |
|---|---|
| **Live Call** | the mixer's telnet — see [The wiring](#the-wiring) |
| **Aired Voicemails** | nothing extra; the studio's DJ-reads backend airs over the plain admin API |

A closed door just means **private**: callers still talk to the DJ, they just don't reach the broadcast.

**3. The caller's own switch** — the ON AIR / OFF AIR route on the card's top edge. It defaults to OFF AIR, is never remembered between visits, and locks the moment anything is running. One switch arms both doors: with it on, a call relays live, and a recorded message goes to [the soundbite studio](VOICEMAIL.md) for air instead of the private machine.

> Settings are re-read before **every push** — tighter than the per-call re-read the rest of the system promises — so flipping a door shut mid-broadcast stops the next clip, not the next caller.

---

## The pull

**PULL OFF AIR**, on the dashboard's On-air transmission row, is the operator's dump:

- the turn in hand and the one arriving both die unaired,
- the DJ signs the segment off,
- and the call itself carries on — the caller may not even notice.

The press itself asks the server whether a phone-in is actually live, so pressed on a quiet line it says so and kills nothing, and a leftover press can never behead the next caller's first turn.

### What it protects depends on where you are listening

The dump kills what has **not been pushed yet**. It cannot reach back for something already in the mixer's queue — so the question is how far behind the room you are when you decide to press it.

| Monitoring | How far behind | What the dump gets you |
|---|---|---|
| **The panel** | live with the worker | every finished turn, for the whole On-air delay |
| **The stream** | a turn, plus ~2.3s | what made you reach has already gone out; you kill what comes after it |

Neither is wrong — a phone-in you are half-watching is more realistically monitored on the stream — but **the panel is the surface where the dump does what the word implies.**

> Anything that must *never* air is a caller who should not have been given the ON AIR route in the first place. The three consents above are the real control; the dump is the one you keep for the thing nobody predicted.

---

## When the plumbing is missing

A caller who pressed the button in good faith is never punished for the mixer being down. **Every failure lands on the broadcast side, out loud, and the call survives it.**

| When | What happens |
|---|---|
| **Mixer unreachable at pickup** | the call goes ahead **off air**, and the transcript says why — *"no air base URL"*, or *"no reachable mixer"* |
| **Two pushes fail back to back** | the transport is gone, not unlucky: the relay takes the segment off air out loud rather than airing a conversation with holes in it, and the call continues privately |
| **A half-joined deployment** | the dashboard's Live Call door shows **shut**, rather than open over calls that quietly fall back |

The preflight exists because the studio once spent a week not noticing a missing network stanza, and a silently absent broadcast is that all over again. The door's verdict comes from the worker — the process that actually pushes — written at worker start and at every phone-in.

---

## The wiring

Talk Wave serves each clip at an unguessable URL on its published `:8100`, then pushes `voice_queue.push <url>` over the station mixer's telnet. **Liquidsoap fetches the audio itself**, exactly the way it fetches every music track, so nothing is ever written to the station's disk. The voice channel is the right one on purpose: it is the only channel that carries speech at broadcast level, music ducked beneath it.

### Why a compose change is needed at all

| Docker fact | Consequence |
|---|---|
| Compose gives every project its own private network | the station's stack and Talk Wave's cannot see each other's service names, so `broadcast:1234` only resolves on the station's network |
| Host-published ports are reachable from everywhere | the clip-fetch half needs no changes at all |

Neither is a configuration anyone made — it is just Compose's default.

### Join both containers to the station's network

```yaml
  talkwave-worker:
    networks:
      - default                                       # keep — Talk Wave's own services find each other here
      - <station-project>_default                     # the station's network, so broadcast:1234 resolves

  talkwave-web:
    networks:
      - default
      - <station-project>_default

networks:
  <station-project>_default:
    external: true                                    # created by the station's own compose — joined, not owned
```

**Both**, because the pushing is split across the two processes and reachability is answered per container:

- **live calls push from the worker** — the relay runs inside the call;
- **the studio's caller-voice sends push from the web half** — the panel's API lives there.

Join only the web half and the Live Call door honestly shows shut. Only joining both gives you both features.

> **Keep `default` in each list.** Naming any network on a service drops Compose's implicit one, and Talk Wave's own services stop finding each other by name.

### Where the mixer fetches from

The mixer fetches clips at `http://HOST_IP:8100` by default — the published port your `.env` already names for LiveKit.

- **No `HOST_IP` in your compose?** Give **both** services `VM_AIR_BASE_URL` explicitly, or the relay falls back private with *"no air base URL"* in the record.
- **Once joined to the station's network**, `VM_AIR_BASE_URL=http://talkwave-web:8100` also works, and never goes stale when the LAN address changes.

`VM_MIXER_TELNET` and `VM_AIR_BASE_URL` deliberately have **no panel row**: they are deployment facts, set in the environment or `data/settings.json`. They keep their historical `vm_` prefix because the studio configured them first, and a rename would silently disconnect every station that already has them.

<details>
<summary><b>Why not just publish the mixer's telnet port on the host</b></summary>

That port takes unauthenticated commands — push audio on air, skip the record — so publishing it offers that to the whole LAN. The network join offers it to exactly one stack, and assumes the station and Talk Wave share a Docker host.

</details>

---

## What touches a disk, and for how long

The worker writes each finished turn as a clip into the shared `data/onair/`; the web half serves it to the mixer once. **The URL's token is the credential** — unguessable, and the mixer's HEAD probe peeks without spending it, so the probe cannot rob the download that follows.

A clip in a caller's voice is deleted three ways:

1. **Discarded on serve** — read into memory and gone before the response goes out.
2. **Swept by a three-minute TTL** — if the fetch never came.
3. **Cleared wholesale** — when the call's relay closes.

What remains afterwards is the transcript — under the Transcripts switch and retention like every other conversation — with a line per aired turn naming the request id the mixer minted for it.

The broadcast itself is the station's: **what aired, aired.** The held turn is the only take-back there is.
