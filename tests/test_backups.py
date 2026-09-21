import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from leam_api.backups import Backups, restore, router
from leam_api.store import Store
from leam_api.vault import Vault


def prepared(path):
    store = Store(path)
    vault = Vault(path)
    store.set(
        "account_config:google",
        vault.seal("config:google", {"clientSecret": "fixture-secret"}),
    )
    store.set("password_hash", "fixture-password-hash")
    with store.connect() as db:
        db.execute("INSERT INTO sessions VALUES ('old-session',9999999999)")
    (path / "tools-token").write_text("x" * 48)
    (path / "tools-token").chmod(0o600)
    return store


def test_authenticated_export_restores_database_and_vault_without_replacing_current(
    tmp_path,
):
    source = tmp_path / "source"
    store = prepared(source)
    (source / "unrelated-secret").write_text("must not export")
    manager = Backups(store)
    app = FastAPI()
    app.include_router(router(manager))
    with TestClient(app) as client:
        response = client.post("/api/backups")
        assert response.status_code == 200, response.text
        item = response.json()
        download = client.get(f"/api/backups/{item['id']}/download")
        assert download.status_code == 200
        assert download.headers["cache-control"] == "no-store"
        archive = manager.path(item["id"])
        assert archive.stat().st_mode & 0o077 == 0
        with zipfile.ZipFile(archive) as z:
            assert set(z.namelist()) == {
                "manifest.json",
                "leam.sqlite3",
                "accounts-key",
                "tools-token",
            }
        target = tmp_path / "restored"
        result = restore(archive, target, source)
        assert Path(result["safetyBackup"]).is_file()
        restored = Store(target)
        assert (
            Vault(target).open("config:google", restored.get("account_config:google"))[
                "clientSecret"
            ]
            == "fixture-secret"
        )
        with restored.connect() as db:
            assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        with store.connect() as db:
            assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
        assert restored.get("password_hash") == "fixture-password-hash"
        assert (target / "tools-token").read_text() == "x" * 48
        with pytest.raises(ValueError, match="exist"):
            restore(archive, target, source)
        assert client.get("/api/backups/not-an-id/download").status_code == 404


def rewrite(source, target, mutate):
    with zipfile.ZipFile(source) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    mutate(files)
    with zipfile.ZipFile(target, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


@pytest.mark.parametrize(
    "change", ["path", "digest", "missing-key", "schema", "version"]
)
def test_restore_rejects_invalid_archives_before_touching_destination(tmp_path, change):
    source = tmp_path / "source"
    manager = Backups(prepared(source))
    archive = manager.path(manager.create()["id"])
    bad = tmp_path / "bad.zip"

    def mutate(files):
        manifest = json.loads(files["manifest.json"])
        if change == "path":
            files["../outside"] = b"bad"
        elif change == "digest":
            files["accounts-key"] = b"x" * 32
        elif change == "missing-key":
            del files["accounts-key"]
        elif change == "version":
            manifest["version"] = 99
        else:
            path = tmp_path / "bad.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE different(id TEXT)")
            files["leam.sqlite3"] = path.read_bytes()
            manifest["files"]["leam.sqlite3"] = {
                "size": len(files["leam.sqlite3"]),
                "sha256": hashlib.sha256(files["leam.sqlite3"]).hexdigest(),
            }
        files["manifest.json"] = json.dumps(manifest).encode()

    rewrite(archive, bad, mutate)
    target = tmp_path / "destination"
    with pytest.raises(ValueError):
        restore(bad, target, source)
    assert not target.exists()
    assert not (tmp_path / "outside").exists()


def test_backup_rejects_symlinked_key_and_unreadable_archive_paths(tmp_path):
    source = tmp_path / "source"
    manager = Backups(prepared(source))
    key = source / "accounts-key"
    original = key.read_bytes()
    key.unlink()
    outside = tmp_path / "outside-key"
    outside.write_bytes(original)
    key.symlink_to(outside)
    with pytest.raises(ValueError, match="regular"):
        manager.create()
    with pytest.raises(ValueError):
        manager.path("../outside-key")


def test_wrong_vault_key_with_valid_digest_is_rejected(tmp_path):
    source = tmp_path / "source"
    manager = Backups(prepared(source))
    archive = manager.path(manager.create()["id"])
    bad = tmp_path / "wrong-vault.zip"

    def mutate(files):
        files["accounts-key"] = b"z" * 32
        manifest = json.loads(files["manifest.json"])
        manifest["files"]["accounts-key"]["sha256"] = hashlib.sha256(
            files["accounts-key"]
        ).hexdigest()
        files["manifest.json"] = json.dumps(manifest).encode()

    rewrite(archive, bad, mutate)
    with pytest.raises(ValueError, match="vault"):
        restore(bad, tmp_path / "restored", source)


