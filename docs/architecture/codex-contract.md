# Codex bridge contract

Backend: installed Codex CLI 0.155.1 App Server over private stdio. No browser receives Codex credentials or direct access to its control socket. External access goes through authenticated Leam sessions and an exact Origin allowlist.

## Ownership
Codex owns thread history, execution, tools, approvals and goal state. Leam stores thread references, replayable transport notifications and durable submission reservations. It does not edit Codex rollout files. Existing thread continuation uses thread/resume, never transcript impersonation. A user explicitly hands off after the original client's turn finishes; concurrent writers across independent Codex processes are not yet supported.

## Lifecycle
Read/history requests do not load a thread for execution. Explicit connect resumes it. Bindings are associated with one initialized transport generation; restart requires reconnection. Every logical send has an idempotency key reserved before dispatch. Unknown outcomes remain pending and must be reconciled with authoritative thread state rather than automatically resent. Browser retries retain their original submission ID and draft across reloads in session storage. Explicit reconciliation looks for the client message ID in authoritative Codex turns. Absence is still uncertain: the user can inspect history and set the draft aside, without resending it. Sign-out clears private browser drafts.

Server request identifiers are random Leam UUIDs, privately mapped to an upstream request ID. A stale browser approval cannot resolve a new action after process/API restart. Allow-once, decline/cancel and structured questions are supported. Other request types are explicitly unsupported until corresponding deliberate UI flows are implemented. Submitted responses remain pending until Codex resolves them.

## Verification so far
- Actual installed App Server initializes and reads this existing thread and active goal.
- Live browser tests at 390x844 and 1440x1000 display actual thread history and goal, test commitment persistence and sign-out access revocation.
- API/transport regressions cover authentication, Origin, chunked body limit, repeat setup rejection, conflicts, duplicate sends, failed initialization, dead readers and stale approval IDs.
- A separate real Codex session has executed a workspace read tool and rendered its response through the authenticated phone-sized Leam UI.
- No turn has been sent into the currently active build thread by the bridge. Exact-session interactive handoff is pending and cannot be claimed from read-only evidence.

## Browser recovery
Streaming messages are keyed by turn and item. Async navigation cannot redirect a submission or pagination response into another thread. Ordinary Coding streams replay from the selected thread’s latest persisted `turn/started` within the last4096 event IDs. The authenticated `/api/events?thread_id=…` parameter selects that replay boundary; it is not a snapshot watermark or a thread filter. An explicit `after` keeps its existing behavior, and `Last-Event-ID` takes precedence on reconnect. Numeric event IDs suppress repeated packets. Active text is constructed from start/item/delta events alone, tool starts/completions remain visible, and incomplete snapshots cannot erase live projection. Terminal history replaces the partial exactly and is published together with current execution metadata before a voice reply can become final. If the bounded window has no retained start, one-second coalesced snapshot refreshes provide the safe fallback. Shared-owner events retain their separate coalesced snapshot path. The service worker caches only the public shell; authenticated information is unavailable offline. New sessions use an existing absolute workspace directory and inherit Codex configuration.


Local `Store.event` commits wake authenticated SSE listeners immediately using
loop-owned futures and thread-safe notification. Waiters register before reading
the durable cursor, so publication between an empty read and waiting is not lost.
SQLite IDs and stored payloads remain authoritative; notifications carry no data.
Writers in another process or Store instance are discovered by the retained
two-second fallback, which also bounds keepalive and session checks. Sessions are
revalidated after waiting and before emitting records, and disconnect/cancellation
unregisters waiters. This applies to ordinary Codex events and the existing compact
shared-owner notifications without changing either transport or execution rights.

## Required engineering policy
Every new coding turn carries `additionalContext["leam.agent-protocols"]` with
`kind: application` (installed Codex 0.155.1 schema). It identifies the reviewed
package's source, commit, version, lock hash and absolute path, and requires the
appropriate substantive/minor protocol plus target-repository instructions and
applicable overlays. Coding delegates must inherit the same policy. User text and
existing thread developer instructions are preserved; no automatic vendoring or
repository edits occur. This policy applies to coding, not companion conversation.

