"""HTTP caller coverage for coordinator identity, routing and replay safety."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from shared_coding_fixtures import SHARED_THREAD
from test_api import FakeCodex, login
from test_shared_coding_api import api_owner

from leam_api.app import create_app
from leam_api.codex import CodexError

H = {"origin": "http://testserver"}


class CoordinatorCodex(FakeCodex):
    active = False
    lose_reply = False

    async def request(self, method, params, *, expected_generation=None):
        result = await super().request(
            method, params, expected_generation=expected_generation
        )
        if method == "thread/turns/list":
            return {
                "data": [
                    {
                        "id": "active-turn",
                        "status": "inProgress" if self.active else "completed",
                    }
                ]
            }
        if method in {"turn/start", "turn/steer"} and self.lose_reply:
            raise CodexError("lost acknowledgment")
        if method == "turn/steer":
            return {"turnId": params["expectedTurnId"]}
        if method == "thread/list":
            return {"data": [{"id": "main", "name": "Main source"}, {"id": "source"}]}
        return result


def setup(tmp_path):
    codex = CoordinatorCodex()
    app = create_app(
        tmp_path, {"http://testserver"}, bootstrap="bootstrap-for-tests", codex=codex
    )
    return app, codex


def select(client, thread="main", revision=0):
    result = client.put(
        "/api/coding/main",
        headers=H,
        json={"threadId": thread, "expectedRevision": revision, "confirmed": True},
    )
    assert result.status_code == 200, result.text
    return result.json()


def task(**kwargs):
    return {
        "requestId": str(uuid4()),
        "mainRevision": 1,
        "mainThreadId": "main",
        "sourceThreadId": "source",
        "text": "Implement the reviewed change",
        "context": "Bounded context only",
        **kwargs,
    }


def connect(client, thread="main"):
    assert (
        client.post(
            f"/api/codex/threads/{thread}/connect",
            headers=H,
            json={"handoffConfirmed": True},
        ).status_code
        == 200
    )


def test_explicit_selection_cas_auth_and_delete_protection(tmp_path):
    app, codex = setup(tmp_path)
    with TestClient(app) as c:
        assert c.get("/api/coding/main").status_code == 401
        login(c)
        assert c.get("/api/coding/main").json()["main"] is None
        assert (
            c.put(
                "/api/coding/main",
                json={"threadId": "main", "expectedRevision": 0, "confirmed": True},
            ).status_code
            == 403
        )
        assert select(c)["permissionEnforcement"] == "instructions-only"
        stale = c.put(
            "/api/coding/main",
            headers=H,
            json={"threadId": "source", "expectedRevision": 0, "confirmed": True},
        )
        assert stale.status_code == 409
        listing = c.get("/api/codex/threads").json()["data"]
        assert next(t for t in listing if t["id"] == "main")["leamMain"]
        deletion = c.request(
            "DELETE",
            "/api/codex/threads/main",
            headers=H,
            json={"confirmed": True, "deleteChildren": True, "stopRunning": True},
        )
        assert deletion.status_code == 409
        assert not any(m == "thread/delete" for m, _ in codex.calls)


@pytest.mark.parametrize("active", [False, True])
def test_native_handoff_actual_caller_policies_active_steering_and_replay(
    tmp_path, active
):
    app, codex = setup(tmp_path)
    codex.active = active
    with TestClient(app) as c:
        login(c)
        select(c)
        connect(c)
        body = task()
        result = c.post("/api/coding/main/handoffs", headers=H, json=body)
        assert result.status_code == 200, result.text
        assert result.json()["state"] == "accepted"
        turns = [(m, p) for m, p in codex.calls if m in {"turn/start", "turn/steer"}]
        assert len(turns) == 1
        method, sent = turns[0]
        assert method == ("turn/steer" if active else "turn/start")
        assert sent["threadId"] == "main"
        assert sent["clientUserMessageId"] == result.json()["submissionId"]
        assert "leam.agent-protocols" in sent["additionalContext"]
        assert "This is Main" in sent["additionalContext"]["leam.main-coding"]["value"]
        assert "Bounded context only" in sent["input"][0]["text"]
        assert "source" in sent["input"][0]["text"]
        assert "sandboxPolicy" not in sent and "approvalPolicy" not in sent
        assert codex.dispatch_generations == [0]
        # Reassignment never retargets a replay, including the receipt after restart.
        select(c, "source", 1)
        replay = c.post("/api/coding/main/handoffs", headers=H, json=body)
        assert replay.json() == result.json()
        assert replay.json()["mainThreadId"] == "main"
        changed = c.post(
            "/api/coding/main/handoffs",
            headers=H,
            json={**body, "text": "another task"},
        )
        assert changed.status_code == 409
        assert len([m for m, _ in codex.calls if m.startswith("turn/")]) == 1


def test_unknown_receipt_is_never_resent_and_stale_target_never_dispatches(tmp_path):
    app, codex = setup(tmp_path)
    with TestClient(app) as c:
        login(c)
        select(c)
        connect(c)
        stale = c.post(
            "/api/coding/main/handoffs", headers=H, json=task(mainRevision=2)
        )
        assert stale.status_code == 409
        codex.lose_reply = True
        body = task()
        assert (
            c.post("/api/coding/main/handoffs", headers=H, json=body).status_code == 502
        )
        assert (
            c.get(f"/api/coding/main/handoffs/{body['requestId']}").json()["state"]
            == "uncertain"
        )
        assert (
            c.post("/api/coding/main/handoffs", headers=H, json=body).json()["state"]
            == "uncertain"
        )
        assert len([m for m, _ in codex.calls if m == "turn/start"]) == 1


def test_secondary_discussion_stays_in_source_with_planning_policy(tmp_path):
    app, codex = setup(tmp_path)
    with TestClient(app) as c:
        login(c)
        select(c)
        connect(c, "source")
        response = c.post(
            "/api/codex/threads/source/turns",
            headers=H,
            json={
                "text": "Discuss implementation alternatives",
                "requestId": "discussion-123",
            },
        )
        assert response.status_code == 200
        sent = next(p for m, p in codex.calls if m == "turn/start")
        assert sent["threadId"] == "source"
        assert sent["input"][0]["text"] == "Discuss implementation alternatives"
        assert (
            "do not implement" in sent["additionalContext"]["leam.main-coding"]["value"]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [False, True])
async def test_shared_main_uses_actual_owner_caller_and_never_resumes_native(
    tmp_path, monkeypatch, active
):
    async with api_owner(tmp_path, monkeypatch, active=active) as (
        client,
        _shared,
        observed,
        other,
        _,
    ):
        selected = await client.put(
            "/api/coding/main",
            json={"threadId": SHARED_THREAD, "expectedRevision": 0, "confirmed": True},
        )
        assert selected.status_code == 200
        body = task(mainThreadId=SHARED_THREAD)
        result = await client.post("/api/coding/main/handoffs", json=body)
        assert result.status_code == 200, result.text
        assert result.json()["state"] == "accepted"
        assert result.json()["receipt"]["operation"] == ("steer" if active else "start")
        replay = await client.post("/api/coding/main/handoffs", json=body)
        assert replay.json() == result.json()
        assert not any(
            m in {"thread/resume", "turn/start", "turn/steer"} for m, _ in other.calls
        )
        commands = [m for m in observed if m["method"].startswith("thread-follower-")]
        assert len(commands) == 1
        payload = (
            commands[0]["params"]
            if active
            else commands[0]["params"]["turnStart"]["request"]
        )
        assert "leam.agent-protocols" in payload["additionalContext"]
        assert (
            "This is Main" in payload["additionalContext"]["leam.main-coding"]["value"]
        )


def test_reviewed_companion_handoff_routes_main_without_new_thread(
    tmp_path, monkeypatch
):
    from test_coding_handoff import propose, review

    monkeypatch.setenv("LEAM_CODING_WORKSPACE", str(tmp_path))
    app, codex = setup(tmp_path)
    with TestClient(app) as c:
        login(c)
        select(c)
        connect(c)
        item = propose(c)
        reviewed = review(c, item["id"], "Implement a bounded fix")
        assert reviewed["main"]["threadId"] == "main"
        response = c.post(
            f"/api/coding/handoffs/{item['id']}/start",
            headers=H,
            json={"previewToken": reviewed["previewToken"], "confirmed": True},
        )
        assert response.status_code == 200, response.text
        assert response.json()["threadId"] == "main"
        assert response.json()["state"] == "accepted"
        assert c.get("/api/proposals/status").json()["unreadCount"] == 0
        assert not any(m == "thread/start" for m, _ in codex.calls)
        sent = next(p for m, p in codex.calls if m == "turn/start")
        assert "companion-source" in sent["input"][0]["text"]
        assert sent["threadId"] == "main"


def test_ticket_origin_is_verified_without_creating_source_chat(tmp_path, monkeypatch):
    from test_ticket_chat import make_ticket

    app, codex, ticket = make_ticket(tmp_path, monkeypatch, CoordinatorCodex())
    with TestClient(app) as c:
        login(c)
        select(c)
        connect(c)
        bad = c.post(
            "/api/coding/main/handoffs",
            headers=H,
            json=task(sourceTicketId=ticket["id"]),
        )
        assert bad.status_code == 409
        result = c.post(
            "/api/coding/main/handoffs",
            headers=H,
            json=task(sourceTicketId=ticket["id"], sourceThreadId=None),
        )
        assert result.status_code == 200, result.text
        assert result.json()["source"]["deploymentId"] == "build-1"
        assert not any(m == "thread/start" for m, _ in codex.calls)
        assert app.state.updates.list()["items"][0]["uat"]["state"] == "pending"


def test_reassignment_from_another_instance_before_reservation_rejects_dispatch(tmp_path):
    from leam_api.main_coding import MainCoding, SelectMain

    app, codex = setup(tmp_path)
    first = app.state.main_coding
    second = MainCoding(first.store, codex, first.shared, first.bindings, first.tickets)
    original = codex.request
    reassigned = False

    async def concurrent_request(method, params, *, expected_generation=None):
        nonlocal reassigned
        if method == "thread/read" and params["threadId"] == "source" and not reassigned:
            reassigned = True
            await second.select(SelectMain(threadId="replacement-main", expectedRevision=1, confirmed=True))
        return await original(method, params, expected_generation=expected_generation)

    codex.request = concurrent_request
    with TestClient(app) as client:
        login(client)
        select(client)
        connect(client)
        body = task()
        response = client.post("/api/coding/main/handoffs", headers=H, json=body)
        assert response.status_code == 409, response.text
        assert first.binding()["threadId"] == "replacement-main"
        assert not any(method in {"turn/start", "turn/steer"} for method, _ in codex.calls)
        assert first.lookup(body["requestId"])["state"] == "not_recorded"
