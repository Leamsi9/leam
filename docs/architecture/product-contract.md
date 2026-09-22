# Leam product and acceptance contract

## Companion recovery feedback
Response-local model recovery displays only typed runtime retry reason/count,
recorded delay and elapsed-at-decision evidence. A reserved retry is not proof of
provider activity or exhaustion; canonical terminal status is separate. After a
failed latest run the user may prepare its request as a draft, preserving existing
drafts and requiring explicit Send. User-invoked diagnosis prepares a pending
coding.handoff proposal for existing Approvals/Main review, with only technical
enum/numeric evidence and source IDs. It never sends coding work automatically.

## Chat and administrative status
Today reconciliation status, history and exact retry controls live in Plan, using
the selected date/timezone's existing day-thread binding. Merely reading Plan
never creates a chat. Check failures, pending review and clarification remain
indicated while their details are collapsed. Companion/Today transcripts contain
no administrative check panel or Approvals footer; More → Approvals and its global
unread badge remain authoritative navigation. Message delivery failures, Stop and
current tool/input feedback remain part of the conversation. Removing chat status
observation does not stop the durable backend reconciliation worker.

## Ownership
Leam owns personal identity/preferences, cross-domain context and continuity, commitments/capacities/objectives/daily logs, Today, account synchronization views, deterministic reminders, contextual routines, and administration. Its PWA supports all implemented product and administrative operations on mobile and desktop. Specialized applications own authoritative domain details. IronClaw owns runtime execution via an adapter. Codex owns coding sessions/tools through App Server; messages in the coding surface are direct, not rewritten by another model. Every Leam-invoked coding task must receive the user's reviewed agent-protocols package, choose the applicable substantive/minor protocol, preserve repository instructions and propagate that policy to any coding delegates. Missing or modified pinned protocols must block new dispatch.

## Initial boundaries
New repository with separate packages/processes, not pervasive microservices. Independent recovery service must survive main process failure. Wake from host sleep requires another always-on reachable system and cannot be claimed with only a same-host service. Existing deployments and account stores remain untouched until migration is rehearsed and cutover approved.

## Scope
First milestone: PWA shell and authenticated Codex bridge that reads/resumes the existing thread configured by `LEAM_SHARED_CODEX_THREAD`, streams turns, renders server questions and approvals, allows interruption, exposes goal/workspace, and reconnects safely. Session originated in VS Code Codex 0.154.0-alpha.6.2; installed CLI 0.155.1. Verify tool/config availability and avoid duplicate writers.

Then: pinned IronClaw 1.4 runtime, companion continuity, relational commitments, Today, Google and Microsoft connected account workflows, reminders/push, model configuration, budgets, routines/events, open-source decision backend, backup/restore, operational controls and full mobile settings. Research app deferred until everything else is complete. Native mobile sensing deferred; expose versioned event inputs.

## Remember preservation
Reviewed Ezra remember-app: capacities with notes/records; objectives linked to capacities with boolean/count/minutes targets, date ranges, reminder times, rewards/notes; per-day logs, snoozes and completion undo. Preserve those concepts and any existing provenance. Replace whole-state overwrite sync, browser-local authoritative storage, browser timers and prompt-based editing. Never modify Ezra's SQLite source or copy its accounts/passwords into Leam. User scope amendment 2026-09-21: Remember import is not required for the MVP and is removed from Settings. Retained import backend is compatibility code; no existing records are removed.

## Decision backend
Open-source only; code and weights licenses verified separately. Versioned choice/score interface with provider adapters, abstention, backend-specific confidence and deadlines. Shadow evaluation before consequential routing. Never override permissions or silently downgrade local-only constraints.

## Required acceptance
1. Configure and operate supported workflows from phone-sized viewport and desktop.
2. Continue the existing Codex thread through Leam, carry out a verified repository change, disconnect/reconnect without duplicate execution, and answer real server questions/approvals when required.
3. Conversation -> persisted commitment -> Today -> calendar action -> durable reminder -> recorded follow-through, including timezone/DST and restart.
4. Personal memory is inspectable/correctable and cited to source; project overview shows freshness and source.
5. Account connections and sync expose truthful freshness/reconnect states.
6. Main service recovery is independently reachable; supported host wake is proven on actual infrastructure or remains explicitly pending.
7. Backup restoration and update rollback are demonstrated, not merely documented.
8. Exact source/runtime/client identities and evidence timestamps bind acceptance to the deployed artifact.

