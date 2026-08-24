---
name: talkwave-panel-design
description: The settings panel's design system — section anatomy, row shapes, where help text sits, button families, state colour, and the dashboard's grammar. Read BEFORE adding or moving anything in panel.html, and use as the checklist when the operator says a section "doesn't look clean or consistent". Every rule here is one the operator has already corrected once.
---

# The panel's design system

The operator's bar: **intentional layout, typography and elements chosen for the right
occasion** — their words, after a stretch where sections were "slapped on top of each other".
Every rule below exists because its violation was reported. Don't re-earn them.

## Section anatomy

One section = one `<details class="sec" data-group="...">`. The order on the page comes from
`GROUPS` in settings.py, never from markup position — moving a section means editing the GROUPS
tuple, and its supergroup assignment is the second element of that tuple.

- `summary` = chev + secname + secblurb (filled from schema) + `.tag` (state chip).
- **Super-group bands carry no subtext** (operator: noise). Sections explain themselves.
  Since 0.9.155 the band sits on a darker fill than its sections (mixed toward black, so it
  lands darker in BOTH themes — mixing toward `--text` brightens dark mode).
- **Section tags carry state in colour** via `setTag()`: first word `on/open/always` paints
  green, `off/never/closed/none` dims. Write tags so the first word IS the state.
- A section whose only field moved elsewhere **retires** — don't leave a one-field section
  standing (Call length died this way; Transmission modes retired into the dashboard).

## Row shapes — pick the right one

| Shape | Use for | Help goes |
|---|---|---|
| `.row` | label + one control | **inline right** (`hint inrow`, injected from schema) |
| `.row.narrow` (in `.grid2`) | short numbers | inline right |
| `label.check` | one checkbox | **inline after the label** (`hint inlabel` span) |
| `.prow` (matrix) | per-surface Page/Embed pair | **inside `.plabel`** as `hint inlabel` |
| `.permrow` (permgrid) | tiered permission | sibling `hint wide` (grid rules — do NOT inline) |
| `.testrow` | a run of buttons, or field+buttons on one line | n/a — `.testrow` inputs wear the `.row` field skin |

`.grid2` is a **one-column** grid despite the name — it exists for the row rhythm, not for
columns. Worth knowing before you write a finding about a two-column layout that is not
there (0.98.24 did exactly that).

