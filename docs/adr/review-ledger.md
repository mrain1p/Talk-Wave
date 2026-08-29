# Architecture review ledger

A per-batch record of the file-by-file review (the maintainability plan): what
was found, what was fixed, what was accepted with a reason, and what was
deferred. This is the committed home so a decision stops being re-litigated
every time someone opens the file. Verdicts: **sound / peel-a-seam / refactor /
accept-with-reason**. The binding rule throughout: one source of truth beats a
split — no change may add a second copy of a fact or a second read-site.

---

## Batch 1 — platform hubs (2026-08-29)

### settings.py — **peel-a-seam → done**
Three unrelated truths cohabited in the highest-blast-radius file (31 importers):
the layered store, the caller-tier security ladder, and ~1,500 lines of panel
presentation data. The security ladder — the code a reviewer must audit for
fail-closed behaviour — was buried between the field table and the UI copy.

- **Fixed:** peeled to two pure leaf modules — `caller_tiers.py` (the tier
  ladder) and `settings_schema.py` (SCHEMA/GROUPS/SUPERGROUPS + provider/vocab
  tables). Re-exported from settings.py so `settings_store.<name>` is
  byte-identical for every caller and test. The resolver functions that read the
  tables stayed in settings.py — moving them would form a settings↔schema cycle,
  so the dependency runs one way (settings → schema → caller_tiers). settings.py
  went 3,169 → ~1,450 lines. The "field metadata" explanation moved with SCHEMA.
- Verified: full suite green, no test assertion changed, no caller edited.

### station.py — **accept-with-reason** (docstring fixed)
- **Fixed:** the module docstring called it a "slim read-only client" whose
  actions "go through MCP" over "public reads, no auth" — none true since the
  admin-gated write wrappers landed. Rewritten to name both halves honestly.
- **Accepted:** the one-class-per-external-service shape is right — renaming the
  ~14 write methods or splitting into read/write classes was **rejected** (churn
  across 31 callers, no gain; the defect was the description).
- **Accepted:** `_AIRED_BY_US` (the said-by-us ledger) is documented shared
  state with a test seam — correct and low-harm as-is.
- **Accepted:** the upstream-mirrored constants (SAY cap, takeover/genre-lock
  bounds) are single sources on our side kept beside their use with an upstream
  citation — **not** hoisted into a shared table (that would be a second
  read-site that drifts). The weekly upstream pass is the drift detector.

### station_config.py — small fixes; one reshape deferred
- **Fixed:** dropped a docstring line for `/api/system` (an endpoint the file
  never reads); cleaned three private-alias re-imports (`_json`/`_os`/`_time` →
  top-level `json`/`os`/`time`).
- **Fixed:** added a regression test pinning that the DJ-model DFS returns the
  DJ model, not a sibling embedding/tagger `model` — the exact silent failure
  `_SKIP_SUBTREES` exists to prevent.
- **Deferred:** reshaping `_extract_persona_voices` to anchor under
  `settings['values'|'personas']` before the DFS. It changes how voices are
  found on a real payload; do it with a live-station payload in the loop (the
  NAS) rather than blind.

### tts_adapter.py — small fixes; discovery peel deferred
- **Fixed:** `load_adapter` now fails loudly if a config is missing
  `endpoint_path` (naming the file), instead of a KeyError deep in the first
  synthesis — a single guard, single source, at the untyped-dict seam the
  report-only type check is blind across.
- **Fixed:** the module docstring now names the discovery half (parse_voice_list
  / available_voices) and the "empty list == could not find out" rule.
- **Deferred:** splitting discovery into `tts_voices.py`. The seam is real and
  recorded in the size ledger, but tts_adapter is not a hub — low urgency.

### Deferred index (carry as their own tickets)
- station_config `_extract_persona_voices` anchor-then-DFS reshape — needs live payload.
- tts_adapter discovery → `tts_voices.py` split.
- station.py `self._admin()` helper to dedupe ~20 credential prologues — optional; only if station.py is touched again.
- station_config `_walk` consolidation — **rejected**: three genuinely different yield types make it over-abstraction.

---

## Batch 2 — the api edge (2026-08-29)

### api/diagnostics.py — **peel-a-seam → done**
Two jobs cohabited: the `/test/*` probes ("does the configuration work") and the
`/calls` + `/logs` readback ("what already happened").

- **Fixed:** split the 6 readback handlers (`handle_calls`, `handle_clear_calls`,
  `handle_delete_call`, `handle_mark_call`, `handle_logs`, `handle_clear_logs`)
  into a new **`api/readback.py`**. diagnostics.py went 1,583 → 1,437; readback
  is 170. The routing table (`token_server.build_app`) now imports the 6 from
  readback. They share only the admin gate and CORS edge with the probes, so the
  seam is clean. Two dead imports (`time`, and `_mint_info` once its readers
  left) removed from diagnostics.

### The door/tier rule — **duplication → consolidated**
The guest-door rule (whether the guest tier is reachable, given front_access
mode + a guest code + `guest_tier`) was spelled out in **two** places —
`auth.caller_tier` and the `/live` card — and the code's own comment warned that
"a fourth spelling of it is how the card and the panel disagreed by accident".

