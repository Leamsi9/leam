import base64
import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app

ORIGIN = {"origin": "http://testserver"}


def upload(
    client, data=b"Synthetic attachment text", mime="text/plain", name="note.txt"
):
    return client.post(
        "/api/attachments",
        params={"filename": name},
        content=data,
        headers={**ORIGIN, "content-type": mime},
    )


def test_authenticated_upload_download_validation_and_bound_delete(tmp_path):
    app = create_app(
        tmp_path,
        codex=FakeCodex(),
        origins={"http://testserver"},
        bootstrap="bootstrap-for-tests",
    )
    with TestClient(app) as client:
        assert upload(client).status_code == 401
        login(client)
        assert (
            client.post(
                "/api/attachments?filename=x.txt",
                content=b"x",
                headers={"content-type": "text/plain"},
            ).status_code
            == 403
        )
        response = upload(client)
        assert response.status_code == 201, response.text
        row = response.json()
        assert row["sha256"] == hashlib.sha256(b"Synthetic attachment text").hexdigest()
        assert row["state"] == "uploaded"
        get = client.get("/api/attachments/" + row["id"])
        assert get.content == b"Synthetic attachment text"
        assert get.headers["cache-control"] == "no-store"
        assert get.headers["x-content-type-options"] == "nosniff"
        from leam_api.attachments import AttachmentStore

        attachments = AttachmentStore(app.state.store)
        assert (
            attachments.inline_parts([row["id"]])[0]["data_base64"]
            == base64.b64encode(get.content).decode()
        )
        attachments.bind([row["id"]], str(uuid.uuid4()))
        assert (
            client.delete("/api/attachments/" + row["id"], headers=ORIGIN).status_code
            == 409
        )
        other = upload(client, b"another").json()
        assert (
            client.delete("/api/attachments/" + other["id"], headers=ORIGIN).status_code
            == 200
        )
        assert client.get("/api/attachments/" + other["id"]).status_code == 404
        for data, mime, name in [
            (b"<svg/>", "image/svg+xml", "x.svg"),
            (b"not image", "image/png", "x.png"),
            (b"\xff", "text/plain", "x.txt"),
            (b"x", "text/plain", "../x.txt"),
            (b"x", "text/html", "x.html"),
        ]:
            assert upload(client, data, mime, name).status_code == 422


def test_upload_actual_body_and_combined_budgets(tmp_path):
    app = create_app(
        tmp_path,
        codex=FakeCodex(),
        origins={"http://testserver"},
        bootstrap="bootstrap-for-tests",
    )
    with TestClient(app) as client:
        login(client)
        data = b"a" * (1024 * 1024 + 1)
        a = upload(client, data).json()
        assert a["sizeBytes"] == len(data)
        assert upload(client, b"a" * (10 * 1024 * 1024 + 1)).status_code == 413
        from leam_api.attachments import AttachmentStore

        attachments = AttachmentStore(app.state.store)
        with pytest.raises(ValueError):
            attachments.resolve([a["id"]] * 2)
        with pytest.raises(ValueError):
            attachments.resolve([str(uuid.uuid4())])


def test_backup_restores_attachment_bytes_and_rebuilds_private_file(tmp_path):
    from test_backups import prepared

    from leam_api.attachments import AttachmentStore
    from leam_api.backups import Backups, restore
    from leam_api.store import Store

    source = tmp_path / "source"
    store = prepared(source)
    manager = AttachmentStore(store)
    item = manager.add(b"Private synthetic attachment", "text/plain", "note.txt")
    manager.bind([item["id"]], "request-attachment")
    original = manager.materialize(manager.rows([item["id"]])[0])
    archive = Backups(store).create()
    target = tmp_path / "restored"
    restore(Backups(store).path(archive["id"]), target, source)
    restored = AttachmentStore(Store(target))
    row = restored.rows([item["id"]])[0]
    assert row["bytes"] == b"Private synthetic attachment"
    rebuilt = restored.materialize(row)
    assert rebuilt != original and rebuilt.read_bytes() == original.read_bytes()
    assert rebuilt.stat().st_mode & 0o077 == 0
    with pytest.raises(ValueError, match="retained"):
        restored.remove(item["id"])