**Field widths inside a `.row`** (2026-08-24 review): text/password/URL fields stay at
260px — a URL is not more readable at 420. **Selects grow to their content, capped at
420px**, because option labels are "value — consequence" sentences and the closed control
was chopping them mid-word on twenty rows. **Number inputs are 96px everywhere**, narrow
row or not — a box holding "0.8" at 260px was the old look's tell. A control outside any
`.row` (the dashboard's Open Lines box) must put the field skin on explicitly; two bare
native controls shipped unstyled on the cream theme because nothing dressed them.

**A field whose switch is a dashboard card wears the card's own words as its schema
label** ("Live calls", "Voicemail", "Text line"), with the old verb phrase kept in
`alias=` for the finder — the door-state banners quote the label, and "Take live calls"
named a switch no card shows.

**Never** add a static `<p class="hint wide">` under a single field — field help belongs in the
schema (`help=`), which injects it in the right place per shape. Static paragraphs are only for
whole-section prose (a prerequisite, a warning, what a run of buttons does), and ONE per
section — merged, not stacked (the sounds section's three-paragraph pile-up was reported).

## Help text rules

- Labels say what the thing IS, with its noun: "Calls per hour", not "Per hour"; "How many
  transcripts to keep", not "How many to keep".
- Selects: every option label is `value — plain-words consequence`. Dropdown defaults NAME what
  they stand for ("Default — the Exchange set's ring", "Classic tone — synthesized (default)").
- Placeholders on text fields carry the default: `default: in character, no fixed formula`.
- Anything accepting `{station} {dj} {show} {track} {tagline}` says so.
- Comments in help cite behaviour, not mechanism, and true claims only — the docs test and the
  operator both check ("turns every refusal into a message" was an overclaim; it got caught).

## Button families

- `.ctrlcard` — dashboard CONTROLS: card-shaped (the tiles' grid, height and radius), micro
  label / state word / note, colour-edged by state (green open, coral paused). **Post
  immediately**, never through Save — a 37px chip floating over 64px tiles was reported as
  two pages sharing a corner, which is why controls are cards now.
- `.runbtn` — diagnostics headers and toolbar toggles (thumbs, problems filter — all three
  match; a checkbox next to toggle-buttons was reported).
- `.btnquiet` — secondary actions in a testrow.
- plain `button` in a `.testrow` — the section's primary action (Save keys, Stage greetings).
- **A testrow that follows content is the section's FOOTER**: hairline above, breathing room
  (`.row + .testrow` etc.) — buttons drifting after content read as appended.
- Two-step reveal for rare fields: the New-password box exists only after "Change password…" is
  pressed. Copy that pattern for any usually-irrelevant input.
- **Slot cards** (`.slotcard`, the sound board): a moment's card shows what is assigned, ▶ as a
  SIBLING button (buttons cannot nest), press opens one shared `.slotmenu` picker. The hidden
  per-slot inputs stay the real settings; faults (missing file, unplayable beep) paint the
  card coral. A field row that must start hidden needs `[hidden]` re-asserted in CSS — the
  `.row` skin's display beats the UA hidden rule, and six "hidden" URL rows once sat fully
  visible (operator-reported; pinned by TestTheUrlRowsOnlyExistInUrlMode).
- **Per-persona lists** (staged greetings, per-DJ voice effects): a `.soundlist` of `.vmrow`s,
  painted lazily on the section's `toggle`, each row saving IMMEDIATELY on change with a
  result line — a costume or a greeting is a decision about a character, not a form draft.
- The station-admin chip is three-valued: `admin=True` (dies without credentials, coral when
  missing), `admin="optional"` (works without, never coral), absent. `schema_payload` must
  pass "optional" through — `bool()` flattened it once and the panel claimed a hard
  requirement that did not exist.

## Radius & surface scale

**The panel is SQUARE.** The newspaper redesign (`talkwave-panel-spec.md` §2) zeroed
`border-radius` on every panel container, button, chip, input and summary hover, and that
is what ships: the panel block of `style.css` has seven `border-radius: 0` declarations
and one 10px. Add a rounded corner to the panel and it will be the only one on the page.

> This section used to read "three steps, no strays: 8px controls, 10px contained
> surfaces, 12px section bodies" — the scale from BEFORE the redesign, left here after it.
> Anyone following it would have rounded a panel of squares. Checked and corrected
> 2026-08-21; `TestThePanelIsSquare` now holds it.

The 8/10/12 scale is still live on the **call card**, which is a different surface with a
different guide (`HOST-STYLE-GUIDE.md`) and its own `--skin-radius` tokens. Do not carry
panel rules onto the card or card rules onto the panel.

Tables are still surfaces: border, `overflow:hidden`, a `--panel` header band, row hover —
a bare `<table>` in a section was reported as slapped together. Square, like everything
else here.

## Type scale

Eleven distinct pixel sizes live in the panel block, five of them inside 2px of each
other, so "no new font sizes without reason" has not been holding anything up. The sizes
in use are the scale now, and `TestThePanelKeepsItsTypeScale` fails on a twelfth:

`9px` `9.5px` `10.5px` `11px` `11.5px` `12px` `12.5px` `13px` `15px` `17px` `19px`

Reach for one that is already there. If a new size is genuinely right, add it to the test
in the same commit and say why — the point is that it is a decision, not an accident.

## The dashboard

Leads the page since 0.9.153, under its own `.dashband` label; the settings heading + search
share one header row (`.settingshead`) below it, then the full-width `.panelnav` jump menu
(entries wear the quiet button skin and flex to fill each row).

**The picker WRAPS on wide screens** (2026-08-24): thirteen chips outgrew one 1180px row,
and the old hidden-scrollbar slide simply cut the last chip off — Diagnostics was
invisible with nothing saying so. Chips are `flex: 1 1 auto` capped at 320px (a lone chip
on the wrap's last row otherwise becomes a page-wide bar); under 760px the one-row slide
returns. Each chip carries its page's section names as a hover `title`, built from the
schema so it cannot drift.

**Notification tone**: a needs row that reports the box WORKING (a pending release, "N
calls since you were last here") is `info: true` — quiet frame, no coral. Coral in the
needs list means something is wrong; three alarms where one is an FYI trains the operator
to stop reading them.

The three controls live in the **Transmission group** (0.9.155, the operator's own sketch): a
bordered `.transmission` cluster whose micro-label is the only outlined group on the dash —
because these three ACT and everything else reads. The Line spans the top (`grid-column:
1/-1`) wearing a drawn `.switch` (knob right + line-green open, left + coral paused); Live
calls and Voicemail sit under it and, while the line is paused, go `disabled` + dimmed +
**amber** — held, not broken. The `#modeSay` caption reads out the combination.

Below it, **tiles in a strict grid** — `repeat(3, 1fr)`, two columns under 760px,
`grid-auto-rows: 1fr`, every row full, every tile one height. Tiles are glanceable
(value + note + tone class ok/warn/bad), jump somewhere on click, and may carry one image
(the on-air avatar). New status belongs here only if an operator opens the page to check it.
Cards carry their meat in the note line (per-tier can-do counts, failed/thumbs, held
messages) — informative, never forced.

## State & colour

Colour carries state; fill never does (except the one primary). Green = on/ok, dimmed = off,
coral = bad/not-set. `.setchip` (set / not set), `.tag[data-state]`, tile tones — same
vocabulary everywhere. Moot rows (voicemail-only greying the call-button options): opacity +
pointer-events only — **no strikethrough** (read as a rendering fault).

## Typography

Mono uppercase micro-labels (9–11px, letterspaced) for labels-of-things: section names, tags,
chips, meter labels, table headers. Body sans for prose and values. No new font sizes without
reason — the diagnostics viewers were re-cut once because the call list ran its own sizes.

## Hard-won mechanics (violations ship silently)

- A field is FIVE places: FIELDS, SCHEMA, panel markup control, a reader, tests. The panel
  silently skips a field with no matching id — `/talkwave-setting` has the walk.
- `needs=` lists must name **every** qualifying value — the intensity dial vanished for the
  newer effects because its list still named the first three.
- Duplicate ids: `byKind` fills the FIRST match; the second sits empty-looking (the twin
  Signing-off box). Moving a row = move, then verify the old copy is gone.
- CSS braces must balance — one duplicated selector killed every rule after it, silently
  (there's a test now, but don't rely on it to think for you).
- The save overlay appears only after a **trusted** user edit; dash controls post immediately
  and never touch it.
- Verify in the stub (`/talkwave-verify`) — the pane hides, screenshots fail, and the per-DJ
  list paints only when its section opens. Structure-probe with javascript_tool, not eyes.
