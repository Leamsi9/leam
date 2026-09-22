"""Small-limit caller regressions for bounded large-product backups."""

import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_backups import prepared

from leam_api import backups
from leam_api.backups import Backups, restore, router


def test_export_and_restore_stream_payload_without_whole_file_reads(
    tmp_path, monkeypatch
):
    source = tmp_path / "source"
    store = prepared(source)
    store.set("scale-fixture", "x" * 400000)
    manager = Backups(store)
    # Model a raised limit with a small fixture exceeding its former ceiling.
    monkeypatch.setattr(backups, "MAX_BYTES", 2 * 1024 * 1024)
    monkeypatch.setattr(backups, "CHUNK_BYTES", 16384)
    original_read = backups.private_read
    original_zip_read = zipfile.ZipFile.read

    def guarded_private_read(path, *args):
        assert path.name != "leam.sqlite3"
        return original_read(path, *args)

    def guarded_zip_read(self, name, *args, **kwargs):
        assert name == "manifest.json", "Payload must use chunked ZipExtFile reads"
        return original_zip_read(self, name, *args, **kwargs)

    monkeypatch.setattr(backups, "private_read", guarded_private_read)
    monkeypatch.setattr(zipfile.ZipFile, "read", guarded_zip_read)
    app = FastAPI()
    app.include_router(router(manager))
    with TestClient(app) as client:
        exported = client.post("/api/backups")
        assert exported.status_code == 200
        key = exported.json()["id"]
    archive = manager.path(key)
    assert archive.stat().st_size > 256 * 1024
    with zipfile.ZipFile(archive) as zipped:
        manifest = json.loads(zipped.read("manifest.json"))
        assert manifest["version"] == 1
        assert manifest["files"]["leam.sqlite3"]["size"] > 256 * 1024
    target = tmp_path / "restored"
    result = restore(archive, target, source)
    assert result["activated"] is False
    from leam_api.store import Store

    assert Store(target).get("scale-fixture") == "x" * 400000


def test_create_total_payload_cap_leaves_no_published_archive(tmp_path, monkeypatch):
    store = prepared(tmp_path / "source")
    manager = Backups(store)
    monkeypatch.setattr(backups, "MAX_BYTES", 32)
    with pytest.raises(ValueError, match="limit"):
        manager.create()
    assert manager.list()["items"] == []
    assert [p.name for p in manager.directory.iterdir()] == [".policy.lock"]


def test_restore_rejects_aggregate_expansion_before_destination_publish(
    tmp_path, monkeypatch
):
    source = tmp_path / "source"
    manager = Backups(prepared(source))
    archive = manager.path(manager.create()["id"])
    with zipfile.ZipFile(archive) as zipped:
        size = sum(
            i.file_size for i in zipped.infolist() if i.filename != "manifest.json"
        )
    # Every entry alone fits; aggregate product bytes exceed the chosen cap.
    monkeypatch.setattr(backups, "MAX_BYTES", size - 1)
    target = tmp_path / "restored"
    with pytest.raises(ValueError, match="expanded size"):
        restore(archive, target, source)
    assert not target.exists()
    assert not list(tmp_path.glob(".leam-restore-*"))


def test_compressed_bomb_is_rejected_before_extraction(tmp_path, monkeypatch):
    source = tmp_path / "source"
    prepared(source)
    monkeypatch.setattr(backups, "MAX_BYTES", 10000)
    archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr("manifest.json", "{}")
        zipped.writestr("leam.sqlite3", b"0" * 10001)
        zipped.writestr("accounts-key", b"x" * 32)
    with pytest.raises(ValueError, match="expanded size"):
        restore(archive, tmp_path / "restored", source)
    assert not (tmp_path / "restored").exists()


def test_stream_copy_enforces_actual_bytes_even_if_source_grows(monkeypatch):
    monkeypatch.setattr(backups, "CHUNK_BYTES", 8)

    class Source(io.BytesIO):
        def read(self, n=-1):
            assert 0 < n <= 8
            return super().read(n)

    output = io.BytesIO()
    with pytest.raises(ValueError, match="size limit"):
        backups.stream_copy(Source(b"x" * 25), output, 20)
    assert len(output.getvalue()) <= 20


def test_private_stream_copy_rejects_symlinks_and_preserves_permissions(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"fixture")
    source.chmod(0o600)
    alias = tmp_path / "alias"
    alias.symlink_to(source)
    with pytest.raises(ValueError, match="private file"):
        backups.private_copy(alias, tmp_path / "rejected", 100)
    target = tmp_path / "copied"
    result = backups.private_copy(source, target, 100)
    assert result["size"] == 7 and target.read_bytes() == b"fixture"
    assert target.stat().st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        backups.private_copy(source, target, 100)


@pytest.mark.asyncio
async def test_mobile_preview_and_restore_keep_stable_streamed_copy(
    tmp_path, monkeypatch
):
    from uuid import uuid4

    from test_mobile_restore import Services, fixture

    from leam_api import mobile_restore

    _roots, store, deployment, runtime = fixture(tmp_path)
    backup = Backups(store).create()
    original_read = mobile_restore.private_read

    def forbid_archive_read(path, *args):
        assert path.suffix != ".zip"
        return original_read(path, *args)

    monkeypatch.setattr(mobile_restore, "private_read", forbid_archive_read)
    controller = mobile_restore.RestoreController(deployment, Services(runtime))
    preview = await controller.preview(backup["id"])
    assert len(preview["archiveSha256"]) == 64
    assert not list(controller.directory.glob(".verify-*"))
    request = str(uuid4())
    await controller.start_restore(request, backup["id"], preview["previewToken"])
    await controller.wait(request)
    assert controller.status(request)["state"] == "ready_for_uat"
    assert not list(controller.directory.glob(".restore-*"))
    assert not list(controller.directory.glob(".verify-*"))
