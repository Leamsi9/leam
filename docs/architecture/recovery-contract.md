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
