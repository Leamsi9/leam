"""Synthetic-only regression through the authenticated domain and companion callers."""

import json

import httpx
from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app
from leam_api.ironclaw import IronClaw

H = {"origin": "http://testserver"}


def encoded(value):
    return len(json.dumps(value, ensure_ascii=True).encode())


def test_memory_search_budget_late_evidence_detail_revision_and_full_browser(tmp_path):
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    with TestClient(app, client=("127.0.0.1", 1234)) as c:
        login(c)
        records = [
            app.state.store.create(
                "memory",
                {
                    "text": "界😀" * 4200 + " late-evidence " + str(i),
                    "source": "源😀" * 450,
                    "category": "preference",
                },
            )
            for i in range(20)
        ]
        bearer = {
            "authorization": "Bearer " + (tmp_path / "tools-token").read_text().strip()
        }

        def tool(**args):
            return c.post(
                "/api/internal/tools",
                headers=bearer,
                json={"tool": "leam_context", "arguments": {"kind": "memory", **args}},
            )

        response = tool(query="late-evidence")
        assert response.status_code == 200, response.text
        page = response.json()
        assert encoded(page) <= 8192
        assert page["total"] == 20 and page["partial"]
        full_bytes = encoded({"items": records})
        assert encoded(page) < full_bytes / 20
        print(
            f"synthetic-memory-search: full-record-bytes={full_bytes}, bounded-page-bytes={encoded(page)}, records=20"
        )
        assert all("late-evidence" in r["text"] for r in page["items"])
        assert all(r["revision"] == 1 and r["partial"] for r in page["items"])
        assert page["nextOffset"] == len(page["items"])
        ids = {r["id"] for r in page["items"]}
        while page["nextOffset"] is not None:
            page = tool(query="late-evidence", offset=page["nextOffset"]).json()
            assert encoded(page) <= 8192
            ids.update(r["id"] for r in page["items"])
        assert len(ids) == 20
        original = records[0]
        chunks, cursor = [], 0
        while True:
            detail = tool(id=original["id"], revision=1, cursor=cursor).json()
            assert encoded(detail) <= 8192
            chunks.append(detail["recordJson"])
            if detail["nextCursor"] is None:
                break
            assert detail["nextCursor"] > cursor
            cursor = detail["nextCursor"]
        restored = json.loads("".join(chunks))
        assert restored["text"] == original["text"]
        assert restored["source"] == original["source"]
        assert c.get("/api/memory").json()["items"] == app.state.store.entities(
            "memory"
        )
        edited = c.patch(
            "/api/memory/" + original["id"],
            headers=H,
            json={
                "text": "new-current-value",
                "source": "edit",
                "category": "preference",
                "revision": 1,
            },
        )
        assert edited.status_code == 200
        assert tool(id=original["id"], revision=1, cursor=cursor).status_code == 409
        assert tool(query="new-current-value").json()["total"] == 1
        assert (
            c.delete(
                "/api/memory/" + original["id"] + "?revision=2", headers=H
            ).status_code
            == 200
        )
        assert tool(query="new-current-value").json()["total"] == 0
        assert tool(id=original["id"]).status_code == 404


def test_companion_memory_grounding_budget_and_retry_after_edit(tmp_path):
    sent = []

    def runtime_call(request):
        if request.url.path.endswith("/session"):
            return httpx.Response(200, json={"session_channel_extension_id": "web-app"})
        if request.url.path.endswith("/messages"):
            sent.append(json.loads(request.content))
            if len(sent) == 1:
                return httpx.Response(503, json={})
            return httpx.Response(200, json={"accepted": True, "run_id": "synthetic"})
        return httpx.Response(200, json={})

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="test",
        transport=httpx.MockTransport(runtime_call),
    )
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        runtime=runtime,
    )
    memories = [
        app.state.store.create(
            "memory",
            {
                "text": "界😀" * 4200 + " late-evidence",
                "source": "源😀" * 450,
                "category": "preference",
            },
        )
        for _ in range(20)
    ]
    with TestClient(app) as c:
        login(c)
        body = {
            "text": "late-evidence\nUser message:\nexact text",
            "requestId": "synthetic-message",
        }
        assert (
            c.post(
                "/api/companion/threads/synthetic/messages", headers=H, json=body
            ).status_code
            == 502
        )
        app.state.store.delete(memories[0]["id"], "memory", 1)
        assert (
            c.post(
                "/api/companion/threads/synthetic/messages", headers=H, json=body
            ).status_code
            == 200
        )
        assert sent[0] == sent[1]
        grounding = json.loads(
            sent[0]["model_context"]["reference_text"]
            .split("<leam_context>", 1)[1]
            .split("</leam_context>", 1)[0]
        )["memoryContext"]
        assert encoded(grounding) <= 4096
        assert grounding["total"] == 20 and grounding["partial"]
        print(
            f"synthetic-auto-grounding: bytes={encoded(grounding)}, available-records=20, admitted-records={len(grounding['items'])}"
        )
        assert sent[0]["content"].endswith(body["text"])
        assert all("late-evidence" in r["text"] for r in grounding["items"])
        assert (
            c.post(
                "/api/companion/threads/synthetic/messages",
                headers=H,
                json={**body, "requestId": "synthetic-next"},
            ).status_code
            == 200
        )
        fresh = json.loads(
            sent[-1]["model_context"]["reference_text"]
            .split("<leam_context>", 1)[1]
            .split("</leam_context>", 1)[0]
        )["memoryContext"]
        assert fresh["total"] == 19
        assert memories[0]["id"] not in {r["id"] for r in fresh["items"]}


def test_real_mcp_memory_search_and_detail_are_bounded(tmp_path):
    import httpx2

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
    saved = api.state.store.create(
        "memory",
        {
            "text": "start " + "界😀" * 4200 + " needle",
            "source": "源😀" * 450,
            "category": "preference",
        },
    )
    # Oversized neighboring domain records must not break the aggregate bound.
    api.state.store.create("capacity", {"name": "界😀" * 10000, "note": "synthetic"})
    with TestClient(
        server, base_url="https://127.0.0.1:46420", client=("127.0.0.1", 1234)
    ) as mcp:
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/json, text/event-stream",
        }

        def call(args):
            response = mcp.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "leam_context", "arguments": args},
                },
            )
            assert response.status_code == 200, response.text
            result = response.json()["result"]
            assert result["isError"] is False, result
            assert "structuredContent" not in result
            assert encoded(result) <= 8192
            content = json.loads(result["content"][0]["text"])
            assert encoded(content) <= 8192
            return content

        found = call({"kind": "memory", "query": "needle"})
        assert found["items"][0]["id"] == saved["id"]
        assert "needle" in found["items"][0]["text"]
        broad = call({"kind": "all"})
        assert broad["items"] and broad["partial"]
        assert any(
            r.get("recordType") == "capacity" and r["partial"] for r in broad["items"]
        )
        chunks, cursor = [], 0
        while True:
            record = call(
                {"kind": "memory", "id": saved["id"], "revision": 1, "cursor": cursor}
            )
            chunks.append(record["recordJson"])
            if record["nextCursor"] is None:
                break
            cursor = record["nextCursor"]
        assert json.loads("".join(chunks))["text"] == saved["text"]
