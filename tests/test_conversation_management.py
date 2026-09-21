import httpx
from fastapi.testclient import TestClient
from shared_coding_fixtures import SHARED_THREAD
from test_api import FakeCodex, login

from leam_api.app import create_app
from leam_api.ironclaw import IronClaw


def make(tmp_path):
    calls = []
    outcome = {"delete": 200}

    def handle(request):
        calls.append(request)
        if request.method == "DELETE":
            return httpx.Response(
                outcome["delete"],
                json={
                    "thread_id": "a",
                    "deleted": True,
                    "secret": "upstream-private-diagnostic",
                }
                if outcome["delete"] != 200
                else {"thread_id": "a", "deleted": True},
            )
        if request.url.path.endswith("/timeline"):
            return httpx.Response(200, json={"messages": []})
        return httpx.Response(
            200,
            json={
                "threads": [{"thread_id": "a", "title": "Original"}],
                "next_cursor": "next opaque/1",
            },
        )

    codex = FakeCodex()
    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="fixture-private",
        transport=httpx.MockTransport(handle),
    )
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=codex,
        runtime=runtime,
    )
    return TestClient(app), calls, outcome, codex


def test_companion_pagination_and_revisioned_local_title(tmp_path):
    client, calls, _, _ = make(tmp_path)
    with client:
        login(client)
        h = {"origin": "http://testserver"}
        response = client.get(
            "/api/companion/threads", params={"cursor": "opaque +/&", "limit": 2}
        )
        assert response.status_code == 200
        assert calls[-1].url.params["cursor"] == "opaque +/&"
        assert calls[-1].url.params["limit"] == "2"
        assert response.json()["next_cursor"] == "next opaque/1"
        assert response.json()["threads"][0]["title_revision"] == 0
        renamed = client.patch(
            "/api/companion/threads/a",
            json={"title": "  My  conversation  ", "revision": 0},
            headers=h,
        )
        assert renamed.status_code == 200, renamed.text
        assert calls[-1].url.path.endswith("/a/timeline")
        assert calls[-1].url.params["limit"] == "1"
        assert renamed.json()["title"] == "My conversation"
        assert (
            client.get("/api/companion/threads").json()["threads"][0]["title"]
            == "My conversation"
        )
        conflict = client.patch(
            "/api/companion/threads/a",
            json={"title": "Stale overwrite", "revision": 0},
            headers=h,
        )
        assert conflict.status_code == 409
        assert (
            client.get("/api/companion/threads").json()["threads"][0]["title_revision"]
            == 1
        )
        assert all(request.method == "GET" for request in calls)


def test_delete_requires_auth_origin_confirmation_and_runtime_success(tmp_path):
    client, calls, outcome, _ = make(tmp_path)
    with client:
        assert (
            client.request(
                "DELETE",
                "/api/companion/threads/a",
                json={"confirmed": True},
                headers={"origin": "http://testserver"},
            ).status_code
            == 401
        )
        login(client)
        h = {"origin": "http://testserver"}
        for body in [{"confirmed": False}, {"confirmed": True, "extra": "bad"}]:
            assert (
                client.request(
                    "DELETE", "/api/companion/threads/a", json=body, headers=h
                ).status_code
                == 422
            )
        assert (
            client.request(
                "DELETE",
                "/api/companion/threads/a",
                json={"confirmed": True},
                headers={"origin": "https://bad.example"},
            ).status_code
            == 403
        )
        assert calls == []
        client.patch(
            "/api/companion/threads/a",
            json={"title": "Keep during failure", "revision": 0},
            headers=h,
        )
        outcome["delete"] = 409
        response = client.request(
            "DELETE", "/api/companion/threads/a", json={"confirmed": True}, headers=h
        )
        assert response.status_code == 409
        assert "upstream-private" not in response.text
        assert (
            client.get("/api/companion/threads").json()["threads"][0]["title"]
            == "Keep during failure"
        )
        outcome["delete"] = 500
        response = client.request(
            "DELETE", "/api/companion/threads/a", json={"confirmed": True}, headers=h
        )
        assert response.status_code == 502
        assert "upstream-private" not in response.text
        outcome["delete"] = 200
        response = client.request(
            "DELETE", "/api/companion/threads/a", json={"confirmed": True}, headers=h
        )
        assert response.status_code == 200
        assert (
            client.get("/api/companion/threads").json()["threads"][0]["title"]
            == "Original"
        )
        outcome["delete"] = 404
        assert (
            client.request(
                "DELETE",
                "/api/companion/threads/a",
                json={"confirmed": True},
                headers=h,
            ).json()["alreadyDeleted"]
            is True
        )


