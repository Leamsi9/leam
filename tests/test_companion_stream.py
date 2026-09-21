import json
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app
from leam_api.companion import router
from leam_api.ironclaw import IronClaw

# Shape captured read-only from pinned 756541 runtime events; text/IDs replaced.
FRAME = {
    "type": "projection_update",
    "cursor": {"sequence": 2},
    "state": {
        "thread_id": "thread-a",
        "items": [
            {"text": {"id": "live-a", "run_id": "run-a", "body": "Partial answer"}},
            {
                "capability_activity": {
                    "invocation_id": "call-a",
                    "turn_run_id": "run-a",
                    "thread_id": "thread-a",
                    "capability_id": "mcp-leam.leam_context",
                    "status": "started",
                    "provider": None,
                    "runtime": None,
                    "process_id": None,
                    "output_bytes": None,
                    "error_kind": None,
                    "updated_at": "2026-09-20T21:00:00Z",
                }
            },
        ],
    },
}


def setup(tmp_path, handler):
    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="private-runtime-token",
        transport=httpx.MockTransport(handler),
    )
    # This delegated checkout predates root's added Codex router injection.
    with patch(
        "leam_api.app.companion_router",
        lambda store, runtime, *args: router(store, runtime, FakeCodex()),
    ):
        return TestClient(
            create_app(
                tmp_path,
                {"http://testserver"},
                bootstrap="bootstrap-for-tests",
                codex=FakeCodex(),
                runtime=runtime,
            )
        )


def test_stream_auth_cursor_and_no_message_resend(tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer private-runtime-token"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="id: opaque-cursor\nevent: projection_update\ndata: "
            + json.dumps(FRAME)
            + "\n\n",
        )

    with setup(tmp_path, handle) as client:
        assert client.get("/api/companion/threads/thread-a/events").status_code == 401
        login(client)
        response = client.get(
            "/api/companion/threads/thread-a/events?cursor=cursor-one",
            headers={"Last-Event-ID": "cursor-two"},
        )
        assert response.status_code == 200
        assert "Partial answer" in response.text and "opaque-cursor" in response.text
        assert "private-runtime-token" not in response.text
        assert len(calls) == 1 and calls[0].method == "GET"
        assert calls[0].url.path == "/api/webchat/v2/threads/thread-a/events"
        assert calls[0].headers["last-event-id"] == "cursor-two"
        assert (
            client.get(
                "/api/companion/threads/thread-a/events?cursor=" + ("a" * 4097)
            ).status_code
            == 422
        )


def test_stream_errors_and_oversize_do_not_leak(tmp_path):
    for status, content in [
        (401, "private-upstream-error"),
        (200, "data: " + ("x" * 262145) + "\n\n"),
    ]:
        with setup(
            tmp_path / str(status),
            lambda request, status=status, content=content: httpx.Response(
                status, headers={"content-type": "text/event-stream"}, content=content
            ),
        ) as client:
            login(client)
            response = client.get("/api/companion/threads/thread-a/events")
            assert "stream_error" in response.text
            assert "private-upstream-error" not in response.text
            assert len(response.content) < 1000


def test_runtime_adapter_delivers_first_frame_before_completion_and_closes():
    import asyncio

    class DelayedBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b"event: projection_update\r\n"
            yield b'data: {"type":"projection_update","state":{"thread_id":"a","items":[]}}\r'
            yield b"\n\r\n"
            await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

    async def run():
        body = DelayedBody()
        runtime = IronClaw(
            "http://127.0.0.1:46410",
            None,
            token="secret",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=body
                )
            ),
        )
        stream = runtime.events("/threads/a/events")
        first = await asyncio.wait_for(anext(stream), 1)
        assert b"projection_update" in first
        await stream.aclose()
        assert body.closed
        await runtime.close()

    asyncio.run(run())


