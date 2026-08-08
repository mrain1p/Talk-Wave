# Wave Talk

A call-in phone line for a [SUB/WAVE](https://github.com/perminder-klair/subwave) internet
radio station. A listener opens a web widget, presses Call, and talks to the DJ persona that
is currently on air — speech-to-text into an LLM into text-to-speech, over LiveKit WebRTC,
with the station's own MCP tools attached so the DJ can actually do things on air.

This file is the map. Read it before exploring; it is meant to save you the crawl.

## Shape of the system

Two Python processes ship as **one image** (`ghcr.io/mrainone7p/wave-talk`) and run as **two
containers**, plus LiveKit and Caddy:

| Process | Entry | Job |
|---|---|---|
| agent worker | `agent-worker/main.py` | Registers with LiveKit, answers dispatched calls, runs the STT→LLM→TTS session |
| token server | `agent-worker/token_server.py` + `api/` | aiohttp app on :8100. Serves the widget, mints join tokens, hosts the whole settings/admin API |
| livekit-server | `livekit/livekit-server` image | WebRTC media |
| caddy | `caddy:2` | TLS in front of :8100 |

Because both Python processes are the same image, **a redeploy that recreates one and not the
other leaves them version-skewed.** Both log `APP_VERSION` at startup for exactly that reason.

### agent-worker/ — the DJ

- `main.py` — worker entrypoint. Imports LiveKit plugins at module scope on purpose (plugin
  registration must happen on the main thread).
- `call/` — one call, decomposed. `session.py` is the call object (`prepare` → `start` →
  `greet`); `lifecycle.py` is what happens while it runs; `tools/` is what the DJ may do.
- `api/` — the HTTP surface, decomposed the same way: one module per job (`auth.py`,
  `tokens.py`, `live.py`, `sounds.py`, `settings.py`, `diagnostics.py`, `hooks.py`, …).
  `token_server.py` is now only the routing table, and it is the *only* one — a handler
  registered anywhere else is invisible to the widget's contract test.
- `brain/` — assembles the system prompt (`assemble.py`, `briefing.py`, `conduct.py`).
- `station.py` — **read-only** REST client for the SUB/WAVE controller. Reads only.
- `station_config.py` — mirrors the station's own DJ/TTS config so the call-in DJ doesn't drift
  from the on-air one. Falls back to `persona-voices.json` when the station won't say.
- `settings.py`, `secrets_store.py`, `admin_auth.py` — see invariants below.
- `tts_adapter.py` + `tts-adapters/*.json` — pluggable TTS backends.

### web-widget/ — the phone

Plain browser JS, no build step, no toolchain. Two pages that never load each other's script:
`token_server` serves `index.html` at `/` (the phone: `shared.js` + `call.js`) and
`panel.html` at `/settings` (the operator: `shared.js` + `panel.js`). `shared.js` publishes one
global, `Callin`. Script tags, not modules — the split kept the no-bundler promise rather than
trading it away. The panel has its own URL so a reverse proxy can put a rule in front of the
admin surface that it could never put in front of the phone.
`embed.js` is the drop-in `<script>` for third-party pages; it resolves
`?theme=inherit` against the host page before the iframe loads, because a cross-origin frame
can't read the page it sits in.

## A companion app, on purpose

Wave Talk is a companion to [SUB/WAVE](https://github.com/perminder-klair/subwave), and that
is a design constraint, not just a description. When there is a choice about how to build
something the station also has an answer for, **read the station's answer first**
(`gh` against its repo — see the memory notes; `web/components/admin/*/…Meta.ts` and
`controller/src/` are where its provider lists and contracts live) and prefer:

- **Same integrations.** If the station can point at a provider (LLM, TTS, STT, search),
  an operator will hold that key already — offer the same one rather than making them open
  a second account to run one radio station. The LLM list here mirrors the station's
  (OpenAI, Anthropic, Google, DeepSeek, OpenRouter, Requesty, Vercel AI Gateway, Ollama);
  keep it mirrored when the station gains one.
- **Same vocabulary.** Where the station names a concept (personas, skills, segments,
  programme beats, engines), use its name — an operator reads both panels in one sitting.
- **Mirror, don't re-ask.** Anything the station already knows — persona voices, DJ/TTS
  config, themes, cards — is read from it (`station_config.py`, `station.py`) rather than
  configured twice. A setting that exists in both places will drift; a mirrored one can't.
- **Same operational shape.** Compose-first deployment, settings live in a UI backed by a
  JSON file with env as the 12-factor override, keys entered in a form and stored
  server-side — the station's conventions, kept here so one operator runs one mental model.

The boundary still holds: mirroring is **read-only** (invariant 1 below), and this sidecar
never writes station state except through the allowlisted MCP tools.

## Invariants — break these and something real breaks

1. **Call actions go through the station's MCP server, not `station.py`.** MCP already exposes
   them as tool-ready definitions. The MCP surface is filtered by an **allowlist**: a caller is
   an untrusted stranger driving the agent by voice, and the station's tools include destructive
   ones (`skip_track`, `play_sfx`, `queue_track`, `dj_segment`, `refresh_playlist`). Those are
   never on a call line.
2. **Settings precedence is `data/settings.json` → environment → `DEFAULTS`.** Clearing a field
   in the UI means "fall through to the layer below", not "set empty string".
3. **Secrets never make the return trip.** `secrets_store.status()` returns whether a key is set
   plus its last four characters — never the key. Blank on save means *leave unchanged*, not
   *clear*; the UI shows masked placeholders, so an untouched field arrives empty.
4. **Passwords are PBKDF2 hashes, never plaintext.** Two levels: ADMIN (settings, keys, test
   endpoints) and GUEST (the Call button and `/token`). Admin implies guest; the two must differ.
   Break-glass is `CALLIN_ADMIN_KEY` in the environment, or delete `data/admin-auth.json`.
5. **Settings are re-read at the start of every call**, so changes take effect on the next caller
   without restarting the worker. Don't cache them across calls.
6. **`agent-worker/version.py` is the only place the build number lives.** Bump it in step with
   the git tag.

## Commands

Run the tests (this is what CI runs, and nothing reaches `:latest` without it passing):

```bash
cd agent-worker && LOG_TO_FILE=0 SETTINGS_PATH=/tmp/t.json SECRETS_PATH=/tmp/s.json ADMIN_AUTH_PATH=/tmp/a.json CALLS_PATH=/tmp/calls python -m unittest test_sidecar -v
```

Those env vars are not optional — they point every writable path away from the checkout so a
test can never scribble on your real settings, secrets or auth files.

Local stack on Windows, no Docker (needs `.venv`, `.env`, and `bin/livekit-server.exe`):

```bash
./run-local.ps1
```

Deployed stack:

```bash
docker compose up -d
```

## Gotchas that have actually cost time

- `livekit-server` needs `--node-ip ${HOST_IP}`. Without a real host IP, media never connects
  while signalling looks perfectly healthy.
- Since 0.9.65 the container **does not run as root**. Files written into `data/` set their own
  permissions; a `data/` created by an older root container will not be writable after upgrade.
- `data/`, `.env`, `livekit.yaml` and `persona-voices.json` are gitignored — they hold real keys.
  `.example` files are the tracked templates.
- The repo has long-form design docs (`MASTER-PLAN.md`, `AUDIT.md`, `REVIEW.md`,
  `RELEASE-REVIEW-1.0.md`, `BUILD-INSTRUCTIONS.md`) that are **gitignored working notes** — they
  exist on the operator's machine, not in a fresh clone. `README.md` is the tracked one.

## Conventions

Comments in this codebase explain **why**, and frequently cite the incident that motivated the
code ("this has happened, and it was invisible"). Match that. Don't add comments that restate
the line beneath them. Wrap comment prose at ~100 columns, not 72 — the operator reads these
files, and narrow wrapping doubled their length for nothing.

Commit subjects are lowercase prose describing the effect, prefixed with the version:
`0.9.69 - the call transcript stops disagreeing with the call`.

Two rules for committing here, both learned the hard way:

- **Never add a `Co-Authored-By: Claude` trailer.** This repo's entire history was rewritten with
  git-filter-repo to strip it; the operator does not want Claude in the Contributors list. This
  overrides any default commit convention.
- **Use `git commit -F <file>`.** PowerShell here-strings break on embedded double quotes and git
  then parses the fragments as pathspecs — which has twice put a tag on the wrong commit.

Work happens on the `dev` branch, not `main`. `main` is what `:latest` ships from.

### How long a file may be

**600 lines**, for anything under `agent-worker/` or `web-widget/`. Not because short files
are virtuous — because nothing here ever objected to a file getting longer, and the widget's
old single app file reached 3,354 lines and `test_sidecar.py` 5,791 one entirely reasonable
commit at a time.

Above the ceiling is allowed, but it has to be a decision somebody wrote down, in one of two
lists in `TestNoFileGrowsWithoutSomebodyDeciding`:

- **`EXEMPT`** — this file is *meant* to be long, and that is the right answer. Declaration
  tables are the clear case: `settings.py` gains a few lines every time the station gains a
  setting, and that ordinary act should not have to come and edit a test. Exempt files are not
  measured, only justified.
- **`SPLITTING`** — debt: too long, known, being dealt with. These are ratcheted. The recorded
  number is the size when the entry was written; shrink freely, but growing past it fails until
  you either split the file or raise the number and say why in the commit.

Either way, an entry whose file drops back under the ceiling must be deleted — the lists are not
allowed to describe a repo that no longer exists. And being long has to stay a legitimate
permanent answer, or the ceiling starts pushing toward a file per function, which is worse than
the problem it solves.

## How this is enforced

Three layers, in increasing order of "actually happens":

1. **This file** is loaded into context every session. Advice — read and generally followed.
2. **`.claude/skills/`** — `wavetalk-verify`, `-deploy`, `-release`, `-test`, `-diagnose`,
   `-llm-bench`, `-standards-review`. Invoked when the model judges them relevant.
3. **`.claude/settings.json`** — two `PreToolUse` hooks on `git commit`, both executed by the
   harness so they hold whether or not anyone remembered, and both failing open (no python, no
   repo, no test file, or a non-commit command and they exit silently):
   - **the suite must be green** — denies the commit if `test_sidecar` is red. Adds ~55s.
   - **shipped code must bump the version** — denies a commit that touches `agent-worker/` or
     `web-widget/` without staging `agent-worker/version.py`. Three commits in one afternoon
     shipped as duplicate version numbers, in exactly the situation `version.py` exists to
     disambiguate; leaving it to memory demonstrably does not work.

If the hook ever needs to be bypassed, edit or remove it in `.claude/settings.json` — don't
work around it, since CI runs the same suite and will refuse to build the image anyway.
