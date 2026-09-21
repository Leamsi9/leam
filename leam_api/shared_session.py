"""Replaceable, version-pinned reader for an already-owned local IDE session.

This private compatibility protocol is not the public App Server API. Nothing in
this reader resumes a thread, changes ownership, starts a turn or resolves input.
"""

import asyncio
import hashlib
import json
import os
import socket
import stat
import struct
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

EXTENSION_VERSION = "26.908.40401"
CLI_VERSION = "0.154.0-alpha.6.2"
INSTALLATION_HASHES = {
    "out/extension.js": "820691c93be40e73f0929b633cddc694b41775050cd72283faba283e53941f4f",
    "webview/assets/app-initial-bca4f920746a.js": "9c43854dc714687bbbdab5895bc084a09681b97c998b3f612e30a7aeac97bfec",
    "webview/assets/app-initial-1e5ee25fb4ec.js": "0d3e38dbac570fefa5a0b0ecec3522308df74aa7b1fe538e1ea2490489347dd9",
    "webview/assets/app-initial-a190b16fc630.js": "50b1a443400ba2f7ac0be53c56536a3e145bccfff0e2f456f100850133a01683",
    "bin/linux-x86_64/codex": "6970ad6a5b7615d2f5838879e19c1369e5527cb1544f1515f76900267740a403",
}
MAX_FRAME = 32 * 1024 * 1024
MAX_TOTAL = 48 * 1024 * 1024
TIMEOUT = 12


class SharedSessionError(RuntimeError):
    pass


class SharedSessionRenewal(SharedSessionError):
    """A bounded read-only subscription reached its intentional lease deadline."""


@dataclass(frozen=True)
class SessionOwner:
    thread_id: str
    client_id: str
    peer_pid: int
    supports_untrusted_app_input: bool


@dataclass(frozen=True)
class SessionSnapshot:
    owner: SessionOwner
    revision: int
    state: dict


class SharedSessionReader(Protocol):
    async def discover_owner(self, thread_id: str) -> SessionOwner: ...
    async def read_snapshot(self, thread_id: str) -> SessionSnapshot: ...


def validate_installation(root: Path):
    try:
        if (
            json.loads((root / "package.json").read_text())["version"]
            != EXTENSION_VERSION
        ):
            raise SharedSessionError(
                "IDE installation version changed; compatibility review required"
            )
        for relative, expected in INSTALLATION_HASHES.items():
            path = root / relative
            if path.stat().st_size > 512 * 1024 * 1024:
                raise SharedSessionError("IDE installation artifact exceeds its bound")
            with path.open("rb") as file:
                if hashlib.file_digest(file, "sha256").hexdigest() != expected:
                    raise SharedSessionError(
                        "IDE installation changed; compatibility review required"
                    )
    except (OSError, ValueError, KeyError) as error:
        raise SharedSessionError("Pinned IDE installation is unavailable") from error


class _Connection:
    def __init__(self, reader, writer, peer_pid):
        self.reader, self.writer, self.peer_pid = reader, writer, peer_pid
        self.client_id = "initializing-client"
        self.total = 0
        self.messages = 0
        self.max_messages = 128
        self.max_total = MAX_TOTAL

    async def send(self, message):
        payload = json.dumps(message, separators=(",", ":")).encode()
        if len(payload) > 65536:
            raise SharedSessionError("IPC outgoing message exceeds its bound")
        self.writer.write(struct.pack("<I", len(payload)) + payload)
        await self.writer.drain()

    async def receive(self):
        self.messages += 1
        if self.messages > self.max_messages:
            raise SharedSessionError("IPC message count exceeded")
        size = struct.unpack("<I", await self.reader.readexactly(4))[0]
        self.total += size
        if not 0 < size <= MAX_FRAME or self.total > self.max_total:
            raise SharedSessionError("IPC frame size exceeds its bound")
        try:
            message = json.loads(await self.reader.readexactly(size))
        except (ValueError, UnicodeError) as error:
            raise SharedSessionError("IPC frame is not valid JSON") from error
        if not isinstance(message, dict):
            raise SharedSessionError("IPC frame is not an object")
        if message.get("type") == "client-discovery-request":
            # Never advertise any execution/owner capability to the router.
            await self.send(
                {
                    "type": "client-discovery-response",
                    "requestId": message.get("requestId"),
                    "response": {"canHandle": False},
                }
            )
        return message

    async def request(self, method, params, version, *, target_client_id=None):
        request_id = str(uuid.uuid4())
        await self.send(
            {
                "type": "request",
                "requestId": request_id,
                "sourceClientId": self.client_id,
                "version": version,
                "method": method,
                "params": params,
                "timeoutMs": 4000,
                **({"targetClientId": target_client_id} if target_client_id else {}),
            }
        )
        while True:
            message = await self.receive()
            if (
                message.get("type") == "response"
                and message.get("requestId") == request_id
            ):
                if (
                    message.get("resultType") != "success"
                    or message.get("method") != method
                ):
                    raise SharedSessionError(
                        "Existing IDE owner is unavailable or incompatible"
                    )
                return message

    async def owner(self, thread_id):
        response = await self.request(
            "thread-owner-discovery",
            {"hostId": "local", "conversationId": thread_id},
            1,
        )
        client_id = response.get("handledByClientId")
        if not isinstance(client_id, str) or not 1 <= len(client_id) <= 256:
            raise SharedSessionError("IDE owner identity is invalid")
        result = response.get("result")
        if not isinstance(result, dict):
            raise SharedSessionError("IDE owner response is invalid")
        return SessionOwner(
            thread_id,
            client_id,
            self.peer_pid,
            result.get("supportsUntrustedAppInput") is True,
        )

    async def follow(self, owner, following):
        await self.send(
            {
                "type": "broadcast",
                "method": "thread-stream-following-changed",
                "sourceClientId": self.client_id,
                "targetClientIds": [owner.client_id],
                "version": 1,
                "params": {
                    "hostId": "local",
                    "conversationId": owner.thread_id,
                    "following": following,
                },
            }
        )


