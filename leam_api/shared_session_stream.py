"""Bounded private follower stream and compact public transcript projection."""

import asyncio
from collections.abc import AsyncIterator

from leam_api.shared_session import (
    PrivateIdeReader,
    SessionSnapshot,
    SharedSessionError,
)


def apply_patches(state: dict, patches: list) -> dict:
    """Apply observed Immer JSON patches, copying only changed ancestor paths."""
    if not isinstance(patches, list) or len(patches) > 10000:
        raise SharedSessionError("IDE patch count exceeds its bound")

    def apply(node, path, operation, value):
        if not path:
            if operation != "replace":
                raise SharedSessionError("Unsupported root patch")
            return value
        key, *tail = path
        if isinstance(node, dict) and isinstance(key, str):
            result = node.copy()
            if tail:
                if key not in node:
                    raise SharedSessionError("IDE patch path is missing")
                result[key] = apply(node[key], tail, operation, value)
            elif operation == "remove":
                if key not in result:
                    raise SharedSessionError("IDE patch path is missing")
                del result[key]
            else:
                if operation == "replace" and key not in node:
                    raise SharedSessionError("IDE patch path is missing")
                result[key] = value
            return result
        if isinstance(node, list) and (type(key) is int or key == "-"):
            index = len(node) if key == "-" else key
            if (
                index < 0
                or index > len(node)
                or (index == len(node) and (tail or operation != "add"))
            ):
                raise SharedSessionError("IDE patch index is invalid")
            result = node.copy()
            if tail:
                result[index] = apply(node[index], tail, operation, value)
            elif operation == "add":
                result.insert(index, value)
            elif operation == "remove":
                result.pop(index)
            else:
                result[index] = value
            return result
        raise SharedSessionError("IDE patch path type is invalid")

    result = state
    for patch in patches:
        if not isinstance(patch, dict):
            raise SharedSessionError("IDE patch is invalid")
        operation, path = patch.get("op"), patch.get("path")
        if (
            operation not in ("add", "replace", "remove")
            or not isinstance(path, list)
            or len(path) > 64
            or any(
                not (type(key) is int or isinstance(key, str) and len(key) <= 512)
                for key in path
            )
            or operation != "remove"
            and "value" not in patch
        ):
            raise SharedSessionError("IDE patch operation is unsupported")
        result = apply(result, path, operation, patch.get("value"))
    if not isinstance(result, dict):
        raise SharedSessionError("IDE patched state is invalid")
    return result


class PrivateIdeStream(PrivateIdeReader):
    async def watch(self, thread_id: str) -> AsyncIterator[SessionSnapshot]:
        """One 5-minute subscription; caller handles deliberate read-only renewal.

        Frames remain at most 32MiB; lifetime budgets are 10000 frames/256MiB.
        No automatic reconnect, takeover, or command dispatch occurs here.
        """
        async with self._connection(
            thread_id, lifetime=300, renewable=True
        ) as connection:
            connection.max_messages = 10000
            connection.max_total = 256 * 1024 * 1024
            owner = await asyncio.wait_for(connection.owner(thread_id), 8)
            await connection.follow(owner, True)
            snapshot = None
            try:
                while True:
                    message = (
                        await asyncio.wait_for(connection.receive(), 12)
                        if snapshot is None
                        else await connection.receive()
                    )
                    params = message.get("params") or {}
                    if not isinstance(params, dict):
                        raise SharedSessionError("IDE stream parameters are invalid")
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
                        raise SharedSessionError("IDE stream protocol changed")
                    change = params.get("change")
                    if (
                        not isinstance(change, dict)
                        or type(change.get("revision")) is not int
                    ):
                        raise SharedSessionError("IDE stream revision is invalid")
                    revision = change["revision"]
                    if change.get("type") == "snapshot":
                        state = change.get("conversationState")
                        if revision < 0 or snapshot and revision < snapshot.revision:
                            raise SharedSessionError("IDE snapshot revision regressed")
                    elif change.get("type") == "patches":
                        if (
                            snapshot is None
                            or type(change.get("baseRevision")) is not int
                            or change["baseRevision"] != snapshot.revision
                            or revision != snapshot.revision + 1
                        ):
                            raise SharedSessionError(
                                "IDE stream revision gap; refresh required"
                            )
                        state = apply_patches(snapshot.state, change.get("patches"))
                    else:
                        raise SharedSessionError("IDE stream change is unsupported")
                    if not isinstance(state, dict) or state.get("id") != thread_id:
                        raise SharedSessionError("IDE stream thread identity changed")
                    snapshot = SessionSnapshot(owner, revision, state)
                    yield snapshot
            finally:
                try:
                    await asyncio.wait_for(connection.follow(owner, False), 1)
                except (TimeoutError, OSError):
                    pass


