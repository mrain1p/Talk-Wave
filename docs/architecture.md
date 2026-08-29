# Architecture and invariants

The committed home for the cross-cutting rules that hold across Talk Wave — the
ones a code review checks a change against. Per-file and per-subsystem detail
lives in [agent-worker/CLAUDE.md](../agent-worker/CLAUDE.md) and
[web-widget/CLAUDE.md](../web-widget/CLAUDE.md); this page is the layer above
them: what the pieces are, how they may depend on each other, and the promises
that must survive any change. (The operator also keeps a private, machine-local
root `CLAUDE.md` for gotchas that have cost time — that one is deliberately not
in the repo; the invariants a contributor needs live here instead.)

Back to the [README](../README.md).

## One image, two processes, shared state

Talk Wave is a single Docker image run as two containers off one shared
on-disk `data/` directory:

- **the worker** — the LiveKit job handler (`main.py`): answers calls, runs the
  DJ, records transcripts.
- **the web/token server** — `token_server.py`: the HTTP edge that mints join
  tokens, serves the widget, and carries the settings/diagnostics API.

A settings change written by the web process must reach the worker, so both
mount the same `data/`, and config is re-read per call rather than cached at
start. The two processes never call each other in memory; they meet only
through `data/` and through LiveKit.

## The layers, top to bottom

An importer may only depend on a layer **below** it. This is enforced
mechanically (`TestTheImportLayeringHolds`), with a short list of sanctioned,
mostly-deferred exceptions carrying their reason.

1. **entrypoints** — `token_server.py` (the one routing table), `main.py` (the
   worker). Imported by nothing.
2. **api/** — the HTTP surface, one module per job. `wire` (CORS + who a request
   is from), `auth` (the ADMIN and GUEST gates, and the only code that sees the
   password), `tokens` (minting + usage ceilings), `settings`, `credentials`,
   `widget` (static files + frame headers), `diagnostics`, `hooks`, `live`.
3. **surfaces** — `chat/`, `openlines/`, `voicemail/`: the non-call mouths and
   features that sit on top of the call machinery.
4. **call/tools/** and **call/** — the live call: the session object, its
   behaviours, and the allowlisted DJ tool surface. These two are a
   co-recursive pair (the session builds the tools; the tools reach the
   session's helpers).
5. **brain/** — system-prompt assembly, from station facts and hand-authored
   rules.
6. **transport** — `onair/`, `voicemail/master`: pure low-level audio transport
   and mastering utilities that depend only on platform.
7. **platform** — `settings`, `station`, `station_config`, `secrets_store`,
   `admin_auth`, `tts_adapter`, `spoken_rules`, `version`, and the other
   top-level modules. Imported by everyone; imports nothing internal upward.

## The invariants

Each is a promise a change must not break. The mechanism that holds it is named
so a reviewer can check the guard still applies.

1. **Settings resolve one field at a time: stored over environment over
   default.** A blank or missing stored value falls through to the layer below
   rather than overriding it with emptiness. — `settings.load()` is the sole
   resolver; `test_settings.py`.

2. **Settings are re-read at the start of every call.** A saved change reaches
   the next caller with no restart, because a session loads config when it
   begins rather than at process start. — `settings.load()` per session in
   `call/session.py` and `chat/session.py`; `test_settings.py`.

3. **API keys are write-only from the browser's point of view.** A stored
   secret's value and even its length never travel back to the browser: status
   reports only set-or-not, the source, and a fixed-width mask. Blank on save
   means "leave it", never "erase it". — `secrets_store.py`;
   `test_secrets_and_auth.py`.

4. **A stored secret never travels to an unsaved host.** A stored key or admin
   password is only ever attached to a request aimed at the host it is already
   configured for; a previewed or draft URL typed into the panel does not
   receive it. — `api/credentials.py` (a rule module) at its call sites;
   `test_secrets_and_auth.py`.

5. **Front-door passwords are salted PBKDF2 hashes, never plaintext.** Both the
   ADMIN and GUEST passwords are stored hashed and compared with a constant-time
   check; admin opens the guest door too, and the guest password must differ
   from admin. — `admin_auth.py`; `test_secrets_and_auth.py`.

6. **The tool allowlist keeps destructive tools off the call line.** The
   station's tool surface is declared once; only served, gate-satisfied tools
   reach the DJ, and the never-served ones are never handed to the model at
   all. — `call/tools/registry.py`; `test_tools_surface.py`.

7. **The caller tier is decided at mint time and fails closed.** Tiers are open
   < guest < admin, each including those below. The tier is set only by the code
   that saw the password and is signed into the room name; the worker reads it
   back and cannot be talked into a higher one mid-call. — `settings.py` tier
   ladder + `api/tokens.py`; `test_http.py`.

8. **Two pages: the call page is frameable, the settings page never is.** The
   caller's page and the operator's page are separate URLs that never load each
   other's scripts. The call page must stay frameable forever (an embed is an
   iframe onto it); the settings page carries frame-deny headers so it cannot be
   embedded. — `api/widget.py`; `test_http.py`.

9. **The routing table lives only in `token_server.build_app`.** Every
   URL-to-handler mapping is registered in one place; no `api/` module registers
   routes of its own, and nothing under `api/` imports the server back. —
   `token_server.py`; `TestTheRoutingTableIsInOnePlace`.

10. **The two mouths share one tool surface.** A live call and the typed text
    line build their DJ tools from the same registry and builders, so a
    permission, allowlist, or per-call-cap change reaches both doors at once. —
    `call/session.py` and `chat/session.py`; `TestTheTwoMouthsShareOneSurface`.

11. **`version.py` is the single build number.** One definition, imported by
    both entrypoints, so a redeploy that recreates one container and not the
    other cannot leave them silently skewed reporting different versions. —
    `version.py`; `test_http.py`, `test_widget.py`.

12. **Every station-changing action leaves a receipt the DJ cannot forge.**
    Anything that changes the station is served by a local wrapper that writes
    its own transcript line, so what happened is never only the DJ's spoken word
    for it, and a refusal is a card the DJ cannot speak over. —
    `call/tools/` local wrappers + the per-call ledger; `test_tools_surface.py`.

13. **The web widget has no build step.** Plain browser JavaScript served as
    static files: no `package.json`, no bundler, no `node_modules`. Every file
    split was done with script tags and one shared global. — served by
    `token_server`; `test_widget.py`.

## The upkeep ledgers

Two mechanical guards keep structure from eroding by accretion, both in
`test_house_rules.py`:

- **The size ledger** (`TestNoFileGrowsWithoutSomebodyDeciding`) — a per-file
  line ceiling; a file over it is a written decision, either EXEMPT (meant to be
  long) or SPLITTING (debt, with a recorded seam that may not grow).
- **The complexity ledger** (`TestNoFunctionGrowsTooComplex`) — the same idea
  per function: cyclomatic complexity has a ceiling, and each function over it
  names the review batch that owns its eventual simplification.

Both are ratchets: a recorded number is its own ceiling, and improving a file or
function is expected to shrink or remove its ledger row.
