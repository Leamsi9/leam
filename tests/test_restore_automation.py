import asyncio
import time
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from test_api import FakeCodex, login
from test_push import subscription
from leam_api.app import create_app
from leam_api.backups import Backups, restore
from leam_api.push import Push
from leam_api.store import Store

H = {"origin": "http://testserver"}


def test_restored_pending_push_never_replays_after_explicit_future_resume(tmp_path):
    original = tmp_path / "original"
    app = create_app(
        original,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        push_transport=httpx.MockTransport(lambda request: httpx.Response(503)),
    )
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(201)

    with TestClient(app) as c:
        login(c)
        c.put(
            "/api/push/config", json={"contact": "mailto:qa@example.com"}, headers=H
        ).raise_for_status()
        sub, _, _ = subscription()
        device = c.post(
            "/api/push/devices", json={"name": "QA", "subscription": sub}, headers=H
        ).json()
        c.post(
            "/api/push/devices/" + device["id"] + "/test", json={}, headers=H
        ).raise_for_status()
        manager = Backups(app.state.store)
        archive = manager.path(manager.create()["id"])
        # Separate physical worker, same durable current state; external receipt after S.
        worker = Push(app.state.store, transport=httpx.MockTransport(provider))
        asyncio.run(worker.tick())
        asyncio.run(worker.close())
        assert len(calls) == 1
    restored = tmp_path / "restored"
    restore(archive, restored, original)
    restored_app = create_app(
        restored,
        {"http://testserver"},
        codex=FakeCodex(),
        push_transport=httpx.MockTransport(provider),
    )
    with TestClient(restored_app) as c:
        c.post(
            "/api/auth/login", json={"password": "long-password-for-tests"}, headers=H
        ).raise_for_status()
        c.portal.call(restored_app.state.push.tick)
        assert (
            len(calls) == 1
        ), "A restored pending attempt replayed an external delivery"
        state = c.get("/api/automation/restore").json()
        assert state["held"] is True
        for path in (
            "/api/notifications/status",
            "/api/routines/status",
            "/api/push/status",
        ):
            observed = c.get(path).json()
            assert (
                observed["automationHeld"] is True
                and observed["automationInvalid"] is False
            )
        review = c.post("/api/automation/restore/preview", json={}, headers=H).json()
        assert review["pendingPushes"] == 1
        request = {
            "requestId": str(uuid4()),
            "previewToken": review["previewToken"],
            "cutoff": review["cutoff"],
            "confirmed": True,
        }
        done = c.post("/api/automation/restore/resume", json=request, headers=H)
        assert done.status_code == 200, done.text
        assert done.json()["held"] is False
        for path in (
            "/api/notifications/status",
            "/api/routines/status",
            "/api/push/status",
        ):
            assert c.get(path).json()["automationHeld"] is False
        assert (
            c.post("/api/automation/restore/resume", json=request, headers=H).json()
            == done.json()
        )
        c.portal.call(restored_app.state.push.tick)
        assert len(calls) == 1
        with restored_app.state.store.connect() as db:
            db.execute(
                "UPDATE push_deliveries SET created=created-31 WHERE kind='test'"
            )
        c.post(
            "/api/push/devices/" + device["id"] + "/test", json={}, headers=H
        ).raise_for_status()
        c.portal.call(restored_app.state.push.tick)
        assert len(calls) == 2


def controlled_app(tmp_path, clock):
    return create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        scheduler_clock=lambda: clock[0],
        push_transport=httpx.MockTransport(lambda request: httpx.Response(201)),
    )


