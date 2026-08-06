---
name: wavetalk-tts-bench
description: Measure whether a TTS or STT backend can actually carry a live call before switching to it — first-audio latency, realtime factor, voice availability and what a swap silently changes. Use for "should I use cloud TTS", "is this voice fast enough", "compare local and cloud", "the DJ sounds laggy or gaps mid-sentence", or before pointing this at a backend nobody has tried.
---

# Bench a speech backend

Sibling to `wavetalk-llm-bench`. That one asks whether a model can *think* fast
enough; this asks whether a backend can *speak* fast enough. They are different
failures: a slow LLM makes the DJ hesitate, a slow TTS makes it gap mid-word.

**Measure, recommend, and change nothing.** The live setting is the operator's.

## The number that decides it

From the panel's **Voice test** (`/test/tts`) — or `POST /test/tts` directly:

```
first audio        how long the caller waits after the DJ decides what to say
realtime factor    generation speed ÷ playback speed
```

**Realtime factor is the one that matters.** Below 1.0 the backend generates
faster than the caller hears it, so the buffer stays ahead. At or above 1.0 it
falls behind mid-sentence and playback gaps — audible, and it gets worse the
longer the line. Below 0.7 is comfortable.

First audio adds to every turn. The whole turn budget is ~1500ms before a call
reads as laggy, and on the operator's own deployment TTS is 1303 of 2117ms —
so this is usually the leg to fix, not the model.

**Run the full pipeline check too** (`/test/env`, or *Run all* in the panel).
Its *Voice availability* stage catches the failure a latency test cannot: a
voice the backend does not have. That was a whole silent call in 0.9.81.

## Comparing two backends honestly

1. Same text both times. The test line is fixed for exactly this reason.
2. Same voice class — a cloud stock voice against a local cloned one is not a
   comparison, it is two different products.
3. Three runs each. First-audio on a cold cloud connection is not the number
   you will live with.
4. Note the realtime factor, not just the milliseconds.

## What a swap silently changes, and what to check after

- **The declared sample rate is the quietest failure in the stack.** It lives
  in the adapter's `audio.sample_rate`, and it is a label attached to the
  samples rather than anything carried in them — declare half the real rate and
  every line plays at half speed an octave down, with no error logged anywhere.
  Some engines report one rate on a GPU and half of it on a CPU, so an adapter
  that is correct on one host is wrong on the next. **Test voice measures it**
  (it asks the backend for wav and reads the RIFF header) and fails the test on
  a mismatch whatever the realtime factor says. Do not try to infer the rate
  from how fast the speech sounds: a persona written to speak in fast clipped
  fragments produces a fraction of the audio a normal voice does for the same
  text, and that reasoning lands octaves out. Measure, or use an ordinary voice
  at several text lengths.
- **Voice ids do not transfer.** Cloud names (`alloy`, `nova`) and local ids
  (`-Cliff1`, `Lily`) are different namespaces. Since 0.9.81 a voice the
  backend lacks falls back audibly and writes the reason into the call record
  rather than producing silence — but the DJ is then not the voice the station
  broadcasts. **Reload the voice list after switching**, then check the
  station's on-air persona actually resolves.
- **Check every persona, not just the one on air.** `/test/speed` now reports
  how many of the station's personas use a voice this backend does not have.
  Without that, a persona whose voice is missing stays invisible until someone
  rings in while that DJ is live.
- **A backend that lists its voices somewhere other than `/v1/audio/voices`
  needs `voices_path` in its adapter.** An unreadable list means "could not
  find out", not "has none", so the symptom is not an error — it is the panel
  quietly showing stock OpenAI names and the voice-availability check never
  running at all.
- **`tts_model` is hidden unless the mode is cloud.** Switching to local does
  not clear it; switching back brings it and its old value into effect.
- **The adapter follows the mode, not the URL.** `tts_mode` chooses
  `local-vibevoice.json` or `openai-cloud.json` unless `tts_adapter` names one.
  Pointing a cloud URL at the local adapter fails in a way that reads as the
  server being wrong.
- **Cloud STT changes the caption path**, not just accuracy — local Whisper is
  batch, cloud is word-by-word, so captions and the idle clock behave
  differently. The idle clock counts *words heard*, so a backend that emits
  partials sooner makes the DJ check in sooner.

## Cost, which no test will tell you

Cloud TTS bills per character and the DJ speaks on every turn. There is no
TTS-side spend ceiling — the guards are `max_call_seconds`, `calls_per_hour`
and `calls_per_day`, which bound it *indirectly*. Do that multiplication before
switching a public deployment to a paid backend: worst case is
`calls_per_day × max_call_seconds` of continuous speech.

## Reporting

Give the operator the two numbers per backend, the verdict against 1.0
realtime, and what a swap would change from the list above. Recommend one, say
why, and say what you did not measure — quality is not in any of these numbers,
and it is usually the reason someone wants the cloud backend in the first place.
