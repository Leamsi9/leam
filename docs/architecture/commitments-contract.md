# Commitments and Remember

Capacities describe areas of life, notes and personal records. Commitments are tasks, habits or goals with a boolean/count/minutes measure, target, date window, timezone, optional reminder time, reward and notes. Zero targets are preserved for open-ended measurements.

Per-day logs store measured value, explicit completion, undo value, revision, target/measure snapshot and timestamp. Completing a numeric objective preserves partial progress; completing from zero fills the target, and undo restores zero. Tasks additionally have a global status: toggle direction follows that status even when an older daily log disagrees. Task status and its affected daily log change in one transaction. Habits/goals use per-day completion; globally completed or paused recurring commitments no longer appear in Today.

Today selects the requested date, or the current date in each commitment's named timezone. Completed tasks without a log for that date stay in All commitments rather than accumulating on every day. Cross-device edits require matching revisions; a slow UI response cannot switch the view back to an earlier date.

## Retained import compatibility

User scope amendment 2026-09-21: Remember import is removed from Settings and
is not an MVP requirement. Existing import provenance and backend compatibility
remain; no existing capacities or commitments are deleted.
Remember version 1 imports use an explicit source name and timezone. Preview validates references, dates and measures; apply is bound to the preview digest. Stable source identities prevent duplicate records. Original and mapped-record fingerprints detect changed exports, including changed timezone assumptions. Existing local edits are retained on repeated import. Changed source records currently stop the import instead of attempting an automatic merge.

Every import preserves the full original document, including unknown fields and original reminder metadata. A private SQLite backup precedes the transaction. Tests open that backup and verify integrity and pre-import contents. Full operational restore/rollback remains a later deployment acceptance gate.

Source app databases/accounts are never read or modified by this adapter. The retained backend supports preview/apply and original-source retrieval with a 1 MiB API body limit. The former mobile import flow is no longer exposed.

## Reminders
The API runs a durable scheduler every 15 seconds. One database identity per commitment/local date prevents duplicate daily occurrences. Current-day reminders catch up after restart; explicit snoozes can cross midnight. DST gaps move to the first valid minute and repeated hours use the first occurrence. Paused/completed commitments cancel pending reminders. Moving an unsnoozed pending reminder into the future withdraws it until the new due time; explicit snoozes remain authoritative. Dismissal suppresses that day's occurrence.

Snooze, dismiss and completion require the current reminder revision. Completion calls the shared progress operation and completes the notification in the same transaction; repeated completion cannot reopen a task. Imported snooze timestamps remain authoritative even if they elapsed before import; otherwise prior reminder metadata suppresses already-delivered occurrences. Invalid timestamp metadata fails preview.

The inbox provides in-app delivery while the browser is closed. It requires the host/service to run; it cannot wake the host. Settings distinguish startup, healthy, stale and failed scheduler states. Phone push is implemented behind explicit device registration; actual phone delivery acceptance is pending (see push-contract.md). Companion tools must call these same domain operations when that integration is completed.

## Reviewed removal

Individual commitments can be permanently removed from their editor or All commitments. Capacities use an impact dialog: move every linked commitment to another existing capacity, or explicitly confirm removing the capacity and all listed commitments. Empty capacities need a removal confirmation. The preview identifies each affected commitment, progress-entry count and active-reminder count. A capacity's notes and personal record are removed with it. No archive status or automatic undo is implied.

GET `/api/{commitments|capacities}/{id}/removal-preview` returns a preview token bound to the entity revision, exact child membership/revisions and every dated progress-log revision. POST to the corresponding `/remove` requires that token and `confirmed: true`; capacities additionally choose `empty`, `move` or `cascade`. Move requires `targetCapacityId` and `targetRevision`. Stale source, child, progress or destination state fails before mutation. A single immediate transaction applies the entire move/deletion and its local event. The existing empty-capacity DELETE remains revision-checked and refuses populated capacities.

Move increments each commitment revision and preserves its progress/reminders. Deletion cascades local progress logs and reminder jobs; pending push receipts are cancelled and references to deleted jobs are cleared, while historical delivery receipts remain. Notifications already sent or in delivery cannot be recalled. External calendar events/actions are retained and must be managed through their own reviewed calendar controls. Original Remember import archives and identity/provenance receipts are retained; repeated imports do not resurrect deliberately removed records. No external provider call occurs during removal.

