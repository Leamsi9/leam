from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from leam_api.routine_events import EventInputs, router
from leam_api.store import Store

NOW = 1790000000.0


def setup(path, clock=None):
    domain = EventInputs(Store(path), clock=lambda: (clock or [NOW])[0])
    app = FastAPI()
    app.include_router(router(domain))
    return domain, TestClient(app)


def rule(client, **changes):
    body = dict(
        title="Take a break",
        message="Time for a short walk",
        source="calendar",
        eventType="meeting.ended",
        cooldownSeconds=60,
        **changes,
    )
    result = client.post("/api/routine-event-rules", json=body)
    assert result.status_code == 200, result.text
    return result.json()


def event(**changes):
    return dict(
        version=1,
        requestId=str(uuid4()),
        source="calendar",
        type="meeting.ended",
        occurredAt=datetime.fromtimestamp(NOW, UTC).isoformat(),
        attributes={"place": "home"},
        **changes,
    )


def test_actual_route_delivery_atomic_replay_restart_and_conflict(tmp_path):
    _, client = setup(tmp_path)
    saved = rule(client)
    payload = event()
    first = client.post("/api/routine-events", json=payload)
    assert first.status_code == 200, first.text
    assert len(first.json()["deliveries"]) == 1
    _, client = setup(tmp_path)
    assert client.post("/api/routine-events", json=payload).json() == first.json()
    assert (
        client.post(
            "/api/routine-events", json={**payload, "type": "changed"}
        ).status_code
        == 409
    )
    notes = client.get("/api/routine-event-notifications").json()["items"]
    assert len(notes) == 1 and notes[0]["ruleId"] == saved["id"]
    assert notes[0]["message"] == "Time for a short walk"
    assert (
        client.post(
            "/api/routine-event-notifications/" + notes[0]["id"] + "/dismiss", json={}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/routine-event-notifications/" + notes[0]["id"] + "/dismiss", json={}
        ).status_code
        == 200
    )
    assert client.get("/api/routine-event-notifications").json()["items"] == []
    stored = client.get("/api/routine-events").json()["items"][0]
    assert (
        stored["provenance"]["channel"] == "owner-session"
        and stored["receivedAt"] == NOW
    )
    assert stored["attributes"] == {"place": "home"}


def test_exact_predicate_cooldown_pause_and_revision(tmp_path):
    clock = [NOW]
    _, client = setup(tmp_path, clock)
    saved = rule(client, match={"field": "atHome", "equals": True})
    assert (
        client.post(
            "/api/routine-events", json={**event(), "attributes": {"atHome": 1}}
        ).json()["deliveries"]
        == []
    )
    body = {**event(), "attributes": {"atHome": True}}
    assert len(client.post("/api/routine-events", json=body).json()["deliveries"]) == 1
    assert (
        client.post(
            "/api/routine-events", json={**body, "requestId": str(uuid4())}
        ).json()["suppressed"][0]["reason"]
        == "cooldown"
    )
    edit = {k: v for k, v in saved.items() if k not in ["id", "lastDeliveredAt"]}
    edit["enabled"] = False
    assert (
        client.put("/api/routine-event-rules/" + saved["id"], json=edit).status_code
        == 200
    )
    assert (
        client.put("/api/routine-event-rules/" + saved["id"], json=edit).status_code
        == 409
    )
    clock[0] += 61
    assert (
        client.post(
            "/api/routine-events", json={**body, "requestId": str(uuid4())}
        ).json()["deliveries"]
        == []
    )


def test_bounds_no_nested_data_no_unknown_actions_and_stale_timestamps(tmp_path):
    _, client = setup(tmp_path)
    for changes in [
        {"version": True},
        {"version": 2},
        {"attributes": {"nested": {"a": 1}}},
        {"attributes": {str(i): i for i in range(17)}},
        {"attributes": {"x": "x" * 257}},
        {"occurredAt": "2026-09-20T12:00:00"},
        {"occurredAt": "2000-01-01T00:00:00Z"},
        {"shell": "bad"},
        {"source": "../shell"},
    ]:
        assert (
            client.post("/api/routine-events", json={**event(), **changes}).status_code
            == 422
        ), changes
    assert (
        client.post(
            "/api/routine-event-rules",
            json={
                "title": "X",
                "message": "Y",
                "source": "a",
                "eventType": "b",
                "action": "shell",
            },
        ).status_code
        == 422
    )
    assert client.get("/api/routine-events/capabilities").json()["actions"] == [
        "notification"
    ]


