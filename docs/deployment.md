# Deployment requirements

Leam is a development preview intended for an owner-controlled installation. Publishing source does not make an installed service public or confer access to its records.

## Runtime and access boundary

Run the application on loopback, normally `127.0.0.1:46400`. Set `LEAM_ORIGINS` to the exact browser origins, including non-default ports. Use an authenticated HTTPS reverse proxy or private access network for remote access. Do not expose the development server or internal MCP endpoint directly to the internet. Disable application access logs (`--no-access-log`) and configure the reverse proxy to omit or redact OAuth callback query strings; they can contain authorization codes and state. The documented local recipe also uses `--no-proxy-headers`; any deployment that trusts forwarded headers must explicitly constrain and review the proxy boundary.

The Companion uses `LEAM_RUNTIME_URL` and the credential file selected by `LEAM_RUNTIME_TOKEN_FILE`. Match the source and capability contract in `architecture/runtime-lock.json`; the app requires the patched runtime source. No prebuilt Leam runtime is supplied with this source release. Build the pinned source, record the resulting binary hash in the installation's runtime receipt, and validate that installation before relying on it. Fresh-install and device acceptance remain deployment checks. Keep runtime and application source identities separate in deployment evidence.

Codex is an independently authenticated local executable. `LEAM_CODING_WORKSPACE` selects the workspace for reviewed handoffs. The optional IDE-owned conversation adapter is tied to exact extension/CLI artifacts and a current-user private Unix socket; an incompatible installation must fail closed. Configure `LEAM_SHARED_CODEX_THREAD` explicitly outside source control. Leave it unset to disable the shared-session feature.

The repository retains a deployment helper, `scripts/stage-release.py`, which creates a source-identified immutable release without switching services. An operator must prepare its environment, client build and rollback target. Candidate availability, source tests, deployment health and accepted behavior are separate states.

## State and credentials

Choose a private `LEAM_DATA_DIR` outside the repository for personal use. Keep the database, attachments, backups and encryption keys outside Git and out of public build artifacts. Preserve `accounts-key` with the database during backup/restore; losing it prevents decryption of connected-account credentials. Do not copy another application's account database or tokens to bootstrap an installation.

Configure OAuth applications through Settings. Register each exact callback:

- Google: `<origin>/api/accounts/oauth/google/callback`
- Microsoft: `<origin>/api/accounts/oauth/microsoft/callback`

Each account completes its own consent flow. Provider application setup is not evidence of a connected or synchronized account. Existing applications may retain separate redirects and grants.

Use the normal pairing/password setup rather than a shared default password. Keep application sessions, internal tool credentials and runtime credentials separate. Maintain origin checks, request limits, encrypted credential storage, approval controls and fresh source reads before consequential actions.

## Acceptance and updates

Run automated checks in isolated state. Validate provider consent, calendar freshness, native attachments, browser microphone/speech and push on the actual supported devices before claiming those behaviors for a deployment. Record the deployed source/runtime identities and observation time without saving private transcripts in the repository.

Back up state before a migration, preserve the prior immutable application/runtime artifacts, and rehearse restoration. Source release does not authorize replacing an existing installation. Dependency updates and changes to runtime/protocol pins require their own review and regression checks.

## Configured candidate delivery workflow

This workflow applies only when the current operator has authorized it for this installation.


The user-authorized candidate workflow is **build → deploy → notify → QA → user
UAT**. A deployable increment is available with QA pending; it is not automatically
accepted. See `agent-protocols/local/candidate-delivery.md`, which supersedes the
base engineering protocol's delivery ordering for this candidate only. Do not wait
for unrelated features or long full test suites before deploying a usable build.
Known data-loss or security defects still block publication.

Installation-specific target URLs, service names, source worktrees and deployment
helper locations live outside public source in:
`~/.local/share/leam-next/coding-deployment.json`.
Read that descriptor and the current live release receipt before deployment.
They are authoritative for current operations; dated inventory in the build plan
is historical evidence and must not override the live descriptor or receipt. A
private descriptor is operational configuration, never permission to weaken auth,
replace unrelated installations or export credentials. If absent, finish the
reviewable build and report that this installation has no configured target;
do not invent a URL, branch or hosting provider.

Build in the dedicated source worktree. Coordinate integration with the descriptor's
current deployment owner; preserve others' changes. Stage the exact clean commit
and its client using `scripts/stage-release.py`, preserving the previous release
for rollback. An immutable release directory is not an editable source checkout.
After activation, verify process source and public client identity, publish its
Updates record as Deployed and notify the user. Then run targeted caller/browser
checks and required review gates; mark QA truthfully with failure details. Only
the user records UAT passed/failed. A genuine regression revokes prior QA acceptance.

The canonical main checkout should track the last deployed source, so newly
created coding worktrees include the application and current instructions. Pending
feature integration may be ahead; the private descriptor identifies that branch.
Never force-reset an active or dirty source checkout to update it.
