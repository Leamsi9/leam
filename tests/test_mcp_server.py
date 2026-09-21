import httpx2
from fastapi.testclient import TestClient


def test_mcp_protocol_exposes_only_sourced_reads_and_proposals():
    from leam_api.mcp_server import create_mcp_app

    seen = []
    token = "private-test-tool-token-" + "x" * 40

    def api(request):
        seen.append(request)
        assert request.url == httpx2.URL("http://127.0.0.1:46400/api/internal/tools")
        assert request.headers["authorization"] == "Bearer " + token
        return httpx2.Response(
            200,
            json={
                "items": [{"text": "Prefer afternoons", "source": "User conversation"}]
            },
        )

    app = create_mcp_app(
        "http://127.0.0.1:46400", token, transport=httpx2.MockTransport(api)
    )
    with TestClient(
        app, base_url="https://127.0.0.1:46420", client=("127.0.0.1", 1234)
    ) as c:
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/json, text/event-stream",
        }
        rpc = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        }
        assert c.post("/mcp", json=rpc).status_code == 401
        assert (
            c.post(
                "/mcp",
                json=rpc,
                headers={**headers, "origin": "https://untrusted.example"},
            ).status_code
            == 403
        )
        init = c.post("/mcp", json=rpc, headers=headers)
        assert init.status_code == 200, init.text
        assert init.json()["result"]["protocolVersion"] == "2025-06-18"
        listed = c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=headers,
        )
        assert listed.status_code == 200, listed.text
        names = {tool["name"] for tool in listed.json()["result"]["tools"]}
        assert names == {
            "leam_context",
            "leam_system",
            "leam_today",
            "leam_calendar_events",
            "leam_calendar_links",
            "leam_calendar_inspect",
            "leam_propose",
            "leam_proposals",
            "leam_operation_schema",
        }
        called = c.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "leam_context", "arguments": {"query": "afternoon"}},
            },
            headers=headers,
        )
        assert called.status_code == 200, called.text
        assert "Prefer afternoons" in called.text
        assert "User conversation" in called.text
        assert len(seen) == 1
        assert token not in called.text
        assert c.post("/other", json=rpc, headers=headers).status_code == 404


def test_real_mcp_handler_to_domain_api_creates_only_an_unapproved_suggestion(tmp_path):
    from test_api import FakeCodex, login

    from leam_api.app import create_app
    from leam_api.mcp_server import create_mcp_app

    api = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    token = (tmp_path / "tools-token").read_text().strip()
    server = create_mcp_app(
        "http://127.0.0.1:46400",
        token,
        transport=httpx2.ASGITransport(app=api, client=("127.0.0.1", 1234)),
    )
    with (
        TestClient(api) as browser,
        TestClient(
            server, base_url="https://127.0.0.1:46420", client=("127.0.0.1", 1234)
        ) as mcp,
    ):
        login(browser)
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/json, text/event-stream",
        }
        suggestion = {
            "requestId": "c3a74474-a02d-4e68-9dfe-ae46d99f4d58",
            "threadId": "thread",
            "operation": "commitment.create",
            "input": {"title": "Tool proposed commitment"},
            "reason": "User planning request",
        }
        result = mcp.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "leam_propose",
                    "arguments": {"request": suggestion},
                },
            },
        )
        assert result.status_code == 200, result.text
        assert result.json()["result"]["isError"] is False, result.text
        assert browser.get("/api/commitments").json()["items"] == []
        proposal = browser.get("/api/proposals?threadId=thread").json()["items"][0]
        assert proposal["state"] == "pending"
        assert proposal["input"]["title"] == "Tool proposed commitment"
        assert (
            browser.post(
                "/api/proposals/" + proposal["id"] + "/approve",
                json={},
                headers={"origin": "http://testserver"},
            ).status_code
            == 200
        )
        assert (
            browser.get("/api/commitments").json()["items"][0]["title"]
            == "Tool proposed commitment"
        )
