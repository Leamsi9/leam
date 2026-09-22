import asyncio
import json
import struct
from contextlib import asynccontextmanager

import httpx
import pytest
from shared_coding_fixtures import SHARED_THREAD

from leam_api.app import create_app
from leam_api.shared_coding import SharedCoding
from leam_api.shared_session_commands import PrivateIdeCommands
from leam_api.shared_session_stream import PrivateIdeStream
from leam_api.store import Store


class OtherCodex:
    def __init__(self):
        self.calls = []

    async def request(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method == "thread/list":
            return {"data": []}
        return {"thread": {"id": params.get("threadId", "other"), "name": "Other"}}

    async def close(self):
        pass


@asynccontextmanager
async def api_owner(
    tmp_path,
    monkeypatch,
    *,
    active=True,
    drop=False,
    interrupt_result="fixture-turn",
    requests=None,
    runtime_status=None,
    settings_applied=True,
):
    monkeypatch.setattr("leam_api.shared_session.validate_installation", lambda _: None)
    observed = []
    writers = set()
    current_owner = ["fixture-owner"]
    state = {
        "id": SHARED_THREAD,
        "title": "Shared fixture",
        "cwd": "/fixture/repo",
        "latestThreadSettings": {
            "model": "gpt-6-astra",
            "effort": "high",
            "modelProvider": "openai",
        },
        "threadGoal": {"objective": "Fixture objective", "status": "active"},
        "turns": [
            {
                "turnId": "fixture-turn",
                "status": "inProgress" if active else "completed",
                "items": [
                    {
                        "id": "existing",
                        "type": "agentMessage",
                        "text": "Existing owner transcript",
                    }
                ],
            }
        ],
        "requests": [
            {
                "id": 41,
                "method": "item/tool/requestUserInput",
                "params": {"secret": "never expose"},
            }
        ],
    }

    if runtime_status is not None:
        state["threadRuntimeStatus"] = runtime_status
        state["resumeState"] = "resumed"

    if requests is not None:
        state["requests"] = requests

    async def send(writer, packet):
        data = json.dumps(packet).encode()
        writer.write(struct.pack("<I", len(data)) + data)
        await writer.drain()

    async def serve(reader, writer):
        writers.add(writer)
        try:
            while True:
                size = struct.unpack("<I", await reader.readexactly(4))[0]
                packet = json.loads(await reader.readexactly(size))
                observed.append(packet)
                method = packet["method"]
                if method == "thread-stream-following-changed":
                    if not packet["params"]["following"]:
                        break
                    await send(
                        writer,
                        {
                            "type": "broadcast",
                            "sourceClientId": current_owner[0],
                            "method": "thread-stream-state-changed",
                            "version": 11,
                            "params": {
                                "hostId": "local",
                                "conversationId": SHARED_THREAD,
                                "change": {
                                    "type": "snapshot",
                                    "revision": 1,
                                    "conversationState": state,
                                },
                            },
                        },
                    )
                    continue
                if method == "initialize":
                    result = {"clientId": "fixture-reader"}
                elif method == "thread-owner-discovery":
                    result = {"supportsUntrustedAppInput": True}
                elif method.startswith("thread-follower-"):
                    if drop:
                        break
                    result = (
                        {"applied": settings_applied}
                        if method == "thread-follower-update-thread-settings"
                        else {"ok": True, "interruptedTurnId": interrupt_result}
                        if method == "thread-follower-interrupt-turn"
                        else (
                            {"ok": True}
                            if method
                            in [
                                "thread-follower-command-approval-decision",
                                "thread-follower-file-approval-decision",
                                "thread-follower-submit-user-input",
                            ]
                            else {"result": {"turnId": "fixture-turn"}}
                        )
                    )
                else:
                    raise AssertionError(method)
                await send(
                    writer,
                    {
                        "type": "response",
                        "method": method,
                        "requestId": packet["requestId"],
                        "resultType": "success",
                        "handledByClientId": current_owner[0],
                        "result": result,
                    },
                )
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            writers.discard(writer)

    path = tmp_path / "fixture.sock"
    server = await asyncio.start_unix_server(serve, path)
    path.chmod(0o600)
    store = Store(tmp_path / "data")
    other = OtherCodex()
    service = SharedCoding(
        store, PrivateIdeStream(path, tmp_path), PrivateIdeCommands(path, tmp_path)
    )
    app = create_app(
        tmp_path / "data",
        {"http://testserver"},
        bootstrap="fixture-bootstrap",
        codex=other,
        shared_coding=service,
    )
    async with (
        server,
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Origin": "http://testserver"},
        ) as client,
    ):
        login = await client.post(
            "/api/auth/setup",
            json={
                "bootstrap": "fixture-bootstrap",
                "password": "fixture-long-password",
            },
        )
        assert login.status_code == 200
        yield client, service, observed, other, current_owner
    for writer in tuple(writers):
        writer.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [False, True])
