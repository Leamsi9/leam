from datetime import UTC, datetime

from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app
from leam_api.updates import Publication


class TicketCodex(FakeCodex):
    async def request(self, method, params, *, expected_generation=None):
        result = await super().request(
            method, params, expected_generation=expected_generation
        )
        if method == "model/list":
            return {
                "data": [
                    {
                        "model": "gpt-5.6-sol",
                        "supportedReasoningEfforts": [{"reasoningEffort": "medium"}],
                    }
                ]
            }
        if method in {"thread/start", "thread/resume"}:
            return {"thread": {"id": "ticket-thread", "cwd": params.get("cwd")}}
        return result


def make_ticket(tmp_path, monkeypatch, codex=None):
    monkeypatch.setenv("LEAM_CODING_WORKSPACE", str(tmp_path))
    codex = codex or TicketCodex()
    app = create_app(
        tmp_path, {"http://testserver"}, bootstrap="bootstrap-for-tests", codex=codex
    )
    item = app.state.updates.publish(
        Publication(
            feature="test-feature",
            title="Ticket title",
            summary="A bounded feature",
            deploymentId="build-1",
            deployedAt=datetime.now(UTC),
        )
    )
    return app, codex, item


def test_ticket_chat_is_lazy_persisted_and_each_turn_preserves_text_and_protocols(
    tmp_path, monkeypatch
):
    app, codex, item = make_ticket(tmp_path, monkeypatch)
    url = f"/api/updates/{item['id']}/chat"
    headers = {"origin": "http://testserver"}
    with TestClient(app) as client:
        assert client.get(url).status_code == 401
        login(client)
        assert client.get(url).json()["state"] == "notCreated"
        assert codex.calls == []
        assert client.post(url).status_code == 403
        connected = client.post(url, headers=headers)
        assert connected.status_code == 200, connected.text
        assert connected.json()["threadId"] == "ticket-thread"
        assert client.post(url, headers=headers).json() == connected.json()
        assert len([call for call in codex.calls if call[0] == "thread/start"]) == 1
        raw = "  What exactly changed?\nKeep my text intact.  "
        for n in range(2):
            result = client.post(
                "/api/codex/threads/ticket-thread/turns",
                json={"text": raw, "requestId": f"ticket-request-{n}"},
                headers=headers,
            )
            assert result.status_code == 200, result.text
        turns = [params for method, params in codex.calls if method == "turn/start"]
        assert len(turns) == 2
        for params in turns:
            assert params["input"][0]["text"] == raw
            assert "leam.agent-protocols" in params["additionalContext"]
            context = params["additionalContext"]["leam.update-ticket"]
            assert context["kind"] == "application"
            assert "Ticket title" in context["value"] and "build-1" in context["value"]
            assert "untrusted" in context["value"].lower()
        assert app.state.updates.list()["items"][0]["uat"]["state"] == "pending"
    restarted_codex = TicketCodex()
    restarted = create_app(tmp_path, {"http://testserver"}, codex=restarted_codex)
    with TestClient(restarted) as client:
        client.post(
            "/api/auth/login",
            json={"password": "long-password-for-tests"},
            headers=headers,
        )
        saved = client.get(url).json()
        assert saved["threadId"] == "ticket-thread" and not saved["connected"]
        assert restarted_codex.calls == []
        assert client.post(url, headers=headers).status_code == 200
        assert [method for method, _ in restarted_codex.calls] == ["thread/resume"]


def test_ticket_creation_uncertainty_is_not_replayed(tmp_path, monkeypatch):
    from leam_api.codex import CodexError

    class Uncertain(TicketCodex):
        async def request(self, method, params, *, expected_generation=None):
            result = await super().request(
                method, params, expected_generation=expected_generation
            )
            if method == "thread/start":
                raise CodexError("Connection lost")
            return result

    app, codex, item = make_ticket(tmp_path, monkeypatch, Uncertain())
    with TestClient(app) as client:
        login(client)
        url = f"/api/updates/{item['id']}/chat"
        first = client.post(url, headers={"origin": "http://testserver"})
        assert first.status_code >= 400
        second = client.post(url, headers={"origin": "http://testserver"})
        assert second.status_code == 409
        assert len([call for call in codex.calls if call[0] == "thread/start"]) == 1
        assert client.get(url).json()["state"] == "uncertain"


