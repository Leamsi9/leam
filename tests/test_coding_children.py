"""Authenticated caller coverage; no real workers or model generations."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_api import login
from test_main_coding import CoordinatorCodex, H, connect, select

from leam_api.app import create_app
from leam_api.backlog import Assessment, Backlog
from leam_api.backlog_assignment import Assignment, transition
from leam_api.codex import CodexError


class ChildCodex(CoordinatorCodex):
    child_status = "completed"
    child_error = False
    changed = None

    async def request(self, method, params, *, expected_generation=None):
        if params.get("threadId") == "child" and method == "thread/turns/list":
            self.calls.append((method, params))
            assert params == {
                "threadId": "child",
                "limit": 100,
                "sortDirection": "desc",
                "itemsView": "notLoaded",
            }
            if self.child_error:
                raise CodexError("private provider failure")
            if self.changed:
                callback, self.changed = self.changed, None
                callback()
            return {"data": [{"id": "child-turn", "status": self.child_status}]}
        return await super().request(
            method, params, expected_generation=expected_generation
        )


def fixture(tmp_path):
    codex = ChildCodex()
    app = create_app(
        tmp_path, {"http://testserver"}, bootstrap="bootstrap-for-tests", codex=codex
    )
    store = app.state.store
    backlog = Backlog(store)
    item = backlog.upsert(
        Assessment(
            feature="task",
            title="Task",
            currentStep="Queued",
            percent=0,
            deliveryState="queued",
        )
    )
    assignment = transition(
        backlog,
        Assignment(
            requestId=uuid4(),
            feature="task",
            worker="child-worker",
            expectedRevision=item["revision"],
            operation="start",
            note="Bounded child task",
        ),
    )
    body = {
        "requestId": str(uuid4()),
        "assignmentId": assignment["requestId"],
        "backlogRevision": assignment["revision"],
        "mainThreadId": "main",
        "mainRevision": 1,
        "childThreadId": "child",
        "childTurnId": "child-turn",
    }
    return app, codex, body, backlog


def register(client, body):
    result = client.post("/api/coding/main/children", headers=H, json=body)
    assert result.status_code == 200, result.text
    return result.json()


def test_authenticated_origin_protected_exact_registration_and_duplicate(tmp_path):
    app, codex, body, _ = fixture(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/coding/main/children").status_code == 401
        login(client)
        select(client)
        assert client.post("/api/coding/main/children", json=body).status_code == 403
        row = register(client, body)
        calls = len(codex.calls)
        assert register(client, body) == row and len(codex.calls) == calls
        changed = {**body, "requestId": str(uuid4())}
        assert (
            client.post(
                "/api/coding/main/children", headers=H, json=changed
            ).status_code
            == 409
        )
        assert row["observation"]["status"] == "completed" and row["handover"] is None
        assert not any(
            method in {"thread/start", "turn/start", "turn/steer", "thread/resume"}
            for method, _ in codex.calls
        )
        assert len(client.get("/api/coding/main/children").json()["items"]) == 1


def test_register_rechecks_assignment_after_remote_read(tmp_path):
    app, codex, body, backlog = fixture(tmp_path)

    def pause():
        transition(
            backlog,
            Assignment(
                requestId=uuid4(),
                feature="task",
                worker="child-worker",
                expectedRevision=body["backlogRevision"],
                operation="pause",
                note="Paused",
            ),
        )

    with TestClient(app) as client:
        login(client)
        select(client)
        codex.changed = pause
        assert (
            client.post("/api/coding/main/children", headers=H, json=body).status_code
            == 409
        )
        assert client.get("/api/coding/main/children").json()["items"] == []


def test_restart_reconciliation_failure_visible_and_never_dispatches(tmp_path):
    app, codex, body, _ = fixture(tmp_path)
    with TestClient(app) as client:
        login(client)
        select(client)
        register(client, body)
    app2 = create_app(tmp_path, {"http://testserver"}, codex=codex)
    codex.child_error = True
    with TestClient(app2) as client:
        assert (
            client.post(
                "/api/auth/login",
                headers=H,
                json={"password": "long-password-for-tests"},
            ).status_code
            == 200
        )
        before = len(codex.calls)
        row = client.post(
            f"/api/coding/main/children/{body['requestId']}/reconcile", headers=H
        ).json()
        assert row["observation"]["status"] == "unknown"
        assert "private provider" not in str(row)
        assert not any(
            m in {"turn/start", "turn/steer", "thread/start"}
            for m, _ in codex.calls[before:]
        )
        codex.child_error = False
        codex.child_status = "interrupted"
        row = client.post(
            f"/api/coding/main/children/{body['requestId']}/reconcile", headers=H
        ).json()
        assert row["observation"]["status"] == "interrupted"


@pytest.mark.parametrize("lost", [False, True])
def test_explicit_handover_original_receipt_no_duplicate_dispatch(tmp_path, lost):
    app, codex, body, _ = fixture(tmp_path)
    with TestClient(app) as client:
        login(client)
        select(client)
        connect(client)
        row = register(client, body)
        codex.lose_reply = lost
        url = f"/api/coding/main/children/{row['id']}/handover"
        handover = {
            "expectedRevision": row["revision"],
            "summary": "Ready for review; no deployment",
            "evidence": "commit abc123",
        }
        result = client.post(url, headers=H, json=handover)
        assert result.status_code == 200, result.text
        outcome = result.json()["handover"]["delivery"]["state"]
        assert outcome == ("uncertain" if lost else "accepted")
        count = len([m for m, _ in codex.calls if m in {"turn/start", "turn/steer"}])
        client.post(url, headers=H, json=handover)
        client.post(f"/api/coding/main/children/{row['id']}/reconcile", headers=H)
        assert (
            len([m for m, _ in codex.calls if m in {"turn/start", "turn/steer"}])
            == count
            == 1
        )
        assert (
            client.post(
                url, headers=H, json={**handover, "summary": "Different"}
            ).status_code
            == 409
        )


def test_tombstone_and_main_reassignment_block_handover(tmp_path):
    app, codex, body, _ = fixture(tmp_path)
    with TestClient(app) as client:
        login(client)
        select(client)
        connect(client)
        row = register(client, body)
        app.state.store.set("backlog:deleted:task", {"restoredAt": None})
        result = client.post(
            f"/api/coding/main/children/{row['id']}/handover",
            headers=H,
            json={"expectedRevision": 1, "summary": "Review"},
        )
        assert result.status_code == 409 and "deleted" in result.text
        app.state.store.set("backlog:deleted:task", {"restoredAt": 1})
        select(client, "other-main", 1)
        result = client.post(
            f"/api/coding/main/children/{row['id']}/handover",
            headers=H,
            json={"expectedRevision": 1, "summary": "Review"},
        )
        assert result.status_code == 409 and "Main changed" in result.text
        assert not any(m in {"turn/start", "turn/steer"} for m, _ in codex.calls)


def test_assignment_revoked_during_main_source_validation_is_not_dispatched(tmp_path):
    app, codex, body, _ = fixture(tmp_path)
    with TestClient(app) as client:
        login(client)
        select(client)
        connect(client)
        row = register(client, body)
        original = codex.request

        async def revoke(method, params, *, expected_generation=None):
            if method == "thread/read" and params.get("threadId") == "child":
                app.state.store.set("backlog:deleted:task", {"restoredAt": None})
            return await original(
                method, params, expected_generation=expected_generation
            )

        codex.request = revoke
        result = client.post(
            f"/api/coding/main/children/{row['id']}/handover",
            headers=H,
            json={"expectedRevision": 1, "summary": "Review bounded work"},
        )
        assert result.status_code == 200, result.text
        receipt = result.json()["handover"]["delivery"]
        assert receipt["state"] == "not_recorded" and "deleted" in receipt["error"]
        assert not any(m in {"turn/start", "turn/steer"} for m, _ in codex.calls)


def test_wrong_exact_turn_and_unrecorded_assignment_fail_without_registration(tmp_path):
    app, _codex, body, _ = fixture(tmp_path)
    with TestClient(app) as client:
        login(client)
        select(client)
        for changed in (
            {"childTurnId": "different-turn"},
            {"assignmentId": str(uuid4())},
            {"backlogRevision": 99},
        ):
            assert (
                client.post(
                    "/api/coding/main/children", headers=H, json={**body, **changed}
                ).status_code
                == 409
            )
        assert client.get("/api/coding/main/children").json()["items"] == []