def test_hold_resume_skips_absent_past_reminders_and_advances_routines_but_delivers_future(
    tmp_path,
):
    from datetime import datetime
    from leam_api.restore_automation import KEY, hold_marker

    clock = [datetime.fromisoformat("2026-09-21T08:59:00+00:00").timestamp()]
    app = controlled_app(tmp_path, clock)
    app.state.store.set(KEY, hold_marker(now=clock[0]))
    with TestClient(app) as c:
        login(c)
        for title, hour in [("Past", "09:00"), ("Future", "10:00")]:
            c.post(
                "/api/commitments",
                json={
                    "title": title,
                    "kind": "habit",
                    "reminderTime": hour,
                    "timezone": "UTC",
                },
                headers=H,
            ).raise_for_status()
        routine = c.post(
            "/api/routines",
            json={
                "title": "Daily",
                "message": "Check in",
                "time": "09:00",
                "timezone": "UTC",
            },
            headers=H,
        ).json()
        future = c.post(
            "/api/routines",
            json={
                "title": "Later",
                "message": "Later check",
                "time": "10:00",
                "timezone": "UTC",
            },
            headers=H,
        ).json()
        clock[0] += 121
        assert (
            c.post("/api/notifications/check", json={}, headers=H).json()[
                "automationHeld"
            ]
            is True
        )
        assert (
            c.post("/api/routines/check", json={}, headers=H).json()["automationHeld"]
            is True
        )
        assert (
            c.post(
                "/api/routines/" + routine["id"] + "/run",
                json={"revision": 1, "requestId": str(uuid4())},
                headers=H,
            ).status_code
            == 409
        )
        review = c.post("/api/automation/restore/preview", json={}, headers=H).json()
        assert review["overdueRoutines"] == 1
        request = {
            "requestId": str(uuid4()),
            "previewToken": review["previewToken"],
            "cutoff": review["cutoff"],
            "confirmed": True,
        }
        assert (
            c.post(
                "/api/automation/restore/resume", json=request, headers=H
            ).status_code
            == 200
        )
        c.post("/api/notifications/check", json={}, headers=H).raise_for_status()
        assert c.get("/api/notifications").json()["items"] == []
        with app.state.store.connect() as db:
            rows = db.execute(
                "SELECT state,due FROM reminder_jobs ORDER BY due"
            ).fetchall()
            assert [row["state"] for row in rows] == ["expired", "scheduled"]
        assert (
            c.post("/api/routines/check", json={}, headers=H).json()["delivered"] == 0
        )
        clock[0] += 3600
        assert (
            c.post("/api/notifications/check", json={}, headers=H).json()["newlyReady"]
            == 1
        )
        assert c.get("/api/notifications").json()["items"][0]["title"] == "Future"
        assert (
            c.post("/api/routines/check", json={}, headers=H).json()["delivered"] == 1
        )
        assert (
            c.get("/api/routines/runs").json()["items"][0]["routineId"] == future["id"]
        )
        # Explicitly editing the skipped reminder to a future time remains useful.
        past = next(
            item
            for item in c.get("/api/commitments").json()["items"]
            if item["title"] == "Past"
        )
        c.patch(
            "/api/commitments/" + past["id"],
            json={"revision": past["revision"], "reminderTime": "11:00"},
            headers=H,
        ).raise_for_status()
        assert (
            c.post("/api/notifications/check", json={}, headers=H).json()["newlyReady"]
            == 0
        )
        clock[0] += 3600
        assert (
            c.post("/api/notifications/check", json={}, headers=H).json()["newlyReady"]
            == 1
        )
        # Restart keeps the cut-off; no old jobs are reconstructed as ready.
        from leam_api.reminders import Scheduler

        assert (
            Scheduler(Store(tmp_path), clock=lambda: clock[0]).tick()["newlyReady"] == 0
        )