class PrivateIdeReader:
    def __init__(self, socket_path: Path, installation: Path):
        self.socket_path = socket_path
        self.installation = installation

    @asynccontextmanager
    async def _connection(self, thread_id, *, lifetime=TIMEOUT, renewable=False):
        if not isinstance(thread_id, str) or not 1 <= len(thread_id) <= 200:
            raise SharedSessionError("Invalid thread identity")
        writer = None
        deadline = asyncio.timeout(lifetime)
        try:
            async with deadline:
                await asyncio.wait_for(
                    asyncio.to_thread(validate_installation, self.installation), TIMEOUT
                )
                info = self.socket_path.lstat()
                if (
                    not stat.S_ISSOCK(info.st_mode)
                    or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600
                ):
                    raise SharedSessionError("IDE socket must be private and owned")
                reader, writer = await asyncio.wait_for(
                    asyncio.open_unix_connection(self.socket_path), TIMEOUT
                )
                peer = writer.get_extra_info("socket")
                pid, uid, _gid = struct.unpack(
                    "3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                )
                if uid != os.getuid():
                    raise SharedSessionError(
                        "IDE socket peer does not belong to this user"
                    )
                connection = _Connection(reader, writer, pid)
                initialized = await asyncio.wait_for(
                    connection.request(
                        "initialize", {"clientType": "leam-session-reader"}, 0
                    ),
                    TIMEOUT,
                )
                result = initialized.get("result")
                if not isinstance(result, dict):
                    raise SharedSessionError("IDE initialization response is invalid")
                client_id = result.get("clientId")
                if not isinstance(client_id, str) or not 1 <= len(client_id) <= 256:
                    raise SharedSessionError("IDE reader identity is invalid")
                connection.client_id = client_id
                yield connection
        except TimeoutError as error:
            if renewable and deadline.expired():
                raise SharedSessionRenewal("Read subscription lease expired") from error
            raise SharedSessionError("IDE connection timed out") from error
        except (OSError, asyncio.IncompleteReadError) as error:
            raise SharedSessionError(
                "IDE connection unavailable, closed or timed out"
            ) from error
        finally:
            if writer:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 1)
                except (TimeoutError, OSError):
                    pass

    async def discover_owner(self, thread_id: str) -> SessionOwner:
        async with self._connection(thread_id) as connection:
            return await connection.owner(thread_id)

    async def read_snapshot(self, thread_id: str) -> SessionSnapshot:
        async with self._connection(thread_id) as connection:
            owner = await connection.owner(thread_id)
            await connection.follow(owner, True)
            try:
                while True:
                    message = await connection.receive()
                    params = message.get("params") or {}
                    if not isinstance(params, dict):
                        raise SharedSessionError("IDE broadcast parameters are invalid")
                    if (
                        message.get("method") == "client-status-changed"
                        and params.get("clientId") == owner.client_id
                        and params.get("status") == "disconnected"
                    ):
                        raise SharedSessionError("IDE owner disconnected")
                    if (
                        message.get("type") != "broadcast"
                        or message.get("sourceClientId") != owner.client_id
                        or message.get("method") != "thread-stream-state-changed"
                        or params.get("conversationId") != thread_id
                    ):
                        continue
                    if message.get("version") != 11 or params.get("hostId") != "local":
                        raise SharedSessionError("IDE snapshot protocol changed")
                    change = params.get("change") or {}
                    if not isinstance(change, dict):
                        raise SharedSessionError("IDE snapshot change is invalid")
                    if change.get("type") != "snapshot":
                        continue
                    state = change.get("conversationState")
                    revision = change.get("revision")
                    if (
                        not isinstance(state, dict)
                        or state.get("id") != thread_id
                        or type(revision) is not int
                        or revision < 0
                    ):
                        raise SharedSessionError(
                            "IDE snapshot identity or revision is invalid"
                        )
                    return SessionSnapshot(owner, revision, state)
            finally:
                try:
                    await asyncio.wait_for(connection.follow(owner, False), 1)
                except (TimeoutError, OSError):
                    pass
