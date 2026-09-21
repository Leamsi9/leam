# Agent Protocols

Experimental package version: `0.0.19`

This package is a reusable, repo-agnostic protocol kit for agent-driven
planning, right-sized proposal capture, phase gates, and live workstream
auditing.

It is designed to work in either of these shapes:

- as the root of the public `agent-protocols` package repo
- as an `agent-protocols/` folder vendored into another repo

Canonical upstream repo:

- `https://github.com/Leamsi9/agent-protocols`

## Adoption Quick Start

Use an external checkout of `agent-protocols` to vendor the package into a
target repo.

Do not clone `agent-protocols` inside the target repo. That leaves a nested git
repo in `agent-protocols/`, which is not the intended vendored shape.

Clone the package somewhere outside the target repo once:

```bash
git clone git@github.com:Leamsi9/agent-protocols.git ~/src/agent-protocols
```

Then run the installer from the repo you want to adopt:

Single repo or standalone sibling repo:

```bash
cd /path/to/target-repo
python3 ~/src/agent-protocols/scripts/install.py --yes --skip-workspace-discovery
```

Orchestration repo with sibling repos:

```bash
cd /path/to/orchestrator-repo
python3 ~/src/agent-protocols/scripts/install.py
```

For non-interactive orchestration installs that should include discovered
sibling repos:

```bash
cd /path/to/orchestrator-repo
python3 ~/src/agent-protocols/scripts/install.py --yes --include-discovered-repos
```

Best-practice defaults:

- keep one external checkout such as `~/src/agent-protocols`
- keep one clean integration worktree on `main`
- `cd` into the repo you want to scaffold before running the installer
- create a fresh branch-owned worktree for each substantive stream
- do pulls, merges, pushes, and reconciliation from the clean integration
  worktree rather than from an implementation worktree
- once a stream is fully merged into `origin/main`, delete the local branch and
  remove its dedicated worktree unless it is explicitly preserved
- use the orchestration repo for cross-repo installs
- use `--skip-workspace-discovery` when adopting the package into a sibling repo
  that should stay standalone
- review the detected plan in interactive mode before accepting it

After the installer runs:

1. review `agent-protocols.toml`
2. review or refresh repo docs that point to the protocol
3. check the printed assistant entrypoint wiring status
4. use `--print-adoption-prompt` if you want a coding assistant to finish the
   repo-specific wiring

The installer copies the package into the target repo as `agent-protocols/`.
The external clone is only the source used for vendoring.

To refresh an existing install after pulling a newer upstream package:

```bash
cd /path/to/target-repo
python3 ~/src/agent-protocols/scripts/install.py --yes
```

That refreshes the vendored package files and creates any newly introduced
scaffold files that are still missing, such as `docs/temp/README.md`.
The installer also reports whether common assistant instruction files such as
`AGENTS.md` or `CLAUDE.md` already point at the vendored protocols.

Default refresh closeout is controlled by the consumer repo, not by this
package. After refreshing a vendored copy:

- keep the refresh diff limited to `agent-protocols/` plus intentional
  package-scaffold files
- do not auto-push or auto-merge the refresh solely because the package changed
- if the active checkout is dirty, on a feature branch, or part of ongoing
  implementation work, use a clean worktree or dedicated refresh branch instead
  of mixing the protocol bump into unrelated work
- promote the refresh with the consumer repo's normal branch, review, CI, and
  merge rules; direct pushes to `main` still require that repo's explicit
  policy or operator approval
- if unrelated source, product, deployment, or docs changes are needed, split
  them into a normal workstream and use the merge-to-main protocol

## Quick Start Patterns

Use the pattern that matches how the repo is worked on:

- Single repo:
  Run the installer inside that repo with `--skip-workspace-discovery`.
- Multi-repo workspace:
  Run the installer inside the chosen orchestration repo first, then let it
  discover or include sibling repos.
