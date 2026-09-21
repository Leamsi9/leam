# Third-party notices

This file identifies the principal separately licensed components. It does not replace their licenses or grant rights to Leam's original application code.

## Engineering protocols

`agent-protocols/` vendors [Leamsi9/agent-protocols](https://github.com/Leamsi9/agent-protocols), version 0.0.19, commit `571fa58d259c2735e5bcbc262e082d275de38d58`. The source identity and file hashes are recorded in `agent-protocols.lock.json`.

Retain [LICENSE-MIT](agent-protocols/LICENSE-MIT) and [LICENSE-APACHE](agent-protocols/LICENSE-APACHE), including their copyright notices. The local candidate-delivery overlay is consumer-owned configuration and is not part of the upstream file hash manifest.

## Companion runtime

The separately built runtime derives from [NEAR AI IronClaw](https://github.com/nearai/ironclaw), under MIT OR Apache-2.0. Its repository retains the upstream license and copyright files. Leam's required upstream baseline and patched source revision are recorded in [runtime-lock.json](docs/architecture/runtime-lock.json). Upstream release binaries do not include the Leam adapter changes.

## Installed dependencies

Python and JavaScript dependency versions are recorded in `requirements.lock` and `apps/web/package-lock.json`. Dependencies are installed from their respective package distributions; their license files and notices remain authoritative. Preserve applicable notices when distributing bundled clients, installed environments or other artifacts.

The JavaScript dependency graph includes MIT, Apache-2.0, ISC and BSD-3-Clause components. Its browser-support build data includes `caniuse-lite`, under CC-BY-4.0. This inventory is descriptive, not a replacement for package-level notices or an exhaustive distribution-compliance analysis.
