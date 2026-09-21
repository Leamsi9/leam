"""Leam companion permissions; Codex owns coding, runtime owns dispatch enforcement."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException

from .commitments import Input
from .ironclaw import IronClaw, RuntimeError

STATES = {"always_allow", "ask_each_time", "disabled"}
ALLOWED = frozenset(
    {
        "builtin.json",
        "builtin.time",
        "ironclaw.memory.read",
        "ironclaw.memory.search",
        "ironclaw.memory.tree",
        *(
            "mcp-leam." + name
            for name in (
                "leam_system",
                "leam_context",
                "leam_today",
                "leam_calendar_events",
                "leam_calendar_links",
                "leam_calendar_inspect",
                "leam_operation_schema",
                "leam_propose",
                "leam_proposals",
            )
        ),
    }
)
LOOP_HELPERS = frozenset(
    {
        "builtin.result_read",
        "builtin.structured_result",
        "ironclaw.tool_search",
        "ironclaw.tool_describe",
        "ironclaw.tool_call",
    }
)
PROTECTED = frozenset(
    {
        "builtin.shell",
        "builtin.write_file",
        "builtin.apply_patch",
        "builtin.spawn_subagent",
        "builtin.http.save",
        "builtin.document_edit",
        "builtin.html_to_pdf",
        "builtin.admin_configuration_replace",
        "builtin.operator_config_set_auto_approve",
        "builtin.operator_config_set_tool_permission",
        "builtin.extension_register_hosted_mcp",
        "builtin.extension_install",
        "builtin.extension_activate",
        "builtin.extension_remove",
        "builtin.ironhub_install",
        "builtin.skill_install",
        "builtin.skill_update",
        "builtin.skill_remove",
        "builtin.skill_auto_activate_set",
        "builtin.skill_auto_activate_learned_set",
    }
)


class Permission(Input):
    state: Literal["always_allow", "ask_each_time", "disabled"]


def capability_id(value):
    return (
        isinstance(value, str)
        and len(value) <= 256
        and re.fullmatch(r"[A-Za-z0-9_.:-]+", value) is not None
    )


def catalog(raw):
    try:
        entries = raw["entries"]
        if not isinstance(entries, list) or len(entries) > 2048:
            raise ValueError()
        items = []
        auto_approve = None
        names = set()
        for entry in entries:
            if entry["key"] == "agent.auto_approve_tools":
                if type(entry["value"]) is not bool:
                    raise ValueError()
                auto_approve = entry["value"]
                continue
            if not entry["key"].startswith("tool.") or entry.get("redacted"):
                raise ValueError()
            name = entry["key"][5:]
            value = entry["value"]
            if (
                not capability_id(name)
                or name in names
                or value["name"] != name
                or value["state"] not in STATES
                or type(value["locked"]) is not bool
                or type(entry["mutable"]) is not bool
                or value["default_state"] not in STATES
                or value["effective_source"]
                not in {"default", "global", "override", "locked"}
            ):
                raise ValueError()
            names.add(name)
            description = value["description"]
            if not isinstance(description, str) or len(description) > 20000:
                raise ValueError()
            items.append(
                {
                    "id": name,
                    "description": description,
                    "state": value["state"],
                    "defaultState": value["default_state"],
                    "source": value["effective_source"],
                    "locked": value["locked"] or not entry["mutable"],
                    "protected": name not in ALLOWED,
                    "recommendedState": "always_allow"
                    if name in ALLOWED
                    else "disabled",
                }
            )
        if auto_approve is None:
            raise ValueError()
        return {
            "autoApprove": auto_approve,
            "items": items,
            "codingSubstrate": "Codex with agent-protocols",
            "modelCatalogFilteringVerified": False,
        }
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise RuntimeError(
            "IronClaw returned an unsupported tool settings response"
        ) from error


class ToolPermissions:
    def __init__(self, runtime):
        self.runtime = runtime
        self.lock = asyncio.Lock()

    async def list(self):
        return catalog(await self.runtime.request("GET", "/settings/tools"))

    async def set(self, key, permission):
        if not capability_id(key):
            raise KeyError("Unknown companion tool")
        async with self.lock:
            state = await self.list()
            item = next((item for item in state["items"] if item["id"] == key), None)
            if item is None:
                raise KeyError("Unknown companion tool")
            if item["locked"]:
                raise ValueError(
                    "IronClaw locks this permission; an operator runtime policy change is required"
                )
            if item["protected"] and permission.state != "disabled":
                raise ValueError(
                    "Coding and runtime administration use Codex with agent-protocols; this companion tool must stay disabled"
                )
            await self.runtime.request(
                "POST",
                "/settings/tools/" + quote(key, safe=""),
                {"state": permission.state},
            )
            current = await self.list()
            result = next(
                (item for item in current["items"] if item["id"] == key), None
            )
            if result is None or result["state"] != permission.state:
                raise RuntimeError(
                    "Runtime permission readback did not match the requested setting; refresh before retrying"
                )
            return result

    @staticmethod
    def preview_token(current):
        state = {
            "autoApprove": current["autoApprove"],
            "allowed": sorted(ALLOWED),
            "items": sorted(
                [
                    {
                        k: item[k]
                        for k in ("id", "state", "source", "locked", "defaultState")
                    }
                    for item in current["items"]
                ],
                key=lambda i: i["id"],
            ),
        }
        return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()

    async def preview(self):
        current = await self.list()
        changes = [
            {
                "id": item["id"],
                "before": item["state"],
                "after": item["recommendedState"],
            }
            for item in current["items"]
            if not item["locked"] and item["state"] != item["recommendedState"]
        ]
        unresolved = [
            item["id"]
            for item in current["items"]
            if item["locked"] and item["state"] != item["recommendedState"]
        ]
        return {
            "previewToken": self.preview_token(current),
            "isolationVerified": False,
            "autoApproveBefore": current["autoApprove"],
            "autoApproveAfter": False,
            "changes": changes,
            "mandatoryApprovalTools": unresolved,
            "missingRecommendedTools": sorted(
                ALLOWED - {item["id"] for item in current["items"]}
            ),
            "unexpectedHelperEntries": sorted(
                LOOP_HELPERS & {item["id"] for item in current["items"]}
            ),
            "modelCatalogFilteringVerified": False,
        }

    async def apply_profile(
        self, backup_file, runtime_url, *, expected_preview=None, record_backup=None
    ):
        # Write before any mutation; explicit operator command, never model-callable.
        previous = await self.list()
        if expected_preview is not None and expected_preview != self.preview_token(
            previous
        ):
            raise ValueError("Tool permissions changed; review a fresh profile preview")
        if LOOP_HELPERS & {item["id"] for item in previous["items"]}:
            raise ValueError(
                "Loop helpers entered the settings catalog; review runtime semantics before applying"
            )
        snapshot = {"version": 1, "runtimeUrl": runtime_url, "settings": previous}
        serialized = json.dumps(snapshot)
        if len(serialized.encode()) > 1024 * 1024:
            raise ValueError("Permission snapshot exceeds the supported restore limit")
        fd = os.open(backup_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write(serialized)
            file.flush()
            os.fsync(file.fileno())
        if record_backup is not None:
            record_backup(snapshot)
        await self.runtime.request("POST", "/settings/tools", {"enabled": False})
        # Revoke unrelated mutable tools before granting the selected narrow tools.
        for item in sorted(previous["items"], key=lambda item: item["id"] in ALLOWED):
            if not item["locked"]:
                await self.runtime.request(
                    "POST",
                    "/settings/tools/" + quote(item["id"], safe=""),
                    {"state": item["recommendedState"]},
                )
        result = await self.preview()
        result["backupFile"] = str(backup_file)
        result["configurationMatched"] = (
            not result["changes"]
            and not result["autoApproveBefore"]
            and not result["missingRecommendedTools"]
        )
        return result

    async def restore_profile(self, backup_file, runtime_url, *, expected_preview=None):
        info = backup_file.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_size > 1024 * 1024
        ):
            raise ValueError("Permission backup must be a private bounded regular file")
        saved = json.loads(backup_file.read_text())
        if (
            type(saved.get("version")) is not int
            or saved.get("version") != 1
            or saved.get("runtimeUrl") != runtime_url
        ):
            raise ValueError("Permission backup does not match this runtime")
        previous = saved["settings"]
        if (
            type(previous.get("autoApprove")) is not bool
            or not isinstance(previous.get("items"), list)
            or len(previous["items"]) > 2048
        ):
            raise ValueError("Permission backup contains unsupported settings")
        ids = set()
        for item in previous["items"]:
            if (
                not capability_id(item.get("id"))
                or item["id"] in ids
                or item.get("state") not in STATES
                or item.get("defaultState") not in STATES
                or type(item.get("locked")) is not bool
                or item.get("source") not in {"default", "global", "override", "locked"}
            ):
                raise ValueError("Permission backup contains unsupported state")
            ids.add(item["id"])
        current = await self.list()
        if expected_preview is not None and expected_preview != self.preview_token(
            current
        ):
            raise ValueError("Tool permissions changed; review a fresh restore preview")
        if {item["id"] for item in previous["items"]} != {
            item["id"] for item in current["items"]
        }:
            raise ValueError("Tool catalog changed; review the backup before restoring")
        current_by_id = {item["id"]: item for item in current["items"]}
        if any(
            item[field] != current_by_id[item["id"]][field]
            for item in previous["items"]
            for field in ("locked", "defaultState")
        ):
            raise ValueError("Tool locks or defaults changed; review before restoring")
        # Restoring is explicitly an operator rollback, not the companion UI's grant path.
        await self.runtime.request("POST", "/settings/tools", {"enabled": False})
        for item in previous["items"]:
            if not item["locked"]:
                state = (
                    "default"
                    if item["source"] in {"default", "global"}
                    else item["state"]
                )
                if state not in STATES | {"default"} or not capability_id(item["id"]):
                    raise ValueError("Permission backup contains unsupported state")
                await self.runtime.request(
                    "POST",
                    "/settings/tools/" + quote(item["id"], safe=""),
                    {"state": state},
                )
        await self.runtime.request(
            "POST", "/settings/tools", {"enabled": previous["autoApprove"]}
        )
        restored = await self.list()
        mismatch = [
            item["id"]
            for item in restored["items"]
            if any(
                item[field]
                != next(
                    old[field] for old in previous["items"] if old["id"] == item["id"]
                )
                for field in ("state", "source", "locked", "defaultState")
            )
        ]
        if mismatch or restored["autoApprove"] != previous["autoApprove"]:
            raise ValueError(
                "Permission rollback readback mismatch; inspect runtime settings"
            )
        return {"restored": True, "count": len(restored["items"])}


def router(permissions):
    routes = APIRouter(prefix="/api/companion/tools")

    @routes.get("")
    async def listing():
        try:
            return await permissions.list()
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from error

    @routes.post("/{key}")
    async def setting(key: str, body: Permission):
        try:
            return await permissions.set(key, body)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from error

    return routes


def main():
    parser = argparse.ArgumentParser(
        description="Preview/apply/restore the Leam companion tool profile; changes require an explicit command"
    )
    parser.add_argument("--runtime-url", default="http://127.0.0.1:46410")
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("command", choices=["preview", "apply", "restore"])
    parser.add_argument("--backup-file", type=Path)
    parser.add_argument("--preview-token")
    args = parser.parse_args()
    if args.command != "preview" and args.backup_file is None:
        parser.error("apply/restore requires --backup-file")

    if args.command == "apply" and not args.preview_token:
        parser.error("apply requires the reviewed --preview-token")

    async def run():
        runtime = IronClaw(args.runtime_url, args.token_file)
        try:
            permissions = ToolPermissions(runtime)
            if args.command == "preview":
                return await permissions.preview()
            if args.command == "apply":
                return await permissions.apply_profile(
                    args.backup_file,
                    args.runtime_url,
                    expected_preview=args.preview_token,
                )
            return await permissions.restore_profile(args.backup_file, args.runtime_url)
        finally:
            await runtime.close()

    try:
        result = asyncio.run(run())
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        parser.exit(1, f"Tool profile operation failed: {error}\n")
    print(json.dumps(result))
    if args.command == "apply" and not result["configurationMatched"]:
        parser.exit(
            2, "Profile is partial; inspect remaining changes or missing tools.\n"
        )


if __name__ == "__main__":
    main()