def test_concurrent_same_input_and_cross_domain_identity_do_not_duplicate_or_overwrite(
    tmp_path,
):
    domain, client = setup(tmp_path)
    rule(client)
    body = event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(
            pool.map(lambda _: client.post("/api/routine-events", json=body), range(2))
        )
    assert all(r.status_code == 200 for r in replies)
    assert replies[0].json() == replies[1].json()
    assert client.get("/api/routine-event-notifications").json()["total"] == 1
    commitment = domain.store.create("commitment", {"title": "Preserve me"})
    bad = {**event(), "requestId": commitment["id"]}
    assert client.post("/api/routine-events", json=bad).status_code == 409
    assert domain.store.entities("commitment")[0]["title"] == "Preserve me"
    assert client.get("/api/routine-events").json()["total"] == 1


def test_rate_limit_preserves_existing_receipt_and_rejected_input_can_retry(tmp_path):
    clock = [NOW]
    _, client = setup(tmp_path, clock)
    first = event()
    receipt = client.post("/api/routine-events", json=first).json()
    for _ in range(59):
        assert client.post("/api/routine-events", json=event()).status_code == 200
    pending = event()
    assert client.post("/api/routine-events", json=pending).status_code == 429
    assert client.post("/api/routine-events", json=first).json() == receipt
    clock[0] += 61
    assert client.post("/api/routine-events", json=pending).status_code == 200


def test_actual_app_session_origin_and_revocation(tmp_path):
    from test_api import FakeCodex, login

    from leam_api.app import create_app

    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        scheduler_clock=lambda: NOW,
    )
    with TestClient(app) as client:
        assert client.get("/api/routine-events").status_code == 401
        assert (
            client.post(
                "/api/routine-events",
                json=event(),
                headers={"origin": "http://testserver"},
            ).status_code
            == 401
        )
        login(client)
        assert client.post("/api/routine-events", json=event()).status_code == 403
        assert (
            client.post(
                "/api/routine-events",
                json=event(),
                headers={"origin": "https://other.invalid"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/routine-events",
                json=event(),
                headers={"origin": "http://testserver"},
            ).status_code
            == 200
        )
        client.post("/api/auth/logout", headers={"origin": "http://testserver"})
        assert client.get("/api/routine-events").status_code == 401


def test_backup_restore_retains_rules_notifications_and_idempotency(tmp_path):
    from leam_api.backups import Backups, restore
    from leam_api.vault import Vault

    source = tmp_path / "source"
    domain, client = setup(source)
    Vault(source)
    saved = rule(client)
    body = event()
    receipt = client.post("/api/routine-events", json=body).json()
    manager = Backups(domain.store)
    archive = manager.path(manager.create()["id"])
    restored = tmp_path / "restored"
    restore(archive, restored, source)
    _, again = setup(restored)
    assert again.get("/api/routine-event-rules").json()["items"][0]["id"] == saved["id"]
    assert again.post("/api/routine-events", json=body).json() == receipt
    assert again.get("/api/routine-event-notifications").json()["total"] == 1


def test_notification_capacity_rolls_back_all_partial_work(tmp_path):
    from leam_api.routine_events import NOTE

    domain, client = setup(tmp_path)
    rule(client)
    rule(client)
    with domain.store.connect() as db:
        for index in range(999):
            domain.put(db, str(uuid4()), NOTE, 1, {"dismissedAt": None}, NOW - index)
    response = client.post("/api/routine-events", json=event())
    assert response.status_code == 409
    assert client.get("/api/routine-event-notifications").json()["total"] == 999
    assert client.get("/api/routine-events").json()["total"] == 0
    assert all(
        item["lastDeliveredAt"] is None
        for item in client.get("/api/routine-event-rules").json()["items"]
    )


def test_recent_event_retention_preserves_idempotent_receipts(tmp_path):
    from leam_api.routine_events import EVENT

    domain, client = setup(tmp_path)
    body = event()
    accepted = client.post("/api/routine-events", json=body).json()
    with domain.store.connect() as db:
        for index in range(1000):
            # Older fixture events avoid the separate ingress rate-limit boundary.
            domain.put(db, str(uuid4()), EVENT, 1, {"version": 1}, NOW - 120 - index)
    assert client.post("/api/routine-events", json=event()).status_code == 200
    assert client.get("/api/routine-events").json()["total"] == 1000
    assert client.post("/api/routine-events", json=body).json() == accepted
