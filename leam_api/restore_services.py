"""Fixed candidate service adapter. No request supplies a unit, process or URL."""

import asyncio
import hashlib
import json
import os
import ssl
import time
from pathlib import Path

import httpx

from .candidate_deployment import digest
from .recovery import SERVICES, ServiceControl

CEILING_VARIABLE = b"IRONCLAW_REBORN_ALLOWED_CAPABILITY_IDS="


class FixedRestoreServices:
    def __init__(self, deployment, control=None):
        self.deployment = deployment
        self.control = control or ServiceControl()

    async def identity(self):
        unit = SERVICES["runtime"][1]

        def parse(raw):
            return dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)

        initial = parse(
            await self.control._systemctl(
                "show",
                unit,
                "--property=MainPID,ExecMainStartTimestampMonotonic,ActiveState",
            )
        )
        pid = int(initial.get("MainPID", "0"))
        if pid <= 0 or initial.get("ActiveState") != "active":
            raise ValueError("Runtime identity is unavailable")

        def read():
            with open(f"/proc/{pid}/exe", "rb") as source:
                binary = hashlib.file_digest(source, "sha256").hexdigest()
            with open(f"/proc/{pid}/environ", "rb") as source:
                environment = source.read(2 * 1024 * 1024 + 1)
            if len(environment) > 2 * 1024 * 1024:
                raise ValueError("Runtime environment exceeds its bound")
            values = [
                part[len(CEILING_VARIABLE) :]
                for part in environment.split(b"\0")
                if part.startswith(CEILING_VARIABLE)
            ]
            if len(values) > 1 or (values and len(values[0]) > 65536):
                raise ValueError("Runtime ceiling configuration is invalid")
            ids = json.loads(values[0]) if values else None
            if ids is not None and (
                not isinstance(ids, list)
                or len(ids) > 1024
                or not all(isinstance(i, str) and len(i) <= 256 for i in ids)
                or len(ids) != len(set(ids))
            ):
                raise ValueError("Runtime ceiling configuration is invalid")
            return binary, digest(
                {"allowedCapabilityIds": sorted(ids) if ids is not None else None}
            )

        binary, ceiling = await asyncio.to_thread(read)
        current = parse(
            await self.control._systemctl(
                "show",
                unit,
                "--property=MainPID,ExecMainStartTimestampMonotonic,ActiveState",
            )
        )
        if initial != current:
            raise ValueError("Runtime changed during observation")
        expected = self.deployment.read()["runtime"]
        return {
            "sourceCommit": expected["sourceCommit"],
            "binarySha256": binary,
            "ceilingSha256": ceiling,
        }

    async def stop(self):
        await self.control._systemctl(
            "stop", *(SERVICES[name][1] for name in ("app", "mcp", "runtime"))
        )
        for name in ("app", "mcp", "runtime"):
            raw = await self.control._systemctl(
                "show", SERVICES[name][1], "--property=MainPID,ActiveState"
            )
            values = dict(
                line.split("=", 1) for line in raw.splitlines() if "=" in line
            )
            if values.get("MainPID") != "0" or values.get("ActiveState") not in {
                "inactive",
                "failed",
            }:
                raise ValueError("Candidate writer has not stopped")
        states = await self.control.status()
        if any(row.get("reachable") for row in states):
            raise ValueError("A candidate listener remains active")

    async def start(self):
        for name in ("app", "mcp", "runtime"):
            await self.control._systemctl("start", SERVICES[name][1])

    async def bindings_match(self):
        value = self.deployment.read()
        async with httpx.AsyncClient(
            trust_env=False, timeout=5, follow_redirects=False
        ) as client:
            response = await client.get("http://127.0.0.1:46400/")
            if (
                response.status_code != 200
                or hashlib.sha256(response.content).hexdigest()
                != value["clientIndexSha256"]
            ):
                return False
        for name in ("app", "mcp"):
            raw = await self.control._systemctl(
                "show", SERVICES[name][1], "--property=MainPID", "--value"
            )
            pid = int(raw.strip())
            if pid <= 0 or Path(f"/proc/{pid}/cwd").resolve() != Path(
                value["releaseDirectory"]
            ):
                return False
            # Read only the public data-directory binding; never return process env.
            raw_env = Path(f"/proc/{pid}/environ").read_bytes()
            bound = [
                part
                for part in raw_env.split(b"\0")
                if part.startswith(b"LEAM_DATA_DIR=")
            ]
            if bound != [b"LEAM_DATA_DIR=" + os.fsencode(value["dataDirectory"])]:
                return False
        return True

    async def healthy(self):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                states = await self.control.status()
                if len(states) != 3 or not all(
                    row.get("active") == "active" and row.get("reachable")
                    for row in states
                ):
                    raise ValueError("Candidate services are not all ready")
                value = self.deployment.read()
                if await self.identity() != value["runtime"]:
                    raise ValueError("Runtime identity changed")
                async with httpx.AsyncClient(
                    trust_env=False, timeout=3, follow_redirects=False
                ) as client:
                    response = await client.get("http://127.0.0.1:46400/api/health")
                    if response.status_code != 200 or not response.json().get(
                        "operational"
                    ):
                        raise ValueError("Candidate API is not operational")
                data = Path(value["dataDirectory"])
                context = ssl.create_default_context(
                    cafile=str(data / "mcp-tls/certificate.pem")
                )
                async with httpx.AsyncClient(
                    verify=context, trust_env=False, timeout=3, follow_redirects=False
                ) as client:
                    token = (data / "tools-token").read_text().strip()
                    response = await client.post(
                        "https://127.0.0.1:46420/mcp",
                        headers={
                            "Authorization": "Bearer " + token,
                            "Accept": "application/json, text/event-stream",
                        },
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "tools/list",
                            "params": {},
                        },
                    )
                    if response.status_code != 200 or not response.json().get(
                        "result", {}
                    ).get("tools"):
                        raise ValueError("Candidate MCP discovery is unavailable")
                if not await self.bindings_match():
                    raise ValueError(
                        "Candidate launch bindings or client identity changed"
                    )
                return True
            except (OSError, ValueError, httpx.HTTPError):
                await asyncio.sleep(0.5)
        return False
