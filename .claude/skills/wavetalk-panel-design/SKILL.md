---
name: wavetalk-panel-design
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

Three steps, no strays: **8px** controls (buttons, inputs, selects, summary hover),
**10px** contained surfaces (tiles, control cards, banners, results, tables, snippets),
**12px** section bodies. Tables are surfaces: border, radius, `overflow:hidden`, a
`--panel` header band, row hover — a bare `<table>` in a section was reported as
slapped together.

## The dashboard

Leads the page since 0.9.153, under its own `.dashband` label; the settings heading + search
share one header row (`.settingshead`) below it, then the full-width `.panelnav` jump menu
(entries wear the quiet button skin and flex to fill each row).

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
  silently skips a field with no matching id — `/wavetalk-setting` has the walk.
- `needs=` lists must name **every** qualifying value — the intensity dial vanished for the
  newer effects because its list still named the first three.
- Duplicate ids: `byKind` fills the FIRST match; the second sits empty-looking (the twin
  Signing-off box). Moving a row = move, then verify the old copy is gone.
- CSS braces must balance — one duplicated selector killed every rule after it, silently
  (there's a test now, but don't rely on it to think for you).
- The save overlay appears only after a **trusted** user edit; dash controls post immediately
  and never touch it.
- Verify in the stub (`/wavetalk-verify`) — the pane hides, screenshots fail, and the per-DJ
  list paints only when its section opens. Structure-probe with javascript_tool, not eyes.
