---
name: wavetalk-diagnose
description: Work out why a Wave Talk call went badly — no answer, no music, wrong DJ behaviour, invented library results, laggy replies. Starts from the call transcripts rather than from grepping logs. Use for any report of a bad, silent, or strange call.
---

# Diagnose a bad call

**Start from `data/calls/*.json`, not from docker logs.** Since 0.9.39 every call writes one
file as it ends: both sides of the conversation, every tool with its result, the config it ran
under, and anything that failed. Newest 40 are kept. The panel's **Recent calls** button under
*Diagnostics* shows the same thing.

```
2026-08-04 23:34:36  Dalia  ·  136s  ·  6 caller turns
  google/gemini-3.1-flash-lite  stt=local/base.en  tts=local
  ⚠ station 503 on /request
23:34:45 DJ      You're through to the booth — what's on your mind?
23:34:48 CALLER  Can you play something fun?
23:34:49   tool  subwave_request_song → Added to the queue
```

The **tool column is the point**: the DJ saying it did something is a claim; that line is the
receipt. The **config line** ties a bad call to the setting that caused it.

## Read the symptom off the transcript

| What you see | What it means |
|---|---|
| DJ describes library contents, **no tool lines** | The agent had no MCP tools. Check `station_mcp_url` in `data/settings.json` — a browser-autofilled junk value here once caused every "bad call" and the DJ invented results wholesale. Guarded since 0.9.34, still check it. |
| Greeting missing, call otherwise empty | Provider 503. Gemini flash-lite overloads under congestion and even the greeting dies. 0.9.20 added a canned-TTS fallback for exactly this; move off flash-lite when congested. |
| `⚠ station <code>` lines | Station read failed. Prompts go thin rather than absent. Station **actions** get a long timeout (45s) because a segment runs before it answers — a read timeout must be reported as "sent, unconfirmed", never as failure. |
| Caller heard, DJ silent | Check TTS. A voice id from the wrong backend 400s — cloud names and local ids are not interchangeable. Reload the voice list after switching backend. |
| Everything present but slow | **Speed test** in the panel reports time to first audio per leg. Over ~1.5s sounds laggy. |

## The other diagnostic rows

- **Full pipeline check** — walks every leg in call order and names the first thing that would
  break. Run this before theorising.
- **Server logs** — recent activity. The live log carries `heard:` / `said:` / `tool:` lines
  now, not just `heard:`.
- **`/health`** — running version. Check **both** containers; they ship as one image but run as
  two, and version skew has been invisible before.

## Probing a live deployment

The operator's HTTP services are reachable from the dev machine, so **probe the endpoints
directly rather than asking them to run commands**. Read-only always: dump state, run the suite
in the container, never mint a token or fire an on-air tool against a live station.

Do not assert a diagnosis you have not measured. The operator will come back with a
counter-observation and be right.

## Model choice is not what the API says

Always probe a model through `build_llm` before recommending it — the provider's model listing
is not the truth. Models that list fine have 404'd through the plugin. Measured, working, fast:
`gemini-3.1-flash-lite` (~0.55s, best tool routing), `gemini-2.5-flash-lite` (~0.44s),
`gemini-3.6-flash` (~1.25s).

## Known open issue: off-LAN calls fail for about half of callers

The only publicly routable address LiveKit advertises is IPv6 — `rtc.node_ip` pins IPv4 to the
LAN address and overrides the STUN-discovered public IP. So IPv6 callers connect with no port
forwarding at all, and IPv4-only callers cannot connect. The fix is single-UDP-port plus
dropping `node_ip`, honest docs and a pipeline stage — or LiveKit Cloud for the media leg.
Do not diagnose this as a firewall problem on the caller's side.