def test_resume_is_atomic_two_callers_stale_review_and_generation_bound(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from leam_api.restore_automation import KEY, hold_marker

    clock = [time.time()]
    app = controlled_app(tmp_path, clock)
    app.state.store.set(KEY, hold_marker(now=clock[0]))
    with TestClient(app) as c:
        assert c.get("/api/automation/restore").status_code == 401
        login(c)
        assert c.post("/api/automation/restore/preview", json={}).status_code == 403
        review = c.post("/api/automation/restore/preview", json={}, headers=H).json()
        request = {
            "requestId": str(uuid4()),
            "previewToken": review["previewToken"],
            "cutoff": review["cutoff"],
            "confirmed": True,
        }
        assert (
            c.post(
                "/api/automation/restore/resume",
                json={**request, "confirmed": False},
                headers=H,
            ).status_code
            == 409
        )
        c.post(
            "/api/commitments",
            json={
                "title": "New schedule",
                "kind": "habit",
                "reminderTime": "20:00",
                "timezone": "UTC",
            },
            headers=H,
        ).raise_for_status()
        assert (
            c.post(
                "/api/automation/restore/resume", json=request, headers=H
            ).status_code
            == 409
        )
        review = c.post("/api/automation/restore/preview", json={}, headers=H).json()
        request.update(previewToken=review["previewToken"], cutoff=review["cutoff"])
        second = {**request, "requestId": str(uuid4())}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda body: c.post(
                        "/api/automation/restore/resume", json=body, headers=H
                    ),
                    [request, second],
                )
            )
        assert sorted(r.status_code for r in results) == [200, 409]
        winner = request if results[0].status_code == 200 else second
        expected = next(r.json() for r in results if r.status_code == 200)
        assert (
            c.post("/api/automation/restore/resume", json=winner, headers=H).json()
            == expected
        )
        app.state.store.set(KEY, hold_marker(now=clock[0]))
        assert (
            c.post("/api/automation/restore/resume", json=winner, headers=H).status_code
            == 409
        )
        assert c.get("/api/automation/restore").json()["held"] is True


def test_malformed_hold_fails_closed_through_worker_and_actual_routes(tmp_path):
    from leam_api.restore_automation import KEY

    clock = [time.time()]
    app = controlled_app(tmp_path, clock)
    with app.state.store.connect() as db:
        db.execute("INSERT INTO settings VALUES (?,?)", (KEY, "not-json"))
    with TestClient(app) as c:
        login(c)
        assert c.get("/api/automation/restore").json()["invalid"] is True
        for path in (
            "/api/notifications/status",
            "/api/routines/status",
            "/api/push/status",
        ):
            assert c.get(path).json()["automationInvalid"] is True
        assert (
            c.post("/api/automation/restore/preview", json={}, headers=H).status_code
            == 409
        )
        assert (
            c.post("/api/notifications/check", json={}, headers=H).json()[
                "automationHeld"
            ]
            is True
        )
        assert (
            c.post("/api/routines/check", json={}, headers=H).json()["automationHeld"]
            is True
        )
        app.state.store.set("push_contact", "mailto:qa@example.com")
        c.portal.call(app.state.push.tick)
        with app.state.store.connect() as db:
            assert db.execute("SELECT count(*) FROM push_deliveries").fetchone()[0] == 0


def test_crossing_new_due_time_requires_updated_preview_and_confirmation_cutoff(
    tmp_path,
):
    from datetime import datetime
    from leam_api.restore_automation import KEY, hold_marker

    clock = [datetime.fromisoformat("2026-09-21T08:59:00+00:00").timestamp()]
    app = controlled_app(tmp_path, clock)
    app.state.store.set(KEY, hold_marker(now=clock[0]))
    with TestClient(app) as c:
        login(c)
        c.post(
            "/api/routines",
            json={
                "title": "Soon",
                "message": "Check",
                "time": "09:00",
                "timezone": "UTC",
            },
            headers=H,
        ).raise_for_status()
        review = c.post("/api/automation/restore/preview", json={}, headers=H).json()
        request = {
            "requestId": str(uuid4()),
            "previewToken": review["previewToken"],
            "cutoff": review["cutoff"],
            "confirmed": True,
        }
        clock[0] += 61
        assert (
            c.post(
                "/api/automation/restore/resume", json=request, headers=H
            ).status_code
            == 409
        )
        review = c.post("/api/automation/restore/preview", json={}, headers=H).json()
        request.update(previewToken=review["previewToken"], cutoff=review["cutoff"])
        clock[0] += 2
        result = c.post("/api/automation/restore/resume", json=request, headers=H)
        assert result.status_code == 200, result.text
        assert result.json()["cutoff"] == clock[0]
        assert app.state.store.get(KEY)["cutoff"] == clock[0]
