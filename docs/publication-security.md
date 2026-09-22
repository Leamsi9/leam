# Public source publication

This repository publishes an audited source snapshot on its existing public history. `public-source.json` identifies the corresponding deployment source and records intentional documentation redactions. A public snapshot commit is not the same identity as the private development commit. Runtime source files, dependency pins and application assets are copied without changing runtime behavior. Installation evidence and personal engineering plans are excluded.

Before a push, stage only reviewed files and run (unstaged tracked changes fail the gate):

```sh
python3 scripts/check-public-source.py --history
```

The offline standard-library scanner checks tracked working files and reachable commit/blob history for recognizable credential/private-key patterns, credential-bearing URLs, private installation hostnames/home paths and forbidden private-artifact filenames. Findings report only paths, line numbers, kinds and object IDs. A small explicit allowlist permits known synthetic password strings and exact SHA-256 matches of reviewed synthetic credential literals in named test files only. Changes to `scripts/public-fixture-allowlist.json` require human review; arbitrary future fixture values are not exempt. There is no broad exclusion of tests or documentation.

GitHub Actions repeats the same check on main pushes and pull requests. Checkout is pinned to an official commit; token permissions are read-only and credentials are not persisted. It invokes no model, sends no source to an outside scanner, installs no dependencies, runs no product test suite, and has no access to deployment secrets. This is a source security/privacy gate, not a live application deployment workflow. Deployment remains installation-owned; the existing local workflow is build → deploy → notify → QA → user UAT. GitHub does not restart personal infrastructure or receive its secrets.

These checks cannot establish absence of every secret or private fact. They do not assess dependency vulnerabilities, runtime security, licensing, encoded credentials or arbitrary personal prose. Human review of changed configuration, fixtures and documentation remains required. If a real credential is exposed, revoke it and separately assess history cleanup; deleting its current file is insufficient. Retain `.gitignore` privacy protections and keep state/backups outside source control.
