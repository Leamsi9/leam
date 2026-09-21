# Shared IDE session compatibility boundary

`SharedSessionReader` separates Leam from private IDE IPC. `PrivateIdeReader` reads
an existing owner; it never creates/resumes a thread or writes shared Codex state.
The current implementation pins extension26.908.40401, its three relevant webview
bundles, extension host bundle and bundled CLI0.154.0-alpha.6.2 by exact SHA256.
Updating the extension fails closed until the new implementation is inspected.
Pinning disk artifacts plus message versions is compatibility evidence, not an
attested identity of JavaScript already loaded in a long-running extension host.

Only a mode0600 current-user Unix socket and matching SO_PEERCRED UID are accepted.
No router creation or recovery occurs when missing. Public methods permit only
initialize, owner discovery and transient following subscription/unsubscription.
The reader advertises no owner/execution request capabilities. Every operation has
one12second deadline,128incoming-frame limit,32MiB per-frame and48MiB total budgets;
outgoing frames are capped at64KiB. Owner loss, malformed data, size/version mismatch
or unavailable sockets fail closed; no automatic thread takeover or replay occurs.

Protocol facts are derived from the installed extension, not public App Server
assumptions: little-endian four-byte UTF8 JSON frames; initialize version0,
thread-owner-discovery version1, following-changed version1, state-changed version11.
Owner discovery consults existing stream-role maps. Snapshot subscription changes
only transient follower/revision bookkeeping; it does not run/resume a turn.
Snapshots are owner/thread/host/version/revision checked and returned in memory.
They are sensitive and must not be logged, cached to public files or committed.
History is normalized under `turnHistory`; `turns` can be empty. Consumers must use
the observed schema rather than assuming public App Server thread/read output.

Read-only acceptance: a real snapshot of the existing IDE-owned build thread passed
through the adapter while that session continued. Only metadata was printed;
approximately7.4MB, existing goal and normalized history present, Astra retained.
Recorded fixture under tests/fixtures/shared-session retains sanitized envelope
shape and documents removal of all transcript/user content. Source tests cover
exact allowed messages, target owner and unsubscription, oversized frame rejection
before payload allocation and pin failure before connection. Full Leam UI, continuing
stream patches, bidirectional messages and approvals remain separate increments.

## Explicit command adapter

`SharedSessionCommands` / `PrivateIdeCommands` adds targeted `start` and `steer`
methods. Both require the previously observed `SessionOwner`, validate the pinned
agent-protocols package before connecting, rediscover and compare owner identity,
and preserve the raw message in text input. Application `additionalContext` carries
coding policy separately on every dispatch. Start inherits existing thread settings;
steer does not send model, effort, permissions or toolOutput overrides. The native
owner selects the active turn for steer and may retry a native turn mismatch; this
private protocol cannot enforce a caller-supplied expectedTurnId.

Start uses follower version 2 and `{conversationId, turnStart:{request,context}}`.
Steer uses version 1 and `{conversationId,input,restoreMessage,attachments,
clientUserMessageId,additionalContext}`. The restore draft follows installed jJ:
id, text, context, cwd, createdAt. `supportsUntrustedAppInput` gates responseItems
injection only, not ordinary additionalContext. No responseItems are used.

A stable client message ID is correlation, not a verified server deduplication
contract. The caller must persist a receipt before dispatch and prevent replay.
Any error/cancellation after dispatch becomes `SubmissionUncertain`; reconcile the
owner transcript before resending. No automatic steer-to-start fallback, ownership
transfer, queueing, resume or model change occurs. Synthetic Unix-socket integration
tests verify exact payloads and policy, owner changes, missing policy and lost reply.
No commands have been sent to the original active thread during implementation.

## Root integration mapping

Use an explicit transport discriminator for the original thread: `ide-owner`.
Other threads remain on the existing private `CodexClient` subprocess. Never send
`thread/resume` for this thread or forward it through both transports. Snapshot owner
client ID and peer PID are the binding; invalidate them on socket disconnect or
owner change. Installation changes also invalidate the adapter.

Do not expose the full owner snapshot to the browser. The observed 7.4MB normalized
history is `state.turnHistory.kind == "canonical"` with:
`history.entitiesByKey`, `history.islands[].entries[].value`, `history.isComplete`.
Each entry value indexes entitiesByKey; island entry order is chronological. The
fallback `state.turns` is empty on the live session. For a bounded latest-50 browser
window, walk islands/entries backward, resolve entities and retain only the needed
recent user/agent items, then reverse to chronological order. A long active turn
can contain many items, so merely limiting to 50 turns is insufficient. Use a byte
and per-text bound as well, with visible truncation. Avoid serializing command output,
raw tool arguments, restoreMessage, reasoning content or approval policy snapshots.

Owned turn fields: turnId, status, turnStartedAtMs, items, error. User items contain
`type:"userMessage", id, clientId, content`; agent items contain
`type:"agentMessage", id, text, phase`. Steering input is a separate
`type:"steeringUserMessage"` with input, clientUserMessageId, status and
serverUserMessageId; use the latter to avoid showing both the pending steering item
and confirmed userMessage. Preserve unrecognized item types as a bounded activity
indicator rather than inventing message content. Existing public `/thread/read`
shape can map id/title/cwd and normalized projected turns to public turn `id` from
turnId, user `content`, assistant `text`; do not return this private state verbatim.

Owner model comes from latestThreadSettings.model (fallback latestModel), provider
from latestThreadSettings.modelProvider (fallback modelProvider), and effort from
latestThreadSettings.effort (fallback latestReasoningEffort). On the live thread the
root latestReasoningEffort is null although settings holds an effort. Reading must
never apply the companion Settings defaults to the existing coding thread.

