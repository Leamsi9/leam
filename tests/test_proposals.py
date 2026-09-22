from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app

H = {"origin": "http://testserver"}


def test_companion_proposal_requires_confirmation_and_commits_once(tmp_path):
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    with TestClient(app) as c:
        login(c)
        body = {
            "requestId": "4c79d325-adf1-41f2-beee-53e0778f314d",
            "threadId": "companion-thread",
            "operation": "commitment.create",
            "input": {
                "title": "Practice French",
                "kind": "habit",
                "measure": "minutes",
                "target": 20,
            },
            "reason": "Make time for the practice you requested",
        }
        proposal = c.post("/api/proposals", json=body, headers=H)
        assert proposal.status_code == 200, proposal.text
        assert c.get("/api/commitments").json()["items"] == []
        assert (
            c.post("/api/proposals", json=body, headers=H).json()["id"]
            == proposal.json()["id"]
        )
        key = proposal.json()["id"]
        accepted = c.post("/api/proposals/" + key + "/approve", json={}, headers=H)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["state"] == "complete"
        repeated = c.post("/api/proposals/" + key + "/approve", json={}, headers=H)
        assert repeated.json() == accepted.json()
        assert len(c.get("/api/commitments").json()["items"]) == 1
        assert (
            c.post(
                "/api/proposals",
                json={**body, "input": {"title": "Different"}},
                headers=H,
            ).status_code
            == 409
        )
        assert (
            c.post("/api/proposals/" + key + "/decline", json={}, headers=H).status_code
            == 409
        )
        declined = c.post(
            "/api/proposals",
            json={**body, "requestId": "3e83f123-d5ef-4ca1-aa93-754295596f75"},
            headers=H,
        ).json()
        assert (
            c.post(
                "/api/proposals/" + declined["id"] + "/decline", json={}, headers=H
            ).status_code
            == 200
        )
        assert (
            c.post(
                "/api/proposals/" + declined["id"] + "/approve", json={}, headers=H
            ).status_code
            == 409
        )
        assert len(c.get("/api/commitments").json()["items"]) == 1


def test_proposal_receipt_failure_rolls_back_domain_effect(tmp_path, monkeypatch):
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        login(c)
        proposal = c.post(
            "/api/proposals",
            json={
                "requestId": "b1e9ed2a-ff04-46d2-b748-997c1356b149",
                "threadId": "thread",
                "operation": "commitment.create",
                "input": {"title": "Atomic effect"},
                "reason": "Test rollback",
            },
            headers=H,
        )
        assert proposal.status_code == 200, proposal.text
        service = app.state.proposals
        original = service.record

        def broken(db, key, result, *, mark_read=False):
            assert db is not None and key == proposal.json()["id"]
            assert result and mark_read is True
            raise RuntimeError("simulated receipt failure")

        monkeypatch.setattr(service, "record", broken)
        assert (
            c.post(
                "/api/proposals/" + proposal.json()["id"] + "/approve",
                json={},
                headers=H,
            ).status_code
            == 500
        )
        assert c.get("/api/commitments").json()["items"] == []
        monkeypatch.setattr(service, "record", original)
        assert (
            c.post(
                "/api/proposals/" + proposal.json()["id"] + "/approve",
                json={},
                headers=H,
            ).status_code
            == 200
        )
        assert len(c.get("/api/commitments").json()["items"]) == 1


