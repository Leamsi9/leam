from uuid import uuid4

from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app

H = {"origin": "http://testserver"}


def body(operation="memory.create", **data):
    return {
        "requestId": str(uuid4()),
        "threadId": "memory-qa",
        "operation": operation,
        "input": data or {"text": "Likes tea", "source": "User said so"},
        "reason": "User requested this",
    }


def test_default_memory_auto_approval_and_exact_retry(tmp_path):
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    with TestClient(app) as c:
        login(c)
        policy = c.get("/api/proposals/memory/policy").json()
        assert policy == {
            "revision": 0,
            "createRequiresApproval": False,
            "editRequiresApproval": False,
            "removeRequiresApproval": False,
        }
        request = body()
        r = c.post("/api/proposals", json=request, headers=H)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "complete"
        assert r.json()["review"]["approval"]["mode"] == "automatic"
        assert (
            c.post("/api/proposals", json=request, headers=H).json()["result"]
            == r.json()["result"]
        )
        memory = c.get("/api/memory").json()["items"]
        assert len(memory) == 1
        row = memory[0]
        edited = c.post(
            "/api/proposals",
            json=body(
                "memory.edit",
                id=row["id"],
                revision=row["revision"],
                text="Prefers green tea",
                source="User correction",
            ),
            headers=H,
        )
        assert edited.json()["state"] == "complete", edited.text
        row = c.get("/api/memory").json()["items"][0]
        deleted = c.post(
            "/api/proposals",
            json=body("memory.remove", id=row["id"], revision=row["revision"]),
            headers=H,
        )
        assert deleted.json()["state"] == "complete", deleted.text
        assert not c.get("/api/memory").json()["items"]


def test_memory_manual_policy_existing_pending_and_scoped_list(tmp_path):
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    with TestClient(app) as c:
        login(c)
        policy = {
            "revision": 0,
            "createRequiresApproval": True,
            "editRequiresApproval": True,
            "removeRequiresApproval": True,
        }
        r = c.put("/api/proposals/memory/policy", json=policy, headers=H)
        assert r.status_code == 200, r.text
        assert r.json()["revision"] == 1
        assert (
            c.put("/api/proposals/memory/policy", json=policy, headers=H).status_code
            == 409
        )
        request = body()
        pending = c.post("/api/proposals", json=request, headers=H).json()
        assert pending["state"] == "pending"
        c.put(
            "/api/proposals/memory/policy",
            json={**policy, "revision": 1, "createRequiresApproval": False},
            headers=H,
        ).raise_for_status()
        assert (
            c.post("/api/proposals", json=request, headers=H).json()["state"]
            == "pending"
        )
        assert c.get("/api/memory").json()["items"] == []
        listing = c.get("/api/proposals/memory").json()["items"]
        assert listing[0]["id"] == pending["id"]
        assert (
            c.get("/api/proposals?threadId=memory-qa&excludeMemory=true").json()[
                "items"
            ]
            == []
        )
        other = c.post(
            "/api/proposals", json=body("capacity.create", name="Energy"), headers=H
        ).json()
        assert other["state"] == "pending"
        assert all(
            x["operation"].startswith("memory.")
            for x in c.get("/api/proposals/memory").json()["items"]
        )
        assert (
            c.post(
                "/api/proposals/" + pending["id"] + "/approve", json={}, headers=H
            ).json()["state"]
            == "complete"
        )


def test_tool_policy_and_browser_only_configuration(tmp_path):
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    with TestClient(app, client=("127.0.0.1", 12345)) as c:
        login(c)
        bearer = {
            "authorization": "Bearer " + (tmp_path / "tools-token").read_text().strip()
        }
        c.cookies.clear()

        def tool(name, args):
            return c.post(
                "/api/internal/tools",
                json={"tool": name, "arguments": args},
                headers=bearer,
            )

        schema = tool("leam_operation_schema", {"operation": "memory.create"})
        assert schema.json()["approvalPolicy"]["mode"] == "automatic"
        result = tool("leam_propose", body()).json()
        assert result["state"] == "complete"
        assert (
            tool("leam_operation_schema", {"operation": "calendar.create"}).json()[
                "approvalPolicy"
            ]["mode"]
            == "manual"
        )
        assert (
            c.put(
                "/api/proposals/memory/policy",
                json={"revision": 0},
                headers={**H, **bearer},
            ).status_code
            == 401
        )
        assert c.get("/api/proposals/memory", headers=bearer).status_code == 401


def test_restart_between_proposal_receipt_and_automatic_save(tmp_path, monkeypatch):
    import pytest

    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    request = body()
    with TestClient(app) as c:
        login(c)

        async def interrupted(key):
            raise RuntimeError("simulated process loss after durable receipt")

        monkeypatch.setattr(app.state.proposals, "_approve_locked", interrupted)
        with pytest.raises(RuntimeError):
            c.post("/api/proposals", json=request, headers=H)
        assert c.get("/api/memory").json()["items"] == []
    restarted = create_app(tmp_path, {"http://testserver"}, codex=FakeCodex())
    with TestClient(restarted) as c:
        c.post(
            "/api/auth/login", json={"password": "long-password-for-tests"}, headers=H
        ).raise_for_status()
        # The originally accepted automatic policy survives a newer manual policy.
        c.put(
            "/api/proposals/memory/policy",
            json={"revision": 0, "createRequiresApproval": True},
            headers=H,
        ).raise_for_status()
        for _ in range(2):
            r = c.post("/api/proposals", json=request, headers=H)
            assert r.json()["state"] == "complete", r.text
        assert len(c.get("/api/memory").json()["items"]) == 1


def test_independent_callers_cannot_duplicate_automatic_memory(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    first = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    second = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    request = body()
    with TestClient(first) as a, TestClient(second) as b:
        login(a)
        b.post(
            "/api/auth/login", json={"password": "long-password-for-tests"}, headers=H
        ).raise_for_status()
        barrier = Barrier(2)
        for app in (first, second):
            original = app.state.proposals.local

            def local(*args, _original=original, **kwargs):
                barrier.wait(timeout=5)
                return _original(*args, **kwargs)

            app.state.proposals.local = local
        with ThreadPoolExecutor(2) as pool:
            results = list(
                pool.map(
                    lambda c: c.post("/api/proposals", json=request, headers=H), [a, b]
                )
            )
        assert [r.status_code for r in results] == [200, 200]
        assert all(r.json()["state"] == "complete" for r in results)
        assert results[0].json()["result"] == results[1].json()["result"]
        assert len(a.get("/api/memory").json()["items"]) == 1
