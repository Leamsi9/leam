# Delegated Work Protocol

This protocol wraps delegated sandbox jobs for a repo that vendors
`agent-protocols/`. It governs only behavior that is unique to delegated
sandbox execution.

Delegated work is not a third category of work. Any delegated job that performs
coding work or mutates repo files must adopt either the
[Substantive work protocol](substantive-work-protocol.md) or the
[Minor work protocol](minor-work-protocol.md) before editing files.

Use the minor protocol only when all of its criteria are true. Use the
substantive protocol for everything else, including any change that crosses
repos or ownership boundaries, touches a public surface, changes a schema or
contract, needs multiple reviewable slices, or carries meaningful product,
runtime, or security risk. If the job is non-mutating research or exploration,
record that as `protocol_adopted: non_mutating`.

## Required Quality Evidence

Before a delegated coding job starts, the runtime must deterministically record
a preflight report with:

- host project directory and delegated container working directory
- resolved git worktree root when available
- current branch and commit when git metadata is available
- for mutating substantive jobs, whether the current branch is registered in
  `git worktree list --porcelain`, and the registered path
- repo-state audit availability and output summary when
  `agent-protocols/scripts/repo_state.py` is present
- delegated protocol path supplied to the worker
- selected work protocol path or a required worker selection step between
  `minor`, `substantive`, and `non_mutating`
- recorded baseline branch or commit for implementation work when available
- effective worker mode, model, reasoning effort, and sandbox mode
- expected developer tools, starting with `git` and `rg`
- whether browser or mobile verification is required by the task
- whether the job may start cleanly, start degraded, or fail before execution

The repo-state audit is a classifier, not a cleanup command. Delegated runtimes
or workers may apply safe cleanup only when cleanup is explicitly in scope and
the adopted minor or substantive protocol permits it.

The preflight report is runtime evidence. Prompt text may point workers at this
protocol, but prompt text is not the enforcement mechanism.

The delegated runtime must inject a protocol gate before the worker's task
content. That gate must instruct the worker to read this protocol first and
then adopt exactly one of:

- `protocol_adopted: minor`
- `protocol_adopted: substantive`
- `protocol_adopted: non_mutating`

The quality log must contain deterministic evidence that this protocol gate was
provided to the worker and that the work protocol adoption step was required.

## Completion Quality

Delegated jobs finish with one top-level quality state:

- `passed`: preflight, worker execution, follow-ups, final git checkpoint, and
  required verification all produced sufficient evidence, including a
  registered branch/worktree binding for substantive repo-mutating work
- `degraded_preflight`: the job ran after a non-fatal environment issue
- `degraded_workers`: follow-up workers or resume launches failed
- `degraded_verification`: required browser or mobile verification evidence is
  missing or incomplete
- `failed_gate`: a required gate failed and the job should not be treated as
  complete

The top-level state may still be the most severe state for compact display, but
the quality log must report every non-passed state and every issue. For
example, a job with missing `rg`, failed follow-up resume, and missing browser
evidence should preserve all three states and all three issue messages, not
only `degraded_workers`.

## Follow-Up Workers

Follow-up prompts, resumed sessions, and child workers are delegated work too.
They must receive this protocol gate before their task content.

The parent job must fold child quality logs into its own quality log without
inference:

- preserve every child quality state other than `passed`
- preserve every child quality issue
- use `degraded_workers` when a child worker, follow-up prompt, or resume
  launch fails but the parent output remains usable
- use `failed_gate` when the child failure invalidates the parent result or
  violates the adopted minor/substantive protocol

If a delegated job adopts the substantive protocol and reports a branch as
ready for review, runnable, or complete while that branch has no registered
worktree, the parent job must treat the result as `failed_gate`.