No launch completion while an applicable acceptance criterion is pending. Missing credentials or hardware are recorded precisely and requested when needed; independent work continues.

## Current MVP scope amendments (2026-09-20)
Decision/Jev integration is paused by the user and excluded from this MVP; no clone
is selected. Preserve the replaceable interface idea for later work without
presenting exploratory code or a proposed backend as implemented.

Voice is in scope: browser speech adapters first, then correlated conversational
turns. See voice-contract.md. Leam should inspect and explain its actual model,
configuration, capabilities and operational state. Read-only inspection belongs in
the companion; supported administration uses explicit domain controls, and code or
infrastructure engineering is delegated through Codex with pinned agent-protocols.
Broader inspection and delegation are pending; a static architecture description
must not be presented as full runtime access.

## Chat layout contract
Coding, Companion and future chat surfaces prioritize the conversation: target
roughly 70% response, 20% composer and 10% collapsed metadata within the usable
chat viewport. More response space is welcome; exact ratios yield to accessible
controls, the onscreen keyboard and short-screen constraints. Keep the transcript
independently scrollable and the composer reachable without scrolling through
administration panels. Verify portrait, landscape and desktop geometry.

Use one compact toolbar. Session lists, coordinator selection, configuration,
paths and detailed status belong behind labelled buttons and dialogs; never add
permanent panels above replies. Icons require accessible names and discoverable
help. Dialogs preserve drafts, restore focus, show errors inside the dialog, and
report successful actions without encouraging duplicate submissions. Important
failures and active response controls remain visible. Extend these shared patterns
instead of introducing a new control panel for each feature.

## Coding display continuity
Coding renders each local submission before receipt lookup or dispatch, labels sending, accepted and uncertain outcomes separately, and retires the display copy only when an authoritative user item carries the exact request/client identity. Equal text or an existing turn ID cannot prove delivery. No reload or reconnect automatically resends a pending submission.

Authenticated Coding display snapshots may survive deliberate reload in session storage: at most four entries, 1 MiB aggregate, 512 KiB per entry, and ten minutes old. Writes coalesce for one second with a pagehide flush. Restored snapshots are unvalidated and read-only until fresh server state arrives; authentication loss clears them. Last-good shared history remains visible during a disconnected empty refresh. Explicit removal or replacement of the shared binding retires stale catalog rows and prevents reconnect/send through that former binding.

## Read-only email
Google mailbox access is a separate per-account opt-in from calendar access. Its fresh PKCE/session-bound consent is matched to the original account and current OAuth client; the actual token response must grant `gmail.readonly` and renewable access. Mail credentials occupy a separate encrypted account slot so declining email or losing mail access does not replace calendar credentials. Incremental Google authorization can include previously granted scopes; Leam exposes only GET message reads through this adapter.

Email synchronization is explicit: normal Sync mail reads at most 20 messages from the inbox in the last 30 days. Explicit Rebuild action inbox may read one list page of at most 100 recent messages per selected connected mailbox so Leam can filter before capping Today at 20 actions. Both retain only selected From/Subject headers, snippets, timestamps, labels and bounded thread metadata. Bodies and attachments are not requested or retained. Gmail's unread/important labels are source metadata, not a model priority decision. A saved snapshot is encrypted, atomically replaced only after a successful bounded read, and includes freshness, errors and truncation. It becomes stale after 15 minutes. GET `/api/email` reads local snapshots only; missing consent, failed sync and a genuinely empty successful snapshot remain distinct. The source remains authoritative and all message text is untrusted data.

Local email removal deletes the current mailbox credential and snapshot while preserving the calendar account. Disconnecting the whole account cascades snapshot deletion. Neither operation revokes the Google grant; older backups are a separate retention boundary. There is no email sending, archiving, label mutation, mark-read action, provider deletion or automatic extraction into memory. Explicit triage and successful mail synchronization can start bounded tool-free classification with the selected main model; its encrypted source-bound decisions are advisory and confer no action authority. Enable Gmail API on the configured Google OAuth project before synchronizing. Live consent/provider acceptance is separate from controlled fixture tests.


