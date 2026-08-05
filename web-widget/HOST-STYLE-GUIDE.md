# Restyling the call-in widget to belong to its host page

Handoff spec for `web-widget/` (`index.html`, `app.js`, `style.css`).

The widget currently reads as a third-party component pasted onto the station page rather
than part of it. This document says what to change, what to keep, and how to make the
styling driven by the host instead of baked in.

Two things here are behaviour, not decoration, and matter more than the palette work:

1. **The transcript grows as lines arrive, so the whole frame resizes mid-call.** Fix by
   reserving the space. Details in §5.
2. **Nothing in the widget may cap or fix the frame height** — the host relies on the
   widget reporting its own height. See §7.

---

## 1. What's wrong today

Three mismatches, in order of how much they cost:

| | Widget now | Host page |
|---|---|---|
| Corners | ~14px radius on the panel, chips, inputs and buttons | Square. Every edge on the page is a right angle |
| Surface | Its own near-black card floating inside the column | No card. Bands sit directly on the page, separated by hairlines |
| Controls | Sentence-case bold sans (`Call the DJ`, `Hang up`, `Unlock the line`) at three different sizes and fills | One button spec: mono, uppercase, `0.14em` tracking, 700, 31px tall, square |

There are currently **at least four button treatments** inside the widget (`Call the DJ`,
`Unlock the line`, `Enter the code`, `Mute`/`Hang up`). There should be two: one primary,
one secondary. See §4.7.

The transcript also renders in **all caps at a large size**. Caps destroy word-shape
recognition and are the wrong choice for full sentences — this is the least readable text
on the page and it's the text that matters most during a call. Sentence case, 13.5px.

---

## 2. The theming contract

**Do not hardcode colour.** Every colour, face and metric comes from CSS custom properties
on the widget root, with the values below as the built-in defaults. The host supplies
overrides; the widget never needs to know which mode it's in.

### Token names

Adopt these names verbatim. The host already uses them, so `?theme=inherit` can copy them
straight across with no translation layer.

| Token | Role in the widget |
|---|---|
| `--pine` | Page ground. The widget's outermost background |
| `--granite` | Raised surface (only if one is genuinely needed — see §4.1) |
| `--granite-hi` | Input fields, inset wells, meter troughs |
| `--alpenglow` | Primary text |
| `--sage` | Secondary text |
| `--sage-dim` | Labels, captions, disabled, meter ticks |
| `--coral` | Primary accent. Live state, DJ identity, primary button fill |
| `--amber` | Data accent. Timers, levels, the caller's identity |
| `--hairline` | Dividers and control borders |
| `--rule` | Heavier rule, used sparingly |
| `--font-mono` | Labels, buttons, timers, all uppercase micro-type |
| `--font-body` | Sentences — transcript, status prose, names |
| `--font-display` | Reserved. The widget probably needs none of it |
| `--control-h` | Height of every button and input |
| `--band-gap` | Gap between controls in a row |

### The three modes

**Dark** (defaults):

```
--pine:#211d19  --granite:#2a2520  --granite-hi:#37302a
--alpenglow:#ece4d2  --sage:#a89e8c  --sage-dim:#7d7466
--coral:#f05a47  --amber:#d9a441
--hairline:rgba(236,228,210,0.12)  --rule:rgba(236,228,210,0.55)
```

**Light** — note the accents *darken*; the dark-mode coral and amber wash out on cream and
fail contrast on light ground. This is not a simple inversion:

```
--pine:#f3efe6  --granite:#faf7f0  --granite-hi:#ebe4d5
--alpenglow:#1a1613  --sage:#6f675c  --sage-dim:#988e80
--coral:#c5302a  --amber:#8a5a00
--hairline:rgba(26,22,19,0.15)  --rule:rgba(26,22,19,0.65)
```

**Station** — the host reads `GET /themes` and dresses itself in the on-air show's palette,
which changes when the show changes. The widget must accept the same map at runtime and
repaint **without reloading**, because a reload during a call drops the call. Today a theme
swap reloads the frame; that has to stop being the mechanism.

Accept the map via `postMessage` from `embed.js`:

```js
{ type: "swtv:theme", tokens: { "--pine": "#…", "--coral": "#…", … } }
```

Apply with `root.style.setProperty(name, value)` for each key. Unknown keys ignored,
missing keys fall through to the defaults. That is the whole implementation — no mode
flag, no stylesheet swap, no reload.

Derive, never invent: if you need a tint, use `color-mix(in srgb, var(--coral) 12%,
transparent)` rather than a new hex. A hardcoded colour is a bug in station mode.

### Fonts