def test_stop_exact_run_idempotency_origin_and_stale_status(tmp_path):
    calls = []
    run_id = "19e9c46c-a47b-4e01-8cb4-f4c5c4c26c63"
    request_id = "19e9c46c-a47b-4e01-8cb4-f4c5c4c26c64"
    response = {
        "run_id": run_id,
        "status": "CancelRequested",
        "event_cursor": 23,
        "already_terminal": False,
    }

    def handle(request):
        calls.append(
            (
                request.method,
                request.url.path,
                json.loads(request.content),
                request.headers.get("authorization"),
            )
        )
        # Match ProductCancelRunRequest -> parse_cancel_reason, not just JSON shape.
        if json.loads(request.content).get("reason") not in {
            "user_requested",
            "superseded",
            "timeout",
            "operator_requested",
            "policy",
            None,
        }:
            return httpx.Response(400, json={"error": "validation", "field": "reason"})
        return httpx.Response(200, json=response)

    with setup(tmp_path, handle) as client:
        login(client)
        path = "/api/companion/threads/thread-a/runs/" + run_id + "/cancel"
        assert (
            client.post(
                path,
                json={"requestId": request_id},
                headers={"origin": "https://wrong.example"},
            ).status_code
            == 403
        )
        headers = {"origin": "http://testserver"}
        first = client.post(path, json={"requestId": request_id}, headers=headers)
        again = client.post(path, json={"requestId": request_id}, headers=headers)
        assert first.status_code == again.status_code == 200
        assert first.json()["status"] == "CancelRequested"
        assert len(calls) == 1
        assert calls[0] == (
            "POST",
            "/api/webchat/v2/threads/thread-a/runs/" + run_id + "/cancel",
            {
                "client_action_id": request_id,
                "thread_id": "thread-a",
                "run_id": run_id,
                "reason": "user_requested",
            },
            "Bearer private-runtime-token",
        )
        assert (
            client.post(
                path.replace("thread-a", "thread-b"),
                json={"requestId": request_id},
                headers=headers,
            ).status_code
            == 409
        )
        assert len(calls) == 1
        response.update(status="Completed", already_terminal=True)
        stale = client.post(
            path,
            json={"requestId": "19e9c46c-a47b-4e01-8cb4-f4c5c4c26c65"},
            headers=headers,
        )
        assert stale.status_code == 409
        assert (
            client.post(path, json={"requestId": "bad"}, headers=headers).status_code
            == 422
        )


def test_stop_mismatched_runtime_receipt_not_accepted(tmp_path):
    with setup(
        tmp_path,
        lambda request: httpx.Response(
            200,
            json={
                "run_id": "other",
                "status": "Cancelled",
                "event_cursor": 1,
                "already_terminal": False,
            },
        ),
    ) as client:
        login(client)
        response = client.post(
            "/api/companion/threads/a/runs/19e9c46c-a47b-4e01-8cb4-f4c5c4c26c63/cancel",
            json={"requestId": "19e9c46c-a47b-4e01-8cb4-f4c5c4c26c64"},
            headers={"origin": "http://testserver"},
        )
        assert response.status_code == 502


def test_open_stream_stops_after_session_revocation(tmp_path):
    import asyncio

    async def run():
        first, release = asyncio.Event(), asyncio.Event()

        class Body(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                yield b'event: projection_update\ndata: {"type":"projection_update","state":{"thread_id":"a","items":[]}}\n\n'
                first.set()
                await release.wait()
                yield b'event: projection_update\ndata: {"type":"projection_update","state":{"thread_id":"a","items":[{"text":{"run_id":"r","body":"after-revocation"}}]}}\n\n'

            async def aclose(self):
                self.closed = True

        body = Body()
        client_fixture = setup(
            tmp_path,
            lambda request: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=body
            ),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client_fixture.app),
            base_url="http://testserver",
            headers={"Origin": "http://testserver"},
        ) as client:
            assert (
                await client.post(
                    "/api/auth/setup",
                    json={
                        "bootstrap": "bootstrap-for-tests",
                        "password": "fixture-password-long",
                    },
                )
            ).status_code == 200
            reading = asyncio.create_task(client.get("/api/companion/threads/a/events"))
            await asyncio.wait_for(first.wait(), 3)
            assert (await client.post("/api/auth/logout", json={})).status_code == 200
            release.set()
            response = await asyncio.wait_for(reading, 3)
            assert "after-revocation" not in response.text
            assert body.closed

    asyncio.run(run())


