# Cross-module read-only context

`leam_context` and authenticated `GET /api/companion/context` use the same
`ContextReader`. Supported kinds are all, memory, commitment, capacity, calendar,
routine, event_rule, notification and project. `GET /api/companion/overview`
returns module summaries and the actual recorded Leam build summary; Today fetches
it only when the collapsed “Across Leam” panel opens or is refreshed.

Each record and module carries source, observedAt, dataAsOf and availability.
Timestamps are Unix seconds; missing source freshness stays null. Availability
available/empty/stale/partial/unavailable describes saved local state, not live
provider access or scheduler health. Mixed unavailable calendar snapshots report
partial. Event rules expose their validated configuration including match predicate;
incoming event records, attributes and payloads are never read. Codex transcripts
and unrelated filesystem/project inventories are not part of this context.

Memory, commitment and capacity preserve complete search/pagination. New sources
scan at most 500 records per source, with deterministic projected-record search.
An incomplete scan reports truncated, totalIsExact=false and partial module state;
nextOffset only pages the scanned result set and is not a claim that all records
were searched. Missing sources also make totalIsExact false. Response pages remain
bounded to 50 records and the existing 512 KiB tool limit applies.

The project record exists only if Updates or Backlog contains actual records. It
separates deployed, QA, UAT, user-accepted and unfinished state. Progress estimates
mean progress toward deployment; assessments older than 20 minutes stay visibly
stale. No aggregate completion percentage is inferred. Deployment/backlog scans
are capped independently: countsTruncated and partial record/module/global state
plus a UI warning identify incomplete aggregate counts. Details show up to five
recent deployments and work items. Superseded releases and already-deployed backlog
items follow the existing Updates/Backlog projection rules. Reads never create a
schema, mark notifications read, run a routine, mutate state or contact a provider.

## Coordinated release and runtime discovery

Root must run the API and separate loopback TLS MCP listener from the same immutable
release. Preserve their data directory, original MCP package identity/source
manifest, pinned endpoint, certificate and bearer credential. No runtime rebuild or
operator binding change is needed for this schema extension: pinned
`hosted_mcp_operator.rs::matches_definition` permits discovered tools/input schemas
to change while keeping source/auth/network and other host surfaces identical.

After both services use that release, the existing authenticated runtime operator
setup route can refresh the catalog:

`POST /api/webchat/v2/extensions/{existing-package-id}/setup`
with a new durable client_action_id UUID, action `submit`, and an empty secrets
payload. Use the existing operator client so its authentication/Origin stay intact;
do not put bearer credentials in shell arguments or print them. This follows
setup_extension → ExtensionActivate → activate_with_credential_gate →
prepare_if_pending. The latter explicitly attempts discovery even when the package
already has model-visible tools. A ready package can retain its old catalog after
transient discovery failure, so an Active response or restart alone is insufficient.

Acceptance must inspect the refreshed runtime catalog and prove leam_context.kind
includes routine/event_rule/notification/project, then perform an actual mediated
read and compare record identity/source/dataAsOf with the authenticated API. Record
API/MCP release identities and refreshed catalog/schema hashes. Direct listener
`tools/list` and source caller tests are useful but do not establish runtime-mediated
acceptance. Do not rewrite registry files or re-register under a new identity.

Source gate covers authenticated API callers, real MCP protocol→ASGI domain calls,
raw input exclusion, search beyond 500 existing records, new-domain truncation,
partial calendars/backlog, stale progress, QA/UAT separation and mobile/desktop UI.
Runtime refresh, deployed acceptance and user UAT remain separate root-owned gates.

Model reads use the bounded projection described in companion-contract.md; browser
context and overview retain their complete current shape. The duplicate project
summary is omitted from model response metadata and remains available as its own
`project` item. This prevents long project titles from escaping the aggregate budget.
