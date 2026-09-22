# Candidate delivery and post-deployment QA

Applies to the isolated Leam MVP candidate, as explicitly requested by the user
on 2026-09-20. This repo-local overlay changes delivery order; it does not change
the vendored agent-protocols package or acceptance requirements.

Independent feature slices may be implemented in parallel in dedicated worktrees.
Deliver the smallest usable increment once its required dependencies build. Build
and deploy it to the candidate before running the full QA/review/phase gates.
Long-running CI must not block candidate CD or user UAT. Keep one integration owner
and a separate QA worker where concurrency allows. Do not wait for every feature
to finish before deploying an unrelated usable slice.

Record source commit, artifact identity, deployment time, rollback artifact, and
QA status for each deployment. Distinguish available / QA pending, QA passed, and
QA failed. A deployed increment is available for UAT, not automatically accepted.
Run targeted caller-level, browser/mobile and regression checks after deployment;
continue required independent review and phase checks before claiming completion.
A reproduced failure revokes acceptance: repair and redeploy promptly, or restore
the previous candidate if the failure prevents useful testing. Recheck the affected
behavior against the new deployment identity.

Preserve existing installations and user data. Candidate deployment authorization
does not authorize destructive migration or replacement of the old installation.
Keep authentication and secret handling intact. This is not permission to knowingly
publish a broken build or ignore a known data-loss/security defect. Archive or
back up mutable state before a migration that requires it.

The user-authorized parallel implementation and post-deployment QA order supersede
the base protocol's serial-phase and verification-before-deployment ordering for
this candidate only. All applicable acceptance gates remain required for final
completion. Ordinary companion conversations do not invoke engineering protocols.


## Backlog and status reconciliation

Before a substantive build status report, after a deployed/completed increment,
and at each active-work heartbeat, reconcile the entire visible backlog against
current source, deployment/QA/UAT receipts and worker handoffs. Use
`python -m leam_api.backlog --data-dir <private-data-dir> list`, then `reconcile
--input <private-reviewed-json>` and `status --require-current`; see the backlog plan for input.
Every ticket needs current evidence coverage or an explicit removal reason. The
operator-only `python -m leam_api.backlog_evidence --data-dir <private-data-dir>`
workflow may snapshot, show a delta, dry-run and apply explicit decisions. It
mechanically validates unchanged evidence while preserving ticket body, revision
and semantic assessment date. Changed evidence requires an explicit assessment,
removal or reasoned acknowledgement; initial baseline and order/user-intent changes
require acknowledgement. `factsCheckedAt` is not renewed semantic assessment.
The tool rechecks exact revisions, dependency QA, assignments and canonical order
atomically; stale snapshots fail. Add missing scope and retain unresolved judgments.
Do not claim currency unless the receipt still covers current evidence. Preserve
user-only UAT authority. The timer never refreshes percentages or assessment dates;
report unfinished review honestly. Existing full reconciliation remains supported.

Every status/new-task/full-inventory review must deliberately select delivery work:
unblocked → deliverable → important. Record stage, rank, priority, owner/worker
(opaque IDs only), next action, dependencies and bounded subtasks where useful.
Review all tickets, including unchanged choices with factual justification; do not
mechanically refresh timestamps. Fresh percentages never imply active work, and
worker exit or subtask completion never implies deployment/QA/UAT. Use the delivery
backlog contract in `docs/plans/feature/delivery-kanban.md`; deployed tickets stay
in Updates. Delegated implementation workers forbidden private-data writes hand
this live reconciliation to Main and must not claim backlog currency themselves.

## User-controlled build priority

Use the canonical `rank` returned by the current Backlog API/CLI as the single
build-priority order. Dragging or Move up/down changes that order; the list and
Kanban cards use the same rank. Read the fresh queue before assigning an available
worker and choose the lowest-ranked unblocked, deliverable task. Preserve saved
user order during factual reviews; do not substitute old assessment ranks, percent
completion or a separate private queue. Keep blocked tickets ranked and explain
dependencies or urgent recovery exceptions. Do not abandon accepted work or
interrupt a deployment merely because another card moved; apply the new order at
the next safe assignment boundary. New explicit user priorities can reorder the
queue through its revision-protected order operation.

