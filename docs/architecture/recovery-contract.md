# Independent recovery service

`leam_api.recovery:application` is a separate ASGI factory; it never imports or
starts Leam's main app, Codex bridge, scheduler or runtime adapter. Its standalone
mobile/desktop page is served from `leam_api/recovery_assets/`. It remains reachable
when the main API fails, provided the host, recovery unit and external route work.
It does not wake a sleeping/offline host and cannot restart itself.

## Deployment

Run one Uvicorn worker, bound to 127.0.0.1:46430, with `--no-proxy-headers`.
Use a separate `leam-next-recovery.service`, independent of the three controlled
units. Root operator publishes a separate HTTPS/Tailscale route (planned port 8444).
Set `LEAM_RECOVERY_ORIGINS` to the exact externally visible origin; Host and Origin
must match. Set `LEAM_RECOVERY_DIR` to a private owned directory, default
`~/.local/share/leam-next/recovery`. Never reuse main application state or password.
Proxy transport must arrive from loopback and preserve the public Host header.

First start creates a mode 0600 `bootstrap-token` inside the mode 0700 state directory.
Pair using that token and a separate password of 12–128 characters. Only the Argon2
hash persists, in a private regular owned file; successful pairing removes the
bootstrap token. Password/validation inputs are never reflected in responses.
Recovery sessions use a distinct HttpOnly/SameSiteStrict cookie, Secure for HTTPS
origins, and expire after 12 hours. Session hashes remain in process memory; restarting
recovery requires sign-in again but preserves its password. Five auth attempts/minute
and six service operations/minute are bounded globally for this single-owner plane.

## Fixed authority

Only these exact user units are reachable through status/start/restart:

- app: `leam-next-candidate.service`
- mcp: `leam-next-mcp.service`
- runtime: `leam-next-runtime.service`

No request can supply a command, executable, shell argument or arbitrary unit name.
Calls execute `/usr/bin/systemctl --user` with separate fixed arguments and a 20-second
timeout. Old `leam.service`/`leam-bootstrap.service`, recovery's own unit, stop/disable,
credential editing and host wake are absent. Root owns unit files and their contents.
All mutations require an exact same-origin header and authenticated recovery session
(except pairing/login). Streamed bodies are capped at 8 KiB. Browser rendering uses
text nodes and an external-script restrictive CSP; no untrusted HTML insertion.

Status reports service-manager state separately from fixed loopback port reachability.
A listening port is not proof of a healthy model/account/domain workflow. Timeout or
lost-response errors require inspecting status before retry; the UI never automatically
replays restart operations. Restart buttons explicitly confirm possible interruption.

## Evidence and limits

Source tests cover auth, privacy, cookie isolation/restart, Origin/Host/loopback/body/
rate boundaries, fixed subprocess argument capture, and a separate actual Uvicorn
process without importing main Leam. Down-main state/control is fixture-backed until
root performs a real stop/start recovery drill. Browser script
`scripts/check-recovery-browser.mjs` checks 390px and 1440px without service mutations;
it requires explicit isolated target/state/password environment variables.
Candidate deployment, HTTPS route and real service restart remain parent acceptance.

## Maintenance admission and native Coding drain

The fixed installed recovery directory also owns `maintenance.json`,
`candidate-work.lock` and a separate mode0600 `maintenance-token`. These are outside
all product generations and archives. The marker is not an authorization token.
The token only authorizes the loopback, no-Origin, read-only main API
`GET /api/internal/maintenance`; the MCP credential cannot call that route and the
maintenance credential cannot call tools. No browser receives either credential.

Authenticated same-origin recovery exposes `GET /api/maintenance` and explicit
`POST /api/maintenance/prepare|release {requestId: UUID}`. Mutation takes the same
cross-process candidate operation lock as restores, releases and service changes.
Prepare immediately pauses admission, observes bounded work, and seals only when
all shared work leases have drained. Repeating a check uses the same active UUID;
a different active UUID conflicts. A lost response is resolved by reading status,
never automatically repeating a mutation. Malformed/private-path state fails closed.

Every ordinary main API operation pauses with HTTP503/Retry-After (including login
and most GETs); health, auth status and the private proof remain readable. Existing
admitted HTTP work retains its lease until its handler completes. Scheduler,
routine and push loops lease individual ticks, never sleeps. Email classification
reserves a lease synchronously before task scheduling and retains it through
coalesced runs; task completion/cancellation releases it, including before first
coroutine execution. It is not cancelled by maintenance. Accepted Companion turns
may finish MCP calls while draining; sealing takes the exclusive work lock, after
which new MCP calls are also refused. The original IDE owner is neither queried
nor interrupted; no shared-session control RPC is used.

Native proof uses only the existing app-owned transport: `thread/loaded/list`
(100 IDs/page, 1000 maximum), then `thread/read {threadId, includeTurns:false}`.
Every status must be `idle`, the transport generation must stay unchanged, and
there must be no pending RPC/approval/answer. The total metadata probe is bounded
at15 seconds and never starts/restarts Codex. Pending user approvals require ending
maintenance, answering the existing request in Leam, and preparing again. The UI
states this limitation; waiting alone cannot resolve a question. Companion proof
reads the pinned runtime's process-materialization SQLite schema read-only, never
conversation contents. Unknown/malformed/active states stop nothing.

Concrete restore, rollback and release stop adapters require a sealed marker and
fresh idle proof before systemctl. Ordinary recovery restarts enforce the same
proof. Restarting a dead API remains possible without that API only when systemd
shows MainPID0/inactive-or-failed, its cgroup (including descendants) has no process,
and the fixed API listener is absent. Active-but-unreachable API fails closed.
Maintenance stays held across service replacement, readiness and rollback; release
is explicit and refuses every unresolved restore journal, including older entries
outside the UI history page. Runtime source/binary/ceiling are still checked against
the current validated descriptor, never a historical capability count.

The current small slice pauses the main product UI too. Operator comparisons of
retained/restored generations happen under continuous maintenance, using private
read-only canonical state hashes and Recovery receipts. End maintenance to conduct
interactive main-app UAT. Interactive read-only Today/settings or approvals during
maintenance are not implemented. A hard-killed API/recovery, external unmanaged
process or changed unit configuration is uncertainty, not proof of a clean drain;
operator review remains necessary. No automatic timeout interrupts accepted work.
