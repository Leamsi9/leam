import uuid

from fastapi.testclient import TestClient
from test_agenda import DAY, H, application
from test_api import login

from leam_api.agenda import Selection, source_key
from leam_api.agenda_priorities import PREFIX, Priorities


def request(**changes):
    return {**DAY, "requestId": str(uuid.uuid4()), **changes}


def test_actual_caller_orders_canonical_tasks_and_leaves_focus_unchanged(tmp_path):
    app, calls, creates = application(tmp_path)
    with TestClient(app) as client:
        login(client)
        ids = []
        for title, extra in [
            ("Due", {"dueDate": DAY["date"]}),
            ("Important", {"priority": "high"}),
            ("Brief", {"measure": "minutes", "target": 5}),
        ]:
            response = client.post(
                "/api/commitments", json={"title": title, **extra}, headers=H
            )
            assert response.status_code == 200, response.text
            ids.append(response.json()["id"])
        before = client.get("/api/agenda", params=DAY).json()
        value = client.post("/api/agenda/priorities", json=request(), headers=H).json()
        assert value["state"] == "completed"
        assert (
            source_key("commitment", ids[0], DAY["date"])
            == value["suggestions"][0]["key"]
        )
        important = client.post(
            "/api/agenda/priorities", json=request(order="important"), headers=H
        ).json()
        assert (
            source_key("commitment", ids[1], DAY["date"])
            == important["suggestions"][0]["key"]
        )
        short = client.post(
            "/api/agenda/priorities", json=request(order="short"), headers=H
        ).json()
        assert (
            source_key("commitment", ids[2], DAY["date"])
            == short["suggestions"][0]["key"]
        )
        assert short["suggestions"][0]["durationMinutes"] == 5
        after = client.get("/api/agenda", params=DAY).json()
        assert before["commitments"] == after["commitments"]
        assert not calls and not creates


def test_receipt_replay_after_new_check_and_conflicting_id(tmp_path):
    app, _, _ = application(tmp_path)
    with TestClient(app) as client:
        login(client)
        body = request()
        first = client.post("/api/agenda/priorities", json=body, headers=H).json()
        second = client.post(
            "/api/agenda/priorities", json=request(order="short"), headers=H
        ).json()
        assert (
            client.post("/api/agenda/priorities", json=body, headers=H).json() == first
        )
        assert (
            client.get("/api/agenda/priorities", params=DAY).json()["check"] == second
        )
        assert (
            client.post(
                "/api/agenda/priorities", json={**body, "order": "important"}, headers=H
            ).status_code
            == 409
        )


def test_failure_visible_and_pending_restart_recovered(tmp_path, monkeypatch):
    app, _, _ = application(tmp_path)
    original = Priorities.canonical_tasks

    def fail(*args, **kwargs):
        raise RuntimeError("secret not for public output")

    with TestClient(app) as client:
        login(client)
        monkeypatch.setattr(Priorities, "canonical_tasks", fail)
        body = request()
        failed = client.post("/api/agenda/priorities", json=body, headers=H).json()
        assert failed["state"] == "failed" and "secret" not in str(failed)
        monkeypatch.setattr(Priorities, "canonical_tasks", original)
        pending = {**failed, "state": "pending"}
        app.state.store.set(PREFIX + Selection(**DAY).scope_key(), pending)
        app.state.store.set(PREFIX + "request:" + body["requestId"], pending)
    restored, _, _ = application(tmp_path)
    with TestClient(restored) as client:
        client.post(
            "/api/auth/login", json={"password": "long-password-for-tests"}, headers=H
        )
        assert (
            client.get("/api/agenda/priorities", params=DAY).json()["check"]["state"]
            == "pending"
        )
        assert (
            client.post("/api/agenda/priorities", json=body, headers=H).json()["state"]
            == "completed"
        )


