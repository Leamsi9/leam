import base64
import json
import uuid

import httpx
from fastapi.testclient import TestClient
from test_api import FakeCodex, login
from test_attachments import ORIGIN, upload

from leam_api.app import create_app
from leam_api.ironclaw import IronClaw


def test_companion_attachments_reach_native_ingress_and_retry_identically(tmp_path):
    calls = []

    def handler(request):
        if request.url.path.endswith("/session"):
            return httpx.Response(200, json={"session_channel_extension_id": "web-app"})
        if request.url.path.endswith("/messages"):
            calls.append(json.loads(request.content))
            if len(calls) == 1:
                raise httpx.ReadTimeout("lost receipt", request=request)
            return httpx.Response(
                200,
                json={
                    "accepted": True,
                    "run_id": "synthetic",
                    "accepted_message_ref": "msg:message-fixture",
                },
            )
        if request.url.path.endswith("/timeline"):
            return httpx.Response(
                200,
                json={
                    "messages": [
                        {"message_id": "message-fixture", "kind": "user", "content": ""}
                    ]
                },
            )
        return httpx.Response(200, json={})

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="test",
        transport=httpx.MockTransport(handler),
    )
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        runtime=runtime,
    )
    with TestClient(app) as client:
        login(client)
        item = upload(client, b"Attachment evidence", name="evidence.txt").json()
        body = {
            "text": "",
            "attachmentIds": [item["id"]],
            "requestId": str(uuid.uuid4()),
        }
        first = client.post(
            "/api/companion/threads/test/messages", json=body, headers=ORIGIN
        )
        assert first.status_code == 502
        assert (
            client.delete("/api/attachments/" + item["id"], headers=ORIGIN).status_code
            == 409
        )
        second = client.post(
            "/api/companion/threads/test/messages", json=body, headers=ORIGIN
        )
        assert second.status_code == 200, second.text
        assert calls[0] == calls[1]
        assert calls[0]["attachments"] == [
            {
                "mime_type": "text/plain",
                "filename": "evidence.txt",
                "data_base64": base64.b64encode(b"Attachment evidence").decode(),
            }
        ]
        assert (
            client.post(
                "/api/companion/threads/test/messages", json=body, headers=ORIGIN
            ).status_code
            == 200
        )
        assert len(calls) == 2
        history = client.get("/api/companion/threads/test").json()
        assert history["messages"][0]["leamAttachments"] == [item]
        other = upload(client, b"Different").json()
        assert (
            client.post(
                "/api/companion/threads/test/messages",
                json={**body, "attachmentIds": [other["id"]]},
                headers=ORIGIN,
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/companion/threads/test/messages",
                json={"text": "", "requestId": str(uuid.uuid4())},
                headers=ORIGIN,
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/companion/threads/test/messages",
                json={
                    "text": "test",
                    "attachmentIds": [str(uuid.uuid4())],
                    "requestId": str(uuid.uuid4()),
                },
                headers=ORIGIN,
            ).status_code
            == 422
        )
        assert len(calls) == 2
