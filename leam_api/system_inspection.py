"""Bounded read-only evidence for Leam's browser and companion inspection tool."""

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Literal

from .commitments import Input
from .host_tool_ceiling import evidence as ceiling_evidence
from .host_tool_ceiling import read_environment
from .ironclaw import RuntimeError
from .tool_permissions import ToolPermissions
from .web_search import integration_status as web_search_status

Section = Literal["summary", "model", "release", "modules", "operations", "tools"]


class SystemQuery(Input):
    section: Section = "summary"


def observation(source, data, *, status="available", data_as_of=None, scope=None):
    return {
        "status": status,
        "source": source,
        "observedAt": time.time(),
        "dataAsOf": data_as_of,
        "scope": scope,
        "data": data,
    }


OPERATIONS = [
    {
        "id": "companion-tool-permissions",
        "access": "browser",
        "destination": "Settings",
        "authority": "authenticated owner reviews a preview and explicitly confirms apply/restore; host ceiling changes use Codex/operator deployment",
    },
    {
        "id": "inspect",
        "access": "read",
        "destination": "Settings",
        "authority": "authenticated owner or companion tool",
    },
    {
        "id": "personal-context",
        "access": "read",
        "destination": "Companion",
        "authority": "companion domain tools",
    },
    {
        "id": "domain-changes",
        "access": "propose",
        "destination": "Companion",
        "authority": "memory changes follow Settings > Memory approval policy (automatic by default); other changes require explicit user approval",
    },
    {
        "id": "model-and-reasoning",
        "access": "browser",
        "destination": "Settings",
        "authority": "authenticated owner",
    },
    {
        "id": "accounts-and-backups",
        "access": "browser",
        "destination": "Settings",
        "authority": "authenticated owner",
    },
    {
        "id": "routines",
        "access": "browser",
        "destination": "Routines",
        "authority": "authenticated owner",
    },
    {
        "id": "service-start-restart",
        "access": "separate-recovery",
        "destination": "Recovery",
        "authority": "separate recovery sign-in; fixed service allowlist",
    },
    {
        "id": "code-and-infrastructure",
        "access": "coding",
        "destination": "Coding",
        "authority": "Companion can prepare coding.handoff prompts for review now. The user edits and reviews it in More > Approvals, then confirms Send to main for the exact selected Main, or Start in Coding for a dedicated session only if no Main is configured. Validated agent-protocols remain required. No automatic execution or companion approval; an accepted handoff receipt proves dispatch, not completion.",
    },
]