def test_coding_native_rename_pagination_and_shared_protection(tmp_path):
    client, _, _, codex = make(tmp_path)
    with client:
        login(client)
        h = {"origin": "http://testserver"}
        r = client.patch(
            "/api/codex/threads/normal", json={"title": "My coding session"}, headers=h
        )
        assert r.status_code == 200
        assert codex.calls[-1] == (
            "thread/name/set",
            {"threadId": "normal", "name": "My coding session"},
        )
        before = len(codex.calls)
        assert (
            client.patch(
                "/api/codex/threads/" + SHARED_THREAD,
                json={"title": "Do not rename build"},
                headers=h,
            ).status_code
            == 409
        )
        assert len(codex.calls) == before
        r = client.get("/api/codex/threads", params={"cursor": "next /&+", "limit": 3})
        assert r.status_code == 200
        assert codex.calls[-1] == (
            "thread/list",
            {
                "limit": 3,
                "cursor": "next /&+",
                "sourceKinds": ["cli", "vscode", "appServer"],
            },
        )
        assert all(item["id"] != SHARED_THREAD for item in r.json()["data"])
        assert client.get("/api/codex/threads?limit=101").status_code == 422


def test_coding_delete_requires_explicit_cascade_and_protects_linked_ancestry(tmp_path):
    client, _, _, codex = make(tmp_path)
    original = codex.request
    parents = {}

    async def request(method, params, *, expected_generation=None):
        if method == "thread/read":
            codex.calls.append((method, params))
            return {
                "thread": {
                    "id": params["threadId"],
                    "parentThreadId": parents.get(params["threadId"]),
                    "status": {"type": "active"},
                }
            }
        return await original(method, params, expected_generation=expected_generation)

    codex.request = request
    with client:
        login(client)
        h = {"origin": "http://testserver"}
        body = {"confirmed": True, "deleteChildren": True, "stopRunning": True}
        for invalid in [
            {"confirmed": True},
            {**body, "stopRunning": False},
            {**body, "extra": True},
        ]:
            assert (
                client.request(
                    "DELETE", "/api/codex/threads/ordinary", json=invalid, headers=h
                ).status_code
                == 422
            )
        assert not codex.calls
        client.app.state.store.set(
            "ticket-chat:ticket", {"state": "ready", "threadId": "ticket-thread"}
        )
        for target in [SHARED_THREAD, "ticket-thread"]:
            assert (
                client.request(
                    "DELETE", "/api/codex/threads/" + target, json=body, headers=h
                ).status_code
                == 409
            )
        assert not codex.calls
        parents[SHARED_THREAD] = "protected-ancestor"
        r = client.request(
            "DELETE", "/api/codex/threads/protected-ancestor", json=body, headers=h
        )
        assert r.status_code == 409
        assert not any(method == "thread/delete" for method, _ in codex.calls)
        parents["ticket-thread"] = "ticket-ancestor"
        assert (
            client.request(
                "DELETE", "/api/codex/threads/ticket-ancestor", json=body, headers=h
            ).status_code
            == 409
        )
        assert not any(method == "thread/delete" for method, _ in codex.calls)
        result = client.request(
            "DELETE", "/api/codex/threads/ordinary", json=body, headers=h
        )
        assert result.status_code == 200, result.text
        assert codex.calls[-1] == ("thread/delete", {"threadId": "ordinary"})
        assert (
            client.app.state.store.get("ticket-chat:ticket")["threadId"]
            == "ticket-thread"
        )


def test_coding_delete_rejects_malformed_mapping_and_ancestry_cycle(tmp_path):
    client, _, _, codex = make(tmp_path)
    original = codex.request

    async def cyclic(method, params, *, expected_generation=None):
        if method == "thread/read":
            codex.calls.append((method, params))
            return {
                "thread": {
                    "id": params["threadId"],
                    "parentThreadId": params["threadId"],
                }
            }
        return await original(method, params, expected_generation=expected_generation)

    codex.request = cyclic
    with client:
        login(client)
        h = {"origin": "http://testserver"}
        body = {"confirmed": True, "deleteChildren": True, "stopRunning": True}
        response = client.request(
            "DELETE", "/api/codex/threads/ordinary", json=body, headers=h
        )
        assert response.status_code == 409 and "cycle" in response.text
        assert not any(method == "thread/delete" for method, _ in codex.calls)
        with client.app.state.store.connect() as db:
            db.execute(
                "INSERT INTO settings VALUES (?,?)",
                ("ticket-chat:bad", "not-json-private-marker"),
            )
        response = client.request(
            "DELETE", "/api/codex/threads/ordinary", json=body, headers=h
        )
        assert response.status_code == 409 and "private-marker" not in response.text
        assert not any(method == "thread/delete" for method, _ in codex.calls)
