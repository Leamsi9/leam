# Minor Work Protocol

This is the lightweight workflow for changes that do not warrant the
[Substantive work protocol](substantive-work-protocol.md) but are still worth
treating with explicit, repeatable discipline.

Use it for tiny edits, isolated fixes, single-file tweaks, dependency bumps,
copy adjustments, and one-off cleanups where a full gated mini-plan would be
heavier than the change.

## When To Use This

Use this protocol when all of these are true:

- the change fits in a single reviewable slice
- it does not span repos or ownership boundaries
- it does not introduce, remove, or relocate a public surface, schema, or
  contract
- it has no meaningful product, runtime, or security risk beyond the local
  edit
- the work can plausibly be completed in one short sitting

If any of those stop being true mid-work, stop and escalate to the
[Substantive work protocol](substantive-work-protocol.md). Mid-work
escalation is normal and not a process failure.

## What This Replaces

This protocol replaces the substantive process for genuinely small work.
It does not replace:

- the [Proposal protocol](proposal-protocol.md) when the change captures a
  durable architectural decision
- the [Temp doc protocol](temp-doc-protocol.md) when the work generates
  scratch notes that should be reviewed and deleted
- normal commit hygiene, code review, or test discipline

## Core Rules

1. Prefer a short-lived dedicated branch over committing directly to the
   primary integration branch. Even at small scope a branch keeps the change
   reverting and reviewing cleanly.
2. Direct commits to the integration branch are acceptable for trivial,
   self-contained, and obviously safe edits. Note the reason in the commit
   message when the change is anything more than that.
3. Keep the change focused. If you find yourself touching unrelated files,
   either revert the wandering edits or escalate to the substantive protocol.
4. Land the change on the integration branch in a single reviewable slice.
   Do not split a minor change across multiple branches.
5. Run any normally required checks (lint, format, tests) before merging.
   Minor scope is not a license to skip them.
6. Before changing an existing code surface, add or identify a meaningful
   passing behavioral test for that surface and run it before the edit. Re-run
   it after the edit before landing the change.
7. For greenfield code additions, use test-driven development: write a
   meaningful failing test for the intended behavior, add the code, then prove
   the same test passes. Do not make the test weak enough for incomplete or
   buggy code to pass.
8. Before landing, opening a PR, or treating the branch's final commit as
   complete, run a full-diff code review gate. When the toolchain supports it,
   run that review in a fresh independent review context, preferably via a
   subagent. Look especially for side effects on nearby code paths, shared
   helpers, data contracts, runtime configuration, permissions, public APIs, and
   user-visible workflows. If a side effect is plausible, add or run the focused
   test or validation needed to prove the risk is mitigated. Do not guess that
   side effects are safe because the change is small.
9. After the change lands, decide whether the decision behind it is worth
   preserving in a durable record. For most minor work the answer is no; for
   some it is yes.
10. Any final `ready for review`, `complete`, `passed`, or equivalent result
   requires a clean git checkpoint. A dirty worktree at final response time is
   a failed gate, not a degraded completion.
11. If the work involves branch/worktree cleanup or ambiguous git state, run
   `agent-protocols/scripts/repo_state.py` before pruning, deleting, or
   classifying git artifacts by hand.

## Lightweight Decision Log

When a minor change reflects a small but durable decision, prefer a one-line
entry in a repo-local decision log over creating a full proposal artifact set.

Recommended shape:

- a single markdown file at `docs/decisions/decision-log.md`, or the path the
  repo already uses for short decisions
- one append-only entry per decision
- format: `YYYY-MM-DD <slug> — one-line decision, optional commit ref`

Do not promote a decision-log entry into a pending ADR or a proposal log
unless the decision crosses the substantive threshold. The whole point of the
log is to capture small decisions without proposal ceremony.

If the repo does not have a decision log and the operator does not want one,
skip this step. Most minor work needs no durable record beyond the commit
message.

## Escalation Triggers

Escalate to the substantive protocol when any of these become true:

- the change starts to span multiple files in different modules with non-local
  effects
- the change touches a public surface, contract, schema, or persisted format
- the change requires more than one reviewable slice
- the work uncovers a separate problem that is not obviously a one-line fix
- the operator or a reviewer asks for a plan, manifest, or completion log

When you escalate, stop committing on the minor branch, treat the work in
progress as early exploration, and recreate the work on a substantive branch
with a durable plan and `.plan.toml` manifest.

## When Not To Use This

Do not use this protocol when the change:

- needs gated phase evidence to be considered done
- spans repos
- needs a pending ADR or proposal log
- is part of a larger workstream that already has a substantive plan
- has even moderate product, runtime, or security risk

In those cases, use the substantive or proposal protocols instead.

## Naming And Branches

When a dedicated branch is used for minor work, prefer a stable shape:

- `chore/<slug>-YYYY-MM-DD`
- `fix/<slug>-YYYY-MM-DD`
- `docs/<slug>-YYYY-MM-DD`

Worktree directories are usually not needed for minor work because the
integration checkout is sufficient and the branch is short-lived. If a
worktree is created, follow the substantive protocol's naming convention.

## Closeout

Closeout for minor work is:

1. run the temp artifact cleanup gate: preserve durable content, then delete
   temporary files, lock files, scratch inventories, and placeholder examples
   that are not meant to survive
