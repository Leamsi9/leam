# Resource associations

Resources and tasks retain separate canonical records. A link stores only resource
ID, target type/ID and linkedAt. Reading resolves current labels/state; renaming or
completing a task never requires copying its data into a resource. Target types:
commitment (the same entity powers Goals/boards), feature (stable backlog key through
latest deployed Update and completed Changelog), proposal (including pending and
accepted coding handoffs). Assignment or linking grants no execution authority.

GET `/api/artifacts/<id>/links` returns `{resourceId,revision,items}`. Each item has
`targetType,targetId,linkedAt,available,title,state,url`. Available targets add their
canonical revision/location; features include latest updateId and completed when
published, proposals operation/updatedAt, commitments capacityId/owner. URL remains
null; the frontend's explicit selectors implement navigation. Locations are goals,
backlog, updates or approvals. Missing/deleted/declined/superseded targets expose
only their reference and unavailable/null display fields; no declined content is
retained or resurrected. Such references remain removable.

POST same route accepts `{requestId:UUID,revision,operation:link|unlink,targetType,
targetId}`. It validates target existence for new links; unlink remains possible
for unavailable targets. Returns durable receipt `{resourceId,revision,operation,
targetType,targetId}`; reread links for current metadata. BEGIN IMMEDIATE encloses
revision check, unique relation modification, quotas and receipt. Exact retry
returns the original receipt even after subsequent unlink; a reused ID with changed
payload conflicts409. Stale revisions conflict409. Already-linked or absent-unlink
is a no-op without revision change. Nothing deletes underlying tasks/resources.

GET `/api/resource-links?targetType=&targetId=&limit=&cursor=` returns
`{items:[resource metadata],target,nextCursor,total}`; resources are ordered by ID,
with bounded keyset pagination. GET `/api/resource-links/targets?type=&q=&limit=&cursor=`
returns `{items:[resolved canonical targets],nextCursor}` for label-based selection.
Search is title-only and uses SQLite case folding; limit defaults30/max100, query
max200characters. Commitment/proposal IDs are canonical UUIDs; feature IDs are
bounded slugs. Feature choices prefer the current deployment over stale backlog
rows. No arbitrary path, remote URL or raw content is returned by this domain.

Relations use existing settings `resource-links:<resourceId>` and permanent compact
`resource-link-receipt:<requestId>` idempotency records. At most32 links/resource and
10000 active links per installation. Existing product backups include both without
schema changes. Authentication and Origin protection are inherited from real app
middleware, including reverse reads and selection. No cross-installation lookup,
external fetch, model call, background mutation or automatic relationship inference
occurs. Source-generated resources can be explicitly associated by the user.

Authenticated `GET /api/proposals/<UUID>` and `GET /api/updates/<UUID>` return the
existing canonical detail shapes for explicit linked targets beyond the first
listing page. These reads do not mark items seen/read or approve anything. Missing
records fail404; invalid identities422. Feature link metadata resolves the latest
non-superseded Update ID before navigation, without copying publication bodies.