Pending input is state.requests: native `{id,method,params}`. Scope external request
IDs by transport, owner identity and native ID; retain native ID type when forwarding.
Use existing Leam validation for the matching public request method, but do not put
these in the subprocess's upstream_requests map. Inspected follower version-1 RPCs:

| Native request method | Follower method suffix | Payload field |
| --- | --- | --- |
| item/commandExecution/requestApproval | command-approval-decision | decision |
| item/fileChange/requestApproval | file-approval-decision | decision |
| item/permissions/requestApproval | permissions-request-approval-response | response |
| item/tool/requestUserInput | submit-user-input | response |
| mcpServer/elicitation/request | submit-mcp-server-elicitation-response | response |

All follower method names have `thread-follower-` prefix and params also contain
conversationId, requestId. The owner handler checks a currently matching request;
missing/stale requests can silently no-op while RPC still returns `{ok:true}`.
Therefore this acknowledgment is not confirmed resolution: wait until the matching
pending request disappears in owner state. Never auto-answer. Dynamic onboarding
input uses item/tool/call and is a separate path; do not assume all dynamic calls
are questions. MCP accept retains owner's extra safety checks. Command adapter
currently deliberately excludes approval mutation until caller validation and
resolution reconciliation are integrated.

`PrivateIdeStream.watch(thread_id)` now maintains one server-side subscription and
applies observed version-11 Immer add/replace/remove patches with consecutive
baseRevision/revision validation. Consuming it yields SessionSnapshot objects;
unchanged state is shared, but changed ancestor paths are copied, preserving earlier
snapshots. The caller should keep only the latest snapshot, never an unbounded queue.
Use one background subscriber per thread and broadcast the latest projection to all
browser subscribers; do not repeatedly fetch snapshots on a browser timer.

The first snapshot has a 12-second response deadline. Each watch has a five-minute
lifetime, 10000-frame and 256MiB cumulative budgets; each frame retains the 32MiB
limit. Budget expiry, malformed patches, revision gaps and owner loss terminate the
watch and unsubscribe. Root can renew read-only following after checking binding;
mark the projection stale while disconnected and never replay commands. No automatic
renewal/takeover is hidden in the adapter. Call aclose() on cancellation/unmount.

`project_snapshot(snapshot)` supplies a public-shaped thread with chronological
turns/items, owner model/provider/effort, revision, activeTurnId, truncation flag and
pending request count. It bounds to 50 recent user/assistant messages, 16KiB per text
and 128KiB combined text. Tool arguments/output, reasoning, local paths, restore
drafts and raw requests are excluded; pending request routing remains server-owned.
Public turns can directly feed the existing transcript renderer. This is a latest
window, not complete pagination; expose truncation instead of claiming full history.

Live read-only acceptance of this stream succeeded: initial snapshot revision 5,
next actual owner patch revision 6, existing Astra/high preserved. Projection had
50 messages and approximately 30KiB versus the 7.4MB source state. No transcript
was printed/saved and no conversation command was sent. Synthetic caller tests
cover snapshot/patch/unsubscribe, immutable prior snapshot, revision-gap rejection,
canonical latest-window resolution, text bounds and confirmed steering deduplication.

## Leam API and mobile integration

`SharedCoding` owns exactly the original build thread and maintains one lazy
background follower for all browser clients. Renewals revalidate the installed
pins and owner; disconnect invalidates the binding and reports stale read state.
Each send requires the generation returned by thread/read, rechecks it after policy
validation, then the command adapter rediscovers the owner immediately before send.
Other coding threads remain on the existing Codex subprocess path. The shared
thread never reaches thread/resume, turn/start or steer on that subprocess.

Existing authenticated APIs now route the original thread through this service.
Thread listing pins it first. Read/turns/goal return compact state and truthful
connection metadata. The Coding surface shows owner model/effort and permits a
follow-up while the original turn runs. Missing owner/pin changes disable sending;
reconnect only follows the existing owner, with no takeover prompt. Shared stop and
pending approval/question responses are explicitly unavailable in this increment:
answer or stop in the original Codex window. API rejects attempts to bypass the UI.
Pending request metadata is bounded and excludes raw request arguments.

Durable request fingerprints include the owner transport discriminator. Receipts
store only the correlation outcome/turn ID, never original transcript contents.
Completed receipts replay without resending. Uncertain receipts remain pending;
reconciliation requires a native userMessage with matching clientId, never merely a
local pending steering draft. Explicit pre-dispatch failures release the reservation.
A transport or owner acknowledgment failure leaves it pending. Model/effort settings
and raw user text remain unchanged; protocol context is independently validated and
attached on every new dispatch.

SSE events contain only thread ID, revision, generation and connection status.
Owner transcript state stays in server memory and browser responses; it is not
written into Leam's durable events table. Updates are coalesced to 250ms. Source
API acceptance uses a real synthetic Unix socket owner and authenticated HTTP client,
covering start/steer payloads, receipt replay/lost-response reconciliation, owner
replacement, pending-request rejection and routing other threads normally.


## Installation-owned thread binding

Set `LEAM_SHARED_CODEX_THREAD` in the private service environment to the existing
IDE-owned thread's UUID before deploying shared-session support. The application
reads and validates it once at construction; changing it requires service restart.
It is installation data and must not appear in source, fixtures or public examples.
Absent/blank configuration disables only the shared IDE transport: no synthetic
shared list row, ownership match, follower connection or shared dispatch is created.
Ordinary Codex sessions remain available. Invalid UUID configuration fails startup
with a value-free error. Configuring the binding does not transfer ownership, change
approval policy or remove the adapter's socket, pin, peer and owner checks.

Deployers preserving an existing shared session must configure its private binding
before rolling out this revision. No automatic discovery or fallback to an operator's
former thread is performed. Tests use a synthetic UUID and temporary Unix sockets.
