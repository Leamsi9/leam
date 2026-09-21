import asyncio
import copy
import json
import struct

import pytest
from test_shared_session import FIXTURE

from leam_api.shared_session import SessionOwner, SessionSnapshot, SharedSessionError
from leam_api.shared_session_stream import PrivateIdeStream, project_snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("gap", [False, True])
async def test_subscription_applies_owner_patch_and_unsubscribes(
    tmp_path, monkeypatch, gap
):
    monkeypatch.setattr("leam_api.shared_session.validate_installation", lambda _: None)
    seen = []
    done = asyncio.Event()

    async def write(writer, packet):
        data = json.dumps(packet).encode()
        writer.write(struct.pack("<I", len(data)) + data)
        await writer.drain()

    async def serve(reader, writer):
        try:
            while True:
                size = struct.unpack("<I", await reader.readexactly(4))[0]
                packet = json.loads(await reader.readexactly(size))
                seen.append(packet)
                if packet["method"] == "initialize":
                    await write(
                        writer,
                        {
                            "type": "response",
                            "requestId": packet["requestId"],
                            "method": "initialize",
                            "resultType": "success",
                            "result": {"clientId": "reader-fixture"},
                        },
                    )
                elif packet["method"] == "thread-owner-discovery":
                    await write(
                        writer, {**FIXTURE["owner"], "requestId": packet["requestId"]}
                    )
                elif packet["params"]["following"]:
                    await write(writer, FIXTURE["snapshot"])
                    patch = copy.deepcopy(FIXTURE["snapshot"])
                    patch["params"]["change"] = {
                        "type": "patches",
                        "baseRevision": 0 if gap else 1,
                        "revision": 2,
                        "patches": [
                            {
                                "op": "replace",
                                "path": ["latestModel"],
                                "value": "changed-fixture",
                            }
                        ],
                    }
                    await write(writer, patch)
                else:
                    break
        finally:
            writer.close()
            await writer.wait_closed()
            done.set()

    path = tmp_path / "ipc.sock"
    server = await asyncio.start_unix_server(serve, path)
    path.chmod(0o600)
    async with server:
        stream = PrivateIdeStream(path, tmp_path).watch("thread-fixture")
        first = await anext(stream)
        if gap:
            with pytest.raises(SharedSessionError, match="revision gap"):
                await anext(stream)
        else:
            second = await anext(stream)
            assert (
                second.revision == 2
                and second.state["latestModel"] == "changed-fixture"
            )
            assert first.state["latestModel"] == "gpt-6-astra"
            await stream.aclose()
        await asyncio.wait_for(done.wait(), 2)
    assert [m["method"] for m in seen] == [
        "initialize",
        "thread-owner-discovery",
        "thread-stream-following-changed",
        "thread-stream-following-changed",
    ]
    assert seen[-1]["params"]["following"] is False


def test_projection_resolves_canonical_history_and_bounds_text_not_just_turns():
    items = [{"id": str(i), "type": "agentMessage", "text": str(i)} for i in range(100)]
    items.append(
        {
            "id": "secret",
            "type": "commandExecution",
            "aggregatedOutput": "must not reach browser",
        }
    )
    state = {
        "id": "thread-fixture",
        "turns": [],
        "latestModel": "wrong-default",
        "latestThreadSettings": {
            "model": "gpt-6-astra",
            "effort": "high",
            "modelProvider": "openai",
        },
        "turnHistory": {
            "kind": "canonical",
            "history": {
                "islands": [{"entries": [{"value": "entity"}]}],
                "entitiesByKey": {
                    "entity": {
                        "turnId": "turn-1",
                        "status": "inProgress",
                        "items": items,
                    }
                },
            },
        },
    }
    snapshot = SessionSnapshot(
        SessionOwner("thread-fixture", "owner", 1, True), 3, state
    )
    result = project_snapshot(snapshot)
    shown = result["thread"]["turns"][0]["items"]
    assert len(shown) == 50 and shown[0]["text"] == "50" and shown[-1]["text"] == "99"
    assert result["activeTurnId"] == "turn-1" and result["truncated"]
    assert (
        result["thread"]["model"] == "gpt-6-astra"
        and result["thread"]["reasoningEffort"] == "high"
    )
    assert "must not reach browser" not in json.dumps(result)
    items[-2]["text"] = "é" * 200000
    result = project_snapshot(snapshot)
    assert len(result["thread"]["turns"][0]["items"][-1]["text"].encode()) <= 16384
    assert len(json.dumps(result, ensure_ascii=False).encode()) < 150000


def test_confirmed_user_message_replaces_steering_draft():
    state = {
        "id": "thread-fixture",
        "turns": [
            {
                "turnId": "turn",
                "status": "completed",
                "items": [
                    {
                        "id": "pending",
                        "type": "steeringUserMessage",
                        "input": [{"type": "text", "text": "duplicate"}],
                        "serverUserMessageId": "confirmed",
                    },
                    {
                        "id": "confirmed",
                        "type": "userMessage",
                        "content": [{"type": "text", "text": "actual"}],
                    },
                ],
            }
        ],
    }
    result = project_snapshot(
        SessionSnapshot(SessionOwner("thread-fixture", "owner", 1, True), 1, state)
    )
    assert len(result["thread"]["turns"][0]["items"]) == 1
    assert result["thread"]["turns"][0]["items"][0]["content"][0]["text"] == "actual"
