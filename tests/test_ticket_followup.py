from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_api import login
from test_attachments import ORIGIN, upload
from test_coding_attachments import pdf, png
from test_ticket_chat import TicketCodex, make_ticket

from leam_api.codex import CodexError


class SteeringCodex(TicketCodex):
    def __init__(self, failure=None):
        super().__init__()
        self.failure = failure
        self.history = []

    async def request(self, method, params, *, expected_generation=None):
        result = await super().request(
            method, params, expected_generation=expected_generation
        )
        if method == "turn/steer":
            if self.failure:
                raise self.failure
            return {"turnId": params["expectedTurnId"]}
        if method == "thread/turns/list":
            return {"data": self.history, "nextCursor": None}
        return result


def connected(tmp_path, monkeypatch, codex):
    app, _, ticket = make_ticket(tmp_path, monkeypatch, codex)
    client = TestClient(app)
    return app, client, f"/api/updates/{ticket['id']}/chat"


def test_active_ticket_followup_preserves_context_attachments_and_exact_receipt(
    tmp_path, monkeypatch
):
    codex = SteeringCodex()
    app, client, chat = connected(tmp_path, monkeypatch, codex)
    with client:
        login(client)
        assert client.post(chat, headers=ORIGIN).status_code == 200
        image = upload(client, png(), "image/png", "fixture.png").json()
        document = upload(client, pdf(), "application/pdf", "fixture.pdf").json()
        body = {
            "text": "  Please use this correction — now.\n",
            "requestId": "ticket-followup-1",
            "expectedTurnId": "active-turn",
            "attachmentIds": [image["id"], document["id"]],
        }
        response = client.post(
            "/api/codex/threads/ticket-thread/turns", json=body, headers=ORIGIN
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "turn": {"id": "active-turn", "status": "inProgress"},
            "operation": "steer",
        }
        assert (
            client.post(
                "/api/codex/threads/ticket-thread/turns", json=body, headers=ORIGIN
            ).json()
            == response.json()
        )
        sent = [params for method, params in codex.calls if method == "turn/steer"]
        assert len(sent) == 1
        assert not [method for method, _ in codex.calls if method == "turn/start"]
        assert sent[0]["expectedTurnId"] == "active-turn"
        assert sent[0]["clientUserMessageId"] == body["requestId"]
        assert sent[0]["input"][0]["text"] == body["text"]
        assert Path(sent[0]["input"][1]["path"]).read_bytes() == png()
        assert set(sent[0]) == {
            "threadId",
            "expectedTurnId",
            "clientUserMessageId",
            "input",
            "additionalContext",
        }
        context = sent[0]["additionalContext"]
        assert context["leam.agent-protocols"]["kind"] == "application"
        assert "untrusted" in context["leam.update-ticket"]["value"].lower()
        assert context["leam.attachments"]["kind"] == "untrusted"
        assert codex.dispatch_generations == [codex.generation]
        assert (
            client.post(
                "/api/codex/threads/ticket-thread/turns",
                json={**body, "expectedTurnId": "different-turn"},
                headers=ORIGIN,
            ).status_code
            == 409
        )
        assert app.state.updates.list()["items"][0]["uat"]["state"] == "pending"


@pytest.mark.parametrize("rejected", [False, True])
def test_steer_failure_never_falls_back_or_resends_and_reconciles_exact_id(
    tmp_path, monkeypatch, rejected
):
    error = CodexError("Active turn changed" if rejected else "Connection lost")
    error.rpc_code = -32600 if rejected else None
    codex = SteeringCodex(error)
    _, client, chat = connected(tmp_path, monkeypatch, codex)
    with client:
        login(client)
        assert client.post(chat, headers=ORIGIN).status_code == 200
        body = {
            "text": "Correction",
            "requestId": "ticket-followup-2",
            "expectedTurnId": "active-turn",
        }
        url = "/api/codex/threads/ticket-thread/turns"
        response = client.post(url, json=body, headers=ORIGIN)
        assert response.status_code == (409 if rejected else 502)
        receipt = client.get("/api/codex/submissions/" + body["requestId"]).json()
        assert receipt["state"] == ("notSubmitted" if rejected else "pending")
        if rejected:
            assert response.headers["X-Leam-Action-Reserved"] == "no"
        else:
            assert client.post(url, json=body, headers=ORIGIN).status_code == 409
            reconcile = (
                "/api/codex/threads/ticket-thread/submissions/"
                + body["requestId"]
                + "/reconcile"
            )
            content = {k: v for k, v in body.items() if k != "requestId"}
            assert (
                client.post(reconcile, json=content, headers=ORIGIN).json()["state"]
                == "pending"
            )
            codex.history = [
                {
                    "id": "active-turn",
                    "status": "inProgress",
                    "items": [
                        {
                            "type": "userMessage",
                            "clientId": body["requestId"],
                            "content": [{"type": "text", "text": body["text"]}],
                        }
                    ],
                }
            ]
            codex.history[0]["id"] = "other-turn"
            assert (
                client.post(reconcile, json=content, headers=ORIGIN).json()["state"]
                == "pending"
            )
            codex.history[0]["id"] = "active-turn"
            accepted = client.post(reconcile, json=content, headers=ORIGIN).json()
            assert accepted["state"] == "complete"
            assert accepted["result"]["operation"] == "steer"
        assert len([m for m, _ in codex.calls if m == "turn/steer"]) == 1
        assert not [m for m, _ in codex.calls if m == "turn/start"]


@pytest.mark.asyncio
async def test_native_transport_retains_admission_error_code(tmp_path):
    import sys

    from leam_api.codex import CodexClient

    server = tmp_path / "server.py"
    server.write_text("""import json,sys
for line in sys.stdin:
 p=json.loads(line)
 if 'id' not in p: continue
 if p['method']=='initialize': result={'result':{}}
 else: result={'error':{'code':-32600,'message':'No active turn'}}
 print(json.dumps({'id':p['id'],**result}),flush=True)
""")
    client = CodexClient(lambda *_: None, [sys.executable, str(server)])
    try:
        with pytest.raises(CodexError) as captured:
            await client.request(
                "turn/steer",
                {"threadId": "synthetic", "expectedTurnId": "turn", "input": []},
            )
        assert captured.value.rpc_code == -32600
    finally:
        await client.close()
