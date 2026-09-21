# Local Agent Protocols

This directory is reserved for repo-local protocol extensions.

The package installer creates `agent-protocols/local/`, but it does not treat
this directory as package-owned content:

- files in this directory are versioned by this repo
- package refreshes preserve local files here
- generic protocol improvements should be upstreamed to the package root
- repo-specific protocols should stay here

Do not move files from this directory into the upstream package unless the
protocol is intentionally being generalized for all consumers.

Leam candidate delivery follows [candidate-delivery.md](candidate-delivery.md).
