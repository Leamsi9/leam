# Capacity board views

Today → Boards reads `/api/capacities` and `/api/commitments`, independently of the selected agenda date. These are the same capacity and commitment IDs displayed in Goals, not a copied task store. All boards and Unassigned filters support list, Kanban and Gantt views. Board creation uses the existing capacity create operation; full capacity notes/records and removal remain in Goals.

Card details edit title, notes, capacity, owner, priority, explicit dates and board status. Existing kind, measure, targets, reminders, rewards and progress logs remain untouched. Completed and paused override stage; moving to To do/In progress/Blocked sets lifecycle active with that stage. This is a lifecycle operation, distinct from Plan's daily progress controls. Leam ownership describes responsibility and never dispatches coding or other work.

Every edit sends the displayed canonical revision. A 409 preserves the draft, disables further writes and offers explicit replacement with the latest record. There is no silent rebase or automatic mutation retry. New board/card requests have no server idempotency receipt: network, response-decoding, timeout or server failures are visibly uncertain. The in-memory draft cannot be resubmitted, including after closing/reopening; explicit inspection refreshes canonical records without matching by title. Discarding the creation draft does not delete a potentially saved record. Reloading/leaving Today ends this in-memory guard, so inspect saved records before making a separate creation. Pending board fields are disabled. Parent-held editor generations fence late responses: discarding/replacing a creation draft invalidates its previous callbacks, and a saved card cannot close an unrelated editor. Card and subtask drafts remain in memory when closing the dialog or changing Today pages; leaving Today/authentication discards this component state. Subtask operations carry exact card/subtask/parent IDs and latest acknowledged card revision. Removing a subtree requires confirmation. Parent and child completion are independent. The backend enforces maximum tree size/depth and date validation.

Gantt uses civil ISO dates without timezone conversion. A duration is drawn only when both planned start and end exist; individual dates are markers, with deadlines labelled separately. Missing endpoints are never invented. Undated cards appear in Unscheduled. Timeline date labels are the accessible source; decorative geometry is hidden from assistive technology. Kanban supports ordinary labelled controls without requiring dragging. Horizontal columns scroll inside the page on phones.

Canonical contract source: `leam_api/commitments.py` in the capacity-board domain increment. Browser callers in `apps/web/tests/capacity-boards.spec.ts` cover operation payloads/revisions, paused vs blocked, ownership, nested subtask mutations, conflicts, unscheduled dates, creation, refresh failure and 360/1280px dialogs. Authenticated live behavior is accepted only after deployment identity and those callers are verified; fixtures are not a claim of physical user acceptance.

Confirmed Board receipts invalidate Today's agenda projection and, for capacity
changes, its label list. Dirty visible projections refresh together; clean local
navigation retains cached state. Plan progress and confirmed Today reconciliation
invalidate the Board cache. Pre-receipt reads cannot overwrite post-receipt views;
record revisions deduplicate receipt invalidations. Explicit inspection of an
uncertain create can refresh canonical views but does not claim that the create
succeeded or retry it. This frontend coherence does not provide realtime external
device updates or invent daily progress for a lifecycle-only completion.

All boards uses collapsible capacity groups in each representation, preserving the
global filter. Group counts include parent cards only. Two populated groups open
by default; selecting a capacity opens that group. Collapsed state is transient.
A fixed palette derived from canonical capacity identity supplies stable decorative
accents; colours may repeat and are distinct from semantic status indicators.
Titles remain visible and unresolved capacity references are retained as unavailable.
Today tab order is a separate browser-local enum-only preference, edited through
Day options using touch/pointer handles or labelled move controls. It does not
change the selected page/date, domain membership, Goals navigation or chat lifecycle.

Cards now start collapsed, with a one-click title editor, owner/status cue and
44px expansion/move targets. Details, notes, dates, priority and subtask metadata
are expandable. Manual order is canonical per capacity, shared by List, Kanban
and Gantt. Dragging and keyboard/button alternatives move within the current lane
in Kanban; they never change status, priority, daily progress or capacity.

`GET /api/commitments` adds `boardOrders` keyed by capacity ID (`unassigned` for
null): `{capacityId, revision, membershipToken, ids, canReorder}`. Cards and order
come from the same snapshot. `PUT /api/commitments/order` takes
`{requestId, capacityId, revision, membershipToken, cardId, beforeId, lane}`;
`requestId` is a UUID, `beforeId` may be null (end), and `lane` is optional. An
atomic transaction checks current membership/card revisions, order revision and
lane, preserving the relative order of every other card. New members append;
removed/moved members are omitted. Settings hold order IDs/revisions and existing
request receipts make exact retries idempotent. No new table or migration is
needed. Ordering supports up to 1,000 cards per capacity; larger groups remain
readable. Conflicts require refresh; uncertain responses allow inspection or an
explicit same-request retry, never an automatic resend. Revision/GET fences keep
older reads or receipts from replacing a newer acknowledged order.
