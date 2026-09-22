"""Durable candidate-only restore controller, independent of the product database."""

import asyncio
import json
import tempfile
import time
from functools import partial
from pathlib import Path
from types import SimpleNamespace

from .backups import (
    ARCHIVE_OVERHEAD,
    MAX_BYTES,
    Backups,
    private_copy,
    private_read,
    restore,
    unpack,
)
from .candidate_deployment import (
    atomic_json,
    digest,
    installation_identity,
    private_directory,
    uuid_value,
)
from .operation_wait import settled
from .store import Store


async def blocking(function, *arguments):
    # Executor futures are not independently cancelled by asyncio's all-tasks
    # shutdown. Shield every await, including repeated cancellation requests, so
    # the caller retains its lock/temp files until the physical worker exits.
    worker = asyncio.get_running_loop().run_in_executor(
        None, partial(function, *arguments)
    )
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(worker)
            break
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            if cancelled:
                raise asyncio.CancelledError() from None
            raise
    if cancelled:
        raise asyncio.CancelledError()
    return result


TERMINAL = {"ready_for_uat", "rolled_back", "needs_review"}
PUBLIC = (
    "requestId",
    "operation",
    "state",
    "createdAt",
    "updatedAt",
    "message",
    "backupId",
    "restoreRequestId",
    "safetyBackupId",
    "beforeGeneration",
    "afterGeneration",
    "automationHeld",
)


