import pytest
from test_api import login, make

HEADERS = {"origin": "http://testserver"}


def setup(client):
    login(client)
    source = client.post(
        "/api/capacities", json={"name": "Health"}, headers=HEADERS
    ).json()
    target = client.post(
        "/api/capacities", json={"name": "Growth"}, headers=HEADERS
    ).json()
    item = client.post(
        "/api/commitments",
        json={"title": "Walk", "kind": "habit", "capacityId": source["id"]},
        headers=HEADERS,
    ).json()
    client.put(
        f"/api/commitments/{item['id']}/progress/2026-09-20",
        json={"revision": 0, "commitmentRevision": 1, "operation": "set", "value": 1},
        headers=HEADERS,
    )
    return source, target, item


def preview(client, kind, key):
    result = client.get(f"/api/{kind}/{key}/removal-preview")
    assert result.status_code == 200, result.text
    return result.json()


def test_remove_commitment_requires_exact_review_and_cleans_dependencies(tmp_path):
    client, _ = make(tmp_path)
    with client:
        _source, _, item = setup(client)
        review = preview(client, "commitments", item["id"])
        assert (
            review["progressEntries"] == 1
            and review["commitments"][0]["title"] == "Walk"
        )
        url = f"/api/commitments/{item['id']}/remove"
        assert (
            client.post(
                url,
                json={"previewToken": review["previewToken"], "confirmed": False},
                headers=HEADERS,
            ).status_code
            == 422
        )
        assert (
            client.post(
                url, json={"previewToken": review["previewToken"], "confirmed": True}
            ).status_code
            == 403
        )
        removed = client.post(
            url,
            json={"previewToken": review["previewToken"], "confirmed": True},
            headers=HEADERS,
        )
        assert removed.status_code == 200, removed.text
        assert client.get("/api/commitments").json()["items"] == []
        assert (
            client.get(f"/api/commitments/{item['id']}/history").json()["items"] == []
        )
        assert len(client.get("/api/capacities").json()["items"]) == 2
        assert (
            client.post(
                url,
                json={"previewToken": review["previewToken"], "confirmed": True},
                headers=HEADERS,
            ).status_code
            == 404
        )


def test_capacity_move_is_atomic_and_preserves_history(tmp_path):
    client, _ = make(tmp_path)
    with client:
        source, target, item = setup(client)
        review = preview(client, "capacities", source["id"])
        response = client.post(
            f"/api/capacities/{source['id']}/remove",
            json={
                "previewToken": review["previewToken"],
                "confirmed": True,
                "operation": "move",
                "targetCapacityId": target["id"],
                "targetRevision": target["revision"],
            },
            headers=HEADERS,
        )
        assert response.status_code == 200, response.text
        moved = client.get("/api/commitments").json()["items"][0]
        assert (
            moved["capacityId"] == target["id"]
            and moved["revision"] == item["revision"] + 1
        )
        assert (
            len(client.get(f"/api/commitments/{item['id']}/history").json()["items"])
            == 1
        )
        assert [c["id"] for c in client.get("/api/capacities").json()["items"]] == [
            target["id"]
        ]


@pytest.mark.parametrize("change", ["member", "edit", "progress", "target"])
def test_capacity_stale_review_or_target_cannot_move_or_cascade(tmp_path, change):
    client, _ = make(tmp_path)
    with client:
        source, target, item = setup(client)
        review = preview(client, "capacities", source["id"])
        if change == "member":
            client.post(
                "/api/commitments",
                json={"title": "New unreviewed", "capacityId": source["id"]},
                headers=HEADERS,
            )
        elif change == "edit":
            client.patch(
                f"/api/commitments/{item['id']}",
                json={"revision": 1, "notes": "Another device"},
                headers=HEADERS,
            )
        elif change == "progress":
            client.put(
                f"/api/commitments/{item['id']}/progress/2026-09-20",
                json={
                    "revision": 1,
                    "commitmentRevision": 1,
                    "operation": "set",
                    "value": 0,
                },
                headers=HEADERS,
            )
        else:
            client.patch(
                f"/api/capacities/{target['id']}",
                json={"revision": 1, "name": "Renamed"},
                headers=HEADERS,
            )
        response = client.post(
            f"/api/capacities/{source['id']}/remove",
            json={
                "previewToken": review["previewToken"],
                "confirmed": True,
                "operation": "move" if change == "target" else "cascade",
                **(
                    {"targetCapacityId": target["id"], "targetRevision": 1}
                    if change == "target"
                    else {}
                ),
            },
            headers=HEADERS,
        )
        assert response.status_code == 409, response.text
        assert len(client.get("/api/capacities").json()["items"]) == 2
        assert all(
            c["capacityId"] == source["id"]
            for c in client.get("/api/commitments").json()["items"]
        )


