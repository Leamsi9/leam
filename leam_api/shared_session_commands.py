"""Explicit, non-replaying commands to an existing version-pinned IDE owner.

Callers own durable submission receipts and user authorization. A transport error
after dispatch is not proof of failure and must never trigger automatic resending.
"""

import asyncio
import time
from typing import Protocol

from leam_api.coding_policy import coding_context
from leam_api.shared_session import PrivateIdeReader, SessionOwner, SharedSessionError


class SubmissionUncertain(SharedSessionError):
    """The owner may have accepted the command; reconcile before another send."""


class SharedSessionCommands(Protocol):
    async def start(
        self,
        owner: SessionOwner,
        text: str,
        message_id: str,
        *,
        extra_input=None,
        extra_context=None,
    ) -> dict: ...

    async def steer(
        self,
        owner: SessionOwner,
        text: str,
        message_id: str,
        cwd: str | None,
        *,
        extra_input=None,
        extra_context=None,
    ) -> dict: ...


def _input(
    text: str, message_id: str, extra_input=None, allow_empty=False
) -> list[dict]:
    if (
        not isinstance(text, str)
        or (not text.strip() and not extra_input and not allow_empty)
        or len(text.encode()) > 16384
    ):
        raise SharedSessionError("A message must contain 1–16384 UTF-8 bytes")
    if not isinstance(message_id, str) or not 1 <= len(message_id) <= 128:
        raise SharedSessionError("A stable client message identity is required")
    return [{"type": "text", "text": text, "text_elements": []}, *(extra_input or [])]


