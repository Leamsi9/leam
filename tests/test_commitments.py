from test_api import login, make


def test_daily_progress_undo_dates_and_stale_writes(tmp_path):
    client, _ = make(tmp_path)
    with client:
        login(client)
        h = {"origin": "http://testserver"}
        capacity = client.post(
            "/api/capacities",
            json={"name": "Health", "note": "Move gently", "record": "Best walk: 5 km"},
            headers=h,
        )
        assert capacity.status_code == 200
        result = client.post(
            "/api/commitments",
            json={
                "title": "Walk",
                "kind": "habit",
                "measure": "minutes",
                "target": 20,
                "capacityId": capacity.json()["id"],
                "startDate": "2026-09-20",
                "endDate": "2026-09-22",
                "timezone": "Europe/London",
                "reward": "Tea",
            },
            headers=h,
        )
        assert result.status_code == 200, result.text
        item = result.json()
        url = f"/api/commitments/{item['id']}/progress/2026-09-20"
        set_value = client.put(
            url,
            json={
                "revision": 0,
                "commitmentRevision": 1,
                "operation": "set",
                "value": 7,
            },
            headers=h,
        )
        assert set_value.status_code == 200, set_value.text
        assert (
            set_value.json()["log"]["value"] == 7
            and not set_value.json()["log"]["done"]
        )
        done = client.put(
            url,
            json={"revision": 1, "commitmentRevision": 1, "operation": "toggle"},
            headers=h,
        )
        assert done.json()["log"]["done"] and done.json()["log"]["value"] == 7
        undo = client.put(
            url,
            json={"revision": 2, "commitmentRevision": 1, "operation": "toggle"},
            headers=h,
        )
        assert undo.json()["log"]["value"] == 7 and not undo.json()["log"]["done"]
        assert (
            client.put(
                url,
                json={
                    "revision": 1,
                    "commitmentRevision": 1,
                    "operation": "set",
                    "value": 99,
                },
                headers=h,
            ).status_code
            == 409
        )
        assert client.get("/api/today?date=2026-09-19").json()["items"] == []
        tomorrow = client.get("/api/today?date=2026-09-21").json()["items"][0]
        assert tomorrow["log"]["value"] == 0 and tomorrow["log"]["revision"] == 0
        invalid = client.post(
            "/api/commitments",
            json={"title": "Bad", "startDate": "2026-09-22", "endDate": "2026-09-20"},
            headers=h,
        )
        assert invalid.status_code == 422
        assert (
            client.post(
                "/api/commitments",
                json={"title": "Bad", "timezone": "Invalid/Zone"},
                headers=h,
            ).status_code
            == 422
        )
        assert (
            client.delete(
                "/api/capacities/" + capacity.json()["id"] + "?revision=1", headers=h
            ).status_code
            == 409
        )


def test_task_completion_is_atomic_with_daily_log_and_numeric_zero_undo(tmp_path):
    client, _ = make(tmp_path)
    with client:
        login(client)
        h = {"origin": "http://testserver"}
        item = client.post(
            "/api/commitments",
            json={"title": "Write", "measure": "count", "target": 3},
            headers=h,
        ).json()
        url = f"/api/commitments/{item['id']}/progress/2026-09-20"
        done = client.put(
            url,
            json={"revision": 0, "commitmentRevision": 1, "operation": "toggle"},
            headers=h,
        )
        assert done.status_code == 200, done.text
        assert done.json()["log"]["value"] == 3
        assert done.json()["commitment"]["status"] == "completed"
        undo = client.put(
            url,
            json={"revision": 1, "commitmentRevision": 2, "operation": "toggle"},
            headers=h,
        )
        assert undo.json()["log"]["value"] == 0
        assert undo.json()["commitment"]["status"] == "active"
        assert (
            len(
                client.get("/api/commitments/" + item["id"] + "/history").json()[
                    "items"
                ]
            )
            == 1
        )


def test_task_can_reopen_on_later_day_and_completed_habits_stop(tmp_path):
    client, _ = make(tmp_path)
    with client:
        login(client)
        h = {"origin": "http://testserver"}
        task = client.post("/api/commitments", json={"title": "Task"}, headers=h).json()
        client.put(
            f"/api/commitments/{task['id']}/progress/2026-09-20",
            json={"revision": 0, "commitmentRevision": 1, "operation": "toggle"},
            headers=h,
        )
        assert not any(
            i["id"] == task["id"]
            for i in client.get("/api/today?date=2026-09-21").json()["items"]
        )
        reopened = client.put(
            f"/api/commitments/{task['id']}/progress/2026-09-21",
            json={"revision": 0, "commitmentRevision": 2, "operation": "toggle"},
            headers=h,
        )
        assert (
            reopened.status_code == 200
            and reopened.json()["commitment"]["status"] == "active"
        )
        habit = client.post(
            "/api/commitments",
            json={"title": "Finished habit", "kind": "habit", "status": "completed"},
            headers=h,
        ).json()
        assert not any(
            i["id"] == habit["id"]
            for i in client.get("/api/today?date=2026-09-21").json()["items"]
        )


def test_task_toggles_follow_global_status_when_visiting_previous_day(tmp_path):
    client, _ = make(tmp_path)
    with client:
        login(client)
        h = {"origin": "http://testserver"}
        item = client.post("/api/commitments", json={"title": "Task"}, headers=h).json()

        def toggle(day, log_revision, commitment_revision):
            return client.put(
                f"/api/commitments/{item['id']}/progress/{day}",
                json={
                    "revision": log_revision,
                    "commitmentRevision": commitment_revision,
                    "operation": "toggle",
                },
                headers=h,
            ).json()

        assert toggle("2026-09-20", 0, 1)["commitment"]["status"] == "completed"
        assert toggle("2026-09-21", 0, 2)["commitment"]["status"] == "active"
        assert toggle("2026-09-20", 1, 3)["commitment"]["status"] == "completed"