The UI requires fresh explicit confirmation after changing operation/destination or refreshing a stale preview. Network failures retain the review; there is no automatic retry. Repeating a completed removal yields not-found rather than affecting new records. Tests use isolated data; actual candidate deployment acceptance and real user actions are separate.

## Item Companion conversations

Commitment cards, commitments outside the current Today view, and capacity
management each expose a collapsed chat panel. Opening ensures a durable Companion
thread without starting a model turn; closed panels mount no transport. The same
Companion model, voice, attachments, streaming, cancellation, delivery receipts and
approval controls apply. Item chats do not change the main Companion selection.
Each new submission reads a current canonical item snapshot (revision/freshness,
local-day progress or bounded linked commitments). Item reference data is at most
2KiB within the existing total8KiB run reference; unrelated memory snippets may be
omitted with a partial flag. User text remains unchanged. Queued or uncertain
submissions retain their original snapshot; tools verify freshness before changes.

Bindings persist a native creation action before HTTP and converge across concurrent
opens/retries. Removing the item preserves its conversation history and identifies
the missing source; it never recreates the item. Deleting the native conversation
retains the binding and reports unavailability instead of silently replacing it.
An explicit replacement-chat control is not yet implemented. Only the explicitly
started voice conversation owns automatic replies/listening across open panels.

## Capacity boards and card structure

A capacity is the canonical board and a commitment is its canonical card. This is
an additive representation of the same IDs and revisions used by Goals, Today,
progress, approvals and item conversations; there is no duplicate board store.
Existing records receive read-time defaults without a migration or data rewrite.

Cards add `owner: user|leam` (default user), `stage: todo|in_progress|blocked`
(default todo), `priority: low|normal|high` (default normal), nullable ISO
`dueDate`, and `subtasks` (default empty). The existing lifecycle status remains
`active|completed|paused`: completed and paused override stage in board displays;
paused is distinct from blocked. A board lifecycle PATCH changes no daily log.
Existing Today completion controls continue using the shared progress operation.
Start/end dates describe an explicit planned window; dueDate is an independent
explicit deadline. A missing date is never replaced with an inferred timeline.

Each subtask has a UUID, title (1–200 characters), owner, status
`todo|in_progress|blocked|completed`, notes (up to 500 characters), nullable
startDate/endDate/dueDate, and children. The full tree permits at most 32 nodes,
three levels and unique UUIDs. End must not precede start when both are present.
Child completion does not complete the parent card or create a progress log.
Assigning Leam records responsibility only; it grants no capabilities and does
not start execution, coding, reminders or automatic follow-up.

POST `/api/commitments/{id}/subtasks` accepts the card `revision`, `action`
(`add|edit|remove`), `subtaskId`, and only the changed known child fields. Addition
requires a title and may include an existing `parentId`; other actions cannot
reparent. Edit preserves omitted fields and every unrelated node. Remove accepts
only revision/action/subtaskId and removes that exact subtree. The response is the
updated card with incremented revision. Missing identities return 404, stale
revisions return 409, and invalid structures return 422 or a domain conflict.
Mutation and any proposal receipt commit atomically.

The same input plus card `id` is exposed as proposal operation
`commitment.subtask`. Generic tool proposals remain manual. Trusted Today/Goals
origins honor the existing approval settings; the browser cannot assert trusted
origin. Approved retries return the existing receipt. User decline/supersession
and stale review protection retain the existing proposal semantics.

Today reconciliation receives bounded canonical board names and card structure.
It resolves readable capacity names to existing IDs, asks when the target is
ambiguous, and emits small child add/edit findings rather than replacing a tree.
Additions receive deterministic IDs; revisions and proposal receipts remain the
mutation authority. A pending new card must be approved before editing its
children. Extraction uses the existing single bounded semantic check, not an
extra routing call; provider mistakes remain possible and are not proof of user
acceptance. No external calendar write occurs.

Rollback must retain validators compatible with these additive stored fields;
older binaries may reject edits to enriched cards. Do not strip user fields to
make rollback pass. There is no destructive migration.
