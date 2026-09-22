"""Explicit delete caller, atomic cleanup and replay/concurrency protections."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import HTTPException
from test_api import login
from test_artifacts import client_for
from test_backups import prepared
from test_resource_links import mutation
from test_resources import ORIGIN, publish

from leam_api.artifacts import ArtifactConflict, Artifacts
from leam_api.backups import Backups, restore
from leam_api.resource_deletion import DeleteResource, delete
from leam_api.resource_links import Mutation, ResourceLinks, Target
from leam_api.resource_reads import ResourceRead, ResourceReads
from leam_api.store import Store


def body(item, **changes):
    return {
        "requestId": str(uuid4()),
        "expectedSha256": item["sha256"],
        "confirmed": True,
        **changes,
    }


def remove(client, payload, key="report", headers=ORIGIN):
    return client.request(
        "DELETE", "/api/artifacts/" + key, json=payload, headers=headers
    )


def test_delete_auth_confirmation_version_and_exact_retry(tmp_path):
    with client_for(tmp_path) as client:
        assert remove(client, body({"sha256": "a" * 64})).status_code == 401
        login(client)
        item = publish(client).json()
        request = body(item)
        assert remove(client, request, headers={}).status_code == 403
        for confirmed in (False, 1, "true"):
            assert (
                remove(client, {**request, "confirmed": confirmed}).status_code == 422
            )
        assert remove(client, body(item, expectedSha256="b" * 64)).status_code == 409
        assert client.get("/api/artifacts/report").status_code == 200
        saved = remove(client, request)
        assert saved.status_code == 200 and saved.json()["state"] == "deleted"
        assert remove(client, request).json() == saved.json()
        # Reload after an uncertain response is safe even with a new UUID.
        assert remove(client, body(item)).json() == saved.json()
        assert (
            remove(client, {**request, "expectedSha256": "c" * 64}).status_code == 409
        )
        for suffix in ("", "/preview", "/download", "/links"):
            assert client.get("/api/artifacts/report" + suffix).status_code == 404
        assert client.get("/api/artifacts").json()["total"] == 0
        assert client.get("/api/artifacts").json()["storage"]["bytes"] == 0
        assert publish(client).status_code == 409


def test_delete_cleans_links_and_read_state_but_keeps_parent_unchanged(tmp_path):
    store = Store(tmp_path)
    artifacts, links, reads = (
        Artifacts(store),
        ResourceLinks(store),
        ResourceReads(store),
    )
    item = artifacts.publish("report", "Report", "text", "private contents")
    task = store.create("commitment", {"title": "Parent task"})
    link = Mutation(**mutation(task))
    links.mutate("report", link)
    reads.mark(ResourceRead(ids=["report"]))
    result = delete(artifacts, "report", DeleteResource(**body(item)))
    assert result["state"] == "deleted"
    assert store.entities("commitment") == [task]
    assert store.get("resource-links:report") is None
    assert store.get("resource-read:report") is None
    assert reads.status()["unreadCount"] == 0
    assert (
        links.reverse(Target(targetType="commitment", targetId=task["id"]))["items"]
        == []
    )
    with pytest.raises(HTTPException) as caught:
        links.mutate("report", link)
    assert caught.value.status_code == 410
    tombstone = store.get("resource-deleted:report")
    assert set(tombstone) == {"sha256", "deletedAt"}
    assert "private contents" not in str(tombstone)
    with pytest.raises(HTTPException) as caught:
        reads.mark(ResourceRead(ids=["report"]))
    assert caught.value.status_code == 404


def test_concurrent_delete_and_publication_cannot_resurrect(tmp_path):
    store = Store(tmp_path)
    artifacts = Artifacts(store)
    item = artifacts.publish("report", "Report", "text", "contents")
    request = DeleteResource(**body(item))

    def republish():
        try:
            return artifacts.publish("report", "Report", "text", "contents")
        except ArtifactConflict:
            return "deleted"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(delete, artifacts, "report", request)
        second = pool.submit(republish)
        first.result()
        second.result()
    assert artifacts.list()["total"] == 0
    with pytest.raises(ArtifactConflict):
        artifacts.publish("report", "Report", "text", "contents")
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(
            pool.map(lambda _: delete(artifacts, "report", request), range(2))
        )
    assert receipts[0] == receipts[1]


def test_deletion_is_installation_scoped_and_tombstone_survives_restore(tmp_path):
    source = tmp_path / "data"
    store = prepared(source)
    artifacts = Artifacts(store)
    item = artifacts.publish("report", "Report", "text", "contents")
    other = Artifacts(Store(tmp_path / "other"))
    other.publish("report", "Other installation", "text", "other")
    delete(artifacts, "report", DeleteResource(**body(item)))
    assert other.get("report")["content"] == "other"
    backups = Backups(store)
    saved = backups.create()
    target = tmp_path / "restored"
    restore(backups.path(saved["id"]), target, source)
    restored = Artifacts(Store(target))
    assert restored.list()["items"] == []
    with pytest.raises(ArtifactConflict):
        restored.publish("report", "Report", "text", "contents")
