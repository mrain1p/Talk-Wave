---
name: wavetalk-verify
description: Run the Wave Talk widget locally and actually exercise a change in the browser — boots token_server via the preview launcher, drives the call page or the settings panel, and works around the sandbox traps that make this slow. Use whenever a change touches web-widget/ or a token_server route the widget calls.
---

# Verify a Wave Talk change in a real browser

A passing unit suite is not verification. Run the thing, drive the flow, report what you
**observed**. This skill exists because several hours once went into fighting the environment
rather than the code — everything below is a trap already paid for.

## The fast path: real token server

`.claude/launch.json` has a `wavetalk` entry that runs `agent-worker/token_server.py` on :8100.

1. `preview_start` with `{name: "wavetalk"}`.
2. Navigate, then verify with `read_page` / `read_console_messages`, not screenshots.

This is right when the change is server-side or on the call page. It needs `.env` to exist.

**The PC stack must be stopped while the NAS stack runs** — two sidecars hammer the same
station. `./run-local.ps1 -Stop` first if in doubt.

## When you need the settings panel: the stub server

The panel calls `/settings/options`, which takes 10–15s when the station is unreachable and
needs TTS/LLM hosts you won't have locally. Write a throwaway server in the scratchpad that
serves the real `web-widget/` files plus fake `/settings`, `/settings/options`,
`/settings/sounds`, `/health`, `/live`, and register it in `.claude/launch.json`.

Build the `/settings` payload by importing the **real** `agent-worker/settings.py` —
`schema_payload()`, `load()`, `stored_only()` — so the stub cannot drift from the app. Point
`SETTINGS_PATH` at a temp file so real `data/` is untouched.

Two traps, each of which has cost a full cycle:

- **Use `ThreadingHTTPServer`, never `HTTPServer`.** Single-threaded, the preview harness holds
  a probe connection open, `serve_forever` blocks, and only `GET /` is ever served — `app.js`
  and `style.css` are silently never requested and you debug a page that never loaded its code.
- **Strip the jsdelivr `<script>` for livekit-client from `index.html`.** The sandbox blackholes
  it, and a blocking script that never resolves stalls the parser before `app.js` runs. Replace
  with `window.LivekitClient = {}` — the panel does not need it.

Set `"autoPort": true` in the launch entry and have the stub read `PORT` from env; 8100 and 8123
are often already taken by another session.

## Environment traps

- **The preview sandbox and the Bash tool cannot reach each other's ports.** A fake station
  started from Bash is invisible to a preview-started server, and Bash cannot fetch :8100 at
  all. Both sides must run under the same launcher or they may as well be on different machines.
- **The Browser pane is often hidden.** `computer` screenshots fail, `requestAnimationFrame`
  never fires (anything awaiting a frame hangs for 30s), and attribute-driven CSS may not
  recalc. To verify CSS, enumerate `document.styleSheets` rules instead of measuring computed
  styles after flipping `data-theme`.
- **The settings panel is lazy-loaded on the gear click**, not at page load. After clicking,
  a 3–4s `setTimeout` snapshot is still too early — take the DOM snapshot in a **separate**
  `javascript_tool` call afterwards.
- **`/live` is cached 30s server-side and the widget polls every 20s.** Stubbing `window.fetch`
  needs a ~21s wait per state you want to observe. There is no hook to force a repaint.

## What counts as done

Report the observed result, not the intent. Name the thing you saw: the text that rendered, the
network request that fired, the console line. If you did not run it, say you did not run it.
