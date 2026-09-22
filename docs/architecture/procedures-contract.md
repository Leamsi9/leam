# Explicit operating procedures

GET `/api/procedures` exposes two generic product templates, `what-now` and
`overload`, version1, steps, limits and independent local adoption metadata.
Initial state is proposed. PUT `/api/procedures/{id}` requires current template
version and adoption revision; owner can adopt, retire or return to proposed.
Optional bounded sourceRefs carry SHA256, message UUID/ordered line span and audit
procedure ID. These are owner-supplied, unverified metadata in private settings;
no personal source content or installation-specific linkage ships in Git. A
saved memory, source claim or procedure invocation is never adoption.

Today > Chat about this day offers a procedure for the next message only. Opening
or selecting reads the catalog; sending uses the existing Companion POST and
current selected model. Optional `procedure:{id,version}` participates in its
request fingerprint. Exact uncertain retries retain the admitted payload;
changing procedure under the same request ID conflicts. Retired/unknown versions
fail rather than falling back. New requests require the canonical day+timezone
thread binding; the general Companion endpoint cannot invent an empty agenda.

Only the chosen steps enter run-local reference context alongside the fresh
canonical cached Agenda. Existing Unicode JSON aggregate8KiB and Agenda2KiB
limits still apply; unrelated automatic memory snippets can be omitted, with
partial metadata. No source sync or hidden model call on navigation. Source
coverage/freshness and user Focus remain authoritative. Ordinary chat has no
selected procedure. Existing transcript mentions remain historical.

These workflows recommend only. They add no scheduler, scripting engine, new
model, approval grant, autonomous capture or domain action. Claims of saved,
parked, delegated or completed work still require actual domain receipts. Existing
chat drafts and request IDs survive failure; no automatic resend. Source-level
and controlled mobile tests do not establish live model quality or phone UAT.