Leam checks all package-owned files against agent-protocols.lock.json before new
dispatch. Missing, malformed or modified policy blocks dispatch with HTTP 503 and
creates no submission reservation. Completed receipts remain retrievable even if
policy files subsequently become unavailable. A transport generation check after
policy validation and immediately before writing prevents a restart from bypassing
session handoff. Only a proven pre-write rejection releases a reservation; uncertain
outcomes remain pending for reconciliation.

The policy is an instruction delivery and integrity guarantee, not proof that every
future agent action complies. Tests verify the actual HTTP caller and transport;
a live phone-sized browser test verifies a real model reading the supplied package
with a tool and reporting its version. Acceptance still requires inspecting work
against the relevant protocol gates.

## Deployment ticket conversations

Each current Update can open a collapsed direct Codex chat. Opening the panel only
reads its saved association; the first explicit send creates a dedicated thread in
`LEAM_CODING_WORKSPACE`. The configured workspace must be an existing absolute source
checkout, not the immutable runtime release. Subsequent explicit sends reconnect
that dedicated thread when required. No ticket takes over the original shared build
session. Associations use the existing settings namespace, not copied transcripts or
new database tables; private backups retain them.

Every ticket turn uses the existing authenticated `/codex/threads/{id}/turns` caller
and its idempotent receipt/policy validation. The raw user message is unchanged.
`additionalContext["leam.update-ticket"]` supplies bounded ticket identity, deployment,
QA/UAT metadata and a read-only running-source reference alongside agent-protocols.
Ticket summaries/review notes are explicitly untrusted quoted data. Conversation does
not change UAT. Thread-creation uncertainty is retained rather than silently creating
another thread; uncertain messages use existing receipt reconciliation, never automatic
resend. A missing workspace or damaged protocol package fails clearly.

Ticket chat accepts follow-ups while a reply is active. The composer captures the
displayed active turn as optional `expectedTurnId`; ordinary submission uses native
`turn/steer` with that precondition, unchanged input, client message ID and policy/
attachment context. It never starts another turn automatically after steering fails.
The installed 0.155.1 experimental schema supports these fields. Steering returns
`turnId` without another `turn/started`; Leam normalizes its receipt to the existing
turn shape with `operation: steer`. Settings and approvals remain with the same turn.

The target is included in the request fingerprint and persisted browser draft,
including after collapse/reload. Reconciliation requires both its exact turn and
canonical user client ID. Only JSON-RPC invalid-request/method/parameters responses
are definitive admission rejections: release that unused reservation, keep the draft,
refresh history, and require another explicit send. Connection/internal errors and
unmatched receipts remain uncertain and are never resent automatically. This is
active-turn steering, not a second queued job. Existing start-request fingerprints
and the original shared IDE owner path remain compatible.

The panel reuses the shared VoiceComposer and finalized matching-turn correlation;
closing unmounts speech capture/playback and transport listeners. This increment shows
text/history/activity inline. Questions or approvals currently direct the user to the
same dedicated session in Coding; no implicit permission is granted by the ticket chat.

Unsent typed/dictated ticket drafts use the private session-storage namespace and
survive collapse/reload before a Codex thread exists; explicit sign-out clears them.
An accepted receipt clears only its exact matching draft, never newly edited text.
Live item deltas render before completion. Event cursors prevent initial event gaps,
replayed IDs are ignored, and snapshot revision fencing plus active projections keep
stale history from erasing partials. Terminal history remains authoritative. Metadata
and QA details are collapsed, except failed QA; UAT actions stay visible.

Ticket-level pending intent (UUID plus exact text) is persisted before connection or
any other await, then aliased to the normal thread receipt namespace. Reopening during
connection reuses that intent. An unmounted sender cannot start a turn; accepted results
clear only matching intent IDs and notify another open panel without erasing newer text.
The explicit saved-message retry checks its original receipt and preserves a newer draft.

