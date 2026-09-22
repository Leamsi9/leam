import httpx2
import pytest
from fastapi.testclient import TestClient
from test_api import FakeCodex

from leam_api.app import create_app
from leam_api.mcp_server import create_mcp_app


def resource():
    return {
        "id": "generated-report-v1",
        "title": "Report",
        "kind": "markdown",
        "content": "# Useful output\nGenerated for the user",
        "filename": "report.md",
        "threadId": "declared-thread",
        "turnId": "declared-turn",
    }


def prepared(tmp_path):
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    token = (tmp_path / "tools-token").read_text().strip()
    return app, token


def test_authenticated_domain_save_retry_read_and_conflict(tmp_path):
    app, token = prepared(tmp_path)
    with TestClient(app, client=("127.0.0.1", 1234)) as client:
        payload = {"tool": "leam_resource_save", "arguments": resource()}
        headers = {"authorization": "Bearer " + token}
        assert client.post("/api/internal/tools", json=payload).status_code == 401
        assert (
            client.post(
                "/api/internal/tools",
                json=payload,
                headers={**headers, "origin": "https://hostile.example"},
            ).status_code
            == 403
        )
        saved = client.post("/api/internal/tools", json=payload, headers=headers)
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert (
            body["state"] == "saved" and body["visibility"] == "private_authenticated"
        )
        assert body["sourceReferencesVerified"] is False
        assert body["url"] == "/?artifact=generated-report-v1"
        assert "content" not in body["item"]
        assert (
            client.post("/api/internal/tools", json=payload, headers=headers).json()
            == body
        )
        changed = {
            "tool": "leam_resource_save",
            "arguments": {**resource(), "content": "changed"},
        }
        assert (
            client.post(
                "/api/internal/tools", json=changed, headers=headers
            ).status_code
            == 409
        )
        listing = client.post(
            "/api/internal/tools",
            json={"tool": "leam_resources", "arguments": {"query": "Report"}},
            headers=headers,
        ).json()
        assert [row["id"] for row in listing["items"]] == ["generated-report-v1"]
        detail = client.post(
            "/api/internal/tools",
            json={"tool": "leam_resources", "arguments": {"id": "generated-report-v1"}},
            headers=headers,
        ).json()
        assert detail["contentIncluded"] is False
        assert detail["item"]["sha256"] == body["item"]["sha256"]
        # Tool token is not a browser session and cannot open private resources.
        assert (
            client.get(
                "/api/artifacts/generated-report-v1", headers=headers
            ).status_code
            == 401
        )
        assert client.get("/api/artifacts", headers=headers).status_code == 401


@pytest.mark.parametrize(
    "extra",
    [
        {"path": "/etc/passwd"},
        {"url": "https://example.invalid/secret"},
        {"approve": True},
        {"kind": "png"},
        {"content": "é" * 140000},
        {"content": "x" * 262145},
        {"id": "../secret"},
    ],
)
def test_producer_rejects_paths_fetching_approval_and_oversized_content(
    tmp_path, extra
):
    app, token = prepared(tmp_path)
    with TestClient(app, client=("127.0.0.1", 1234)) as client:
        result = client.post(
            "/api/internal/tools",
            json={"tool": "leam_resource_save", "arguments": {**resource(), **extra}},
            headers={"authorization": "Bearer " + token},
        )
        assert result.status_code == 422


def test_real_mcp_to_domain_publication_and_tool_annotations(tmp_path):
    app, token = prepared(tmp_path)
    server = create_mcp_app(
        "http://127.0.0.1:46400",
        token,
        transport=httpx2.ASGITransport(app=app, client=("127.0.0.1", 1234)),
    )
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/json, text/event-stream",
    }
    with TestClient(
        server, base_url="https://127.0.0.1:46420", client=("127.0.0.1", 1234)
    ) as client:
        init = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert init.status_code == 200
        listed = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ).json()["result"]["tools"]
        by_name = {tool["name"]: tool for tool in listed}
        assert by_name["leam_resources"]["annotations"]["readOnlyHint"] is True
        assert by_name["leam_resource_save"]["annotations"]["readOnlyHint"] is False
        assert by_name["leam_resource_save"]["annotations"]["idempotentHint"] is True
        result = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "leam_resource_save", "arguments": resource()},
            },
        )
        assert result.status_code == 200
        assert not result.json()["result"].get("isError")
        assert "/?artifact=generated-report-v1" in result.text
        assert token not in result.text
        assert "Useful output" not in result.text


def test_tool_permission_profile_preserves_approval_for_generated_resource():
    from leam_api.tool_permissions import catalog, ALLOWED
    from leam_api.host_tool_ceiling import RECOMMENDED

    names = ["mcp-leam.leam_resources", "mcp-leam.leam_resource_save"]
    entries = [{"key": "agent.auto_approve_tools", "value": False}]
    for name in names:
        entries.append(
            {
                "key": "tool." + name,
                "mutable": True,
                "value": {
                    "name": name,
                    "state": "disabled",
                    "default_state": "ask_each_time",
                    "locked": False,
                    "effective_source": "default",
                    "description": "Resources",
                },
            }
        )
    values = {item["id"]: item for item in catalog({"entries": entries})["items"]}
    assert values[names[0]]["recommendedState"] == "always_allow"
    assert values[names[1]]["recommendedState"] == "ask_each_time"
    assert all(name in ALLOWED and name in RECOMMENDED for name in names)
    assert all(not item["protected"] for item in values.values())
