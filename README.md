# Leam

Leam is a personal companion with a mobile PWA, commitments and capacities, inspectable memory, connected calendars, and separate coding conversations. Leam owns personal state and domain workflows; IronClaw runs the Companion, and Codex runs coding sessions.

Development preview. This repository does not claim production readiness or universal browser/device support. See the [product contract](docs/architecture/product-contract.md), [system map](docs/system-map.md), and [deployment requirements](docs/deployment.md).

## Local development

Requirements: Python 3.12 or newer, Node.js/npm, and an isolated local data directory. Coding requires a compatible authenticated Codex installation. Companion execution requires the matching patched IronClaw runtime described by [runtime-lock.json](docs/architecture/runtime-lock.json). A stock upstream binary is not a substitute for required adapter changes. PDF extraction on Linux requires `prlimit` and `pdftotext`.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
npm ci --prefix apps/web
npm run build --prefix apps/web
LEAM_DATA_DIR="$PWD/.artifacts/dev-data" \
LEAM_ORIGINS="http://127.0.0.1:46400,http://localhost:46400" \
.venv/bin/uvicorn leam_api.app:application --factory --host 127.0.0.1 --port 46400 --no-access-log --no-proxy-headers
```

Open `http://127.0.0.1:46400`. The first setup requires the pairing code in the selected data directory's `bootstrap-token`; setup removes that file after creating the password. Use a private directory for real personal state and keep it outside this checkout. Credentials, uploaded files and conversations are not distributed with the source.

`LEAM_RUNTIME_URL` and `LEAM_RUNTIME_TOKEN_FILE` select the runtime adapter. `LEAM_CODING_WORKSPACE` selects the workspace used for reviewed coding handoffs. Following an existing IDE-owned coding session is an optional, version-pinned compatibility feature. Set `LEAM_SHARED_CODEX_THREAD` explicitly for that installation; when absent, the adapter exposes no shared session and starts no follower. It must never select a bundled personal conversation.

## Validation

```sh
.venv/bin/pytest -q
npm run build --prefix apps/web
```

Browser tests include controlled fixtures and explicitly live scenarios. Read the [test coverage guide](docs/test-coverage-map.md) before running them. Live tests require dedicated test state; some invoke a model, write records or configure providers. Do not point them at a personal installation. Browser API mocks do not establish physical-device speech, push or provider acceptance.

## Project boundaries

Personal information remains installation-local unless an explicitly invoked provider or tool requires it. Source publication does not expose an installation: keep the API on loopback behind an authenticated HTTPS access layer, preserve session/origin checks, and configure provider callbacks for that installation. See [deployment guidance](docs/deployment.md).

The pinned engineering protocols are vendored under their existing MIT/Apache-2.0 terms. Dependency and runtime notices must be retained in distributed artifacts. See [third-party notices](THIRD_PARTY_NOTICES.md).

## License status

The owner has not selected a license for Leam's original application code. This source publication does not add a license grant for that code; do not describe Leam as open source until a license is provided. Separately licensed third-party material retains its existing terms. The companion runtime is derived from IronClaw and retains its MIT OR Apache-2.0 licensing.
