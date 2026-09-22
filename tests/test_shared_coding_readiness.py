"""Actual authenticated routes and follower snapshot transitions, synthetic owner."""

import asyncio

import pytest
from shared_coding_fixtures import SHARED_THREAD
from test_shared_coding_api import api_owner

from leam_api.attachments import AttachmentStore
from leam_api.shared_session import SessionSnapshot
from leam_api.shared_session_stream import PrivateIdeStream


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["notLoaded", "systemError"])
async def test_known_unready_owner_blocks_before_policy_and_reservation(
    tmp_path, monkeypatch, status
):
    async with api_owner(
        tmp_path,
        monkeypatch,
        runtime_status={"type": status, "error": "private runtime error"},
    ) as (client, service, seen, _, _):
        view = (await client.get(f"/api/codex/threads/{SHARED_THREAD}")).json()
        assert view["transportConnected"] and not view["connected"]
        assert view["runtimeStatus"] == status
        assert "VS Code" in view["error"] and "private runtime error" not in str(view)
        assert view["thread"]["turns"]  # Retain history while runtime is unavailable.

        def forbid_policy():
            raise AssertionError("Unavailable runtime reached policy work")

        monkeypatch.setattr("leam_api.shared_coding.coding_context", forbid_policy)
        response = await client.post(
            f"/api/codex/threads/{SHARED_THREAD}/turns",
            json={
                "text": "preserve this draft",
                "requestId": "not-reserved",
                "generation": view["generation"],
            },
        )
        assert response.status_code == 409 and "not ready" in response.json()["detail"]
        with service.store.connect() as db:
            assert (
                db.execute("SELECT 1 FROM requests WHERE id='not-reserved'").fetchone()
                is None
            )
        assert not [p for p in seen if p["method"].startswith("thread-follower-")]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["idle", "active", None, "future-status"])
async def test_healthy_and_legacy_unknown_snapshots_remain_sendable(
    tmp_path, monkeypatch, status
):
    async with api_owner(
        tmp_path,
        monkeypatch,
        active=status == "active",
        runtime_status={"type": status} if status else None,
    ) as (client, _, seen, _, _):
        view = (await client.get(f"/api/codex/threads/{SHARED_THREAD}")).json()
        assert (
            view["connected"] and view["transportConnected"] and view["error"] is None
        )
        assert view["runtimeStatus"] == (
            status if status in ("idle", "active") else "unknown"
        )
        body = {
            "text": "explicit input",
            "requestId": "accepted",
            "generation": view["generation"],
        }
        response = await client.post(
            f"/api/codex/threads/{SHARED_THREAD}/turns", json=body
        )
        assert response.status_code == 200
        assert response.json()["operation"] == (
            "steer" if status == "active" else "start"
        )
        assert len([p for p in seen if p["method"].startswith("thread-follower-")]) == 1


@pytest.mark.asyncio
async def test_runtime_failure_during_async_preparation_is_not_reserved(
    tmp_path, monkeypatch
):
    async with api_owner(tmp_path, monkeypatch) as (client, service, seen, _, _):
        view = (await client.get(f"/api/codex/threads/{SHARED_THREAD}")).json()

        async def change_during_prepare(self, ids):
            assert ids == []
            service.snapshot.state["threadRuntimeStatus"] = {"type": "systemError"}
            return [], {}

        monkeypatch.setattr(AttachmentStore, "coding", change_during_prepare)
        response = await client.post(
            f"/api/codex/threads/{SHARED_THREAD}/turns",
            json={
                "text": "draft",
                "requestId": "changed-during-policy",
                "generation": view["generation"],
            },
        )
        assert response.status_code == 409
        with service.store.connect() as db:
            assert (
                db.execute(
                    "SELECT 1 FROM requests WHERE id='changed-during-policy'"
                ).fetchone()
                is None
            )
        assert not [p for p in seen if p["method"].startswith("thread-follower-")]


@pytest.mark.asyncio
async def test_follow_runtime_recovery_invalidates_old_generation_without_auto_dispatch(
    tmp_path, monkeypatch
):
    original = PrivateIdeStream.watch
    changes = asyncio.Queue()

    async def changing_watch(self, thread_id):
        generator = original(self, thread_id)
        try:
            snapshot = await anext(generator)
            yield snapshot
            while True:
                status = await changes.get()
                snapshot = SessionSnapshot(
                    snapshot.owner,
                    snapshot.revision + 1,
                    {
                        **snapshot.state,
                        "threadRuntimeStatus": {"type": status},
                    },
                )
                yield snapshot
        finally:
            await generator.aclose()

    monkeypatch.setattr(PrivateIdeStream, "watch", changing_watch)
    async with api_owner(tmp_path, monkeypatch, runtime_status={"type": "idle"}) as (
        client,
        service,
        seen,
        _,
        _,
    ):
        first = (await client.get(f"/api/codex/threads/{SHARED_THREAD}")).json()
        generations = [first["generation"]]
        for status in ("active", "systemError", "notLoaded", "idle"):
            revision = service.snapshot.revision
            await changes.put(status)
            for _ in range(100):
                if service.snapshot.revision != revision:
                    break
                await asyncio.sleep(0.005)
            assert service.snapshot.revision != revision
            view = (await client.get(f"/api/codex/threads/{SHARED_THREAD}")).json()
            assert view["runtimeStatus"] == status
            assert view["connected"] == (status in ("idle", "active"))
            generations.append(view["generation"])
        assert generations[0] == generations[1]
        assert generations[1] != generations[2]
        assert generations[2] == generations[3]
        assert generations[3] != generations[4]
        assert not [p for p in seen if p["method"].startswith("thread-follower-")]
        url = f"/api/codex/threads/{SHARED_THREAD}/turns"
        body = {
            "text": "explicit retry",
            "requestId": "after-recovery",
            "generation": first["generation"],
        }
        assert (await client.post(url, json=body)).status_code == 409
        assert (
            await client.post(url, json={**body, "generation": view["generation"]})
        ).status_code == 200
        assert len([p for p in seen if p["method"].startswith("thread-follower-")]) == 1
