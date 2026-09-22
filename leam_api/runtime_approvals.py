"""Response-local controls for existing, caller-scoped IronClaw approval gates.

Never changes tool policy. Runtime is authoritative for current gate/ownership and
atomically rejects stale resolution; diagnostics are display evidence, not authority.
"""

import asyncio
import hashlib
import json
from contextlib import aclosing
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from .ironclaw import RuntimeError


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: UUID
    decision: Literal["approved", "declined"]
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def inspect_gate(runtime, thread, run, approval):
    """Resolve invocation from authenticated runtime projection, never UI input."""
    ref = "gate:approval-" + approval
    gate = None
    try:
        async with (
            asyncio.timeout(8),
            aclosing(
                runtime.events("/threads/" + quote(thread, safe="") + "/events")
            ) as frames,
        ):
            count = 0
            async for raw in frames:
                count += 1
                if count > 256:
                    break
                data = "\n".join(
                    line[5:].lstrip()
                    for line in raw.decode().splitlines()
                    if line.startswith("data:")
                )
                if not data:
                    continue
                frame = json.loads(data)
                state = frame.get("state") or {}
                if state.get("thread_id") != thread:
                    continue
                for item in state.get("items", [])[:512]:
                    value = item.get("gate") or {}
                    if (
                        value.get("run_id") == run
                        and value.get("gate_ref") == ref
                        and value.get("gate_kind") == "approval"
                    ):
                        gate = value
                        break
                if gate:
                    break
    except (TimeoutError, ValueError, RuntimeError):
        raise HTTPException(
            503,
            "Approval inspection unavailable. Refresh and retry; no decision was sent.",
        ) from None
    if not gate:
        raise HTTPException(
            409,
            "This approval is no longer available in the conversation. Refresh its status.",
        )
    result = {
        "threadId": thread,
        "runId": run,
        "approvalId": approval,
        "invocationId": gate.get("invocation_id"),
        "operation": None,
        "arguments": None,
        "argumentsComplete": False,
        "detailsStatus": "unavailable",
    }
    # Only this invocation is returned; unrelated prompts, tool output and model
    # diagnostics never cross the product API or enter persistent receipts.
    try:
        owner = await runtime.request("GET", "/session")
        raw = await runtime.request(
            "GET", f"/operator/inspector/threads/{quote(thread, safe='')}/runs/{run}"
        )
        if raw.get("snapshot") is None:
            result["fingerprint"] = digest(result)
            return result
        snapshot = raw["snapshot"]
        scope = snapshot.get("scope") or {}
        if (
            not owner.get("tenant_id")
            or not owner.get("user_id")
            or scope.get("tenant_id") != owner["tenant_id"]
            or scope.get("user_id") != owner["user_id"]
            or scope.get("thread_id") != thread
            or scope.get("run_id") != run
        ):
            raise HTTPException(
                403, "Approval diagnostic scope did not match this conversation."
            )
        calls = snapshot.get("tool_executions", [])
        if not isinstance(calls, list) or len(calls) > 512:
            raise HTTPException(502, "Approval diagnostic limit exceeded.")
        for call in calls:
            if call.get("activity_id") != gate.get("invocation_id") or not gate.get(
                "invocation_id"
            ):
                continue
            name, args = call.get("capability_name") or {}, call.get("arguments") or {}
            content = args.get("content")
            if isinstance(name.get("content"), str):
                result["operation"] = name["content"][:256]
            if isinstance(content, str) and len(content.encode()) <= 32768:
                result["arguments"] = content
                result["argumentsComplete"] = args.get(
                    "truncated"
                ) is False and args.get("original_bytes") == len(content.encode())
                result["detailsStatus"] = (
                    "complete" if result["argumentsComplete"] else "truncated"
                )
            break
    except RuntimeError:
        pass  # Decline remains available when bounded diagnostics have expired.
    result["fingerprint"] = digest(result)
    return result


def router(store, runtime, locks):
    routes = APIRouter(prefix="/companion/threads")

    @routes.get("/{thread_id}/runs/{run_id}/approvals/{approval_id}")
    async def inspect(
        thread_id: str, run_id: UUID, approval_id: UUID, response: Response
    ):
        response.headers["Cache-Control"] = "no-store"
        return await inspect_gate(runtime, thread_id, str(run_id), str(approval_id))

    @routes.post("/{thread_id}/runs/{run_id}/approvals/{approval_id}")
    async def resolve(thread_id: str, run_id: UUID, approval_id: UUID, body: Decision):
        run, approval, request = str(run_id), str(approval_id), str(body.requestId)
        fingerprint = digest(
            [
                "runtime-approval",
                thread_id,
                run,
                approval,
                body.decision,
                body.fingerprint,
            ]
        )
        path = f"/threads/{quote(thread_id, safe='')}/runs/{run}/gates/{quote('gate:approval-' + approval, safe='')}/resolve"
        payload = {
            "client_action_id": request,
            "thread_id": thread_id,
            "run_id": run,
            "gate_ref": "gate:approval-" + approval,
            "resolution": body.decision,
            "always": False,
        }
        async with locks[thread_id]:
            with store.connect() as db:
                previous = db.execute(
                    "SELECT fingerprint FROM runtime_actions WHERE id=?", (request,)
                ).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise HTTPException(
                        409, "This request ID belongs to a different decision."
                    )
            else:
                view = await inspect_gate(runtime, thread_id, run, approval)
                if view["fingerprint"] != body.fingerprint:
                    raise HTTPException(
                        409, "Approval details changed. Refresh and review them again."
                    )
                if body.decision == "approved" and not view["argumentsComplete"]:
                    raise HTTPException(
                        409,
                        "Complete operation arguments are unavailable. Approval was not sent; you may decline.",
                    )
            try:
                action = store.runtime_action(request, fingerprint, path, payload)
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
            if action["result"] is not None:
                return action["result"]
            try:
                result = await runtime.request("POST", action["path"], action["body"])
            except RuntimeError as error:
                if error.status_code in {400, 403, 404, 409}:
                    raise HTTPException(
                        error.status_code,
                        "This approval could not be resolved. It may already be resolved or no longer belong to the active response. Refresh its status.",
                    ) from None
                raise
            if (
                not isinstance(result, dict)
                or result.get("run_id") != run
                or result.get("outcome") not in {"resumed", "cancelled"}
                or result.get("status")
                not in {
                    "Queued",
                    "Running",
                    "CancelRequested",
                    "Cancelled",
                    "Completed",
                    "Failed",
                    "BlockedApproval",
                    "BlockedAuth",
                    "RecoveryRequired",
                }
                or type(result.get("event_cursor")) is not int
                or result["event_cursor"] < 0
            ):
                raise HTTPException(
                    502,
                    "Approval receipt unavailable. Retry this same decision to check it; do not assume completion.",
                )
            clean = {
                key: result[key]
                for key in ("run_id", "outcome", "status", "event_cursor")
            }
            store.finish_runtime_action(request, clean)
            return clean

    return routes
