# Wave Talk — Admin Panel Redesign ("3a", newspaper direction)

Implementation spec for the approved mock (Canvas.dc.html, option 3a).
Scope: **the panel page only** (`panel.html` / `.panelpage` rules in `web-widget/style.css`).
The embeddable call card stays untouched — it is deliberately neutral per the file's own header
and HOST-STYLE-GUIDE.md. Everything below rides the existing token system, so
system-preference default, `data-theme="light"` / `"dark"` override, and the station
third-theme layer all keep working unchanged.

## 1. Tokens (`:root`, `[data-theme=light]`, `[data-theme=dark]`)

Light becomes the cream newspaper sheet; dark aligns with the Wave TV picker family.

Light:
```css
--pine: #f3efe6;      /* page  */      --granite: #f3efe6;   /* flatten: panel = page */
--granite-hi: #eae5d9;
--alpenglow: #1a1613; --sage: #7a7268; --sage-dim: #a49b8e;
--coral: #c5302a;     --amber: #9a6b16;
--hairline: rgba(26,22,19,.12);  --edge: rgba(26,22,19,.30);
--shadow: none;
```
Dark:
```css
--pine: #100e0c;      --granite: #100e0c;   --granite-hi: #181513;
--alpenglow: #f3efe6; --sage: #969187; --sage-dim: #5c574f;
--coral: #c5302a;     --amber: #d8a13a;
--hairline: rgba(243,239,230,.12);  --edge: rgba(243,239,230,.30);
```
Default stays "follow system" (current `prefers-color-scheme` behavior); the ☾ toggle
cycles as it does now. `--ok` keeps its green only for semantic pass/fail text — ON
states in chrome move to `--coral` (§4).

## 2. Global chrome (panel scope)

- `border-radius: 0` on every panel-page container, button, chip, input, and `details.sec > summary` hover. Grep the `.panelpage` sections for `border-radius:` and zero them (keep the call-card values).
- Kill panel nesting: the outer group cards (`.group`-level containers around TRANSMISSION / LIVE CALLS / VOICEMAIL / TEXT LINE) lose background and border — `background: transparent; border: 0; padding: 0`. Each group is introduced by a caption instead: 11px `--font-mono`, `letter-spacing: .28em`, `--sage`, with `border-bottom: 1px solid var(--hairline)`.
- `.tile` (inner stat/toggle cards): drop `box-shadow: inset … var(--line)` fills; become ruled rows — `border: 0; border-bottom: 1px solid var(--hairline); background: transparent`. Hover: no translateY; just `background: color-mix(in srgb, var(--alpenglow) 4%, transparent)`.

## 3. Masthead

Replace the small "DASHBOARD" eyebrow with a nameplate block at the top of `panel.html`:
- `WAVE TALK` — `--font-mono` 700, 28px, `letter-spacing: .06em`, `--alpenglow`.
- Sub-line — 11px mono, `letter-spacing: .24em`, `--sage`: `DASHBOARD · <host>`.
- Double rule below: `border-top: 3px solid var(--coral)` then a 1px `--edge` hairline 4px under it (two stacked divs or border + box-shadow).
- Back / theme / sign-out buttons: square (radius 0), `1px solid var(--edge)`, mono 11px letterspaced caps, transparent fill.

## 4. Toggles

Replace the rounded pill switch (`.…{width:40px;height:22px;border-radius:999px}` + round knob):
- Track: `40px × 22px`, radius 0, `1px solid var(--coral)`, `background: color-mix(in srgb, var(--coral) 14%, transparent)` when on; `--edge` border + transparent when off.
- Knob: `16px` square, radius 0, `background: var(--coral)` (on) / `var(--sage-dim)` (off), translateX as now.
- The primary "THE LINE" row gets the tuned-row treatment when open: `border-left: 4px solid var(--coral)` + `background: color-mix(in srgb, var(--coral) 8%, transparent)`; value text ("Open") in `--coral`.

## 5. Sections and settings rows

- Section slabs (`CONFIGURATION`, `PERMISSIONS & SAFETY`, …): remove `--granite-hi` background bars. New treatment: bold mono heading (15px, `letter-spacing:.08em`) with a `2px` `--coral` rule filling the rest of the line (flex: heading + `flex:1; height:2px; background:var(--coral)`).
- `details.sec > summary` rows: keep structure; disclosure caret `▶` colored `--coral`; keep the right-aligned value summaries (they're already right). Row rule: `border-bottom: 1px solid var(--hairline)`.
- JUMP TO chips + Save / Reset / Run / Load buttons: square, `1px solid var(--edge)`, mono letterspaced caps, transparent; hover fills `--granite-hi`. Primary Save: `2px solid var(--coral)`, fill `color-mix(in srgb, var(--coral) 14%, transparent)`.
- "Find a setting…" input: square, `1px solid var(--edge)`, transparent.

## 6. Stats

Stat pairs ("8 recent / 6 failed", "1 taken / 1 passed on") right-align inside their line rows as stacked figures: value 15px 700 `--alpenglow`, sub-line 11px `--sage`; failure counts in `--coral` (not amber). The station strip (ON AIR / STATION / BRAINS·VOICE·EARS) becomes three ruled columns under a shared `--hairline` top rule; avatar square (`data-avatar="square"` already exists).

## 7. Footer

`Wave Talk v0.10.35 · caller.yosemite.my` moves into a full-width footer row: `border-top: 1px solid var(--edge)`, space-between, 11px mono `--sage`.

## Sanity checklist

- Station theme layer: it overrides the same tokens, so verify a saturated station accent still passes the panel's existing contrast handling.
- Both themes: check `--edge`/`--hairline` alpha values read on cream AND near-black.
- Embed/call-card pages: confirm no `.panelpage`-scoped rule leaks (grep new rules for the scope class).
- Mobile breakpoint (`@media` single-column `.grouprow`): ruled rows must keep their bottom hairlines when stacked.
