# What to run

Talk Wave is a phone call, and that is the whole constraint: a caller hears every second you spend thinking, with no spinner to hide behind. This page is what to run, and where a caller starts to notice.

Three numbers decide a call. The settings panel measures all three on your own box — **Diagnostics → full pipeline check**, and the **speed test** for the total.

| Number | Where | Target | What missing it costs |
|---|---|---|---|
| **Time to first token** (the model) | Model + tools | under **1.5s** | the pause before every reply |
| **Realtime factor** (the voice) | Voice synthesis | under **1.0** | gaps inside a sentence, worse on long replies |
| **Time to first audio** (the voice) | Voice synthesis | under ~500ms | how long the caller waits once the DJ knows what to say |

The realtime factor is the one people skip: it is synthesis time over playback time, so above 1.0 the voice is produced slower than it is heard and falls further behind with every sentence.

## The model

**Ideal — a small, fast, non-reasoning cloud model.** gpt-4.1-mini, gemini-2.5-flash, claude-haiku, deepseek-chat. Reliable tool calling, nothing to host, a fraction of a cent per call. If you have no strong reason to self-host, this is the answer — and the provider list mirrors the station's, so you probably hold one of these keys already.

**Ideal, self-hosted — a 7-8B tool-capable model resident on a GPU.** llama3.1 8B, qwen2.5 7B. Two things decide it and neither is the parameter count: whether the weights are already in VRAM when the call arrives, and how long your prompt is.

**OK — a 3-4B tool-capable model on modest hardware.** llama3.2 3B and its kin; a tester measured 1325ms and his calls worked. What you give up is judgement rather than speed — smaller models pick the wrong tool, or narrate the action instead of taking it ("let me queue that up for you", and nothing is queued).

**Minimal — anything that calls tools and answers inside the ceiling.** Below that it is not a slow line, it is no line.

**Three things don't work, however good the model is otherwise.** A model without tool calling: the DJ can talk and can never do anything, and the panel's model check says so in one line. A reasoning model on the call leg: it thinks before the first token by design, which is the number a call cannot afford — use the non-reasoning sibling. And 7B+ on CPU: prompt evaluation alone blows the budget, and a call's prompt is not small.

**Ollama has one setting worth changing.** It unloads a model after five idle minutes, so the first call after a quiet hour pays the load time on top of everything else — a DJ that works all afternoon and dies overnight. Set `OLLAMA_KEEP_ALIVE=-1` on any box that answers real calls.

## The voice

**Ideal — a cloud voice built for streaming.** OpenAI, ElevenLabs and Fish Audio adapters ship with it. First audio in the low hundreds of milliseconds, and no GPU contention with the station.

**OK — a fast local backend.** Anything speaking a simple HTTP speech API can be pointed at with an adapter; the files in `agent-worker/tts-adapters/` are the worked examples. Pick for speed on the call leg, not for the best sound you can achieve offline.

**The crack: high-quality diffusion TTS on a shared GPU.** VibeVoice sounds the best of anything here and is the most likely to fall behind playback. Measured on a 16GB RTX 5070 Ti: the 1.5B model runs 1.55-1.9x realtime, the 7B AWQ quant 1.10-1.15x — all of them slower than the caller hears them. If the same card also renders the station's on-air segments, an overlapping call and render contend and playback starves. Give the call leg its own card, cheaper settings, or a cloud voice.

**Minimal — the station's own backend, mirrored.** `subwave-remote.json` points the call at whatever the station already speaks through: nothing new to run, one voice to maintain, and the contention above is the price.

## The ears

The bundled Whisper (`base.en`) needs no key, no network and no GPU, which is why it is the default. It mishears names, song titles and accents — on a request line, exactly the vocabulary that matters. Deepgram or a cloud transcriber is the cheapest of the three upgrades.

**The four bundled sizes buy accuracy with the caller's silence.** The bundled Whisper only starts once the caller stops talking, so its whole runtime lands in the pause before the DJ answers, on top of the model and the voice. Measured with the shipped settings (int8 on CPU, four threads, greedy decoding), transcribing the same ~6-second caller turn five times per size, on one ordinary desktop CPU:

| Model | Download | In memory | A ~6s turn | CPU spent on it | Cost vs `base.en` |
|---|---|---|---|---|---|
| `tiny.en` | 78 MB | ~130 MB | ~0.35s | ~1.2 CPU-s | half |
| `base.en` | 148 MB | ~175 MB | ~0.7s | ~2.3 CPU-s | — |
| `small.en` | 486 MB | ~370 MB | ~2.2s | ~7 CPU-s | 3x |
| `medium.en` | 1.5 GB | ~930 MB | ~5.5s | ~20 CPU-s | 8x |

Your absolute times will differ with the CPU — the ratios are what travel. The **speed test** measures the size you picked on your own box, on real speech.

**The cost per turn is nearly flat, so short turns pay the most per word.** Whisper processes audio in 30-second windows, and almost every caller turn fits in one: tripling the utterance to 17 seconds cost only 20-30% more than the 6-second turn, on every size. A caller's "yeah, that one" costs almost as much as their longest story.

**Against the ~1.5s turn budget:** `base.en` fits with room left for the model and the voice. `small.en` spends the whole budget by itself, and `medium.en` is several times over it — the line reads as dead while it thinks. The bigger sizes belong where nobody is waiting, voicemail being the obvious case; if live accuracy is the problem, a cloud transcriber is faster *and* better than climbing the size ladder.

**While it runs, it takes its four cores.** Every size saturated its four threads (~3.5 cores effective), so the CPU column is also what the rest of the box gives up mid-turn — and `medium.en`'s process peaked just under 2 GB while transcribing, which on a small host is an eviction notice for something else.

**Switching sizes costs the next call a model load.** The worker prewarms the size configured at startup; pick a different one in the panel and the first turn of the next call carries the load — about 2 seconds for the smaller pair up to 6 for `medium.en`, once per worker process.

**What this bench cannot tell you.** On a clean, clearly-spoken test line all four sizes came back essentially identical. The sizes separate on names, accents, background noise and phone-band audio — exactly the vocabulary a request line lives on. Size is a hedge against hard audio, and the table is the price of the hedge, paid on every turn.

## Where the caller notices

| What the caller experiences | The number behind it |
|---|---|
| A pause before every reply | first token between 1.5s and the ceiling |
| "The line's giving me trouble on my end" | first token over the ceiling — the turn is thrown away |
| The DJ agrees, then nothing happens | the model can't call tools, or narrated instead of acting |
| Gaps inside a sentence, worse on long replies | voice realtime factor over 1.0 |
| A long silence before the DJ says hello | the greeting is a model turn too |
| The wrong song title comes back | speech-to-text, not the model |
| Fine all day, broken after a quiet night | the local model unloaded — see `OLLAMA_KEEP_ALIVE` |

**The ceiling is a live-call constraint.** Text mode and voicemail have no one waiting on the line, so a slower, better model is a perfectly good trade if most of your traffic arrives that way.

## Check yours

The **full pipeline check** walks every leg in call order and stops at the first thing that would break; the model and voice stages each make one real, sub-cent call. The **speed test** adds the legs up, which is what the caller actually experiences. Both run the real code paths with a real call's prompt, so what you read is what a caller gets.

[Back to the README](../README.md)