- Standalone sibling in a larger workspace:
  Run the installer inside that sibling repo with
  `--skip-workspace-discovery` so it keeps a local-only config.

If you later decide a standalone repo should become part of a cross-repo
orchestration setup, rerun the installer in the orchestration repo or update
`agent-protocols.toml` there. Do not create a second cross-repo authority in a
different repo.

## Package Files

- `README.md`
  Package overview and vendoring guidance.
- `VERSION`
  Human-readable package version for vendored copies.
- `SYNCING.md`
  Recommended strategies for propagating upstream improvements into vendored
  instances.
- `assistant-integration.md`
  Guidance for wiring repo instruction surfaces back to the package.
- `assistant-adoption-prompt.md`
  Copy-paste prompt template for asking a coding assistant to adopt the package
  in another repo.
- `merge-to-main-protocol.md`
  Canonical workflow for promoting scoped changes to `main` through GitHub
  pull requests.
- `pull-request-protocol.md`
  Invocable PR-body protocol that standardizes the Issue or Feature,
  Implementation Rationale, Risks and Mitigations, and Tests sections.
- `substantive-work-protocol.md`
  Canonical workflow for non-trivial implementation work.
- `minor-work-protocol.md`
  Lightweight workflow for tiny, single-slice changes that do not warrant the
  substantive protocol.
- `delegated-work-protocol.md`
  Deterministic sandbox-job wrapper that requires every coding job to adopt
  either the substantive or minor work protocol.
- `proposal-protocol.md`
  Canonical workflow for proposal-only slices that stop before implementation,
  with one compact durable record by default and extra artifacts only when
  they add review value.
- `plan-protocol.md`
  Canonical conventions for a repo-local `docs/plans/` surface.
- `temp-doc-protocol.md`
  Canonical lifecycle for temporary working docs under `docs/temp/`.
  Substantive and minor closeout gates use it to remove scratch files before
  final review or checkpoint.
- `citation-verification-protocol.md`
  Gated citation-audit workflow for verifying local citation inventory,
  online metadata, source-content support, and verbatim quotations.
- `local/README.md`
  Package-source marker for the consumer-owned local protocol overlay. Consumer
  files under `agent-protocols/local/` are not package-owned.
- `skills/git-cleanup/SKILL.md`
  Optional assistant skill for independent git/worktree cleanup triage. It is
  package-owned so consumers can project it into Codex, Claude, Ironclaw, or
  another local skill surface without hand-copying the guidance.
- `skills/citation-substance-review/SKILL.md`
  Optional assistant skill for judging whether cited sources substantively
  support their manuscript invocation contexts, while keeping the citation
  verification protocol and deterministic gates as the canonical workflow.
- `examples/gated-phase-manifest.example.toml`
  Reusable example manifest for gated mini-plans.
- `scripts/check_gated_plan.py`
  Canonical phase-checker implementation.
- `scripts/check_merge_to_main_protocol.py`
  Canonical merge-to-main protocol and repo-local testing promotion wiring
  checker.
- `scripts/check_local_overlay_policy.py`
  Package guard that keeps repo-local overlay files out of package-owned
  vendoring lists.
- `scripts/workstream.py`
  Canonical live-workstream audit, clean-git reconciliation, and status-ledger
  sync script.
- `scripts/repo_state.py`
  Low-level branch/worktree/orphan checkout classifier with deterministic safe
  cleanup for stale registrations and redundant checkout directories.
- `scripts/citation_inventory.py`
  Mechanical BibTeX/source citation inventory checker.
- `scripts/citation_build_source_audit.py`
  Working-ledger generator for online metadata, content-support, and quote
  verification rows.
- `scripts/citation_check_content_support.py`
  Deterministic checker for per-key source-content support ledgers.
- `scripts/citation_check_audit_ledger.py`
  Deterministic checker for metadata, content, and quote statuses in citation
  audit ledgers.

