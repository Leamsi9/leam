# Pull Request Protocol

Use this protocol when an operator asks to draft, open, review, or revise a PR
description, or when another agent protocol says to prepare a PR.

This protocol governs the PR body. Use the
[Merge to main protocol](merge-to-main-protocol.md) for branch promotion and
merge mechanics.

## Required Preparation

Before writing the PR body:

1. Finish the applicable substantive or minor work protocol through the temp
   cleanup and final code review gates.
2. Run the temp artifact cleanup gate and remove temporary residue that should
   not survive review.
3. Run the final code review gate for the full branch diff. When the toolchain
   supports it, run that review in a fresh independent review context,
   preferably via a subagent. Give the reviewer the diff, relevant protocols,
   test evidence, and acceptance criteria, but not the implementer's defensive
   rationale.
   If a fresh independent review context is unavailable, record why it is
   unavailable and perform the best available independent-style full-diff review
   before proceeding.
4. Resolve each credible side-effect risk with added or rerun tests where the
   risk is testable. If automation is not practical or the risk is not
   applicable, record the narrow manual validation or the reason no extra
   validation applies.
5. After review feedback has been addressed, decide whether another independent
   review pass is necessary. Ask the operator for approval before spending
   another review round unless the repo policy, user instruction, or current
   task contract already requires it. Another review is usually warranted when
   review implementation changed production behavior, public contracts, storage,
   security, deployment wiring, or user-visible flows; when the fix materially
   expanded the diff; when the first review found high-severity or systemic
   issues; or when meaningful uncertainty remains. It is usually good enough to
   stop when fixes were small/local, required checks are green, risks are
   documented, and another review would mainly reread an unchanged diff.

Do not use a PR description to smooth over untested side-effect risk. If a risk
is plausible and testable, test it before asking for review.

## Required Format

Use exactly these top-level sections unless the repo's PR template requires
additional metadata:

```markdown
## Issue or Feature

## Implementation Rationale

## Risks and Mitigations

## Tests
```

### Issue or Feature

Describe the bug behavior or the intended feature behavior.

For a bug, state the externally observable failure and the expected behavior.
For a feature, state the intended capability and the behavioral acceptance
outcome. Keep implementation details out of this section unless they are
needed to identify the affected behavior.

### Implementation Rationale

For a bug fix, explain the technical cause of the problem and why the solution
addresses it.

For a feature, explain the technical requirements and how the implementation
meets them. Name important changed modules, contracts, migrations, jobs, or
runtime surfaces when that helps reviewers understand the approach.

### Risks and Mitigations

List the meaningful risks and side effects considered, especially risks to
adjacent code paths, data contracts, runtime configuration, permissions, public
APIs, and user-visible workflows.

For each risk, include the mitigation. When the mitigation is a test or
validation command, name it here and include the full command and result again
under `Tests`.

If there are no meaningful risks beyond a docs-only or local-only edit, say so
explicitly and name the boundary that makes the risk local.

### Tests

List the tests, checks, and manual validations run for the PR. Include command
names and results.

If a normally relevant check was not run, say `Not run` and give the reason.
Do not omit skipped checks silently.
