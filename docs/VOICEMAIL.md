# Voicemail

The answering machine and the soundbite studio: what each does, where messages go, and the terms on which anything is ever recorded.

[← back to the README](../README.md)

---

The *Leave a message* door opens onto one of two flows, chosen by **Voicemail → The line is**: the classic **answering machine** (the default — a message as text, no audio kept) or the **soundbite studio** (the caller records a take, reviews it, and sends it to air with the DJ around it). Both answer to the same door: the *Voicemail* permission decides which callers may leave a message at all.

## The answering machine

A second, much smaller kind of call. The line rings, the DJ picks up with an answering-machine greeting in their own voice, there is a beep, the caller says one thing, and it goes in. No conversation, no tools — the whole interaction is one caller utterance long, with a hard 30-second ceiling.

```
ring  →  "You've reached Yosemite FM. Francesca's on the air right now —
          leave a request after the beep."   ← the live DJ's voice
      →  BEEP
      →  caller speaks (30s ceiling)
      →  "Got it — I'll pass that on."  →  hang up
```

**Nothing is recorded.** The caller's audio goes through the same speech-to-text a live call uses, and what is kept is the transcript — the only part anything downstream can act on. No audio file, no player, no retention policy for a stranger's voice; the card tells the caller at the beep that only the transcript is kept. Unlike a live call there is no LLM turn on the critical path, so a deployment nervous about API spend can run voicemail wide open and live calls behind a guest code.

## Greetings

The greeting must be in the voice of whoever is on air and must play **instantly**, so it is rendered ahead of time — once per persona — by the **Stage greetings** button, which walks the roster and reports per-persona success. Staged clips re-render only when their text, voice or TTS backend changes. A station-level clip in your default voice answers when nobody is on air, and the fallback order means a missing clip never makes a silent pickup: this persona's clip → the station clip → any clip → the beep alone.

Two modes: **fresh** (the default) — one line written in persona at pickup, with the staged clip as backup if the model can't make it in time — or **staged**, the pre-rendered clip alone, instant every time. Greeting text takes `{station}`, `{dj}` and `{show}`. The beep is synthesized in the browser; upload a WAV in *Call sounds* to replace it (server-played, so WAV only — anything unplayable falls back to the tone, never silence).

## Taking the message

One STT session ends on whichever comes first: the caller stops talking, the 30-second ceiling, or a hang-up (whatever was transcribed still counts; an empty transcript is a hang-up, not a message). Push to talk applies like any call — the talk bar appears the moment *Leave a message* is pressed, a tap latches the mic open, and the caller can talk over the greeting; their words are kept from the first syllable. The quiet clock starts at the beep, and the ceiling ends the whole call, not just the recording.

## Where the message goes

| Mode | What happens |
|---|---|
| `hold` | Nothing is sent — it lands in the panel's list for you to read (the safe default) |
| `request` | The text goes to the station as a song request, through the same rate-limited path a live caller's takes |
| `air` | The text is handed to the on-air DJ as something to mention — a dedication, a shout |
| triage | The model reads each message and picks one of the above, bounded by the caller permissions |

`air` puts a stranger's words in front of every listener and `request` writes station state — both need the station admin credentials and carry the same *Station admin* badge as the permission switches. Every message also writes a call record (`kind: voicemail`), so *Recent conversations* shows the whole night in one place, and one message counts against *Actions per call* like any other action.

## When the machine answers

Its own switch on the dashboard, beside Live calls — and the kill switch outranks both: a paused line closes the machine too. The pickup policy is *never* / *when a live call is impossible* (nobody on air, live calls off, all slots busy) / *always* — voicemail-only being the cheapest way to run a line at all. When the machine is the answer, the card's button reads **Leave a message** instead of Call. The policy governs the door itself, so it applies to both flows.

---

## The soundbite studio

The same door, repurposed into a produced radio segment: the caller's own voice on your airwaves, with the DJ around it — but nothing reaches the air the caller has not seen and approved first.