class PrivateIdeCommands(PrivateIdeReader):
    async def _dispatch(self, owner, method, version, params):
        attempted = False
        try:
            async with self._connection(owner.thread_id) as connection:
                current = await connection.owner(owner.thread_id)
                if current != owner:
                    raise SharedSessionError(
                        "IDE owner changed; refresh before sending"
                    )
                # Mark before writing: a disconnect during drain can follow a
                # successful owner execution. Never infer non-delivery from it.
                attempted = True
                response = await connection.request(
                    method, params, version, target_client_id=owner.client_id
                )
                result = response.get("result")
                if (
                    response.get("handledByClientId") != owner.client_id
                    or not isinstance(result, dict)
                    or not isinstance(result.get("result"), dict)
                ):
                    raise SharedSessionError("IDE command acknowledgment is invalid")
                return result["result"]
        except (SharedSessionError, asyncio.CancelledError) as error:
            if attempted:
                raise SubmissionUncertain(
                    "Delivery is uncertain; inspect the shared session before resending"
                ) from error
            raise

    async def update_settings(
        self, owner, model, effort, expected_model, expected_effort, revalidate
    ):
        attempted = False
        try:
            async with self._connection(owner.thread_id) as connection:
                if await connection.owner(owner.thread_id) != owner:
                    raise SharedSessionError(
                        "IDE owner changed; refresh model settings"
                    )
                revalidate()
                attempted = True
                response = await connection.request(
                    "thread-follower-update-thread-settings",
                    {
                        "conversationId": owner.thread_id,
                        "threadSettings": {"model": model, "effort": effort},
                        "condition": {
                            "ifModelEquals": expected_model,
                            "ifEffortEquals": expected_effort,
                        },
                    },
                    2,
                    target_client_id=owner.client_id,
                )
                result = response.get("result")
                if (
                    response.get("handledByClientId") != owner.client_id
                    or not isinstance(result, dict)
                    or not isinstance(result.get("applied"), bool)
                ):
                    raise SharedSessionError("IDE settings acknowledgment is invalid")
                return result
        except (SharedSessionError, asyncio.CancelledError) as error:
            if attempted:
                raise SubmissionUncertain(
                    "Model change delivery is uncertain; refresh settings"
                ) from error
            raise

    async def interrupt(self, owner: SessionOwner, turn_id: str) -> dict:
        if not isinstance(turn_id, str) or not 1 <= len(turn_id) <= 128:
            raise SharedSessionError("An exact active turn identity is required")
        attempted = False
        try:
            async with self._connection(owner.thread_id) as connection:
                if await connection.owner(owner.thread_id) != owner:
                    raise SharedSessionError(
                        "IDE owner changed; refresh before stopping"
                    )
                attempted = True
                response = await connection.request(
                    "thread-follower-interrupt-turn",
                    {
                        "conversationId": owner.thread_id,
                        "mode": "user-stop",
                        "expectedTurnId": turn_id,
                    },
                    4,
                    target_client_id=owner.client_id,
                )
                result = response.get("result")
                if (
                    response.get("handledByClientId") != owner.client_id
                    or not isinstance(result, dict)
                    or result.get("ok") is not True
                    or "interruptedTurnId" not in result
                    or result["interruptedTurnId"] not in (None, turn_id)
                ):
                    raise SharedSessionError("IDE stop acknowledgment is invalid")
                return result
        except (SharedSessionError, asyncio.CancelledError) as error:
            if attempted:
                raise SubmissionUncertain(
                    "Stop delivery is uncertain; do not resend"
                ) from error
            raise

    async def respond_request(self, owner, packet, response, revalidate):
        methods = {
            "item/commandExecution/requestApproval": "command-approval-decision",
            "item/fileChange/requestApproval": "file-approval-decision",
            "item/tool/requestUserInput": "submit-user-input",
        }
        method = methods.get(packet.get("method"))
        if method is None:
            raise SharedSessionError("Unsupported owner request")
        attempted = False
        try:
            async with self._connection(owner.thread_id) as connection:
                if await connection.owner(owner.thread_id) != owner:
                    raise SharedSessionError(
                        "IDE owner changed; refresh before answering"
                    )
                revalidate()  # Pins/owner discovery awaited; recheck latest snapshot now.
                params = {"conversationId": owner.thread_id, "requestId": packet["id"]}
                params.update(
                    {"response": response}
                    if method == "submit-user-input"
                    else {"decision": response["decision"]}
                )
                attempted = True
                reply = await connection.request(
                    "thread-follower-" + method,
                    params,
                    1,
                    target_client_id=owner.client_id,
                )
                result = reply.get("result")
                if (
                    reply.get("handledByClientId") != owner.client_id
                    or not isinstance(result, dict)
                    or result.get("ok") is not True
                ):
                    raise SharedSessionError("IDE response acknowledgment is invalid")
                return result
        except (SharedSessionError, asyncio.CancelledError) as error:
            if attempted:
                raise SubmissionUncertain(
                    "Response delivery is uncertain; do not resend"
                ) from error
            raise

    async def start(
        self,
        owner: SessionOwner,
        text: str,
        message_id: str,
        *,
        extra_input=None,
        extra_context=None,
    ) -> dict:
        user_input = _input(text, message_id, extra_input, bool(extra_context))
        context = coding_context()
        context.update(extra_context or {})
        return await self._dispatch(
            owner,
            "thread-follower-start-turn",
            2,
            {
                "conversationId": owner.thread_id,
                "turnStart": {
                    "request": {
                        "threadId": owner.thread_id,
                        "input": user_input,
                        "clientUserMessageId": message_id,
                        "additionalContext": context,
                    },
                    "context": {"inheritThreadSettings": True},
                },
            },
        )

    async def steer(
        self,
        owner: SessionOwner,
        text: str,
        message_id: str,
        cwd: str | None,
        *,
        extra_input=None,
        extra_context=None,
    ) -> dict:
        user_input = _input(text, message_id, extra_input, bool(extra_context))
        if cwd is not None and (
            not isinstance(cwd, str) or not cwd.startswith("/") or len(cwd) > 4096
        ):
            raise SharedSessionError("Shared session working directory is invalid")
        context = coding_context()
        context.update(extra_context or {})
        return await self._dispatch(
            owner,
            "thread-follower-steer-turn",
            1,
            {
                "conversationId": owner.thread_id,
                "input": user_input,
                "clientUserMessageId": message_id,
                "additionalContext": context,
                "attachments": [],
                "restoreMessage": {
                    "id": message_id,
                    "text": text,
                    "createdAt": int(time.time() * 1000),
                    "cwd": cwd,
                    "context": {
                        "prompt": text,
                        "addedFiles": [],
                        "fileAttachments": [],
                        "imageAttachments": [],
                        "commentAttachments": [],
                        "ideContext": None,
                        "workspaceRoots": [cwd] if cwd else [],
                    },
                },
            },
        )
