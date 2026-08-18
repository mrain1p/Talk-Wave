---
name: talkwave-release
description: Cut a Talk Wave release — bump APP_VERSION, commit, push, and confirm CI built and published the image before telling the operator to pull. Use when asked to release, ship, publish, cut a version, or move :latest.
---

# Release Talk Wave

Images publish from GitHub Actions to `ghcr.io/mrain1p/talk-wave`:

| Ref | Tag |
|---|---|
| `main` | `:latest` |
| `dev` | `:dev` |
| `beta` | `:beta` |
| `vX.Y.Z` tag | `:X.Y.Z` |

**Work happens on the operator's active lane — `beta` since 0.97.50 — never directly on
`main`.** The operator does not want iterative tweaks on main until a change is ready to
publish. `dev` still exists and builds `:dev`, but the NAS compose has run `:beta` since the
caller-on-air stream; ask which lane is live before assuming, and check `git log` — the lane
with today's commits is the active one.

## When to cut one, and what to call it

**A new heading in `CHANGELOG.md` IS a release.** Write the heading, cut the tag at that
version, publish the notes — one act, not three. The rule exists because the alternative was
measured: on 2026-08-15 the newest release was fourteen builds and twenty commits behind
`:latest`, and everything an operator would want to read about was in the image and nowhere
else. Tagging when a batch "feels finished" is what produced that gap; a heading is a decision
somebody already made, so it cannot drift.

**The number reads as distance from 1.0.** The series restarted at **0.97.0** on 2026-08-15,
from 0.10.159 — the operator's ask, "close to a 1.0 but not quite there". 0.98 and 0.99 are in
hand; 1.0.0 is a deliberate call, not an accident of counting. Patch within the series
(0.97.1, 0.97.2…) for ordinary work.

**It only ever goes UP.** Everything that compares two versions — the panel's update flag,
the container-skew check — parses them part by part, so 0.9.7 would sort *below* 0.10.159 and
every deployment would report an update that was really a downgrade. That is why the restart
went to 97 and not to 9.

## The motion

1. **Bump `agent-worker/version.py`.** `APP_VERSION` is the only place the number lives; both
   the worker and the token server read it. Keep it in step with the git tag.
2. **Run the suite first** — see the `talkwave-test` skill. CI runs tests *then* builds, so a
   red suite means no image, and finding that out from Actions wastes ten minutes.
3. **Commit.** Two hard rules:
   - **Use `git commit -F <file>`.** PowerShell here-strings break on embedded double quotes and
     git parses the fragments as pathspecs. This has twice put tags on the wrong commit.
   - **Never add a `Co-Authored-By: Claude` trailer.** The operator rewrote this repo's entire
     history with git-filter-repo to strip it and does not want Claude in the Contributors list.
     This overrides the default commit convention.
   - Subject style: `0.9.69 - the call transcript stops disagreeing with the call`. Lowercase
     prose, version prefix, describes the *effect*.
4. **Push to the active lane** (`beta` today). CI builds that lane's tag; the NAS compose
   points at it.
5. **When it is good, PR the lane → `main`.** The suite also runs on those PRs; PRs build no
   image. Merge, and `:latest` moves. Keep the other lanes fast-forwarded after the merge so
   "are these in sync?" stays answerable at a glance.
6. **Tag and publish the GitHub Release** — merging alone leaves the Releases page stale,
   which sat on v0.9.45 for 85 versions before anyone noticed. The notes are the version's
   CHANGELOG.md entry (write that entry BEFORE the merge — high level, grouped, no laundry
   list, no AI commentary), extracted verbatim:

   ```bash
   git tag vX.Y.Z <main merge commit> && git push origin vX.Y.Z
   gh release create vX.Y.Z --title "vX.Y.Z" --latest --notes-file <changelog entry>
   ```

   The tag push also builds the pinned `:X.Y.Z` image — wait for that run too before
   telling the operator the version exists to pin against.

## Before telling the operator to pull

Check **both** CI jobs passed and the GHCR digest actually changed:

```bash
gh run list --branch dev --limit 3
```

```bash
gh run watch
```

A green `test` job with a skipped or failed `build-and-push` means nothing shipped. Do not say
"pull it" until you have seen the new digest — the operator runs `:latest` and a stale pull is
indistinguishable from a broken fix.

## After a pull, check for version skew

The worker and the token server are the **same image in two containers**. A redeploy that
recreates one and not the other leaves them skewed, and it has happened. `/health` reports the
running version — check both, not one.
