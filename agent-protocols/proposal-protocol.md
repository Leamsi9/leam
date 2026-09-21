# Proposal Protocol

Use this protocol when a change is important enough to preserve durably, but it
is not ready to implement or accept yet.

Proposal capture should be compact by default. A proposal is a durable review
record, not a requirement to create a plan, manifest, completion log, pending
ADR, and proposal log every time.

Use the [Substantive work protocol](substantive-work-protocol.md) only when the
proposal work itself needs substantive branch discipline, phase gates, or
multi-step completion evidence.

## When To Use This

Use the proposal protocol when any of these are true:

- the workstream stops at a durable proposal rather than implementation
- the architecture direction is useful to preserve, but not ready to accept
- the change needs a reviewable proposal artifact in one or more repos
- you want later chats to resume from durable repo state instead of chat memory

## Artifact Economy

The durable minimum is one compact proposal record in the owning repo.

Add artifacts only when they earn their keep:

- add a `.plan.toml` manifest only when the proposal needs gated capture or
  should appear in a manifest-backed live-status ledger
- add a completion log only when the proposal work produces evidence that is
  too detailed for the proposal record or final review note
- add a pending ADR only when the proposal preserves an architecture decision
  that is not ready to accept
- add a proposal log only when a repo already has an owned review surface that
  needs a compact local target record
- add mirrors only when another repo owns real review or implementation work

Temporary comparison notes, inventories, run ledgers, draft manifests, and
tracking files should start in `docs/temp/` or another repo-approved temporary
location. At closeout, fold the durable substance into the proposal record,
ADR, history note, or commit message, then delete the temporary files.

## Canonical Locations

### Proposal Plan Families

When helpful, use explicit status buckets under `docs/plans/proposals/`:

- `active/`
- `pending/`
- `blocked/`
- `in-progress/`

These buckets are optional. A repo may keep proposals directly under
`docs/plans/proposals/` when the extra status folders add more navigation cost
than clarity.

Historical proposal plan families move to `docs/plans/archive/`.

For orchestration repos that use the optional cross-repo extension, keep
cross-repo proposal plan families under `docs/plans/cross-repo/proposals/`
with the same optional status-bucket pattern.

### Pending ADRs

- accepted ADRs stay in `docs/adr/`
- pending ADRs live in `docs/adr/pending/` when the repo keeps pending ADRs
- do not keep pending ADRs in the accepted ADR namespace

### Proposal Logs

Use a proposal-log surface only when the repo has a concrete pending target
that needs a separate review record.

When a repo uses the default layout, keep explicit status buckets under
`docs/proposals/`:

- `active/`
- `pending/`
- `blocked/`
- `in-progress/`
- `archive/`

Do not create duplicate proposal logs in both repos unless both repos have
their own concrete owned targets.

### Temporary proposal notes

Scratch comparison notes, temporary review checklists, and other proposal
working notes that are not yet durable should live under `docs/temp/` first.

When they become durable, promote the relevant content into the proposal
record, pending ADR, proposal log, or history note and then delete the temp
doc.

## Manifest Vs Proposal

A manifest is a `.plan.toml` gate file for a plan family. It is not the same
thing as a proposal, and proposals do not need manifests by default.

A proposal is the durable review record for a pending idea. Depending on the
risk and ownership shape, it may be:

- one proposal markdown file
- a proposal markdown file plus a manifest for gated/live-status tracking
- a proposal markdown file plus a pending ADR
- a thin local proposal log that points to a canonical proposal elsewhere

Manifest-backed pending proposals are therefore a subset of proposals, not the
whole set.

## Proposal Visibility

Pending proposals are part of current-state visibility, but they do not belong
inside a `README`.

Keep the durable proposal record in `docs/plans/proposals/` or the repo's
chosen proposal surface. Keep pending ADRs and proposal logs only when they are
owned, useful surfaces. A script-owned ledger such as
`docs/live-workstream-status.md` may surface manifest-backed proposals as
`pending_proposal`, but a proposal does not need to appear in that ledger to be
valid durable state.

Pending means the proposal record is ready on `main`, but substantive
implementation has not begun. Once implementation starts, move to a fresh
substantive-work branch and treat that workstream as `active` implementation
rather than as a still-pending proposal.

## Ownership Rules

For cross-repo proposal work, use these defaults:

- one canonical proposal record
- one pending ADR only in the repo that owns the architecture decision
- one proposal log only in a repo that owns a real pending target
- thin mirrors instead of duplicate narrative copies

Examples:

- shared runtime proposal:
  - proposal record in the shared repo
  - pending ADR only when it preserves an unaccepted architecture decision
- product- or workspace-owned proposal:
  - proposal record in the canonical planning repo when the work is cross-repo
  - pending ADR in the repo that owns the architecture decision, when needed
  - proposal log only when the product- or workspace-owned review surface needs
    a separate compact target
- cross-repo implementation idea with no concrete product-owned target yet:
  - keep one proposal record in the shared repo
  - add a pending ADR only if the architecture decision needs review

Placement heuristic:

- local proposal work stays in the default `docs/plans/proposals/` taxonomy
- use `docs/plans/cross-repo/proposals/` only when the proposal needs
  coordinated review, implementation planning, or acceptance across 2+ repos

## Proposal Log Content

A proposal log, when used, should record:

- status
- proposal class
- owning repo and target surface
- related plan path
- pending ADR path when applicable
- the proposed change in a compact, reviewable form

If the proposal log uses an existing typed contract, keep using that contract
instead of inventing a second one.

Prefer namespaced basenames when a proposal belongs to a clear feature slug, for
example `agent-protocols-temp-doc-governance-proposal-2026-03-30.md`.

## Lifecycle

The intended lifecycle is:

1. create a short-lived branch for the proposal change
2. capture the pending direction in one compact proposal record
3. add a manifest, pending ADR, proposal log, or mirror only when needed
4. delete temporary notes after folding durable content into the proposal
5. merge the proposal slice to the integration baseline
6. later either:
   - resume it as a fresh implementation workstream, or
   - archive it as superseded or rejected

A completed implementation does not leave the proposal live. Once the
implementation lands, archive or supersede the pending proposal artifacts so
they remain historical rather than active.

When a proposal is accepted later:

- promote the accepted ADR into `docs/adr/`
- archive the old pending ADR path
- archive or update the proposal log according to the owning repo’s review
  contract
- archive or update the proposal record when it is no longer live

## Archive Vs History

- `docs/plans/archive/` is for historical proposal plan families
- `docs/history/` is for factual ledgers and preserved records
- `docs/proposals/archive/` is for historical proposal logs when the repo keeps
  proposal logs under `docs/proposals/`

Do not move proposal plans or pending ADRs into `docs/history/` just because
they are no longer active. Use archive paths for inactive planning artifacts
and history paths for factual records.