## Coding catalog identity and removal

The native active-session catalog uses descending `recency_at`, preserves its
opaque cursors, and deduplicates only exact native thread IDs. Explicit ephemeral,
ambient-suggestion and subagent metadata is omitted; names, short histories and
unknown origins are never heuristically hidden. Same-title different-ID sessions
remain separate and display their short IDs. All Codex sessions remains the
default. The optional Leam view filters loaded pages by exact saved purpose links
or native creation-client identity; it does not claim an exhaustive separate index.

Updates-ticket links and the configured shared owner are visibly protected from
deletion, with server enforcement unchanged. Ticket titles remain editable.
Preflight failure states that no delete was sent; unsupported native deletion and
unconfirmed dispatched deletion remain distinct. No automatic retry, archive, bulk
cleanup or user-history migration occurs. CLI/IDE catalog differences require their
own source/filter/index evidence and are not assumed from similar titles.

## Usage accounting
Settings → Usage & efficiency records numeric metadata without model inference. Native Codex own-thread response identities are deduplicated; copied fork history and cumulative snapshots are never added as independent charges. Cache and reasoning counters are subsets; conflicting finalized evidence is quarantined. Historical/current-state import has explicit missing-source coverage. Companion runtime attempts are captured separately from retained, owner-verified IronClaw inspector snapshots for exact Leam dispatch run receipts. Valid reported input/output enter the shared ledger once per scoped call identity, including failed attempts; cache stays a subset. Started attempts and unknown usage remain visible outside measured totals. Provider identity, reasoning counts, isolated auxiliary completions, unacknowledged/deferred run identities and evicted diagnostics remain explicit coverage gaps. Collection never invokes a model or stores prompt/tool/failure text. Goal assignment is explicit and optional descendant propagation uses delegation edges, not fork ancestry. Daily warnings use UTC installation totals and do not enforce subscription quotas. Metadata exports preserve integer precision as decimal strings; prompts, tool contents and native paths are excluded. Collection may be paused without deleting existing measurements.

The native Codex session journey is a bounded, read-only reconstruction from installed native rollout records. `/api/usage/journey` pages metadata by session and optional turn; Usage Calls links to this on-demand view. Reported response tokens join the existing deduplicated ledger. Tool byte sizes are not token charges; inferred ownership is counted separately, hidden prompt stages and unreported retries remain unavailable. Historical cursor rescans are bounded and atomic with their derived rows, and backup validation recognizes the additive trace tables. This native view does not provide provider billing amounts. A separate on-demand IronClaw attempts view exposes captured runtime counters and unknowns.

## Private published artifacts

Reports and reviewed self-contained HTML studies can be explicitly published as
immutable private snapshots with `python -m leam_api.artifacts`. HTTPS links use
`/?artifact=<versioned-id>` and survive sign-in on another device. Authentication
is the existing installation session; no bearer credentials appear in links.
Artifact APIs never accept filesystem paths, and only explicit operator
publication makes a file reachable. Reports render without raw HTML or external
images. Interactive previews use an opaque sandbox and a restrictive response
policy, without account, form, popup or parent-navigation access. Downloads are
attachments; API responses are no-store and excluded from PWA caches. Publication
content is covered by the existing settings-store backup. Revocation is an
operator deletion of that artifact setting; backups retain their own lifetime.

## Main Coding selection

Implementation tasks use an explicitly selected Main Coding session and deliberate
Send to main action, preserving source ticket/thread references and concise context.
Main coordinates integration, deployment and bounded native worker delegation.
Secondary conversations remain usable for discussion and receive planning/handoff
instructions; this first slice does not enforce a universal Codex tool sandbox.
Selection, receipts and pinned protocol delivery are authoritative; no classifier,
extra routing inference, guessed session identity or transcript copying is involved.


## Today obligation checks

