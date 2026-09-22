# Trusted Today approval policy for direct chat tools

## Observed gap

The user-facing policy already defaults Today/Goals changes to automatic and coding
handoffs to manual. Direct `leam_propose` currently reaches `DomainTools.call` and
`Proposals.propose(origin=None)`. Its `leam_operation_schema` likewise evaluates
policy without a product origin. The durable Today reconciler separately passes
`origin="today"`; it preserves existing manual proposals, so it cannot silently
repair direct-tool proposals. This is a missing authority path, not a settings
value or prompt problem.

The current native MCP HTTP client has a host-stamped `ResourceScope` but serializes
only tool name and arguments into `tools/call`. Scope never arrives at the Python
MCP server. Model-provided `threadId`, surface labels and prompt text cannot replace
that missing evidence. Installed MCP SDK supports injected `Context` and
`Context.request_context.meta`; injected context is excluded from model tool schemas.
These are source-inspection findings; the new path has not been implemented or tested.

## Minimal proposed contract

1. An explicit host-owned opt-in on the existing operator-pinned loopback MCP
   binding permits scope forwarding. Default remains false. Existing provider,
   exact TLS endpoint, manifest digest and capability checks must all pass. Other
   MCP services receive no extra user/thread identifiers. A mutable extension
   manifest or remote catalog cannot enable forwarding. Revalidate authority at
   send, including the opt-in, to prevent package/endpoint substitution races.
2. Native `tools/call.params._meta["io.ironclaw/host-scope"]` carries exactly
   `{version:1, tenantId, userId, threadId, invocationId}` from `ResourceScope`.
   All values are bounded opaque IDs, never transcript text, secrets, origin
   declarations or approval instructions. Emit only for conversation-scoped calls;
   no thread means no automatic product origin. Model arguments cannot override it.
3. The loopback/authenticated Python MCP listener reads the SDK-injected request
   metadata, validates its strict shape and forwards it in a separate `hostScope`
   envelope alongside tool/arguments to the authenticated internal-tools endpoint.
   Only `leam_propose` and `leam_operation_schema` need this first slice. Missing
   metadata remains compatible/manual; malformed or contradictory supplied scope
   fails closed. Metadata cannot be obtained by inspecting tool arguments.
4. Product code verifies tenant/user against the existing pinned
   `today-reconciliation:owner`, verifies the runtime thread's canonical scope via
   the authenticated timeline endpoint (one message maximum), and resolves Today
   membership from `companion-agenda:<thread>`. A proposal's threadId must equal
   the trusted threadId. Unknown/unbound threads have no automatic origin. Runtime
   ownership verification failure is a visible error, never an automatic fallback
   that executes changes. Do not create owner bindings from MCP payloads.
5. The existing domain proposal operation receives `origin="today"`; the existing
   revisioned policy and operation allowlist remain authoritative. Schema queries
   use precisely the same origin resolution and accurately describe whether the
   operation is automatic or manual. Coding handoffs remain manual. Already pending
   manual proposals retain their original approval requirements. No browser API,
   prompt field or model schema gains an origin switch.

The installation tool credential authenticates the local runtime/MCP adapter chain;
this does not claim resistance to a compromised same-user process that has stolen
that credential. Existing no-browser-origin and loopback constraints remain.

## Delivery stages and effort

- Runtime: narrow neutral metadata serialization plus host-owned operator opt-in,
  exact egress authorization/revalidation and native caller tests. No product
  names or policy branches inside the neutral MCP lane. Expect a moderate
  cross-crate change, with Rust rebuild as the largest delivery dependency.
- Product: bounded envelope validation/owner resolution, MCP context injection,
  existing proposal/schema integration and authenticated caller tests. Deploying
  this first can be inert/backward compatible until the sender is enabled, but is
  not a user-visible fix by itself.
- Joint activation/QA: coordinator deploys pinned runtime plus product, enables only
  the already pinned Leam endpoint, verifies exact binaries and source, then checks
  complete MCP-to-domain behavior. Retain manual compatibility for rollback.

Expected scope is three focused stages, not a redesign of approvals. A precise
wall-clock estimate depends on Rust build/cache state. Goals needs its own canonical
thread-to-domain binding and is excluded from this first Today-only activation.
Do not infer Goals ownership from arbitrary entity IDs in a prompt.

## Required evidence

Native actual HTTP caller tests capture the outbound JSON: host IDs win despite
forged argument metadata, missing/disabled opt-in emits none, remote or substituted
endpoint/package/capability cannot receive scope, and concurrent users/threads do
not share metadata. Schema discovery must not leak scope or expose context inputs.

Python actual MCP/internal API callers cover absent metadata, malformed supplied
metadata, foreign tenant/user, missing pinned owner, mismatched proposal thread,
missing Today binding, unavailable runtime, canonical Today owner, concurrent scopes,
manual settings, policy revision changes, coding handoff, and pre-existing manual
pending proposals. Reuse existing proposal revision/idempotency tests; prove exact
state changes and pending/complete results. No live personal records or model calls
are required for these fixtures. Final user UAT remains separate.

