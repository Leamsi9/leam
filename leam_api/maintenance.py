"""Candidate-only admission barrier, independent of restored product generations."""

import asyncio
import fcntl
import hmac
import json
import math
import os
import secrets
import sqlite3
import stat
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path

from .backups import fsync_directory, private_read
from .candidate_deployment import Roots, atomic_json, private_directory, uuid_value

MESSAGE = "Leam is in maintenance. No new work was accepted; retry explicitly after maintenance ends."


class MaintenanceHeld(ValueError):
    pass


class Maintenance:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        private_directory(self.directory)
        self.path = self.directory / "maintenance.json"
        self.lock_path = self.directory / "candidate-work.lock"
        self.token_path = self.directory / "maintenance-token"
        try:
            fd = os.open(
                self.token_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w") as stream:
                stream.write(secrets.token_urlsafe(48))
                stream.flush()
                os.fsync(stream.fileno())
            fsync_directory(self.directory)
        token = private_read(self.token_path, 256).decode().strip()
        if len(token) != 64 or not all(c.isalnum() or c in "-_" for c in token):
            raise MaintenanceHeld("Maintenance credential is invalid")

    def authorized(self, value):
        expected = "Bearer " + private_read(self.token_path, 256).decode().strip()
        return hmac.compare_digest(value, expected)

    def status(self):
        if not os.path.lexists(self.path):
            return {"held": False}
        try:
            data = json.loads(private_read(self.path, 4096))
        except (ValueError, OSError) as error:
            raise MaintenanceHeld("Maintenance state needs operator review") from error
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "requestId", "phase", "createdAt"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or not isinstance(data["phase"], str)
            or data["phase"] not in {"draining", "held"}
            or type(data["createdAt"]) not in (int, float)
            or not math.isfinite(data["createdAt"])
        ):
            raise MaintenanceHeld("Maintenance state needs operator review")
        try:
            uuid_value(data["requestId"])
        except (ValueError, TypeError, AttributeError) as error:
            raise MaintenanceHeld("Maintenance state needs operator review") from error
        return {**data, "held": True}

    def hold(self, request_id):
        request_id = uuid_value(request_id)
        current = self.status()
        if current["held"]:
            if current["requestId"] != request_id:
                raise MaintenanceHeld("Another maintenance operation is active")
            return current
        atomic_json(
            self.path,
            {
                "version": 1,
                "requestId": request_id,
                "phase": "draining",
                "createdAt": time.time(),
            },
        )
        return self.status()

    def seal(self, request_id):
        fd = self._fd()
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise MaintenanceHeld("Accepted work is still draining") from error
            value = self.status()
            if not value["held"] or value["requestId"] != request_id:
                raise MaintenanceHeld("Maintenance identity changed")
            value.pop("held")
            value["phase"] = "held"
            atomic_json(self.path, value)
        finally:
            os.close(fd)

    def release(self, request_id):
        value = self.status()
        if not value["held"] or value["requestId"] != request_id:
            raise MaintenanceHeld("Maintenance identity changed")
        self.path.unlink()
        fsync_directory(self.directory)
        return {"held": False}

    def _fd(self):
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            os.close(fd)
            raise MaintenanceHeld("Invalid work admission lock")
        return fd

    @contextmanager
    def admit(self, *, existing_companion=False, read_only=False):
        fd = self._fd()
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise MaintenanceHeld(MESSAGE) from error
            state = self.status()
            if (
                state["held"]
                and not read_only
                and not (existing_companion and state["phase"] == "draining")
            ):
                raise MaintenanceHeld(MESSAGE)
            yield
        finally:
            os.close(fd)

    def drained(self):
        fd = self._fd()
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            return True
        finally:
            os.close(fd)


def installed_maintenance(data_directory):
    roots = Roots.installed()
    # Isolated fixtures and other installations must never consult live state.
    if Path(data_directory).resolve().is_relative_to(roots.data):
        return Maintenance(roots.recovery)
    return None


def work_admission(store):
    gate = getattr(store, "maintenance", None)
    return gate.admit() if gate is not None else nullcontext()


