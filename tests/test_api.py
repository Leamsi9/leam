from fastapi.testclient import TestClient

from leam_api.app import create_app


class FakeCodex:
    def __init__(self):
        self.calls = []
        self.generation = 0
        self.dispatch_generations = []

    async def request(self, method, params, *, expected_generation=None):
        from leam_api.codex import CodexGenerationError

        if expected_generation is not None:
            self.dispatch_generations.append(expected_generation)
            if expected_generation != self.generation:
                raise CodexGenerationError("Codex restarted")
        self.calls.append((method, params))
        if method == "model/list":
            return {
                "data": [
                    {
                        "model": "gpt-5.6-sol",
                        "supportedReasoningEfforts": [{"reasoningEffort": "medium"}],
                    }
                ]
            }
        if method == "thread/read":
            return {
                "thread": {
                    "id": params["threadId"],
                    "name": "Build",
                    "status": {"type": "idle"},
                }
            }
        return {"turn": {"id": "turn-1", "status": "inProgress"}}

    async def close(self):
        pass


def login(client):
    result = client.post(
        "/api/auth/setup",
        json={
            "bootstrap": "bootstrap-for-tests",
            "password": "long-password-for-tests",
        },
        headers={"origin": "http://testserver"},
    )
    assert result.status_code == 200, result.text


