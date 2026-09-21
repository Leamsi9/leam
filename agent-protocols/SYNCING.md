# Syncing

This package is meant to be vendored into other repos without forking the
protocol logic by hand.

## Recommended Strategy

Use a dedicated upstream repo plus a vendored folder in consumers.

Recommended flow:

1. publish and tag releases in `https://github.com/Leamsi9/agent-protocols`
2. vendor the package into consumer repos as `agent-protocols/`
3. keep a `VERSION` file inside the vendored copy
4. update consumers by copying a tagged release, pulling a subtree update, or
   rerunning `scripts/install.py` from an updated external checkout

The installer vendors the package-owned protocols, examples, scripts, and
skills. Consumer-owned skill projections outside `agent-protocols/` should be
generated or copied from the vendored package rather than edited as independent
forks.

The package is intentionally staying on `0.0.x` until the config contract and
Leam adoption are proven stable.

## Compatibility Contract

Treat the package directory structure and canonical filenames as a compatibility
surface.

- adding new protocols, examples, or scripts is normally safe
- moving or renaming canonical files is a breaking change for consumers
- while the package is still on `0.0.x`, avoid layout churn unless the gain is
  clear and the migration notes are ready

## Good Import Options

- `git subtree`
  Best fit when you want one external history source but do not want submodule
  friction in day-to-day work.
- release tarball or tagged copy
  Best fit when consumers are not all using Git in the same way.
- manual copy with version bump
  Acceptable for smaller repos, but only if the `VERSION` file is updated and
  the sync source is recorded in the consuming repo.

## Installer Refresh Path

When a consumer repo already has a vendored copy, the lightest refresh path is:

```bash
cd /path/to/consumer-repo
python3 /path/to/external/agent-protocols/scripts/install.py --yes
```

That refreshes the vendored package files and fills in newly added missing
scaffold files without requiring a nested package clone inside the consumer
repo.

Vendored package refresh closeout belongs to the consumer repo. The package
does not make a refresh eligible for automatic `main` pushes or automatic
merges. Use a clean worktree or dedicated refresh branch when the normal
checkout is dirty or branch-owned, commit only the vendored package update and
intentional package scaffold files, and promote the refresh with the consumer
repo's normal branch, review, CI, and merge rules. Direct pushes to `main`
still require that repo's explicit policy or operator approval. If the refresh
needs unrelated product/source changes, split those into their own branch and
follow the normal merge-to-main protocol.

## Minimal Consumer Contract

Each consumer should document:

- the upstream repo URL
- the vendored package version
- the local owner of repo-specific overlays outside the package

Keep reusable protocol content in the package. Keep repo-specific landing
pages, ledgers, ADRs, and any proposal logs in the consuming repo.

Package-owned reusable skills live under `agent-protocols/skills/`. Product or
repo-specific skills can live in the consuming repo's native skill surface, but
generic skill improvements should come back to the package first.

## Upstream First For Generic Changes

If a consumer repo discovers a generic improvement:

1. implement and validate it locally
2. open a branch or PR against `Leamsi9/agent-protocols`
3. then sync the resulting upstream package change back into consumers

Do not let long-lived consumer-only package forks become the default workflow.