def test_proposed_edit_preserves_omitted_fields_and_detects_a_stale_approval(tmp_path):
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    with TestClient(app) as c:
        login(c)
        item = c.post(
            "/api/commitments",
            json={
                "title": "Original",
                "notes": "Preserve me",
                "kind": "habit",
                "target": 20,
                "measure": "minutes",
            },
            headers=H,
        ).json()
        body = {
            "requestId": "db85e17a-153c-4519-b07c-1fda2a5d9c57",
            "threadId": "thread",
            "operation": "commitment.edit",
            "input": {"id": item["id"], "revision": 1, "title": "New title"},
            "reason": "Requested edit",
        }
        proposal = c.post("/api/proposals", json=body, headers=H).json()
        accepted = c.post(
            "/api/proposals/" + proposal["id"] + "/approve", json={}, headers=H
        )
        assert accepted.status_code == 200, accepted.text
        result = accepted.json()["result"]
        assert result["notes"] == "Preserve me"
        assert result["kind"] == "habit" and result["target"] == 20
        assert result["title"] == "New title"
        other = c.post(
            "/api/proposals",
            json={
                **body,
                "requestId": "9b3f6c71-4b42-47d8-8c9b-d4b2090aab2c",
                "input": {**body["input"], "revision": 2},
            },
            headers=H,
        ).json()
        c.patch(
            "/api/commitments/" + item["id"],
            json={"revision": 2, "title": "Edited on phone"},
            headers=H,
        )
        assert (
            c.post(
                "/api/proposals/" + other["id"] + "/approve", json={}, headers=H
            ).status_code
            == 409
        )
        assert (
            c.get("/api/commitments").json()["items"][0]["title"] == "Edited on phone"
        )


def test_calendar_proposal_keeps_approved_retry_after_account_reconnect_error(
    tmp_path, monkeypatch
):
    from fastapi import HTTPException

    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    service = app.state.proposals
    calls = []
    monkeypatch.setattr(
        service.calendar_actions,
        "preview",
        lambda body: {"digest": "a" * 64, "title": "Reviewed title"},
    )
    monkeypatch.setattr(service.calendar_actions, "get", lambda key: {"result": None})

    async def external(body):
        calls.append(body.model_dump(mode="json"))
        if len(calls) == 1:
            raise HTTPException(502, "Response lost")
        if len(calls) == 2:
            raise HTTPException(409, "Reconnect account")
        return {"event": {"title": "Reviewed title"}}

    monkeypatch.setattr(service.calendar_actions, "create", external)
    with TestClient(app) as c:
        login(c)
        body = {
            "requestId": "c7d46b38-8158-4b11-9bfa-9138c3cdca69",
            "threadId": "thread",
            "operation": "calendar.create",
            "input": {
                "calendarId": "c",
                "commitmentId": "task",
                "commitmentRevision": 1,
                "date": "2026-09-20",
                "time": "10:00",
                "timezone": "Europe/London",
                "minutes": 30,
            },
            "reason": "Schedule it",
        }
        key = c.post("/api/proposals", json=body, headers=H).json()["id"]
        assert (
            c.post("/api/proposals/" + key + "/approve", json={}, headers=H).status_code
            == 502
        )
        assert (
            c.post("/api/proposals/" + key + "/approve", json={}, headers=H).status_code
            == 409
        )
        saved = c.get("/api/proposals?threadId=thread").json()["items"][0]
        assert saved["state"] == "executing"
        assert (
            c.post("/api/proposals/" + key + "/decline", json={}, headers=H).status_code
            == 409
        )
        assert (
            c.post("/api/proposals/" + key + "/approve", json={}, headers=H).json()[
                "state"
            ]
            == "complete"
        )
        assert calls[0] == calls[1] == calls[2]


