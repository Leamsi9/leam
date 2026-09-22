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

@pytest.mark.parametrize('selected,name', [
    ('image/jpeg', 'Screenshot.jpg'),
    ('image/webp', 'Screenshot.webp'),
    ('application/octet-stream', 'Screenshot.png'),
])
def test_image_upload_uses_supported_bytes_over_misleading_label(tmp_path, selected, name):
    # A complete one-pixel PNG, carried under labels mobile share sheets can supply.
    data = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ioAAAAASUVORK5CYII=')
    app = create_app(tmp_path, codex=FakeCodex(), origins={'http://testserver'}, bootstrap='bootstrap-for-tests')
    with TestClient(app) as client:
        login(client)
        result = upload(client, data, selected, name)
        assert result.status_code == 201, result.text
        saved = result.json()
        assert saved['mimeType'] == 'image/png'
        assert saved['filename'] == name
        downloaded = client.get('/api/attachments/' + saved['id'])
        assert downloaded.content == data
        assert downloaded.headers['content-type'] == 'image/png'
        assert downloaded.headers['x-content-type-options'] == 'nosniff'
        resource = client.get('/api/artifacts/attachment-' + saved['id']).json()
        assert resource['kind'] == 'png'


def test_mislabelled_image_still_rejects_bad_bytes_and_office_mismatch(tmp_path):
    app = create_app(tmp_path, codex=FakeCodex(), origins={'http://testserver'}, bootstrap='bootstrap-for-tests')
    with TestClient(app) as client:
        login(client)
        for data, mime, name in [
            (b'<html>not a screenshot</html>', 'image/png', 'Screenshot.png'),
            (b'\x89PNG\r\n\x1a\n', 'image/jpeg', 'Screenshot.jpg'),
            (b'\x89PNG\r\n\x1a\n', 'image/png', 'document.docx'),
            (b'\xff\xd8\xffbroken', 'image/png', 'Screenshot.png'),
        ]:
            assert upload(client, data, mime, name).status_code == 422
        result = upload(client, b'\0\0\0\x18ftypheic' + b'\0'*20, 'image/jpeg', 'Screenshot.jpg')
        assert result.status_code == 422
        assert 'HEIF/HEIC or AVIF' in result.json()['detail']


@pytest.mark.parametrize("data,mime,reason,detected", [
    (b"private content", "image/png", "PNG signature is missing", "unrecognized"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "PNG header is incomplete", "PNG"),
    (b"\x89PNG\r\n\x1a\n" + b"x" * 30, "image/png", "first chunk is not", "PNG"),
    (b"\xff\xd8\xffimage\xff\xd9trailer", "image/jpeg", "JPEG marker framing is invalid", "JPEG"),
    (b"RIFF" + (100).to_bytes(4, "little") + b"WEBP" + b"x" * 12, "image/webp", "declared RIFF length", "WebP"),
    (b"GIF89a" + b"x" * 30, "image/png", "PNG signature is missing", "GIF"),
    (b"%PDF-1.4 private content", "application/pdf", "PDF end marker is missing", "PDF"),
    (b"private content\0", "text/plain", "Text contains a NUL", "unrecognized"),
    (b"{private content", "application/json", "Text is not valid JSON", "unrecognized"),
])
def test_upload_validation_reports_safe_specific_reason_without_saving(tmp_path, data, mime, reason, detected):
    app = create_app(tmp_path, codex=FakeCodex(), origins={"http://testserver"}, bootstrap="bootstrap-for-tests")
    with TestClient(app) as client:
        login(client)
        response = upload(client, data, mime, "sensitive-private-filename")
        assert response.status_code == 422
        message = response.json()["detail"]
        assert reason in message
        assert f"detected signature: {detected}" in message
        assert "Selected:" in message
        assert "private content" not in message and "sensitive-private-filename" not in message
        with app.state.store.connect() as db:
            assert db.execute("SELECT count(*) FROM attachments").fetchone()[0] == 0
