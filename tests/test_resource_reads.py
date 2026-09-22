"""Caller coverage for explicit, snapshot-bounded Resource acknowledgments."""

from concurrent.futures import ThreadPoolExecutor

from test_api import login
from test_artifacts import client_for
from test_backups import prepared
from test_resources import ORIGIN, publish

from leam_api.artifacts import Artifacts
from leam_api.backups import Backups, restore
from leam_api.resource_reads import ResourceRead, ResourceReads
from leam_api.store import Store


def test_reads_require_auth_origin_and_never_acknowledge_on_get(tmp_path):
    with client_for(tmp_path) as client:
        assert client.get("/api/artifacts/_status").status_code == 401
        assert (
            client.post(
                "/api/artifacts/_read", json={"ids": []}, headers=ORIGIN
            ).status_code
            == 401
        )
        login(client)
        assert client.post("/api/artifacts/_read", json={"ids": []}).status_code == 403
        assert publish(client).status_code == 200
        status = client.get("/api/artifacts/_status")
        assert status.headers["cache-control"] == "no-store"
        assert status.json() == {"total": 1, "unreadCount": 1, "unreadIds": ["report"]}
        for path in [
            "/api/artifacts",
            "/api/artifacts/report",
            "/api/artifacts/report/download",
        ]:
            assert client.get(path).status_code == 200
        item = client.get("/api/artifacts/report").json()
        assert item["unread"] is True and item["readAt"] is None
        assert client.get("/api/artifacts/_status").json()["unreadCount"] == 1
        marked = client.post(
            "/api/artifacts/_read", json={"ids": ["report"]}, headers=ORIGIN
        )
        assert marked.status_code == 200
        assert marked.json()["unreadCount"] == 0
        detail = client.get("/api/artifacts/report").json()
        assert detail["unread"] is False and detail["readAt"] > 0
        assert (
            client.post(
                "/api/artifacts/_read", json={"ids": ["report"]}, headers=ORIGIN
            ).json()
            == marked.json()
        )
        assert client.get("/api/artifacts/report").json()["readAt"] == detail["readAt"]
        assert (
            client.get("/api/artifacts").json()["items"][0]["readAt"]
            == detail["readAt"]
        )


def test_mark_all_uses_exact_observed_snapshot_and_unknown_ids_abort_atomically(
    tmp_path,
):
    with client_for(tmp_path) as client:
        login(client)
        publish(client, "first")
        publish(client, "second")
        observed = client.get("/api/artifacts/_status").json()["unreadIds"]
        publish(client, "later")
        assert (
            client.post(
                "/api/artifacts/_read",
                json={"ids": observed + ["missing"]},
                headers=ORIGIN,
            ).status_code
            == 404
        )
        assert client.get("/api/artifacts/_status").json()["unreadCount"] == 3
        marked = client.post(
            "/api/artifacts/_read", json={"ids": observed}, headers=ORIGIN
        )
        assert marked.json()["unreadIds"] == ["later"]
        assert client.post(
            "/api/artifacts/_read", json={"ids": observed}, headers=ORIGIN
        ).json()["unreadIds"] == ["later"]
        assert (
            client.post(
                "/api/artifacts/_read", json={"ids": ["first", "first"]}, headers=ORIGIN
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/artifacts/_read", json={"ids": ["../../bad"]}, headers=ORIGIN
            ).status_code
            == 422
        )
        # Previously valid IDs are not shadowed by the new private status route.
        publish(client, "status")
        assert client.get("/api/artifacts/status").json()["id"] == "status"


def test_publication_race_cannot_mark_new_document_as_read(tmp_path):
    store = Store(tmp_path)
    artifacts, reads = Artifacts(store), ResourceReads(store)
    artifacts.publish("old", "Old", "text", "one")
    observed = reads.status()["unreadIds"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(reads.mark, ResourceRead(ids=observed)),
            pool.submit(artifacts.publish, "new", "New", "text", "two"),
        ]
        for future in futures:
            future.result()
    assert reads.status()["unreadIds"] == ["new"]
    # Natural retry idempotency preserves the original timestamp.
    before = artifacts.metadata(artifacts.get("old"))["readAt"]
    reads.mark(ResourceRead(ids=observed))
    assert artifacts.metadata(artifacts.get("old"))["readAt"] == before


def test_status_reads_only_keys_and_small_markers_not_resource_content(tmp_path):
    store = Store(tmp_path)
    artifacts = Artifacts(store)
    artifacts.publish("large", "Large", "text", "x" * 1000000)
    # A malformed legacy payload demonstrates status doesn't parse any JSON
    # resource body; content-reading routes retain their existing validation.
    store.set("artifact:legacy", "not a resource object")
    reads = ResourceReads(store)
    assert reads.status()["unreadIds"] == ["large", "legacy"]
    reads.mark(ResourceRead(ids=["large"]))
    assert reads.status()["unreadIds"] == ["legacy"]


def test_old_archives_start_unread_and_markers_restore_with_private_state(tmp_path):
    source = tmp_path / "data"
    store = prepared(source)
    artifacts = Artifacts(store)
    artifacts.publish("legacy", "Legacy", "text", "old")
    manager = Backups(store)
    first = manager.create()
    old_target = tmp_path / "old-target"
    restore(manager.path(first["id"]), old_target, source)
    assert ResourceReads(Store(old_target)).status()["unreadIds"] == ["legacy"]
    ResourceReads(store).mark(ResourceRead(ids=["legacy"]))
    # The incoming backup retention policy reuses today's archive; next day is
    # required for an archive containing subsequently recorded acknowledgments.
    manager.clock = lambda: first["createdAt"] + 86400
    marked = manager.create()
    new_target = tmp_path / "new-target"
    restore(manager.path(marked["id"]), new_target, source)
    assert ResourceReads(Store(new_target)).status()["unreadCount"] == 0
    assert ResourceReads(Store(tmp_path / "other-user")).status()["unreadCount"] == 0


def test_removed_resource_markers_are_pruned_and_unknown_not_created(tmp_path):
    store = Store(tmp_path)
    artifacts, reads = Artifacts(store), ResourceReads(store)
    artifacts.publish("removed", "Removed", "text", "old")
    reads.mark(ResourceRead(ids=["removed"]))
    with store.connect() as db:
        db.execute("DELETE FROM settings WHERE key='artifact:removed'")
    assert reads.mark(ResourceRead(ids=[]))["unreadCount"] == 0
    assert store.get("resource-read:removed") is None
