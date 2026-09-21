"""Real archive callers for the additive email table; all data is synthetic."""

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_backups import rewrite
from test_mobile_restore import Services, fixture
from test_recovery import Control
from test_recovery import setup as pair

from leam_api.backups import Backups, restore
from leam_api.mobile_restore import RestoreController
from leam_api.recovery import create_recovery_app
from leam_api.store import Store
from leam_api.vault import Vault

ACCOUNT = "synthetic-google-account"
HEADERS = {"origin": "http://testserver"}


def mailbox(store, *, legacy=False):
    vault = Vault(store.path.parent)
    credentials = {
        "token": {
            "access_token": "synthetic-calendar-access",
            "refresh_token": "synthetic-calendar-refresh",
            "scope": "https://www.googleapis.com/auth/calendar",
        }
    }
    if not legacy:
        credentials["email"] = {
            "token": {
                "access_token": "synthetic-mail-access",
                "refresh_token": "synthetic-mail-refresh",
                "scope": "https://www.googleapis.com/auth/gmail.readonly",
            }
        }
    snapshot = {
        "items": [
            {
                "id": "message-a",
                "threadId": "thread-a",
                "accountId": ACCOUNT,
                "subject": "Synthetic subject",
                "from": "sender@example.test",
                "snippet": "Synthetic snippet",
                "receivedAt": "2026-09-21T00:00:00+00:00",
                "unread": True,
                "important": False,
                "url": "https://mail.google.com/mail/#all/thread-a",
                "futureItemMetadata": {"version": 1},
            }
        ],
        "truncated": False,
        "futureSnapshotMetadata": {"version": 1},
    }
    with store.connect() as db:
        db.execute(
            "INSERT INTO accounts VALUES (?,?,?,?,?,NULL,NULL,?,?)",
            (
                ACCOUNT,
                "google",
                "owner@example.test",
                vault.seal("account:" + ACCOUNT, credentials),
                "connected",
                1,
                "subject-a",
            ),
        )
        if not legacy:
            db.execute(
                "INSERT INTO email_snapshots VALUES (?,?,?,NULL)",
                (ACCOUNT, vault.seal("email-snapshot:" + ACCOUNT, snapshot), 1),
            )
    return vault, credentials, snapshot


def change_archive(manager, backup, tmp_path, change):
    """Alter only synthetic SQLite, then recompute the actual archive digest."""
    path = manager.path(backup["id"])
    replacement = tmp_path / "replacement.zip"

    def mutate(files):
        database = tmp_path / "archive.sqlite3"
        database.write_bytes(files["leam.sqlite3"])
        db = sqlite3.connect(database)
        try:
            db.execute("PRAGMA journal_mode=DELETE")
            change(db)
            db.commit()
        finally:
            db.close()
        files["leam.sqlite3"] = database.read_bytes()
        manifest = json.loads(files["manifest.json"])
        manifest["files"]["leam.sqlite3"] = {
            "size": len(files["leam.sqlite3"]),
            "sha256": hashlib.sha256(files["leam.sqlite3"]).hexdigest(),
        }
        files["manifest.json"] = json.dumps(manifest).encode()

    rewrite(path, replacement, mutate)
    replacement.chmod(0o600)
    os.replace(replacement, path)
    return path


