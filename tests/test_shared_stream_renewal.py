import asyncio

import pytest
from shared_coding_fixtures import SHARED_THREAD
from test_shared_coding_api import api_owner

from leam_api.shared_session import SharedSessionError
from leam_api.shared_session_stream import PrivateIdeStream


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_owner", [False, True])
async def test_scheduled_renewal_is_immediate_and_binding_follows_owner(
    tmp_path, monkeypatch, changed_owner
):
    original = PrivateIdeStream._connection

    def short_lease(self, thread_id, **kwargs):
        kwargs["lifetime"] = 0.08
        return original(self, thread_id, **kwargs)

    monkeypatch.setattr(PrivateIdeStream, "_connection", short_lease)
    async with api_owner(tmp_path, monkeypatch) as (client, service, seen, _, owner):
        first = await client.get(f"/api/codex/threads/{SHARED_THREAD}")
        assert first.status_code == 200
        generation = first.json()["generation"]
        previous = service.snapshot
        if changed_owner:
            owner[0] = "replacement-fixture-owner"
        for _ in range(60):
            await asyncio.sleep(0.01)
            if service.snapshot is not previous and service.connected:
                break
        assert service.snapshot is not previous, "planned renewal waited like a failure"
        current = await client.get(f"/api/codex/threads/{SHARED_THREAD}")
        assert current.status_code == 200
        view = current.json()
        assert view["connected"] and view["error"] is None
        assert (view["generation"] != generation) is changed_owner
        assert (
            view["thread"]["turns"][0]["items"][0]["text"]
            == "Existing owner transcript"
        )
        assert all(
            not packet["method"].startswith("thread-follower-") for packet in seen
        )


@pytest.mark.asyncio
async def test_shared_api_projects_only_bounded_exact_input_identity(
    tmp_path, monkeypatch
):
    async with api_owner(tmp_path, monkeypatch) as (client, service, _, _, _):
        await client.get(f"/api/codex/threads/{SHARED_THREAD}")
        service.snapshot.state["turns"][0]["items"] = [
            {
                "id": "confirmed",
                "type": "userMessage",
                "clientId": "request-one",
                "content": [{"type": "text", "text": "same input"}],
            },
            {
                "id": "steering",
                "type": "steeringUserMessage",
                "clientUserMessageId": "request-two",
                "status": "pending",
                "input": [{"type": "text", "text": "same input"}],
            },
            {
                "id": "oversized",
                "type": "userMessage",
                "clientId": "x" * 257,
                "content": [{"type": "text", "text": "same input"}],
            },
        ]
        response = await client.get(f"/api/codex/threads/{SHARED_THREAD}/turns")
        assert response.status_code == 200
        items = response.json()["data"][0]["items"]
        assert items[0]["clientId"] == "request-one"
        assert items[1]["clientUserMessageId"] == "request-two"
        assert "clientId" not in items[2]


@pytest.mark.asyncio
async def test_genuine_stream_failure_invalidates_binding_and_retains_transcript(
    tmp_path, monkeypatch
):
    original = PrivateIdeStream.watch
    calls = []

    async def failing_watch(self, thread_id):
        calls.append(thread_id)
        generator = original(self, thread_id)
        try:
            yield await anext(generator)
            raise SharedSessionError("Synthetic transport loss")
        finally:
            await generator.aclose()

    monkeypatch.setattr(PrivateIdeStream, "watch", failing_watch)
    async with api_owner(tmp_path, monkeypatch) as (client, service, seen, _, _):
        generation = service.generation
        view = (await client.get(f"/api/codex/threads/{SHARED_THREAD}")).json()
        for _ in range(30):
            if service.error:
                break
            await asyncio.sleep(0.01)
        view = (await client.get(f"/api/codex/threads/{SHARED_THREAD}")).json()
        assert not view["connected"] and "unavailable" in view["error"]
        assert view["generation"] != generation
        assert (
            view["thread"]["turns"][0]["items"][0]["text"]
            == "Existing owner transcript"
        )
        await asyncio.sleep(0.1)
        assert len(calls) == 1, "real failure must retain bounded backoff"
        response = await client.post(
            f"/api/codex/threads/{SHARED_THREAD}/turns",
            json={
                "text": "Do not dispatch while disconnected",
                "requestId": "disconnected-input",
                "generation": view["generation"],
            },
        )
        assert response.status_code in (409, 503)
        assert all(
            not packet["method"].startswith("thread-follower-") for packet in seen
        )
