---
name: branch-align
description: >-
  Align branch name with artifact ID when they mismatch. Use when: renumbering
  a REQ/DR/ADR after branch creation, "branch name is wrong", "rename branch
  to match", or when PR review flags branch/artifact mismatch. Handles the git
  branch rename and remote update.
argument-hint: "[new-branch-name]"
user-invocable: true
metadata:
  author: APME Team
  version: 1.0.0
---

# Branch Align

Rename a git branch to match its artifact ID after renumbering.

## Why This Skill Exists

During PR review, artifact IDs sometimes get renumbered (e.g., REQ-005 → REQ-011 to avoid conflicts). The branch name then mismatches the artifact, causing confusion:

```
Branch: docs/req-005-aa-deprecated-reporting
Artifact: REQ-011-aa-deprecated-reporting
```

This skill renames the branch locally and on the remote fork.

## Arguments

- `[new-branch-name]` — target branch name (e.g., `docs/req-011-aa-deprecated-reporting`)
- `--dry-run` — show what would happen without executing

## Workflow

### 1. Detect Current State

```
Current branch: docs/req-005-aa-deprecated-reporting
Tracks remote: fork/docs/req-005-aa-deprecated-reporting

Open PR: #102 (https://github.com/ansible/apme/pull/102)
PR head: ffirg:docs/req-005-aa-deprecated-reporting
```

### 2. Confirm New Name

If argument provided, use it. Otherwise:
```
New branch name? (current: docs/req-005-aa-deprecated-reporting)
> docs/req-011-aa-deprecated-reporting
```

Validate format:
- Matches artifact ID in branch (e.g., `req-011` matches `REQ-011`)
- Follows naming convention (`type/id-slug`)

### 3. Execute Rename

```bash
# Rename local branch
git branch -m docs/req-005-aa-deprecated-reporting docs/req-011-aa-deprecated-reporting

# Push new branch to remote
git push -u fork docs/req-011-aa-deprecated-reporting

# Delete old remote branch
git push fork --delete docs/req-005-aa-deprecated-reporting
```

### 4. Update PR (if exists)

GitHub PRs automatically track renamed branches if the new branch is pushed before deleting the old one. Verify:

```
PR #102 now tracks: ffirg:docs/req-011-aa-deprecated-reporting
```

If PR doesn't update automatically, provide manual fix:
```bash
gh pr edit 102 --head ffirg:docs/req-011-aa-deprecated-reporting
```

### 5. Summary

```
Done!
- Renamed: docs/req-005-aa-deprecated-reporting → docs/req-011-aa-deprecated-reporting
- Remote updated: fork
- PR #102: Now tracks new branch

Old branch deleted from remote.
```

## Edge Cases

| Situation | Handling |
|-----------|----------|
| Uncommitted changes | Stash before rename, restore after |
| Not on the branch to rename | Prompt to checkout or specify branch |
| No open PR | Skip PR update step |
| Multiple remotes | Prompt which remote to update |
| Protected branch | Warn and abort |

## Safety Checks

1. **Never rename `main` or `master`** — abort with error
2. **Check for uncommitted changes** — stash or abort
3. **Verify remote exists** — fail gracefully if fork not configured
4. **Confirm before deleting old remote branch** — show what will be deleted

## Example Session

```
/branch-align docs/req-011-aa-deprecated-reporting

Current branch: docs/req-005-aa-deprecated-reporting
Target branch: docs/req-011-aa-deprecated-reporting

This will:
1. Rename local branch
2. Push new branch to 'fork'
3. Delete old branch from 'fork'
4. PR #102 will track new branch

Proceed? (Y/N) Y

Renaming local branch... done
Pushing to fork... done
Deleting old remote branch... done
Verifying PR #102... tracks new branch ✓

Done! Branch aligned with REQ-011.
```

## Integration with pr-new

The `pr-new` skill should:
1. Check if branch name matches artifact IDs being committed
2. Warn if mismatch detected
3. Offer to invoke `branch-align` before pushing

Add to pr-new pre-flight checks:
```
Checking branch/artifact alignment...
- Branch: docs/req-005-aa-deprecated-reporting
- Artifacts: REQ-011, DR-013
⚠️  Branch name contains 'req-005' but artifacts use REQ-011

Run /branch-align to fix? (Y/N)
```