@pytest.mark.parametrize("legacy", [True, False], ids=["pre-email", "current-email"])
def test_recovery_preview_and_restore_keep_credentials_and_initialize_mail(
    tmp_path, legacy
):
    roots, store, deployment, runtime = fixture(tmp_path)
    _, credentials, snapshot = mailbox(store, legacy=legacy)
    manager = Backups(store)
    backup = manager.create()
    if legacy:
        # Store's additive mail schema is the only difference being rehearsed.
        change_archive(
            manager,
            backup,
            tmp_path,
            lambda db: db.execute("DROP TABLE email_snapshots"),
        )
    source_before = store.entities("memory")
    services = Services(runtime)
    controller = RestoreController(deployment, services)
    app = create_recovery_app(
        roots.recovery, {"http://testserver"}, control=Control(), restore=controller
    )
    with TestClient(app, client=("127.0.0.1", 4444)) as client:
        pair(client, roots.recovery)
        preview = client.post(
            "/api/restores/preview", json={"backupId": backup["id"]}, headers=HEADERS
        )
        assert preview.status_code == 200, preview.text
        assert not services.calls
        request_id = str(uuid4())
        accepted = client.post(
            "/api/restores",
            headers=HEADERS,
            json={
                "backupId": backup["id"],
                "requestId": request_id,
                "previewToken": preview.json()["previewToken"],
                "confirmed": True,
            },
        )
        assert accepted.status_code == 202, accepted.text
        client.portal.call(controller.wait, request_id)
        receipt = client.get("/api/restores/" + request_id).json()
        assert receipt["state"] == "ready_for_uat"
    destination = Path(deployment.read()["dataDirectory"])
    assert destination != store.path.parent
    # Same additive initialization as application startup.
    restored = Store(destination)
    restored_vault = Vault(destination)
    with restored.connect() as db:
        saved = db.execute(
            "SELECT body FROM accounts WHERE id=?", (ACCOUNT,)
        ).fetchone()
        rows = db.execute("SELECT body FROM email_snapshots").fetchall()
    assert restored_vault.open("account:" + ACCOUNT, saved["body"]) == credentials
    if legacy:
        assert rows == [] and "email" not in credentials
    else:
        assert len(rows) == 1
        assert (
            restored_vault.open("email-snapshot:" + ACCOUNT, rows[0]["body"])
            == snapshot
        )
    assert restored_vault.open(
        "config:google", restored.get("account_config:google")
    ) == {"clientSecret": "fixture-secret"}
    assert store.entities("memory") == source_before
    with store.connect() as db:
        original = db.execute(
            "SELECT body FROM accounts WHERE id=?", (ACCOUNT,)
        ).fetchone()
    assert (
        Vault(store.path.parent).open("account:" + ACCOUNT, original["body"])
        == credentials
    )
    assert services.calls == ["stop", "start"]
    assert (
        store.path.parent / "backups" / (receipt["safetyBackupId"] + ".zip")
    ).is_file()


@pytest.mark.parametrize(
    "damage",
    [
        "schema",
        "required-table",
        "wrong-label",
        "wrong-key",
        "corrupt-ciphertext",
        "not-object",
        "items-not-list",
        "too-many-items",
        "item-not-object",
        "truncated-type",
    ],
)
def test_recovery_preview_and_offline_restore_reject_present_invalid_mail(
    tmp_path, damage
):
    roots, store, deployment, runtime = fixture(tmp_path)
    vault, _, snapshot = mailbox(store)
    manager = Backups(store)
    backup = manager.create()

    def damage_database(db):
        if damage == "schema":
            db.execute("ALTER TABLE email_snapshots ADD COLUMN unsupported TEXT")
            return
        if damage == "required-table":
            db.execute("DROP TABLE calendar_scans")
            return
        label = "email-snapshot:" + ACCOUNT
        if damage == "wrong-label":
            value = vault.seal("email-snapshot:another-account", snapshot)
        elif damage == "wrong-key":
            value = Vault(tmp_path).seal(label, snapshot)
        elif damage == "corrupt-ciphertext":
            value = "invalid-ciphertext"
        else:
            malformed = {
                "not-object": [],
                "items-not-list": {"items": {}, "truncated": False},
                "too-many-items": {"items": snapshot["items"] * 21, "truncated": False},
                "item-not-object": {"items": ["invalid"], "truncated": False},
                "truncated-type": {"items": [], "truncated": "false"},
            }[damage]
            value = vault.seal(label, malformed)
        db.execute("UPDATE email_snapshots SET body=?", (value,))

    archive = change_archive(manager, backup, tmp_path, damage_database)
    services = Services(runtime)
    controller = RestoreController(deployment, services)
    before = deployment.read()
    app = create_recovery_app(
        roots.recovery, {"http://testserver"}, control=Control(), restore=controller
    )
    with TestClient(app, client=("127.0.0.1", 4444)) as client:
        pair(client, roots.recovery)
        rejected = client.post(
            "/api/restores/preview", json={"backupId": backup["id"]}, headers=HEADERS
        )
        assert rejected.status_code == 409, rejected.text
        assert "Synthetic snippet" not in rejected.text
    destination = tmp_path / "rejected-restore"
    with pytest.raises(ValueError):
        restore(archive, destination, store.path.parent)
    assert not destination.exists()
    assert deployment.read() == before
    assert not services.calls and not list(roots.data.glob("restored-*"))
