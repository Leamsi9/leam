"""Leam integration for one IDE-owned coding thread, without ownership transfer."""

import asyncio
import hashlib
import json
import os
import uuid
from pathlib import Path

from fastapi import HTTPException

from .attachments import AttachmentStore
from .coding_policy import CodingPolicyError, coding_context
from .shared_decisions import SharedDecisions
from .shared_session import SharedSessionError, SharedSessionRenewal
from .shared_session_commands import PrivateIdeCommands, SubmissionUncertain
from .shared_session_stream import PrivateIdeStream, project_snapshot, runtime_blocked

RUNTIME_UNAVAILABLE = (
    "The original Codex window is connected, but this session is not ready. "
    "Reopen this conversation in VS Code; if it still says thread not found, "
    "refresh the VS Code window. Then reconnect Leam. Sending is paused until the session is ready."
)


class SharedCoding:
    def __init__(self, store, stream=None, commands=None):
        configured = os.environ.get("LEAM_SHARED_CODEX_THREAD", "").strip()
        try:
            self.thread_id = str(uuid.UUID(configured)) if configured else None
        except ValueError:
            raise ValueError("LEAM_SHARED_CODEX_THREAD must be a valid UUID") from None
        installation = Path(
            os.environ.get(
                "LEAM_CODEX_IDE_INSTALLATION",
                str(
                    Path.home()
                    / ".vscode/extensions/openai.chatgpt-26.908.40401-linux-x64"
                ),
            )
        )
        socket = Path(
            os.environ.get(
                "LEAM_CODEX_IDE_SOCKET", str(Path.home() / ".codex/ipc/ipc.sock")
            )
        )
        self.store = store
        self.stream = stream or PrivateIdeStream(socket, installation)
        self.commands = commands or PrivateIdeCommands(socket, installation)
        self.snapshot = None
        self.task = None
        self.notification = None
        self.ready = asyncio.Event()
        self.connected = False
        self.generation = str(uuid.uuid4())
        self.error = None
        self.lock = asyncio.Lock()
        self.start_lock = asyncio.Lock()
        self.decisions = SharedDecisions(self)

    @property
    def connected(self):
        return self._transport_connected and not runtime_blocked(self.snapshot)

    @connected.setter
    def connected(self, value):
        self._transport_connected = value

    def require_runtime_ready(self):
        if self._transport_connected and runtime_blocked(self.snapshot):
            raise HTTPException(409, RUNTIME_UNAVAILABLE)

    def owns(self, thread_id):
        return self.thread_id is not None and thread_id == self.thread_id

    def listing(self):
        if self.thread_id is None:
            return None
        result = {
            "id": self.thread_id,
            "name": "Leam build · shared with Codex",
            "transport": "ide-owner",
        }
        if self.snapshot:
            result.update(self.view()["thread"])
            result.pop("turns", None)
        return result

    async def _notify(self):
        await asyncio.sleep(0.25)
        self.store.event(
            "codex.shared",
            {
                "threadId": self.thread_id,
                "connected": self.connected,
                "generation": self.generation,
                "revision": self.snapshot.revision if self.snapshot else None,
            },
        )

    def notify(self):
        if self.thread_id is None:
            return
        if self.notification is None or self.notification.done():
            self.notification = asyncio.create_task(self._notify())

    async def follow(self):
        if self.thread_id is None:
            return
        # Renew only a read subscription. Never resume, take ownership or replay.
        while True:
            generator = self.stream.watch(self.thread_id)
            renewal = False
            try:
                async for snapshot in generator:
                    if self.snapshot and (
                        snapshot.owner != self.snapshot.owner
                        or runtime_blocked(snapshot) != runtime_blocked(self.snapshot)
                    ):
                        self.generation = str(uuid.uuid4())
                    self.snapshot = snapshot
                    self.connected = True
                    self.decisions.sync()
                    self.error = None
                    self.ready.set()
                    self.notify()
            except asyncio.CancelledError:
                raise
            except SharedSessionRenewal:
                renewal = True
                self.error = None
            except (SharedSessionError, OSError, ValueError, TypeError):
                self.error = "Shared Codex connection is unavailable. Keep the original Codex window open and reconnect."
            finally:
                await generator.aclose()
                self.connected = False
                if not renewal:
                    self.generation = str(uuid.uuid4())
                self.ready.set()
                self.notify()
            # Bounded retry interval; all renewals revalidate disk pins/socket/owner.
            if not renewal:
                await asyncio.sleep(5)

    async def ensure(self):
        if self.thread_id is None:
            raise HTTPException(404, "No shared Codex session is configured")
        async with self.start_lock:
            if self.task is None or self.task.done():
                self.ready.clear()
                self.task = asyncio.create_task(self.follow())
        try:
            await asyncio.wait_for(self.ready.wait(), 15)
        except TimeoutError as error:
            raise HTTPException(
                503, "Shared Codex snapshot timed out; retry connection"
            ) from error
        if self.snapshot is None:
            raise HTTPException(
                503, self.error or "Waiting for the existing Codex owner"
            )

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.notification:
            self.notification.cancel()
            await asyncio.gather(self.notification, return_exceptions=True)

    def view(self):
        if self.snapshot is None:
            raise HTTPException(503, "Shared Codex snapshot is not available")
        result = project_snapshot(self.snapshot)
        result.update(
            connected=self.connected,
            transportConnected=self._transport_connected,
            generation=self.generation,
            error=(
                RUNTIME_UNAVAILABLE
                if self._transport_connected and runtime_blocked(self.snapshot)
                else self.error
            ),
        )
        result["thread"]["transport"] = "ide-owner"
        cwd = self.snapshot.state.get("cwd")
        result["thread"]["cwd"] = cwd[:4096] if isinstance(cwd, str) else None
        return result

    async def read(self):
        await self.ensure()
        return self.view()

    async def turns(self):
        view = await self.read()
        return {
            "data": list(reversed(view["thread"]["turns"])),
            "nextCursor": None,
            "truncated": view["truncated"],
            "transport": "ide-owner",
            "activeTurnId": view["activeTurnId"],
            "revision": view["revision"],
        }

    async def goal(self):
        await self.ensure()
        goal = self.snapshot.state.get("threadGoal")
        if not isinstance(goal, dict):
            return {"goal": None}
        return {
            "goal": {
                k: (v[:16384] if isinstance(v, str) else v)
                for k, v in goal.items()
                if k
                in (
                    "objective",
                    "status",
                    "tokenBudget",
                    "tokensUsed",
                    "elapsedSeconds",
                    "timeUsedSeconds",
                )
                and (v is None or isinstance(v, (str, int, float, bool)))
            }
        }

    def pending_requests(self):
        return self.decisions.listing()

    def fingerprint(self, text, attachment_ids=None):
        if self.thread_id is None:
            raise HTTPException(404, "No shared Codex session is configured")
        try:
            refs = AttachmentStore(self.store).resolve(attachment_ids or [])
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return hashlib.sha256(
            json.dumps(
                ["ide-owner", self.thread_id, text] + ([refs] if refs else [])
            ).encode()
        ).hexdigest()

    def _receipt(self, request_id, fingerprint):
        try:
            return self.store.receipt(request_id, fingerprint)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    async def send(
        self,
        text,
        request_id,
        generation,
        attachment_ids=None,
        *,
        coordination_context=None,
    ):
        attachment_ids = attachment_ids or []
        attachments = AttachmentStore(self.store)
        async with self.lock:
            fingerprint = self.fingerprint(text, attachment_ids)
            existing = self._receipt(request_id, fingerprint)
            if existing is not None:
                return existing
            await self.ensure()
            self.require_runtime_ready()
            if not self.connected or generation != self.generation:
                raise HTTPException(
                    409,
                    "Shared Codex binding changed. Refresh the session before sending.",
                )
            if len(text.encode()) > 16384:
                raise HTTPException(
                    422, "Shared Codex messages are limited to 16384 UTF-8 bytes"
                )
            try:
                await asyncio.to_thread(coding_context)
                extra_input, extra_context = await attachments.coding(attachment_ids)
                extra_context.update(coordination_context or {})
            except CodingPolicyError as error:
                raise HTTPException(503, str(error)) from error
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            # Ensure the owner/generation did not change during policy validation.
            self.require_runtime_ready()
            if not self.connected or generation != self.generation:
                raise HTTPException(
                    409, "Shared Codex reconnected. Refresh before sending."
                )
            snapshot = self.snapshot
            active = self.view()["activeTurnId"]
            try:
                attachments.bind(attachment_ids, request_id, thread_id=self.thread_id, surface="coding")
                existing = self.store.reserve(request_id, fingerprint)
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
            if existing is not None:
                return existing
            try:
                extra = (
                    {"extra_input": extra_input, "extra_context": extra_context}
                    if attachment_ids or extra_context
                    else {}
                )
                if active:
                    result = await self.commands.steer(
                        snapshot.owner,
                        text,
                        request_id,
                        snapshot.state.get("cwd"),
                        **extra,
                    )
                else:
                    result = await self.commands.start(
                        snapshot.owner, text, request_id, **extra
                    )
            except SubmissionUncertain as error:
                raise HTTPException(409, str(error)) from error
            except (SharedSessionError, CodingPolicyError) as error:
                # The command adapter guarantees these errors precede dispatch.
                self.store.release_unsent(request_id, fingerprint)
                raise HTTPException(503, str(error)) from error
            turn = result.get("turn") or {
                "id": result.get("turnId"),
                "status": "inProgress",
            }
            receipt = {
                "turn": {
                    "id": turn.get("id"),
                    "status": turn.get("status", "inProgress"),
                },
                "transport": "ide-owner",
                "operation": "steer" if active else "start",
            }
            self.store.finish(request_id, receipt)
            return receipt

    async def reconcile(self, request_id, text, attachment_ids=None):
        fingerprint = self.fingerprint(text, attachment_ids)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM requests WHERE id=?", (request_id,)
            ).fetchone()
        if row is None:
            return {"state": "notSubmitted"}
        if row["fingerprint"] != fingerprint:
            raise HTTPException(
                409, "Submission belongs to another message or transport"
            )
        if row["state"] == "complete":
            return {"state": "complete", "result": json.loads(row["result"])}
        await self.ensure()
        state = self.snapshot.state
        history = state.get("turnHistory") or {}
        if history.get("kind") == "canonical":
            turns = (history.get("history") or {}).get("entitiesByKey", {}).values()
        else:
            turns = state.get("turns") or []
        for turn in turns:
            for item in turn.get("items") or []:
                # A local pending steering draft is not proof of backend acceptance.
                confirmed = (
                    item.get("type") == "userMessage"
                    and item.get("clientId") == request_id
                )
                if confirmed:
                    result = {
                        "turn": {
                            "id": turn.get("turnId"),
                            "status": turn.get("status"),
                        },
                        "transport": "ide-owner",
                    }
                    self.store.finish(request_id, result)
                    return {"state": "complete", "result": result}
        return {
            "state": "pending",
            "detail": "No confirmed matching owner message yet; delivery remains uncertain. Do not resend.",
        }