def test_ticket_turn_fails_closed_on_policy_damage_and_missing_workspace(
    tmp_path, monkeypatch
):
    import leam_api.app as app_module
    from leam_api.coding_policy import CodingPolicyError

    app, codex, item = make_ticket(tmp_path, monkeypatch)
    headers = {"origin": "http://testserver"}
    with TestClient(app) as client:
        login(client)
        url = f"/api/updates/{item['id']}/chat"
        monkeypatch.delenv("LEAM_CODING_WORKSPACE")
        assert client.post(url, headers=headers).status_code == 503
        assert codex.calls == []
        monkeypatch.setenv("LEAM_CODING_WORKSPACE", str(tmp_path))
        assert client.post(url, headers=headers).status_code == 200

        def damaged():
            raise CodingPolicyError("Policy package changed")

        monkeypatch.setattr(app_module, "coding_context", damaged)
        result = client.post(
            "/api/codex/threads/ticket-thread/turns",
            json={"text": "Help", "requestId": "damaged-policy-ticket"},
            headers=headers,
        )
        assert result.status_code == 503
        assert not [call for call in codex.calls if call[0] == "turn/start"]
        assert (
            client.get("/api/codex/submissions/damaged-policy-ticket").json()["state"]
            == "notSubmitted"
        )


def test_ticket_turn_references_deployed_baseline_instead_of_old_main(
    tmp_path, monkeypatch
):
    import json

    import leam_api.ticket_chat as ticket_module

    release = tmp_path / "release"
    release.mkdir()
    source = "a" * 40
    (release / "release.json").write_text(json.dumps({"sourceCommit": source}))
    monkeypatch.setattr(ticket_module, "PRODUCT_ROOT", release)
    app, codex, item = make_ticket(tmp_path / "data", monkeypatch)
    headers = {"origin": "http://testserver"}
    with TestClient(app) as client:
        login(client)
        assert (
            client.post(f"/api/updates/{item['id']}/chat", headers=headers).status_code
            == 200
        )
        response = client.post(
            "/api/codex/threads/ticket-thread/turns",
            json={
                "text": "Explain the current source",
                "requestId": "ticket-source-baseline",
            },
            headers=headers,
        )
        assert response.status_code == 200
        context = next(
            params for method, params in codex.calls if method == "turn/start"
        )["additionalContext"]["leam.update-ticket"]["value"]
        assert source in context and str(release) in context
        assert '"repositoryPath"' in context and '"currentDeploymentSource"' in context
        assert "from the verified current deployment source" in context
        assert "Never edit an immutable release or active build worktree" in context


def test_ticket_stream_cursor_replays_own_latest_turn_start_with_bounded_fallback(
    tmp_path, monkeypatch
):
    app, _, item = make_ticket(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        url = f"/api/updates/{item['id']}/chat"
        assert (
            client.post(url, headers={"origin": "http://testserver"}).status_code == 200
        )
        store = app.state.store
        start = store.event(
            "codex",
            {
                "method": "turn/started",
                "params": {"threadId": "ticket-thread", "turn": {"id": "own-turn"}},
            },
        )
        store.event(
            "codex",
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "ticket-thread",
                    "turnId": "own-turn",
                    "delta": "Hello",
                },
            },
        )
        store.event(
            "codex",
            {
                "method": "turn/started",
                "params": {
                    "threadId": "another-thread",
                    "turn": {"id": "foreign-turn"},
                },
            },
        )
        assert client.get(url).json()["eventCursor"] == start - 1
        with store.connect() as db:
            db.execute(
                "INSERT INTO events(id,topic,payload,created) VALUES (?,?,?,?)",
                (start + 5000, "unrelated", "{}", 0),
            )
        assert client.get(url).json()["eventCursor"] == start + 5000
