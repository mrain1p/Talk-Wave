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
  bug** (attribution corrected in the pre-release review): the single queue-cancel
  site (`removal.py`) ended "…do NOT claim it's gone", whose lead ("come out of
  the queue") matched neither `spoken_rules.reads_as_a_refusal`'s "didn't go
  out/through/into" nor its tail — so with a real station error it read as NOT a
  refusal; the pinned "do not claim it worked" tail now matches. Net positive
  (one more site graded, none lost); the two curation "don't" sites were already
  matching via "didn't go through" and were never the gap. The other 9 sites
  (batch queue/album refusals, the two
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

---

## Batch 5 — the brain (2026-08-29)

Mostly **accept-with-reason**: the brain assembles a byte-pinned prompt, and it
already follows the one-source-of-truth rule where strings are genuinely
identical (shared imports for `SPEAK_AS_YOURSELF`/`DOORWAY`/`running_the_call`/
`say_the_true_thing`; the `OFF_LIST` constant read by both the prompt and
`TestNothingAskableGoesUnsaid` — a guard that exists because a two-place
hardcode once drifted). The apparent duplication the recon flagged ("a miss is
not proof", the film-soundtrack guidance) is **deliberate byte-different
paraphrase** in often mutually-exclusive code paths — unifying it would change
the shipped prompt for one path and trip the byte-identity guards. Left as-is.

- **Fixed (the one safe win):** `brain.briefing._is_spoken` → `is_spoken`. It was
  the single genuine cross-package reach past a brain underscore
  (`openlines/quiz.py` imports it). A pure rename of a filter predicate — no
  prompt byte changes — so the booth/quiz coupling is now an intentional public
  surface rather than reaching past an underscore. `_fmt_now_playing`/`_fmt_booth`
  turned out not to be reached cross-package; `CARD_BUDGET`/`OFF_LIST` are the
  intended public constants.

### Kept load-bearing (do NOT "simplify")
- `_tools` complexity (27, ledgered) — each cfg branch gates a distinct
  switch-riding paragraph; a capability taught without its tool gets *mimed*
  (the 2026-08-12 fake-takeover). The branching is the product.
- The ablation machinery (`SECTIONS` + `drop`, `TRUTH_CLAUSES`, `CLOSING_CLAUSES`)
  and `conduct._CLOSING_GROUPS` — the closing split is *derived* from the one
  string, not copied out; "fixing" it into copied strings would reintroduce the
  drift the index approach removes.
- `assemble.build_system_prompt`'s re-resolution — deliberate preview-vs-call
  divergence (the preview resolves at admin tier to show the fullest capability
  set); all three re-resolves are fallbacks the call path never hits.

---

## Batch 6 — chat / onair / openlines / voicemail (2026-08-29)

- **The atomic-JSON-store idiom — 9 sites → one helper.** The same shape (make
  the parent, write JSON through a `.json.tmp` neighbour, best-effort chmod for
  the Synology mode-000 share, then `os.replace`) had grown independently in nine
  write blocks. New platform module `jsonstore.py` owns it (`write_atomic`,
  `read_or`, `store_path`), with the only two axes that genuinely differ — the
  file mode and whether the dir is chmod'd — as parameters. Adopted at all nine:
  openlines/state + premises, voicemail/greetings (×2) + review + deliver
  (0700/0600 private), settings.save (0644, inside its lock), secrets_store
  (0600), voice_effects. A direct test (`TestTheJsonStoreIdiom`) pins the
  atomicity, the mode, the temp name, and read_or's seed behaviour. Full suite
  green — the fold is behaviour-preserving at every site.
- **Deliberately left out of the fold** (the review's per-site call): admin_auth
  — its read must keep absent-vs-corrupt APART (fail-closed: unreadable ⇒ "a
  password IS set"), which `read_or` would erase (a security regression);
  settings._stored's read — its SUCCESS path returns `_migrate(json.load(f))`, so
  `read_or` (which returns the raw dict, unmigrated) would drop the migration a
  loaded store needs; call/record's three
  writes (different temp name + split dir-chmod — a real fold but not
  byte-identical); daylog/stats/prefetch (a leaner no-chmod sub-idiom — folding
  would ADD a chmod they don't have).

### Deferred follow-ons (safe, not done this batch)
- **The "one LLM pass" ×4** — only the *un-tooled single pass* (~7 sites) is truly
  identical; a `stream_reply` primitive in `call/providers.py` would fold those.
  The tooled loops diverge. Worth a focused pass.
- **Two call/ privates crossing into voicemail** — `_query_variants` and
  `_match_show` → public (the Batch-5 `is_spoken` move). Cheap honest-coupling.
- **`settings.data_dir()`** — expose `SETTINGS_PATH.parent` to kill a hand-counted
  `Path(__file__).parent` walk that has miscounted before.
- The **cross-process `data/` seam** (onair/chunks, hush, openlines/state) is
  **accepted** as sound architecture, documented, not a fix.

---

## Batch 7 — the widget (2026-08-29)

The hard constraint: `web-widget/` has **no JS test harness** by design — only
Python text-contract tests (`TestWidgetServerContract`: DOM ids per page, scripts
per page, fetch presence) and the Playwright driver. So a big god-file split has
nothing to catch a regression and is out of scope. Only the provably-safe wins:

- **Deleted dead code.** `olNextUpId()` in `panel.js` — defined, never called
  (reference count = 1, its own definition), and carrying a comment that
  contradicted current server behaviour (it claimed an LRU "next up" rule the
  shelf abandoned for "one at random"). And two grep-proven-dead CSS clusters in
  `panel.css`: the retired first-run `.banner.setup`/`#firstPwMsg` form and the
  retired Open-Lines `.oleyebrow` — both absent from `panel.html` and every
  panel JS.
- **One header builder, not two.** `vmKeyHeaders` and `plKeyHeaders` in `call.js`
  were the same `X-Call-Key` builder written twice (copy-form vs mutate-form);
  all 11 call sites pass a fresh object, so the difference was dead. Collapsed to
  one `keyHeaders` (the copy form, which can never mutate a caller's object).
- Verified: `node --check` (syntax), `test_widget` + the full suite (the DOM-id /
  script / fetch contract), grep-proven-dead for the deletions, and a live driver
  smoke — both pages load, the panel renders its full structure, no JS errors.

### Accepted / deferred (the no-test-harness bar)
- **The god-files stay whole.** `call.js`'s voicemail-studio cluster is
  bidirectionally coupled to the call core (`startTimer`/`setCardMode` in, the
  `vmCall` flag straddling the boundary out) — the CLAUDE.md "coupled both ways"
  pattern; not separable. `panel.js` measured the same. Accept-exempt.
- **Deferred (unprovable without a manual listen/send):** merging
  `playPcm`/`playPcmWithEffect`'s shared PCM-decode prefix (WebAudio, no driver
  coverage, silent-failure risk) and `mmss`/`fmt` (they diverge on fractional
  input, which is untested).
- **Accepted:** both CSS design eras are live (bare-class selectors still resolve
  to real markup); the `callinTheme`/`callinPalette` three-file writers are the
  deliberate "apply the theme before /live arrives" pattern, not a drift to fold.

---

## Pre-release verification (2026-08-29) — before the parity push to :latest

A five-agent adversarial pass over the whole `origin/main..HEAD` diff (0.99.2 →
the 7 review batches), each reading the code to CONFIRM or REFUTE. **Verdict:
GO.** Every high/med regression and security concern was **REFUTED** with
evidence: the settings.py peel is *identity*-identical for all 31 importers,
`guest_door_open` matches both former spellings across the full input space
(0/44 mismatches), the readback split is byte-identical with all routes wired
and the `_mint_info` shared dict intact, no store's file mode changed (secrets
stay 0600, admin_auth's fail-closed absent≠corrupt intact), `watch.py`'s
predicates are char-for-char identical, no auth gate dropped, and the version /
two-container / commit-style / no-JS-toolchain invariants all hold. Suite 32/32,
ruff clean.

Confirmed items were all minor and none a regression from the review:
- **Doc fixes (done):** the grader-gap attribution (it was the queue-cancel
  site, not the two curation sites) and the `_stored` left-out rationale.
- **Pre-existing follow-ups (NOT introduced here; the fold preserved existing
  behaviour):** voicemail draft sidecars are 0644 while delivered messages are
  0600 (a stranger's in-progress transcript is world-readable in a 0755 drafts
  dir) — tighten `review.py` sidecars to 0600; and the shared voicemail dir mode
  tugs between deliver's 0700 and greetings' 0755, so the 0700 hardening on the
  messages dir is non-durable (the files are 0600 regardless). Both are voicemail
  privacy hardening for a follow-up, not release blockers.
- **`jsonstore.read_or`/`store_path`** have no production callers yet — they are
  the API for the deferred read-fold tier; kept.
- **Biggest residual risk:** `web-widget/` has no JS test harness, and this span
  carries real widget changes (0.99.2's CSS split, 0.99.6's call.js on-air
  re-entrancy, Batch 7). Mitigated by the Python text-contract tests, a live
  driver smoke (both pages render, no JS errors), and existing dev exposure — but
  a manual click-through of the panel + a real player-like/voicemail send is the
  one thing worth an operator eyeball post-release.
