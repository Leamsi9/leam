# Local companion tool connection

Status: architecture and Leam read/propose-only MCP implementation reviewed; runtime compatibility and real TLS acceptance pending. Runtime pin remains upstream 4cb47cf / ironclaw-v1.4.0 until a separate compatibility patch passes its gates. The Leam domain API and MCP server belong to Leam; no personal domain code belongs in IronClaw.

IronClaw 1.4 hosted MCP admission, discovery, grants and egress disallow private/loopback endpoints, and its stdio transport is explicitly unsupported. Do not bypass this with model-output parsing, an untrusted proxy/HTTP rewrite, ambient process authority, or a public relay for personal data.

Chosen seam: an immutable operator configuration binding an exact extension identity/package provenance to one HTTPS loopback endpoint on a dedicated listener and fixed port/path. Empty by default. Model input and package metadata cannot grant local network authority. The same policy must participate in admission, restoration, discovery, activation, capability grants and actual egress. Private-IP permission is valid only when every derived target is the operator-pinned loopback target. Generic hosted MCP behavior remains restrictive.

Use a locally validated TLS certificate, a dedicated bearer credential with only read/propose authority, exact audience host/port, and no redirects or ambient proxies. Approval/execution of Leam changes remains on authenticated browser routes; the tool token must never authorize those routes. Credentials are injected by the existing runtime mediation. The MCP listener serves only the tool protocol and is not the public PWA listener.

Review identified an existing explicit-port credential projection issue in IronClaw v3 manifests and ambient-proxy/redirect concerns in the real network transport. The patch must preserve server ports in credential audiences and prove transport confinement rather than merely relaxing URL validation. No certificate-validation disablement.

Required runtime gates: real TLS initialize/discover/call through registration and mediation; correct bearer on the nondefault port; unconfigured loopback/other ports/paths/private/metadata destinations and borrowed identities denied; proxy trap sees nothing; redirects receive no follow-up; invalid certificates fail; restart retains authorized behavior and removing operator permission revokes restored calls; caller ownership, credential/approval denial and resource limits remain effective.

Keep a separate source worktree and committed compatibility patch with exact base/patch/binary identities, build recipe and reviewed tests. New Leam's runtime lock changes only after the patched artifact passes. This is required product integration, not permission to change the old live Leam deployment.

## Leam listener implementation

`leam_api.local_tls.provision` creates one installation-specific P-256 TLS identity in the private `mcp-tls` directory. The certificate names only IPv4/IPv6 loopback, lasts 397 days, and its private key remains mode 0600. Atomic no-replace publication uses Linux `renameat2`; existing damaged/expired identities fail closed and require explicit repair. Provisioning does not alter the operating system trust store. Future renewal must coordinate the runtime's exact certificate trust and listener restart; silently replacing a trusted identity is forbidden.

For the isolated candidate:

```sh
.venv/bin/python -m leam_api.local_tls --data-dir /tmp/leam-candidate-ui
LEAM_DATA_DIR=/tmp/leam-candidate-ui .venv/bin/uvicorn leam_api.mcp_server:application --factory --host 127.0.0.1 --port 46420 --ssl-keyfile /tmp/leam-candidate-ui/mcp-tls/key.pem --ssl-certfile /tmp/leam-candidate-ui/mcp-tls/certificate.pem --no-access-log --no-proxy-headers
```

The upstream API creates the private `tools-token` first. Never put its value in command arguments, model context or logs. MCP binds loopback only and must not be exposed by the public PWA reverse proxy. Production service setup will use the durable installation data directory instead of this test-only candidate path.

73 backend tests pass. Four TLS tests cover stable private identity, damage refusal, no-replace race and concurrent provisioning, and real TLS protocol/authentication. A separate-process check on the running candidate verifies TLS initialize and a sourced Today read through the actual API; anonymous requests fail. Runtime-mediated registration/calls and model-driven behavior are still pending.
