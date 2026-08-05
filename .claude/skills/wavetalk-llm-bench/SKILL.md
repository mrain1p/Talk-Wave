---
name: wavetalk-llm-bench
description: Measure whether an LLM can actually run the call-in DJ before switching to it — tool-calling reliability, per-leg latency, and conduct. Use for "which model should I use", "is <model> fast enough", "compare these models", "why is the DJ slow", or any diagnosis where the model choice is suspect. Measures and recommends only; never changes the live setting.
---

# Benchmark a model for the call line

Adapted from SUB/WAVE's `subwave-llm-bench`. The load-bearing idea transfers exactly: **the
same model can pass through one provider and fail through another**, because each provider
translates tool calls and structured output differently. So always test the provider path you
will actually deploy.

For this project that is not theoretical. The operator's Google key **404s on
`gemini-2.5-flash` and `gemini-2.0-flash` through the livekit plugin even though the API lists
them as available.** A model listing is not evidence. `build_llm` is.

## The rule

**Never recommend a model you have not probed through `build_llm(cfg)`.** That is the real
construction path — the same one a call uses — and it is where provider translation breaks.

## What to measure, in this order

### 1. Does it actually call tools?

The single most important property, and the one a chat-quality impression completely misses.

```bash
curl -s -X POST http://<host>:8100/test/llm -H 'Content-Type: application/json' \
  -d '{"llm_provider":"google","llm_model":"gemini-3.1-flash-lite"}'
```

`/test/llm` runs one short completion **with a tool offered** and reports whether the model
emitted a tool call. Plenty of models — local ones especially — answer fluently and never call
a tool, which reaches the operator as *"the DJ never submits a request"* and looks like a bug
in the tool registry rather than the model.

A model that does not call tools is disqualified. Nothing else about it matters.

### 2. Is the whole turn fast enough?

```bash
curl -s -X POST http://<host>:8100/test/speed -H 'Content-Type: application/json' \
  -d '{"llm_provider":"google","llm_model":"gemini-3.1-flash-lite"}'
```

`/test/speed` times every leg — station snapshot, STT, LLM, TTS — and sums them. Read the
**total**, not the legs: each one can look acceptable while the sum decides whether the DJ feels
like a person or a kiosk. Over ~1.5s to first audio sounds laggy on a phone call.

Both endpoints take a body that **overrides settings for that request only**, so you can measure
a candidate without touching what the station is running. Both need admin auth.

### 3. Does it behave on a real call?

Latency and tool-calling are necessary, not sufficient. Conduct — whether the DJ pushes to end
the call, repeats its goodbye, invents library results — only shows up with a caller in front of
it. That is what `agent-worker/scripted_call.py` is for; see the `wavetalk-diagnose` skill and
the harness's own docstring. It drives the real brain with typed caller turns and records what
the DJ tried to do, with every station write swapped for a recorder.

It spends the operator's LLM key, a few calls per scenario. Budget for that.

## Measured baselines

Probed through `build_llm`, not read off a docs page:

| Model | Time to first audio | Notes |
|---|---|---|
| `gemini-3.1-flash-lite` | ~0.55s | Best tool routing. Current choice. |
| `gemini-2.5-flash-lite` | ~0.44s | Fastest measured. |
| `gemini-3.6-flash` | ~1.25s | Usable, noticeably slower. |
| `gemini-2.5-flash`, `gemini-2.0-flash` | — | **404 through the plugin** despite being listed. |

Flash-lite models **503 under provider congestion**, and when that happens even the greeting
dies. 0.9.20 added a canned-TTS fallback so the caller is not left in dead air, but the right
response to a congested window is to move off flash-lite, not to lean on the fallback.

## Reporting

Say what you measured, on which provider path, and how many samples. Give a recommendation, not
a table dump. If a model failed, say what it failed at — "never emitted a tool call" and "1.9s
to first audio" lead to completely different decisions.

**Do not change the configured model as part of benchmarking.** Measure, recommend, and let the
operator switch it in the panel.
