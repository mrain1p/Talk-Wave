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