def test_capacity_and_memory_proposals_never_erase_unreviewed_fields(tmp_path):
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    with TestClient(app) as c:
        login(c)
        capacity = c.post(
            "/api/capacities",
            json={"name": "Health", "note": "Keep note", "record": "Keep record"},
            headers=H,
        ).json()
        memory = c.post(
            "/api/memory",
            json={"text": "Original", "source": "user", "category": "identity"},
            headers=H,
        ).json()
        for key, operation, fields in [
            (
                "757c1f52-0c85-46cd-90b9-9eece9ed2a61",
                "capacity.edit",
                {"id": capacity["id"], "revision": 1, "name": "Wellbeing"},
            ),
            (
                "1555e042-c7de-4281-bcdf-f86818086b57",
                "memory.edit",
                {
                    "id": memory["id"],
                    "revision": 1,
                    "text": "Corrected",
                    "source": "user",
                },
            ),
        ]:
            proposal = c.post(
                "/api/proposals",
                json={
                    "requestId": key,
                    "threadId": "thread",
                    "operation": operation,
                    "input": fields,
                    "reason": "User correction",
                },
                headers=H,
            )
            assert proposal.status_code == 200, proposal.text
            accepted = c.post("/api/proposals/" + key + "/approve", json={}, headers=H)
            assert accepted.status_code == 200, accepted.text
        current = c.get("/api/capacities").json()["items"][0]
        assert (
            current["name"] == "Wellbeing"
            and current["note"] == "Keep note"
            and current["record"] == "Keep record"
        )
        current = c.get("/api/memory").json()["items"][0]
        assert current["text"] == "Corrected" and current["category"] == "identity"


def test_invalid_calendar_proposals_fail_validation_before_persistence(tmp_path):
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        login(c)
        base = {
            "requestId": "411c2a74-5809-4cb1-b17a-dc9a9d5f2337",
            "threadId": "thread",
            "reason": "Invalid proposal",
        }
        version = {
            "creationId": "2348c58d-0439-44c5-8263-e39cf4b7dc70",
            "expectedEtag": '"v1"',
        }
        for operation, data in [
            ("calendar.edit", version),
            ("calendar.remove", {**version, "edit": {}}),
            ("calendar.remove", {**version, "expectedEtag": "invalid\r\nsecret-body"}),
        ]:
            response = c.post(
                "/api/proposals",
                json={**base, "operation": operation, "input": data},
                headers=H,
            )
            assert response.status_code == 422, response.text
            assert "secret-body" not in response.text
        assert c.get("/api/proposals?threadId=thread").json()["items"] == []


def test_proposed_progress_has_reviewed_day_and_atomic_replayable_result(tmp_path):
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    with TestClient(app) as c:
        login(c)
        habit = c.post(
            "/api/commitments",
            json={
                "title": "Practice",
                "kind": "habit",
                "measure": "minutes",
                "target": 20,
            },
            headers=H,
        ).json()
        c.put(
            "/api/commitments/" + habit["id"] + "/progress/2026-09-20",
            json={
                "revision": 0,
                "commitmentRevision": 1,
                "operation": "set",
                "value": 7,
            },
            headers=H,
        )
        body = {
            "requestId": "5a084dd9-0f03-431c-844c-50c2237ebdc0",
            "threadId": "thread",
            "reason": "You said you finished",
            "operation": "commitment.progress",
            "input": {
                "id": habit["id"],
                "date": "2026-09-20",
                "revision": 1,
                "commitmentRevision": 1,
                "operation": "complete",
            },
        }
        proposal = c.post("/api/proposals", json=body, headers=H)
        assert proposal.status_code == 200, proposal.text
        assert proposal.json()["review"]["before"]["value"] == 7
        assert proposal.json()["review"]["after"]["action"] == "Mark complete"
        result = c.post(
            "/api/proposals/" + body["requestId"] + "/approve", json={}, headers=H
        )
        assert result.status_code == 200, result.text
        assert result.json()["result"]["log"]["done"] is True
        assert result.json()["result"]["log"]["value"] == 7
        assert (
            c.post(
                "/api/proposals/" + body["requestId"] + "/approve", json={}, headers=H
            ).json()
            == result.json()
        )
        history = c.get("/api/commitments/" + habit["id"] + "/history").json()["items"][
            0
        ]
        assert history["revision"] == 2
        with app.state.store.connect() as db:
            assert (
                db.execute(
                    "SELECT count(*) FROM events WHERE topic='proposal.completed'"
                ).fetchone()[0]
                == 1
            )