## Register work before dispatch

Before any new substantive worker assignment or local implementation slice, Main
registers/reconciles its existing backlog ticket, then runs
`python -m leam_api.backlog --data-dir <private-data-dir> start --feature <id> --worker <opaque-id> --expected-revision <n> --request-id <uuid> --note <scope>`
and obtains a successful receipt before dispatch. Preserve the request UUID for
retries; a rejected gate means no dispatch. Worker handoff uses `handoff`, and
blocked/failed work uses `blocked`/`failed` with the same owner/worker and current
revision. Reprioritized unfinished work uses `pause` to return it to Queued and
clear its assignment. Main reconciles these outcomes before the next assignment; handover is
not deployment or completion. The CLI enforces ticket/revision/readiness and
records state; it cannot intercept external collaboration tools or prove a
worker is running. Full-backlog review remains a separate requirement.


## Keep available workers usefully assigned

At each work-boundary/status check compare the four-workstream target with actual
worker liveness; reservations alone are not proof. Dispatch available eligible
work in canonical user rank after its start gate. Inspect deletion tombstones
before dispatch, integration and publication. For a deleted active task, stop and
verify the external worker before acknowledging cancellation. Record real capacity
limits or dependencies; never manufacture four active workers to satisfy the target.


## Immediate refill and interruption gate

At turn entry after a user interruption, inspect `collaboration.list_agents` before
assuming workers are running: an interruption can stop every worker while its
backlog reservation remains active. Run `staffing-invalidate --reason <bounded reason>`,
then resume the same unfinished, still-authorized assignment with `followup_task`
(or record its pause/failure/cancellation). Do not issue a duplicate `start` for a
resumed assignment. Check tombstones before resuming deleted work.

Immediately after worker handoff/exit, deployment publication or assignment change,
and before each status report, run the deterministic operator staffing gate:

1. Finish the existing full-backlog evidence reconciliation when required.
2. `python -m leam_api.backlog --data-dir <private-data-dir> staffing-snapshot`.
3. Inspect `collaboration.list_agents`; record fresh worker states/agent IDs and
   matching canonical assignment IDs in a private Observation JSON. Record real
   coordinator activity and any explicit reduction from four available slots.
4. `python -m leam_api.backlog --data-dir <private-data-dir> staffing-check --input <private-json>`.
5. If capacity and eligible work remain, dispatch the next canonical-ranked task
   through its existing start gate, then inspect workers and check again. Resume
   interrupted authorized work or resolve its assignment before substituting work.
6. `status --require-current` rejects missing/failed/expired staffing evidence as
   well as stale backlog review. A passing staffing receipt expires after 120 seconds
   and becomes stale when assignments, ordering, dependencies, deployment/QA or
   cancellation state changes. It does not renew task percentages or review dates.

This CLI validates coordinator-attested observations; it cannot prove external
process liveness, intercept collaboration lifecycle events, or launch agents. A
reservation, pending dispatch or idle/interrupted worker must never be called an
active workstream. Main counts once, including when integrating several handed-off
features. Deployed QA remains attached to the same canonical feature in Updates;
its rightful worker may hand off or report a blocker after publication.

## User queue suspension

A ticket's canonical `paused` flag is a user-owned scheduling decision, not a
completion or dependency state. Preserve its rank and skip it during dispatch,
refill and triage selection until the user explicitly unpauses it. Do not clear
pause through operator assessment updates. A user request to finish active work
and pause the queue overrides the four-workstream refill target: finish accepted
work and its deployment/QA, then stop. An unpaused ticket is eligible, not a claim
that an idle external Codex session has automatically started running.
