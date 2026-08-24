# Live on air

[← back to the README](../README.md)

A caller's conversation with the DJ, going out on the station's broadcast while it happens.

**The short version**

- The station has no live input, so a phone-in is a **relay**: each finished turn becomes a clip pushed onto the station's voice queue.
- Air runs **about one exchange behind** the room — and that lag is the dump button.
- **Three switches** have to agree before anyone airs. Any one of them alone says no.
- It needs **one compose change**: both Talk Wave containers joined to the station's network. See [The wiring](#the-wiring).

The switches live on the panel's **On air** page, mirrored on [the dashboard](dashboard.md). The delivery cousin for recorded messages — the soundbite studio — is on [the voicemail page](VOICEMAIL.md).

---

## What listeners hear

Each finished utterance — the caller's and the DJ's — becomes a short clip on the station's voice queue, in conversation order. The queue is first-in-first-out and the music duck holds across back-to-back items, so listeners hear the conversation *tightened*: the model's thinking gaps don't air.

Two delays sit between the room and a listener's ears:

| Gap | How long | Why |
|---|---|---|
| Room → air | one turn, capped by the **On-air delay** (default 6s) | the relay holds one finished clip back so it stays killable |
| Air → listener | a couple of seconds | ordinary stream buffering |

The lag is deliberate — real phone-in radio runs on a delay for the same reason: **a turn that has not aired yet can still be killed.** A turn airs when the one after it completes, or when the On-air delay expires, whichever comes first. Longer buys a wider pull window, shorter buys tighter radio; **On-air delay** (2–30s) sits on the On air page.

## The three modes

Set by **`When the call airs`** on the On air page:

| Mode | What airs | PULL OFF AIR reaches | Trade-off |
|---|---|---|---|
| **Live** *(default)* | from the first clip, turn by turn | the turn still inside the delay window | can air an intro around a caller who is never heard |
| **Live once heard** | from the caller's **first words** | the turn still inside the delay window | the start airs about one exchange late |
| **Tape** | nothing until hangup, then the whole reel | the **entire tape**, at any moment of the call | listeners hear nothing until the call ends |

**Live once heard** holds the DJ's opening unaired until someone is provably there, then airs it in order ahead of them — a call where the caller is never heard (dead media, blocked microphone, silence) airs nothing at all.

**Tape** plays the whole conversation the moment the call ends — intro, exchange, outro. The wait buys the dump promise inverted: PULL OFF AIR at any moment kills the entire tape before a word of it airs. The stage frame tells the caller *"airs after you hang up"*, the DJ is briefed it is taping, the On-air window caps the reel's aired length, and a tape the caller never made it onto stays in the drawer.

In every mode the brackets are lazy: the DJ's on-air intro airs at the first clip, and the thank-you outro airs only if something actually aired.

**One at a time, and not forever.** One phone-in holds the air at a time — the ON AIR route refuses the next caller until it ends. The **On-air window** (default 240s) bounds how long one caller may hold the broadcast; when it closes, the relay signs them off air and the call carries on privately. The station's own segments queue behind a live call, so shorter is kinder to the programme.

---

## Quieting the station's own DJ

Ducking manages the collision; this removes it. **Quiet the station during calls** (panel → Call vs broadcast) flips the station's own **Voice** switch off while a phone-in is live and back on seconds after it ends, so idents, time checks, links and segments never talk over a call. Three positions: **off** (default), **during on-air calls**, or **during every call** — a private caller hears the station through tune-in too, so quieting off-air calls is a real choice.

It needs the **station admin credentials** and a **SUB/WAVE from July 2026 or newer**. Switched on without either, the panel shows a wiring banner naming what is missing — it never fails silently.

**What keeps working while the station is quiet** — the station's own rules for its Voice switch:

