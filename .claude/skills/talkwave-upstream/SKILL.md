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

Judge every PR against these surfaces, in this order:

- **REST reads** — `agent-worker/station.py` (`/health`, `/now-playing`, `/dj`, `/personas`,
  `/schedule`, `/state`, `/themes`, `/session`, `/dj/search`, `/request`, `/lyrics/current`) and
  `agent-worker/station_config.py` (`GET /settings`, authed — persona voices + TTS config).
  A changed response shape here is the only thing that can break a deployed sidecar.
- **MCP tool surface** — `agent-worker/call/tools/registry.py` is the allowlist; new or renamed
  station tools matter, and so do changed refusal semantics (a 409, a decline body).
- **Mirrored vocabularies** — the LLM provider list (`settings.py` LLM_PROVIDER_KEY /
  MODEL_CHOICES / LLM_PROVIDER_LABELS) must match the station's
  (`controller/src/settings/vocab.ts` LLM_PROVIDERS); same rule for TTS providers and any
  concept the operator sees named in both panels.
- **Webhooks** — `agent-worker/api/hooks.py` against the station's webhook schema.

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
provider, `/dj/search` paging, the `/lyrics/current` read (all 0.10.47).