def test_capacity_cascade_removes_only_reviewed_children_and_rejects_empty_choice(
    tmp_path,
):
    client, _ = make(tmp_path)
    with client:
        source, target, item = setup(client)
        kept = client.post(
            "/api/commitments",
            json={"title": "Keep", "capacityId": target["id"]},
            headers=HEADERS,
        ).json()
        review = preview(client, "capacities", source["id"])
        url = f"/api/capacities/{source['id']}/remove"
        assert (
            client.post(
                url,
                json={
                    "previewToken": review["previewToken"],
                    "confirmed": True,
                    "operation": "empty",
                },
                headers=HEADERS,
            ).status_code
            == 409
        )
        assert (
            client.post(
                url,
                json={
                    "previewToken": review["previewToken"],
                    "confirmed": True,
                    "operation": "cascade",
                },
                headers=HEADERS,
            ).status_code
            == 200
        )
        assert [c["id"] for c in client.get("/api/commitments").json()["items"]] == [
            kept["id"]
        ]
        assert (
            client.get(f"/api/commitments/{item['id']}/history").json()["items"] == []
        )


def test_same_capacity_and_missing_target_fail_without_loss(tmp_path):
    client, _ = make(tmp_path)
    with client:
        source, _, item = setup(client)
        review = preview(client, "capacities", source["id"])
        for target in [source["id"], "missing"]:
            response = client.post(
                f"/api/capacities/{source['id']}/remove",
                json={
                    "previewToken": review["previewToken"],
                    "confirmed": True,
                    "operation": "move",
                    "targetCapacityId": target,
                    "targetRevision": 1,
                },
                headers=HEADERS,
            )
            assert response.status_code in [404, 409]
        assert client.get("/api/commitments").json()["items"][0]["id"] == item["id"]


def test_deletion_cancels_push_and_cascades_jobs_but_keeps_import_provenance(tmp_path):
    import json
    import time

    client, _ = make(tmp_path)
    with client:
        _, _, item = setup(client)
        store = client.app.state.store
        with store.connect() as db:
            db.execute(
                "INSERT INTO reminder_jobs VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "job",
                    item["id"],
                    "2026-09-20",
                    time.time() + 3600,
                    "scheduled",
                    1,
                    0,
                    time.time(),
                    time.time(),
                ),
            )
            db.execute(
                "INSERT INTO push_devices VALUES (?,?,?,?,?,?)",
                ("device", "Fixture", "{}", "active", 0, 0),
            )
            db.execute(
                "INSERT INTO push_deliveries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "delivery",
                    "device",
                    "reminder",
                    "job",
                    1,
                    "pending",
                    0,
                    9999999999,
                    9999999999,
                    None,
                    0,
                    0,
                    None,
                ),
            )
            db.execute(
                "INSERT INTO import_records VALUES (?,?,?,?,?,?)",
                (
                    "source",
                    "commitment",
                    "old",
                    item["id"],
                    "fingerprint",
                    json.dumps({"title": "Original archive"}),
                ),
            )
        review = preview(client, "commitments", item["id"])
        assert review["reminders"] == 1
        response = client.post(
            f"/api/commitments/{item['id']}/remove",
            json={"previewToken": review["previewToken"], "confirmed": True},
            headers=HEADERS,
        )
        assert response.status_code == 200
        with store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM reminder_jobs").fetchone()[0] == 0
            delivery = db.execute(
                "SELECT state,reminder_id,reminder_revision FROM push_deliveries"
            ).fetchone()
            assert tuple(delivery) == ("cancelled", None, None)
            assert (
                "Original archive"
                in db.execute("SELECT raw FROM import_records").fetchone()[0]
            )
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("operation", ["move", "cascade"])
def test_capacity_failure_rolls_back_every_child_change(tmp_path, operation):
    import sqlite3

    client, _ = make(tmp_path)
    with client:
        source, target, item = setup(client)
        review = preview(client, "capacities", source["id"])
        with client.app.state.store.connect() as db:
            db.execute(
                "CREATE TRIGGER injected_failure BEFORE DELETE ON entities WHEN OLD.kind='capacity' BEGIN SELECT RAISE(ABORT,'fixture transaction failure'); END"
            )
        with pytest.raises(sqlite3.IntegrityError):
            client.post(
                f"/api/capacities/{source['id']}/remove",
                json={
                    "previewToken": review["previewToken"],
                    "confirmed": True,
                    "operation": operation,
                    **(
                        {"targetCapacityId": target["id"], "targetRevision": 1}
                        if operation == "move"
                        else {}
                    ),
                },
                headers=HEADERS,
            )
        assert len(client.get("/api/capacities").json()["items"]) == 2
        kept = client.get("/api/commitments").json()["items"][0]
        assert kept["capacityId"] == source["id"] and kept["revision"] == 1
        assert (
            len(client.get(f"/api/commitments/{item['id']}/history").json()["items"])
            == 1
        )


def test_empty_capacity_removal_requires_preview(tmp_path):
    client, _ = make(tmp_path)
    with client:
        login(client)
        item = client.post(
            "/api/capacities", json={"name": "Empty"}, headers=HEADERS
        ).json()
        review = preview(client, "capacities", item["id"])
        assert review["commitments"] == []
        result = client.post(
            f"/api/capacities/{item['id']}/remove",
            json={
                "previewToken": review["previewToken"],
                "confirmed": True,
                "operation": "empty",
            },
            headers=HEADERS,
        )
        assert result.status_code == 200
        assert client.get("/api/capacities").json()["items"] == []