The host loads Archivo and IBM Plex Mono. A cross-origin iframe does **not** inherit them —
the widget must load them itself or the mono labels will silently fall back to Courier.
Ship the same two families and keep the same fallback stacks.

---

## 3. Global rules

- **Border radius: `0`.** Everywhere. Panel, chips, inputs, buttons, avatar frame.
- **No shadows** except a live-state bloom (`0 0 18px rgba(240,90,71,0.38)`), matching the
  host's playing button.
- **Separate with hairlines, not with cards.** `1px solid var(--hairline)`, or
  `1px dashed var(--hairline)` between stacked bands.
- **Micro-type spec** — every label, button and timer: `var(--font-mono)`, 9.5–11px, 700,
  `letter-spacing: 0.16em`, `text-transform: uppercase`.
- **Prose spec** — transcript, status sentences, names: `var(--font-body)`, 13.5px,
  `line-height: 1.5`, sentence case.
- **Numbers** that tick get `font-variant-numeric: tabular-nums` so they don't jitter.
- **Every control is `var(--control-h)` tall.** No exceptions.
- Honour `prefers-reduced-motion` — kill all animation under it.

---

## 4. Element by element

Working from the opened in-call card.

### 4.1 The panel

Drop it. Remove the near-black card background and the radius; let the host column's own
tint show through. The widget's outer element should be transparent with `padding: 0`, and
the host provides the inset. If a raised surface is genuinely needed for one sub-block, use
`--granite` — but the default is no card.

### 4.2 Header — `● ON AIR NOW` + `?`

Closest thing to right already. Keep: coral pip, coral micro-type. Pip is a 7px square
(**not** a circle) to match the host. The `?` becomes a mono `?` in `--sage-dim`, no
circle, hover to `--amber`.

### 4.3 DJ identity — avatar, name, show

- Avatar: square, `1px solid var(--hairline)`, no border-radius. 44px.
- Name: `var(--font-body)`, 17px, 600, `--alpenglow`.
- Show line: micro-type in `--sage-dim`. Separator `·` as now.

### 4.4 Status chips — `ON THE LINE` / `SPEAKING` / `0:14 / 5:00`

Square, `1px solid var(--hairline)`, transparent fill, `--control-h` tall, micro-type.
Colour carries state, fill never does:

- idle/neutral → text `--sage-dim`, border `--hairline`
- connected → text and border `--coral`
- speaking → text and border `--coral`, plus the pip animating
- timer → text `--amber`, tabular numerals, border `--hairline`

The green currently used for `ON THE LINE` is not in the palette. Use `--coral` for live
states; if a distinct "good" colour is genuinely required, it must come in as a token, not
a literal.

### 4.5 Connection line

Micro-type is wrong here — it's a sentence. `var(--font-body)`, 12.5px, `--sage`, with the
state dot as a 7px square in `--coral` (live) or `--sage-dim` (idle).

### 4.6 Level meters — `YOU` / `DJ`

Labels are micro-type. Replace the dashed placeholder with a 3px trough in `--hairline` and
a fill that matches the speaker's identity colour (§5). No radius, no gradient.

### 4.7 Buttons — two treatments, not four

**Primary** (`Call the DJ`, `Hang up`, `Enter the code` — whichever is *the* action in that
state, and there is only ever one):

```css
font: 700 11px/1 var(--font-mono);
letter-spacing: .14em; text-transform: uppercase;
height: var(--control-h); padding: 0 18px;
background: var(--coral); color: #fff;
border: 1px solid var(--coral); border-radius: 0;
```

**Secondary** (`Mute`, `Unlock the line`, anything else) — same box, ghosted:

```css
background: transparent; color: var(--coral);
border: 1px solid var(--coral);
```

Rows of buttons use `gap: var(--band-gap)`.

One caution learned on the host page: a flex button is floored at its own min-content
width, so a label that changes length (`Call the DJ` → `Hang up` → `Reconnecting…`) will
silently push its row wider. If buttons sit in a fixed grid, give them `min-width: 0` and
`overflow: hidden` so a long label clips instead of moving the layout.

### 4.8 Access code input

```css
height: var(--control-h); border-radius: 0;
background: var(--granite-hi);
border: 1px solid var(--hairline);
color: var(--alpenglow); font: 13.5px var(--font-body);
```

Focus: `border-color: var(--amber)`, no glow, no outline offset. Placeholder in
`--sage-dim` at 11.5px — smaller than the typed text, so long prompts fit.

---

## 5. The transcript — the part that actually matters

### The problem