A partial snapshot cannot establish an event watermark. Ticket status therefore searches
at most the latest4096 persisted event IDs for this thread's latest turn/started, and
replays from just before it. Active agent text resets at that start while user items stay;
only initialized turns append deltas. Without a retained start, ignored deltas trigger
snapshot reconciliation instead. Replayed starts/deltas never downgrade terminal history.

## New-session model defaults

`GET/POST /api/settings/coding-model` reads/saves Leam's separate default selection
(`model`, `reasoningEffort`) for **new** ordinary Coding sessions and dedicated
Update ticket sessions. Missing stored preference means `gpt-5.6-sol` / `medium`.
The existing owner session and trusted-Origin middleware applies. The account's
bounded live `model/list` catalog validates the exact model/effort pair before save
and before creation; disappearance fails without fallback or ticket reservation.
Storage uses the existing settings table, so backups need no schema amendment.

Both creation callers send explicit `thread/start.model` and
`thread/start.config.model_reasoning_effort`. No global Codex config is written.
A resumed/existing session—including the original shared IDE conversation—retains
its own model and effort. Companion model/provider selection remains independent.
Source caller tests and an isolated installed-CLI ephemeral start verify the exact
parameters; no real model turn or original-session mutation is needed for that proof.

## Exact stop for the shared IDE turn
The shared owner exposes a separately decoded v4 interrupt acknowledgment.
`POST /api/codex/shared/interrupt` takes the displayed generation and exact turn ID.
Leam deduplicates that binding/turn intent in durable receipts; a lost reply never
causes automatic dispatch again. The pinned owner receives expectedTurnId and may
return null when the turn has already changed. There is no unscoped fallback.
The operation also stops child agents but leaves the goal active. The UI labels
this explicitly and preserves the separate steering input. Source/synthetic socket
acceptance does not prove actual original-owner interruption; user UAT remains
pending until deliberately exercised. Questions and approvals remain pending.

## Shared questions and narrow approvals
The pinned IDE owner exposes v1 command/file approval and user-input methods.
Leam presents opaque capabilities bound to the current owner/generation, typed native
request ID, method, complete parameters and complete review data. Reordering never
rebinds a capability; removal/change leaves a tombstone. The server rechecks after
installation/owner awaits and before dispatch. Native transport has no atomic request
parameter-fingerprint guard, so Leam cannot promise one against concurrent owner edits.

Only ordinary questions and allow-once/decline approvals are enabled. Command text
and cwd must be complete; file changes must come from the matching raw owner item,
including canonical turnHistory entries, never the truncated chat projection. Broad
file grantRoot (potential session grant), secret/complex questions, stdin/network
permissions, MCP and other request types remain original-window-only. Oversized or
missing review data is refused rather than truncated into an approval.

Durable decisions bind owner and complete request/review identity independently of
reconnect generation. Two browsers, reloads and lost acknowledgments cannot cause an
automatic second dispatch. Reused identical native requests after a recorded response
conservatively require the original window. A native ok:true means submitted, not
executed; disappearance means only no longer pending and may follow a desktop answer.
Opaque capability maps expire on binding changes/restart; decision receipts persist in
the existing requests store and are covered by product-state backups. Actual owner
positive question/approval acceptance remains pending deliberate UAT.

`GET /api/codex/shared-thread` returns `{configured, thread}` from the local optional
IDE-owner binding. `thread` is null when unconfigured, otherwise the same metadata
used in the shared list row, excluding turns. Auth and no-store apply. This endpoint
starts no native RPC or IDE watch and can return while native session discovery is
pending. Clients may fetch it independently and merge by thread ID; native catalog
pagination, unavailable-state handling and existing rows must remain intact.

## Main implementation coordinator

`GET/PUT /api/coding/main` exposes a user-selected exact Codex thread ID, native or
shared-owner transport, and compare-and-set revision in Leam's settings store.
No thread is selected from its name or guessed from the current build. Selection
validates native identity or the configured shared reference. Main is marked in
Coding and protected from deletion, including ancestor deletion. Deliberate
reassignment requires the displayed revision and does not stop prior work.

