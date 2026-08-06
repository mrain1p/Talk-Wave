# web-widget

The phone. Plain browser JavaScript — **no build step, no bundler, no npm, no node_modules.**
Keep it that way; the whole thing is served as static files by `token_server.py`.

| File | Served at | What it is |
|---|---|---|
| `index.html` | `/` | The call page and the settings panel, one document |
| `shared.js` | `/shared.js` | What both surfaces need, published as the `Callin` global |
| `call.js` | `/call.js` | The phone: the card, the meters, the captions, the call itself |
| `panel.js` | `/panel.js` | The operator's surface: settings, `/test/*`, uploads, the viewers |
| `style.css` | `/style.css` | Themed by `data-theme` on `<html>` |
| `embed.js` | — | Drop-in `<script>` for third-party pages |
| `embed-test.html` | — | Local harness for the embed path |

LiveKit's client SDK arrives from a CDN `<script>` tag in `index.html`, not from a package.

## How it is structured

Three script tags, not modules — there is **no build step, no bundler, no npm**, and the split
was done in a way that keeps it that way. `shared.js` publishes one global, `Callin`, and the
other two destructure what they need from it at the top of their own IIFE. `$` is
`document.getElementById`.

The seam is deliberately narrow. `shared.js` holds only what both surfaces genuinely want: the
query-param and theme setup, the `ASKS`/`NEVER` lists, `CALL_KEY`, and the synthesized sound
engine. Anything only one surface uses belongs in that surface's file.

**The dependency runs one way.** `panel.js` may call `window.Callin.refreshLive()` if the call
page happens to be on the same document, and does nothing when it isn't — that hook is the only
thing crossing between them, and it exists so the panel does not have to know whether there is a
card to repaint. The sound engine is *fed* (`setSounds`) rather than read from, which is why a
sound preview in the panel no longer briefly changes what a live caller would hear.

There is still **no JS unit-test harness and no toolchain to add one to.** What guards these
files lives in the Python suite (`test_sidecar.py`):

- `TestWidgetServerContract` — reads *every* `.js` in this directory, so a fourth file is covered
  the moment it lands. Every path the widget fetches is a route `token_server.py` serves; every
  DOM id it reaches for exists in `index.html` or is assigned in JS (`firstRun` and `pwNudge` are
  built on the fly; that is fine and the test knows it); and `index.html` must actually load each
  file, because a script nothing loads is this split's own failure mode.
- `TestPanelMarkup`, `TestPanelLoadsOnOpen`, `TestAssetVersioning` — panel structure and
  cache-busting.
- `TestNoFileGrowsWithoutSomebodyDeciding` — `call.js` and `panel.js` are both still over the
  600-line ceiling and carry waivers saying so. Neither may grow.

So: **if you rename a DOM id or add a `fetch()`, run the Python suite.** That is what catches it.

## Things that will bite

- **Query params are the config surface.** `?compact=1`, `?captions=full|ticker|off`,
  `?theme=light|dark`. Embeds default to the ticker so the widget stays small.
- **`?theme=inherit` is resolved by `embed.js` before the frame loads.** A cross-origin iframe
  cannot read its host page, so if `inherit` ever reaches `call.js` unresolved, there is nothing
  to inherit from and auto is the honest answer.
- An explicit theme choice is stored in `localStorage.callinTheme` and beats the OS setting; a
  host page that forces `?theme=` hides the toggle entirely.
- The operator's configured theme arrives with `/live`, long after first paint, so there are two
  code paths applying a theme and they must agree.
- **Which corner controls exist is the server's answer, not a CSS rule.** `/live` carries
  `controls: {help, theme, settings}` (`token_server.corner_controls`). `applyControls()` may
  only *subtract* — a host that pinned `?theme=`, or an embed, which never loads the panel code
  at all. Three separate mechanisms used to decide this and the two surfaces disagreed.
- **`embed.js` and `call.js` talk over `postMessage`, both ways.** Widget → host:
  `subwave-callin:height` (report my height) and `subwave-callin:overlay` (I need N px of room
  for the ask list; 0 = done). Host → widget: `swtv:theme` (station palette, repaint in place —
  never reload, that drops the call) and `swtv:overlay` (here is the room, and the direction).
  The host owns the direction because only it can see the page. While overlaid the widget stops
  reporting height and the host ignores it, or the frame adopts the overlay's size for good.

## Verifying a change

There is a `/wavetalk-verify` skill that boots the token server and drives the widget in the
preview browser. Use it rather than asking the operator to click around.
