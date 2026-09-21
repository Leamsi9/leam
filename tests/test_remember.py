from test_api import login, make


def export():
    return {
        "version": 1,
        "selectedCapacityId": "c1",
        "customField": "preserve me",
        "capacities": [{"id": "c1", "name": "Health", "note": "Move", "record": "5k"}],
        "objectives": [
            {
                "id": "o1",
                "capacityId": "c1",
                "name": "Walk",
                "measure": "minutes",
                "target": 20,
                "startDate": "2026-09-01",
                "endDate": "2026-09-30",
                "time": "09:30",
                "reward": "Tea",
                "notes": "Outside",
                "snoozes": {},
                "lastReminder": {},
            }
        ],
        "dailyLogs": {
            "2026-09-20": {
                "o1": {"value": 7, "done": True, "preDoneValue": 7, "ts": 1789900000000}
            }
        },
    }


def test_remember_preview_import_repeat_and_original_preservation(tmp_path):
    client, _ = make(tmp_path)
    with client:
        login(client)
        h = {"origin": "http://testserver"}
        body = {
            "sourceId": "my-remember",
            "timezone": "Europe/London",
            "state": export(),
        }
        preview = client.post("/api/imports/remember/preview", json=body, headers=h)
        assert preview.status_code == 200, preview.text
        assert preview.json()["counts"] == {
            "capacities": 1,
            "commitments": 1,
            "logs": 1,
        }
        assert client.get("/api/commitments").json()["items"] == []
        applied = client.post(
            "/api/imports/remember",
            json={**body, "previewDigest": preview.json()["digest"]},
            headers=h,
        )
        assert applied.status_code == 200, applied.text
        backup = tmp_path / "backups" / applied.json()["backup"]
        assert backup.is_file()
        import sqlite3

        with sqlite3.connect(backup) as restored:
            assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert (
                restored.execute(
                    "SELECT COUNT(*) FROM entities WHERE kind='commitment'"
                ).fetchone()[0]
                == 0
            )
            assert (
                restored.execute(
                    "SELECT COUNT(*) FROM settings WHERE key='password_hash'"
                ).fetchone()[0]
                == 1
            )
        timezone_change = client.post(
            "/api/imports/remember/preview",
            json={**body, "timezone": "America/New_York"},
            headers=h,
        ).json()
        assert timezone_change["conflicts"]
        item = client.get("/api/today?date=2026-09-20").json()["items"][0]
        assert item["log"]["value"] == 7 and item["log"]["done"]
        assert item["log"]["preDoneValue"] == 7
        repeat = client.post(
            "/api/imports/remember",
            json={**body, "previewDigest": preview.json()["digest"]},
            headers=h,
        )
        assert repeat.status_code == 200
        assert len(client.get("/api/commitments").json()["items"]) == 1
        original = client.get("/api/imports/" + applied.json()["id"] + "/source").json()
        assert original["state"] == body["state"]
        changed = {**body, "state": export()}
        changed["state"]["objectives"][0]["name"] = "Changed elsewhere"
        p = client.post("/api/imports/remember/preview", json=changed, headers=h).json()
        assert p["conflicts"]
        assert (
            client.post(
                "/api/imports/remember",
                json={**changed, "previewDigest": p["digest"]},
                headers=h,
            ).status_code
            == 409
        )
        assert client.get("/api/commitments").json()["items"][0]["title"] == "Walk"


def test_invalid_remember_reference_and_unreviewed_payload_do_not_write(tmp_path):
    client, _ = make(tmp_path)
    with client:
        login(client)
        h = {"origin": "http://testserver"}
        body = {"sourceId": "test", "timezone": "Europe/London", "state": export()}
        body["state"]["objectives"][0]["capacityId"] = "missing"
        p = client.post("/api/imports/remember/preview", json=body, headers=h)
        assert p.status_code == 422
        body["state"] = export()
        assert (
            client.post(
                "/api/imports/remember",
                json={**body, "previewDigest": "wrong"},
                headers=h,
            ).status_code
            == 409
        )
        assert client.get("/api/capacities").json()["items"] == []
