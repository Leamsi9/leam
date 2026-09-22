"""Resource caller/security/storage/restore tests; use synthetic private files."""

import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_api import login
from test_artifacts import client_for
from test_backups import prepared
from test_office_attachments import archive, parts

from leam_api import artifacts as module
from leam_api.artifacts import ArtifactQuota, Artifacts
from leam_api.backups import Backups, restore
from leam_api.store import Store

ORIGIN = {"origin": "http://testserver"}
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)


def publish(client, key="report", **fields):
    return client.post(
        "/api/artifacts",
        json={
            "id": key,
            "title": "Report",
            "kind": "markdown",
            "content": "# Hello",
            **fields,
        },
        headers=ORIGIN,
    )


def test_authenticated_publication_listing_source_and_no_raw_binary(tmp_path):
    with client_for(tmp_path) as client:
        assert client.get("/api/artifacts").status_code == 401
        assert publish(client).status_code == 401
        login(client)
        assert client.post("/api/artifacts", json={}).status_code == 403
        source = {"surface": "today", "threadId": "thread", "turnId": "turn"}
        response = publish(client, source=source)
        assert response.status_code == 200
        assert publish(client, source=source).json() == response.json()
        assert publish(client, source={"surface": "coding"}).status_code == 409
        assert publish(client, content="different").status_code == 409
        rows = client.get("/api/artifacts").json()
        assert rows["total"] == 1 and rows["items"][0]["source"] == source
        assert "content" not in rows["items"][0]
        assert rows["items"][0]["downloadUrl"] == "/api/artifacts/report/download"
        assert client.get("/api/artifacts").headers["cache-control"] == "no-store"
        image = publish(
            client,
            "image",
            kind="png",
            content=None,
            contentBase64=base64.b64encode(PNG).decode(),
        )
        assert image.status_code == 200
        detail = client.get("/api/artifacts/image").json()
        assert "contentBase64" not in detail and "content" not in detail
        preview = client.get(detail["previewUrl"])
        assert preview.content == PNG and preview.headers["content-type"] == "image/png"
        assert (
            preview.headers["content-security-policy"] == "default-src 'none'; sandbox"
        )
        assert preview.headers["x-content-type-options"] == "nosniff"
        assert preview.headers["x-frame-options"] == "DENY"


def test_legacy_records_retry_and_stable_pagination_during_additions(tmp_path):
    store = Store(tmp_path)
    old = {
        "id": "legacy",
        "title": "Original",
        "kind": "markdown",
        "content": "old",
        "bytes": 3,
        "sha256": hashlib.sha256(b"old").hexdigest(),
        "publishedAt": 1,
    }
    store.set("artifact:legacy", old)
    service = Artifacts(store)
    assert (
        service.publish("legacy", "Original", "markdown", "old")["filename"]
        == "legacy.md"
    )
    for key in ("a", "b", "c"):
        service.publish(key, "Searchable " + key, "text", key)
        store.set("artifact:" + key, {**service.get(key), "publishedAt": 2})
    with client_for(tmp_path) as client:
        login(client)
        page = client.get("/api/artifacts", params={"limit": 2}).json()
        assert [x["id"] for x in page["items"]] == ["c", "b"]
        service.publish("z", "Newly added", "text", "z")
        store.set("artifact:z", {**service.get("z"), "publishedAt": 2})
        next_page = client.get(
            "/api/artifacts", params={"limit": 2, "cursor": page["nextCursor"]}
        ).json()
        assert [x["id"] for x in next_page["items"]] == ["a", "legacy"]
        found = client.get(
            "/api/artifacts", params={"q": "SEARCHABLE", "kind": "document"}
        ).json()
        assert found["total"] == 3
        assert (
            client.get("/api/artifacts", params={"kind": "image"}).json()["items"] == []
        )
        assert (
            client.get("/api/artifacts", params={"cursor": "invalid"}).status_code
            == 422
        )
        assert client.get("/api/artifacts", params={"kind": "svg"}).status_code == 422


@pytest.mark.parametrize(
    "fields",
    [
        {"filename": "../../private"},
        {"filename": "bad\r\nname"},
        {"filename": 'bad"name'},
        {"path": "/etc/passwd"},
        {"url": "https://example.com/private"},
        {"source": {"secret": "unsupported"}},
        {"source": {"surface": "x\n"}},
        {"kind": "svg", "content": "<svg/>"},
        {"kind": "png", "content": None, "contentBase64": "not-base64"},
        {
            "kind": "png",
            "content": None,
            "contentBase64": base64.b64encode(b"<svg/>").decode(),
        },
        {"kind": "markdown", "contentBase64": "YQ=="},
    ],
)
def test_resource_publication_rejects_untrusted_shapes_without_saving(tmp_path, fields):
    with client_for(tmp_path) as client:
        login(client)
        assert publish(client, **fields).status_code == 422
        assert client.get("/api/artifacts").json()["items"] == []


@pytest.mark.parametrize("kind", ["pdf", "docx", "xlsx", "pptx"])
def test_document_resources_are_download_only(tmp_path, kind):
    data = b"%PDF-1.7\nfixture\n%%EOF" if kind == "pdf" else archive(parts(kind))
    with client_for(tmp_path) as client:
        login(client)
        response = publish(
            client,
            kind=kind,
            content=None,
            contentBase64=base64.b64encode(data).decode(),
            filename="document." + kind,
        )
        assert response.status_code == 200, response.text
        assert response.json()["previewUrl"] is None
        assert client.get("/api/artifacts/report/preview").status_code == 404
        downloaded = client.get("/api/artifacts/report/download")
        assert downloaded.content == data
        assert downloaded.headers["content-type"] == "application/octet-stream"


def test_aggregate_quota_serializes_concurrent_publishers_and_allows_exact_retry(
    tmp_path, monkeypatch
):
    service = Artifacts(Store(tmp_path))
    monkeypatch.setattr(module, "MAX_STORED_BYTES", 10)

    def write(key):
        try:
            return service.publish(key, "Title", "text", "123456")
        except ArtifactQuota:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ["one", "two"]))
    accepted = [r for r in results if r]
    assert len(accepted) == 1 and service.list()["storage"]["bytes"] == 6
    assert service.publish(accepted[0]["id"], "Title", "text", "123456") == accepted[0]
    monkeypatch.setattr(module, "MAX_RESOURCES", 1)
    with pytest.raises(ArtifactQuota):
        service.publish("three", "Title", "text", "1")


def test_generated_resources_survive_existing_backup_restore_and_installation_isolation(
    tmp_path,
):
    source = tmp_path / "source"
    store = prepared(source)
    resources = Artifacts(store)
    resources.publish(
        "image",
        "Image",
        "png",
        content_base64=base64.b64encode(PNG).decode(),
        source={"surface": "coding"},
    )
    resources.publish("report", "Report", "markdown", "# Generated")
    backups = Backups(store)
    archive = backups.path(backups.create()["id"])
    target = tmp_path / "restored"
    restore(archive, target, source)
    assert Artifacts(Store(target)).list() == resources.list()
    assert module.resource_bytes(Artifacts(Store(target)).get("image")) == PNG
    with client_for(tmp_path / "unrelated") as client:
        login(client)
        assert client.get("/api/artifacts").json()["items"] == []
        assert client.get("/api/artifacts/image").status_code == 404
