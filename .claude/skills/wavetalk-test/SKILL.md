---
name: wavetalk-test
description: Run the Wave Talk test suite, or write new tests in this repo's house style. Use after any change to agent-worker/ or web-widget/, before any release, and whenever asked to add test coverage.
---

# Test Wave Talk

`agent-worker/test_sidecar.py` is the entire suite. It gates CI: no image reaches `:latest`
without it green.

## Running it

From `agent-worker/`:

```bash
LOG_TO_FILE=0 SETTINGS_PATH=/tmp/t.json SECRETS_PATH=/tmp/s.json ADMIN_AUTH_PATH=/tmp/a.json CALLS_PATH=/tmp/calls python -m unittest test_sidecar -v
```

On Windows use the venv interpreter: `../.venv/Scripts/python.exe`.

**Those env vars are not optional.** They point every writable path away from the checkout so a
test can never scribble on real settings, secrets or auth files. CI sets exactly the same set.

A single class while iterating:

```bash
python -m unittest test_sidecar.TestStoredKeysStayHome -v
```

## Running it inside the deployed image

The strongest check available — it runs against the image the operator is actually running:

```bash
docker exec -e SETTINGS_PATH=/tmp/t.json -e SECRETS_PATH=/tmp/s.json -e ADMIN_AUTH_PATH=/tmp/a.json -e LOG_TO_FILE=0 <worker-container> python -m unittest test_sidecar
```

Keep anything against the live deployment **read-only**: run the suite, dump state, never mint
a token or fire an on-air tool.

## House style — follow it, the suite is consistent

- **Stdlib only** (`unittest`, `tempfile`, `types`). The venv needs nothing new. No pytest, no
  mock libraries, no network — ever. Fake the client instead of reaching for the station.
- `os.environ["LOG_TO_FILE"] = "0"` is set **before** importing any module that calls
  `log_setup.setup()`, or the run pollutes `data/logs/worker.log`.
- Inherit from `_TempStores` for anything touching settings, secrets or auth.
- **Class names state the claim they defend.** `TestStoredKeysStayHome`,
  `TestFirstRunIsNotOpenToTheWeb`, `TestJoinTokensExpire`, `TestActionsAllHaveAReceipt`. A name
  like `TestSettings2` is wrong here.
- **When a test exists because something broke on air, say so in a comment.** Several already
  do, and that context is why they survive refactors:
  `# Went out on a real call: "(Phone rings) Yeah, Cliff here."`
- Async surfaces use `aiohttp.test_utils`; see `TestHttpSurface` for the established pattern.

## What to cover when adding a feature

This suite is biased toward regressions that would be **audible on air**, **misroute money**, or
**break tool truthfulness**. Aim there. Specifically, a new feature wants a test if it:

- adds a settings key (precedence: file → env → default, and blank means fall through)
- touches secrets (they must never make the return trip to the browser)
- adds an HTTP route the widget calls (`TestWidgetServerContract` will already catch a mismatch,
  but the route's own behaviour needs its own test)
- adds a DJ tool (it must appear in the registry with a receipt, and must not be exposed to
  callers if it is destructive)
- adds a DOM id the panel drives — the panel silently skips any schema field with no matching
  element id, so a setting can ship completely unreachable. `TestPanelMarkup` guards that.

## The widget

There is no JS toolchain in this repo and none should be added. `web-widget/app.js` is guarded
from the Python side by `TestWidgetServerContract`: every path `app.js` fetches must be a route
`token_server.py` serves, and every DOM id it reaches for must exist in `index.html` or be
assigned by `app.js` itself. **Rename a DOM id or add a `fetch()` and you must run the Python
suite.**
