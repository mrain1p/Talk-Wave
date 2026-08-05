---
name: wavetalk-setting
description: Add, change or remove a Wave Talk setting. A setting lives in five places and the panel silently skips one that is missing a piece, so it can ship completely unreachable. Use for "add a setting for X", "make X configurable", "expose X in the panel", or when a setting exists but does nothing.
---

# Add a setting

A setting is not one edit. It has shipped **unreachable twice** (`avoid_on_air_overlap`,
`on_air_quiet_secs`) because the panel builds itself from the schema and silently skips any
field with no matching DOM id — no error, no warning, the row just is not there.
`TestPanelMarkup` catches that one now. The rest of this list has no guard, so work through it.

## The five places

1. **`agent-worker/settings.py` → `FIELDS`** — the name, its env var (or `None`), and the
   default. The default is the behaviour for every deployment that never touches it, so it is
   the most consequential line you will write. **A new default must not change what an existing
   deployment does** unless that is the point of the change and you say so — 0.9.61 nearly
   shipped a `front_access` default that would have silently stopped every deployment without a
   guest code from taking calls, and the existing suite is what caught it.

2. **`agent-worker/settings.py` → `SCHEMA`** — `group`, `kind`, `label`, `help`, and optionally
   `needs` and `placeholder`. The `help` is the whole documentation of the setting; write why it
   exists and what to pick, not what it is called.

3. **`web-widget/index.html`** — a control whose `id` is exactly the field name, inside the
   `<details data-group="...">` for its group. `kind` decides the markup: `check` is a
   `div.check` with a checkbox, everything else is a `div.row` with a label and an input/select.
   **Miss this and the setting exists, saves, and is invisible.**

4. **Whatever reads it** — `call/`, `brain/`, `token_server.py`. If nothing reads it, you have
   added a control that does nothing, which is worse than no control.

5. **`test_sidecar.py`** — see below.

## Rules that have been learned the hard way

- **Blank means "fall through", never "empty string".** Precedence is
  `data/settings.json` → environment → `DEFAULTS`, and clearing a field in the panel is how an
  operator goes back to the default. `save()` pops the key rather than storing `""`.
- **0 is a real value and usually is not "off".** `min_endpointing_delay: 0` means "keep the
  SDK's tuned default", `record_keep: 0` means "use the default", `calls_per_hour: 0` means "no
  limit". Decide which, then say it in the `help`, then make the code agree.
- **A setting that replaces another must hide it.** `needs=("greeting", False)` means "only
  while that field is empty". Two controls where one silently wins is the exact shape 0.9.61
  removed from `front_access`.
- **Pairs need `complain()`.** A floor above its own ceiling used to save without complaint.
  Cross-field checks live in `settings._complain_about_pairs`, which is merged against what is
  already stored, because a patch usually carries one half of a pair.
- **Never smuggle a setting through `os.environ`.** `tts_mode` was written into the environment
  by four modules so one function could read it back; in the token server that state is shared
  by every concurrent request. Pass it.

## What to test

- The default, and that it is the one an existing deployment already had.
- Precedence: stored beats env beats default, and blank falls through.
- The behaviour it drives — not that the value round-trips, but that the thing it controls
  actually changes.
- Its partner, if it has one: turning a limit on must not be one edit away from disabling the
  feature. Test both directions.

Run the suite (see the `wavetalk-test` skill). `TestPanelMarkup` will fail if you missed
step 3; nothing will fail if you missed step 4, so check it yourself.

## Removing one

Delete from `FIELDS`, `SCHEMA` and the markup together, and grep for the name across
`agent-worker/` and `web-widget/` before you finish — a reader left behind reads `None` and
usually means the feature quietly stops working rather than erroring.
