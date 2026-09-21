# Source publication

This public repository begins with a reviewed source snapshot. It deliberately does not include private development history, installation records, personal documents, conversations or account state. The required runtime has separate upstream-derived history and its own source pin.

## Updating the public source

Prefer normal public branches for new work that is suitable for publication. If a change originates in a private development repository, review its source and documentation as an export before applying it here. Never add private Git object directories, alternates or refs to this repository, and never merge or push private history as a shortcut.

For each export, record the source revision, included paths, transformations, content hashes and validation results in a private release receipt. Inspect both file contents and commit metadata. Confirm that no installation data, credentials, personal identifiers, transcripts, generated client state or private plans are included. Retain upstream licenses and the protocol lock. Any privacy transformation that changes application behavior needs normal implementation review and tests.

The public runtime pin must resolve to published source. Distinguish source equivalence from binary reproducibility: record the actual binary's hash, build profile and toolchain separately for an installation. A private developer binary is not a published release artifact.

Run the relevant backend and client checks against the exported tree. Keep source publication, deployment, automated QA, device/provider acceptance and user acceptance as separate recorded outcomes. Public source does not authorize altering a running installation.

Enable GitHub secret scanning and push protection where available. These supplement review; they do not detect every secret format or all personal information. Keep credentials and personal state outside source control and rotate a credential if an actual exposure is discovered.
