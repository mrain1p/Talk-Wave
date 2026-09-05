---
name: talkwave-card-design
description: The call card's design system — the skeleton the three faces share, the tokens each surface answers with, and the CSS traps that make a change look right in one state and broken in four others. Read BEFORE adding or moving anything in web-widget/style.css or the painters in call.js. Use when a face looks wrong, when a band is the wrong size or in the wrong place, or before restyling the phone, the player or the guide.
---

# The card's design system

The sibling of `talkwave-panel-design`: that one owns `panel.html`, this one owns the
CALL CARD — `style.css`, `index.html`, and the painters in `call.js`. Every rule below is
one somebody has already got wrong once, and the cost is always the same shape: the change
looks right in the state you were looking at and is broken in four you were not.

`HOST-STYLE-GUIDE.md` is the older handoff and is superseded in part by its own top box.
This is the working checklist.

## One skeleton, three faces

The phone, the player and the guide wear the same bands so the pager reads as one object
turning rather than three unrelated cards:

    header   --head-h     the label, the count, the corner controls
    hero     --ident-h + --station-h    the SAME on all three, or the swipe jumps
    middle   flex         the ONLY scrolling region on a face
    dock     auto         --granite over a hairline, rows at --control-h
    ribbon   the faces row

The skeleton is written once against custom properties and **each surface answers with its
own values** — the phone block at `max-width: 500px`, the installed app, the 620x544 card
on `:root`, `body.compact` for the embed. Write a token, not a number: a number is right on
one surface and absurd on the other three.

**The card reports ONE height per mode.** Fixed 620x544 on the page, 400 in an embed, the
viewport when installed. Only the designated region scrolls: `#lineBox` on the phone,
`.plpanelbody` on the player, `#guideScroll` on the guide. `TestTheCardIsOneHeightAndStaysThere`
pins it.

## The traps

Six ways a card change goes wrong. Every one of them has shipped.

**1. Your `display` beats the UA's `[hidden]`.** The script hides bands with
`el.hidden = true`, and `[hidden] { display: none }` is specificity (0,1,0) — any rule of
yours that names a class and sets a `display` outranks it. The band then lays out,
occupies its rows, and every probe agrees it is gone. This has now cost a progress rail
drawn as a bare grey rule, a level meter as a row of stubs, an empty booth well, a queue
splitting into three, and a stage drawn straight through the post-call strip.
**Every rule you write that sets a display needs its `[hidden]` twin**, and where a
`:has()` or `[data-mode]` selector claims the box, put `:not([hidden])` on that selector
rather than stacking a heavier guard after it.

**2. An older rule out-specifies your new token.** `style.css` is 5,000 lines with three
media blocks that all name `.who-row`. Before adding a rule,
`grep -n '<selector>' style.css` and read what already claims it — a phone block written
for the centred layout pinned the portrait at 124px, so `--portrait` moved the glow and
nothing else. Same story cost the queue rows a `max-width: 46%`, the queue panel a
`flex: 0 1 auto`, and the well a `width: 100%`.

**3. A `-webkit-line-clamp` element that measures 0px.** Two causes, both measured:
an ancestor is `display: none` (check the ancestor before the element), or the clamped
element is a direct flex item where a block parent would do. Wrap it.

**4. A scrolling flex column must not shrink its children.** `display: flex` is how a
scroller gets `order`; flex items then shrink by default, so growing one COMPRESSES its
siblings and the box never overflows — the region silently stops scrolling.
`> * { flex: 0 0 auto; }`.

**5. A gap sits between two things.** A collapsed-to-zero-width item still takes its
`gap`, so the band after it starts inset and is visibly out of line with everything above
and below. Kill the gap in the state where the item is gone.

**6. Whichever row closes the dock pays its bottom padding** — and which row that is
changes with the mode, because `data-mode` hides rows with `display`. `:last-child` reads
the DOM and cannot answer it.

## Check the change in every state it reaches

A dock change touches five modes; a band change touches three faces, two themes and four
surfaces. Drive them, do not reason about them — `talkwave-verify` has the mechanics.

- **Faces**: phone, player, guide.
- **Modes**: `data-mode` is `idle` / `call` / `chat` / `voicemail` / `vmstudio`, plus the
  post-call strip and the door gate inside the line box.
- **Routes**: off air (cool) and on air (coral) change the stage's border, glow, headline,
  sentence and the CTA's fill.
- **Surfaces**: the page card, the phone at `<=500px`, the installed app, `?compact=1`.
- **Themes**: light and dark, plus a station palette pushed over `swtv:theme`.

## Empty is a state, not an edge case

The card is drawn against a station, and a quiet station has nothing to put in most of it.
Every band needs an answer for empty, and the answer is rarely "hide it": hiding reflows
whatever is under it the moment the first line arrives, which moves the thing somebody is
reading. Reserve the space instead.

Check each of these with the payload actually empty: no queue, no history, no booth line,
no open-lines segment, no cover art the browser can fetch, no listener count, a record the
station is not clocking, and a show name long enough to wrap.

**A poll must not undo a fallback.** `/live` repaints every 20 seconds from the same
payload, so anything that says `el.hidden = false` on the way past will keep un-hiding a
picture that has already failed. Reveal on load, never on paint.

## The vocabulary

Colour is a vocabulary, not a palette — each accent names a side of the line, and a new
element picks by answering *whose is this?*

- `--cool` is the CALLER's side: their door, their private route.
- `--coral` is the STATION's: on air, the DJ's identity, the primary fill.
- `--amber` is time and data: clocks, progress, levels, moods.
- `--ok` means the line is open, and nothing else.
- The greys serve both sides — the theme toggle, the gear.

**No literal colours.** Derive with `color-mix()` from a token; a hex is a bug the moment a
station repaints the widget. **Square corners** except the card's own radius and the 6px
sleeve. **One control height** per surface (`--control-h`), and **never two filled buttons**
in one state.

Type comes from a fixed scale, and `TestThePanelKeepsItsOwnRules::test_the_card_keeps_its_type_scale`
fails the build on a size outside it. Reach for a step that exists; if a new one is genuinely
right, add it there with the reason.

## Before you call it done

- The suite: `cd agent-worker && ../.venv/Scripts/python.exe run_tests.py`. The card's
  invariants are pinned by tests that carry their reasons — read the failure, do not just
  satisfy it.
- Both pages load clean (`/` and `/settings` share `style.css`).
- `prefers-reduced-motion` kills every animation; the sheet's global rule does this only
  for properties you animated with `animation` or `transition`.
