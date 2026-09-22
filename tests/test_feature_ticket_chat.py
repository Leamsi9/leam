"""Stable feature chats through authenticated callers; no real Codex transport."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from test_api import login
from test_ticket_chat import TicketCodex, make_ticket

from leam_api.backlog import Assessment, Backlog
from leam_api.updates import Publication

H = {"origin": "http://testserver"}


def backlog(app, feature="predeploy-feature", **fields):
    return Backlog(app.state.store).upsert(
        Assessment(
            feature=feature,
            title="One coherent feature",
            currentStep="Implementing",
            percent=50,
            rationale="Keep the same conversation through delivery",
            scope="Bounded scope",
            **fields,
        )
    )


def publish(app, feature, deployment="second"):
    return app.state.updates.publish(
        Publication(
            feature=feature,
            title="Delivered feature",
            summary="Feature now deployed",
            deploymentId=deployment,
            deployedAt=datetime.now(UTC) + timedelta(seconds=1),
        )
    )


def test_backlog_feature_chat_survives_publication_and_restart(tmp_path, monkeypatch):
    app, codex, _ = make_ticket(tmp_path, monkeypatch)
    backlog(app)
    url = "/api/features/predeploy-feature/chat"
    with TestClient(app) as client:
        assert client.get(url).status_code == 401
        login(client)
        assert client.get(url).json()["state"] == "notCreated"
        assert not codex.calls
        assert client.post(url).status_code == 403
        assert client.post(url, headers=H).json()["threadId"] == "ticket-thread"
        update = publish(app, "predeploy-feature")
        assert client.post(url, headers=H).json()["threadId"] == "ticket-thread"
        assert (
            client.get(f"/api/updates/{update['id']}/chat").json()["threadId"]
            == "ticket-thread"
        )
        assert len([call for call in codex.calls if call[0] == "thread/start"]) == 1
        current = client.get("/api/updates").json()["items"][0]
        assert current["rationale"] == "Keep the same conversation through delivery"
        assert current["qa"]["state"] == current["uat"]["state"] == "pending"
        result = client.post(
            "/api/codex/threads/ticket-thread/turns",
            headers=H,
            json={"text": "How does this fit?", "requestId": "feature-current-context"},
        )
        assert result.status_code == 200, result.text
        context = next(
            params for method, params in codex.calls if method == "turn/start"
        )["additionalContext"]["leam.update-ticket"]["value"]
        assert (
            "Feature now deployed" in context
            and "Keep the same conversation" in context
        )
    from leam_api.app import create_app

    restarted_codex = TicketCodex()
    restarted = create_app(tmp_path, {"http://testserver"}, codex=restarted_codex)
    with TestClient(restarted) as client:
        client.post(
            "/api/auth/login", json={"password": "long-password-for-tests"}, headers=H
        )
        assert client.get(url).json()["threadId"] == "ticket-thread"
        assert client.post(url, headers=H).status_code == 200
        assert [method for method, _ in restarted_codex.calls] == ["thread/resume"]


def test_legacy_sessions_are_preserved_and_selected_without_merging(
    tmp_path, monkeypatch
):
    app, codex, old = make_ticket(tmp_path, monkeypatch)
    store = app.state.store
    original = {"state": "ready", "threadId": "old-thread", "ticket": old}
    store.set("ticket-chat:" + old["id"], original)
    latest = publish(app, "test-feature")
    recent = {"state": "ready", "threadId": "recent-thread", "ticket": latest}
    store.set("ticket-chat:" + latest["id"], recent)
    with TestClient(app) as client:
        login(client)
        status = client.get("/api/features/test-feature/chat").json()
        assert status["threadId"] == "recent-thread" and not status["connected"]
        assert status["olderConversations"][0]["threadId"] == "old-thread"
        assert store.get("ticket-chat:feature:test-feature") is None
        assert (
            client.post("/api/features/test-feature/chat", headers=H).status_code == 200
        )
        assert (
            store.get("ticket-chat:feature:test-feature")["threadId"] == "recent-thread"
        )
        assert store.get("ticket-chat:" + old["id"]) == original
        assert store.get("ticket-chat:" + latest["id"]) == recent
        assert (
            client.get(f"/api/updates/{old['id']}/chat").json()["threadId"]
            == "old-thread"
        )
        publish(app, "test-feature", "third")
        assert (
            client.get("/api/features/test-feature/chat").json()["threadId"]
            == "recent-thread"
        )
        assert not [call for call in codex.calls if call[0] == "thread/start"]


def test_uncertain_legacy_creation_does_not_spawn_a_replacement(tmp_path, monkeypatch):
    app, codex, old = make_ticket(tmp_path, monkeypatch)
    app.state.store.set("ticket-chat:" + old["id"], {"state": "uncertain"})
    with TestClient(app) as client:
        login(client)
        assert (
            client.get("/api/features/test-feature/chat").json()["state"] == "uncertain"
        )
        assert (
            client.post("/api/features/test-feature/chat", headers=H).status_code == 409
        )
        assert not codex.calls


def test_concurrent_update_and_feature_connections_create_once(tmp_path, monkeypatch):
    class Slow(TicketCodex):
        async def request(self, method, params, *, expected_generation=None):
            if method == "thread/start":
                await asyncio.sleep(0.04)
            return await super().request(
                method, params, expected_generation=expected_generation
            )

    app, codex, item = make_ticket(tmp_path, monkeypatch, Slow())
    with TestClient(app) as client:
        login(client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(
                    lambda url: client.post(url, headers=H),
                    [
                        "/api/features/test-feature/chat",
                        f"/api/updates/{item['id']}/chat",
                    ],
                )
            )
        assert [r.status_code for r in responses] == [200, 200]
        assert len([call for call in codex.calls if call[0] == "thread/start"]) == 1


def test_missing_feature_is_not_created_from_browser_claims(tmp_path, monkeypatch):
    app, codex, _ = make_ticket(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        for key, status in [("not-existing", 404), ("BAD", 422)]:
            response = client.post(
                f"/api/features/{key}/chat", headers=H, json={"title": "Invented"}
            )
            assert response.status_code == status
        assert not codex.calls


def test_legacy_update_rationale_reads_exact_backlog_without_mutating_receipt(
    tmp_path, monkeypatch
):
    app, _, item = make_ticket(tmp_path, monkeypatch)
    backlog(app, "test-feature")
    with app.state.store.connect() as db:
        before = tuple(
            db.execute(
                "SELECT revision,sequence,body FROM deployment_updates WHERE id=?",
                (item["id"],),
            ).fetchone()
        )
    with TestClient(app) as client:
        login(client)
        result = client.get(f"/api/updates/{item['id']}").json()
        assert result["rationale"] == "Keep the same conversation through delivery"
        assert result["scope"] == "Bounded scope"
    with app.state.store.connect() as db:
        after = tuple(
            db.execute(
                "SELECT revision,sequence,body FROM deployment_updates WHERE id=?",
                (item["id"],),
            ).fetchone()
        )
    assert before == after


def test_feature_handoff_uses_existing_coordinator_and_verifies_source(
    tmp_path, monkeypatch
):
    from test_main_coding import CoordinatorCodex, connect, select, task

    app, codex, _ = make_ticket(tmp_path, monkeypatch, CoordinatorCodex())
    backlog(app)
    with TestClient(app) as client:
        login(client)
        select(client)
        connect(client)
        forged = client.post(
            "/api/coding/main/handoffs",
            headers=H,
            json=task(sourceTicketId="feature:predeploy-feature"),
        )
        assert forged.status_code == 409
        response = client.post(
            "/api/coding/main/handoffs",
            headers=H,
            json=task(sourceTicketId="feature:predeploy-feature", sourceThreadId=None),
        )
        assert response.status_code == 200, response.text
        assert response.json()["source"]["ticketId"] == "feature:predeploy-feature"
        sent = [params for method, params in codex.calls if method == "turn/start"]
        assert len(sent) == 1 and sent[0]["threadId"] == "main"
        assert not any(method == "thread/start" for method, _ in codex.calls)


def test_update_displays_recorded_assignment_without_duplicate_backlog_card(
    tmp_path, monkeypatch
):
    app, _, item = make_ticket(tmp_path, monkeypatch)
    backlog(
        app,
        "test-feature",
        worker="qa-worker",
        owner="coordinator",
        deliveryState="in_progress",
    )
    with TestClient(app) as client:
        login(client)
        current = client.get(f"/api/updates/{item['id']}").json()
        assert current["activeWork"] == {
            "deliveryState": "in_progress",
            "owner": "coordinator",
            "worker": "qa-worker",
            "currentStep": "Implementing",
            "revision": 1,
        }
        assert not client.get("/api/backlog").json()["items"]
        backlog(app, "test-feature", worker=None, deliveryState="queued")
        assert "activeWork" not in client.get(f"/api/updates/{item['id']}").json()