## Inert product receiver checkpoint

The product receiver implements the envelope behind constructor-only opt-ins:
`create_mcp_app(..., runtime_scope_credential=...)` and
`DomainTools(..., scope_guard=RuntimeScopeGuard(store, runtime, credential))`.
Both are absent in normal production composition. No credential is generated,
read from new files, stored in settings, or activated by this patch. Native sender
and operator provisioning remain separate work.

The runtime scope credential must differ from the ordinary tools-token. MCP
middleware authenticates that credential and sets a server-owned request-scope
marker; arbitrary request headers and SDK metadata alone do not confer identity.
Only an authenticated scope-bearing call obtains a short-lived HMAC forwarding
proof covering exact tool, arguments, scope and expiry. The domain requires this
proof as well as its existing ordinary internal-tools authentication. A caller
holding only tools-token cannot forge an automatic origin via the internal API.
The proof carries no credential, binds all input changes, and expires within30
seconds. Proposal request IDs retain normal durable retry/idempotency behavior;
no additional state ledger is introduced. Rollback defaults back to manual tools.

Identity fields remain bounded opaque strings, matching the native ResourceScope
contract (tenant/user IDs are not universally UUIDs). Thread/request/invocation
IDs are preserved exactly; the tests use immutable thread and invocation UUIDs.
The complete route still depends on native pinned-endpoint opt-in and credential
provisioning; no current Today auto-approval improvement is claimed yet.

## Explicit production activation

The production API and MCP factories now accept the startup flag
`LEAM_RUNTIME_SCOPE_ENABLED=1`; absent/0 keeps the receiver disabled. Other values
fail startup. Enabling reads the existing accounts-key through O_NOFOLLOW plus
private-owner/regular-file/32-byte checks. It derives the runtime bearer with
HMAC-SHA256 label `leam-pinned-runtime-scope-bearer-v1`, then derives a separate
internal-proof key with `leam-runtime-scope-forwarding-proof-key-v1`. Neither the
vault master nor derived secrets are logged or exposed through a product endpoint.
The optional create_app constructor credential supports the same actual composed
route in isolated fixtures. No request, UI setting or tool can enable the flag.

This replaces the earlier proposed separate key-file provisioning. Existing
backups preserve the derivation root and require no schema/whitelist change;
restoring the same accounts-key yields the same derived credential. Rotating or
restoring a different root changes the credential: the old runtime credential
fails authentication visibly until an operator updates it. There is no automatic
fallback to another credential or approval origin.

Coordinator-only activation order (do not execute from implementation workers):

1. Record source/binary/client identities, backup receipt, current pinned provider,
   exact TLS endpoint/certificate and manifest digest, native resource/recovery
   settings, and the current revisioned approval policy. Never copy secrets into
   receipts or repository files.
2. Deploy product receiver, then enable the startup flag on API and MCP. Both
   legacy tools-token and the distinct derived runtime bearer are accepted by the
   MCP listener; only the latter can authorize scope forwarding. Internal API
   authentication remains tools-token plus the exact-payload forwarding proof.
3. Derive the bearer in an operator process using
   `installation_credential(data_directory, enabled=True)` and pass it directly to
   the existing native manual-credential setup operation for the already pinned
   local Leam MCP extension. Do not print it, place it in CLI arguments, or write
   it in operator-mcp.json. Preserve extension/provider identity and manifest.
4. Set only that existing private operator-mcp.json binding's `forward_scope` boolean
   to true, select the reviewed native artifact and restart through normal guards.
   Preserve all other bindings, TLS pins, tool ceilings, model/provider recovery
   configuration and installation state. The new field defaults false and is
   outside model inputs, mutable manifests and server tool catalogs.
5. Confirm current binary hash/source/process and product source, then run the
   native mediated HTTP and product MCP/API caller suites. Verify a selected
   canonical Today chat's schema reports its actual current policy and explicit
   approved-scope task creation/update has the expected domain receipt; coding
   must remain pending, and an older manual proposal remains pending. Fixture
   success is not evidence of live activation. User UAT is separately recorded.

Rollback first disables native forwarding (or returns to a sender without it),
then restores the old tools-token in native credential storage before disabling
the API/MCP flag. Remove the new forward_scope field before starting the old
native binary, whose strict configuration parser does not know it. Keeping the
new product receiver enabled temporarily accepts both credentials; absent scope
continues through manual policy. Do not disable MCP credential acceptance while
native calls still use its derived bearer. No rollback changes user approvals.

The candidate deployment descriptor explicitly permits the 0/1 activation flag in
its hashed environment. Both app and MCP launch plans preserve the same reviewed
value across release changes; invalid values fail descriptor validation. The
launcher strips ambient LEAM_ overrides, so setting a systemd environment variable
alone cannot enable a descriptor-managed installation. Set the descriptor through
the existing locked compare-and-replace operator seam. Legacy private launchers
must separately persist the flag for both processes until adopted.
