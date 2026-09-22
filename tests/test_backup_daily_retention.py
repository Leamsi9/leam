"""Daily policy exercised through API, restore and concurrent operator callers."""

import multiprocessing
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_backups import prepared

from leam_api.backups import Backups, restore, router
from leam_api.store import Store


def timestamp(day, hour=12):
    return datetime(2026, 9, day, hour, tzinfo=ZoneInfo("Europe/London")).timestamp()


@pytest.fixture
def local_zone(monkeypatch):
    original = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Europe/London")
    time.tzset()
    yield
    if original is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = original
    time.tzset()


def test_api_reuses_daily_snapshot_and_retains_three(tmp_path, local_zone):
    store = prepared(tmp_path / "data")
    now = [timestamp(20)]
    manager = Backups(store, clock=lambda: now[0])
    app = FastAPI()
    app.include_router(router(manager))
    ids = []
    with TestClient(app) as client:
        for day in range(20, 25):
            now[0] = timestamp(day)
            response = client.post("/api/backups")
            assert response.status_code == 200, response.text
            first = response.json()
            assert first["reused"] is False
            ids.append(first["id"])
            store.set("later-change", day)
            repeat = client.post("/api/backups").json()
            assert repeat["reused"] is True
            assert repeat["id"] == first["id"]
            assert repeat["createdAt"] == first["createdAt"]
            assert len(client.get("/api/backups").json()["items"]) == min(3, day - 19)
        assert [row["id"] for row in manager.list()["items"]] == ids[-1:-4:-1]
        assert client.get(f"/api/backups/{ids[0]}/download").status_code == 404
        saved = client.get(f"/api/backups/{ids[-1]}/download")
        assert saved.status_code == 200
        assert saved.headers["cache-control"] == "no-store"
        assert int(saved.headers["content-length"]) == len(saved.content)


def test_failed_creation_and_corrupt_reuse_do_not_prune(
    tmp_path, local_zone, monkeypatch
):
    manager = Backups(prepared(tmp_path / "data"), clock=lambda: timestamp(20))
    first = manager.create()["id"]
    # Legacy installations can already have more than three archives.
    import shutil
    import uuid

    for day in (16, 17, 18, 19):
        path = manager.path(str(uuid.uuid4()))
        shutil.copyfile(manager.path(first), path)
        path.chmod(0o600)
        os.utime(path, (timestamp(day), timestamp(day)))
    before = {p.name: p.stat().st_size for p in manager.directory.glob("*.zip")}
    manager.clock = lambda: timestamp(21)
    from leam_api import backups

    monkeypatch.setattr(backups, "MAX_BYTES", 1)
    with pytest.raises(ValueError, match="limit"):
        manager.create()
    assert {p.name: p.stat().st_size for p in manager.directory.glob("*.zip")} == before
    monkeypatch.undo()
    manager.clock = lambda: timestamp(20)
    manager.path(first).write_bytes(b"broken")
    os.utime(manager.path(first), (timestamp(20), timestamp(20)))
    with pytest.raises(ValueError, match="archive"):
        manager.create()
    assert len(list(manager.directory.glob("*.zip"))) == 5


def test_changed_keys_refuse_reuse_without_second_archive(tmp_path, local_zone):
    store = prepared(tmp_path / "data")
    manager = Backups(store, clock=lambda: timestamp(20))
    first = manager.create()["id"]
    (store.path.parent / "tools-token").write_text("y" * 48)
    with pytest.raises(ValueError, match="different installation keys"):
        manager.create()
    assert [row["id"] for row in manager.list()["items"]] == [first]


def process_create(directory, start, queue):
    start.wait(10)
    try:
        result = Backups(Store(Path(directory))).create()
        queue.put(("ok", result["id"]))
    except (OSError, ValueError) as error:
        queue.put(("error", type(error).__name__))


def test_concurrent_processes_create_exactly_one_archive(tmp_path):
    store = prepared(tmp_path / "data")
    context = multiprocessing.get_context("fork")
    start, queue = context.Event(), context.Queue()
    processes = [
        context.Process(
            target=process_create, args=(str(store.path.parent), start, queue)
        )
        for _ in range(3)
    ]
    try:
        for process in processes:
            process.start()
        start.set()
        results = [queue.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(30)
        assert all(result[0] == "ok" for result in results), results
        assert len({result[1] for result in results}) == 1
        assert len(Backups(store).list()["items"]) == 1
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(5)
        queue.close()


def test_open_download_survives_retention_and_restore_reuses_today(
    tmp_path, local_zone
):
    store = prepared(tmp_path / "data")
    now = [timestamp(20)]
    manager = Backups(store, clock=lambda: now[0])
    first = manager.create()["id"]
    with manager.open_download(first) as opened:
        expected = opened.read()
        opened.seek(0)
        for day in (21, 22, 23):
            now[0] = timestamp(day)
            manager.create()
        assert not manager.path(first).exists()
        assert opened.read() == expected
    # Real-clock snapshot exercises restore's independently constructed manager.
    current = Backups(store)
    saved = current.create()
    result = restore(
        current.path(saved["id"]), tmp_path / "restored", store.path.parent
    )
    assert Path(result["safetyBackup"]).stem == saved["id"]
    assert len(current.list()["items"]) == 3
    assert Store(tmp_path / "restored").get("password_hash") == "fixture-password-hash"


def test_local_calendar_day_observes_dst_and_midnight(tmp_path, local_zone):
    store = prepared(tmp_path / "data")
    now = [datetime(2026, 10, 25, 0, 15, tzinfo=ZoneInfo("UTC")).timestamp()]
    manager = Backups(store, clock=lambda: now[0])
    first = manager.create()["id"]
    now[0] = datetime(2026, 10, 25, 1, 15, tzinfo=ZoneInfo("UTC")).timestamp()
    assert manager.create()["id"] == first  # repeated 01:15 after autumn rollback
    now[0] = datetime(2026, 10, 26, 0, 1, tzinfo=ZoneInfo("UTC")).timestamp()
    assert manager.create()["id"] != first


def test_symlink_lock_rejected_without_touching_target(tmp_path):
    manager = Backups(prepared(tmp_path / "data"))
    target = tmp_path / "unrelated"
    target.write_text("preserved")
    (manager.directory / ".policy.lock").symlink_to(target)
    with pytest.raises(OSError):
        manager.create()
    assert target.read_text() == "preserved"
    assert not manager.list()["items"]


def test_operator_predeployment_defers_retention_until_normal_create(
    tmp_path, local_zone
):
    manager = Backups(prepared(tmp_path / "data"), clock=lambda: timestamp(20))
    ids = []
    for day in (20, 21, 22, 23):
        manager.clock = lambda day=day: timestamp(day)
        ids.append(manager.create(prune=False)["id"])
    assert len(manager.list()["items"]) == 4
    assert manager.create()["id"] == ids[-1]
    assert len(manager.list()["items"]) == 3