2. run the final code review gate in a fresh independent review context when
   the toolchain supports it, preferably via a subagent. Give the reviewer the
   diff, relevant protocols, test evidence, and acceptance criteria, but not
   the implementer's defensive rationale. Resolve each credible side-effect risk
   with added or rerun tests, or record why it is not applicable
3. ensure the change is on the integration branch and pushed when the repo
   policy expects a remote checkpoint
4. if a PR is required, write the PR body with the
   [Pull request protocol](pull-request-protocol.md)
5. delete the local branch if one was used
6. add a decision-log entry only when the decision is worth preserving

A decision-log entry is not a substitute for substantive completion evidence.
It is a lightweight historical breadcrumb, not a gate.

## Temp Artifact Cleanup Gate

Run this gate after the focused change and validation evidence are in place,
but before final code review, landing, or final git checkpoint.

Review `docs/temp/` and any other temporary location touched by the minor
change. Promote any durable finding into the right long-lived surface, then
delete temporary files, editor lock files, scratch outputs, and placeholder
example plans that are not meant to survive. `docs/temp/` should normally end
with only `README.md` unless an active follow-up note is explicitly justified.

## Final Code Review Gate

Run this gate after the focused change and required docs updates are in place,
but before landing, opening a PR, or reporting the branch's final commit as the
complete reviewable result.

When the toolchain supports it, run the review in a fresh independent review
context, preferably via a subagent. Give the reviewer the diff, relevant
protocols, test evidence, and acceptance criteria, but not the implementer's
defensive rationale. The implementing agent still owns follow-up fixes,
validation, and final responsibility for the branch.

If a fresh independent review context is unavailable, record why it is
unavailable and perform the best available independent-style full-diff review
before proceeding.

The review must inspect the full branch diff against the starting baseline and
ask where the small change could have side effects beyond the directly edited
lines. For each plausible risk, prove the mitigation before proceeding:

- add or update a focused automated test when the risk is code-testable
- run the existing regression, integration, or contract test that covers the
  side effect
- perform a narrow manual validation only when automation is not practical, and
  record why that manual check is the right evidence

If the review finds a real defect or an unproven risk, fix it and rerun the
relevant checks before closeout. Resolve each credible side-effect risk with
added or rerun tests, or record why it is not applicable. Do not proceed on
"looks safe" reasoning alone.

After review feedback has been addressed, decide whether another independent
review pass is necessary before final checkpoint. Ask the operator for approval
before spending another review round unless the repo policy, user instruction,
or current task contract already requires it.

Run another review when any of these are true:

- review implementation changed production code, public contracts, schemas,
  migrations, auth/security/billing/privacy boundaries, deployment/runtime
  config, scheduled jobs, or background workers
- the fix touched files outside the originally reviewed diff or materially
  increased the diff size or scope
- the first review found a high-severity defect, multiple credible side-effect
  risks, or a systemic test gap
- acceptance criteria changed, or failing checks required non-trivial fixes
- meaningful uncertainty remains about correctness, security, data integrity, or
  user-visible behavior

It is usually good enough to stop without another review when all of these are
true:

- review feedback was resolved with small local edits, docs clarification, test
  evidence, or rerun checks only
- no new production behavior, public contract, storage shape, security boundary,
  deployment wiring, or user-visible flow changed after the review
- targeted tests, phase gates, and required manual validations are green
- remaining risks are documented as mitigated, accepted, or not applicable
- another review would mainly reread an unchanged diff

## Repo-State Audit

For ordinary clean minor edits, `git status --short --branch` may be enough.
When the task mentions stale worktrees, orphan directories, dirty branches,
shadowed checkouts, cleanup, or ambiguous git state, run:

```bash
python3 agent-protocols/scripts/repo_state.py --repo . --json
```

Run it after reading the repo instructions and identifying scope, but before
manual pruning, branch deletion, checkout-directory deletion, or cleanup
classification.

Use `--apply-safe-cleanup` only after the plain audit has identified
deterministic safe actions and cleanup is within the requested scope. Do not
use safe cleanup on dirty, unmerged, ahead, active, security-sensitive, or
otherwise ambiguous state.

## Final Git Checkpoint

Before reporting minor work as `ready for review`, `complete`, `passed`, or an
equivalent terminal state, prove the reviewable result is committed.

The final checkpoint requires:

- `git status --porcelain --untracked-files=all` is empty in the worktree used
  for the change
- `HEAD` is ahead of the recorded baseline branch or commit when repo files
  were mutated
- the final answer, job result, or completion note states `branch`, `commit`,
  `pushed: yes/no`, and `worktree_clean: true/false`

If durable source, doc, schema, or config changes remain uncommitted at final
response time, the result is `failed_gate`. Do not report dirty implementation
work as ready for review, and do not downgrade it to a degraded completion.

Non-mutating exploratory work does not need a git checkpoint. If exploratory or
proposal-only work creates durable repo artifacts, those artifacts must be
committed or explicitly discarded before the work can be reported as complete.

## Browser And Mobile Verification

Visual UI work must declare browser verification requirements. Mobile-sensitive
work must also require a narrow viewport check.

Evidence should record the route, viewport, browser/tool, target visibility,
and any captured artifact path when available. If verification cannot run, the
work may still finish, but it must finish as `degraded_verification`.
