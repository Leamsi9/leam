import json

from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app

H = {"origin": "http://testserver"}


def test_tool_service_token_can_read_and_propose_but_never_approve(tmp_path):
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    with TestClient(app, client=("127.0.0.1", 12345)) as c:
        login(c)
        memory = c.post(
            "/api/memory",
            json={
                "text": "Prefer an afternoon walk",
                "source": "Conversation with user",
                "category": "preference",
            },
            headers=H,
        ).json()
        c.post(
            "/api/capacities", json={"name": "Health", "note": "Wellbeing"}, headers=H
        )
        c.post(
            "/api/commitments",
            json={"title": "Afternoon walk", "kind": "habit"},
            headers=H,
        )
        token_path = tmp_path / "tools-token"
        assert token_path.exists()
        assert token_path.stat().st_mode & 0o777 == 0o600
        token = token_path.read_text().strip()
        bearer = {"authorization": "Bearer " + token}
        request = {
            "tool": "leam_context",
            "arguments": {"query": "walk", "limit": 1, "offset": 0},
        }
        assert c.post("/api/internal/tools", json=request).status_code == 401
        assert (
            c.post(
                "/api/internal/tools",
                json=request,
                headers={**bearer, "origin": "https://attacker.example"},
            ).status_code
            == 403
        )
        # This credential is separate from the authenticated browser session.
        c.cookies.clear()
        response = c.post("/api/internal/tools", json=request, headers=bearer)
        assert response.status_code == 200, response.text
        assert len(response.json()["items"]) == 1
        second = c.post(
            "/api/internal/tools",
            json={**request, "arguments": {"query": "walk", "limit": 1, "offset": 1}},
            headers=bearer,
        ).json()
        assert len(second["items"]) == 1
        records = response.json()["items"] + second["items"]
        assert any(
            r["id"] == memory["id"] and r["source"] == "Conversation with user"
            for r in records
        )
        suggestion = {
            "requestId": "ef56e0ca-9e1d-431e-bb72-6ba31ca5349b",
            "threadId": "planning",
            "operation": "commitment.create",
            "input": {"title": "Drink water"},
            "reason": "Suggested routine",
        }
        proposed = c.post(
            "/api/internal/tools",
            json={"tool": "leam_propose", "arguments": suggestion},
            headers=bearer,
        )
        assert proposed.status_code == 200, proposed.text
        key = proposed.json()["id"]
        assert (
            c.post(
                "/api/proposals/" + key + "/approve", json={}, headers={**bearer, **H}
            ).status_code
            == 401
        )
        assert (
            c.post(
                "/api/internal/tools",
                json={"tool": "leam_approve", "arguments": {"id": key}},
                headers=bearer,
            ).status_code
            == 422
        )
        assert c.get("/api/accounts", headers=bearer).status_code == 401
        assert token not in json.dumps(proposed.json())


def test_tool_ingress_rejects_non_loopback_and_oversized_requests_and_keeps_token_on_restart(
    tmp_path,
):
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    token = (tmp_path / "tools-token").read_text()
    bearer = {"authorization": "Bearer " + token}
    request = {"tool": "leam_context", "arguments": {}}
    with TestClient(app, client=("192.0.2.4", 1234)) as c:
        assert (
            c.post("/api/internal/tools", json=request, headers=bearer).status_code
            == 403
        )
    restarted = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    assert (tmp_path / "tools-token").read_text() == token
    with TestClient(restarted, client=("127.0.0.1", 1234)) as c:
        assert (
            c.post(
                "/api/internal/tools", content=b"x" * (1024 * 1024 + 1), headers=bearer
            ).status_code
            == 413
        )
        schema = c.post(
            "/api/internal/tools",
            json={
                "tool": "leam_operation_schema",
                "arguments": {"operation": "commitment.create"},
            },
            headers=bearer,
        )
        assert schema.status_code == 200
        assert "title" in schema.json()["inputSchema"]["properties"]
        assert (
            c.post(
                "/api/internal/tools",
                json=request,
                headers={**bearer, "origin": "http://testserver"},
            ).status_code
            == 403
        )
