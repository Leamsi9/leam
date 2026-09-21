import httpx
from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app
from leam_api.ironclaw import IronClaw


def test_runtime_credentials_stay_private_and_companion_retries_keep_action_id(
    tmp_path,
):
    calls = []

    def handle(request):
        assert request.headers["authorization"] == "Bearer private-runtime-token"
        calls.append(request)
        if request.url.path.endswith("/session"):
            return httpx.Response(200, json={"session_channel_extension_id": "web-app"})
        if request.url.path.endswith("/providers"):
            return httpx.Response(
                200,
                json={
                    "providers": [
                        {
                            "id": "test",
                            "adapter": "open_ai_completions",
                            "active": True,
                            "api_key_set": True,
                        }
                    ]
                },
            )
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"accepted": True, "run_id": "run1"})
        return httpx.Response(200, json={"ok": True})

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="private-runtime-token",
        transport=httpx.MockTransport(handle),
    )
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        runtime=runtime,
    )
    with TestClient(app) as client:
        assert client.get("/api/settings/providers").status_code == 401
        login(client)
        h = {"origin": "http://testserver"}
        assert (
            client.get("/api/settings/providers").json()["providers"][0]["id"] == "test"
        )
        r = client.post(
            "/api/settings/providers",
            json={
                "id": "test",
                "adapter": "open_ai_completions",
                "apiKey": "secret-model-key",
                "model": "chosen",
            },
            headers=h,
        )
        assert r.status_code == 200, r.text
        assert "secret-model-key" not in r.text
        import json

        sent = json.loads(calls[-1].content)
        assert sent["api_key"] == "secret-model-key"
        assert app.state.store.get("api_key") is None
        body = {"text": "Help me prioritise", "requestId": "companion-action-1"}
        a = client.post("/api/companion/threads/t/messages", json=body, headers=h)
        b = client.post("/api/companion/threads/t/messages", json=body, headers=h)
        assert a.status_code == b.status_code == 200
        messages = [r for r in calls if r.url.path.endswith("/messages")]
        assert len(messages) == 1
        assert (
            json.loads(messages[0].content)["client_action_id"] == "companion-action-1"
        )


def test_runtime_errors_do_not_echo_upstream_secrets(tmp_path):
    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="private-runtime-token",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(500, json={"detail": "secret-model-key"})
        ),
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
        r = client.get("/api/settings/providers")
        assert r.status_code == 502
        assert "secret-model-key" not in r.text


def test_memory_corrections_and_removal_are_revision_checked(tmp_path):
    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="test",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
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
        h = {"origin": "http://testserver"}
        a = client.post(
            "/api/memory",
            json={"text": "Morning focus", "source": "Conversation 1"},
            headers=h,
        ).json()
        b = client.patch(
            "/api/memory/" + a["id"],
            json={
                "text": "Afternoon focus",
                "source": "Correction by me",
                "revision": 1,
            },
            headers=h,
        )
        assert b.status_code == 200
        assert (
            client.delete(
                "/api/memory/" + a["id"] + "?revision=1", headers=h
            ).status_code
            == 409
        )
        assert (
            client.delete(
                "/api/memory/" + a["id"] + "?revision=2", headers=h
            ).status_code
            == 200
        )
        assert client.get("/api/memory").json()["items"] == []


def test_companion_uncertain_retry_reuses_identical_durable_payload(tmp_path):
    calls = []

    def handle(r):
        if r.url.path.endswith("/session"):
            return httpx.Response(200, json={"session_channel_extension_id": "web-app"})
        if r.url.path.endswith("/messages"):
            calls.append(r.content)
            if len(calls) == 1:
                raise httpx.ReadTimeout("response lost", request=r)
            return httpx.Response(200, json={"accepted": True})
        return httpx.Response(200, json={})

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="test",
        transport=httpx.MockTransport(handle),
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
        h = {"origin": "http://testserver"}
        body = {"text": "Hello", "requestId": "retry-original-action"}
        assert (
            client.post(
                "/api/companion/threads/t/messages", json=body, headers=h
            ).status_code
            == 502
        )
        client.post(
            "/api/memory", json={"text": "New context", "source": "Me"}, headers=h
        )
        assert (
            client.post(
                "/api/companion/threads/t/messages", json=body, headers=h
            ).status_code
            == 200
        )
        assert len(calls) == 2 and calls[0] == calls[1]
        assert (
            client.post(
                "/api/companion/threads/t/messages",
                json={**body, "text": "Different"},
                headers=h,
            ).status_code
            == 409
        )


def test_runtime_ignores_ambient_proxy_for_local_credentials(tmp_path, monkeypatch):
    import asyncio
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.server.label)
            raw = json.dumps({"server": self.server.label}).encode()
            self.send_response(200)
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    local = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    local.label = "runtime"
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    proxy.label = "proxy"
    for server in [local, proxy]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{proxy.server_port}")
    monkeypatch.setenv("ALL_PROXY", f"http://127.0.0.1:{proxy.server_port}")
    monkeypatch.setenv("NO_PROXY", "")

    async def run():
        runtime = IronClaw(
            f"http://127.0.0.1:{local.server_port}", None, token="test-secret"
        )
        try:
            return await runtime.request("GET", "/session")
        finally:
            await runtime.close()

    try:
        assert asyncio.run(run()) == {"server": "runtime"}
        assert seen == ["runtime"]
    finally:
        for server in [local, proxy]:
            server.shutdown()
            server.server_close()