def project_snapshot(snapshot: SessionSnapshot) -> dict:
    """Return at most 50 recent conversation messages and 128KiB of message text.

    Drops tool arguments/output, reasoning, restored drafts, local paths and raw
    pending requests. Requests remain available to the trusted server in snapshot.
    Caller may adapt this public-shaped thread directly to its existing renderer.
    """
    state = snapshot.state
    history = state.get("turnHistory") or {}
    if history.get("kind") == "canonical":
        canonical = history.get("history") or {}
        entities = canonical.get("entitiesByKey") or {}
        turns = (
            entities.get(entry.get("value"))
            for island in reversed(canonical.get("islands") or [])
            for entry in reversed(island.get("entries") or [])
        )
    else:
        turns = reversed(state.get("turns") or [])
    remaining = 128 * 1024
    count = 0
    truncated = False
    projected = []
    active_turn = None
    confirmed_ids = set()
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        if active_turn is None and turn.get("status") == "inProgress":
            active_turn = turn.get("turnId")
        items = []
        for item in reversed(turn.get("items") or []):
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind not in ("userMessage", "agentMessage", "steeringUserMessage"):
                continue
            if (
                kind == "steeringUserMessage"
                and item.get("serverUserMessageId") in confirmed_ids
            ):
                continue
            if kind == "userMessage":
                confirmed_ids.add(item.get("id"))
            if count >= 50 or remaining <= 0:
                truncated = True
                break
            if kind == "agentMessage":
                text = item.get("text") or ""
            else:
                content = (
                    item.get("input" if kind == "steeringUserMessage" else "content")
                    or []
                )
                text = "\n".join(
                    x.get("text", "")
                    for x in content
                    if isinstance(x, dict)
                    and x.get("type") == "text"
                    and isinstance(x.get("text"), str)
                )
            if not isinstance(text, str):
                continue
            encoded = text.encode()
            limit = min(remaining, 16384)
            shortened = len(encoded) > limit
            if shortened:
                text = encoded[:limit].decode("utf-8", errors="ignore")
                truncated = True
            remaining -= len(text.encode())
            public = {
                "id": str(item.get("id", ""))[:256],
                "type": "agentMessage" if kind == "agentMessage" else "userMessage",
                "truncated": shortened,
            }
            if kind == "agentMessage":
                public["text"] = text
            else:
                public["content"] = [{"type": "text", "text": text}]
                correlation = (
                    "clientUserMessageId"
                    if kind == "steeringUserMessage"
                    else "clientId"
                )
                value = item.get(correlation)
                if isinstance(value, str) and 0 < len(value) <= 256:
                    public[correlation] = value
                if kind == "steeringUserMessage":
                    public["deliveryStatus"] = item.get("status")
            items.append(public)
            count += 1
        if items:
            projected.append(
                {
                    "id": str(turn.get("turnId", ""))[:256],
                    "status": turn.get("status"),
                    "items": list(reversed(items)),
                }
            )
        if count >= 50 or remaining <= 0:
            truncated = True
            break
    settings = state.get("latestThreadSettings") or {}

    def metadata(primary, fallback):
        value = settings.get(primary) or state.get(fallback)
        return value[:256] if isinstance(value, str) else None

    return {
        "thread": {
            "id": snapshot.owner.thread_id,
            "name": str(state.get("title") or "")[:512],
            "turns": list(reversed(projected)),
            "model": metadata("model", "latestModel"),
            "modelProvider": metadata("modelProvider", "modelProvider"),
            "reasoningEffort": metadata("effort", "latestReasoningEffort"),
        },
        "revision": snapshot.revision,
        "activeTurnId": active_turn,
        "transport": "ide-owner",
        "truncated": truncated,
        "pendingRequestCount": len(state.get("requests") or []),
    }
