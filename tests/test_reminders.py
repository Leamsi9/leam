from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app


def epoch(value):
    return datetime.fromisoformat(value).timestamp()


def test_reminders_survive_restart_snooze_once_and_complete(tmp_path):
    clock = [epoch("2026-09-20T08:29:00+00:00")]

    def app():
        return create_app(
            tmp_path,
            {"http://testserver"},
            bootstrap="bootstrap-for-tests",
            codex=FakeCodex(),
            scheduler_clock=lambda: clock[0],
        )

    h = {"origin": "http://testserver"}
    with TestClient(app()) as client:
        login(client)
        item = client.post(
            "/api/commitments",
            json={
                "title": "Morning walk",
                "kind": "habit",
                "reminderTime": "09:30",
                "timezone": "Europe/London",
            },
            headers=h,
        ).json()
        assert (
            client.post("/api/notifications/check", json={}, headers=h).status_code
            == 200
        )
        assert client.get("/api/notifications").json()["items"] == []
    clock[0] = epoch("2026-09-20T08:31:00+00:00")
    with TestClient(app()) as client:
        client.post(
            "/api/auth/login", json={"password": "long-password-for-tests"}, headers=h
        )
        client.post("/api/notifications/check", json={}, headers=h)
        notifications = client.get("/api/notifications").json()["items"]
        assert len(notifications) == 1
        notification = notifications[0]
        for _ in range(2):
            client.post("/api/notifications/check", json={}, headers=h)
        assert len(client.get("/api/notifications").json()["items"]) == 1
        snoozed = client.post(
            "/api/notifications/" + notification["id"] + "/snooze",
            json={"revision": notification["revision"], "minutes": 10},
            headers=h,
        )
        assert snoozed.status_code == 200, snoozed.text
        assert client.get("/api/notifications").json()["items"] == []
        assert (
            client.post(
                "/api/notifications/" + notification["id"] + "/snooze",
                json={"revision": notification["revision"], "minutes": 10},
                headers=h,
            ).status_code
            == 409
        )
        clock[0] += 601
        client.post("/api/notifications/check", json={}, headers=h)
        assert len(client.get("/api/notifications").json()["items"]) == 1
        client.put(
            "/api/commitments/" + item["id"] + "/progress/2026-09-20",
            json={"revision": 0, "commitmentRevision": 1, "operation": "toggle"},
            headers=h,
        )
        assert client.get("/api/notifications").json()["items"] == []


def test_dst_gap_shifts_forward_and_fold_only_delivers_once(tmp_path):
    clock = [epoch("2026-03-29T00:59:00+00:00")]
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        scheduler_clock=lambda: clock[0],
    )
    h = {"origin": "http://testserver"}
    with TestClient(app) as client:
        login(client)
        client.post(
            "/api/commitments",
            json={
                "title": "DST habit",
                "kind": "habit",
                "reminderTime": "01:30",
                "timezone": "Europe/London",
            },
            headers=h,
        )
        client.post("/api/notifications/check", json={}, headers=h)
        assert client.get("/api/notifications").json()["items"] == []
        clock[0] = epoch("2026-03-29T01:00:00+00:00")
        client.post("/api/notifications/check", json={}, headers=h)
        item = client.get("/api/notifications").json()["items"][0]
        assert item["dueAt"] == clock[0]
        client.post(
            "/api/notifications/" + item["id"] + "/dismiss",
            json={"revision": item["revision"]},
            headers=h,
        )
        clock[0] = epoch("2026-10-25T00:31:00+00:00")
        client.post("/api/notifications/check", json={}, headers=h)
        item = client.get("/api/notifications").json()["items"][0]
        client.post(
            "/api/notifications/" + item["id"] + "/dismiss",
            json={"revision": item["revision"]},
            headers=h,
        )
        clock[0] = epoch("2026-10-25T01:31:00+00:00")
        client.post("/api/notifications/check", json={}, headers=h)
        assert client.get("/api/notifications").json()["items"] == []


