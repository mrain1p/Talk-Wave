# web-widget

The phone. Plain browser JavaScript — **no build step, no bundler, no npm, no node_modules.**
Keep it that way; the whole thing is served as static files by `token_server.py`.

| File | Served at | What it is |
|---|---|---|
| `index.html` | `/` | The call page and the settings panel, one document |
| `app.js` | `/app.js` | All widget logic, one IIFE (~3k lines) |
| `style.css` | `/style.css` | Themed by `data-theme` on `<html>` |
| `embed.js` | — | Drop-in `<script>` for third-party pages |
| `embed-test.html` | — | Local harness for the embed path |

LiveKit's client SDK arrives from a CDN `<script>` tag in `index.html`, not from a package.

## How it is structured

`app.js` is one IIFE with no exports, and `$` is `document.getElementById`. That is deliberate
— it is a single script tag on a single page — but it means **there is no JS unit-test
harness and there is no toolchain to add one to.** What guards this file instead lives in the
Python suite (`test_sidecar.py`):

- `TestWidgetServerContract` — every path `app.js` fetches is a route `token_server.py` serves,
  and every DOM id `app.js` reaches for either exists in `index.html` or is assigned by `app.js`
  itself (`firstRun` and `pwNudge` are built on the fly; that is fine and the test knows it).
- `TestPanelMarkup`, `TestPanelLoadsOnOpen`, `TestAssetVersioning` — panel structure and
  cache-busting.

So: **if you rename a DOM id or add a `fetch()`, run the Python suite.** That is what catches it.

## Things that will bite

- **Query params are the config surface.** `?compact=1`, `?captions=full|ticker|off`,
  `?theme=light|dark`. Embeds default to the ticker so the widget stays small.
- **`?theme=inherit` is resolved by `embed.js` before the frame loads.** A cross-origin iframe
  cannot read its host page, so if `inherit` ever reaches `app.js` unresolved, there is nothing
  to inherit from and auto is the honest answer.
- An explicit theme choice is stored in `localStorage.callinTheme` and beats the OS setting; a
  host page that forces `?theme=` hides the toggle entirely.
- The operator's configured theme arrives with `/live`, long after first paint, so there are two
  code paths applying a theme and they must agree.

## Verifying a change

There is a `/wavetalk-verify` skill that boots the token server and drives the widget in the
preview browser. Use it rather than asking the operator to click around.
