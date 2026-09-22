# Local Agent Protocols

Read matching overlays before the base protocol or planning. Load only what applies;
reuse the resolved policy until its scope or instructions change.

| Trigger | Local overlay | Effect |
| --- | --- | --- |
| Work on this Leam MVP candidate | [candidate-delivery.md](candidate-delivery.md) | Build → deploy → notify → QA → user UAT; parallel independent slices; overrides conflicting base test/review-before-deploy and serial-phase ordering. |

Select exactly one base; retain unaffected gates. Other repositories use their own
AGENTS.md and local index. Ordinary companion conversation is outside this scope.

Keep the effective policy in the existing task plan/handoff, not a new document:

`Scope: Leam candidate | Base: <minor/substantive> | Override: candidate-delivery | Order: build → deploy → notify → QA → user UAT`

Carry that line and paths into delegation/continuation. Recheck on resumption,
scope change, and before deployment. See [deployment instructions](../../docs/deployment.md)
for target/owner discovery.

These repo-owned extensions survive package refreshes. Keep package-owned base
protocols unchanged; upstream only improvements intended for all consumers.
