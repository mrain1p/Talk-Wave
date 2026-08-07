---
name: wavetalk-release
description: Cut a Wave Talk release — bump APP_VERSION, commit, push, and confirm CI built and published the image before telling the operator to pull. Use when asked to release, ship, publish, cut a version, or move :latest.
---

# Release Wave Talk

Images publish from GitHub Actions to `ghcr.io/mrainone7p/wave-talk`:

| Ref | Tag |
|---|---|
| `main` | `:latest` |
| `dev` | `:dev` |
| `beta` | `:beta` |
| `vX.Y.Z` tag | `:X.Y.Z` |

**Work happens on `dev`, not `main`.** The operator does not want iterative tweaks on main
until a change is ready to publish.

## The motion

1. **Bump `agent-worker/version.py`.** `APP_VERSION` is the only place the number lives; both
   the worker and the token server read it. Keep it in step with the git tag.
2. **Run the suite first** — see the `wavetalk-test` skill. CI runs tests *then* builds, so a
   red suite means no image, and finding that out from Actions wastes ten minutes.
3. **Commit.** Two hard rules:
   - **Use `git commit -F <file>`.** PowerShell here-strings break on embedded double quotes and
     git parses the fragments as pathspecs. This has twice put tags on the wrong commit.
   - **Never add a `Co-Authored-By: Claude` trailer.** The operator rewrote this repo's entire
     history with git-filter-repo to strip it and does not want Claude in the Contributors list.
     This overrides the default commit convention.
   - Subject style: `0.9.69 - the call transcript stops disagreeing with the call`. Lowercase
     prose, version prefix, describes the *effect*.
4. **Push to `dev`.** CI builds `:dev`. The operator points compose at `:dev` on the NAS to test.
5. **When it is good, PR `dev` → `main`.** The suite also runs on those PRs; PRs build no image.
   Merge, and `:latest` moves.
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