def test_history_cursor_is_forwarded_and_completed_receipt_survives_runtime_outage(
    tmp_path,
):
    paths = []
    offline = False

    def handle(r):
        paths.append(str(r.url))
        if offline:
            raise httpx.ConnectError("offline", request=r)
        if r.url.path.endswith("/session"):
            return httpx.Response(200, json={"session_channel_extension_id": "web-app"})
        if r.url.path.endswith("/timeline"):
            return httpx.Response(200, json={"messages": [], "next_cursor": "older-2"})
        return httpx.Response(200, json={"accepted": True})

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="test",
        transport=httpx.MockTransport(handle),
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
        h = {"origin": "http://testserver"}
        assert (
            client.get("/api/companion/threads/t?cursor=older%2F1&limit=20").json()[
                "next_cursor"
            ]
            == "older-2"
        )
        assert "cursor=older%2F1" in paths[-1] and "limit=20" in paths[-1]
        body = {"text": "Hello", "requestId": "receipt-action-1"}
        first = client.post("/api/companion/threads/t/messages", json=body, headers=h)
        assert first.status_code == 200
        offline = True
        count = len(paths)
        replay = client.post("/api/companion/threads/t/messages", json=body, headers=h)
        assert replay.status_code == 200 and replay.json() == first.json()
        assert len(paths) == count


def test_subscription_model_catalog_and_selection_are_account_validated(tmp_path):
    import json

    class ModelsCodex(FakeCodex):
        async def request(self, method, params, **kwargs):
            self.calls.append((method, params))
            assert method == "model/list"
            return {
                "data": [
                    {
                        "id": "gpt-5.6-sol",
                        "model": "gpt-5.6-sol",
                        "displayName": "Sol",
                        "supportedReasoningEfforts": [
                            {"reasoningEffort": "medium", "description": "Balanced"},
                            {
                                "reasoningEffort": "high",
                                "description": "More reasoning",
                            },
                        ],
                        "defaultReasoningEffort": "medium",
                    }
                ],
                "nextCursor": None,
            }

    calls = []
    active = None
    confirm_effort = True

    def handle(request):
        nonlocal active
        calls.append(request)
        if request.url.path.endswith("/active"):
            active = json.loads(request.content)
            if not confirm_effort:
                active.pop("reasoning_effort", None)
        return httpx.Response(200, json={"providers": [], "active": active})

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="test",
        transport=httpx.MockTransport(handle),
    )
    codex = ModelsCodex()
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=codex,
        runtime=runtime,
    )
    with TestClient(app) as client:
        assert client.get("/api/settings/models").status_code == 401
        login(client)
        catalog = client.get("/api/settings/models")
        assert catalog.status_code == 200
        assert catalog.json()["default"] == {
            "providerId": "openai_codex",
            "model": "gpt-5.6-sol",
            "reasoningEffort": "medium",
        }
        h = {"origin": "http://testserver"}
        selection = {
            "providerId": "openai_codex",
            "model": "gpt-5.6-sol",
            "reasoningEffort": "high",
        }
        result = client.post(
            "/api/settings/providers/active", json=selection, headers=h
        )
        assert result.status_code == 200, result.text
        assert active == {
            "provider_id": "openai_codex",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "high",
        }
        assert (
            client.get("/api/settings/providers").json()["active"]["reasoning_effort"]
            == "high"
        )
        count = len(calls)
        for invalid in [
            {**selection, "model": "not-available"},
            {**selection, "reasoningEffort": "ultra"},
        ]:
            assert (
                client.post(
                    "/api/settings/providers/active", json=invalid, headers=h
                ).status_code
                == 422
            )
        assert len(calls) == count
        assert all(method == "model/list" for method, _ in codex.calls)
        # The released runtime ignores the unknown effort field: fail confirmation.
        confirm_effort = False
        assert (
            client.post(
                "/api/settings/providers/active", json=selection, headers=h
            ).status_code
            == 502
        )


def test_companion_grounds_live_model_without_provider_secrets(tmp_path):
    import json

    sent = []
    selected = {
        "provider_id": "openai_codex",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "medium",
    }

    def handle(request):
        if request.url.path.endswith("/providers"):
            return httpx.Response(
                200,
                json={
                    "active": selected,
                    "providers": [{"api_key": "never-send-this"}],
                },
            )
        if request.url.path.endswith("/session"):
            return httpx.Response(200, json={"session_channel_extension_id": "web-app"})
        if request.url.path.endswith("/messages"):
            sent.append(json.loads(request.content)["model_context"]["reference_text"])
            return httpx.Response(200, json={"accepted": True})
        return httpx.Response(200, json={})

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="private-runtime-token",
        transport=httpx.MockTransport(handle),
    )
    with TestClient(
        create_app(
            tmp_path,
            {"http://testserver"},
            bootstrap="bootstrap-for-tests",
            codex=FakeCodex(),
            runtime=runtime,
        )
    ) as client:
        assert client.get("/api/companion/system").status_code == 401
        login(client)
        first = client.get("/api/companion/system")
        assert first.json()["activeModel"] == selected
        assert "never-send-this" not in first.text
        h = {"origin": "http://testserver"}
        for index, model in enumerate(["gpt-5.6-sol", "gpt-6-astra"]):
            selected["model"] = model
            response = client.post(
                "/api/companion/threads/t/messages",
                json={
                    "text": "What model are you?",
                    "requestId": f"identity-test-{index}",
                },
                headers=h,
            )
            assert response.status_code == 200
            context = json.loads(
                sent[-1].split("<leam_context>")[1].split("</leam_context>")[0]
            )
            assert context["system"]["activeModel"]["model"] == model
            assert "never-send-this" not in sent[-1]
            assert "private-runtime-token" not in sent[-1]
