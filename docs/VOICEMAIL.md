# Voicemail

The answering machine: what it does, where messages go, and what is deliberately never recorded.

[← back to the README](../README.md)

---

## What it is

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

Two modes: **staged** (instant, the default) or **fresh** — one line written in persona at pickup, with the staged clip as backup if the model can't make it in time. Greeting text takes `{station}`, `{dj}` and `{show}`. The beep is synthesized in the browser; upload a WAV in *Call sounds* to replace it (server-played, so WAV only — anything unplayable falls back to the tone, never silence).

## Taking the message

One STT session ends on whichever comes first: the caller stops talking, the 30-second ceiling, or a hang-up (whatever was transcribed still counts; an empty transcript is a hang-up, not a message). Push to talk applies like any call — the talk bar appears the moment *Leave a message* is pressed, a tap latches the mic open, and the caller can talk over the greeting; their words are kept from the first syllable. The quiet clock starts at the beep, and the ceiling ends the whole call, not just the recording.

## Where the message goes

| Mode | What happens |
|---|---|
| `hold` | Nothing is sent — it lands in the panel's list for you to read (the safe default) |
| `request` | The text goes to the station as a song request, through the same rate-limited path a live caller's takes |
| `air` | The text is handed to the on-air DJ as something to mention — a dedication, a shout |
| triage | The model reads each message and picks one of the above, bounded by the caller permissions |

`air` puts a stranger's words in front of every listener and `request` writes station state — both need the station admin credentials and carry the same *Station admin* badge as the permission switches. Every message also writes a call record (`kind: voicemail`), so *Recent calls* shows the whole night in one place, and one message counts against *Actions per call* like any other action.

## When the machine answers

Its own switch on the dashboard, beside Live calls — and the kill switch outranks both: a paused line closes the machine too. The pickup policy is *never* / *when a live call is impossible* (nobody on air, live calls off, all slots busy) / *always* — voicemail-only being the cheapest way to run a line at all. When the machine is the answer, the card's button reads **Leave a message** instead of Call.

Every setting lives on the panel's **Voicemail** page — see the [settings reference](settings.md).
