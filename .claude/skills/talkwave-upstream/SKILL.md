---
name: talkwave-upstream
description: Review SUB/WAVE's merged and open PRs for changes Talk Wave should mirror, adapt to, or take advantage of. Use when asked "what's new upstream", "anything to align with", or on a periodic alignment pass. Produces a ranked report; implementation is a separate decision.
---

# Review the station's changes as the companion app

Talk Wave is a companion to SUB/WAVE by design (root CLAUDE.md, "A companion app, on
purpose"), so upstream movement is operationally relevant here in a way it wouldn't be for an
independent app. This skill is the periodic pass that keeps the two aligned. It reads and
reports — changing code is a follow-up the operator picks from the report.

## 1. Pull what moved

```bash
gh pr list -R perminder-klair/subwave --state open --limit 50 --json number,title,updatedAt
gh pr list -R perminder-klair/subwave --state closed --limit 60 --json number,title,mergedAt,state
```

Read bodies only for PRs that touch a surface below (`gh pr view N -R perminder-klair/subwave
--json title,body,files`). Their PR bodies are thorough — usually enough without the diff.
To read station source directly, `gh api repos/perminder-klair/subwave/contents/<path> --jq
.content | base64 -d` — pipe through Python with utf-8 stdout on Windows, cp1252 will choke.

## 2. Know what Talk Wave actually consumes

**MCP is the smaller half.** Most of what the DJ can actually DO — vibe search, banter and
programme beats, running segments, switching the show, liking a track — is admin REST that this
sidecar calls directly, not MCP. A pass that checks only `registry.py` against the station's
tool list will report "all clear" while the action surface has drifted underneath it.

Judge every PR against these surfaces, in this order:

- **REST, read and write** — `agent-worker/station.py` is the whole client. Enumerate what it
  actually calls rather than trusting a list that ages:

  ```bash
  grep -n -oE '"/[a-zA-Z0-9/_.-]+"' agent-worker/station.py | sort -u -t: -k2
  grep -n -E 'f"/' agent-worker/station.py          # the interpolated ones
  ```

  Then pull the station's real route table and diff the two:

  ```bash
  gh api repos/perminder-klair/subwave/contents/controller/src/routes/<mod>.ts --jq .content \
    | python -c "import sys,base64,io; sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace'); print(base64.b64decode(sys.stdin.read()).decode('utf-8'))"
  ```

  `dj.ts`, `library.ts`, `likes.ts`, `request.ts`, `shows.ts`, `public.ts` are where everything
  we touch lives. `station_config.py` adds authed `GET /settings` (persona voices + TTS config).
- **MCP tool surface** — `controller/src/mcp/tools.ts` is the station's one definition; check it
  against `agent-worker/call/tools/registry.py` for renames, new tools, changed arg shapes and
  changed refusal semantics (a 409, a decline body).
- **Mirrored vocabularies** — the LLM provider list (`settings.py` LLM_PROVIDER_KEY /
  MODEL_CHOICES / LLM_PROVIDER_LABELS) must match the station's
  (`controller/src/settings/vocab.ts` LLM_PROVIDERS); same rule for TTS providers, segment
  kinds (`SEGMENTS` in `dj.ts`), takeover bounds (`OVERRIDE_MIN/MAX_MINUTES` in
  `schemas/schedule.ts`, hand-copied into `station.py`), the mood vocabulary, and any concept
  the operator sees named in both panels.
- **Webhooks** — `WANTED_EVENTS` in `agent-worker/api/hook_receiver.py` against
  `WEBHOOK_EVENTS` in `controller/src/schemas/webhook.ts`.

### The check the PR list will not give you

Three of the four findings on the 2026-08-14 pass were invisible in the PR titles and bodies —
they only showed up by reading the handler we call and comparing it field by field with what we
do with the response. Do this every pass, for the endpoints that feed the DJ:

- **Field types.** Read the handler's response shape and check our parser against it. The
  station sends `energy` as `'low'|'medium'|'high'`; `_fmt_track` tested it with `isinstance(…,
  (int, float))` and silently dropped it on every row, with a passing test pinning a float the
  station has never sent. **A green test proves nothing about a field the fixture invented.**
- **New fields on a response we already read.** They arrive with no PR of their own and no
  breakage — just an opportunity going unused, or a warning going unheard (`blockedBy` on every
  `/dj/search` row says a track is never-play, and we were offering those to callers).
- **Fields the API does NOT expose that gate behaviour.** If the station honours a flag our
  catalogue read can't see (`cronOnly`, and `enabled` which it returns but we ignore), the DJ
  can drive past an operator's intent. Ask whether every gate the station applies to its own
  autonomous path is one a caller can bypass through us — the admin-override endpoints
  (`/dj/skill`, `/dj/segment`) deliberately ignore cooldowns, gates and the enabled flag.

### Opportunity sweep

Once a pass, list what the station serves that we never call, and ask what a caller could do
with it. The route table above is the source; today's unused set includes `/library/genres`,
`/library/genres/related`, `/library/liked`, `/library/history`, `/library/blocklist/check`,
`/library/coverage` and `/dj/playlists`. Report them under class 4 with the caller-facing
sentence they'd enable, not as a bare endpoint list.

## 3. Rank findings

1. **Breaks a consumed surface** — shape or path changes on anything in the list above.
2. **Mirror gap** — the station gained config/behaviour the sidecar should read rather than
   re-ask (settings the operator would otherwise enter twice; they WILL drift).
3. **Adapt** — station behaviour changed in a way the DJ's tools should handle better
   (new error semantics, new refusal reasons).
4. **Take advantage** — new endpoints or data the call line could use.
5. **Watch** — open PRs that will land in one of the classes above.

## 4. Report, don't implement

Lead with anything in class 1 (there usually isn't any — the station is disciplined about
public surfaces). One paragraph per finding, naming the upstream PR number and the Talk Wave
file it touches. End with the watch list.

Past decisions live in memory: the parked-items file records what the operator already chose
to defer, and the roadmap file records agreed order. Read them first so the report doesn't
re-litigate; update them after the operator decides. Items already implemented: locca
provider, `/dj/search` paging, the `/lyrics/current` read (all 0.10.47), the blocklist 409
relay (0.10.91), the voice event lifecycle and the `djSpeakClock` mirror (0.10.89–0.10.90).

`/lyrics/current` is the one read we ship that **no released station serves** — it is still
open upstream as #1316. The tool degrades to "no lyrics on file" by design, so it is not a
bug; don't re-report it as one, and do check whether #1316 landed before writing it off.
