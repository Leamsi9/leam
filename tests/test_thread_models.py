"""Authenticated caller tests use synthetic sessions and pinned IPC fixtures only."""

import pytest
from fastapi.testclient import TestClient
from shared_coding_fixtures import SHARED_THREAD
from test_api import FakeCodex, login
from test_coding_models import CATALOG, HEADERS
from test_shared_coding_api import api_owner

from leam_api.app import create_app


class ModelThreads(FakeCodex):
    def __init__(self):
        super().__init__()
        self.selection = {"model": "gpt-5.6-sol", "reasoningEffort": "medium"}
        self.flip = None

    async def request(self, method, params, *, expected_generation=None):
        result = await super().request(
            method, params, expected_generation=expected_generation
        )
        if method == "model/list":
            if self.flip:
                self.flip()
            return {"data": CATALOG}
        if method == "thread/read":
            return {
                "thread": {
                    "id": params["threadId"],
                    "status": {"type": "idle"},
                    **self.selection,
                }
            }
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"]}}
        if method == "thread/settings/update":
            self.selection = {
                "model": params["model"],
                "reasoningEffort": params["effort"],
            }
            return {}
        return result


def selected(current):
    return {
        "model": "gpt-5.6-luna",
        "reasoningEffort": "low",
        "generation": current["generation"],
        "expectedModel": current["model"],
        "expectedReasoningEffort": current["reasoningEffort"],
    }


def native(tmp_path):
    bridge = ModelThreads()
    app = create_app(
        tmp_path, {"http://testserver"}, bootstrap="bootstrap-for-tests", codex=bridge
    )
    return TestClient(app), bridge


def test_exact_bound_native_session_auth_origin_catalog_and_readback(tmp_path):
    client, bridge = native(tmp_path)
    with client:
        path = "/api/codex/threads/exact-session/model"
        assert client.get(path).status_code == 401
        login(client)
        current = client.get(path).json()
        assert current["model"] == "gpt-5.6-sol" and not current["connected"]
        assert (
            client.post(path, json=selected(current), headers=HEADERS).status_code
            == 409
        )
        client.post(
            "/api/codex/threads/exact-session/connect",
            json={"handoffConfirmed": True},
            headers=HEADERS,
        )
        current = client.get(path).json()
        bridge.calls.clear()
        assert client.post(path, json=selected(current)).status_code == 403
        invalid = {**selected(current), "reasoningEffort": "high"}
        assert client.post(path, json=invalid, headers=HEADERS).status_code == 422
        assert not any(method == "thread/settings/update" for method, _ in bridge.calls)
        result = client.post(path, json=selected(current), headers=HEADERS)
        assert result.status_code == 200 and result.json()["confirmed"]
        assert result.json()["threadId"] == "exact-session"
        mutations = [
            (method, params)
            for method, params in bridge.calls
            if method == "thread/settings/update"
        ]
        assert mutations == [
            (
                "thread/settings/update",
                {"threadId": "exact-session", "model": "gpt-5.6-luna", "effort": "low"},
            )
        ]
        assert bridge.dispatch_generations[-1] == bridge.generation
        assert not any(
            method in {"thread/resume", "thread/start", "config/value/write"}
            for method, _ in bridge.calls
        )
        assert client.get("/api/settings/coding-model").json()["model"] == "gpt-5.6-sol"
        assert (
            client.post(path, json=selected(current), headers=HEADERS).status_code
            == 409
        )
        assert (
            client.post(
                path,
                json={**selected(result.json()), "approvalPolicy": "never"},
                headers=HEADERS,
            ).status_code
            == 422
        )


@pytest.mark.parametrize("change", ["generation", "configuration"])
def test_transition_during_catalog_lookup_never_dispatches(tmp_path, change):
    client, bridge = native(tmp_path)
    with client:
        login(client)
        client.post(
            "/api/codex/threads/exact/connect",
            json={"handoffConfirmed": True},
            headers=HEADERS,
        )
        path = "/api/codex/threads/exact/model"
        current = client.get(path).json()
        if change == "generation":
            bridge.flip = lambda: setattr(bridge, "generation", bridge.generation + 1)
        else:
            bridge.flip = lambda: bridge.selection.update(reasoningEffort="high")
        result = client.post(path, json=selected(current), headers=HEADERS)
        assert result.status_code == 409
        assert not any(method == "thread/settings/update" for method, _ in bridge.calls)


async def catalog(other, monkeypatch):
    original = other.request

    async def request(method, params, **kwargs):
        if method == "model/list":
            return {"data": CATALOG}
        return await original(method, params, **kwargs)

    monkeypatch.setattr(other, "request", request)


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [False, True])
async def test_shared_owner_settings_actual_socket_call_never_steers_or_takes_over(
    tmp_path, monkeypatch, active
):
    async with api_owner(tmp_path, monkeypatch, active=active) as (
        client,
        service,
        seen,
        other,
        _,
    ):
        await catalog(other, monkeypatch)
        path = f"/api/codex/threads/{SHARED_THREAD}/model"
        current = (await client.get(path)).json()
        result = await client.post(path, json=selected(current))
        assert result.status_code == 200
        assert (
            result.json()["accepted"] and not result.json()["confirmed"]
        )  # Fixture owner has not broadcast settings yet.
        command = [
            item for item in seen if item["method"].startswith("thread-follower-")
        ]
        assert len(command) == 1
        assert command[0]["version"] == 2
        assert command[0]["targetClientId"] == service.snapshot.owner.client_id
        assert command[0]["params"] == {
            "conversationId": SHARED_THREAD,
            "threadSettings": {"model": "gpt-5.6-luna", "effort": "low"},
            "condition": {"ifModelEquals": "gpt-6-astra", "ifEffortEquals": "high"},
        }
        assert other.calls == []
        service.snapshot.state["latestThreadSettings"].update(
            model="gpt-5.6-luna", effort="low"
        )
        assert (await client.get(path)).json()["model"] == "gpt-5.6-luna"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["owner", "runtime", "condition", "dropped"])
async def test_shared_changes_and_unknown_outcomes_visible_without_retry(
    tmp_path, monkeypatch, failure
):
    async with api_owner(
        tmp_path,
        monkeypatch,
        drop=failure == "dropped",
        settings_applied=failure != "condition",
    ) as (client, service, seen, other, owner):
        await catalog(other, monkeypatch)
        path = f"/api/codex/threads/{SHARED_THREAD}/model"
        current = (await client.get(path)).json()
        if failure == "owner":
            owner[0] = "replacement-owner"
        if failure == "runtime":
            service.snapshot.state["threadRuntimeStatus"] = {"type": "systemError"}
        result = await client.post(path, json=selected(current))
        assert result.status_code == (502 if failure == "dropped" else 409)
        commands = [
            item for item in seen if item["method"].startswith("thread-follower-")
        ]
        assert len(commands) == (1 if failure in {"condition", "dropped"} else 0)
        assert other.calls == []
