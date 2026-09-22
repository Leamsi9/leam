"""Actual Companion/tool callers prepare review without dispatching coding."""

import json
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from test_api import login
from test_ticket_chat import TicketCodex

from leam_api.app import create_app
from leam_api.ironclaw import IronClaw


def test_explicit_coding_request_is_grounded_as_prepare_now_approve_later(tmp_path):
    sent = []

    def upstream(request):
        if request.url.path.endswith("/session"):
            return httpx.Response(200, json={"session_channel_extension_id": "fixture"})
        if request.url.path.endswith("/messages"):
            sent.append(json.loads(request.content))
            return httpx.Response(
                200, json={"outcome": "submitted", "run_id": str(uuid4())}
            )
        return httpx.Response(200, json={})

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="fixture",
        transport=httpx.MockTransport(upstream),
    )
    bridge = TicketCodex()
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=bridge,
        runtime=runtime,
    )
    headers = {"origin": "http://testserver"}
    raw = "Please code a compact calendar view for Leam."
    with TestClient(app, client=("127.0.0.1", 1234)) as client:
        login(client)
        response = client.post(
            "/api/companion/threads/source/messages",
            headers=headers,
            json={"requestId": str(uuid4()), "text": raw},
        )
        assert response.status_code == 200, response.text
        assert sent[0]["content"] == raw
        envelope = sent[0]["model_context"]["reference_text"]
        assert (
            "When the user asks you to code, build, fix or change software" in envelope
        )
        assert "prepare a coding.handoff proposal immediately" in envelope
        assert "More > Approvals" in envelope
        assert "Send to main uses the selected Main session" in envelope
        assert "Selection changes require fresh review" in envelope
        assert "review below" not in envelope
        assert "not a permission sandbox" in envelope
        assert "Do not refuse on the grounds that you can only review" in envelope
        assert "automatic delegation is not implemented" not in envelope
        assert "automatic companion delegation not implemented" not in envelope
        assert not any(
            method in {"thread/start", "turn/start"} for method, _ in bridge.calls
        )

        # Drive the real model-facing tool ingress: proposal creation needs no
        # advance approval, but cannot execute the task or grant approval.
        token = (tmp_path / "tools-token").read_text().strip()
        auth = {"authorization": "Bearer " + token}
        schema = client.post(
            "/api/internal/tools",
            headers=auth,
            json={
                "tool": "leam_operation_schema",
                "arguments": {"operation": "coding.handoff"},
            },
        )
        assert schema.status_code == 200, schema.text
        description = schema.json()["inputSchema"]["description"]
        assert "More > Approvals" in description and "Send to main" in description
        assert (
            "no dispatch" in description
            and "Target changes require fresh review" in description
        )
        proposed = client.post(
            "/api/internal/tools",
            headers=auth,
            json={
                "tool": "leam_propose",
                "arguments": {
                    "requestId": str(uuid4()),
                    "threadId": "source",
                    "operation": "coding.handoff",
                    "input": {"title": "Compact calendar view", "instructions": raw},
                    "reason": "Requested by the user",
                },
            },
        )
        assert proposed.status_code == 200, proposed.text
        item = proposed.json()
        assert item["state"] == "pending"
        assert item["review"]["approval"]["mode"] == "manual"
        listing = client.get("/api/proposals?threadId=source&excludeMemory=true").json()
        assert listing["items"][0]["id"] == item["id"]
        assert listing["items"][0]["input"]["instructions"] == raw
        assert (
            client.post(
                "/api/proposals/" + item["id"] + "/approve", headers=headers, json={}
            ).status_code
            == 409
        )
        assert not any(
            method in {"thread/start", "turn/start"} for method, _ in bridge.calls
        )
