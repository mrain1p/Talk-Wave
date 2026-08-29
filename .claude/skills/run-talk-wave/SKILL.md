---
name: run-talk-wave
description: Boot and drive Talk Wave on a clean machine — screenshot the caller page, the operator settings panel, and the embed, press the Call button, and run the test suite. Use to run, launch, start, screenshot, or smoke-test the widget or the whole project without a live station/LiveKit/TTS backend. Programmatic (Playwright), not the Browser pane.
---

# Run Talk Wave

Talk Wave is a LiveKit call-in + text + voicemail sidecar for an AI radio DJ:
a Python worker + token server (`agent-worker/`) and a plain-JS widget
(`web-widget/`). **The full stack cannot run headless** — the worker needs a
SUB/WAVE station, a LiveKit server, and a TTS backend it dials out to. What
runs with nothing external is the **widget**, served by the repo's own fake
backend (`tools/panel_dev_server.py`) and driven with Playwright, and the
**test suite**.

The driver is [`.claude/skills/run-talk-wave/driver.py`](driver.py). It boots
the stub on a scratch 127.0.0.1 port and drives headless Chromium. All paths
below are relative to the repo root. On Windows the interpreter is
`.venv/Scripts/python.exe`; on Linux/macOS it is `.venv/bin/python`.

## Prerequisites

The venv already has the app's deps. The driver additionally needs Playwright
+ Chromium (a DEV dependency — the shipped image never needs it):

```bash
.venv/Scripts/python.exe -m pip install playwright
.venv/Scripts/python.exe -m playwright install chromium
```

## Run (agent path) — the driver

```bash
.venv/Scripts/python.exe .claude/skills/run-talk-wave/driver.py shots widget-shots
```

Boots the stub and writes `call-page.png`, `settings-panel.png`, and
`embed-compact.png` to `widget-shots/`. **Open the PNGs** — the panel is the
one to check: a full "TALK WAVE DASHBOARD" with page tabs, the ON AIR /
STATION / BRAINS cards, the TRANSMISSION toggles and STATION OVERRIDE. Blank
cream = you shot too early (see Gotchas).

```bash
.venv/Scripts/python.exe .claude/skills/run-talk-wave/driver.py drive
```

Opens the call page, reads the Call button and status line, presses Call, and
reports the state the widget moved to. With no LiveKit backend the connection
fails, so a healthy run prints `call button reads: 'CALL THE DJ'` then
`after Call press, status: 'Could not connect'` — that transition off idle is
the proof the state machine is reachable, not just painted.

```bash
.venv/Scripts/python.exe .claude/skills/run-talk-wave/driver.py check
```

Runs `tools/widget_check.py` (the source-and-render smoke test): both pages
plus the embed's compact frame, checking for load-time JS exceptions, CSS that
parsed-but-died, and the two-pages script/stylesheet contract as the browser
resolved it. Prints `widget check: all checks passed (12 ok)`; exits non-zero
on any failure.

## Test — the suite

```bash
.venv/Scripts/python.exe agent-worker/run_tests.py
```

Runs all 32 test modules in parallel (each sets its own writable-path env), the
same suite CI and the pre-commit hook gate on. Prints
`All 32 test modules passed in ~110s (8 workers)`. The single-process form CI
also names is, from `agent-worker/`:

```bash
LOG_TO_FILE=0 SETTINGS_PATH=/tmp/t.json SECRETS_PATH=/tmp/s.json ADMIN_AUTH_PATH=/tmp/a.json CALLS_PATH=/tmp/calls LISTENERS_PATH=/tmp/l.json LISTENER_SAMPLE_INTERVAL=0 python -m unittest test_sidecar
```

## Run (human path)

`tools/panel_dev_server.py` alone serves the widget against the fake backend
for hand-driving in a real browser (`PORT=8123 python tools/panel_dev_server.py`,
then open `http://localhost:8123/settings`). Useless to an automated agent —
use the driver. For a change against the real `token_server` routes rather than
the stub's fixtures, the `talkwave-verify` skill drives the Browser pane; this
skill is the headless, no-Browser-pane path.

## The real thing

Placing an actual call needs the deployed stack. `tools/livecall/` places real
calls with a fake microphone against a running station; `agent-worker/scripted_call.py`
(the `talkwave-drill` skill) drives the real brain with typed turns inside the
deployed worker. Neither is headless-clean; both need the external services.

## Gotchas

- **The panel screenshots blank if you shoot at `load`.** Its masthead and 39
  setting sections paint only after ~two dozen fixture fetches (options,
  settings, schema, calls, live…) resolve. The driver waits for `networkidle`
  plus 800 ms; `driver.py drive`/`shots` already do this. The DOM has 161
  `.row` elements the whole time — they are just undrawn, so a DOM check passes
  while the picture is empty. Look at the picture.
- **The sections are `<details>` collapsed by default**, so `document.body.innerText`
  reads `''` and `offsetParent`-visible elements number ~15 (just the masthead
  chrome) even when the panel is fully built. Count `.row`/`.sec`, don't trust
  innerText, to tell "built" from "broken".
- **The stub picks a random `SETTINGS_PATH` under the OS temp dir per boot** and
  prints it on the first line; it is throwaway, and each `driver.py` run is a
  clean slate.
- **`driver.py` binds 127.0.0.1 only, no override flag.** A browser harness
  pointed at the deployment would hammer a live box — same rule as
  `tools/call_harness.py`, and there is deliberately no escape hatch.
- **Windows vs POSIX interpreter path**: `.venv/Scripts/python.exe` vs
  `.venv/bin/python`. This skill was authored and verified on Windows.

## Troubleshooting

- `playwright not in this venv` → run the two Prerequisites lines.
- `stub exited before it listened` → run `python tools/panel_dev_server.py` by
  hand; a syntax error or a missing dep in `agent-worker/` surfaces there (the
  stub imports the real `settings` module).
- `widget check` FAILs on a `.card`/`.row` computed-style line → a CSS rule
  died in the browser (an unbalanced brace or a stray `*/`); the suite's text
  checks cannot see this, which is why `check` exists.