- **Fixed:** one spelling now — `caller_tiers.guest_door_open(front_access,
  guest_is_set, guest_tier)`, re-exported through `settings_store` like the other
  tier functions. Both sites call it; the truth table is pinned by
  `TestTheGuestDoorRuleHasOneSpelling`. The recon's "5 copies" was an overcount:
  the other tier/door consultations already route through `caller_tier()`, so
  there was one rule in two spellings, not five.

Deferred to later batches (their own findings): the `/live` payload god-dict
split (Batch 2 file, but the payload assembly is its own large job), the
diagnostics probe helpers' shared error-shaping, and the several api/ modules'
in-memory usage-ledger duplication — none blocking, recorded for when those
files are the subject.

---

## Batch 3 — the call core (2026-08-29)

The theme was **consolidation**, not god-object surgery: the two big objects
(`session.py`, `air.py`) are timing-sensitive and their correctness lives in
ordering comments, so their splits are deferred. What shipped removes drift
surfaces and dead code.

- **`call/watch.py` — the event-unwrap has one home.** "A committed DJ line is
  an assistant item's stripped `text_content`" was written four times (door,
  arc, state, comeback) and the caller-line unwrap twice (floor, asks). It now
  lives once (`dj_line`/`caller_line`/`on_dj_line`/`on_caller_line`); the four
  live watchers delegate, keeping their `attach_*` names and the session.py
  wiring byte-identical. Two dead functions (`attach_door_watch`,
  `attach_arc_watch` — orphaned when `state.py`'s consolidated watcher landed)
  were deleted. **Kept OUT deliberately** (they diverge and would silently
  break): `lifecycle.attach_card_flush` (fires on an empty assistant item),
  `clocks` idle (resets on partial transcripts), `promise_guard._on_caller`
  (resets on an empty final), `think_pace` (reads metrics, not text). Folding
  those needs per-handler predicates and their own proof — a deliberate
  follow-on, not this batch's clean win.
- **`AirVerdict._spoken_secs` — 3 copies → 1.** `speaking_secs(text,
  int(self.quiet_secs) or 30)` was inline at three verdict sites; one method
  now. It dropped `_push_verdict` under the complexity ceiling, so its ledger
  row was removed in the same commit.
- **`onair.relay.on_air_window_secs(cfg)` — one home for the 240s window.** The
  DJ's promised on-air window and the relay's enforced deadline are the SAME
  number, written inline three times; now one helper. The session prompt casts
  it to `int` so its "About N minute(s)" display is unchanged.
- **A pin for the hush-marker owner.** `TestTheHushMarkerHasAnOwnerEvenWhenStartRaised`
  fixes the exactly-once removal correctness that had lived only in `_started`'s
  comments.

### Deferred (with reason)
- **session.py god-object split** — the file is long-but-FLAT (breadth of
  wiring, not depth), and correctness is ordering across concurrent shutdown
  callbacks. A split relocates coupling rather than reducing it; revisit only
  after the ordering is test-pinned. The size ledger keeps ratcheting it down as
  features consolidate.
- **air.py / clocks.py god-objects** — incident-scarred, timing-sensitive (the
  duck, the idle watch). Seams recorded; do not cut without a live-call harness.
- **record.py builder/store split + the `_record_path`/`_find_by_room` path
  consolidation** — genuinely safe but its own focused pass; deferred to keep
  this batch to the call-guard consolidation.
- **The divergent watchers** (above) — need per-handler predicates before they
  can share the `watch.py` plumbing.

---

## Batch 4 — the call tools (2026-08-29)

- **The refusal-card idiom — 14 sites → one method.** Every station-refusal site
  read the station's reason TWICE (once for the `denied()` card, once inside the
  return string) and had drifted on the tail ("don't" vs "do not"). They now
  call `CallActions.station_refused(result, said)`, which reads the reason once,
  cards once, and returns the pinned house prose; each site passes only its own
  lead ("That didn't go out", "That segment didn't run"). **This fixed a latent
  bug**: two curation sites spelled "don't", which `spoken_rules.reads_as_a_refusal`
  and the refusals ablation do NOT recognise — so their refusals weren't being
  graded as refusals. The other 9 sites (batch queue/album refusals, the two
  "unavailable" capability gaps, and the request-song rate-gate relay) have
  genuinely different shapes and stay as they are. Two tests updated: the
  speech-filter grader pin now reads the phrase from its ONE home (`call.actions`),
  and a cancel test matches the normalized tail.
- **albums.py stops being a utility hub.** The three generic helpers
  (`_txt`, `_squash`, `_BATCH_BUDGET_SECS`) that `removal.py` and `shows.py`
  reached into `albums.py` for moved to `rows.py` — the pure-leaf formatter home
  its own docstring already describes ("two of them were importing the tool
  module purely to borrow a formatter"). albums/removal/shows now import them
  from rows.

### Deferred / rejected
- **Tool availability derived in two places** — **rejected** as a consolidation:
  the registry (declarative catalogue) and each builder's imperative gate are
  deliberate belt-and-braces, reconciled by `test_tools_surface.py`. Documented
  as intentional; not a drift to fix.
- **CallActions dead getattr-defaults** (2 sites) and **reads.py import hygiene**
  — low-value polish, deferred; the untyped-bus reads that have real `__init__`
  defaults are already fine.