Every Today user turn submitted after reconciliation activation, plus in-flight
replies finalized after activation, receives a persisted user/thread/turn check.
Canonical scoped timeline finalization admits semantic extraction; stream frames
only wake a restart-safe worker. Completed historical exchanges are not mined.
The checker records waiting, queued, checking, done or failed independently of
chat delivery, with three bounded attempts, expiring leases and explicit retry.
Interrupted replies are not successful checks. Activation time and scan failures
are observable even before a completed turn can be classified.

The single-owner installation pins the runtime tenant/user identity and verifies
thread ownership before personal records are read. Source references, confidence
and sanitized outcomes are retained; duplicate chat and tool content is excluded
from the job ledger. Semantic detection remains fallible. Domain proposals retain
existing approval and revision controls; pending or executing proposals never
mean active tracking or confirmed completion. Approval receipts update check
outcomes without another classifier call. No reminder or future follow-up is
created by this workflow.

The paginated scanner commits opaque cursors between bounded four-page passes.
Source revalidation is limited to ten pages per attempt; unavailable or changed
sources produce a visible retryable failure. Additive tables participate in
backup validation and restore, and older archives remain compatible. Deployed
acceptance is distinct from source implementation and automated fixture evidence.


## Subscription exhaustion observations
Usage settings can record manual or user-reported ChatGPT exhaustion milestones
with an explicit observed timestamp, timezone and opaque source reference. Creating
an event requires the user's explicit attribution of collected Codex responses to
that subscription. Existing response records lack historic subscription identity;
the counter describes user-attributed observations, not provider-confirmed billing.
Companion runtime calls remain outside this ledger, including openai_codex.

Each event starts a derived half-open period ending at the next observation for the
same provider, installation subscription scope and usage source. Historical totals
are preserved; backfilled milestones and late response imports recompute periods.
Integer token counts remain decimal strings; cache and reasoning stay subsets.
Idempotency receipts and provider/source/timestamp uniqueness prevent retry duplicates.
These are observation/report times, not proof of the provider's exact exhaustion
instant. Remaining allowance and provider reset times are unknown. Automatic
subscription exhaustion detection is not connected in this increment; generic429,
auth failures, prompt text and model classification never create exhaustion events.

Companion send recovery distinguishes pre-dispatch failures from uncertain delivery.
Only absence of the exact durable action ID allows the server to return
`X-Leam-Action-Reserved: no`; a previously reserved retry remains uncertain.
The client keeps draft and attachments, never silently resends, and retries only
with the original action ID. Today reconciliation wakes from newly terminal run
identities, not streamed text chunks or keepalives; periodic scans preserve restart
recovery when stream hints are absent.

## Local email drafts

Editable drafts can be saved in Leam through authenticated `/api/email/drafts`
operations and the mediated `leam_email_draft` tool. Each stable UUID has a revision;
exact retried writes return their original result, and stale edits fail with409.
Draft bodies and retry fingerprints are authenticated-encrypted with the account
vault, including in backups. At most256 drafts are retained; list results are
bounded summaries. The account must belong to this installation and be Google;
removing a connection does not silently destroy its saved drafts. Removing a local
draft requires explicit confirmation and the current revision. A content-free UUID tombstone prevents delayed saves from resurrecting deleted drafts or resetting revisions. This does not
send, delete, mark read, or save drafts in Gmail, and does not expand OAuth scopes.
The response always identifies local storage. Email text remains untrusted data.

Today Inbox offers a separate, explicitly opened Mail browser. Opening lists local
mailbox connections; Search and message opening perform on-demand Gmail reads.
Search uses the selected account/query/cursor and is independent of Today date
and saved priority triage. Messages render plain text with revision-bound text
pagination and attachment metadata only. Local draft compose/list/edit/remove
uses the same encrypted domain operations as the companion tool, with exact
UUID/revision retries and explicit local removal confirmation. It has no Send or
Gmail-delete action and labels saves as Leam-only. Draft edits remain in memory
across dialog closure and Today pages, survive request errors, and clear on auth
loss; save before leaving Today. No draft body is copied to browser storage.

