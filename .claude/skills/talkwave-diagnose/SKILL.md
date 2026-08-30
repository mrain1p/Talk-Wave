---
name: talkwave-diagnose
description: Work out why a Talk Wave call went badly — no answer, no music, wrong DJ behaviour, invented library results, laggy replies. Starts from the call transcripts rather than from grepping logs. Use for any report of a bad, silent, or strange call.
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
| "The DJ queued it but something else played" | Station 1.9.0+ (upstream #1415): an exact pick Liquidsoap cannot resolve is spliced out near its seam and **autonomously re-picked**, with no webhook we can hear. Before suspecting the tools, check the station log — or authed `GET /debug`, `queue.djLog`, which works with LOG_TO_FILE off — for a "Liquidsoap never resolved" line. A Talk Wave pick shows "(requested by studio)", same as an operator dashboard pick; a caller name in the suffix means it came via /request. |

## The other diagnostic rows

- **Full pipeline check** — walks every leg in call order and names the first thing that would
  break. Run this before theorising.
- **Server logs** — recent activity. The live log carries `heard:` / `said:` / `tool:` lines
  now, not just `heard:`.
- **`/health`** — the **web** container's running version, and only that one: the worker
  publishes no port, so its version comes from its boot banner (`docker logs talkwave-worker
  2>&1 | grep 'talk-wave worker' | tail -1`) or from the panel's own "the two containers
  disagree" notice, which fires once a call has been answered since the panel booted. They ship
  as one image but run as two, and version skew has been invisible before.

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

## Off-LAN reach: SOLVED with config (2026-08-11) — verify the config before diagnosing

This was a known open issue ("off-LAN calls fail for about half of callers"): `rtc.node_ip`
pinned the advertised IPv4 to the LAN address, so only IPv6 callers connected. Fixed by
config, and proven by a cellular-with-wifi-off call on the live deployment: `livekit.yaml`
carries `use_external_ip: true` with `node_ip` UNSET, and the router forwards UDP 7882 to the
LiveKit host (docs/networking.md option 3; the TLS front-door section covers signalling).

If an off-LAN caller fails NOW, check the config regressed before anything else:
`docker logs <livekit> | grep "using external IPs"` must show the real public IPv4, not the
LAN address — a re-pinned `node_ip`, a moved DHCP lease under the router rule, or newly
appeared CGNAT are the three ways it comes back. Only then look elsewhere, and still do not
diagnose it as a firewall problem on the caller's side.