async def test_api_routes_original_owner_preserves_policy_and_deduplicates(
    tmp_path, monkeypatch, active
):
    async with api_owner(tmp_path, monkeypatch, active=active) as (
        client,
        service,
        observed,
        other,
        _,
    ):
        listing = (await client.get("/api/codex/threads")).json()
        assert listing["data"][0]["transport"] == "ide-owner"
        view = (await client.get(f"/api/codex/threads/{SHARED_THREAD}")).json()
        assert view["connected"] and view["thread"]["reasoningEffort"] == "high"
        assert view["thread"]["model"] == "gpt-6-astra"
        turns = (await client.get(f"/api/codex/threads/{SHARED_THREAD}/turns")).json()
        assert turns["data"][0]["items"][0]["text"] == "Existing owner transcript"
        goal = (await client.get(f"/api/codex/threads/{SHARED_THREAD}/goal")).json()
        assert goal["goal"]["objective"] == "Fixture objective"
        body = {
            "text": "  exact raw input\n",
            "requestId": "fixture-request-1",
            "generation": view["generation"],
        }
        url = f"/api/codex/threads/{SHARED_THREAD}/turns"
        assert (
            await client.post(url, json={**body, "generation": "stale"})
        ).status_code == 409
        response = await client.post(url, json=body)
        assert response.status_code == 200, response.text
        assert response.json()["operation"] == ("steer" if active else "start")
        assert (await client.post(url, json=body)).json() == response.json()
        commands = [m for m in observed if m["method"].startswith("thread-follower-")]
        assert len(commands) == 1
        payload = (
            commands[0]["params"]
            if active
            else commands[0]["params"]["turnStart"]["request"]
        )
        assert payload["input"][0]["text"] == body["text"]
        assert (
            payload["additionalContext"]["leam.agent-protocols"]["kind"]
            == "application"
        )
        assert all(params.get("threadId") != SHARED_THREAD for _, params in other.calls)
        requests = (await client.get("/api/codex/requests")).json()["items"]
        assert requests[0]["unsupported"] and "never expose" not in json.dumps(requests)
        assert (
            await client.post(
                "/api/codex/requests/" + requests[0]["id"], json={"answers": {}}
            )
        ).status_code == 409
        assert (await client.get("/api/codex/threads/other")).status_code == 200
        assert other.calls[-1][0] == "thread/read"
        with service.store.connect() as db:
            events = [
                json.loads(row[0])
                for row in db.execute(
                    'SELECT payload FROM events WHERE topic="codex.shared"'
                )
            ]
            receipt = db.execute(
                "SELECT result FROM requests WHERE id=?", (body["requestId"],)
            ).fetchone()[0]
        assert "raw input" not in receipt
        assert all(
            "Existing owner transcript" not in json.dumps(event) for event in events
        )


@pytest.mark.asyncio
async def test_lost_owner_response_remains_uncertain_without_resending(
    tmp_path, monkeypatch
):
    async with api_owner(tmp_path, monkeypatch, drop=True) as (
        client,
        service,
        observed,
        _,
        _,
    ):
        view = (await client.get(f"/api/codex/threads/{SHARED_THREAD}")).json()
        body = {
            "text": "raw message",
            "requestId": "fixture-request-2",
            "generation": view["generation"],
        }
        url = f"/api/codex/threads/{SHARED_THREAD}/turns"
        assert (await client.post(url, json=body)).status_code == 409
        assert (await client.post(url, json=body)).status_code == 409
        assert (
            len([m for m in observed if m["method"].startswith("thread-follower-")])
            == 1
        )
        receipt = (
            await client.get("/api/codex/submissions/" + body["requestId"])
        ).json()
        assert receipt["state"] == "pending"
        pending = (
            await client.post(
                f"/api/codex/threads/{SHARED_THREAD}/submissions/{body['requestId']}/reconcile",
                json={"text": body["text"]},
            )
        ).json()
        assert pending["state"] == "pending"
        # Only a confirmed native userMessage, not a draft, settles the receipt.
        service.snapshot.state["turns"][0]["items"].append(
            {
                "type": "userMessage",
                "id": "confirmed",
                "clientId": body["requestId"],
                "content": [{"type": "text", "text": body["text"]}],
            }
        )
        complete = (
            await client.post(
                f"/api/codex/threads/{SHARED_THREAD}/submissions/{body['requestId']}/reconcile",
                json={"text": body["text"]},
            )
        ).json()
        assert complete["state"] == "complete"
        assert (await client.post(url, json=body)).status_code == 200
        assert (
            len([m for m in observed if m["method"].startswith("thread-follower-")])
            == 1
        )


@pytest.mark.asyncio
async def test_owner_change_before_dispatch_releases_only_unsent_receipt(
    tmp_path, monkeypatch
):
    async with api_owner(tmp_path, monkeypatch) as (
        client,
        _service,
        observed,
        _,
        owner,
    ):
        view = (await client.get(f"/api/codex/threads/{SHARED_THREAD}")).json()
        owner[0] = "replacement-owner"
        body = {
            "text": "raw message",
            "requestId": "fixture-request-3",
            "generation": view["generation"],
        }
        response = await client.post(
            f"/api/codex/threads/{SHARED_THREAD}/turns", json=body
        )
        assert response.status_code == 503
        assert not [m for m in observed if m["method"].startswith("thread-follower-")]
        assert (await client.get("/api/codex/submissions/" + body["requestId"])).json()[
            "state"
        ] == "notSubmitted"
