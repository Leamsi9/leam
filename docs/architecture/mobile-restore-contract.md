# Candidate product restore and coordinated launch pointer

Implementation is isolated until the operator adopts the descriptor/launch commands.
The recovery process has its own immutable code release and private password/session
store. It never imports/starts the main app, schedules work or resumes Codex to restore.
Only fixed candidate app/MCP/runtime units are stopped/started by this controller.
Old Leam and the recovery unit are never targets. Browser parameters are UUIDs,
preview digests and explicit confirmation; no path, unit, command or environment input.

## Descriptor and deployment seam

`CandidateDeployment` uses fixed roots under `~/.local/share/leam-next`: data,
releases, venvs and recovery. Operator-only `adopt(data, release, runtime, environment)`
creates `recovery/candidate.json` once. Files are private owned regular files;
private data/recovery directories and non-writable-by-others immutable release/venv
paths are validated without symlinks. Unknown descriptor/environment keys fail.
The descriptor binds installation/generation UUIDs, revision, data path, source and
client hashes, release/Python environment identity, launch environment digest and
MCP certificate/tool-token fingerprints. Runtime metadata contains reviewed
sourceCommit, binarySha256 and ceilingSha256. The latter hashes canonical JSON
`{"allowedCapabilityIds": <sorted list or null>}` from the actual fixed runtime PID's
startup environment. This does not capture or restore mutable permission modes.

Initial adoption belongs to the operator: inspect the currently working candidate,
construct the exact validated descriptor using its immutable release manifest and
verified running runtime identity, retain old unit definitions, then change only
candidate app/MCP ExecStart to the pinned Python's `-m leam_api.candidate_launch app`
and `... mcp` entrypoints (WorkingDirectory remains an immutable launch-code release).
Do not apply this to recovery or runtime. The launcher resolves BOTH app and MCP from
one pointer, sets active LEAM_DATA_DIR/PYTHONPATH, fixed loopback ports/factory options
and current generation TLS paths, and drops stale LEAM_* overrides inherited from the
unit. All intended LEAM_ORIGINS/runtime/IDE socket/install/API overrides must first
be included in the descriptor's strict environment allowlist. OS environment is
otherwise preserved. The installation operator owns adoption/restart validation; source code never edits
units or the live descriptor automatically.

Normal deployment MUST participate in the same `candidate-operation.lock` for its
entire stop/pointer-switch/start sequence, including expected descriptor identity:

```python
with deployment.lock():
    before = deployment.read()
    # Compare the operator's recorded expected revision/generation here too.
    after = deployment.prepare_release_locked(new_release, reviewed_runtime, before)
    # stop only fixed candidate writers; retain before for operator rollback
    deployment.replace_locked(after, before)
    # start and verify fixed services before releasing the operation lock
```

`prepare_release_locked` refuses unfinished restore journals. `replace_locked`
requires this object's held cross-process lock and the exact prior descriptor digest;
normal deployment cannot race a restore or silently keep an old release pointer.
The operator updates reviewed runtime metadata when activating a new runtime/host ceiling.
Existing app release does not dictate recovery's own release. Mobile restore is
restricted to the compatible current active application release and installation.

## Restore, interruption and rollback

Host-local archive UUIDs are selected under current data/backups. Preview opens a
private bounded regular archive without symlinks, validates a stable copy using the
existing archive/schema/vault validator and requires certificate/tool-token hashes
to match current pinned identity. It binds the entire descriptor and archive hash.
The existing original schema remains supported; restored state does not include
Codex/IronClaw history or runtime permissions. Invalid or corrupted current state
cannot bypass the required fresh safety backup.

An independent private JSON journal reserves request ID and full operation digest
before side effects. Same ID/digest returns its receipt; changed payload conflicts.
A cross-process lock prevents concurrent restore and normal deployment. The journal
records quiescing/stopped/restoring/switching/starting/checking plus terminal outcome.
Every record/pointer replacement fsyncs the file and directory. A recovery restart
marks unfinished work needs_review; it never automatically repeats a side effect.
Cancellation waits for any filesystem thread to finish before releasing the lock.

Stop verifies the exact fixed units have MainPID0 and no listening fixed ports before
copying/swapping. A fresh safety archive is saved and restore writes a new generated
private directory. Original product data is retained; explicit rollback adds the automation hold described below. A staged expected after-descriptor is
journaled before atomic pointer replacement, so either side of a crash can be
identified. Runtime trust/settings are untouched; startup checks matching binary
and ceiling, API health and authenticated TLS MCP tools/list. These are operational
checks, not a model-driven read or product UAT; outcome says ready_for_uat explicitly.

On uncertainty/failure, new restores are blocked until explicit rollback. Rollback
binds the exact original operation and currently expected descriptor; later releases
or restores make it stale. Both directories remain. Rollback stops writers again,
points to retained old data, starts/checks and marks related uncertain receipts
resolved. A failed rollback can be explicitly reviewed/retried with a fresh request;
its staged rollback pointer is recorded for that purpose. No external calendar or
Codex action is reversed by this controller. Old sessions in restored DB are
invalidated; sign in again and verify accounts/workers/Companion from the UI.

Source isolated gates verify actual SQLite/vault restore+rollback, preexisting data
preservation, launcher call arguments/environment, key identity mismatch, stale
preview/runtime drift, overlapping operation/deployment locks, crash uncertainty,
stop/health failure and cancellation while a filesystem worker is still active.
Deployment adoption, installed restore/rollback validation and user UAT are separate from source-level verification.

## Automation restart fence and adoption dependency

