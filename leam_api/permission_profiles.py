"""Explicit owner profile changes, pre-write backups and durable non-replay receipts."""

import asyncio
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import Field

from .commitments import Input
from .ironclaw import RuntimeError


class ApplyProfile(Input):
    requestId: UUID
    previewToken: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: Literal[True]


class RestoreProfile(ApplyProfile):
    backupId: UUID


class PermissionProfiles:
    def __init__(self, store, permissions, inspector):
        self.store, self.permissions, self.inspector = store, permissions, inspector
        self.directory = store.path.parent / "permission-snapshots"
        if self.directory.is_symlink():
            raise ValueError("Permission snapshot directory must not be a symlink")
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.directory.chmod(0o700)

    @property
    def runtime_url(self):
        client = getattr(self.permissions.runtime, "client", None)
        if client is None:
            raise HTTPException(
                503, "This runtime adapter cannot identify its permission scope"
            )
        return str(client.base_url).rstrip("/")

    @staticmethod
    def key(request_id):
        return "permission-profile:" + str(request_id)

    def records(self):
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT value FROM settings WHERE key GLOB 'permission-profile:*' ORDER BY rowid DESC LIMIT 50"
            ).fetchall()
        records = [json.loads(row["value"]) for row in rows]
        return [
            self.public(record)
            for record in sorted(records, key=lambda r: r["created"], reverse=True)[:50]
        ]

    @staticmethod
    def public(record):
        return {
            k: record.get(k)
            for k in (
                "requestId",
                "operation",
                "created",
                "state",
                "message",
                "backupId",
                "hasSnapshot",
            )
        }

    async def preview(self):
        result = await self.permissions.preview()
        result["hostCeiling"] = (await self.inspector.tools())["data"]["hostCeiling"]
        result["history"] = self.records()
        result["modeExplanation"] = (
            "Global automatic approval will be off; the 14 selected domain tools receive explicit Allow. Proposal execution follows its separate domain approval policy."
        )
        return result

    def snapshot(self, backup_id):
        record = self.store.get(self.key(backup_id))
        if not record or not record.get("snapshot"):
            raise HTTPException(404, "Permission snapshot is unavailable")
        return record["snapshot"]

    async def restore_preview(self, backup_id):
        saved = self.snapshot(backup_id)
        current = await self.permissions.list()
        previous = {item["id"]: item for item in saved["settings"]["items"]}
        compatible = saved.get("runtimeUrl") == self.runtime_url and set(previous) == {
            i["id"] for i in current["items"]
        }
        if compatible:
            compatible = all(
                previous[item["id"]][key] == item[key]
                for item in current["items"]
                for key in ("locked", "defaultState")
            )
        digest = hashlib.sha256(
            json.dumps(
                {
                    "current": self.permissions.preview_token(current),
                    "snapshot": saved,
                    "backupId": str(backup_id),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return {
            "previewToken": digest,
            "currentPermissionsToken": self.permissions.preview_token(current),
            "backupId": str(backup_id),
            "compatible": compatible,
            "autoApproveBefore": current["autoApprove"],
            "autoApproveAfter": saved["settings"]["autoApprove"],
            "changes": [
                {
                    "id": item["id"],
                    "before": item["state"],
                    "after": previous[item["id"]]["state"],
                }
                for item in current["items"]
                if item["id"] in previous
                and item["state"] != previous[item["id"]]["state"]
            ],
            "warning": "Restoring previous permission modes can widen access. The host ceiling is unchanged.",
        }

    def write_snapshot(self, request_id, saved):
        path = self.directory / (str(request_id) + ".json")
        # Caller IDs are UUIDs, never paths. A saved DB snapshot survives ordinary
        # Leam backup restoration even when the redundant private file is absent.
        serialized = json.dumps(saved)
        if len(serialized.encode()) > 1024 * 1024:
            raise ValueError("Permission snapshot exceeds the supported restore limit")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write(serialized)
            file.flush()
            os.fsync(file.fileno())
        return path

    async def execute(self, operation, body):
        request_id = str(body.requestId)
        fingerprint = hashlib.sha256(
            json.dumps(
                {"operation": operation, **body.model_dump(mode="json")}, sort_keys=True
            ).encode()
        ).hexdigest()
        async with self.permissions.lock:
            old = self.store.get(self.key(request_id))
            if old:
                if old["fingerprint"] != fingerprint:
                    raise HTTPException(
                        409, "Request ID already belongs to a different profile action"
                    )
                return self.public(old)
            if operation == "apply":
                preview = await self.permissions.preview()
                if preview["unexpectedHelperEntries"]:
                    raise HTTPException(
                        409,
                        "Loop helper settings changed; review the runtime catalog before applying",
                    )
                if preview["missingRecommendedTools"]:
                    raise HTTPException(
                        409,
                        "Recommended tools are missing; refresh the runtime catalog first",
                    )
            else:
                preview = await self.restore_preview(body.backupId)
                if not preview["compatible"]:
                    raise HTTPException(
                        409,
                        "Runtime locks, defaults or tool inventory changed; restore is unavailable",
                    )
            if preview["previewToken"] != body.previewToken:
                raise HTTPException(
                    409, "Permissions changed. Review a fresh preview before confirming"
                )
            record = {
                "requestId": request_id,
                "fingerprint": fingerprint,
                "operation": operation,
                "created": time.time(),
                "state": "in_progress",
                "message": "Awaiting runtime readback; never replay this request automatically.",
                "backupId": request_id,
                "hasSnapshot": False,
            }
            # Claim durably before any upstream writes. The transaction also
            # fences another process using the same installation during recovery.
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute(
                    "SELECT value FROM settings WHERE key=?", (self.key(request_id),)
                ).fetchone()
                if existing:
                    old = json.loads(existing["value"])
                    if old["fingerprint"] != fingerprint:
                        raise HTTPException(
                            409,
                            "Request ID already belongs to a different profile action",
                        )
                    return self.public(old)
                db.execute(
                    "INSERT INTO settings VALUES (?, ?)",
                    (self.key(request_id), json.dumps(record)),
                )

            def save_snapshot(saved):
                record["snapshot"] = saved
                record["hasSnapshot"] = True
                self.store.set(self.key(request_id), record)

            try:
                async with asyncio.timeout(45):
                    if operation == "apply":
                        result = await self.permissions.apply_profile(
                            self.directory / (request_id + ".json"),
                            self.runtime_url,
                            expected_preview=body.previewToken,
                            record_backup=save_snapshot,
                        )
                        if not result["configurationMatched"]:
                            raise ValueError(
                                "Permission readback did not match the profile"
                            )
                    else:
                        current = await self.permissions.list()
                        if (
                            self.permissions.preview_token(current)
                            != preview["currentPermissionsToken"]
                        ):
                            raise ValueError(
                                "Permissions changed after the restore preview"
                            )
                        snapshot = {
                            "version": 1,
                            "runtimeUrl": self.runtime_url,
                            "settings": current,
                        }
                        self.write_snapshot(request_id, snapshot)
                        save_snapshot(snapshot)
                        target = self.snapshot(body.backupId)
                        # A separate fresh UUID avoids replacing an existing snapshot.
                        with tempfile.TemporaryDirectory(
                            dir=self.directory
                        ) as directory:
                            path = Path(directory) / "restore.json"
                            path.write_text(json.dumps(target))
                            path.chmod(0o600)
                            await self.permissions.restore_profile(
                                path,
                                self.runtime_url,
                                expected_preview=preview["currentPermissionsToken"],
                            )
                record.update(
                    state="complete",
                    message="Runtime permission modes verified. Host ceiling was not changed.",
                )
            except (RuntimeError, OSError, ValueError, TimeoutError):
                record.update(
                    state="needs_review",
                    message="The change may be partial. Refresh actual permissions and review the saved snapshot before another explicit action. This request will not be replayed.",
                )
            except asyncio.CancelledError:
                record.update(
                    state="needs_review",
                    message="Operation interrupted; inspect actual permissions before proceeding. No automatic retry.",
                )
                raise
            finally:
                self.store.set(self.key(request_id), record)
            return self.public(record)


def router(profiles):
    routes = APIRouter(prefix="/api/companion/tools/profile")

    @routes.get("")
    async def preview():
        return await profiles.preview()

    @routes.get("/restore/{backup_id}")
    async def restore_preview(backup_id: UUID):
        return await profiles.restore_preview(backup_id)

    @routes.post("/apply")
    async def apply(body: ApplyProfile):
        return await profiles.execute("apply", body)

    @routes.post("/restore")
    async def restore(body: RestoreProfile):
        return await profiles.execute("restore", body)

    return routes
