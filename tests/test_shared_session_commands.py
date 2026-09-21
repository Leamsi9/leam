import asyncio
import json
import os
import struct
from contextlib import asynccontextmanager

import pytest

from leam_api.coding_policy import CodingPolicyError
from leam_api.shared_session import SessionOwner, SharedSessionError
from leam_api.shared_session_commands import PrivateIdeCommands, SubmissionUncertain

OWNER = SessionOwner("thread-fixture", "owner-fixture", os.getpid(), True)


@asynccontextmanager
async def ipc(tmp_path, monkeypatch, *, changed=False, drop=False):
    monkeypatch.setattr("leam_api.shared_session.validate_installation", lambda _: None)
    seen = []
    closed = asyncio.Event()

    async def serve(reader, writer):
        try:
            while True:
                size = struct.unpack("<I", await reader.readexactly(4))[0]
                message = json.loads(await reader.readexactly(size))
                seen.append(message)
                method = message["method"]
                if method == "initialize":
                    result = {"clientId": "reader-fixture"}
                elif method == "thread-owner-discovery":
                    result = {"supportsUntrustedAppInput": True}
                else:
                    if drop:
                        break
                    result = {"result": {"turnId": "turn-fixture"}}
                response = {
                    "type": "response",
                    "requestId": message["requestId"],
                    "resultType": "success",
                    "method": method,
                    "handledByClientId": "changed" if changed else OWNER.client_id,
                    "result": result,
                }
                data = json.dumps(response).encode()
                writer.write(struct.pack("<I", len(data)) + data)
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            closed.set()

    path = tmp_path / "ipc.sock"
    server = await asyncio.start_unix_server(serve, path)
    path.chmod(0o600)
    async with server:
        yield PrivateIdeCommands(path, tmp_path), seen
        await asyncio.wait_for(closed.wait(), 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["start", "steer"])
async def test_actual_caller_preserves_user_text_policy_and_owner_settings(
    tmp_path, monkeypatch, operation
):
    raw = "  literal user input\nincluding `code`  "
    async with ipc(tmp_path, monkeypatch) as (adapter, seen):
        result = await (
            adapter.start(OWNER, raw, "stable-id")
            if operation == "start"
            else adapter.steer(OWNER, raw, "stable-id", "/fixture/repo")
        )
    assert result == {"turnId": "turn-fixture"}
    assert len(seen) == 3
    request = seen[-1]
    assert request["targetClientId"] == OWNER.client_id
    assert request["version"] == (2 if operation == "start" else 1)
    params = request["params"]
    payload = params["turnStart"]["request"] if operation == "start" else params
    assert payload["input"] == [{"type": "text", "text": raw, "text_elements": []}]
    assert payload["clientUserMessageId"] == "stable-id"
    policy = payload["additionalContext"]["leam.agent-protocols"]
    assert (
        policy["kind"] == "application"
        and "substantive-work-protocol.md" in policy["value"]
    )
    assert all(
        key not in payload
        for key in (
            "model",
            "effort",
            "reasoningEffort",
            "approvalPolicy",
            "sandboxPolicy",
        )
    )
    if operation == "start":
        assert params["turnStart"]["context"] == {"inheritThreadSettings": True}
    else:
        assert params["restoreMessage"]["context"]["prompt"] == raw
        assert params["restoreMessage"]["cwd"] == "/fixture/repo"
        assert "toolOutput" not in params


@pytest.mark.asyncio
async def test_changed_owner_fails_before_dispatch(tmp_path, monkeypatch):
    async with ipc(tmp_path, monkeypatch, changed=True) as (adapter, seen):
        with pytest.raises(SharedSessionError, match="owner changed"):
            await adapter.start(OWNER, "message", "stable-id")
    assert [m["method"] for m in seen] == ["initialize", "thread-owner-discovery"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["start", "steer"])
async def test_dropped_response_is_uncertain_and_never_replayed(
    tmp_path, monkeypatch, operation
):
    async with ipc(tmp_path, monkeypatch, drop=True) as (adapter, seen):
        with pytest.raises(SubmissionUncertain):
            await (
                adapter.start(OWNER, "message", "stable-id")
                if operation == "start"
                else adapter.steer(OWNER, "message", "stable-id", "/fixture")
            )
    assert len(seen) == 3
    assert seen[-1]["method"] == f"thread-follower-{operation}-turn"


@pytest.mark.asyncio
async def test_missing_policy_prevents_socket_connection(tmp_path, monkeypatch):
    def missing():
        raise CodingPolicyError("missing")

    monkeypatch.setattr("leam_api.shared_session_commands.coding_context", missing)
    adapter = PrivateIdeCommands(tmp_path / "missing.sock", tmp_path)
    with pytest.raises(CodingPolicyError):
        await adapter.start(OWNER, "message", "stable-id")
