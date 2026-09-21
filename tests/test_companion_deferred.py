import json
import httpx
from fastapi.testclient import TestClient
from test_api import FakeCodex, login
from leam_api.app import create_app
from leam_api.ironclaw import IronClaw

# Canonical shape captured from actual 3696920 runtime admission receipts, IDs/text replaced.
ACK = {
    "outcome": "deferred_busy",
    "thread_id": "a",
    "accepted_message_ref": "msg:fixture",
    "active_run_id": "11111111-1111-4111-8111-111111111111",
    "status": "Running",
    "event_cursor": None,
    "notice": "Legacy runtime notice is not used as retry authority.",
}


def test_actual_caller_keeps_deferred_ack_and_reads_receipt_without_resubmitting(
    tmp_path,
):
    sent = []

    def runtime(request):
        if request.url.path.endswith("/session"):
            return httpx.Response(200, json={"session_channel_extension_id": "web-app"})
        if request.url.path.endswith("/messages"):
            sent.append(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "body": json.loads(request.content),
                    "authorization": request.headers["authorization"],
                }
            )
            return httpx.Response(200, json=ACK)
        return httpx.Response(200, json={})

    adapter = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="private-fixture",
        transport=httpx.MockTransport(runtime),
    )
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        runtime=adapter,
    )
    path = "/api/companion/threads/a/submissions/fixture-request"
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        login(client)
        body = {"text": "Private user fixture", "requestId": "fixture-request"}
        result = client.post(
            "/api/companion/threads/a/messages",
            json=body,
            headers={"origin": "http://testserver"},
        )
        assert result.json() == ACK
        for _ in range(2):
            receipt = client.get(path)
            assert receipt.json()["state"] == "recorded"
            assert receipt.json()["receipt"]["outcome"] == "deferred_busy"
            assert (
                "Private user fixture" not in receipt.text
                and "leam_context" not in receipt.text
                and "private-fixture" not in receipt.text
            )
        assert len(sent) == 1
        assert (
            sent[0]["method"] == "POST"
            and sent[0]["path"] == "/api/webchat/v2/channels/web-app/messages"
        )
        assert (
            sent[0]["body"]["thread_id"] == "a"
            and sent[0]["body"]["client_action_id"] == "fixture-request"
        )
        assert sent[0]["body"]["content"].endswith("Private user fixture")
        assert sent[0]["authorization"] == "Bearer private-fixture"
        assert client.get(path.replace("/a/", "/b/")).status_code == 404
        assert client.get(path + "-missing").status_code == 404
        app.state.store.runtime_action(
            "pending-request",
            "fingerprint",
            "/channels/web-app/messages",
            {"thread_id": "a", "content": "Never expose"},
        )
        pending = client.get("/api/companion/threads/a/submissions/pending-request")
        assert pending.json() == {
            "requestId": "pending-request",
            "state": "pending",
            "receipt": None,
        }
        assert len(sent) == 1