When an operator asks to "clean git", use the reconciliation inventory first
instead of pruning branches blindly:

- `python3 agent-protocols/scripts/repo_state.py --repo . --json`
- `python3 agent-protocols/scripts/workstream.py reconcile --json`
- `scripts/install.py`
  Bootstrap installer that vendors the package and scaffolds repo-local config
  and docs.

## Standalone Quick Start

The public package repo ships a small example `docs/` skeleton so you can
validate the package in isolation:

- `python3 scripts/check_gated_plan.py docs/plans/feature/example-workstream.plan.toml --phase acceptance`
- `python3 scripts/repo_state.py --repo . --json`
- `python3 scripts/workstream.py reconcile --json`
- `python3 scripts/workstream.py sync-index --confirm`

To vendor the package into a repo and scaffold the repo-local layout:

- `python3 agent-protocols/scripts/install.py`
- `python3 agent-protocols/scripts/install.py --yes`
- `python3 agent-protocols/scripts/install.py --yes --skip-workspace-discovery`
- `python3 agent-protocols/scripts/install.py --yes --include-discovered-repos`
- `python3 scripts/install.py --target /path/to/repo --repo-id my-repo`
- `python3 scripts/install.py --target /path/to/repo --repo-id my-repo --linked-repo workspace@git_common_root=../workspace`
- `python3 scripts/install.py --target /path/to/repo --repo-id my-repo --print-adoption-prompt`

Installer behavior:

- When run inside a git repo, the installer defaults to that repo root.
- In interactive mode, it proposes the detected repo plus discovered sibling
  repos in the surrounding workspace and asks for confirmation.
- If you reject the detected plan, it prompts for the repo path, repo id,
  branch, and linked repo entries.
- After scaffolding, it prints `assistant entrypoint wiring: ok` when a common
  assistant instruction file already references the vendored protocols, or
  `assistant entrypoint wiring: action needed` with next steps when no such
  reference is found.
- Use `--yes` for automation.
- Use `--skip-workspace-discovery` when you want a strictly single-repo
  install.
- The example plan family is only seeded when the target repo does not already
  have a `docs/plans/` surface.

## Vendoring

To vendor this package into another repo:

1. Copy this package into the repo root as `agent-protocols/`.
2. Generate or maintain a repo-local `agent-protocols.toml` that declares the
   owning repo and any linked repos.
3. Point maintainer docs such as `AGENTS.md` or `CLAUDE.md` to
   `agent-protocols/substantive-work-protocol.md`.
4. Add a short `docs/plans/README.md` landing page plus a
   `docs/plans/plans-index.md` inventory.
5. Keep live generated status in a repo-local ledger such as
   `docs/live-workstream-status.md`.
6. Keep temporary working docs in `docs/temp/` and follow
   `agent-protocols/temp-doc-protocol.md`.
7. Put repo-specific protocol extensions in `agent-protocols/local/`; package
   refreshes preserve that directory and do not treat it as upstream content.
8. Run the canonical scripts directly:
   - `python3 agent-protocols/scripts/check_gated_plan.py path/to/work.plan.toml --phase phase_name`
   - `python3 agent-protocols/scripts/repo_state.py --repo . --json`
   - `python3 agent-protocols/scripts/workstream.py reconcile --json`
   - `python3 agent-protocols/scripts/workstream.py sync-index --confirm`
   - `python3 agent-protocols/scripts/install.py`

See also:

- `examples/repo-config.example.toml`
- `assistant-integration.md`
- `assistant-adoption-prompt.md`

## Compatibility Surface

Treat the package path layout and canonical filenames as part of the package
interface.

- additive protocols, examples, or scripts are the safe default
- moved or renamed canonical files should ship with an explicit migration note
- consumer repos should update active references before pruning historical docs
- `agent-protocols/local/` is the consumer-owned overlay; package-owned copy
  lists must not include it, and installer refreshes must preserve its files

