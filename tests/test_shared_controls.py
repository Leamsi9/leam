"""Authenticated control callers against the synthetic pinned owner socket."""

import asyncio

import pytest
from shared_coding_fixtures import SHARED_THREAD
from test_shared_coding_api import api_owner

URL = "/api/codex/shared/interrupt"


@pytest.mark.asyncio
async def test_exact_stop_wire_identity_and_duplicate_dispatch(tmp_path, monkeypatch):
    async with api_owner(tmp_path, monkeypatch) as (client, service, seen, other, _):
        view = (await client.get(f"/api/codex/threads/{SHARED_THREAD}")).json()
        body = {"generation": view["generation"], "turnId": "fixture-turn"}
        responses = await asyncio.gather(
            *[client.post(URL, json=body) for _ in range(3)]
        )
        assert all(r.status_code == 200 for r in responses)
        assert all(r.json() == responses[0].json() for r in responses)
        assert responses[0].json()["state"] == "requested"
        calls = [x for x in seen if x["method"].startswith("thread-follower-")]
        assert len(calls) == 1
        assert calls[0]["method"] == "thread-follower-interrupt-turn"
        assert calls[0]["version"] == 4
        assert calls[0]["params"] == {
            "conversationId": SHARED_THREAD,
            "mode": "user-stop",
            "expectedTurnId": "fixture-turn",
        }
        assert calls[0]["targetClientId"] == "fixture-owner"
        assert not other.calls
        assert service.snapshot.state["latestThreadSettings"]["effort"] == "high"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["generation", "turn", "owner"])
async def test_stale_stop_cannot_target_another_binding(tmp_path, monkeypatch, change):
    async with api_owner(tmp_path, monkeypatch) as (client, _service, seen, _, owner):
        view = (await client.get(f"/api/codex/threads/{SHARED_THREAD}")).json()
        body = {"generation": view["generation"], "turnId": "fixture-turn"}
        if change == "generation":
            body["generation"] = "stale"
        if change == "turn":
            body["turnId"] = "old-turn"
        if change == "owner":
            owner[0] = "replacement"
        response = await client.post(URL, json=body)
        assert response.status_code in [409, 503]
        assert not [x for x in seen if x["method"].startswith("thread-follower-")]


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, "wrong-turn"])
async def test_native_exact_turn_guard_and_invalid_ack(tmp_path, monkeypatch, result):
    async with api_owner(tmp_path, monkeypatch, interrupt_result=result) as (
        client,
        _,
        seen,
        _,
        _,
    ):
        view = (await client.get(f"/api/codex/threads/{SHARED_THREAD}")).json()
        body = {"generation": view["generation"], "turnId": "fixture-turn"}
        response = await client.post(URL, json=body)
        assert response.status_code == 200
        assert response.json()["state"] == ("stale" if result is None else "uncertain")
        await client.post(URL, json=body)
        assert len([x for x in seen if x["method"].startswith("thread-follower-")]) == 1


@pytest.mark.asyncio
async def test_lost_stop_response_cannot_be_resent(tmp_path, monkeypatch):
    async with api_owner(tmp_path, monkeypatch, drop=True) as (client, _, seen, _, _):
        view = (await client.get(f"/api/codex/threads/{SHARED_THREAD}")).json()
        body = {"generation": view["generation"], "turnId": "fixture-turn"}
        response = await client.post(URL, json=body)
        assert response.status_code == 200 and response.json()["state"] == "uncertain"
        assert (await client.post(URL, json=body)).json() == response.json()
        assert len([x for x in seen if x["method"].startswith("thread-follower-")]) == 1


@pytest.mark.asyncio
async def test_stop_pin_failure_and_origin_guard_precede_dispatch(
    tmp_path, monkeypatch
):
    from leam_api.shared_session import SharedSessionError

    async with api_owner(tmp_path, monkeypatch) as (client, _, seen, _, _):
        view = (await client.get(f"/api/codex/threads/{SHARED_THREAD}")).json()
        body = {"generation": view["generation"], "turnId": "fixture-turn"}
        denied = await client.post(
            URL, json=body, headers={"Origin": "https://untrusted.example"}
        )
        assert denied.status_code == 403

        def fail_pin(_):
            raise SharedSessionError("Pinned IDE installation changed")

        monkeypatch.setattr("leam_api.shared_session.validate_installation", fail_pin)
        response = await client.post(URL, json=body)
        assert response.status_code == 503
        assert not [x for x in seen if x["method"].startswith("thread-follower-")]


@pytest.mark.asyncio
async def test_existing_stop_reservation_after_service_restart_never_dispatches(
    tmp_path, monkeypatch
):
    import hashlib
    import json

    async with api_owner(tmp_path, monkeypatch) as (client, service, seen, _, _):
        view = (await client.get(f"/api/codex/threads/{SHARED_THREAD}")).json()
        body = {"generation": view["generation"], "turnId": "fixture-turn"}
        fingerprint = hashlib.sha256(
            json.dumps(
                ["ide-exact-stop-v4", body["generation"], body["turnId"]]
            ).encode()
        ).hexdigest()
        service.store.reserve("shared-stop:" + fingerprint, fingerprint)
        # A reservation with no response is durable evidence of possible dispatch.
        response = await client.post(URL, json=body)
        assert response.status_code == 200 and response.json()["state"] == "uncertain"
        assert not [x for x in seen if x["method"].startswith("thread-follower-")]


import pytest


@pytest.mark.asyncio
async def test_racing_completed_reservation_must_not_dispatch(tmp_path, monkeypatch):
    async with api_owner(tmp_path, monkeypatch) as (client, service, seen, _, _):
        view = (await client.get(f"/api/codex/threads/{SHARED_THREAD}")).json()
        original = service.store.reserve
        cached = {
            "state": "requested",
            "turnId": "fixture-turn",
            "detail": "existing receipt",
        }

        def race(key, fingerprint):
            original(key, fingerprint)
            service.store.finish(key, cached)
            return original(key, fingerprint)

        monkeypatch.setattr(service.store, "reserve", race)
        response = await client.post(
            "/api/codex/shared/interrupt",
            json={"generation": view["generation"], "turnId": "fixture-turn"},
        )
        assert response.status_code == 200
        assert not [p for p in seen if p["method"].startswith("thread-follower-")]
        assert response.json() == cached