def test_restore_cli_and_restarted_authenticated_app(tmp_path):
    import subprocess
    import sys

    from test_api import FakeCodex, login

    from leam_api.app import create_app

    source = tmp_path / "source"
    origin = {"origin": "http://testserver"}
    app = create_app(
        source,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    manager = Backups(Store(source))
    app.include_router(router(manager))
    with TestClient(app) as client:
        assert client.get("/api/backups").status_code == 401
        login(client)
        assert client.post("/api/backups", json={}).status_code == 403
        saved = client.post("/api/backups", json={}, headers=origin)
        assert saved.status_code == 200
        item = saved.json()
        assert client.get(item["downloadUrl"]).status_code == 200
        old_session = client.cookies.get("leam_session")
    target = tmp_path / "restored"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "leam_api.backups",
            "--archive",
            str(manager.path(item["id"])),
            "--destination",
            str(target),
            "--current-data",
            str(source),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    receipt = json.loads(result.stdout)
    assert receipt["activated"] is False
    with TestClient(
        create_app(target, {"http://testserver"}, codex=FakeCodex())
    ) as client:
        client.cookies.set("leam_session", old_session)
        assert client.get("/api/today").status_code == 401
        assert (
            client.post(
                "/api/auth/login",
                json={"password": "long-password-for-tests"},
                headers=origin,
            ).status_code
            == 200
        )
        assert client.get("/api/today").status_code == 200


def test_duplicate_entries_executable_schema_and_expanded_size_are_rejected(tmp_path):
    source = tmp_path / "source"
    manager = Backups(prepared(source))
    archive = manager.path(manager.create()["id"])
    duplicate = tmp_path / "duplicate.zip"
    duplicate.write_bytes(archive.read_bytes())
    with zipfile.ZipFile(duplicate, "a") as file:
        with pytest.warns(UserWarning):
            file.writestr("accounts-key", b"a" * 32)
    with pytest.raises(ValueError, match="duplicate"):
        restore(duplicate, tmp_path / "duplicate-target", source)
    huge = tmp_path / "huge.zip"
    with zipfile.ZipFile(huge, "w", compression=zipfile.ZIP_DEFLATED) as file:
        file.writestr("manifest.json", "{}")
        file.writestr("leam.sqlite3", b"x")
        file.writestr("accounts-key", b"x" * 65537)
    with pytest.raises(ValueError, match="size"):
        restore(huge, tmp_path / "huge-target", source)
    with manager.store.connect() as db:
        db.execute(
            "CREATE TRIGGER unsupported AFTER INSERT ON events BEGIN DELETE FROM sessions; END"
        )
    with pytest.raises(ValueError, match="schema"):
        manager.create()


@pytest.mark.parametrize(
    "change",
    [
        "tls",
        "vapid",
        "primary-key",
        "short-token",
        "binary-token",
        "unicode-token",
        "newline-token",
    ],
)
def test_restore_rejects_unusable_identity_and_schema_with_valid_digests(
    tmp_path, change
):
    source = tmp_path / "source"
    manager = Backups(prepared(source))
    archive = manager.path(manager.create()["id"])
    bad = tmp_path / "bad.zip"

    def mutate(files):
        if change == "tls":
            files["mcp-tls/certificate.pem"] = b"invalid certificate"
            files["mcp-tls/key.pem"] = b"invalid private key"
        elif change == "short-token":
            files["tools-token"] = b"short"
        elif change == "unicode-token":
            files["tools-token"] = ("é" * 48).encode()
        elif change == "newline-token":
            files["tools-token"] = b"x" * 30 + b"\n" + b"x" * 30
        elif change == "binary-token":
            files["tools-token"] = b"\xff" * 48
        elif change == "vapid":
            files["push-vapid.pem"] = b"invalid signing key"
        else:
            dbpath = tmp_path / "bad.sqlite3"
            dbpath.write_bytes(files["leam.sqlite3"])
            with sqlite3.connect(dbpath) as db:
                db.executescript(
                    "ALTER TABLE settings RENAME TO old_settings; CREATE TABLE settings(key TEXT,value TEXT); INSERT INTO settings SELECT * FROM old_settings; DROP TABLE old_settings;"
                )
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.close()
            files["leam.sqlite3"] = dbpath.read_bytes()
        manifest = json.loads(files["manifest.json"])
        manifest["files"] = {
            name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in files.items()
            if name != "manifest.json"
        }
        files["manifest.json"] = json.dumps(manifest).encode()

    rewrite(archive, bad, mutate)
    target = tmp_path / "destination"
    with pytest.raises(ValueError):
        restore(bad, target, source)
    assert not target.exists()