def test_thread_subject_comes_from_original_message_not_system_envelope(tmp_path):
    def handler(request):
        path = request.url.path
        if path.endswith("/session"):
            return httpx.Response(200, json={"session_channel_extension_id": "web-app"})
        if path.endswith("/threads"):
            return httpx.Response(
                200,
                json={
                    "threads": [
                        {
                            "thread_id": "a",
                            "title": "You are Leam, a personal companion helping",
                            "created_at": "2026-09-20T21:00:00Z",
                        },
                        {
                            "thread_id": "b",
                            "title": "My explicit title",
                            "created_at": "2026-09-20T20:00:00Z",
                        },
                        {
                            "thread_id": "c",
                            "title": "You are Leam",
                            "created_at": "2026-09-20T19:00:00Z",
                        },
                    ]
                },
            )
        if path.endswith("/messages"):
            return httpx.Response(200, json={"run_id": "r", "outcome": "submitted"})
        return httpx.Response(200, json={})

    with setup(tmp_path, handler) as client:
        login(client)
        headers = {"origin": "http://testserver"}
        assert (
            client.post(
                "/api/companion/threads/a/messages",
                json={
                    "text": "Plan the weekend\n\nwith a walk",
                    "requestId": "subject-message-1",
                },
                headers=headers,
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/companion/threads/a/messages",
                json={
                    "text": "Later unrelated topic",
                    "requestId": "subject-message-2",
                },
                headers=headers,
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/companion/threads/b/messages",
                json={
                    "text": "Do not overwrite my rename",
                    "requestId": "subject-message-3",
                },
                headers=headers,
            ).status_code
            == 200
        )
        response = client.get("/api/companion/threads")
        items = response.json()["threads"]
        assert [item["title"] for item in items] == [
            "Plan the weekend with a walk",
            "My explicit title",
            "Conversation",
        ]
        assert items[0]["created_at"] == "2026-09-20T21:00:00Z"
        assert (
            "You are Leam" not in response.text and "leam_context" not in response.text
        )


def test_stop_retry_upgrades_rejected_legacy_reason_without_new_action(tmp_path):
    import hashlib

    run_id = "19e9c46c-a47b-4e01-8cb4-f4c5c4c26c63"
    request_id = "19e9c46c-a47b-4e01-8cb4-f4c5c4c26c64"
    received = []

    def handler(request):
        payload = json.loads(request.content)
        received.append(payload)
        assert payload == {
            "client_action_id": request_id,
            "thread_id": "a",
            "run_id": run_id,
            "reason": "user_requested",
        }
        return httpx.Response(
            200,
            json={
                "run_id": run_id,
                "status": "Cancelled",
                "event_cursor": 2,
                "already_terminal": False,
            },
        )

    with setup(tmp_path, handler) as client:
        login(client)
        store = client.app.state.store
        fingerprint = hashlib.sha256(
            json.dumps(["cancel", "a", run_id]).encode()
        ).hexdigest()
        path = "/threads/a/runs/" + run_id + "/cancel"
        store.runtime_action(
            request_id,
            fingerprint,
            path,
            {"client_action_id": request_id, "reason": "User requested Stop in Leam"},
        )
        response = client.post(
            "/api/companion" + path,
            json={"requestId": request_id},
            headers={"origin": "http://testserver"},
        )
        assert response.status_code == 200
        assert (
            store.existing_runtime_action(request_id, fingerprint)["body"]
            == received[0]
        )
        assert len(received) == 1