`POST /api/coding/main/handoffs` is an explicit implementation action in Coding and
Updates. It carries the selected Main identity/revision, UUID, source thread/ticket,
user-entered task and at most 2000 characters of context. The server verifies source
references and adds bounded ticket provenance; no transcript or model inference is
used for routing. Companion's reviewed coding action also targets Main when selected;
its review captures the exact Main reference. Without a Main selection, existing
Companion dedicated-session behavior remains. Ordinary chat stays in its source.

Main must already be explicitly connected for native execution. Native active turns
receive `turn/steer` with the observed turn ID; idle turns use `turn/start`. Shared
Main goes through the current owner/generation adapter, never native resume or rollout
writes. Existing dispatch callers retain pinned protocol validation and idempotent
receipts. Selection and handoff dispatch serialize; receipts retain their original
target across reassignment. Retrying the same handoff only reads its underlying
receipt; unknown outcomes are never automatically dispatched again.
`GET /api/coding/main/handoffs/{requestId}` reports accepted, uncertain, not_sent or
not_recorded. Accepted means admitted to Codex (possibly an active-turn follow-up),
not implementation completion or a separate queued job. A saved browser intent uses
private session storage and is cleared on sign-out.

Secondary sessions receive instructions to discuss/inspect/propose and ask for an
explicit Main handoff before implementation, integration or deployment. Main may
coordinate bounded parallel workers through its existing native delegation tools;
there is no Leam worker-grant API and source text confers no privilege. This is an
instruction policy, **not a permission sandbox**. Active native steering cannot
change permissions, shared-owner permissions are outside this adapter, and native
filesystem read-only settings alone do not prove MCP/custom-tool safety. Universal
execution/deployment enforcement remains follow-up work; no tool grant or host
configuration is changed by selecting Main. The UI explains this in collapsed help.
An explicit `POST /api/coding/main/handoffs/{requestId}/reconcile` checks the existing
native/shared exact-message reconciliation caller using the saved submitted task;
it never resends. A changed original owner transport blocks reconciliation rather
than falling back to native ownership. Only the handoff task is persisted, not its
source conversation transcript.

## Explicit model changes in composers

The shared composer exposes a compact Model and reasoning dialog, including ticket
chats. `GET /api/codex/threads/{id}/model` reports the exact thread's configured
`model`, `reasoningEffort`, `generation` and `connected` status. Missing settings
stay unknown, not inferred from new-session defaults. The authenticated,
Origin-protected POST takes model/effort, displayed generation, expectedModel and
expectedReasoningEffort. Only already-connected sessions are writable. Validation
uses the bounded live catalog, then rechecks ownership/configuration after I/O.

Native changes use installed experimental `thread/settings/update` with only
threadId/model/effort and generation fencing under the session lock. Shared IDE
changes use version-pinned `thread-follower-update-thread-settings` v2 with exact
owner and conditional old-model/effort checks. activeTurnId is omitted so the
owner updates subsequent-turn settings rather than live permissions. No implicit
resume, new thread, global config, permission change or default fallback occurs.
The owner may maintain next-turn settings locally when its older native app server
does not implement the experimental method; this is the owner's existing behavior.

Acknowledgment (`accepted`) and canonical observed configuration (`confirmed`)
remain separate. Unknown delivery/readback requires refresh and never triggers an
automatic repeat. Running replies keep their current model. Native bindings cannot
atomically exclude edits made by an unrelated external writer; the existing explicit
handoff/exclusive-owner contract remains necessary. Shared owner conditions are
checked by the pinned owner itself.

Companion/Today/Goals composer controls explicitly label their existing provider
selection as shared defaults for future requests, not per-thread overrides. Models
and efforts come from the actual account catalog. Providers without an exposed
catalog remain configurable through the existing Settings UI. Dialogs fetch on
opening and preserve drafts; routine chat renders add no model-catalog polling.