def make(tmp_path):
    codex = FakeCodex()
    app = create_app(
        tmp_path,
        origins={"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=codex,
    )
    return TestClient(app), codex


def test_unauthenticated_cannot_read_or_execute(tmp_path):
    client, codex = make(tmp_path)
    with client:
        assert client.get("/api/codex/threads").status_code == 401
        assert client.post(
            "/api/codex/threads/t/turns",
            json={"text": "hello", "requestId": "request-1"},
        ).status_code in [401, 403]
        assert codex.calls == []


def test_cross_origin_action_rejected(tmp_path):
    client, codex = make(tmp_path)
    with client:
        login(client)
        response = client.post(
            "/api/codex/threads/t/turns",
            json={"text": "hello", "requestId": "request-1"},
            headers={"origin": "https://untrusted.example"},
        )
        assert response.status_code == 403
        assert codex.calls == []


def test_turn_requires_binding_and_duplicate_is_not_resent(tmp_path):
    client, codex = make(tmp_path)
    with client:
        login(client)
        headers = {"origin": "http://testserver"}
        data = {"text": "Please inspect the repository", "requestId": "request-1"}
        assert (
            client.post(
                "/api/codex/threads/t/turns", json=data, headers=headers
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/codex/threads/t/connect",
                json={"handoffConfirmed": True},
                headers=headers,
            ).status_code
            == 200
        )
        a = client.post("/api/codex/threads/t/turns", json=data, headers=headers)
        b = client.post("/api/codex/threads/t/turns", json=data, headers=headers)
        assert a.status_code == b.status_code == 200
        assert a.json() == b.json()
        turns = [call for call in codex.calls if call[0] == "turn/start"]
        assert len(turns) == 1
        assert turns[0][1]["input"] == [
            {"type": "text", "text": data["text"], "text_elements": []}
        ]
        changed = client.post(
            "/api/codex/threads/t/turns",
            json={**data, "text": "Different"},
            headers=headers,
        )
        assert changed.status_code == 409


def test_account_setup_cannot_overwrite_password(tmp_path):
    client, _ = make(tmp_path)
    with client:
        login(client)
        assert (
            client.post(
                "/api/auth/setup",
                json={
                    "bootstrap": "bootstrap-for-tests",
                    "password": "another-long-password",
                },
                headers={"origin": "http://testserver"},
            ).status_code
            == 409
        )


def test_commitment_revision_conflict_and_persistence(tmp_path):
    client, _ = make(tmp_path)
    with client:
        login(client)
        h = {"origin": "http://testserver"}
        a = client.post(
            "/api/commitments",
            json={"title": "Walk", "kind": "habit", "measure": "minutes", "target": 20},
            headers=h,
        )
        assert a.status_code == 200, a.text
        item = a.json()
        b = client.patch(
            "/api/commitments/" + item["id"],
            json={"revision": 1, "title": "Walk outside"},
            headers=h,
        )
        assert b.status_code == 200
        stale = client.patch(
            "/api/commitments/" + item["id"],
            json={"revision": 1, "title": "Stale"},
            headers=h,
        )
        assert stale.status_code == 409
    next_client, _ = make(tmp_path)
    with next_client:
        assert (
            next_client.post(
                "/api/auth/login",
                json={"password": "long-password-for-tests"},
                headers=h,
            ).status_code
            == 200
        )
        assert (
            next_client.get("/api/commitments").json()["items"][0]["title"]
            == "Walk outside"
        )


def test_chunked_body_limit(tmp_path):
    client, _ = make(tmp_path)
    with client:

        def chunks():
            yield b'{"bootstrap":"'
            yield b"x" * (2 * 1024 * 1024)
            yield b'","password":"long-password-for-tests"}'

        result = client.post(
            "/api/auth/setup",
            content=chunks(),
            headers={
                "origin": "http://testserver",
                "content-type": "application/json",
                "transfer-encoding": "chunked",
            },
        )
        assert result.status_code == 413


def test_restart_invalidates_binding_before_reservation(tmp_path):
    client, codex = make(tmp_path)
    codex.generation = 1
    with client:
        login(client)
        h = {"origin": "http://testserver"}
        client.post(
            "/api/codex/threads/t/connect", json={"handoffConfirmed": True}, headers=h
        )
        codex.generation = 2
        result = client.post(
            "/api/codex/threads/t/turns",
            json={"text": "hello", "requestId": "restart-request"},
            headers=h,
        )
        assert result.status_code == 409
        assert not any(m == "turn/start" for m, p in codex.calls)
        assert client.app.state.store.reserve("restart-request", "test") is None


def test_stale_approval_cannot_authorize_reused_upstream_id(tmp_path):
    from leam_api.codex import CodexClient

    c = CodexClient(lambda *args: None)
    sent = []

    async def send(packet):
        sent.append(packet)

    c.send = send
    old = c.register_request(
        {
            "id": 9,
            "method": "item/commandExecution/requestApproval",
            "params": {"command": "old"},
        }
    )["id"]
    c.requests.clear()
    c.upstream_requests.clear()
    new = c.register_request(
        {
            "id": 9,
            "method": "item/commandExecution/requestApproval",
            "params": {"command": "different"},
        }
    )["id"]
    app = create_app(
        tmp_path,
        origins={"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=c,
    )
    with TestClient(app) as client:
        login(client)
        h = {"origin": "http://testserver"}
        assert (
            client.post(
                "/api/codex/requests/" + old, json={"decision": "accept"}, headers=h
            ).status_code
            == 502
        )
        assert not sent
        assert new in c.requests
        assert (
            client.post(
                "/api/codex/requests/" + new, json={"decision": "accept"}, headers=h
            ).status_code
            == 200
        )
        assert sent == [{"id": 9, "result": {"decision": "accept"}}]


def test_new_thread_binds_and_pending_submission_reconciles_without_resend(tmp_path):
    client, codex = make(tmp_path)
    original = codex.request

    async def request(method, params, **kwargs):
        if method == "thread/start":
            codex.calls.append((method, params))
            return {"thread": {"id": "new-thread"}}
        if method == "thread/turns/list":
            return {
                "data": [
                    {
                        "id": "recovered-turn",
                        "items": [
                            {"type": "userMessage", "clientId": "uncertain-request"}
                        ],
                    }
                ]
            }
        return await original(method, params, **kwargs)

    codex.request = request
    with client:
        login(client)
        h = {"origin": "http://testserver"}
        created = client.post(
            "/api/codex/threads", json={"cwd": str(tmp_path)}, headers=h
        )
        assert created.status_code == 200, created.text
        result = client.post(
            "/api/codex/threads/new-thread/turns",
            json={"text": "hello", "requestId": "first-request"},
            headers=h,
        )
        assert result.status_code == 200
        import hashlib
        import json

        client.app.state.store.reserve(
            "uncertain-request",
            hashlib.sha256(
                json.dumps(["new-thread", "uncertain"]).encode()
            ).hexdigest(),
        )
        recovered = client.post(
            "/api/codex/threads/new-thread/submissions/uncertain-request/reconcile",
            json={"text": "uncertain"},
            headers=h,
        )
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["state"] == "complete"
        assert recovered.json()["result"]["turn"]["id"] == "recovered-turn"
        assert len([c for c in codex.calls if c[0] == "turn/start"]) == 1


def test_new_unmaterialized_thread_has_empty_history_without_masking_other_errors(
    tmp_path,
):
    from leam_api.codex import CodexError

    client, codex = make(tmp_path)

    async def request(method, params):
        raise CodexError(
            f"thread {params['threadId']} is not materialized yet; thread/turns/list is unavailable before first user message"
        )

    codex.request = request
    with client:
        login(client)
        r = client.get("/api/codex/threads/new/turns")
        assert r.status_code == 200
        assert r.json()["data"] == []

        async def broken(method, params):
            raise CodexError("Connection failed")

        codex.request = broken
        assert client.get("/api/codex/threads/new/turns").status_code == 502


def test_exceptional_lifespan_closes_all_resources_even_when_close_fails(tmp_path):
    import asyncio

    import pytest

    calls = []

    class ClosingCodex(FakeCodex):
        async def close(self):
            calls.append("codex")
            raise ValueError("close failed")

    class ClosingRuntime:
        async def close(self):
            calls.append("runtime")

    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=ClosingCodex(),
        runtime=ClosingRuntime(),
    )

    async def exercise():
        with pytest.raises(RuntimeError, match="lifespan failure"):
            async with app.router.lifespan_context(app):
                raise RuntimeError("lifespan failure")

    asyncio.run(exercise())
    assert calls == ["codex", "runtime"]


def test_every_coding_turn_receives_pinned_protocol_context(tmp_path):
    client, codex = make(tmp_path)
    with client:
        login(client)
        headers = {"origin": "http://testserver"}
        assert (
            client.post(
                "/api/codex/threads/t/connect",
                json={"handoffConfirmed": True},
                headers=headers,
            ).status_code
            == 200
        )
        for index in range(2):
            response = client.post(
                "/api/codex/threads/t/turns",
                json={
                    "text": "Inspect this project",
                    "requestId": f"policy-request-{index}",
                },
                headers=headers,
            )
            assert response.status_code == 200
        turns = [params for method, params in codex.calls if method == "turn/start"]
        assert len(turns) == 2
        for turn in turns:
            policy = turn["additionalContext"]["leam.agent-protocols"]
            assert policy["kind"] == "application"
            assert "substantive-work-protocol.md" in policy["value"]
            assert "minor-work-protocol.md" in policy["value"]
            assert "0.0.19" in policy["value"]
            assert "AGENTS.md" in policy["value"]
            assert "candidate-delivery.md" in policy["value"]
            assert "Deploy" in policy["value"]
            assert "before running the full QA" in policy["value"]
            assert "deployment.json" in policy["value"]
            assert turn["input"][0]["text"] == "Inspect this project"


def test_broken_protocol_package_blocks_dispatch_without_reserving_request(
    tmp_path, monkeypatch
):
    import shutil

    from leam_api import coding_policy

    root = tmp_path / "policy"
    root.mkdir()
    shutil.copytree(
        coding_policy.PACKAGE_ROOT / "agent-protocols", root / "agent-protocols"
    )
    shutil.copy(coding_policy.PACKAGE_ROOT / "agent-protocols.lock.json", root)
    monkeypatch.setattr(coding_policy, "PACKAGE_ROOT", root)
    client, codex = make(tmp_path / "state")
    with client:
        login(client)
        headers = {"origin": "http://testserver"}
        assert (
            client.post(
                "/api/codex/threads/t/connect",
                json={"handoffConfirmed": True},
                headers=headers,
            ).status_code
            == 200
        )
        path = root / "agent-protocols/minor-work-protocol.md"
        original = path.read_bytes()
        for changed in [b"unreviewed change", None]:
            if changed is None:
                path.unlink()
            else:
                path.write_bytes(changed)
            result = client.post(
                "/api/codex/threads/t/turns",
                json={"text": "Inspect", "requestId": "policy-repair-request"},
                headers=headers,
            )
            assert result.status_code == 503
            assert "agent-protocols" in result.json()["detail"]
            assert not any(method == "turn/start" for method, _ in codex.calls)
            assert (
                client.get("/api/codex/submissions/policy-repair-request").json()[
                    "state"
                ]
                == "notSubmitted"
            )
        path.write_bytes(original)
        result = client.post(
            "/api/codex/threads/t/turns",
            json={"text": "Inspect", "requestId": "policy-repair-request"},
            headers=headers,
        )
        assert result.status_code == 200
        assert len([1 for method, _ in codex.calls if method == "turn/start"]) == 1


def test_restart_during_policy_load_does_not_reserve_or_send(tmp_path, monkeypatch):
    import leam_api.app as app_module

    client, codex = make(tmp_path)
    codex.generation = 1
    original = app_module.coding_context

    def restart_during_load():
        result = original()
        codex.generation += 1
        return result

    monkeypatch.setattr(app_module, "coding_context", restart_during_load)
    with client:
        login(client)
        h = {"origin": "http://testserver"}
        client.post(
            "/api/codex/threads/t/connect", json={"handoffConfirmed": True}, headers=h
        )
        response = client.post(
            "/api/codex/threads/t/turns",
            json={"text": "Inspect", "requestId": "policy-restart-race"},
            headers=h,
        )
        assert response.status_code == 409
        assert not any(method == "turn/start" for method, _ in codex.calls)
        assert (
            client.get("/api/codex/submissions/policy-restart-race").json()["state"]
            == "notSubmitted"
        )


def test_completed_receipt_survives_protocol_damage(tmp_path, monkeypatch):
    import leam_api.app as app_module
    from leam_api.coding_policy import CodingPolicyError

    client, codex = make(tmp_path)
    with client:
        login(client)
        h = {"origin": "http://testserver"}
        client.post(
            "/api/codex/threads/t/connect", json={"handoffConfirmed": True}, headers=h
        )
        body = {"text": "Inspect", "requestId": "completed-policy-receipt"}
        first = client.post("/api/codex/threads/t/turns", json=body, headers=h)
        assert first.status_code == 200

        def broken():
            raise CodingPolicyError("missing protocols")

        monkeypatch.setattr(app_module, "coding_context", broken)
        replay = client.post("/api/codex/threads/t/turns", json=body, headers=h)
        assert replay.status_code == 200
        assert replay.json() == first.json()
        assert len([1 for method, _ in codex.calls if method == "turn/start"]) == 1


def test_malformed_protocol_lock_returns_recoverable_error(tmp_path, monkeypatch):
    import json

    from leam_api import coding_policy

    root = tmp_path / "policy"
    root.mkdir()
    monkeypatch.setattr(coding_policy, "PACKAGE_ROOT", root)
    client, codex = make(tmp_path / "state")
    with client:
        login(client)
        h = {"origin": "http://testserver"}
        client.post(
            "/api/codex/threads/t/connect", json={"handoffConfirmed": True}, headers=h
        )
        for malformed in [
            [],
            {
                "files": [
                    "VERSION",
                    "substantive-work-protocol.md",
                    "minor-work-protocol.md",
                ]
            },
            {"files": {}, "version": []},
        ]:
            (root / "agent-protocols.lock.json").write_text(json.dumps(malformed))
            response = client.post(
                "/api/codex/threads/t/turns",
                json={"text": "Inspect", "requestId": "malformed-policy-request"},
                headers=h,
            )
            assert response.status_code == 503
            assert not any(method == "turn/start" for method, _ in codex.calls)
            assert (
                client.get("/api/codex/submissions/malformed-policy-request").json()[
                    "state"
                ]
                == "notSubmitted"
            )


def test_transport_restart_before_write_releases_only_unsent_reservation(tmp_path):
    from leam_api.codex import CodexError

    client, codex = make(tmp_path)
    original = codex.request
    fail_after_write = False

    async def request(method, params, **kwargs):
        if method == "turn/start":
            if fail_after_write:
                codex.calls.append((method, params))
                raise CodexError("connection lost after dispatch")
            codex.generation += 1
        return await original(method, params, **kwargs)

    codex.request = request
    with client:
        login(client)
        h = {"origin": "http://testserver"}
        client.post(
            "/api/codex/threads/t/connect", json={"handoffConfirmed": True}, headers=h
        )
        body = {"text": "Inspect", "requestId": "transport-generation-request"}
        response = client.post("/api/codex/threads/t/turns", json=body, headers=h)
        assert response.status_code == 409
        assert codex.dispatch_generations == [0]
        assert not any(method == "turn/start" for method, _ in codex.calls)
        assert (
            client.get("/api/codex/submissions/transport-generation-request").json()[
                "state"
            ]
            == "notSubmitted"
        )
        client.post(
            "/api/codex/threads/t/connect", json={"handoffConfirmed": True}, headers=h
        )
        fail_after_write = True
        response = client.post("/api/codex/threads/t/turns", json=body, headers=h)
        assert response.status_code == 502
        assert (
            client.get("/api/codex/submissions/transport-generation-request").json()[
                "state"
            ]
            == "pending"
        )


def test_local_preview_and_https_keep_origin_scoped_secure_sessions(tmp_path):
    origins = {"http://testserver", "https://private.example"}
    app = create_app(
        tmp_path, origins=origins, bootstrap="bootstrap-for-tests", codex=FakeCodex()
    )
    with TestClient(app) as client:
        login(client)
        for origin in origins:
            response = client.post(
                "/api/auth/login",
                headers={"origin": origin},
                json={"password": "long-password-for-tests"},
            )
            assert response.status_code == 200
            cookie = response.headers["set-cookie"]
            assert ("Secure" in cookie) == origin.startswith("https://")
            assert "HttpOnly" in cookie and "SameSite=strict" in cookie
        for headers in ({}, {"origin": "https://untrusted.example"}):
            response = client.post(
                "/api/auth/login",
                headers=headers,
                json={"password": "long-password-for-tests"},
            )
            assert response.status_code == 403
            assert "set-cookie" not in response.headers


def test_missing_candidate_overlay_blocks_new_turn_without_reservation(
    tmp_path, monkeypatch
):
    import shutil

    from leam_api import coding_policy

    root = tmp_path / "policy"
    root.mkdir()
    shutil.copytree(
        coding_policy.PACKAGE_ROOT / "agent-protocols", root / "agent-protocols"
    )
    shutil.copy(coding_policy.PACKAGE_ROOT / "agent-protocols.lock.json", root)
    (root / "agent-protocols/local/candidate-delivery.md").unlink()
    monkeypatch.setattr(coding_policy, "PACKAGE_ROOT", root)
    client, codex = make(tmp_path / "state")
    with client:
        login(client)
        headers = {"origin": "http://testserver"}
        assert (
            client.post(
                "/api/codex/threads/t/connect",
                json={"handoffConfirmed": True},
                headers=headers,
            ).status_code
            == 200
        )
        response = client.post(
            "/api/codex/threads/t/turns",
            json={"text": "Inspect", "requestId": "overlay-missing-request"},
            headers=headers,
        )
        assert response.status_code == 503
        assert not any(method == "turn/start" for method, _ in codex.calls)
        assert (
            client.get("/api/codex/submissions/overlay-missing-request").json()["state"]
            == "notSubmitted"
        )
