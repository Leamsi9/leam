from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from leam_api.routines import Routines, router
from leam_api.store import Store


def epoch(value):
    return datetime.fromisoformat(value).timestamp()


def service(tmp_path, clock):
    domain = Routines(Store(tmp_path), clock=lambda: clock[0])
    app = FastAPI()
    app.include_router(router(domain))
    return domain, TestClient(app)


def create(client, **changes):
    response = client.post(
        "/api/routines",
        json={
            "title": "Evening reflection",
            "message": "How did today go?",
            "time": "18:00",
            "timezone": "Europe/London",
            **changes,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_routes_restart_duplicate_execution_and_dismiss(tmp_path):
    clock = [epoch("2026-09-20T16:59:00+00:00")]
    domain, client = service(tmp_path, clock)
    item = create(client)
    assert client.get("/api/routines/notifications").json()["items"] == []
    clock[0] += 61
    domain, client = service(tmp_path, clock)
    assert client.post("/api/routines/check").json()["delivered"] == 1
    assert client.post("/api/routines/check").json()["delivered"] == 0
    notes = client.get("/api/routines/notifications").json()["items"]
    assert len(notes) == 1 and notes[0]["message"] == "How did today go?"
    receipt = client.post(
        f"/api/routines/{item['id']}/run",
        json={"revision": 1, "requestId": str(uuid4())},
    ).json()
    assert receipt["trigger"] == "manual"
    assert len(client.get("/api/routines/runs").json()["items"]) == 2
    assert (
        client.post(f"/api/routines/notifications/{notes[0]['id']}/dismiss").status_code
        == 200
    )
    assert (
        client.post(f"/api/routines/notifications/{notes[0]['id']}/dismiss").status_code
        == 200
    )
    assert len(client.get("/api/routines/notifications").json()["items"]) == 1
    with domain.store.connect() as db:
        events = db.execute(
            "SELECT payload FROM events WHERE topic='routine.notification.ready'"
        ).fetchall()
    assert len(events) == 2 and '"version": 1' in events[0]["payload"]


def test_manual_replay_concurrent_workers_and_changed_request(tmp_path):
    clock = [epoch("2026-09-20T16:00:00+00:00")]
    domain, client = service(tmp_path, clock)
    item = create(client)
    payload = {"revision": 1, "requestId": str(uuid4())}
    url = f"/api/routines/{item['id']}/run"
    a = client.post(url, json=payload)
    assert a.status_code == 200
    assert client.post(url, json=payload).json() == a.json()
    assert client.post(url, json={**payload, "revision": 2}).status_code == 409
    clock[0] = epoch("2026-09-20T17:01:00+00:00")
    second, _ = service(tmp_path, clock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda worker: worker.tick(), [domain, second]))
    assert sum(r["delivered"] for r in results) == 1
    assert len(client.get("/api/routines/runs").json()["items"]) == 2


def test_edits_pause_and_validation(tmp_path):
    clock = [epoch("2026-09-20T16:00:00+00:00")]
    domain, client = service(tmp_path, clock)
    for change in [
        {"timezone": "Bad/zone"},
        {"days": []},
        {"days": [7]},
        {"time": "25:00"},
        {"action": "shell"},
    ]:
        assert (
            client.post(
                "/api/routines",
                json={"title": "Bad", "message": "x", "time": "18:00", **change},
            ).status_code
            == 422
        )
    item = create(client)
    body = {
        k: item[k]
        for k in ["title", "message", "time", "timezone", "days", "enabled", "revision"]
    }
    changed = client.put(f"/api/routines/{item['id']}", json={**body, "enabled": False})
    assert changed.status_code == 200
    assert client.put(f"/api/routines/{item['id']}", json=body).status_code == 409
    clock[0] += 7200
    assert domain.tick()["delivered"] == 0
    assert client.get("/api/routines").json()["items"][0]["nextDueAt"] is None
    assert (
        client.post(
            f"/api/routines/{item['id']}/run",
            json={"revision": 2, "requestId": str(uuid4())},
        ).status_code
        == 200
    )


def test_dst_gap_and_fold_and_weekdays(tmp_path):
    clock = [epoch("2026-03-29T00:59:00+00:00")]
    domain, client = service(tmp_path, clock)
    item = create(client, time="01:30", days=[6])
    assert item["nextDueAt"] == epoch("2026-03-29T01:00:00+00:00")
    clock[0] = item["nextDueAt"]
    assert domain.tick()["delivered"] == 1
    clock[0] = epoch("2026-10-25T00:31:00+00:00")
    assert domain.tick()["delivered"] == 1
    clock[0] = epoch("2026-10-25T01:31:00+00:00")
    assert domain.tick()["delivered"] == 0
    assert client.get("/api/routines").json()["items"][0]["nextDueAt"] == epoch(
        "2026-11-01T01:30:00+00:00"
    )


def test_transaction_rollback_keeps_due_work_recoverable(tmp_path):
    clock = [epoch("2026-09-20T16:59:00+00:00")]
    domain, client = service(tmp_path, clock)
    create(client)
    with domain.store.connect() as db:
        db.execute(
            "CREATE TRIGGER fail_routine_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'simulate disk failure'); END"
        )
    clock[0] += 61
    assert client.post("/api/routines/check").status_code == 503
    assert client.get("/api/routines/runs").json()["items"] == []
    with domain.store.connect() as db:
        db.execute("DROP TRIGGER fail_routine_event")
    restarted, _ = service(tmp_path, clock)
    assert restarted.tick()["delivered"] == 1


def test_missed_weekly_run_expires_and_edits_do_not_replay_old_schedule(tmp_path):
    clock = [epoch("2026-09-20T16:59:00+00:00")]
    domain, client = service(tmp_path, clock)
    item = create(client, days=[6])
    clock[0] = epoch("2026-09-22T16:59:00+00:00")
    assert domain.tick()["delivered"] == 0
    assert client.get("/api/routines/runs").json()["items"][0]["state"] == "expired"
    assert client.get("/api/routines/notifications").json()["items"] == []
    body = {
        k: item[k]
        for k in ["title", "message", "time", "timezone", "days", "enabled", "revision"]
    }
    updated = client.put(
        f"/api/routines/{item['id']}", json={**body, "days": [1], "time": "18:30"}
    ).json()
    assert updated["nextDueAt"] == epoch("2026-09-22T17:30:00+00:00")
    assert domain.tick()["delivered"] == 0
    clock[0] += 31 * 60
    assert domain.tick()["delivered"] == 1
    history = client.get("/api/routines/runs?limit=1").json()
    assert len(history["items"]) == 1 and history["nextCursor"]
    second = client.get(
        f"/api/routines/runs?limit=1&before={history['nextCursor']}"
    ).json()
    assert len(second["items"]) == 1 and second["nextCursor"] is None
    assert second["items"][0]["id"] != history["items"][0]["id"]


def test_router_uses_existing_auth_origin_and_does_not_change_commitment_reminders(
    tmp_path,
):
    from test_api import FakeCodex, login

    from leam_api.app import create_app

    clock = [epoch("2026-09-20T16:59:00+00:00")]
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        scheduler_clock=lambda: clock[0],
    )
    domain = Routines(Store(tmp_path), clock=lambda: clock[0])
    app.include_router(router(domain))
    with TestClient(app) as client:
        assert client.get("/api/routines").status_code == 401
        login(client)
        payload = {"title": "Protected", "message": "Private nudge", "time": "18:00"}
        assert client.post("/api/routines", json=payload).status_code == 403
        response = client.post(
            "/api/routines", json=payload, headers={"origin": "http://testserver"}
        )
        assert response.status_code == 200
        clock[0] += 61
        response = client.post(
            "/api/routines/check", json={}, headers={"origin": "http://testserver"}
        )
        assert response.json()["delivered"] == 1
        assert client.get("/api/notifications").json()["items"] == []
        assert len(client.get("/api/routines/notifications").json()["items"]) == 1