```
Hold to record (up to the ceiling)  →  the transcript, and what sending will DO
   →  play it back · record another take · send
   →  on air: DJ intro  →  the caller's take  →  the DJ reacts, honestly
```

The recording happens **in the caller's browser** — playback is instant, re-records are free, and one upload happens only when they are happy. The Record button is the talk bar in another costume: press and hold to record, let go to stop, or tap to latch it on. The server masters the take for broadcast (trim, telephone band, level), transcribes it with the same STT a live call uses, and answers with the review card.

**The action preview is the point.** Before sending, the caller sees exactly what send will do — not "this looks like a request" but *"Queue: Landslide — Fleetwood Mac"*, resolved against your library to a real track id at review time. Send executes that record, never a re-interpretation of the words. A message naming a track the library doesn't hold previews as a request; one asking for a different DJ previews as a takeover **only if** your *Show takeovers* permission is on (the same switch the live line rides, checked again at send); anything else previews as "no station action — the message just plays." And the DJ's on-air reaction is chosen **after** the station answers: a refused queue is never claimed as a track that's coming.

**This is the one place the sidecar keeps caller audio, and it holds it on terms.** The card says the message may be played on air. A draft lives only between recording and decision, and the exact moment the audio leaves the disk depends on how it went:

| What happened | The audio is deleted… |
|---|---|
| Sent, caller's own voice aired | at the mixer's fetch — served from memory, gone before the response ends (the mixer collects lazily, after the DJ's intro, so deleting at send aired a hole where the caller's voice belonged) |
| Sent, the DJ read it (or any failure) | at send — nothing will ever read it again |
| Re-recorded, or *Never mind* | immediately |
| Walked away, browser closed, crash | the fifteen-minute sweep |

The URL the mixer fetches from is unguessable, spent by its one download, and dead in two minutes regardless — the mixer's HEAD probe checks it without spending it, so the probe cannot rob the download that follows. Sent or failed, the transcript lands in the panel's messages list like any voicemail, labelled `soundbite/<backend>`, with the receipt naming what actually aired.

### How it airs: two backends

**The DJ reads it** (`dj-reads`, the default) works on any deployment with station admin credentials — the on-air DJ reads the caller's message in its own voice and reacts. **The caller's own voice** (`caller-voice`) plays the actual recording on the station's voice channel — music ducked like any DJ segment, proper broadcast level — and needs one deployment step, below. When its doors are missing it falls back to the DJ reading, and the receipt says so out loud rather than letting the downgrade pass silently.

### Wiring caller-voice

Talk Wave serves the mastered clip at a tokened URL and pushes it to the station mixer over its telnet port. The mixer fetches the clip itself, over HTTP, exactly the way it already fetches every music track — nothing is ever written to the station's disk, and nothing needs cleaning up there. Two facts about your Docker layout decide the wiring:

- Compose gives every compose project its own private network, so the station's stack and Talk Wave's cannot see each other's service names — **not a configuration anyone made, just Compose's default**. The mixer's telnet (`broadcast:1234`) only resolves on the station's network.
- Host-published ports are reachable from everywhere, which is why the clip-fetch half needs no changes at all.

So the one step is joining `talkwave-web` (the web half does the push) to the station's network, and telling it where the mixer fetches from:

```yaml
  talkwave-web:
    environment:
      - VM_AIR_BASE_URL=http://<your-LAN-IP>:8100    # where the mixer fetches a clip — or leave unset if HOST_IP is in your .env
    networks:
      - default                                       # keep — the worker reaches this container by name on it
      - <station-project>_default                     # the station's network, so broadcast:1234 resolves

networks:
  <station-project>_default:
    external: true                                    # created by the station's own compose — joined, not owned
```

Deliberately **not** the alternative of publishing the mixer's telnet port on the host: that port takes unauthenticated commands — push audio on air, skip the record — and publishing it offers that to the whole LAN. The network join offers it to exactly one container.

Every setting lives on the panel's **Voicemail** page — see the [settings reference](settings.md).