def test_reminder_complete_does_not_reopen_on_duplicate_and_resume_after_pause(
    tmp_path,
):
    def clock():
        return epoch("2026-09-20T09:00:00+00:00")

    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        scheduler_clock=clock,
    )
    h = {"origin": "http://testserver"}
    with TestClient(app) as client:
        login(client)
        item = client.post(
            "/api/commitments",
            json={
                "title": "Walk",
                "kind": "habit",
                "reminderTime": "09:30",
                "timezone": "Europe/London",
            },
            headers=h,
        ).json()
        client.post("/api/notifications/check", json={}, headers=h)
        client.patch(
            "/api/commitments/" + item["id"],
            json={"revision": 1, "status": "paused"},
            headers=h,
        )
        client.post("/api/notifications/check", json={}, headers=h)
        assert client.get("/api/notifications").json()["items"] == []
        client.patch(
            "/api/commitments/" + item["id"],
            json={"revision": 2, "status": "active"},
            headers=h,
        )
        client.post("/api/notifications/check", json={}, headers=h)
        notice = client.get("/api/notifications").json()["items"][0]
        url = "/api/notifications/" + notice["id"] + "/complete"
        assert (
            client.post(
                url, json={"revision": notice["revision"]}, headers=h
            ).status_code
            == 200
        )
        assert (
            client.post(
                url, json={"revision": notice["revision"]}, headers=h
            ).status_code
            == 409
        )
        assert client.get("/api/today?date=2026-09-20").json()["items"][0]["log"][
            "done"
        ]


@pytest.mark.parametrize("snooze_offset", [600, -60])
def test_imported_snooze_is_preserved_and_bad_metadata_is_rejected(
    tmp_path, snooze_offset
):
    from test_remember import export

    clock = [epoch("2026-09-20T09:00:00+00:00")]
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        scheduler_clock=lambda: clock[0],
    )
    h = {"origin": "http://testserver"}
    with TestClient(app) as client:
        login(client)
        state = export()
        state["dailyLogs"] = {}
        state["objectives"][0]["snoozes"] = {
            "2026-09-20": (clock[0] + snooze_offset) * 1000
        }
        state["objectives"][0]["lastReminder"] = {"2026-09-20": clock[0] * 1000}
        body = {
            "sourceId": "remember-snooze",
            "timezone": "Europe/London",
            "state": state,
        }
        preview = client.post(
            "/api/imports/remember/preview", json=body, headers=h
        ).json()
        assert (
            client.post(
                "/api/imports/remember",
                json={**body, "previewDigest": preview["digest"]},
                headers=h,
            ).status_code
            == 200
        )
        client.post("/api/notifications/check", json={}, headers=h)
        assert len(client.get("/api/notifications").json()["items"]) == int(
            snooze_offset < 0
        )
        clock[0] += 601
        client.post("/api/notifications/check", json={}, headers=h)
        assert len(client.get("/api/notifications").json()["items"]) == 1
        state["objectives"][0]["snoozes"] = "bad"
        assert (
            client.post(
                "/api/imports/remember/preview", json=body, headers=h
            ).status_code
            == 422
        )


def test_editing_ready_reminder_to_future_withdraws_until_new_time(tmp_path):
    clock = [epoch("2026-09-20T09:00:00+00:00")]
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        scheduler_clock=lambda: clock[0],
    )
    h = {"origin": "http://testserver"}
    with TestClient(app) as client:
        login(client)
        item = client.post(
            "/api/commitments",
            json={
                "title": "Walk",
                "kind": "habit",
                "reminderTime": "09:30",
                "timezone": "Europe/London",
            },
            headers=h,
        ).json()
        client.post("/api/notifications/check", json={}, headers=h)
        old = client.get("/api/notifications").json()["items"][0]
        assert (
            client.patch(
                "/api/commitments/" + item["id"],
                json={"revision": 1, "reminderTime": "18:00"},
                headers=h,
            ).status_code
            == 200
        )
        client.post("/api/notifications/check", json={}, headers=h)
        assert client.get("/api/notifications").json()["items"] == []
        assert (
            client.post(
                "/api/notifications/" + old["id"] + "/complete",
                json={"revision": old["revision"]},
                headers=h,
            ).status_code
            == 409
        )
        clock[0] = epoch("2026-09-20T17:00:00+00:00")
        client.post("/api/notifications/check", json={}, headers=h)
        new = client.get("/api/notifications").json()["items"][0]
        assert new["id"] == old["id"]
        assert new["revision"] > old["revision"]
