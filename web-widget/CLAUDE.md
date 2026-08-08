# web-widget

The phone. Plain browser JavaScript — **no build step, no bundler, no npm, no node_modules.**
Keep it that way; the whole thing is served as static files by `token_server.py`.

**Two pages, and they never load each other's script.**

| File | Served at | What it is |
|---|---|---|
| `index.html` | `/` | The call page. Loads `shared.js` + `call.js` |
| `panel.html` | `/settings` (`/panel` redirects) | The operator's page. Loads `shared.js` + `panel.js` + `panel-viewers.js` |
| `shared.js` | `/shared.js` | What both pages need, published as the `Callin` global |
| `call.js` | `/call.js` | The phone: the card, the meters, the captions, the call itself |
| `panel.js` | `/panel.js` | The operator's surface: settings, secrets, `/test/*`, uploads |
| `panel-viewers.js` | `/panel-viewers.js` | Reading back what happened: the log and call viewers |
| `style.css` | `/style.css` | Both pages. Themed by `data-theme` on `<html>` |
| `embed.js` | — | Drop-in `<script>` for third-party pages |
| `embed-test.html` | — | Local harness for the embed path |

The panel was a section of `index.html` until 0.9.105, which meant **every anonymous caller
downloaded the whole operator interface** to look at a page with one button on it. Nothing
leaked — every endpoint behind the panel checks admin auth for itself — but the two audiences
could not be told apart by anything in front of the server. On its own URL they can be: if this
box is reachable from outside your network, a rule in front of `/settings` in the reverse proxy (an IP
allowlist, or basic auth) is worth adding, and is the main reason the page exists separately.

LiveKit's client SDK arrives from a CDN `<script>` tag in both pages, not from a package. The
panel needs it because the full-pipeline check really does place a call — it mints a token,
connects, reads the ICE candidates and hangs up, which is the only way to tell "signalling
works" from "media works".

## How it is structured

Script tags, not modules — there is **no build step, no bundler, no npm**, and every split was
done in a way that keeps it that way. `shared.js` publishes one global, `Callin`, and each page's
scripts destructure what they need from it at the top of their own IIFE. `panel.js` publishes a
second, `Panel`, carrying the two names `panel-viewers.js` needs (`afetch`, `showResult`) — which
is why the script order in `panel.html` is load-bearing rather than cosmetic. `$` is
`document.getElementById`.

The seam is deliberately narrow. `shared.js` holds only what both surfaces genuinely want: the
query-param and theme setup, the `ASKS`/`NEVER` lists, `CALL_KEY`, and the synthesized sound
engine. Anything only one surface uses belongs in that surface's file.

**Nothing crosses between `call.js` and `panel.js`.** They are never on the same page, so there
is no hook, no shared state and no direction to get wrong. Each fetches `/live` for itself. The
sound engine in `shared.js` is *fed* (`setSounds`) rather than read from, which is why a sound
preview in the panel no longer briefly changes what a live caller would hear — it used to reach
into the call page's own `/live` object and put it back 1.5 seconds later.

A settings save no longer repaints the call card, because there is no card on that page. The
call page's own 20-second `/live` poll picks the change up instead.

There is still **no JS unit-test harness and no toolchain to add one to.** What guards these
files lives in the Python suite (`test_sidecar.py`):

- `TestWidgetServerContract` — reads *every* `.js` in this directory, so a fourth file is covered
  the moment it lands, and checks DOM ids **per page**: `call.js` against `index.html`,
  `panel.js` against `panel.html`. That is stricter than the old whole-widget check — reaching
  for an id that lives on the other page used to pass, because both surfaces were one document.
  It also pins which scripts each page loads, in order, so neither page can start shipping the
  other's, and asserts the call page contains no trace of the settings form.
- `TestPanelMarkup`, `TestPanelLoadsOnOpen`, `TestAssetVersioning` — panel structure and
  cache-busting.
- `TestNoFileGrowsWithoutSomebodyDeciding` — `call.js`, `panel.js`, `panel.html` and
  `style.css` are all over the 600-line ceiling and all `EXEMPT`, every one of them *measured*
  rather than assumed. **If you are tempted to split one of these, measure the seam first** —
  count the names a candidate region needs from the rest of the file and the names the rest
  needs back. The two splits that did happen scored 6 and 2. What is left scores 5 (captions,
  for 140 lines), 10 (the pipeline check) and 25 (the call itself), and every region of
  `call.js` is coupled in both directions because every part of a call touches `room`,
  `live`, `callBtn`, `capBox` and `muted`. `style.css` has 308 lines used by both pages
  against 193 panel-only, so splitting leaves two files that still need each other.

So: **if you rename a DOM id or add a `fetch()`, run the Python suite.** That is what catches it.

## Things that will bite

- **Query params are the config surface.** `?compact=1`, `?captions=full|ticker|off`,
  `?theme=light|dark`. Embeds default to the ticker so the widget stays small.
- **`?theme=inherit` is resolved by `embed.js` before the frame loads.** A cross-origin iframe
  cannot read its host page, so if `inherit` ever reaches `call.js` unresolved, there is nothing
  to inherit from and auto is the honest answer.
- An explicit theme choice is stored in `localStorage.callinTheme` and beats the OS setting.
  A host's `data-theme` arrives as `?themeDefault=` — a starting point the toggle can override —
  and only `data-lock-theme="true"` sends the old `?theme=`, which pins it and hides the toggle.
- The operator's configured theme arrives with `/live`, long after first paint, so there are two
  code paths applying a theme and they must agree.
- **Which corner controls exist is the server's answer, not a CSS rule.** `/live` carries
  `controls` AND `embedControls` (`api/live.corner_controls`), plus `card`/`embedCard` for the
  who's-on-air lines and a resolved `callLabel`. `/live` is cached across every caller and
  cannot know which surface is asking, so it sends both and the widget picks on `framed`.
  `applyControls()` may still only *subtract* — a host that pinned `?theme=`, or an embed,
  which never loads the panel code at all. Three separate mechanisms used to decide this and
  the two surfaces disagreed by accident; they may differ now, but only because an operator
  filled in two columns.
- **`embed.js` and `call.js` talk over `postMessage`, both ways.** Widget → host:
  `subwave-callin:height` (report my height) and `subwave-callin:overlay` (I need N px of room
  for the ask list; 0 = done). Host → widget: `swtv:theme` (station palette, repaint in place —
  never reload, that drops the call) and `swtv:overlay` (here is the room, and the direction).
  The host owns the direction because only it can see the page. While overlaid the widget stops
  reporting height and the host ignores it, or the frame adopts the overlay's size for good.

## Verifying a change

There is a `/wavetalk-verify` skill that boots the token server and drives the widget in the
preview browser. Use it rather than asking the operator to click around.