class RestoreController:
    def __init__(self, deployment, services):
        self.deployment, self.services = deployment, services
        self.directory = deployment.roots.recovery / "restore-operations"
        self.directory.mkdir(mode=0o700, exist_ok=True)
        private_directory(self.directory)
        self.tasks = {}
        # A restarted recovery process reports uncertainty; it never guesses that
        # a previously started service/configuration side effect should be replayed.
        with deployment.lock():
            for path in self.directory.glob("*.json"):
                record = self._read(path.stem)
                if record["state"] not in TERMINAL:
                    self._save(
                        record,
                        "needs_review",
                        "Recovery restarted during this operation. Inspect state and use its explicit rollback; no action was replayed.",
                    )

    def _path(self, request_id):
        return self.directory / (uuid_value(request_id) + ".json")

    def _read(self, request_id):
        return json.loads(private_read(self._path(request_id), 256 * 1024))

    def _save(self, record, state, message):
        record.update(state=state, message=message, updatedAt=time.time())
        atomic_json(self._path(record["requestId"]), record)

    @staticmethod
    def public(record):
        return {key: record[key] for key in PUBLIC if key in record}

    def status(self, request_id):
        return self.public(self._read(request_id))

    def history(self):
        rows = [self._read(path.stem) for path in self.directory.glob("*.json")]
        return [
            self.public(row)
            for row in sorted(rows, key=lambda row: row["createdAt"], reverse=True)[:50]
        ]

    def listing(self):
        value = self.deployment.read()
        directory = Path(value["dataDirectory"]) / "backups"
        if not directory.exists():
            return {"items": [], "generationId": value["generationId"]}
        private_directory(directory)
        manager = Backups(
            SimpleNamespace(path=Path(value["dataDirectory"]) / "leam.sqlite3")
        )
        result = manager.list()
        # Independent recovery downloads are not implemented; do not emit main-API URLs.
        return {
            "items": [
                {k: row[k] for k in ("id", "createdAt", "bytes")}
                for row in result["items"]
            ],
            "generationId": value["generationId"],
        }

    async def _runtime(self, value):
        if await self.services.identity() != value["runtime"]:
            raise ValueError(
                "Running runtime or host ceiling changed; operator must update the verified descriptor"
            )

    def _validated_archive(self, value, backup_id, stable_archive=None):
        directory = Path(value["dataDirectory"]) / "backups"
        private_directory(directory)
        source = directory / (uuid_value(backup_id) + ".zip")
        # Open a private regular file without following symlinks, then validate a
        # stable private copy. A replacement of the original cannot race extraction.
        with tempfile.TemporaryDirectory(
            prefix=".verify-", dir=self.directory
        ) as temporary:
            root = Path(temporary)
            archive = (
                stable_archive if stable_archive is not None else root / "archive.zip"
            )
            metadata = private_copy(source, archive, MAX_BYTES + ARCHIVE_OVERHEAD)
            target = root / "data"
            target.mkdir(mode=0o700)
            unpack(archive, target)
            if installation_identity(target) != value["mcpIdentity"]:
                raise ValueError(
                    "Backup belongs to a different MCP installation identity"
                )
        return metadata["sha256"]

    async def preview(self, backup_id):
        value = self.deployment.read()
        await self._runtime(value)
        archive_hash = await blocking(self._validated_archive, value, backup_id)
        token = digest(
            {
                "operation": "restore",
                "descriptor": value,
                "backupId": backup_id,
                "archiveSha256": archive_hash,
            }
        )
        return {
            "backupId": backup_id,
            "previewToken": token,
            "archiveSha256": archive_hash,
            "generationId": value["generationId"],
            "sourceCommit": value["sourceCommit"],
            "identityMatched": True,
            "sessionsInvalidated": True,
            "warning": "This replaces Leam product state with the selected snapshot. A fresh safety backup and the original directory are retained. External calendar/Codex actions and runtime permissions are not undone. Scheduled automations remain paused until you review and resume them in Settings. Sign in again after restore.",
        }

    async def rollback_preview(self, restore_id):
        record = self._read(restore_id)
        if (
            record["operation"] != "restore"
            or record["state"] == "rolled_back"
            or not record.get("beforeDescriptor")
        ):
            raise ValueError("This operation cannot be rolled back")
        current = self.deployment.read()
        before = record["beforeDescriptor"]
        after = record.get("afterDescriptor")
        if digest(current) not in {
            digest(before),
            digest(after) if after else "",
            (
                digest(record["rollbackDescriptor"])
                if record.get("rollbackDescriptor")
                else ""
            ),
        }:
            raise ValueError(
                "A later deployment or restore is active; this rollback is stale"
            )
        if current["runtime"] != before["runtime"]:
            raise ValueError("Runtime identity changed; operator review is required")
        # Runtime may be stopped after a failed restore. Startup will use the same
        # retained config; rollback cannot install a runtime or alter its settings.
        token = digest(
            {
                "operation": "rollback",
                "restoreRequestId": restore_id,
                "descriptor": current,
                "target": before,
            }
        )
        return {
            "restoreRequestId": restore_id,
            "previewToken": token,
            "generationId": current["generationId"],
            "targetGeneration": before["generationId"],
            "warning": "Stop candidate services and return to the retained pre-restore product state. Both data directories remain. Scheduled automations are paused until you review and resume them in Settings. External actions and runtime permissions are not undone.",
        }

    async def start_restore(self, request_id, backup_id, token):
        return await self._start("restore", request_id, backup_id, token)

    async def start_rollback(self, request_id, restore_id, token):
        return await self._start("rollback", request_id, restore_id, token)

    async def _start(self, operation, request_id, target_id, token):
        uuid_value(request_id)
        uuid_value(target_id)
        fingerprint = digest(
            {"operation": operation, "target": target_id, "previewToken": token}
        )
        path = self._path(request_id)
        if path.exists():
            old = self._read(request_id)
            if old["fingerprint"] != fingerprint:
                raise ValueError("Request identity belongs to another operation")
            return self.public(old)
        guard = self.deployment.lock()
        guard.__enter__()
        transferred = False
        try:
            # Recheck after taking the cross-process deployment/restore lock.
            if path.exists():
                old = self._read(request_id)
                if old["fingerprint"] != fingerprint:
                    raise ValueError("Request identity belongs to another operation")
                return self.public(old)
            if operation == "restore" and any(
                self._read(p.stem)["state"] == "needs_review"
                for p in self.directory.glob("*.json")
            ):
                raise ValueError(
                    "An uncertain operation needs explicit rollback before another restore"
                )
            admission = getattr(self.services, "ensure_drained", None)
            if admission is not None:
                await admission()
            preview = await (
                self.preview(target_id)
                if operation == "restore"
                else self.rollback_preview(target_id)
            )
            if preview["previewToken"] != token:
                raise ValueError(
                    "Preview changed; inspect a fresh preview before confirming"
                )
            current = self.deployment.read()
            record = {
                "requestId": request_id,
                "operation": operation,
                "fingerprint": fingerprint,
                "createdAt": time.time(),
                "beforeDescriptor": current,
                "beforeGeneration": current["generationId"],
                (
                    "backupId" if operation == "restore" else "restoreRequestId"
                ): target_id,
            }
            if operation == "rollback":
                record["targetDescriptor"] = self._read(target_id)["beforeDescriptor"]
            self._save(
                record,
                "reserved",
                "Reserved once. Refresh this receipt after a lost response; never repeat with a new identity.",
            )
            task = asyncio.create_task(self._run(record, guard))
            self.tasks[request_id] = task
            task.add_done_callback(lambda _task: self.tasks.pop(request_id, None))
            transferred = True
            return self.public(record)
        finally:
            if not transferred:
                guard.__exit__(None, None, None)

    async def wait(self, request_id):
        task = self.tasks.get(request_id)
        if task:
            await task

    async def _run(self, record, guard):
        try:
            before = record["beforeDescriptor"]
            self._save(
                record,
                "quiescing",
                "Stopping only the fixed candidate services before copying or switching data.",
            )
            await settled(self.services.stop())
            self._save(
                record,
                "stopped",
                "Candidate writers stopped. The existing data directory remains untouched.",
            )
            if record["operation"] == "restore":
                target = self.deployment.roots.data / (
                    "restored-" + record["requestId"]
                )
                with tempfile.TemporaryDirectory(
                    prefix=".restore-", dir=self.directory
                ) as temporary:
                    archive = Path(temporary) / "archive.zip"
                    # Bind again to the archive inspected under the operation lock.
                    archive_hash = await blocking(
                        self._validated_archive, before, record["backupId"], archive
                    )
                    expected = digest(
                        {
                            "operation": "restore",
                            "descriptor": before,
                            "backupId": record["backupId"],
                            "archiveSha256": archive_hash,
                        }
                    )
                    actual = digest(
                        {
                            "operation": "restore",
                            "target": record["backupId"],
                            "previewToken": expected,
                        }
                    )
                    if actual != record["fingerprint"]:
                        raise ValueError("Backup changed after confirmation")
                    self._save(
                        record,
                        "restoring",
                        "Saving a fresh safety backup and restoring into a new private directory.",
                    )
                    result = await blocking(
                        restore, archive, target, Path(before["dataDirectory"])
                    )
                record["safetyBackupId"] = Path(result["safetyBackup"]).stem
                after = {
                    **before,
                    "generationId": record["requestId"],
                    "dataDirectory": str(target),
                }
            else:
                after = record["targetDescriptor"]
            # Every generation entering service gets a fresh durable hold, even
            # rollback: deliveries made while using the alternate generation must
            # not be replayed from older scheduler/delivery cursors.
            held_at = time.time()
            await blocking(
                Store(Path(after["dataDirectory"])).set,
                "restore_automation_hold",
                {
                    "version": 1,
                    "requestId": record["requestId"],
                    "restoredAt": held_at,
                    "cutoff": held_at,
                    "state": "held",
                },
            )
            record["automationHeld"] = True
            # Publish intent before the atomic pointer side effect. A crash can be
            # reconciled against either exact descriptor, never guessed/replayed.
            after = {**after, "revision": before["revision"] + 1}
            record["afterDescriptor"] = after
            record["afterGeneration"] = after["generationId"]
            self._save(
                record,
                "switching",
                "Switching the shared candidate data pointer; prior generation is retained.",
            )
            if record["operation"] == "rollback":
                original = self._read(record["restoreRequestId"])
                original["rollbackDescriptor"] = after
                atomic_json(self._path(original["requestId"]), original)
            self.deployment.replace_locked(after, before)
            self._save(
                record,
                "starting",
                "Starting the fixed candidate services from the shared descriptor.",
            )
            await settled(self.services.start())
            self._save(
                record,
                "checking",
                "Checking service and API reachability. Product and MCP acceptance still require verification.",
            )
            if not await self.services.healthy():
                raise ValueError("Candidate readiness checks failed")
            if record["operation"] == "rollback":
                original_id = record["restoreRequestId"]
                for path in self.directory.glob("*.json"):
                    previous = self._read(path.stem)
                    if previous["requestId"] != record["requestId"] and (
                        previous["requestId"] == original_id
                        or previous.get("restoreRequestId") == original_id
                    ):
                        previous["resolvedBy"] = record["requestId"]
                        self._save(
                            previous,
                            "rolled_back",
                            "Returned to the preserved original generation by an explicit rollback.",
                        )
            state = (
                "ready_for_uat" if record["operation"] == "restore" else "rolled_back"
            )
            self._save(
                record,
                state,
                "Candidate services are reachable. Scheduled automations are paused: review and resume them in Settings. Sign in and verify restored state and Companion tools; user acceptance is still pending.",
            )
        except asyncio.CancelledError:
            self._save(
                record,
                "needs_review",
                "Operation interrupted. Inspect the current generation and use explicit rollback; no automatic replay.",
            )
            raise
        except Exception:  # noqa: BLE001 - journal every failure without exposing private diagnostics
            self._save(
                record,
                "needs_review",
                "Restore or readiness could not finish. Both generations remain preserved. Inspect this receipt and use explicit rollback.",
            )
        finally:
            guard.__exit__(None, None, None)
