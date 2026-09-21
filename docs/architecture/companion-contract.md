# Companion runtime boundary

Leam owns user-facing conversations, sourced personal context and domain state. IronClaw 1.4 owns execution and its provider credentials. The adapter is pinned to the runtime lock; upgrades require contract and behavioral tests before activation.

## Private transport
The API connects only to an operator-configured loopback HTTP endpoint. Environment proxies and redirects are disabled. Runtime bearer tokens are read from a private token file, never returned to the browser. Provider keys pass to IronClaw's credential API and are not persisted in Leam settings. Error messages omit upstream bodies, which may contain secrets.

## Durable delivery
Before dispatch, Leam saves the exact contextual message, route and action ID. On uncertain response, retry uses that exact payload and the upstream session inbound idempotency ledger. Changing the text under an existing action ID fails. Completed receipts are served without contacting IronClaw. This guarantee assumes the same pinned runtime installation and intact runtime idempotency store; replacing or restoring runtime state requires coordinated Leam/runtime backup handling.

Codex uses its separate protocol and conservative receipt reconciliation. No companion context is injected into coding messages.

## Context and retention
Automatic memory grounding is a deterministic query-ranked view of current canonical records, with query-centered snippets, IDs, revisions, source/category previews, dates and explicit partial/count metadata. Its complete JSON is bounded to 4 KiB, including escaped Unicode and metadata. Active commitment titles remain separately bounded by count. This is initial reference grounding, not complete relevance retrieval or a permission grant.

Model `leam_context` responses are bounded to 8 KiB of serialized JSON, including metadata and the complete MCP result envelope with escaped text. One explicit text block is emitted without a structured-output duplicate. Search examines full original records before snippet projection; `nextOffset` advances by records actually returned. Memory text/source/category previews expose `partial`. Exact memory detail uses `kind=memory`, `id`, and optionally the returned `revision`; concatenate `recordJson` fragments using `nextCursor` (Unicode character offsets). Continuation requires the same revision and fails on edits rather than silently mixing versions. Deletion removes records from future searches and detail. Browser memory/context reads retain full content. Oversized non-memory model records expose a partial pointer to the corresponding Leam view. Source labels are user editable and are never trusted provenance.

The PWA renders the user's original message instead of its runtime context envelope. Forget removes the current memory from future context, but does not erase copies already in transcripts, saved delivery payloads or backups. Historical retention/erasure controls remain product work.

## UI and acceptance
Provider configuration, connection testing, active model selection, device sign-in initiation, memory editing/removal and conversation selection/creation are exposed through the authenticated mobile UI. History forwards cursors and preserves earlier pages during polling. Provider credentials are cleared from the form after save. Live model use still requires a configured credential and an actual response test; a provider catalog or empty conversation is not proof of a working companion.


## Proposed domain changes
The companion domain boundary prepares suggestions with validated inputs, a reviewed before/after view, reason and conversation ID. Suggestions are not executed by model tool access. Authenticated same-origin browser approval is required. Declines cannot execute; accepted local effects and their completion receipts commit in the same SQLite transaction. The shared commitments/store operations accept an internal transaction receipt callback rather than duplicating domain mutation logic. Progress events also commit in that transaction.

External calendar proposals reuse the shared calendar services and deterministic request IDs. Approval is persisted before dispatch; uncertain outcomes remain approved and recoverable, including an account reconnect error while a provider action is pending. A stale revision/version becomes a conflict requiring a new review. Terminal completion receipts make repeated approval harmless. The UI shows concrete changes, approve/decline/recover actions, results and earlier suggestions. Proposal inputs preserve omitted edit fields so defaults cannot silently overwrite fields absent from review.

This is the shared operation/approval foundation. Runtime tool registration and actual model-driven use follow the separately reviewed local-mcp-contract.md; this slice alone does not establish conversation-to-action acceptance.

## Model and reasoning controls

The default new companion selection is ChatGPT subscription (`openai_codex`),
`gpt-5.6-sol`, Medium reasoning. Settings reads the current Codex App Server
`model/list` catalog, including per-model supported reasoning efforts; unavailable
models or efforts cannot be activated. This catalog lookup never resumes a coding
thread or changes its model. Existing coding sessions retain their own settings.
The independently authenticated runtime subscription account must be the intended
account; this catalog is not proof that a different runtime account has access.

