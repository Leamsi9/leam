import json
from unittest.mock import patch

import httpx
import httpx2
from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app
from leam_api.ironclaw import IronClaw
from leam_api.mcp_server import create_mcp_app


def test_browser_and_mcp_share_live_inspection_without_secrets_or_mutation(tmp_path):
    calls = []

    def upstream(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/providers"):
            return httpx.Response(
                200,
                json={
                    "active": {
                        "provider_id": "openai_codex",
                        "model": "gpt-5.6-sol",
                        "reasoning_effort": "medium",
                        "secret": "secret-sentinel",
                    },
                    "providers": [{"api_key": "secret-sentinel"}],
                },
            )
        return httpx.Response(
            200,
            json={
                "entries": [
                    {
                        "key": "tool.mcp-leam.leam_system",
                        "value": {"state": "always_allow", "secret": "secret-sentinel"},
                    }
                ]
            },
        )

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="private-runtime-token",
        transport=httpx.MockTransport(upstream),
    )
    with patch(
        "leam_api.system_inspection.SystemInspector._services",
        return_value={"runtime": {"state": "inactive"}},
    ):
        app = create_app(
            tmp_path,
            {"http://testserver"},
            bootstrap="bootstrap-for-tests",
            codex=FakeCodex(),
            runtime=runtime,
        )
        with TestClient(app, client=("127.0.0.1", 1234)) as browser:
            assert (
                browser.get("/api/companion/system?section=summary").status_code == 401
            )
            login(browser)
            result = browser.get("/api/companion/system?section=summary")
            assert result.status_code == 200
            sections = result.json()["sections"]
            assert sections["model"]["data"]["model"] == "gpt-5.6-sol"
            assert sections["release"]["status"] == "partial"
            assert sections["modules"]["data"]["decisionBackend"]["selected"] is None
            assert "secret-sentinel" not in result.text
            assert (
                browser.get("/api/companion/system?section=environment").status_code
                == 422
            )
            token = (tmp_path / "tools-token").read_text().strip()

            def bridge(request):
                assert request.url.path == "/api/internal/tools"
                forwarded = browser.post(
                    "/api/internal/tools",
                    json=json.loads(request.content),
                    headers={"authorization": request.headers["authorization"]},
                )
                return httpx2.Response(
                    forwarded.status_code,
                    content=forwarded.content,
                    headers={"content-type": "application/json"},
                )

            mcp = create_mcp_app(
                "http://127.0.0.1:46400", token, transport=httpx2.MockTransport(bridge)
            )
            with TestClient(
                mcp, base_url="https://127.0.0.1:46420", client=("127.0.0.1", 1234)
            ) as client:
                response = client.post(
                    "/mcp",
                    headers={
                        "authorization": "Bearer " + token,
                        "accept": "application/json, text/event-stream",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "leam_system",
                            "arguments": {"section": "model"},
                        },
                    },
                )
                assert response.status_code == 200
                assert "gpt-5.6-sol" in response.text
                assert (
                    "secret-sentinel" not in response.text
                    and token not in response.text
                )
            denied = browser.post(
                "/api/internal/tools",
                headers={"authorization": "Bearer " + token},
                json={
                    "tool": "leam_system",
                    "arguments": {"section": "model", "command": "restart"},
                },
            )
            assert denied.status_code == 422
    assert calls and all(method == "GET" for method, _ in calls)


def test_inspection_upstream_failure_is_explicit_and_redacted(tmp_path):
    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="private-runtime-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(500, json={"error": "secret-sentinel"})
        ),
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
        login(client)
        response = client.get("/api/companion/system")
        assert response.status_code == 200
        assert response.json()["available"] is False
        assert response.json()["activeModel"]["model"] is None
        assert "secret-sentinel" not in response.text


def test_summary_preserves_healthy_sections_when_release_or_database_fails(tmp_path):
    import sqlite3
    from leam_api.system_inspection import SystemInspector

    for case in ["release", "lock", "database"]:
        root = tmp_path / case / "release"
        (root / "docs/architecture").mkdir(parents=True)
        (root / "docs/architecture/runtime-lock.json").write_text(
            "[]" if case == "lock" else "{}"
        )
        if case == "release":
            (root / "release.json").write_text("[]")
        runtime = IronClaw(
            "http://127.0.0.1:46410",
            None,
            token="private",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"active": {"model": "test-model"}, "entries": []}
                )
            ),
        )

        def factory(store, runtime, **kwargs):
            inspector = SystemInspector(
                store,
                runtime,
                root=root,
                probe=lambda: {"runtime": {"state": "inactive"}},
                **kwargs,
            )
            if case == "database":

                class FailedStore:
                    def connect(self):
                        raise sqlite3.OperationalError("secret-sentinel")

                inspector.store = FailedStore()
            return inspector

        with patch("leam_api.app.SystemInspector", side_effect=factory):
            app = create_app(
                tmp_path / case / "state",
                {"http://testserver"},
                bootstrap="bootstrap-for-tests",
                codex=FakeCodex(),
                runtime=runtime,
            )
        with TestClient(app) as client:
            login(client)
            response = client.get("/api/companion/system?section=summary")
            assert response.status_code == 200
            sections = response.json()["sections"]
            assert (
                sections["model"]["status"]
                == sections["operations"]["status"]
                == "available"
            )
            assert sections["modules" if case == "database" else "release"][
                "status"
            ] in {"partial", "unavailable"}
            assert "secret-sentinel" not in response.text
