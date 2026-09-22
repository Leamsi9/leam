"""Caller evidence for references, terminal deletion and existing upload compatibility."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from test_api import FakeCodex, login, make
from test_artifacts import client_for
from test_attachments import ORIGIN, upload
from test_backups import prepared
from test_coding_attachments import png

from leam_api.app import create_app
from leam_api.artifacts import Artifacts
from leam_api.attachments import AttachmentStore
from leam_api.backups import Backups, restore
from leam_api.ironclaw import IronClaw
from leam_api.store import Store


def key(item):
    return "attachment-" + item["id"]


def remove(client, item, **changes):
    body = {
        "requestId": str(uuid4()),
        "expectedSha256": item["sha256"],
        "confirmed": True,
        **changes,
    }
    return client.request(
        "DELETE", "/api/artifacts/" + key(item), json=body, headers=ORIGIN
    )


def test_upload_resource_uses_one_blob_and_existing_auth_read_download_paths(tmp_path):
    with client_for(tmp_path) as client:
        assert client.get("/api/artifacts?kind=attachment").status_code == 401
        login(client)
        item = upload(client, b"Source text", name="work.txt").json()
        image = upload(client, png(), "image/png", "image.png").json()
        page = client.get("/api/artifacts?kind=attachment&q=work").json()
        assert page["total"] == 1
        resource = page["items"][0]
        assert resource["id"] == key(item) and resource["tags"] == ["Attachment"]
        assert resource["category"] == "attachment" and resource["source"] is None
        assert client.get(resource["downloadUrl"]).content == b"Source text"
        detail = client.get("/api/artifacts/" + key(item)).json()
        assert detail["content"] == "Source text"
        preview = client.get("/api/artifacts/" + key(image) + "/preview")
        assert (
            preview.content == png() and preview.headers["cache-control"] == "no-store"
        )
        assert "sandbox" in preview.headers["content-security-policy"]
        assert (
            client.post(
                "/api/artifacts/_read", json={"ids": [key(item)]}, headers=ORIGIN
            ).status_code
            == 200
        )
        assert key(item) not in client.get("/api/artifacts/_status").json()["unreadIds"]
        with client.app.state.store.connect() as db:
            stored = db.execute(
                "SELECT value FROM settings WHERE key=?", ("artifact:" + key(item),)
            ).fetchone()[0]
            assert "Source text" not in stored and "contentBase64" not in stored
            assert db.execute(
                "SELECT length(bytes) FROM attachments WHERE id=?", (item["id"],)
            ).fetchone()[0] == len(b"Source text")
        assert (
            client.request(
                "DELETE",
                "/api/artifacts/" + key(item),
                json={
                    "requestId": str(uuid4()),
                    "expectedSha256": item["sha256"],
                    "confirmed": True,
                },
            ).status_code
            == 403
        )
        assert remove(client, item, confirmed=False).status_code == 422
        assert remove(client, item, expectedSha256="0" * 64).status_code == 409


def test_existing_upload_is_indexed_without_reupload_or_guessed_provenance(tmp_path):
    store = Store(tmp_path)
    manager = AttachmentStore(store)
    item = manager.add(b"Legacy upload", "text/plain", "old.txt")
    manager.bind([item["id"]], "unknown-old-request")
    with store.connect() as db:
        db.execute("DELETE FROM settings WHERE key=?", ("artifact:" + key(item),))
    resources = Artifacts(store)
    assert resources.list()["items"][0]["id"] == key(item)
    assert resources.get(key(item))["source"] is None
    assert manager.rows([item["id"]])[0]["bytes"] == b"Legacy upload"
    assert resources.list(q="old")["total"] == 1
    Artifacts(store)
    assert resources.list()["total"] == 1


def companion(tmp_path):
    state = {"fail": True, "posts": []}

    def handler(request):
        if request.url.path.endswith("/session"):
            return httpx.Response(200, json={"session_channel_extension_id": "web-app"})
        if request.url.path.endswith("/messages"):
            state["posts"].append(json.loads(request.content))
            if state["fail"]:
                raise httpx.ReadTimeout("synthetic lost receipt", request=request)
            # Canonical native submitted/deferred wire, not an invented accepted boolean.
            return httpx.Response(
                200,
                json={
                    "outcome": "submitted",
                    "thread_id": "task-thread",
                    "accepted_message_ref": "msg:source-message",
                    "run_id": "source-run",
                    "status": "Running",
                },
            )
        if request.url.path.endswith("/timeline"):
            return httpx.Response(
                200,
                json={
                    "messages": [
                        {
                            "message_id": "source-message",
                            "kind": "user",
                            "content": "Read the file",
                        }
                    ]
                },
            )
        return httpx.Response(200, json={})

    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        runtime=IronClaw(
            "http://127.0.0.1:46410",
            None,
            token="fixture",
            transport=httpx.MockTransport(handler),
        ),
    )
    return TestClient(app), state


def test_uncertain_companion_blocks_then_confirmed_delete_preserves_history_and_links(
    tmp_path,
):
    client, state = companion(tmp_path)
    with client:
        login(client)
        store = client.app.state.store
        commitment = store.create(
            "commitment", {"title": "Canonical obligation", "status": "active"}
        )
        store.set(
            "companion-item:task-thread",
            {"kind": "commitment", "itemId": commitment["id"]},
        )
        item = upload(client, b"Private fixture data", name="evidence.txt").json()
        body = {
            "requestId": str(uuid4()),
            "text": "Read file",
            "attachmentIds": [item["id"]],
        }
        assert (
            client.post(
                "/api/companion/threads/task-thread/messages", json=body, headers=ORIGIN
            ).status_code
            == 502
        )
        assert remove(client, item).status_code == 409
        assert (
            client.get("/api/attachments/" + item["id"]).content
            == b"Private fixture data"
        )
        assert client.get("/api/artifacts/" + key(item)).json()["source"] is None
        state["fail"] = False
        assert (
            client.post(
                "/api/companion/threads/task-thread/messages", json=body, headers=ORIGIN
            ).status_code
            == 200
        )
        assert state["posts"][0] == state["posts"][1]
        resource = client.get("/api/artifacts/" + key(item)).json()
        assert resource["source"] == {
            "surface": "companion",
            "threadId": "task-thread",
            "turnId": "source-message",
        }
        links = client.get("/api/artifacts/" + key(item) + "/links").json()
        assert links["items"][0]["targetId"] == commitment["id"]
        request_id = str(uuid4())
        result = remove(client, item, requestId=request_id)
        assert result.status_code == 200, result.text
        assert remove(client, item, requestId=request_id).json() == result.json()
        assert client.get("/api/artifacts/" + key(item)).status_code == 404
        assert client.get("/api/attachments/" + item["id"]).status_code == 404
        history = client.get("/api/companion/threads/task-thread").json()
        assert history["messages"][0]["content"] == "Read the file"
        assert history["messages"][0]["leamAttachments"][0]["state"] == "deleted"
        with store.connect() as db:
            assert (
                db.execute(
                    "SELECT length(bytes) FROM attachments WHERE id=?", (item["id"],)
                ).fetchone()[0]
                == 0
            )
            saved = json.loads(
                db.execute(
                    "SELECT body FROM runtime_actions WHERE id=?", (body["requestId"],)
                ).fetchone()[0]
            )
            assert saved["attachments"] == []
        assert store.entities("commitment")[0]["id"] == commitment["id"]
        assert client.get("/api/artifacts").json()["total"] == 0


def test_coding_requires_terminal_evidence_and_retains_deleted_placeholder(tmp_path):
    client, codex = make(tmp_path)
    original_request = codex.request

    async def request(method, params, **kwargs):
        if method == "thread/turns/list":
            return {
                "data": [{"id": "turn-1", "status": "completed", "items": []}],
                "nextCursor": None,
            }
        return await original_request(method, params, **kwargs)

    codex.request = request
    with client:
        login(client)
        item = upload(client, b"Coding text", name="code.txt").json()
        assert (
            client.post(
                "/api/codex/threads/t/connect",
                json={"handoffConfirmed": True},
                headers=ORIGIN,
            ).status_code
            == 200
        )
        request_id = str(uuid4())
        sent = client.post(
            "/api/codex/threads/t/turns",
            json={
                "requestId": request_id,
                "text": "Read",
                "attachmentIds": [item["id"]],
            },
            headers=ORIGIN,
        )
        assert sent.status_code == 200, sent.text
        assert remove(client, item).status_code == 409
        manager = AttachmentStore(client.app.state.store)
        original = manager.materialize(manager.rows([item["id"]])[0])
        history = client.get("/api/codex/threads/t/turns").json()
        assert history["data"][0]["leamAttachments"][0]["id"] == item["id"]
        assert remove(client, item).status_code == 200
        assert not original.exists()
        history = client.get("/api/codex/threads/t/turns").json()
        assert history["data"][0]["leamAttachments"][0]["state"] == "deleted"
        assert client.get("/api/artifacts").json()["total"] == 0


def test_deleted_file_cannot_be_rebound_or_rematerialized_and_other_digest_copy_survives(
    tmp_path,
):
    with client_for(tmp_path) as client:
        login(client)
        item = upload(client, b"Identical", name="one.txt").json()
        other = upload(client, b"Identical", name="two.txt").json()
        manager = AttachmentStore(client.app.state.store)
        stale = manager.rows([item["id"]])[0]
        cached = manager.materialize(stale)
        assert remove(client, item).status_code == 200
        assert cached.read_bytes() == b"Identical"
        with pytest.raises(ValueError):
            manager.bind([item["id"]], "late-send")
        with pytest.raises(ValueError):
            manager.materialize(stale)
        assert manager.rows([other["id"]])[0]["bytes"] == b"Identical"
        assert remove(client, other).status_code == 200
        assert not cached.exists()


def test_concurrent_bind_and_delete_have_one_safe_serial_order(tmp_path):
    with client_for(tmp_path) as client:
        login(client)
        item = upload(client, b"Concurrent fixture").json()
        manager = AttachmentStore(client.app.state.store)

        def bind():
            try:
                manager.bind([item["id"]], "pending-concurrent")
                return "bound"
            except ValueError:
                return "deleted"

        with ThreadPoolExecutor(max_workers=2) as pool:
            sent = pool.submit(bind)
            deleted = pool.submit(remove, client, item)
            bind_state, response = sent.result(), deleted.result()
        assert (bind_state, response.status_code) in {("bound", 409), ("deleted", 200)}


def test_decoded_byte_and_item_limits_ignore_unrelated_database_size(
    tmp_path, monkeypatch
):
    import leam_api.attachment_resources as refs
    from leam_api import attachments

    with client_for(tmp_path) as client:
        login(client)
        # Emulate a large metadata-only DB without allocating a 300MiB fixture.
        # The old whole-file gate rejected this actual upload caller.
        import os
        from pathlib import Path

        original_stat = Path.stat
        database = client.app.state.store.path

        def reported_size(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            if path == database:
                fields = list(result)
                fields[6] = 300 * 1024 * 1024
                return os.stat_result(fields)
            return result

        monkeypatch.setattr(Path, "stat", reported_size)
        monkeypatch.setattr(attachments, "MAX_STORED_BYTES", 8)
        first = upload(client, b"12345").json()
        assert upload(client, b"6789").status_code == 422
        assert remove(client, first).status_code == 200
        monkeypatch.setattr(refs, "MAX_ATTACHMENTS", 1)
        assert upload(client, b"a").status_code == 201
        assert upload(client, b"b").status_code == 422


def test_attachment_pointer_and_deleted_placeholders_survive_backup(tmp_path):
    source = tmp_path / "source"
    store = prepared(source)
    manager = AttachmentStore(store)
    item = manager.add(b"Kept", "text/plain", "kept.txt")
    removed = manager.add(b"Deleted", "text/plain", "removed.txt")
    from leam_api.resource_deletion import DeleteResource, delete

    delete(
        Artifacts(store),
        key(removed),
        DeleteResource(
            requestId=uuid4(), expectedSha256=removed["sha256"], confirmed=True
        ),
    )
    archive = Backups(store).create()
    target = tmp_path / "restore"
    restore(Backups(store).path(archive["id"]), target, source)
    restored = Store(target)
    assert Artifacts(restored).list()["items"][0]["id"] == key(item)
    assert AttachmentStore(restored).rows([item["id"]])[0]["bytes"] == b"Kept"
    with restored.connect() as db:
        assert db.execute(
            "SELECT size,length(bytes) FROM attachments WHERE id=?", (removed["id"],)
        ).fetchone()[:] == (0, 0)


def test_symlink_cache_blocks_removal_without_touching_external_file(tmp_path):
    with client_for(tmp_path) as client:
        login(client)
        item = upload(client, b"Stored bytes").json()
        manager = AttachmentStore(client.app.state.store)
        row = manager.rows([item["id"]])[0]
        cache = manager.materialize(row)
        cache.unlink()
        external = tmp_path / "unrelated.txt"
        external.write_text("Keep me")
        cache.symlink_to(external)
        response = remove(client, item)
        assert response.status_code == 409
        assert external.read_text() == "Keep me"
        assert client.get("/api/attachments/" + item["id"]).content == b"Stored bytes"


def test_independent_installations_cannot_read_or_delete_each_others_attachments(
    tmp_path,
):
    with client_for(tmp_path / "a") as owner, client_for(tmp_path / "b") as other:
        login(owner)
        login(other)
        item = upload(owner, b"Private A").json()
        assert other.get("/api/artifacts/" + key(item)).status_code == 404
        assert remove(other, item).status_code == 404
        assert owner.get("/api/attachments/" + item["id"]).content == b"Private A"
