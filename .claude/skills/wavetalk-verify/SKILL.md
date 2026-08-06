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

## When you need the settings panel: `tools/panel_dev_server.py`

**Committed, not written from scratch each time.** It serves the real `web-widget/` files
against the real settings schema with everything slow faked — `/settings`,
`/settings/options`, `/settings/sounds`, `/logs`, `/health`, `/live` — so the panel paints in
under a second instead of the 10–15s the real `/settings/options` takes when the station is
unreachable. It points every writable path at a temp dir before importing `settings`, so
driving the panel can never touch real settings.

Add it to `.claude/launch.json` and `preview_start` it:

```json
{ "name": "panel-stub",
  "runtimeExecutable": ".venv/Scripts/python.exe",
  "runtimeArgs": ["tools/panel_dev_server.py"],
  "port": 8123, "autoPort": true }
```

`autoPort` matters — 8100 and 8123 are usually already taken by another session. **Take the
launch.json entry back out before committing**; it is scaffolding, not configuration.

Its fixtures deliberately reproduce the 0.9.81 bug: the voice list has no entry for the
station's `p_default1` voice, so voice-mismatch handling stays exercisable.

Build the `/settings` payload by importing the **real** `agent-worker/settings.py` —
`schema_payload()`, `load()`, `stored_only()` — so the stub cannot drift from the app. Point
`SETTINGS_PATH` at a temp file so real `data/` is untouched.

Two traps, each of which has cost a full cycle:

- **Use `ThreadingHTTPServer`, never `HTTPServer`.** Single-threaded, the preview harness holds
  a probe connection open, `serve_forever` blocks, and only `GET /` is ever served — `call.js`
  and `style.css` are silently never requested and you debug a page that never loaded its code.
- **Strip the jsdelivr `<script>` for livekit-client.** The sandbox blackholes it, and a
  blocking script that never resolves stalls the parser before the page's own scripts run.
  Replace with `window.LivekitClient = {}` — nothing but placing a real call needs it.

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
- **The panel is its own page at `/panel`** since 0.9.105 — navigate there rather than looking
  for a gear to click. It loads its settings on arrival, but the fetches take a moment: a
  `setTimeout` inside the same call is still too early, so take the DOM snapshot in a
  **separate** `javascript_tool` call afterwards.
- **The two pages load different scripts**, and that is worth checking rather than assuming:
  `/` must load `shared.js` + `call.js` and no panel code; `/panel` loads `shared.js` +
  `panel.js` + `panel-viewers.js`. `read_network_requests` shows this directly.
- **`/live` is cached 30s server-side and the widget polls every 20s.** Stubbing `window.fetch`
  needs a ~21s wait per state you want to observe. There is no hook to force a repaint.

## What counts as done

Report the observed result, not the intent. Name the thing you saw: the text that rendered, the
network request that fired, the console line. If you did not run it, say you did not run it.
