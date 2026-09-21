---
name: git-cleanup
description: Use when the user asks to clean git state, prune stale worktrees, inspect branch/worktree cleanliness, remove orphaned or shadowed checkout directories, reconcile dirty branches, or decide which local branches/worktrees are safe to keep, quarantine, merge, or delete. Prefer repo-local deterministic tooling such as agent-protocols/scripts/repo_state.py before ad hoc shell probes.
metadata:
  short-description: Clean branch and worktree state safely
---

# Git Cleanup

Use this skill for repo hygiene, especially when branches, worktrees, stale
registrations, orphaned checkout directories, or dirty worktrees are involved.

## Core Rule

Let deterministic repo tooling classify state before making judgement calls.
Do not start by pruning or deleting broadly.

If the repo vendors `agent-protocols`, run the repo-state tool before deciding
what to delete, prune, merge, quarantine, or preserve:

```bash
python3 agent-protocols/scripts/repo_state.py --repo /path/to/repo
python3 agent-protocols/scripts/repo_state.py --repo /path/to/repo --json
```

If the current checkout is the package source itself, use:

```bash
python3 scripts/repo_state.py --repo /path/to/repo
```

If safe cleanup is requested or clearly within scope, prefer:

```bash
python3 agent-protocols/scripts/repo_state.py --repo /path/to/repo --apply-safe-cleanup
```

Safe cleanup may prune stale Git worktree metadata and quarantine redundant
checkout directories. Quarantine is preferred over irreversible deletion for
directory contents.

## Fallback Audit

If `repo_state.py` is unavailable, gather:

```bash
git status --short --branch --untracked-files=all
git worktree list --porcelain
git branch --format='%(refname:short) %(objectname:short) %(upstream:short) %(worktreepath)'
```

For each non-main branch, check:

```bash
git merge-base --is-ancestor <branch> main
git rev-list --left-right --count main...<branch>
```

## Automatic Actions

Only take automatic action for deterministic cases:

- stale registered worktree paths that no longer exist: `git worktree prune`
- shadowed checkout directories whose `.git` points at another registered
  worktree gitdir: quarantine
- orphaned checkout directories that exactly map to a local branch leaf and
  that branch is already merged into `main` with no registered worktree:
  quarantine
- local branch refs that are already merged into `main` and have no registered
  worktree: `git branch -d`

After any automatic action, rerun the audit and report the before/after state.

## Escalate To Operator

Ask before changing or deleting:

- dirty worktrees
- unmerged branches
- branches ahead of `main`
- active review branches
- orphaned directories that do not map exactly to a merged branch
- unregistered checkout directories with a live gitdir
- anything involving `main`, production, deployment, secrets, or migrations

## Report Shape

Keep the closeout practical:

- current branch and whether `main` is clean
- stale registrations pruned
- directories quarantined and quarantine path
- remaining judgement calls, ordered by risk
- exact commands run for verification