Backlog owner priority is separate from factual delivery assessments. Authenticated
`POST /api/backlog/order` accepts a UUID request ID, exact ordering revision and
visible assessment revisions, effective lane and complete reordered membership.
Atomic CAS and permanent compact receipts reject stale or mixed-lane changes and
make old retries non-reapplying. `GET /api/backlog` exposes ordering metadata at the
top level, preserving strict Assessment/CLI input compatibility. Operator full
reviews cannot overwrite user lane preference; stages/blockers and QA/UAT facts
remain unchanged. Compact cards expand for evidence and keyboard ordering; touch
and pointer grips reorder within the current lane only. Unknown saves retain their
original request identity for explicit retry; conflicts require refreshed choice.

Shared Coding distinguishes the IDE follower's transport from known native runtime
readiness. Public runtimeStatus is limited to idle/active/notLoaded/systemError or
unknown. Known unloaded/error snapshots keep history readable but disable dispatch
before reservation; fresh healthy snapshots restore readiness with a new binding
generation. Unknown legacy status retains existing behavior. No automatic resume,
resend or ownership transfer occurs. Raw owner error payloads remain private.

## Private action inbox

Today → Inbox combines actionable saved Gmail items with immutable private Leam notes, distinct from Approvals, commitments
and reminders. `create_inbox_item` shares the authenticated domain operation and
saves directly under its mediated tool permission; only a saved receipt proves
creation. Source actor is assigned by the server, not model/browser arguments.
Stable UUID retries preserve identity, canonical links resolve current titles,
removed links remain visibly unavailable, and reads never acknowledge. Explicit
mark-all uses an observed sequence so later items remain unread. Notes render as
plain text. The first slice exposes shared event-emission infrastructure but does
not subscribe to automated ticket changes; those actor-aware hooks remain pending.

Uploaded chat attachments also appear in Resources as tagged metadata references;
original upload bytes are not duplicated by publication. The existing private
preview/download/read/link controls apply. Acknowledged source receipts can link
canonical commitments or Update features; unknown provenance remains unknown.
Confirmed version-bound resource removal clears stored upload bytes, acknowledged
replay copies and verified local materializations while retaining deleted-file
placeholders in conversation history. Uncertain delivery or unconfirmed coding
completion blocks removal. Independent decoded upload quotas are128MiB/1000 active
files, not total SQLite metadata size; generated resources retain their own quota.
Backups and external model/download copies retain their independent lifetimes.

## Runtime tool approvals
Companion/Today/Goals show pending runtime approval within the affected response,
with collapsed plaintext operation arguments and Approve once/Decline. Runtime
retains thread/actor/current-gate authority; these controls never change tool
permission settings. Missing/truncated diagnostics disable approval and leave
Decline available. A pending decision is not an executed operation. UUID receipts
support exact retry after connection uncertainty; no raw arguments are persisted
in those receipts. Runtime approval is distinct from More → Approvals domain proposals.


Today Inbox reads notes from the canonical Inbox service and mail from the current
agenda snapshot. Leam notes never pass through mail classification. Source filters
identify Leam, Gmail and the connected account; collapsed cards share explicit
read/unread actions. Gmail read markers are Leam-only metadata, not Gmail labels,
triage approval, task completion or mail processing. They use verified saved
account/message source keys, have a5000-marker limit and fail visibly at capacity.
Mark-all acknowledges the observed note sequence and displayed mail identities;
subsequent arrivals remain unread. Legacy More Inbox links redirect to Today Inbox
and retain selected date/item; no records are migrated or deleted.

Build-ticket conversations use the stable feature identity across Backlog and
Updates. Existing deployment-specific conversations remain available separately;
no histories are merged or silently removed. Opening a card does not send a model
turn, approve implementation or change QA/UAT. Explicit Main handoffs validate the
same canonical binding. New publications retain exact-feature rationale/scope;
older receipts may display current rationale without changing acceptance records.
Board cards and capacity groups reuse the same item chat binding as Goals.


## Native dynamic request completion
The app-owned Codex transport retires only already-responded internal dynamic
requests when the native runtime confirms the exact thread and turn terminal.
External approvals, unanswered internal work and other/live turns remain pending.
A missing serverRequest/resolved notification is not required for dynamic tools;
model text never clears requests. Maintenance still waits for native idle threads,
unfinished response tasks and all other pending work. This lifecycle correction
does not itself repair an older running process or authorize cancelling work.