Selection writes provider, model and effort through IronClaw's active-selection API.
The browser displays the persisted snapshot after save/reload. Older runtimes that
ignore reasoning fields fail Leam's confirmation check. Runtime changes require the
paired reasoning-support patch: stored configuration alone is not a successful live
model call. Initial device sign-in remains separate from the active Codex session.
No API-billed fallback or credential copying is performed.


## Live system inspection
`leam_system` and Settings → Inside Leam share an injected read-only inspector.
Sections distinguish configured model selection, running API release/client hashes,
expected runtime lock versus observed process executable hash, worker heartbeats,
connection states and declared operation authority. Probe time is separate from
heartbeat and cached binary observation time. Unknown identity never becomes green.
Provider credentials, account identities, environment, raw errors and transcripts
are excluded. Inspection never opens a coding session or executes administrative
actions. Failure-history instrumentation and automatic coding delegation remain
pending. Ordinary prompts receive only the small model/architecture hint; detailed
inspection runs on request. The current tool exposes summary/model/release/modules/
operations; no arbitrary file, command, URL or setting-key query exists.

MCP tool additions require supported catalog rediscovery and actual mediated
invocation before acceptance. Existing endpoint/certificate/credential and original
source definition stay pinned; discovered schemas may refresh under that binding.


## Conversation management

Companion and Coding lists forward opaque runtime pagination; selecting a row loads its history. A selected chat collapses the scrollable list. Titles activate inline rename; Open and Delete are separate labelled controls with dates beside titles. Companion names are revisioned Leam-owned display metadata because the pinned runtime has no rename API. Its native deletion rejects active runs under the runtime submission lock; confirmation discloses retained Leam delivery receipts, proposals, memories and backups.

Coding names use thread/name/set. Coding deletion uses native thread/delete only after explicit confirmation that it permanently deletes spawned child conversations and stops their running work; workspace files remain. The shared build and ticket-linked conversation identities and their ancestry are protected, with bounded metadata verification failing closed. Successful cascade deletion invalidates Coding display snapshots and selection before a fresh list read, while unrelated drafts remain. This is not archive or an inactive-only operation.

## Memory review preferences
Memory create/edit/remove proposals now execute automatically by default, under
user-owned Settings → Memory preferences. Three switches can require review per
operation. The policy is revisioned in existing settings storage; MCP cannot change
it. Each new proposal records its selected policy before execution. Repeating an
exact request resumes that saved decision, including after a process restart;
changing preferences never retroactively approves an existing manual suggestion.
Atomic domain/result receipts prevent duplicate local mutations across API/MCP
processes. Only saved state `complete` establishes execution; conflicts remain
explicit. All other domain operations still require explicit approval.

Memory suggestions/activity from all threads live in the collapsed Memory section
in Settings, alongside inspect/edit/forget controls. Companion's inline suggestions
exclude memories. Operation schema reports the current approval policy; historical
records retain their original source and revisions. Existing pending requests stay
available for explicit approval or decline.

## Reviewed coding handoff

An explicit user coding request should immediately prepare a concrete editable
`coding.handoff` proposal through `leam_operation_schema` and `leam_propose`.
Preparing that prompt needs no separate permission question. The Companion must
not refuse merely because Codex owns code execution. Essential missing task
details may require a focused clarification. The existing inline review card
lets the user edit, Review coding task and explicitly Start in Coding. Preparing
a proposal neither approves it nor starts execution; unsolicited suggestions
have the same review boundary. System inspection and tool descriptions must
describe this available capability consistently.

Conversation proposals are collapsed by default with a pending-review count;
expanding mounts their editable review cards. No source-message association is
invented. Coding's first list page supplements native indexed sessions with up
to ten recent accepted handoffs verified through native metadata reads. Failed
discovery and truncation are explicit; missing sessions are not reconstructed
from stale receipts. Linked Open controls and selected-session identity remain
available while native indexing catches up. Listing never starts/resumes a turn.

`coding.handoff` is always a pending manual proposal, regardless of memory policy.
Review binds the exact browser-edited text to a server preview; Start creates a
separate ordinary Codex thread and sends that exact text through the existing
pinned-protocol dispatch path. Configured workspace/default model are shown before
Start. Quoted source context is separate. Native approvals remain unchanged and
original shared build ownership is never taken over. Duplicate/restart uncertainty
is visible, never automatically replayed. Only dispatch acceptance completes the
proposal; bounded linked Codex task/result evidence is separate, sourced and dated.
Existing context/proposal tools read these handoffs without authorizing execution.
