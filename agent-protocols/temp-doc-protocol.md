# Temp Doc Protocol

Use this protocol for working notes that help the current task but are not
expected to survive as durable repo artifacts.

## Purpose

`docs/temp/` is the holding area for temporary markdown created during active
work.

Use it for things like:

- scratch comparison notes
- migration checklists that are only useful during execution
- working writeups that may later be condensed into a durable plan, ADR, or
  history note
- temporary inventories or reconciliations that are not themselves part of the
  preserved repo record
- temporary ledgers, tracking files, and draft manifests that are useful while
  a branch is active but should not survive promotion

Do not use `docs/temp/` for artifacts that the substantive-work protocol,
proposal protocol, or reviewer explicitly expects to preserve durably.

## What Must Stay Durable Instead

These stay outside `docs/temp/` from the start:

- durable plan families in `docs/plans/`
- proposal plan families in `docs/plans/proposals/`
- accepted ADRs in `docs/adr/`
- pending ADRs in `docs/adr/pending/` when the repo keeps pending ADRs
- historical ledgers and postmortems in `docs/history/`
- proposal logs in `docs/proposals/` when the repo keeps proposal logs

If a note becomes important enough to survive the current task, preserve the
relevant content in the proper durable surface and then delete the temp doc.

## Naming And Placement

Keep `docs/temp/` namespaced by the core feature or initiative slug when one is
clear.

Recommended shape:

- `docs/temp/<feature-slug>/<topic>-YYYY-MM-DD.md`

Examples:

- `docs/temp/agent-protocols/topology-clarification-2026-03-29.md`
- `docs/temp/oauth/callback-audit-2026-03-30.md`

If there is no stable feature slug yet, place the file directly under
`docs/temp/` with a descriptive basename and date.

## Lifecycle

1. Create temporary notes in `docs/temp/` first.
2. Review them before closing the task.
3. Preserve any durable content in `docs/plans/`, `docs/proposals/`,
   `docs/adr/`, `docs/history/`, or the commit message as appropriate.
4. Delete the temp docs, scratch outputs, editor lock files, generated
   inventories, and placeholder examples after preservation.

At steady state, `docs/temp/` should normally contain only its `README.md` or a
small number of active task notes.

## Cleanup Gate

Every substantive or minor closeout should include a temp cleanup gate.

The gate passes only when each temp artifact has one of these dispositions:

- promoted into a durable surface and deleted from `docs/temp/`
- deleted because it was scratch evidence, generated output, or an editor lock
  file
- kept because it is still an active task note, with that reason recorded in
  the plan, completion log, commit message, or final result

Do not leave temp files behind merely because they are harmless. If they are not
active and not durable, remove them before the final git checkpoint.

## Indexing And Ledgers

Temp docs are intentionally outside the durable plan/proposal/archive indexes.

- do not add `docs/temp/` contents to `docs/plans/plans-index.md`
- do not treat temp docs as live workstream state
- do not preserve them in archive/history unless they are promoted into a
  durable artifact first