def test_custom_filters_exclusions_and_unknown_size(tmp_path, monkeypatch):
    app, _, _ = application(tmp_path)
    cap = str(uuid.uuid4())

    def snapshot(*args, **kwargs):
        base = {
            "kind": "task",
            "status": "active",
            "priority": "high",
            "owner": "user",
            "capacityId": cap,
            "revision": 1,
        }
        return {
            "observedAt": 1,
            "partial": True,
            "sources": {},
            "commitments": [
                {**base, "key": "allowed"},
                {**base, "key": "done", "status": "completed"},
                {**base, "key": "blocked", "stage": "blocked"},
                {**base, "key": "leam", "owner": "leam"},
                {**base, "key": "focus", "triage": {"disposition": "focus"}},
                {**base, "key": "other", "capacityId": str(uuid.uuid4())},
            ],
            "events": [],
            "emails": [],
        }

    monkeypatch.setattr(app.state.agenda, "snapshot", snapshot)
    monkeypatch.setattr(
        Priorities,
        "canonical_tasks",
        lambda self, selection, db: snapshot()["commitments"],
    )
    with TestClient(app) as client:
        login(client)
        value = client.post(
            "/api/agenda/priorities",
            json=request(owner="user", capacityId=cap),
            headers=H,
        ).json()
        assert [i["key"] for i in value["suggestions"]] == ["allowed"]
        assert (
            value["suggestions"][0]["durationMinutes"] is None
            and value["partial"] is False
        )