## Recommended Repo-Local Shape

If a repo adopts the default taxonomy described by this package, it should
normally keep:

- `docs/plans/README.md`
  Short landing page describing what lives in the plans folder.
- `docs/plans/plans-index.md`
  Durable plan inventory and taxonomy index.
- `docs/plans/proposals/`
  Compact proposal-only records when a proposal genuinely needs to survive.
- `docs/live-workstream-status.md`
  Script-owned current-state ledger.
- `docs/temp/README.md`
  Landing page for temporary working docs that should be reviewed and deleted
  after preservation.
- `docs/plans/cross-repo/README.md`
  Optional extension for repos that act as the canonical orchestration root for
  coordinated multi-repo work.

Repos that keep pending ADRs or proposal logs may also add thin repo-local
landing pages such as `docs/adr/pending/README.md` or
`docs/proposals/README.md`, but those are no longer default scaffolding.

## Naming Conventions

Use one stable feature or initiative slug across git state and temporary docs
when the workstream has a clear owner.

Recommended patterns:

- branches:
  - `feature/<feature-slug>/<slice>-YYYY-MM-DD`
  - `docs/<feature-slug>/<slice>-YYYY-MM-DD`
- worktree directories:
  - `<repo>-<feature-slug>-<slice>`
- temp docs:
  - `docs/temp/<feature-slug>/<topic>-YYYY-MM-DD.md`

For durable plans and proposals, prefer the existing taxonomy first and use the
feature slug in the filename rather than replacing the type or status folders.
For example, prefer `docs/plans/feature/agent-protocols-temp-doc-governance-2026-03-30.md`
over creating a new top-level `docs/plans/agent-protocols/` taxonomy by
default.

## Recommended Topologies

Use one of these shapes, depending on how the codebase is actually worked on:

- Single repo:
  Vendor `agent-protocols/` in that repo and keep `agent-protocols.toml`
  rooted to `.`.
- Workspace anchor repo:
  Pick one primary repo to own integrated cross-repo plans and the shared live
  ledger, then list sibling repos in `agent-protocols.toml`.
- Standalone sibling repo:
  If a sibling repo is also worked on independently, vendor its own
  `agent-protocols/` copy there too, with a repo-local config and ledger.

Important clarifications:

- The workspace anchor repo still vendors `agent-protocols/` for itself.
  Its local vendored copy is also the cross-repo orchestration copy.
- A sibling repo having its own vendored `agent-protocols/` does not create a
  technical clash by itself.
  Each repo keeps its own local `agent-protocols.toml`, local instruction
  pointers, and optional local ledger.
- The real thing to avoid is split authority:
  do not let multiple repos in the same workspace each claim to be the
  cross-repo orchestration root for the same set of workstreams.
- Cross-repo work should normally start from the chosen workspace anchor repo.
  Single-repo work can still start inside any repo that has its own local
  package copy.

Recommended rule:

- Cross-repo workstreams should have one orchestration root.
- Repos that are regularly opened on their own should also carry their own
  local package instance.
- Generic improvements made in any consumer repo should be upstreamed here.

Cross-repo planning shape:

- local plans stay in the default taxonomy directly under `docs/plans/`
- cross-repo plans live under `docs/plans/cross-repo/` only in the chosen
  orchestration repo
- sibling repos may keep thin local notes that point back to the canonical
  cross-repo plan, but should not duplicate the full plan family

Placement heuristic:

- use the default local taxonomy when one repo can own implementation,
  validation, and acceptance
- use `docs/plans/cross-repo/` only when completion or acceptance depends on
  coordinated work across 2+ repos
- mentioning another repo as context does not make a plan `cross-repo`
- if a local plan later expands into coordinated multi-repo work, supersede or
  promote it into the orchestration repo under `docs/plans/cross-repo/`

The goal is one reusable canonical package and no duplicate authorities inside
each consuming repo.
