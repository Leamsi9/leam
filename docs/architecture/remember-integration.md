# Remember integration assessment

Source inspected: `/home/user/Github/Ezra/remember-app` (README, app.js, index.html, server.py, manifest and service worker). Original files and SQLite user data remain untouched.

## Preserve the product model
Remember's strongest contribution is a positive commitments model: capacities represent areas of life; objectives have boolean/count/minutes measures, targets, date windows, time, rewards and notes. Daily logs record measured value separately from explicit completion. Toggling completion preserves a previous partial value. This distinction matters: reaching a numeric target and deliberately marking something done are not identical actions.

Leam should expose this through Today, capacity pages, objective editing, progress history, snoozing and an optional reward record. The companion should operate on the same domain commands as the UI, and reference current commitments rather than duplicate their state in a prompt. A commitment is a deliberate user decision; extracting a possible task from conversation must not silently make it an accepted commitment.

## Reengineer durability
The source uses localStorage state, debounced whole-document account synchronization, and browser timer checks for reminders. Those choices suit a small standalone app but cannot provide reliable multi-device edits or reminders when the browser is closed. Leam uses revision-checked domain records, per-day logs, durable scheduling, delivery receipts and explicit device notification permission. Recurrence needs a named timezone, daylight-saving rules and date-based idempotency. Snoozing changes the durable next delivery, not merely a browser timeout.

Retain separate task/habit/goal concepts, with capacities as optional organization. Do not copy password hashes, session handling, or whole-state overwrite synchronization. Remember can remain independently usable; a narrow import/export adapter is preferable to embedding its original server.

## Migration contract
Offer preview before importing a Remember JSON export: counts, validation errors, date/timezone assumptions and source provenance. Stable source IDs make re-import idempotent. Preserve capacities, objectives, daily logs, notes, rewards and partial progress; never silently drop unknown fields. Import must be transactional and reversible from a pre-import backup. Validate export/import round trips with synthetic data before offering a user-data migration. No real Remember account data has been read or migrated.

## Acceptance still required
- Numeric and boolean daily progress, undo and concurrent edits work through phone and desktop UI.
- Date-window and timezone boundaries, including DST, select the right daily log.
- Reminders survive browser closure and service restart; duplicate delivery is controlled and failed delivery is visible.
- Remember import preview, provenance, repeat import and rollback are tested.
- Conversation and UI actions use one authoritative commitments service.

The initial candidate currently contains only basic task creation/completion. The richer model described here remains implementation scope, not a claim of shipped functionality.