def test_auth_origin_validation_and_read_only_get(tmp_path):
    app, _, _ = application(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/agenda/priorities", params=DAY).status_code == 401
        login(client)
        assert client.get("/api/agenda/priorities", params=DAY).json() == {
            "check": None,
            "excludedKeys": [],
        }
        assert not app.state.store.get(PREFIX + Selection(**DAY).scope_key())
        assert client.post("/api/agenda/priorities", json=request()).status_code == 403
        assert (
            client.post(
                "/api/agenda/priorities", json=request(order="invent"), headers=H
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/api/agenda/priorities", params={**DAY, "timezone": "wrong"}
            ).status_code
            == 422
        )


def test_interrupted_old_request_cannot_replace_newer_result(tmp_path):
    app, _, _ = application(tmp_path)
    with TestClient(app) as client:
        login(client)
        body = request()
        old = client.post("/api/agenda/priorities", json=body, headers=H).json()
        app.state.store.set(
            PREFIX + "request:" + body["requestId"], {**old, "state": "pending"}
        )
        latest = client.post("/api/agenda/priorities", json=request(), headers=H).json()
        assert (
            client.post("/api/agenda/priorities", json=body, headers=H).status_code
            == 409
        )
        assert (
            client.get("/api/agenda/priorities", params=DAY).json()["check"] == latest
        )


def test_real_day_focus_is_excluded_from_every_custom_check(tmp_path):
    app, _, _ = application(tmp_path)
    with TestClient(app) as client:
        login(client)
        item = client.post(
            "/api/commitments", json={"title": "Chosen", "priority": "high"}, headers=H
        ).json()
        key = source_key("commitment", item["id"], DAY["date"])
        assert (
            client.put(
                "/api/agenda/triage",
                json={**DAY, "key": key, "revision": 0, "disposition": "focus"},
                headers=H,
            ).status_code
            == 200
        )
        for order in ("urgent", "important", "short"):
            result = client.post(
                "/api/agenda/priorities", json=request(order=order), headers=H
            ).json()
            assert result["state"] == "completed" and result["suggestions"] == []


def test_all_canonical_tasks_beyond_page_unscheduled_and_in_progress(tmp_path):
    import json
    import time

    app, _, _ = application(tmp_path)
    with TestClient(app) as client:
        login(client)
        with app.state.store.connect() as db:
            for index in range(1101):
                key = str(uuid.uuid4())
                body = {
                    "title": f"Task {index:04}",
                    "kind": "task",
                    "status": "active",
                    "measure": "boolean",
                    "target": 1,
                    "owner": "user",
                    "stage": "in_progress" if index == 1100 else "todo",
                    "priority": "high" if index == 1100 else "normal",
                    "timezone": "Europe/London",
                    "startDate": "2027-01-01" if index == 1100 else None,
                }
                db.execute(
                    "INSERT INTO entities VALUES (?, 'commitment', 1, ?, ?)",
                    (key, json.dumps(body), time.time()),
                )
                if index == 1100:
                    important_id = key
        response = client.post("/api/agenda/priorities", json=request(), headers=H)
        assert response.status_code == 200
        check = response.json()
        assert check["coverage"]["canonicalTasks"] == 1101
        assert len(check["suggestions"]) == 5
        first = check["suggestions"][0]
        assert first["entityId"] == important_id
        assert "In progress" in first["reason"]
        assert first["currentItem"]["title"] == "Task 1100"
        assert first["currentItem"]["focusEligible"] is False
        assert any("Pending task" in item["reason"] for item in check["suggestions"])


def test_exclude_rerun_reload_undo_and_next_day_isolation(tmp_path):
    app, _, _ = application(tmp_path)
    with TestClient(app) as client:
        login(client)
        client.post(
            "/api/commitments", json={"title": "Unscheduled pending"}, headers=H
        )
        first = client.post("/api/agenda/priorities", json=request(), headers=H).json()
        key = first["suggestions"][0]["key"]
        body = {**DAY, "key": key, "excluded": True}
        for _ in range(2):
            result = client.put(
                "/api/agenda/priorities/exclusions", json=body, headers=H
            )
            assert result.status_code == 200
            assert result.json()["excludedKeys"] == [key]
            assert result.json()["check"]["suggestions"] == []
        assert (
            client.post("/api/agenda/priorities", json=request(), headers=H).json()[
                "suggestions"
            ]
            == []
        )
        tomorrow = client.post(
            "/api/agenda/priorities", json=request(date="2026-10-26"), headers=H
        ).json()
        assert len(tomorrow["suggestions"]) == 1
        assert client.get("/api/agenda/priorities", params=DAY).json()[
            "excludedKeys"
        ] == [key]
        assert (
            client.put(
                "/api/agenda/priorities/exclusions",
                json={**body, "excluded": False},
                headers=H,
            ).status_code
            == 200
        )
        assert (
            len(
                client.post("/api/agenda/priorities", json=request(), headers=H).json()[
                    "suggestions"
                ]
            )
            == 1
        )
        assert (
            client.put("/api/agenda/priorities/exclusions", json=body).status_code
            == 403
        )
        assert (
            client.put(
                "/api/agenda/priorities/exclusions",
                json={**body, "key": "invented"},
                headers=H,
            ).status_code
            == 404
        )


def test_exclusion_timezone_and_installation_isolation(tmp_path):
    app, _, _ = application(tmp_path / "first")
    other, _, _ = application(tmp_path / "other")
    with TestClient(app) as client, TestClient(other) as second:
        login(client)
        login(second)
        client.post("/api/commitments", json={"title": "Pending"}, headers=H)
        check = client.post("/api/agenda/priorities", json=request(), headers=H).json()
        key = check["suggestions"][0]["key"]
        assert (
            second.put(
                "/api/agenda/priorities/exclusions", json={**DAY, "key": key}, headers=H
            ).status_code
            == 404
        )
        client.put(
            "/api/agenda/priorities/exclusions", json={**DAY, "key": key}, headers=H
        )
        assert (
            client.get(
                "/api/agenda/priorities", params={**DAY, "timezone": "UTC"}
            ).json()["excludedKeys"]
            == []
        )


def test_task_only_check_never_reads_mail_calendar_or_retains_old_non_tasks(
    tmp_path, monkeypatch
):
    app, _, _ = application(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("Task triage must not read Agenda/mail/calendar")

    monkeypatch.setattr(app.state.agenda, "snapshot", forbidden)
    with TestClient(app) as client:
        login(client)
        ids = {}
        for kind in ("task", "habit", "goal"):
            ids[kind] = client.post(
                "/api/commitments",
                json={"title": kind, "kind": kind, "priority": "high"},
                headers=H,
            ).json()["id"]
        result = client.post("/api/agenda/priorities", json=request(), headers=H).json()
        assert result["state"] == "completed"
        assert [item["entityId"] for item in result["suggestions"]] == [ids["task"]]
        assert result["sources"] == {"tasks": "local"}
        # Old result receipts remain immutable facts, but their display is task-only.
        saved = app.state.store.get(PREFIX + Selection(**DAY).scope_key())
        saved["suggestions"] += [
            {"key": "old-mail", "kind": "email"},
            {"key": "old-event", "kind": "appointment"},
            {
                "key": source_key("commitment", ids["habit"], DAY["date"]),
                "kind": "habit",
            },
            {"key": source_key("commitment", ids["goal"], DAY["date"]), "kind": "task"},
        ]
        app.state.store.set(PREFIX + Selection(**DAY).scope_key(), saved)
        assert [
            item["entityId"]
            for item in client.get("/api/agenda/priorities", params=DAY).json()[
                "check"
            ]["suggestions"]
        ] == [ids["task"]]
