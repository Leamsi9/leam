import json
from uuid import uuid4

from test_api import login, make

H = {"origin": "http://testserver"}


def test_status_timestamps_survive_edits_and_record_each_transition(
    tmp_path, monkeypatch
):
    clock = [1790078400.0]
    monkeypatch.setattr("leam_api.completion_evidence.time.time", lambda: clock[0])
    client, _ = make(tmp_path)
    with client:
        login(client)
        card = client.post(
            "/api/commitments", headers=H, json={"title": "Real task"}
        ).json()
        url = "/api/commitments/" + card["id"]
        assert card["statusChangedAt"] == clock[0]
        assert card["completedAt"] is None
        clock[0] += 60
        started = client.patch(
            url, headers=H, json={"revision": 1, "stage": "in_progress"}
        ).json()
        assert started["statusChangedAt"] == clock[0]
        clock[0] += 60
        done = client.patch(
            url, headers=H, json={"revision": 2, "status": "completed"}
        ).json()
        assert done["completedAt"] == done["statusChangedAt"] == clock[0]
        clock[0] += 60
        edited = client.patch(
            url, headers=H, json={"revision": 3, "title": "Same task"}
        ).json()
        assert edited["completedAt"] == done["completedAt"]
        assert edited["statusChangedAt"] == done["statusChangedAt"]
        sub = client.post(
            url + "/subtasks",
            headers=H,
            json={
                "revision": 4,
                "action": "add",
                "subtaskId": str(uuid4()),
                "title": "Recorded detail",
            },
        )
        assert sub.status_code == 200, sub.text
        assert sub.json()["completedAt"] == done["completedAt"]
        assert (
            client.patch(
                url, headers=H, json={"revision": 4, "status": "active"}
            ).status_code
            == 409
        )
        assert (
            client.patch(
                url, headers=H, json={"revision": 5, "completedAt": 1}
            ).status_code
            == 422
        )
        opened = client.patch(
            url, headers=H, json={"revision": 5, "status": "active"}
        ).json()
        assert opened["completedAt"] is None
        assert opened["statusChangedAt"] == clock[0]
        clock[0] += 60
        again = client.patch(
            url, headers=H, json={"revision": 6, "status": "completed"}
        ).json()
        assert again["completedAt"] == clock[0]
        with client.app.state.store.connect() as db:
            events = [
                json.loads(r["payload"])
                for r in db.execute(
                    "SELECT payload FROM events WHERE topic='commitment.status_changed' ORDER BY id"
                )
            ]
        assert [(e["from"], e["to"]) for e in events] == [
            ("todo", "in_progress"),
            ("in_progress", "completed"),
            ("completed", "in_progress"),
            ("in_progress", "completed"),
        ]
        assert [e["revision"] for e in events] == [2, 3, 6, 7]


def test_legacy_completed_metadata_edit_does_not_invent_timestamp(tmp_path):
    client, _ = make(tmp_path)
    with client:
        login(client)
        card = client.post(
            "/api/commitments", headers=H, json={"title": "Old task"}
        ).json()
        with client.app.state.store.connect() as db:
            body = {
                k: v
                for k, v in card.items()
                if k not in {"id", "revision", "completedAt", "statusChangedAt"}
            }
            body["status"] = "completed"
            db.execute(
                "UPDATE entities SET body=? WHERE id=?", (json.dumps(body), card["id"])
            )
        edited = client.patch(
            "/api/commitments/" + card["id"],
            headers=H,
            json={"revision": 1, "title": "Old task renamed"},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["completedAt"] is None
        assert edited.json()["statusChangedAt"] is None


def test_progress_caller_timestamps_completion_and_reopen(tmp_path, monkeypatch):
    clock = [1790078400.0]
    monkeypatch.setattr("leam_api.completion_evidence.time.time", lambda: clock[0])
    client, _ = make(tmp_path)
    with client:
        login(client)
        card = client.post(
            "/api/commitments", headers=H, json={"title": "Daily task"}
        ).json()
        for revision, status in [(0, "completed"), (1, "active"), (2, "completed")]:
            clock[0] += 60
            result = client.put(
                "/api/commitments/" + card["id"] + "/progress/2026-09-22",
                headers=H,
                json={
                    "revision": revision,
                    "commitmentRevision": revision + 1,
                    "operation": "toggle",
                },
            )
            assert result.status_code == 200, result.text
            changed = result.json()["commitment"]
            assert changed["status"] == status
            assert changed["statusChangedAt"] == clock[0]
            assert changed["completedAt"] == (
                clock[0] if status == "completed" else None
            )
        with client.app.state.store.connect() as db:
            assert (
                db.execute(
                    "SELECT COUNT(*) FROM events WHERE topic='commitment.status_changed'"
                ).fetchone()[0]
                == 3
            )


def test_subtask_status_history_and_activity_use_child_owner(tmp_path, monkeypatch):
    from test_overview_completion_evidence import snapshot

    monkeypatch.setattr("leam_api.completion_evidence.time.time", lambda: 1790078400.0)
    client, _ = make(tmp_path)
    with client:
        login(client)
        child = str(uuid4())
        card = client.post(
            "/api/commitments",
            headers=H,
            json={
                "title": "Leam parent",
                "owner": "leam",
                "subtasks": [{"id": child, "title": "My step", "owner": "user"}],
            },
        ).json()
        result = client.post(
            "/api/commitments/" + card["id"] + "/subtasks",
            headers=H,
            json={
                "revision": 1,
                "action": "edit",
                "subtaskId": child,
                "status": "in_progress",
            },
        )
        assert result.status_code == 200, result.text
        activity = snapshot(client)["activity"]
        assert len(activity) == 1
        assert activity[0]["action"] == "subtask_started"
        assert activity[0]["owner"] == "user"
        with client.app.state.store.connect() as db:
            row = db.execute(
                "SELECT payload,created FROM events WHERE topic='commitment.subtask_status_changed'"
            ).fetchone()
            event = json.loads(row["payload"])
        assert event["owner"] == "user"
        assert (event["from"], event["to"]) == ("todo", "in_progress")
        assert row["created"] == 1790078400.0
