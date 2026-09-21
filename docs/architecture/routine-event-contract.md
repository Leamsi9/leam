# Routine event input v1

This Leam-owned interface is separate from existing wall-clock routines. It has no
model, native-sensor, research, webhook-action or arbitrary evaluation dependency.
All routes require the existing owner session; mutations also require an allowed
Origin. No browser-visible service key or authentication exception is introduced.
Source names are declared by that owner, not verified device/connector provenance.

## Input and discovery

`GET /api/routine-events/capabilities` reports versions, actions and limits.
`POST /api/routine-events` accepts this bounded v1 shape (unknown fields rejected):

```json
{
  "version": 1,
  "requestId": "172bb4e7-6fa4-4920-a44f-1ab0ddfd96fe",
  "source": "calendar",
  "type": "meeting.ended",
  "occurredAt": "2026-09-21T10:00:00+01:00",
  "attributes": {"place": "home", "durationMinutes": 30}
}
```

Use a new UUID for a new input. Retry uncertainty with the unchanged body and UUID;
changing any normalized input under an existing identity returns 409. Receipt replay
precedes freshness/rate checks so a prior accepted input remains retryable. New
inputs must be within the past 24 hours / next 5 minutes of server time; occurredAt must include a
timezone. Names are 1–80 ASCII identifier characters (letters/numbers, `_ . : -`,
starting with a letter/number). Up to 16 scalar attributes: keys 1–48 ASCII letters,
numbers, `_ . -`; strings max 256 characters, integers within JSON's exact safe range,
finite numbers, booleans or null; no nested data. Attribute encoding max 8192 bytes.
At most 60 new inputs per minute. Invalid input 422, identity/revision/capacity conflict
409, intake rate limit 429. No model or arbitrary action runs on receipt.

An accepted response includes `version`, `eventId`, server `receivedAt`, created
notification IDs in `deliveries`, and matching rules suppressed by cooldown in
`suppressed` (rule ID, revision, reason). The record retains the input, timestamps,
owner-session provenance and receipt. Global event stream `routine.event.accepted`
emits only the receipt summary, not raw attributes.

## Rules and notifications

`GET/POST /api/routine-event-rules`; `PUT /api/routine-event-rules/{uuid}` edits with
an exact `revision`. Configuration: title, static message, source, eventType,
optional `match: {field, equals}`, cooldownSeconds 0–86400 (default 60), enabled(default
true), action=`notification`. Match uses exact source/type and optional exact JSON
scalar equality: true is distinct from 1, null differs from absent, and no wildcard,
coercion, template or expression executes. Max 50 rules; existing rules can be
edited/paused. Edits preserve last-delivery time; config revisions do not change
when an event executes. Cooldown uses server receipt time, not caller time.

`GET /api/routine-event-notifications` lists waiting notifications (limit 1–100,
default 50), each with immutable rule revision/title/message/source/event links.
`POST /api/routine-event-notifications/{uuid}/dismiss` is idempotent. At most 1000
waiting notifications; overflow rejects the entire event and rolls back all partial
notifications/cooldowns/receipt. Dismissal does not undo the source event.

`GET /api/routine-events?limit=50&offset=0` inspects recent records, reports total and
nextOffset. Latest 1000 input records and 1000 dismissed notifications retained;
waiting notifications are never silently pruned. Durable idempotent receipts remain
in the existing requests table, including after history pruning. Offset browsing is
a recent-history view, not a frozen snapshot; refresh if input arrives while paging.

## Persistence and integration

Rules, event records and notifications use dedicated kinds in existing `entities`;
receipts use `requests`. A single BEGIN IMMEDIATE transaction records all effects.
Cross-kind entity identities cannot overwrite other product state. No new table or
key file, no worker, no backup schema amendment. Isolated backup/restore proves rules,
notification and request receipts survive. Existing scheduled routines unchanged.

Root integration (after app Store initialization, beside the existing routine router
and BEFORE the optional static-file mount):

```python
from .routine_events import EventInputs
from .routine_events import router as event_inputs_router
app.include_router(event_inputs_router(EventInputs(store)))
```

The parent app's actual middleware remains the auth/origin authority. The module
router alone is not an authentication boundary. Root should regenerate the aggregate
OpenAPI snapshot after wiring this and other concurrently owned routers.
`RoutinesPanel` includes `EventRulesPanel`; existing `RoutineInbox` also includes
`EventNotifications`, so reminders appear in Today and Routines. UI supports rule
create/edit/pause, exact retry, input history/provenance and notification dismissal.
The labelled manual test input is a real event and may create reminders.

Future connectors must adopt this versioned envelope and explicit credential scope;
dedicated revocable ingress tokens and native sensors are not implemented here.
