"""Authenticated producers, actual domain transactions and durable delivery callers."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_api import FakeCodex, login
from test_tool_scope import host_scope, invoke, proposal, setup

from leam_api.app import create_app
from leam_api.artifacts import Artifacts
from leam_api.inbox import Inbox
from leam_api.inbox_events import TOPIC, InboxEvents
from leam_api.resource_tools import ResourceSave, ResourceTools
from leam_api.store import Store

H = {"origin": "http://testserver"}


def client_for(path):
    return TestClient(
        create_app(
            path,
            {"http://testserver"},
            bootstrap="bootstrap-for-tests",
            codex=FakeCodex(),
        ),
        client=("127.0.0.1", 1234),
    )


@pytest.fixture(autouse=True)
def controlled_delivery(monkeypatch):
    async def idle(self):
        await asyncio.sleep(3600)

    monkeypatch.setattr(InboxEvents, "run", idle)


def tool(client, root, body):
    return client.post(
        "/api/internal/tools",
        json={"tool": "leam_propose", "arguments": body},
        headers={
            "authorization": "Bearer " + (root / "tools-token").read_text().strip()
        },
    )


def approve(client, item):
    result = client.post(
        f"/api/proposals/{item['id']}/approve",
        headers=H,
        json={"fingerprint": item["fingerprint"]},
    )
    assert result.status_code == 200, result.text
    return result.json()


def events(store):
    with store.connect() as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT * FROM events WHERE topic=? ORDER BY id", (TOPIC,)
            )
        ]


def test_tool_create_requires_approval_then_delivers_once_with_source_links(tmp_path):
    with client_for(tmp_path) as client:
        login(client)
        body = proposal()
        pending = tool(client, tmp_path, body).json()
        store = client.app.state.store
        assert pending["state"] == "pending"
        assert pending["review"]["actionActor"] == "companion"
        assert events(store) == []
        completed = approve(client, pending)
        assert completed["state"] == "complete"
        assert len(events(store)) == 1
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sum(pool.map(lambda _: InboxEvents(store).drain(), range(2))) == 1
        approve(client, completed)
        assert tool(client, tmp_path, body).json()["state"] == "complete"
        assert InboxEvents(Store(tmp_path)).drain() == 0
        item = client.get("/api/inbox").json()["items"][0]
        assert item["origin"] == "automation" and item["unread"]
        assert {link["type"] for link in item["links"]} == {"commitment", "proposal"}
        assert "#today/inbox" in item["url"]
        assert len(events(store)) == 1


def test_manual_routes_and_body_actor_spoofing_stay_quiet(tmp_path):
    with client_for(tmp_path) as client:
        login(client)
        assert (
            client.post(
                "/api/proposals", json={**proposal(), "actor": "automation"}, headers=H
            ).status_code
            == 422
        )
        assert (
            tool(client, tmp_path, {**proposal(), "actor": "automation"}).status_code
            == 422
        )
        pending = client.post("/api/proposals", json=proposal(), headers=H).json()
        approve(client, pending)
        direct = client.post(
            "/api/commitments", json={"title": "Manual task"}, headers=H
        ).json()
        assert (
            client.patch(
                f"/api/commitments/{direct['id']}",
                json={"revision": direct["revision"], "title": "Manual correction"},
                headers=H,
            ).status_code
            == 200
        )
        Artifacts(client.app.state.store).publish(
            "manual",
            "Manual",
            "text",
            "Private document",
            source={"surface": "companion"},
        )
        assert events(client.app.state.store) == []
        assert InboxEvents(client.app.state.store).drain() == 0


def test_trusted_today_policy_autoapproval_produces_confirmed_note(
    tmp_path, monkeypatch
):
    with setup(tmp_path, monkeypatch) as (_, client, domain, _):
        result = invoke(client, domain, proposal(), scope=host_scope())
        assert result.status_code == 200 and result.json()["state"] == "complete"
        assert result.json()["review"]["approval"]["mode"] == "automatic"
        assert len(events(domain.store)) == 1
        assert InboxEvents(domain.store).drain() == 1
        assert (
            Inbox(domain.store).list()["items"][0]["subject"].startswith("Task added:")
        )


def test_edited_leam_proposal_preserves_actor_and_conflict_decline_do_not_notify(
    tmp_path,
):
    with client_for(tmp_path) as client:
        login(client)
        original = tool(client, tmp_path, proposal()).json()
        edited = client.patch(
            f"/api/proposals/{original['id']}",
            json={
                "fingerprint": original["fingerprint"],
                "input": {"title": "Corrected task"},
            },
            headers=H,
        )
        assert edited.status_code == 200, edited.text
        saved = approve(client, edited.json())
        assert saved["review"]["actionActor"] == "companion"
        entity = saved["result"]
        noop = tool(
            client,
            tmp_path,
            proposal(
                operation="commitment.edit",
                input={
                    "id": entity["id"],
                    "revision": entity["revision"],
                    "title": entity["title"],
                },
            ),
        ).json()
        approve(client, noop)
        assert len(events(client.app.state.store)) == 1
        stale = tool(
            client,
            tmp_path,
            proposal(
                operation="commitment.edit",
                input={"id": entity["id"], "revision": 1, "title": "Stale"},
            ),
        )
        assert stale.status_code == 409
        declined = tool(client, tmp_path, proposal(input={"title": "Distinct declined task"})).json()
        assert (
            client.post(
                f"/api/proposals/{declined['id']}/decline", headers=H
            ).status_code
            == 200
        )
        assert len(events(client.app.state.store)) == 1


def test_progress_and_subtask_change_use_confirmed_outcomes(tmp_path):
    with client_for(tmp_path) as client:
        login(client)
        created = approve(client, tool(client, tmp_path, proposal()).json())["result"]
        progress = proposal(
            operation="commitment.progress",
            input={
                "id": created["id"],
                "date": "2026-09-22",
                "revision": 0,
                "commitmentRevision": 1,
                "operation": "toggle",
            },
        )
        done = approve(client, tool(client, tmp_path, progress).json())
        assert done["result"]["commitment"]["status"] == "completed"
        assert InboxEvents(client.app.state.store).drain() == 2
        assert (
            Inbox(client.app.state.store)
            .list()["items"][0]["subject"]
            .startswith("Marked complete:")
        )
        assert "reason" not in json.loads(events(client.app.state.store)[-1]["payload"])
        current = done["result"]["commitment"]
        subtask_id = str(uuid4())
        child = approve(
            client,
            tool(
                client,
                tmp_path,
                proposal(
                    operation="commitment.subtask",
                    input={
                        "id": current["id"],
                        "revision": current["revision"],
                        "action": "add",
                        "subtaskId": subtask_id,
                        "title": "Follow up",
                    },
                ),
            ).json(),
        )
        approve(
            client,
            tool(
                client,
                tmp_path,
                proposal(
                    operation="commitment.subtask",
                    input={
                        "id": current["id"],
                        "revision": child["result"]["revision"],
                        "action": "remove",
                        "subtaskId": subtask_id,
                    },
                ),
            ).json(),
        )
        assert InboxEvents(client.app.state.store).drain() == 2
        assert (
            Inbox(client.app.state.store)
            .list()["items"][0]["subject"]
            .startswith("Subtask removed:")
        )


def test_source_transaction_failure_rolls_back_task_and_notification(
    tmp_path, monkeypatch
):
    with client_for(tmp_path) as client:
        login(client)
        pending = tool(client, tmp_path, proposal()).json()

        def fail(*args, **kwargs):
            raise ValueError("Fixture transaction interruption")

        monkeypatch.setattr("leam_api.inbox_events.enqueue", fail)
        assert (
            client.post(
                f"/api/proposals/{pending['id']}/approve", headers=H
            ).status_code
            == 409
        )
        assert client.get("/api/commitments").json()["items"] == []
        assert events(client.app.state.store) == []


def test_notification_failure_does_not_undo_task_or_starve_other_notes_and_can_retry(
    tmp_path, monkeypatch
):
    with client_for(tmp_path) as client:
        login(client)
        first = approve(
            client,
            tool(client, tmp_path, proposal(input={"title": "Fail delivery"})).json(),
        )
        approve(
            client,
            tool(
                client, tmp_path, proposal(input={"title": "Deliver normally"})
            ).json(),
        )
        store = client.app.state.store
        original = Inbox._create

        def fail_one(self, db, body, **kwargs):
            if "Fail delivery" in body.subject:
                raise HTTPException(429, "Sensitive fixture failure detail")
            return original(self, db, body, **kwargs)

        monkeypatch.setattr(Inbox, "_create", fail_one)
        now = [1000]
        delivery = InboxEvents(store, clock=lambda: now[0])
        assert delivery.drain() == 1
        assert len(store.entities("commitment")) == 2
        assert client.get(f"/api/proposals/{first['id']}").json()["state"] == "complete"
        for _ in range(2):
            now[0] += 1000
            InboxEvents(Store(tmp_path), clock=lambda: now[0]).drain()
        status = client.get("/api/inbox/status").json()["automationDelivery"]
        assert status["failed"] == 1 and status["retrying"] == 0
        assert "Sensitive" not in json.dumps(status)
        identity = status["failures"][0]["eventId"]
        assert client.post(f"/api/inbox/automation/{identity}/retry").status_code == 403
        monkeypatch.setattr(Inbox, "_create", original)
        assert (
            client.post(f"/api/inbox/automation/{identity}/retry", headers=H).json()[
                "state"
            ]
            == "queued"
        )
        assert InboxEvents(store).drain() == 1
        assert Inbox(store).status()["total"] == 2
        assert InboxEvents(store).drain() == 0


def test_generated_resource_tool_is_atomic_idempotent_and_private(tmp_path):
    with client_for(tmp_path) as client:
        login(client)
        data = {
            "id": "generated",
            "title": "Report",
            "kind": "text",
            "content": "Sensitive document text",
        }
        headers = {
            "authorization": "Bearer " + (tmp_path / "tools-token").read_text().strip()
        }
        for _ in range(2):
            result = client.post(
                "/api/internal/tools",
                json={"tool": "leam_resource_save", "arguments": data},
                headers=headers,
            )
            assert result.status_code == 200, result.text
        store = client.app.state.store
        assert len(events(store)) == 1
        assert "Sensitive document" not in events(store)[0]["payload"]
        assert InboxEvents(store).drain() == 1
        assert Inbox(store).list()["items"][0]["links"][0]["id"] == "generated"
        other = Store(tmp_path / "other")
        assert InboxEvents(other).drain() == 0 and Inbox(other).status()["total"] == 0


def test_missing_target_does_not_resurrect_or_block_delivery(tmp_path):
    with client_for(tmp_path) as client:
        login(client)
        saved = approve(client, tool(client, tmp_path, proposal()).json())
        store = client.app.state.store
        entity = saved["result"]
        store.delete(entity["id"], "commitment", entity["revision"])
        assert InboxEvents(store).drain() == 1
        link = Inbox(store).list()["items"][0]["links"][0]
        assert link["available"] is False
        assert store.entities("commitment") == []


def test_queued_resource_notification_survives_backup_and_respects_restore_hold(
    tmp_path,
):
    from test_backups import prepared

    from leam_api.backups import Backups, restore

    source = tmp_path / "source"
    store = prepared(source)
    ResourceTools(store).save(
        ResourceSave(
            id="saved-report", title="Report", kind="text", content="Saved document"
        )
    )
    manager = Backups(store)
    saved = manager.create()
    target = tmp_path / "restored"
    restore(manager.path(saved["id"]), target, source)
    restored = Store(target)
    assert len(events(restored)) == 1
    assert InboxEvents(restored).drain() == 0
    assert Inbox(restored).status()["automationDelivery"]["paused"] is True