class SystemInspector:
    def __init__(
        self,
        store,
        runtime,
        *,
        scheduler=None,
        routines=None,
        codex=None,
        shared=None,
        root=None,
        probe=None,
    ):
        self.store, self.runtime = store, runtime
        self.scheduler, self.routines, self.codex, self.shared = (
            scheduler,
            routines,
            codex,
            shared,
        )
        self.root = root or Path(__file__).resolve().parents[1]
        self.started = time.time()
        self.probe = probe or self._services
        self.binary_cache = {}
        self.probe_task = None

    async def model(self):
        try:
            response = await asyncio.wait_for(
                self.runtime.request("GET", "/llm/providers"), 3
            )
            active = response.get("active") or {}
            data = {
                key: active.get(key)
                if isinstance(active.get(key), str) and len(active[key]) <= 200
                else None
                for key in ("provider_id", "model", "reasoning_effort")
            }
            return observation(
                "IronClaw live /llm/providers",
                data,
                status="available" if data["model"] else "unavailable",
                scope="Configured selection for new turns; an existing turn may retain an earlier selection. Provider-internal weights/build version is not exposed.",
            )
        except (RuntimeError, TimeoutError, AttributeError, TypeError):
            return observation(
                "IronClaw live /llm/providers",
                None,
                status="unavailable",
                scope="No model selection confirmed",
            )

    def _services(self):
        units = {
            "api": "leam-next-candidate.service",
            "runtime": "leam-next-runtime.service",
            "mcp": "leam-next-mcp.service",
            "recovery": "leam-next-recovery.service",
        }
        raw = subprocess.run(
            [
                "/usr/bin/systemctl",
                "--user",
                "show",
                *units.values(),
                "--property=Id,ActiveState,MainPID,ExecMainStartTimestampMonotonic",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        ).stdout
        result = {}
        for block in raw.strip().split("\n\n"):
            values = dict(
                line.split("=", 1) for line in block.splitlines() if "=" in line
            )
            name = next(
                (key for key, unit in units.items() if values.get("Id") == unit), None
            )
            if name is None:
                continue
            state = values.get("ActiveState")
            result[name] = {
                "state": state
                if state
                in {
                    "active",
                    "inactive",
                    "failed",
                    "activating",
                    "deactivating",
                    "reloading",
                }
                else "unknown"
            }
            if name == "runtime" and state == "active":
                pid = int(values.get("MainPID", "0"))
                identity = (pid, values.get("ExecMainStartTimestampMonotonic"))
                if pid <= 0:
                    continue
                if identity not in self.binary_cache:
                    with open(f"/proc/{pid}/exe", "rb") as binary:
                        digest = hashlib.file_digest(binary, "sha256").hexdigest()
                    ceiling = read_environment(pid)
                    current = subprocess.check_output(
                        [
                            "/usr/bin/systemctl",
                            "--user",
                            "show",
                            units["runtime"],
                            "--property=MainPID,ExecMainStartTimestampMonotonic",
                        ],
                        text=True,
                        timeout=2,
                    )
                    if (
                        f"MainPID={pid}\n" not in current
                        or f"ExecMainStartTimestampMonotonic={identity[1]}\n"
                        not in current
                    ):
                        raise ValueError("Runtime changed during inspection")
                    self.binary_cache = {
                        identity: {
                            "sha256": digest,
                            "observedAt": time.time(),
                            "hostCeiling": ceiling,
                        }
                    }
                cached = self.binary_cache[identity]
                result[name]["binary"] = {
                    k: cached[k] for k in ("sha256", "observedAt")
                }
                result[name]["hostCeiling"] = cached.get("hostCeiling")
        return result

    async def release(self):
        data = {
            "apiProcessId": os.getpid(),
            "inspectorStartedAt": self.started,
            "leam": None,
            "runtime": None,
            "services": None,
        }
        status = "available"
        try:
            manifest = json.loads((self.root / "release.json").read_text())
            if not isinstance(manifest, dict):
                raise TypeError("Release manifest must be an object")
            leam = {
                key: manifest.get(key)
                for key in (
                    "sourceCommit",
                    "clientIndexSha256",
                    "requirementsSha256",
                    "stagedAt",
                )
            }
            if not all(
                isinstance(leam[key], str)
                and re.fullmatch(r"[a-f0-9]{40,64}", leam[key])
                for key in ("sourceCommit", "clientIndexSha256", "requirementsSha256")
            ):
                raise ValueError("Invalid release identity")
            leam["clientHashMatches"] = (
                hashlib.sha256(
                    (self.root / "apps/web/dist/index.html").read_bytes()
                ).hexdigest()
                == leam["clientIndexSha256"]
            )
            data["leam"] = leam
            if not leam["clientHashMatches"]:
                status = "partial"
        except (OSError, ValueError, TypeError):
            status = "partial"
        try:
            lock = json.loads(
                (self.root / "docs/architecture/runtime-lock.json").read_text()
            )
            if not isinstance(lock, dict):
                raise TypeError("Runtime lock must be an object")
            data["runtime"] = {
                "expectedVersion": lock.get("tag"),
                "expectedSourceCommit": lock.get("sourceCommit"),
                "expectedBinarySha256": lock.get("sha256"),
                "runningBinaryMatches": None,
            }
            if self.probe_task is None or self.probe_task.done():
                self.probe_task = asyncio.create_task(asyncio.to_thread(self.probe))
                self.probe_task.add_done_callback(
                    lambda task: None if task.cancelled() else task.exception()
                )
            services = await asyncio.wait_for(asyncio.shield(self.probe_task), 5)
            data["services"] = services
            binary = services.get("runtime", {}).get("binary")
            if binary:
                data["runtime"].update(
                    {
                        "observedBinarySha256": binary["sha256"],
                        "binaryObservedAt": binary["observedAt"],
                        "runningBinaryMatches": binary["sha256"] == lock.get("sha256"),
                    }
                )
            if data["runtime"]["runningBinaryMatches"] is not True:
                status = "partial"
        except (
            OSError,
            ValueError,
            TypeError,
            TimeoutError,
            subprocess.SubprocessError,
        ):
            status = "partial"
        return observation(
            "Running API release manifest/client bytes; fixed systemd service status and /proc executable bytes",
            data,
            status=status,
            scope="Only matching running binary bytes verify the runtime lock; missing metadata is unverified, not a successful deployment check.",
        )

    async def tools(self):
        release = await self.release()
        ceiling = ceiling_evidence(self.root, release)
        try:
            modes = await asyncio.wait_for(ToolPermissions(self.runtime).list(), 4)
        except (RuntimeError, TimeoutError):
            modes = None
        return observation(
            "Fixed runtime executable hash and allowlist environment; caller-scoped live tool settings",
            {
                "hostCeiling": ceiling,
                "mutablePermissions": modes,
                "webSearch": web_search_status(modes),
            },
            status="available"
            if ceiling["enforcementStatus"] != "unknown" and modes
            else "partial",
            scope="Host configuration evidence and mutable approval modes are separate. No live behavioral acceptance is inferred. Codex remains the engineering substrate.",
        )

    async def modules(self):
        def worker(instance):
            if instance is None:
                return {"status": "unavailable", "lastCheck": None}
            last = instance.last_check
            return {
                "status": "healthy"
                if last is not None
                and instance.last_error is None
                and instance.clock() - last < 45
                else "not_healthy",
                "lastCheck": last,
                "hasError": instance.last_error is not None,
            }

        with self.store.connect() as db:
            accounts = {
                "total": db.execute("SELECT count(*) FROM accounts").fetchone()[0]
            }
            push = db.execute(
                "SELECT count(*) FROM push_devices WHERE state='active'"
            ).fetchone()[0]
        data = {
            "routines": worker(self.routines),
            "reminders": worker(self.scheduler),
            "accounts": accounts,
            "registeredPushDevices": push,
            "coding": {
                "nativeTransportConnected": bool(getattr(self.codex, "ready", False)),
                "sharedTransportConnected": bool(
                    getattr(self.shared, "connected", False)
                ),
                "inspectionStartsSessions": False,
            },
            "research": {"status": "deferred"},
            "decisionBackend": {"status": "deferred", "selected": None},
            "tools": None,
            "failureHistory": {
                "status": "not_instrumented",
                "scope": "Absence of this data does not mean no tool failures occurred.",
            },
        }
        try:
            result = await asyncio.wait_for(
                self.runtime.request("GET", "/settings/tools"), 3
            )
            rows = result.get("entries", [])
            data["tools"] = [
                {"id": row["key"][5:], "state": row["value"]["state"]}
                for row in rows[:2048]
                if isinstance(row, dict)
                and isinstance(row.get("key"), str)
                and row["key"].startswith("tool.mcp-leam.")
                and isinstance(row.get("value"), dict)
                and row["value"].get("state")
                in {"always_allow", "ask_each_time", "disabled"}
            ]
        except (RuntimeError, TimeoutError, AttributeError, TypeError, KeyError):
            pass
        return observation(
            "Current worker objects, Leam database counts and IronClaw tool settings",
            data,
            scope="Worker freshness uses lastCheck, not this probe time. Account/device counts are not live provider or notification delivery tests. A closed lazy Codex connection need not be a failure.",
        )

    async def inspect(self, section="summary"):
        names = (
            ["model", "release", "modules", "operations", "tools"]
            if section == "summary"
            else [section]
        )

        async def one(name):
            if name == "operations":
                return observation(
                    "Declared Leam product operation catalog",
                    OPERATIONS,
                    scope="Capabilities and required authority, not evidence that an operation was executed",
                )
            try:
                return await getattr(self, name)()
            except (
                OSError,
                ValueError,
                TypeError,
                AttributeError,
                KeyError,
                sqlite3.Error,
                RuntimeError,
                TimeoutError,
                subprocess.SubprocessError,
            ):
                return observation(
                    "Leam inspection: " + name,
                    None,
                    status="unavailable",
                    scope="This section could not be inspected; other sections remain independent.",
                )

        results = await asyncio.gather(*(one(name) for name in names))
        sections = dict(zip(names, results))
        return {"schemaVersion": 1, "sections": sections, "referenceData": True}

    async def identity(self):
        model = await self.model()
        return {
            "source": model["source"],
            "observedAt": model["observedAt"],
            "available": model["status"] == "available",
            "activeModel": model["data"]
            or {"provider_id": None, "model": None, "reasoning_effort": None},
            "identityScope": model["scope"],
            "internalInspection": "Use leam_system for live model, release, module status and operation authority. It is read-only. For coding requests, prepare a coding.handoff proposal now; the user reviews/edits it in More > Approvals, then confirms Send to main for the reviewed Main target (or Start in Coding only when Main is unset). Preparing the prompt is supported; dispatch requires user confirmation and validated agent-protocols. Report dispatch only from an accepted receipt; completion needs a linked result.",
            "architecture": {
                "source": "Declared Leam product contract",
                "companion": "Leam integration and personal context on IronClaw",
                "coding": "Codex with agent-protocols",
                "research": "deferred",
                "decisionBackend": "deferred beyond MVP; none selected",
            },
        }
