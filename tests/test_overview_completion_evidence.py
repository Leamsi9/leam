import json
from datetime import datetime

from test_api import login, make

H = {"origin": "http://testserver"}


def snapshot(client, day="2026-09-22", timezone="Europe/London"):
    response = client.get(
        "/api/agenda", params={"date": day, "timezone": timezone, "limit": 1}
    )
    assert response.status_code == 200, response.text
    return response.json()["accomplishments"]


def test_board_completion_is_dated_deduplicated_and_not_metadata_date(
    tmp_path, monkeypatch
):
    client, _ = make(tmp_path)
    clock = [datetime.fromisoformat("2026-09-22T23:30:00+00:00").timestamp()]
    monkeypatch.setattr("leam_api.completion_evidence.time.time", lambda: clock[0])
    with client:
        login(client)
        card = client.post(
            "/api/commitments", headers=H, json={"title": "Board task"}
        ).json()
        done = client.patch(
            f"/api/commitments/{card['id']}",
            headers=H,
            json={"revision": 1, "status": "completed"},
        )
        assert done.status_code == 200, done.text
        assert snapshot(client)["items"] == []  # London already Sep23.
        assert len(snapshot(client, "2026-09-23")["items"]) == 1
        assert len(snapshot(client, timezone="UTC")["items"]) == 1
        clock[0] += 86400
        edited = client.patch(
            f"/api/commitments/{card['id']}",
            headers=H,
            json={"revision": 2, "title": "Renamed"},
        )
        assert edited.status_code == 200
        assert len(snapshot(client, "2026-09-23")["items"]) == 1
        assert snapshot(client, "2026-09-24")["items"] == []
        assert (
            client.patch(
                f"/api/commitments/{card['id']}",
                headers=H,
                json={"revision": 2, "status": "active"},
            ).status_code
            == 409
        )
        assert (
            client.patch(
                f"/api/commitments/{card['id']}",
                headers=H,
                json={"revision": 3, "status": "active"},
            ).status_code
            == 200
        )
        assert snapshot(client, "2026-09-23")["items"] == []


def test_full_completion_set_ignores_agenda_page_and_includes_archived_habit(tmp_path):
    client, _ = make(tmp_path)
    with client:
        login(client)
        for n in range(10):
            card = client.post(
                "/api/commitments",
                headers=H,
                json={"title": f"Done {n}", "kind": "habit"},
            ).json()
            progress = client.put(
                f"/api/commitments/{card['id']}/progress/2026-09-22",
                headers=H,
                json={"revision": 0, "commitmentRevision": 1, "operation": "toggle"},
            )
            assert progress.status_code == 200, progress.text
            if n == 0:
                assert (
                    client.patch(
                        f"/api/commitments/{card['id']}",
                        headers=H,
                        json={"revision": 1, "status": "paused"},
                    ).status_code
                    == 200
                )
        result = snapshot(client)
        assert len(result["items"]) == 10
        assert len({x["id"] for x in result["items"]}) == 10
        assert snapshot(client, "2026-09-21")["items"] == []


def test_legacy_approval_transition_and_undated_cards_not_guessed(tmp_path):
    client, _ = make(tmp_path)
    with client:
        login(client)
        cards = [
            client.post(
                "/api/commitments", headers=H, json={"title": f"Legacy {n}"}
            ).json()
            for n in range(3)
        ]
        # Legacy database fixture models pre-event receipts, including a later
        # title edit on an already-completed card. Production API is the caller.
        with client.app.state.store.connect() as db:
            for card in cards:
                data = {k: v for k, v in card.items() if k not in {"id", "revision"}}
                data["status"] = "completed"
                db.execute(
                    "UPDATE entities SET body=? WHERE id=?",
                    (json.dumps(data), card["id"]),
                )
            stamp = datetime.fromisoformat("2026-09-22T10:00:00+00:00").timestamp()
            for n, before in [(0, "active"), (1, "completed")]:
                result = {**cards[n], "status": "completed"}
                db.execute(
                    "INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"fixture-{n}",
                        f"fp{n}",
                        "fixture",
                        "commitment.edit",
                        "{}",
                        json.dumps({"before": {"status": before}}),
                        "fixture",
                        "complete",
                        json.dumps(result),
                        None,
                        stamp,
                        stamp,
                    ),
                )
        result = snapshot(client)
        assert [x["id"] for x in result["items"]] == [cards[0]["id"]]
        assert result["undatedCompleted"] == 2


def test_shared_activity_records_started_and_nested_subtask_completion_once(
    tmp_path, monkeypatch
):
    import uuid

    client, _ = make(tmp_path)
    monkeypatch.setattr(
        "leam_api.completion_evidence.time.time",
        lambda: datetime.fromisoformat("2026-09-22T12:00:00+00:00").timestamp(),
    )
    with client:
        login(client)
        card = client.post(
            "/api/commitments", headers=H, json={"title": "Still active"}
        ).json()
        url = f"/api/commitments/{card['id']}"
        assert (
            client.patch(
                url, headers=H, json={"revision": 1, "stage": "in_progress"}
            ).status_code
            == 200
        )
        child = str(uuid.uuid4())
        added = client.post(
            url + "/subtasks",
            headers=H,
            json={
                "revision": 2,
                "action": "add",
                "subtaskId": child,
                "title": "One step",
                "status": "completed",
            },
        )
        assert added.status_code == 200, added.text
        evidence = snapshot(client)
        assert evidence["items"] == []
        assert sorted(x["action"] for x in evidence["activity"]) == [
            "started",
            "subtask_completed",
        ]
        assert (
            client.patch(
                url, headers=H, json={"revision": 3, "title": "Renamed active"}
            ).status_code
            == 200
        )
        assert len(snapshot(client)["activity"]) == 2
        assert (
            client.post(
                url + "/subtasks",
                headers=H,
                json={
                    "revision": 3,
                    "action": "edit",
                    "subtaskId": child,
                    "status": "completed",
                },
            ).status_code
            == 409
        )
        assert (
            client.post(
                url + "/subtasks",
                headers=H,
                json={
                    "revision": 4,
                    "action": "edit",
                    "subtaskId": child,
                    "status": "todo",
                },
            ).status_code
            == 200
        )
        assert [x["action"] for x in snapshot(client)["activity"]] == ["started"]
        assert (
            client.patch(
                url, headers=H, json={"revision": 5, "stage": "todo"}
            ).status_code
            == 200
        )
        assert snapshot(client)["activity"] == []
