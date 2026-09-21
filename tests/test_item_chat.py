import json
import uuid

import httpx
from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app
from leam_api.ironclaw import IronClaw

ORIGIN = {"Origin": "http://testserver"}


def test_item_chat_binding_retry_fresh_context_and_removed_source(tmp_path):
    calls = []
    creates = []
    lost = True
    thread = str(uuid.uuid4())

    def handler(request):
        nonlocal lost
        body = json.loads(request.content) if request.content else None
        if request.url.path.endswith("/threads") and request.method == "POST":
            creates.append(body)
            if lost:
                lost = False
                raise httpx.ReadTimeout("lost create receipt", request=request)
            return httpx.Response(200, json={"thread": {"thread_id": thread}})
        if request.url.path.endswith("/session"):
            return httpx.Response(200, json={"session_channel_extension_id": "web"})
        if request.url.path.endswith("/messages"):
            calls.append(body)
            return httpx.Response(200, json={"accepted": True, "run_id": "run"})
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
        item = client.post(
            "/api/commitments",
            headers=ORIGIN,
            json={"title": "Plan walk", "notes": "Original note"},
        ).json()
        endpoint = f"/api/commitments/{item['id']}/chat"
        assert client.get(endpoint).json()["threadId"] is None
        assert not creates
        assert client.post(endpoint, headers=ORIGIN).status_code == 502
        result = client.post(endpoint, headers=ORIGIN)
        assert result.status_code == 200, result.text
        assert result.json()["threadId"] == thread
        assert len(creates) == 2 and creates[0] == creates[1]
        assert client.post(endpoint, headers=ORIGIN).json()["threadId"] == thread
        assert len(creates) == 2
        message = {"requestId": str(uuid.uuid4()), "text": "What should I consider?"}
        send = f"/api/companion/threads/{thread}/messages"
        assert client.post(send, headers=ORIGIN, json=message).status_code == 200
        assert "Original note" in json.dumps(calls[-1])
        revised = client.patch(
            f"/api/commitments/{item['id']}",
            headers=ORIGIN,
            json={"revision": item["revision"], "notes": "Changed note"},
        )
        assert revised.status_code == 200, revised.text
        assert client.post(send, headers=ORIGIN, json=message).status_code == 200
        assert (
            len(calls) == 1
        )  # Receipt replay does not rerun or change frozen context.
        assert (
            client.post(
                send, headers=ORIGIN, json={**message, "requestId": str(uuid.uuid4())}
            ).status_code
            == 200
        )
        assert "Changed note" in json.dumps(calls[-1])
        preview = client.get(f"/api/commitments/{item['id']}/removal-preview").json()
        removed = client.post(
            f"/api/commitments/{item['id']}/remove",
            headers=ORIGIN,
            json={"previewToken": preview["previewToken"], "confirmed": True},
        )
        assert removed.status_code == 200, removed.text
        assert client.get(endpoint).status_code == 404
        assert (
            client.post(
                send, headers=ORIGIN, json={**message, "requestId": str(uuid.uuid4())}
            ).status_code
            == 200
        )
        assert "source_unavailable" in json.dumps(calls[-1])
        assert "Changed note" not in json.dumps(calls[-1])


def test_item_chat_missing_item_and_origin_do_not_create(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.path)
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
        calls.clear()
        assert (
            client.post("/api/capacities/missing/chat", headers=ORIGIN).status_code
            == 404
        )
        assert (
            client.post(
                "/api/capacities/missing/chat",
                headers={"Origin": "https://untrusted.example"},
            ).status_code
            == 403
        )
        assert not calls


def test_capacity_chat_bounds_full_run_reference_with_large_multibyte_sources(tmp_path):
    calls = []
    thread = str(uuid.uuid4())

    def handler(request):
        if request.url.path.endswith("/threads"):
            return httpx.Response(200, json={"thread": {"thread_id": thread}})
        if request.url.path.endswith("/session"):
            return httpx.Response(200, json={"session_channel_extension_id": "web"})
        if request.url.path.endswith("/messages"):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={"accepted": True, "run_id": "r"})
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
        capacity = client.post(
            "/api/capacities",
            headers=ORIGIN,
            json={"name": "Focus", "note": '🧭"\\' * 3000, "record": "🧭" * 10000},
        ).json()
        for index in range(20):
            response = client.post(
                "/api/memory",
                headers=ORIGIN,
                json={
                    "text": "Focus "
                    + str(index)
                    + " "
                    + ('context "\\ evidence ' * 400),
                    "source": "source" + str(index),
                },
            )
            assert response.status_code == 200, response.text
        response = client.post(f"/api/capacities/{capacity['id']}/chat", headers=ORIGIN)
        assert response.status_code == 200, response.text
        raw = "How should I think about this?"
        response = client.post(
            f"/api/companion/threads/{thread}/messages",
            headers=ORIGIN,
            json={"text": raw, "requestId": str(uuid.uuid4())},
        )
        assert response.status_code == 200, response.text
        assert calls[0]["content"] == raw
        reference = calls[0]["model_context"]["reference_text"]
        assert len(reference.encode()) <= 8192
        context = json.loads(
            reference.split("<leam_context>", 1)[1].split("</leam_context>", 1)[0]
        )
        assert context["linkedItem"]["item"]["name"] == "Focus"
        assert context["linkedItem"]["item"]["revision"] == 1
        assert context["linkedItem"]["truncatedFields"]
        assert (
            len(
                json.dumps(
                    context["linkedItem"], ensure_ascii=True, separators=(",", ":")
                ).encode()
            )
            <= 2048
        )