| Still happens | Stands down |
|---|---|
| Music — picks never stop | Station idents and hourly time checks |
| Jingles (pre-rendered, on the mixer's own rotation) | Between-track DJ links |
| Listener requests — queued, with their text acknowledgement | The request's *spoken* intro |
| Your own manual pushes (`/dj/say`, segment buttons) | Auto segments (weather, news, facts) and banter breaks |
| This call's own clips — they ride the mixer directly | Programme episode beats |

**The operator always outranks the machine.** A station whose Voice you already keep off is left alone. Flip Voice back on in the station's admin mid-call and Talk Wave stands down and does not touch it again — and expect to *see* the toggle sitting off while a call is up; that is the feature working.

**A crash cannot leave the station mute.** Every call heartbeats a marker, and the token server restores the switch when no marker is fresh — seconds after a normal hangup, minutes after a worker that died mid-call, and on the first tick after a stack restart. The restore is confirmed against the station and retried until it lands. This is the one place Talk Wave writes a station setting: one boolean, restored to exactly what it was.

---

## Three switches have to agree

Nobody airs by accident. Any one of these alone says no:

| Switch | Where it lives | Who sets it |
|---|---|---|
| **Go live on the station** | Caller permissions → the tier row | operator |
| **Doors to air** | panel → **On air**, mirrored on the dashboard | operator |
| **The ON AIR route** | the call card's top edge | the caller |

1. **Go live on the station** — off, anyone, guest code, or admin. Off means nobody, the admin included. The route is minted into the signed room name at pickup, so a caller cannot put themselves on air mid-call.
2. **The two doors** — quick kills, one per door, so you can close one without touching the other. **Calls to air** needs the mixer's telnet ([The wiring](#the-wiring)); **Voicemails to air** needs nothing extra. A closed door just means private: callers still talk to the DJ.
3. **The caller's own switch** — ON AIR / OFF AIR on the card's top edge. Defaults to OFF AIR, never remembered between visits, locks the moment anything is running. With it on, a call relays live and a recorded message goes to [the soundbite studio](VOICEMAIL.md) instead of the private machine.

> Settings are re-read before **every push**, so flipping a door shut mid-broadcast stops the next clip, not the next caller.

---

## The pull

**PULL OFF AIR**, on the dashboard's On-air transmission card: the turn in hand and the one arriving both die unaired, the DJ signs the segment off, and the call itself carries on — the caller may not even notice. The press asks the server whether a phone-in is actually live, so pressed on a quiet line it says so and kills nothing.

The dump kills what has **not been pushed yet**. Watching the panel, you are live with the worker and every finished turn is reachable for the whole delay; listening to the stream, you are a turn-plus-buffer behind, and what made you reach has already gone out — you kill what comes after it.

> Anything that must *never* air is a caller who should not have the ON AIR route in the first place. The three consents are the real control; the dump is for the thing nobody predicted.

---

## When the plumbing is missing

A caller who pressed the button in good faith is never punished for the mixer being down — every failure lands on the broadcast side, out loud, and the call survives it:

| When | What happens |
|---|---|
| **Mixer unreachable at pickup** | the call goes ahead **off air**, and the transcript says why |
| **Two pushes fail back to back** | the relay takes the segment off air out loud rather than airing a conversation with holes in it; the call continues privately |
| **A half-joined deployment** | the dashboard's Calls-to-air door shows **shut**, rather than open over calls that quietly fall back |

---

## The wiring

Talk Wave serves each clip at an unguessable URL on its published `:8100`, then pushes `voice_queue.push <url>` over the station mixer's telnet. Liquidsoap fetches the audio itself, the way it fetches every music track — nothing is written to the station's disk. The voice channel is the right one on purpose: it carries speech at broadcast level, music ducked beneath it.

**Why a compose change:** Compose gives every project its own private network, so the station's `broadcast:1234` only resolves on the station's network. Join both Talk Wave containers to it:

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

**Both containers**, because live calls push from the worker while the studio's caller-voice sends push from the web half. Join only one and only that feature works — the doors report it honestly. And **keep `default` in each list**: naming any network on a service drops Compose's implicit one.

**Where the mixer fetches from:** `http://HOST_IP:8100` by default. No `HOST_IP` in your compose? Set `VM_AIR_BASE_URL` on **both** services — once joined to the station's network, `VM_AIR_BASE_URL=http://talkwave-web:8100` works and never goes stale. `VM_MIXER_TELNET` and `VM_AIR_BASE_URL` are deployment facts with no panel row; set them in the environment or `data/settings.json`.

> The mixer's telnet takes unauthenticated commands, which is why the network join is the mechanism rather than publishing that port to the whole LAN.

---

## What touches a disk, and for how long

The worker writes each finished turn as a clip into the shared `data/onair/`; the web half serves it to the mixer once. The URL's token is the credential — unguessable, and the mixer's HEAD probe peeks without spending it. A clip in a caller's voice is deleted three ways: discarded on serve, swept by a three-minute TTL if the fetch never came, or cleared wholesale when the call's relay closes.

What remains is the transcript — under the Transcripts switch and retention like every other conversation — with a line per aired turn. The broadcast itself is the station's: **what aired, aired.** The held turn is the only take-back there is.