Before any service starts, restore AND rollback write Store setting
`restore_automation_hold` with exactly `{version:1,requestId,restoredAt,cutoff,state:"held"}`;
all times are Unix seconds and initial cutoff equals restoredAt. The operation receipt
reports automationHeld. This is an intentional metadata change to the preserved
rollback generation: older cursors can otherwise repeat notifications delivered while
using another generation. Domain entities are not discarded.

**Do not adopt this launcher/controller without the coordinated worker consumer.**
The coordinated worker consumer must fail closed on a held/malformed marker in push,
reminder and routine workers and their manual tick/run-now callers. Explicit owner
Settings review/confirmation resumes future work only: account for skipped pending
push and past-due reminder work, advance routine cursors past confirmed resume cutoff,
and persist that cutoff so a missing old reminder cannot be recreated. This source
slice proves the marker exists before fixed services.start on restore AND rollback;
real sender/cursor non-replay acceptance belongs to the integrated consumer drill.
No claim of notification non-replay is made from controller tests alone.

## Recovery browser contract

Recovery supplies authenticated same-origin `/api/restores` list, UUID preview,
explicit confirmed start, receipt GET and explicit rollback preview/start routes.
Existing fixed service start/restart shares candidate-operation.lock, including when
the restore controller is unavailable. Main API may be down throughout orchestration.
The browser persists a request UUID before each single dispatch; uncertainty/reload
only retrieves its receipt, never resends a mutation. A definitive rejection plus
absent receipt requires another explicit review. Logout fences delayed response
bodies and clears private views. Mobile and desktop callers cover restore, rollback,
lost response/reload and delayed preview after logout. Browser tests use controlled
API responses; isolated authenticated API callers actually restore SQLite/vault data.

Launcher preserves the reviewed operator LEAM_CODING_WORKSPACE and the app's fixed
10-second graceful shutdown timeout. Adoption inventories all active public unit
LEAM environment variables into the strict allowlist; unknown variables are rejected,
not silently copied. Recovery remains on its own immutable code/venv during app releases.

Normal operator deployments use `python -m leam_api.candidate_rollout --release
<immutable-directory> --expected-digest <digest-of-reviewed-descriptor>`. The shared
lock remains held across app/MCP stop, exact pointer replacement, startup and
verification or rollback. Runtime/recovery stay running. New client index, both
process working directories and data bindings must match, in addition to runtime
and MCP operational checks. A failed directory fsync after pointer publication is
resolved by observing the actual pointer under lock; unknown results require review.
Repeated cancellation waits for the operation to finish. This CLI stages nothing
and accepts no browser input; deployments require a separately staged clean release.

## Current installation adoption checklist

Source integration alone does not enable mobile restore. The installation owner
must perform these ordered operations in a coordinated idle window:

1. Stage the reviewed integrated release and preserve the current immutable release,
   unit definitions, deployment receipt and runtime receipt. Inventory public launch
   variables from the actual API/MCP units. Include `LEAM_SHARED_CODEX_THREAD` when
   configured; the descriptor validates its UUID and the launcher preserves it.
   Omission intentionally disables shared Coding, so verify the original owner ID
   after adoption. Unknown LEAM variables require reviewed allowlist support.
2. Pin recovery/launcher code independently of the active app release and keep its
   separate auth store. Capture the current runtime source, executable hash and
   canonical host-ceiling hash from its running PID; do not reuse an older receipt.
   Adopt the current data/release/venv and unchanged MCP certificate/tool-token
   identity into the protected candidate descriptor before replacing fixed app/MCP
   launch commands. Preserve the app's 10-second graceful-shutdown argument.
3. Route **all subsequent ordinary deployments** through `candidate_rollout`, with
   the reviewed descriptor digest. Retire any direct switch/restart helper which
   bypasses `candidate-operation.lock`; otherwise restore is unsafe. Runtime changes
   need coordinated descriptor identity updates under the same ownership boundary.
4. Verify exact API/MCP cwd, data binding, client hash, scoped coding policy and
   shared owner. Recovery authentication, service controls and backup list must work
   independently. Confirm automation status is unchanged before any restore.
5. Rehearse a fresh local backup and explicit restore/rollback from mobile. Preserve
   all original generations. Check attachment bytes and vault-backed account state,
   invalidate restored sessions, prove workers held before any send, then explicitly
   review future-only resume. Rollback must hold workers again. Do not use external
   Codex/IronClaw histories or permission modes as restoration success criteria.
6. Record deployed source/client/runtime/descriptor identities and timestamps plus
   actual browser/service evidence. Recovery operational readiness is not user UAT.

If launcher adoption fails before a successful pointer switch, restore the saved
unit definitions and immutable release bindings under operator coordination; retain
both data directories and all journals. Once a mobile operation is reserved, use
its receipt and explicit rollback flow. Never erase a pending journal or replay an
uncertain operation to make the interface appear successful.

The integrated source rehearsal uses private fixture roots, real SQLite archives,
vault/TLS identities, attachment reconstruction and the actual Push sender with an
HTTP MockTransport. It proves no duplicate S→delivery B→restore S send before or
after reviewed resume, and a fresh hold after rollback. It does not contact a real
phone or operate the installed services.

Restore and ordinary rollout both verify the served client hash and each API/MCP
process's immutable cwd and data-directory environment against the descriptor.
Reachable services with an old direct launch binding fail readiness; matching MCP
keys alone are insufficient to prove the restored generation is serving.

Cancellation of an already dispatched service action waits for that action to
settle while holding the operation lock, including repeated cancellations. Restore
then records needs_review without proceeding to its next phase. Manual recovery
start/restart follows the same rule. A mutating systemctl timeout never kills the
client and assumes its systemd job stopped: the lock stays held until the manager
client finishes, then the timeout is reported. A stuck manager requires operator
intervention; availability is not restored by allowing overlapping operations.
Hard process death still requires journal and actual service-state reconciliation.
