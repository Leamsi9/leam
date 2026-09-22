import json
from uuid import uuid4

import pytest
from test_api import login, make
from test_overview_completion_evidence import snapshot

from leam_api.activity_backfill import ActivityBackfill, Apply, main

H = {"origin": "http://testserver"}
NOW = 1790078400.0


def create(client, **extra):
    result = client.post("/api/commitments", headers=H, json={"title": "Task", **extra})
    assert result.status_code == 200, result.text
    return result.json()


def command(monkeypatch, capsys, root, *args):
    monkeypatch.setattr("sys.argv", ["backfill", "--data-dir", str(root), *args])
    main()
    return json.loads(capsys.readouterr().out)


def test_operator_preview_apply_retry_and_shared_projection(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr("leam_api.activity_backfill.time.time", lambda: NOW - 86400)
    client, _ = make(tmp_path)
    with client:
        login(client)
        nested, child = str(uuid4()), str(uuid4())
        card = create(
            client,
            status="completed",
            owner="leam",
            subtasks=[
                {
                    "id": child,
                    "title": "User started step",
                    "owner": "user",
                    "status": "in_progress",
                    "children": [
                        {
                            "id": nested,
                            "title": "Leam completed nested step",
                            "owner": "leam",
                            "status": "completed",
                        }
                    ],
                }
            ],
        )
        started = create(
            client, title="User active task", stage="in_progress", owner="user"
        )
        idle = create(client, title="Not selected")
        store = client.app.state.store
        with store.connect() as db:
            historical = [
                tuple(row) for row in db.execute("SELECT * FROM events ORDER BY id")
            ]
        monkeypatch.setattr("leam_api.activity_backfill.time.time", lambda: NOW)
        preview = command(
            monkeypatch, capsys, store.path.parent, "preview", "--day", "2026-09-22"
        )
        assert preview["recordCount"] == 4
        assert preview["cardCount"] == 2
        with store.connect() as db:
            assert (
                db.execute(
                    "SELECT revision FROM entities WHERE id=?", (card["id"],)
                ).fetchone()[0]
                == 1
            )
        request_path = tmp_path / "reviewed.json"
        request_path.write_text(json.dumps(preview["request"]))
        receipt = command(
            monkeypatch,
            capsys,
            store.path.parent,
            "apply",
            "--input",
            str(request_path),
        )
        replay = command(
            monkeypatch,
            capsys,
            store.path.parent,
            "apply",
            "--input",
            str(request_path),
        )
        assert replay == receipt
        assert receipt["recordedAt"] == NOW
        assert receipt["source"] == "user_requested_backfill"
        rows = {x["id"]: x for x in client.get("/api/commitments").json()["items"]}
        assert rows[card["id"]]["completedAt"] == NOW
        assert rows[started["id"]]["statusChangedAt"] == NOW
        assert rows[idle["id"]]["revision"] == 1
        assert "completedAt" not in rows[card["id"]]["subtasks"][0]["children"][0]
        with store.connect() as db:
            assert [
                tuple(row)
                for row in db.execute(
                    "SELECT * FROM events ORDER BY id LIMIT ?", (len(historical),)
                )
            ] == historical
            records = list(
                db.execute(
                    "SELECT payload FROM events WHERE topic='commitment.activity_backfill'"
                )
            )
            assert len(records) == 2
            event = next(
                json.loads(row[0])
                for row in records
                if json.loads(row[0])["id"] == card["id"]
            )
            assert event["previousTimestamps"]["completedAt"] == NOW - 86400
        view = snapshot(client)
        assert (
            next(x for x in view["items"] if x["id"] == card["id"])[
                "completionEvidence"
            ]
            == "user_requested_backfill"
        )
        activity = {x["title"]: x for x in view["activity"]}
        assert activity["User started step"]["owner"] == "user"
        assert activity["Leam completed nested step"]["owner"] == "leam"
        assert all(x["source"] == "user_requested_backfill" for x in activity.values())
        # Canonical strict nested model still accepts a later ordinary edit.
        edited = client.patch(
            "/api/commitments/" + card["id"],
            headers=H,
            json={"revision": 2, "title": "Renamed task"},
        )
        assert edited.status_code == 200, edited.text


@pytest.mark.parametrize("change", ["revision", "new_eligible"])
def test_stale_complete_selection_is_atomic(tmp_path, change):
    client, _ = make(tmp_path)
    with client:
        login(client)
        card = create(client, status="completed")
        service = ActivityBackfill(client.app.state.store, clock=lambda: NOW)
        request = Apply(**service.preview("2026-09-22", "Europe/London")["request"])
        if change == "revision":
            assert (
                client.patch(
                    "/api/commitments/" + card["id"],
                    headers=H,
                    json={"revision": 1, "title": "Changed"},
                ).status_code
                == 200
            )
        else:
            create(client, stage="in_progress")
        with pytest.raises(ValueError, match="changed"):
            service.apply(request)
        with client.app.state.store.connect() as db:
            assert (
                db.execute(
                    "SELECT count(*) FROM events WHERE topic='commitment.activity_backfill'"
                ).fetchone()[0]
                == 0
            )
            assert (
                db.execute(
                    "SELECT count(*) FROM settings WHERE key GLOB 'activity:backfill:*'"
                ).fetchone()[0]
                == 0
            )


def test_request_identity_and_today_boundary(tmp_path):
    client, _ = make(tmp_path)
    with client:
        login(client)
        create(client, status="completed")
        clock = [NOW]
        service = ActivityBackfill(client.app.state.store, clock=lambda: clock[0])
        request = Apply(**service.preview("2026-09-22", "Europe/London")["request"])
        receipt = service.apply(request)
        clock[0] += 86400
        assert service.apply(request) == receipt
        with pytest.raises(ValueError, match="different input"):
            service.apply(request.model_copy(update={"timezone": "UTC"}))
        fresh = request.model_copy(update={"requestId": uuid4()})
        with pytest.raises(ValueError, match="today"):
            service.apply(fresh)
        with pytest.raises(ValueError, match="today"):
            service.preview("2026-09-22", "Europe/London")


def test_child_only_backfill_keeps_parent_dates_and_empty_selection_safe(tmp_path):
    client, _ = make(tmp_path)
    with client:
        login(client)
        service = ActivityBackfill(client.app.state.store, clock=lambda: NOW)
        empty = Apply(**service.preview("2026-09-22", "Europe/London")["request"])
        assert service.apply(empty)["recordCount"] == 0
        child = str(uuid4())
        parent = create(
            client,
            subtasks=[
                {
                    "id": child,
                    "title": "Child only",
                    "status": "completed",
                    "owner": "leam",
                }
            ],
        )
        request = Apply(**service.preview("2026-09-22", "Europe/London")["request"])
        assert service.apply(request)["recordCount"] == 1
        current = next(
            x
            for x in client.get("/api/commitments").json()["items"]
            if x["id"] == parent["id"]
        )
        assert current["statusChangedAt"] == parent["statusChangedAt"]
        assert current["completedAt"] is None
        assert current["status"] == "active"
        assert current["revision"] == parent["revision"] + 1
