from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from leam_api.store import Store
from leam_api.updates import QA, Publication, Updates, router


def service(tmp_path):
    updates = Updates(Store(tmp_path))
    app = FastAPI()
    app.include_router(router(updates))
    return updates, TestClient(app)


def publish(updates, identity="build-1", feature="routines"):
    return updates.publish(
        Publication(
            feature=feature,
            title="Local routines",
            summary="Create and pause local reminder routines.",
            deploymentId=identity,
            deployedAt=datetime.now(UTC),
        )
    )


def test_qa_cannot_claim_user_acceptance_and_unread_tracks_each_change(tmp_path):
    updates, client = service(tmp_path)
    item = publish(updates)
    assert item["qa"]["state"] == item["uat"]["state"] == "pending"
    assert not item["completed"]
    assert item["stage"] == "Deployed"
    listing = client.get("/api/updates").json()
    assert listing["unreadCount"] == 1
    assert (
        client.post(
            "/api/updates/seen", json={"sequence": listing["sequence"]}
        ).status_code
        == 200
    )
    assert client.get("/api/updates/status").json()["unreadCount"] == 0
    reviewed = updates.qa(
        item["id"],
        QA(
            deploymentId="build-1",
            state="passed",
            details="Phone and desktop checks passed",
        ),
    )
    assert reviewed["uat"]["state"] == "pending" and not reviewed["completed"]
    assert reviewed["stage"] == "UAT"
    assert client.get("/api/updates/status").json()["unreadCount"] == 1
    accepted = client.post(
        f"/api/updates/{item['id']}/uat",
        json={
            "revision": reviewed["revision"],
            "deploymentId": "build-1",
            "state": "passed",
            "details": "Works on my phone",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["completed"]
    assert accepted.json()["stage"] == "Complete"
    failed = client.post(
        f"/api/updates/{item['id']}/uat",
        json={
            "revision": accepted.json()["revision"],
            "deploymentId": "build-1",
            "state": "failed",
            "details": "Actually the edit did not save",
        },
    )
    assert failed.status_code == 200 and not failed.json()["completed"]
    assert failed.json()["stage"] == "Fail" and failed.json()["awaitingUserInput"]
    assert failed.json()["uat"]["details"] == "Actually the edit did not save"
    assert (
        updates.qa(
            item["id"], QA(deploymentId="build-1", state="passed", details="Retested")
        )["uat"]["state"]
        == "failed"
    )


def test_new_deployment_does_not_inherit_uat_and_stale_actions_are_rejected(tmp_path):
    updates, client = service(tmp_path)
    first = updates.qa(
        publish(updates)["id"], QA(deploymentId="build-1", state="passed")
    )
    payload = {
        "revision": first["revision"],
        "deploymentId": "build-1",
        "state": "passed",
    }
    assert (
        client.post(f"/api/updates/{first['id']}/uat", json=payload).status_code == 200
    )
    assert (
        client.post(f"/api/updates/{first['id']}/uat", json=payload).status_code == 409
    )
    second = publish(updates, "build-2")
    assert second["uat"]["state"] == "pending" and second["qa"]["state"] == "pending"
    assert second["id"] != first["id"]
    assert (
        client.post(
            f"/api/updates/{second['id']}/uat",
            json={**payload, "deploymentId": "build-1"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/updates/{first['id']}/uat", json={**payload, "revision": 3}
        ).status_code
        == 409
    )
    assert (
        client.post("/api/updates/seen", json={"sequence": 100000}).status_code == 409
    )
    _, restarted = service(tmp_path)
    items = restarted.get("/api/updates").json()["items"]
    assert [item["id"] for item in items] == [second["id"]]
    with updates.store.connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM deployment_update_events WHERE update_id=?",
                (first["id"],),
            ).fetchone()[0]
            == 3
        )


def test_updates_router_uses_production_auth_and_origin(tmp_path):
    from test_api import FakeCodex, login

    from leam_api.app import create_app

    updates = Updates(Store(tmp_path))
    item = updates.qa(
        publish(updates)["id"], QA(deploymentId="build-1", state="passed")
    )
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    app.include_router(router(updates))
    with TestClient(app) as client:
        assert client.get("/api/updates").status_code == 401
        seen_url = f"/api/updates/{item['id']}/seen"
        seen_payload = {"sequence": item["sequence"]}
        assert (
            client.post(
                seen_url, json=seen_payload, headers={"origin": "http://testserver"}
            ).status_code
            == 401
        )
        login(client)
        assert client.post(seen_url, json=seen_payload).status_code == 403
        assert (
            client.post(
                seen_url,
                json=seen_payload,
                headers={"origin": "https://foreign.invalid"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                seen_url, json=seen_payload, headers={"origin": "http://testserver"}
            ).status_code
            == 200
        )
        payload = {
            "revision": item["revision"],
            "deploymentId": "build-1",
            "state": "passed",
        }
        assert (
            client.post(f"/api/updates/{item['id']}/uat", json=payload).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/updates/{item['id']}/uat",
                json=payload,
                headers={"origin": "http://testserver"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/updates", json={}, headers={"origin": "http://testserver"}
            ).status_code
            == 405
        )


def test_cli_publication_and_qa_leave_uat_pending(tmp_path):
    import json
    import subprocess
    import sys

    Store(tmp_path)
    summary = tmp_path / "summary.txt"
    summary.write_text("Isolated CLI deployment receipt")
    deployed = datetime.now(UTC).isoformat()
    command = [
        sys.executable,
        "-m",
        "leam_api.updates",
        "--data-dir",
        str(tmp_path),
        "publish",
        "--feature",
        "updates",
        "--title",
        "Updates",
        "--summary-file",
        str(summary),
        "--deployment-id",
        "sha256:fixture",
        "--deployed-at",
        deployed,
    ]
    first = subprocess.run(command, check=True, capture_output=True, text=True)
    item = json.loads(first.stdout)
    duplicate = json.loads(
        subprocess.run(command, check=True, capture_output=True, text=True).stdout
    )
    assert duplicate == item
    review = subprocess.run(
        [
            sys.executable,
            "-m",
            "leam_api.updates",
            "--data-dir",
            str(tmp_path),
            "qa",
            "--id",
            item["id"],
            "--deployment-id",
            "sha256:fixture",
            "--state",
            "passed",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    reviewed = json.loads(review.stdout)
    assert reviewed["qa"]["state"] == "passed" and reviewed["uat"]["state"] == "pending"
    assert not reviewed["completed"]


def test_bounded_validation_pagination_and_stale_seen_do_not_hide_new_updates(tmp_path):
    updates, client = service(tmp_path)
    first = publish(updates)
    sequence = client.get("/api/updates/status").json()["sequence"]
    second = publish(updates, "build-2", "another-feature")
    client.post("/api/updates/seen", json={"sequence": sequence})
    assert client.get("/api/updates/status").json()["unreadCount"] == 1
    page = client.get("/api/updates?limit=1").json()
    assert page["items"][0]["id"] == second["id"]
    older = client.get(f"/api/updates?limit=1&before={page['nextCursor']}").json()
    assert older["items"][0]["id"] == first["id"] and older["nextCursor"] is None
    url = f"/api/updates/{second['id']}/uat"
    payload = {"revision": 1, "deploymentId": "build-2", "state": "passed"}
    assert client.post(url, json={**payload, "qa": "passed"}).status_code == 422
    assert client.post(url, json={**payload, "details": "x" * 10001}).status_code == 422
    assert client.get("/api/updates?limit=101").status_code == 422


def test_qa_failure_and_pending_gate_user_pass(tmp_path):
    updates, client = service(tmp_path)
    item = publish(updates)
    url = f"/api/updates/{item['id']}/uat"
    assert (
        client.post(
            url, json={"revision": 1, "deploymentId": "build-1", "state": "passed"}
        ).status_code
        == 409
    )
    failed = updates.qa(
        item["id"],
        QA(deploymentId="build-1", state="failed", details="Integration smoke failed"),
    )
    assert failed["stage"] == "QA" and not failed["completed"]
    assert failed["qa"]["details"] == "Integration smoke failed"
    assert not failed["awaitingUserInput"]
    assert (
        client.post(
            url,
            json={
                "revision": failed["revision"],
                "deploymentId": "build-1",
                "state": "passed",
            },
        ).status_code
        == 409
    )


def test_explicit_ticket_read_persists_and_does_not_ack_other_or_newer_changes(
    tmp_path,
):
    updates, client = service(tmp_path)
    first = publish(updates)
    other = publish(updates, "build-other", "other")
    listing = client.get("/api/updates").json()
    assert all(item["unread"] for item in listing["items"])
    url = f"/api/updates/{first['id']}/seen"
    assert client.post(url, json={"sequence": first["sequence"]}).status_code == 200
    _, restarted = service(tmp_path)
    rows = {row["id"]: row for row in restarted.get("/api/updates").json()["items"]}
    assert not rows[first["id"]]["unread"] and rows[other["id"]]["unread"]
    assert restarted.get("/api/updates/status").json()["unreadCount"] == 1
    reviewed = updates.qa(first["id"], QA(deploymentId="build-1", state="passed"))
    assert client.post(url, json={"sequence": first["sequence"]}).status_code == 200
    assert client.get("/api/updates/status").json()["unreadCount"] == 2
    assert (
        client.post(url, json={"sequence": reviewed["sequence"] + 1}).status_code == 409
    )
    accepted = client.post(
        f"/api/updates/{first['id']}/uat",
        json={
            "revision": reviewed["revision"],
            "deploymentId": "build-1",
            "state": "passed",
        },
    )
    assert accepted.status_code == 200 and not accepted.json()["unread"]
    assert client.get("/api/updates/status").json()["unreadCount"] == 1
    latest = publish(updates, "build-2")
    listing = client.get("/api/updates").json()
    assert {item["id"] for item in listing["items"]} == {latest["id"], other["id"]}
    assert listing["unreadCount"] == 2
    assert client.post(url, json={"sequence": first["sequence"]}).status_code == 409


def test_obsolete_receipts_never_fill_visible_pages_or_unread_count(tmp_path):
    updates, client = service(tmp_path)
    for number in range(4):
        publish(updates, f"build-{number}")
    listing = client.get("/api/updates?limit=1").json()
    assert len(listing["items"]) == 1
    assert listing["nextCursor"] is None and listing["unreadCount"] == 1
    assert listing["items"][0]["deploymentId"] == "build-3"


def test_prune_cli_requires_apply_preserves_current_uat_and_audit_and_backs_up(
    tmp_path,
):
    import json
    import sqlite3
    import subprocess
    import sys
    import zipfile

    from leam_api.vault import Vault

    updates, client = service(tmp_path)
    Vault(tmp_path)
    old = publish(updates)
    current = updates.qa(
        publish(updates, "build-2")["id"], QA(deploymentId="build-2", state="passed")
    )
    accepted = client.post(
        f"/api/updates/{current['id']}/uat",
        json={
            "revision": current["revision"],
            "deploymentId": "build-2",
            "state": "passed",
        },
    ).json()
    with updates.store.connect() as db:
        audit_count = db.execute(
            "SELECT count(*) FROM deployment_update_events"
        ).fetchone()[0]
    command = [
        sys.executable,
        "-m",
        "leam_api.updates",
        "--data-dir",
        str(tmp_path),
        "prune-obsolete",
    ]
    preview = json.loads(
        subprocess.run(command, check=True, capture_output=True, text=True).stdout
    )
    assert preview["candidateIds"] == [old["id"]] and preview["deleted"] == 0
    assert not (tmp_path / "backups").exists()
    result = json.loads(
        subprocess.run(
            [*command, "--apply"], check=True, capture_output=True, text=True
        ).stdout
    )
    assert result["deleted"] == 1
    archive = tmp_path / "backups" / (result["backupId"] + ".zip")
    assert archive.is_file() and archive.stat().st_mode & 0o077 == 0
    with zipfile.ZipFile(archive) as z:
        snapshot = tmp_path / "inspected-snapshot.sqlite3"
        snapshot.write_bytes(z.read("leam.sqlite3"))
        connection = sqlite3.connect(snapshot)
        assert (
            connection.execute("SELECT count(*) FROM deployment_updates").fetchone()[0]
            == 2
        )
        connection.close()
    with updates.store.connect() as db:
        assert db.execute("SELECT count(*) FROM deployment_updates").fetchone()[0] == 1
        assert (
            db.execute("SELECT count(*) FROM deployment_update_events").fetchone()[0]
            == audit_count
        )
    assert client.get("/api/updates").json()["items"][0] == accepted
    repeated = json.loads(
        subprocess.run(
            [*command, "--apply"], check=True, capture_output=True, text=True
        ).stdout
    )
    assert repeated["deleted"] == 0 and repeated["backupId"] is None


def test_failed_prune_backup_cannot_delete_receipts(tmp_path, monkeypatch):
    import pytest

    from leam_api.backups import Backups

    updates, _ = service(tmp_path)
    publish(updates)
    publish(updates, "build-2")

    def fail_backup(_):
        raise OSError("disk unavailable")

    monkeypatch.setattr(Backups, "create", fail_backup)
    with pytest.raises(OSError):
        updates.prune_obsolete(apply=True)
    with updates.store.connect() as db:
        assert db.execute("SELECT count(*) FROM deployment_updates").fetchone()[0] == 2


def test_prune_retains_receipt_superseded_after_backup(tmp_path, monkeypatch):
    from leam_api.backups import Backups
    from leam_api.vault import Vault

    updates, _ = service(tmp_path)
    Vault(tmp_path)
    old = publish(updates)
    second = publish(updates, "build-2")
    original_create = Backups.create

    def concurrent_publication(manager):
        archive = original_create(manager)
        publish(updates, "build-3")
        return archive

    monkeypatch.setattr(Backups, "create", concurrent_publication)
    result = updates.prune_obsolete(apply=True)
    assert result["candidateIds"] == [old["id"]] and result["deleted"] == 1
    with updates.store.connect() as db:
        remaining = {row[0] for row in db.execute("SELECT id FROM deployment_updates")}
        assert second["id"] in remaining and len(remaining) == 2