def runtime_idle(base):
    """Read-only adapter to the pinned runtime's materialized process contract."""
    path = Path(base) / "ironclaw/local-dev/reborn-local-dev.db"
    if not path.is_file() or path.resolve() != path:
        raise MaintenanceHeld("Runtime idle state is unavailable")
    count, size = 0, 0
    deadline = time.monotonic() + 5
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1) as db:
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
            for index, (raw,) in enumerate(
                db.execute(
                    "SELECT contents FROM root_filesystem_entries WHERE kind='process_materialized' AND path LIKE '%/process/%'"
                )
            ):
                size += len(raw)
                if (
                    index >= 100000
                    or len(raw) > 1048576
                    or size > 64 * 1024 * 1024
                    or time.monotonic() > deadline
                ):
                    raise MaintenanceHeld("Runtime idle state exceeds its bound")
                row = json.loads(raw)
                if row["process_kind"] == "agent_turn":
                    count += 1
                    if row["status"] not in {"completed", "failed", "cancelled"}:
                        return {"idle": False, "reason": "A Companion turn is active"}
    except (sqlite3.Error, ValueError, KeyError, TypeError) as error:
        raise MaintenanceHeld("Runtime idle state could not be verified") from error
    return {"idle": True, "observedTurns": count}


class DrainControl:
    """Fixed loopback inspection; caller holds candidate-operation.lock for changes."""

    def __init__(self, deployment, control, *, transport=None):
        self.deployment, self.control = deployment, control
        self.gate = Maintenance(deployment.roots.recovery)
        self.transport = transport

    async def api_absent(self):
        from .recovery import SERVICES

        raw = await self.control._systemctl(
            "show", SERVICES["app"][1], "--property=MainPID,ActiveState,ControlGroup"
        )
        values = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
        if values.get("MainPID") != "0" or values.get("ActiveState") not in {
            "inactive",
            "failed",
        }:
            return False
        if "ControlGroup" not in values:
            raise MaintenanceHeld("API process group could not be verified")
        group = values["ControlGroup"]
        if group:
            if not group.startswith("/") or ".." in group.split("/"):
                raise MaintenanceHeld("API process group could not be verified")
            directory = Path("/sys/fs/cgroup") / group.lstrip("/")
            if not directory.resolve().is_relative_to(Path("/sys/fs/cgroup")):
                raise MaintenanceHeld("API process group could not be verified")
            if directory.exists():
                for index, path in enumerate(directory.rglob("cgroup.procs")):
                    if index >= 1024 or path.read_text().strip():
                        return False
        states = await self.control.status()
        rows = [row for row in states if row.get("id") == "app"]
        return len(rows) == 1 and rows[0].get("reachable") is False

    async def probe(self):
        import httpx

        if await self.api_absent():
            native = {"idle": True, "apiAbsent": True}
        else:
            try:
                async with httpx.AsyncClient(
                    transport=self.transport,
                    trust_env=False,
                    timeout=20,
                    follow_redirects=False,
                ) as client:
                    response = await client.get(
                        "http://127.0.0.1:46400/api/internal/maintenance",
                        headers={
                            "Authorization": "Bearer "
                            + private_read(self.gate.token_path, 256).decode().strip()
                        },
                    )
                    if response.status_code != 200:
                        raise MaintenanceHeld("API drain proof is unavailable")
                    native = response.json()
                    state = native.get("maintenance", {})
                    current = self.gate.status()
                    if state.get("requestId") != current.get("requestId") or state.get(
                        "phase"
                    ) != current.get("phase"):
                        raise MaintenanceHeld("API maintenance identity changed")
            except (httpx.HTTPError, ValueError) as error:
                raise MaintenanceHeld(
                    "API drain proof is unavailable; no service was stopped"
                ) from error
        runtime = await asyncio.to_thread(runtime_idle, self.deployment.roots.base)
        return {
            "idle": native.get("idle") is True
            and runtime["idle"]
            and self.gate.drained(),
            "native": native,
            "companion": runtime,
        }

    async def prepare(self, request_id):
        self.gate.hold(request_id)
        proof = await self.probe()
        if proof["idle"]:
            self.gate.seal(request_id)
        return {"maintenance": self.gate.status(), **proof}

    async def require(self):
        state = self.gate.status()
        if not state["held"] or state["phase"] != "held":
            raise MaintenanceHeld(
                "Prepare maintenance and drain accepted work before stopping services"
            )
        if not (await self.probe())["idle"]:
            raise MaintenanceHeld(
                "Accepted work is still active; no service was stopped"
            )

    async def guard_restart(self, service_id):
        if service_id == "app" and await self.api_absent():
            # There is no owned API process/child to interrupt. Recovery stays usable.
            return
        await self.require()
