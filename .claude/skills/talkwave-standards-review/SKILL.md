---
name: talkwave-standards-review
description: Review changes against this repo's documented standards and against what the change was supposed to do — the two axes the built-in bug-hunting review does not cover. Use when asked to review a branch, a PR, or "what changed since X" for conformance rather than for bugs.
---

# Standards + spec review

The built-in `/code-review` hunts bugs. This one asks two different questions, both of which
have caught real problems here that a bug hunt would pass over:

1. **Standards** — does this code follow the conventions this repo has actually written down?
2. **Spec** — does it do what it was supposed to do, all of it, and nothing else?

Adapted from [mattpocock/skills](https://github.com/mattpocock/skills).

## 1. Establish the comparison point

Ask for it if it was not given: a commit SHA, branch, tag, or `origin/main`. Then:

```bash
git diff origin/main...HEAD
```

**Three dots, not two** — that diffs against the merge base, so you review what this branch did
rather than everything main has done since. Also read the commit subjects:

```bash
git log --oneline origin/main..HEAD
```

Confirm the ref resolves and the diff is non-empty before going further. An empty diff means the
comparison point is wrong, not that the change is clean.

## 2. The standards axis

The written standards live in `docs/architecture.md` (the cross-cutting invariants and the layer
map), `agent-worker/CLAUDE.md` and `web-widget/CLAUDE.md` (per-subsystem detail). Read the ones
covering the changed files, then check the diff against them. In this repo that means, concretely:

- **Invariants** (`docs/architecture.md`): settings precedence, secrets never making the return
  trip, passwords hashed, the MCP allowlist keeping destructive tools off a call line, settings
  re-read every call, `version.py` as the single source of the build number, the layering order.
- **Test house style**: stdlib only, no network, `_TempStores` for anything writable, class names
  that state the claim they defend.
- **No JS toolchain.** A diff that adds `package.json`, a bundler, or `node_modules` to
  `web-widget/` is a standards violation on its own — say so.
- **Comments explain why, and cite the incident.** This codebase's comments carry the reason a
  line exists ("Went out on a real call:", "this has happened, and it was invisible"). A diff
  that adds comments restating the code beneath them is drifting from house style.
- **Commit style**: `0.9.69 - lowercase prose describing the effect`. **No `Co-Authored-By:
  Claude` trailer** — the history was rewritten once to strip it.

## 3. The spec axis

Get the intent from whatever exists: the issue, the PR body, the operator's request in chat, or
`MASTER-PLAN.md` (gitignored, local only, but it is the definitive architecture plan and has the
phase checklists). Then check three things, in this order:

- **Complete?** Every part of what was asked, not the easy parts.
- **Faithful?** It does what was described, not an adjacent thing that was easier.
- **Bounded?** No opportunistic extra changes riding along unmentioned.

The house bar is **"everything does what it says"** — a feature that silently no-ops is a bug
here, not a nitpick. Look specifically for changes that would fail quietly: a setting the panel
can't reach because no DOM id matches, a tool that reports success without a receipt, a stream
URL that gets blocked as mixed content without an error.

## 4. Report

Group by axis, most severe first. For each finding give the file, the line, the standard or
requirement it misses, and the concrete consequence. Separate **"this violates a written
standard"** from **"I would have done this differently"** — only the first is a finding.

Then, in **two or three plain sentences with no jargon**, say what the change actually does.
The operator asks for this every time; lead with it if the review is otherwise clean.

## When the diff is large

For a diff over roughly 500 lines, run the two axes as separate parallel subagents so neither
contaminates the other's reading, then merge the findings. Below that, do it inline — spawning
agents for a small diff costs more than it returns.
