"""Explicit controls for the pinned IDE owner, never for a replacement writer."""

import hashlib
import json

from fastapi import APIRouter, HTTPException
from pydantic import Field

from .commitments import Input
from .shared_session import SharedSessionError
from .shared_session_commands import SubmissionUncertain


class Stop(Input):
    generation: str = Field(min_length=1, max_length=128)
    turnId: str = Field(min_length=1, max_length=128)


class SharedControls:
    def __init__(self, shared):
        self.shared = shared

    async def interrupt(self, body):
        service = self.shared
        # One durable intent per displayed binding and exact turn. Different
        # browsers cannot bypass this reservation by supplying a fresh UUID.
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    "ide-exact-stop-v4",
                    body.generation,
                    body.turnId,
                ]
            ).encode()
        ).hexdigest()
        key = "shared-stop:" + fingerprint
        async with service.lock:
            with service.store.connect() as db:
                row = db.execute(
                    "SELECT state,result,fingerprint FROM requests WHERE id=?", (key,)
                ).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    raise HTTPException(
                        409, "Stop receipt identity conflicts with another action"
                    )
                return (
                    json.loads(row["result"])
                    if row["result"]
                    else {
                        "state": "uncertain",
                        "turnId": body.turnId,
                        "detail": "Stop delivery is uncertain. Inspect the current turn; it will not be resent.",
                    }
                )
            await service.ensure()
            if not service.connected or body.generation != service.generation:
                raise HTTPException(
                    409, "Shared session changed. Refresh before stopping."
                )
            if service.view()["activeTurnId"] != body.turnId:
                raise HTTPException(
                    409, "That turn is no longer active. No other turn was stopped."
                )
            owner = service.snapshot.owner
            try:
                existing = service.store.reserve(key, fingerprint)
            except ValueError as error:
                raise HTTPException(
                    409,
                    "A stop for this exact turn is already pending; inspect its state",
                ) from error
            if existing is not None:
                return existing
            try:
                result = await service.commands.interrupt(owner, body.turnId)
                receipt = {
                    "state": "requested"
                    if result["interruptedTurnId"] == body.turnId
                    else "stale",
                    "turnId": body.turnId,
                    "detail": (
                        "Stop requested for this turn and its child agents. The goal remains active."
                        if result["interruptedTurnId"] == body.turnId
                        else "That turn is no longer active. No newer turn was stopped."
                    ),
                }
            except SubmissionUncertain:
                receipt = {
                    "state": "uncertain",
                    "turnId": body.turnId,
                    "detail": "Stop delivery is uncertain. Inspect the current turn; it will not be resent.",
                }
            except SharedSessionError as error:
                # Adapter guarantees this exception is raised before dispatch.
                service.store.release_unsent(key, fingerprint)
                raise HTTPException(503, str(error)) from error
            service.store.finish(key, receipt)
            return receipt


def router(shared):
    routes = APIRouter(prefix="/api/codex/shared")
    controls = SharedControls(shared)

    @routes.post("/interrupt")
    async def interrupt(body: Stop):
        return await controls.interrupt(body)

    return routes
