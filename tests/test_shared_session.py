import asyncio
import json
import struct
from pathlib import Path

import pytest

from leam_api.shared_session import PrivateIdeReader, SharedSessionError

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/shared-session/owner-snapshot.json").read_text()
)


@pytest.mark.asyncio
async def test_reader_discovers_existing_owner_and_unsubscribes_without_turn_requests(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "leam_api.shared_session.validate_installation", lambda _root: None
    )
    seen = []
    finished = asyncio.Event()

    async def serve(reader, writer):
        try:
            while True:
                size = struct.unpack("<I", await reader.readexactly(4))[0]
                message = json.loads(await reader.readexactly(size))
                seen.append(message)
                if message["method"] == "initialize":
                    response = {
                        "type": "response",
                        "requestId": message["requestId"],
                        "resultType": "success",
                        "method": "initialize",
                        "handledByClientId": "reader-fixture",
                        "result": {"clientId": "reader-fixture"},
                    }
                elif message["method"] == "thread-owner-discovery":
                    response = {**FIXTURE["owner"], "requestId": message["requestId"]}
                elif (
                    message["method"] == "thread-stream-following-changed"
                    and message["params"]["following"]
                ):
                    response = FIXTURE["snapshot"]
                else:
                    assert message["params"]["following"] is False
                    break
                data = json.dumps(response).encode()
                writer.write(struct.pack("<I", len(data)) + data)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            finished.set()

    path = tmp_path / "ipc.sock"
    server = await asyncio.start_unix_server(serve, path)
    path.chmod(0o600)
    async with server:
        snapshot = await PrivateIdeReader(path, tmp_path).read_snapshot(
            "thread-fixture"
        )
        await asyncio.wait_for(finished.wait(), 2)
    assert snapshot.owner.client_id == "owner-fixture"
    assert snapshot.owner.supports_untrusted_app_input is True
    assert snapshot.revision == 1 and snapshot.state["latestModel"] == "gpt-6-astra"
    assert [m["method"] for m in seen] == [
        "initialize",
        "thread-owner-discovery",
        "thread-stream-following-changed",
        "thread-stream-following-changed",
    ]
    assert seen[1]["params"] == {"hostId": "local", "conversationId": "thread-fixture"}
    assert seen[2]["targetClientIds"] == ["owner-fixture"]


@pytest.mark.asyncio
async def test_reader_rejects_oversized_frames_before_reading_body(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "leam_api.shared_session.validate_installation", lambda _root: None
    )

    async def serve(reader, writer):
        await reader.read(4096)
        writer.write(struct.pack("<I", 33 * 1024 * 1024))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    path = tmp_path / "ipc.sock"
    server = await asyncio.start_unix_server(serve, path)
    path.chmod(0o600)
    async with server:
        with pytest.raises(SharedSessionError, match="frame"):
            await PrivateIdeReader(path, tmp_path).discover_owner("thread-fixture")


@pytest.mark.asyncio
async def test_pin_failure_happens_before_any_connection(tmp_path):
    with pytest.raises(SharedSessionError, match="installation"):
        await PrivateIdeReader(tmp_path / "missing.sock", tmp_path).discover_owner(
            "thread-fixture"
        )