Each new line grows the block, the widget reports a taller height, `embed.js` applies it,
and the host frame jumps mid-call. It moves while someone is reading it.

### The fix: reserve the space

Give the caption area a **fixed two-line height** and clamp overflow. The block never
changes size, so the reported height stops changing during a call, so the frame stops
moving. This is the single highest-value change in this document.

```css
.transcript-line {
  min-height: calc(1.5em * 2);        /* two lines, always */
  font: 13.5px/1.5 var(--font-body);
}
.transcript-line .said {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  white-space: normal;
  overflow: hidden;
}
```

Two traps, both hit on the host page:

- `visibility: hidden` on the container is **not** enough. If the children are
  `display: none`, the row inside still collapses and the reserved height is only padding.
  The inner row needs its own `min-height`.
- Reserve on the element that holds the text, not an ancestor, or a clamped 1-line message
  still collapses the block.

### Fade in from below

Same treatment the host uses for track changes — the line rises half its own height as it
fades in, so a change registers as movement rather than as text that was simply different
when you looked back.

```css
@keyframes roll-in {
  from { opacity: 0; transform: translateY(0.5em); }
  to   { opacity: 1; transform: translateY(0); }
}
.roll { animation: roll-in .42s cubic-bezier(.22,.7,.3,1) both; }
```

```js
function rollIn(el) {
  el.classList.remove("roll");
  void el.offsetWidth;          // reflow — without it a second change won't replay
  el.classList.add("roll");
}
function setLine(el, text) {
  if (el.textContent === text) return;   // only animate real changes
  el.textContent = text;
  rollIn(el);
}
```

The `void el.offsetWidth` is load-bearing. Without the forced reflow the class is already
present on the second update and nothing moves.

Guard it in the reduced-motion block:

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
```

### Two speakers, two colours

Identity is carried by the **label**, and reinforced — not replaced — by a text tint, so it
stays readable:

| | Label | Text | Meter fill |
|---|---|---|---|
| DJ | `--coral` | `--alpenglow` | `--coral` |
| Caller | `--amber` | `--sage` | `--amber` |

Labels are micro-type (`DJ`, `YOU`), sitting in a fixed gutter to the left of the line so
both speakers' text starts on the same edge — an 86px gutter is what the host uses. The
text itself is sentence case at one size for both; only the label and tint differ.

### What may still grow

Reserving the transcript does **not** mean the widget's height is frozen. States that
genuinely need more room — door-code entry, mic permission warning, an error — should still
grow the frame and report the new height. The rule is narrower than "never resize": the
frame must not resize *per line of speech*.

---

## 6. Layout metrics

- Every control and input: `var(--control-h)` (31px).
- Gap between controls in a row: `var(--band-gap)` (12px).
- The header band, if the widget keeps one, is 38px so it sits level with the host's own
  caption row across the seam.
- Stack sections as bands separated by `1px dashed var(--hairline)`, full-bleed to the
  widget's edges — not as nested cards with their own margins.

---

## 7. Do not break these

1. **Never set a fixed or maximum height on the widget root or the frame.** The host sizes
   the iframe with `min-height: 100%`, which fills the column when the widget is short and
   gets out of the way when it needs more. Capping it makes the in-call view scroll inside
   its own frame.
2. **Keep reporting height to `embed.js` on every state change.** Reserving the transcript
   makes that report *stable*; it doesn't make it unnecessary.
3. **Station theming must repaint without reloading the frame.** A reload drops an active
   call.
4. **The widget must load its own fonts.** Cross-origin frames inherit nothing.
5. **Contrast:** white on `--coral` is 3.4:1 in dark mode — under AA for 11px bold. It's a
   deliberate choice on the host for the primary button only. Don't extend it to secondary
   controls or body text, and don't use `--sage-dim` on `--granite-hi` for anything a
   caller has to read.

---

## 8. Acceptance checklist

- [ ] No `border-radius` anywhere except an explicit, justified exception.
- [ ] No literal colour values in `style.css` — every one resolves from a token.
- [ ] All three modes render correctly, and station mode repaints **without** a reload.
- [ ] Widget height does not change when a new transcript line arrives — measure the
      reported height across ten lines and confirm it's constant.
- [ ] A one-line utterance and a three-line utterance produce the same block height.
- [ ] New lines fade in from below; nothing animates under `prefers-reduced-motion`.
- [ ] DJ and caller are distinguishable at a glance without reading the labels.
- [ ] Exactly one primary (filled) button visible in any state.
- [ ] Every button and input measures `--control-h` tall.
- [ ] Longest button label doesn't widen its row.
- [ ] Mono labels render in IBM Plex Mono, not Courier.
