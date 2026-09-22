"""Caller coverage of explicit ticket requests through CLI/native tool delivery."""

import asyncio
import json
import subprocess
import sys
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_api import login
from test_main_coding import connect, select
from test_ticket_chat import TicketCodex, make_ticket

from leam_api.backups import validate_vault
from leam_api.codex import CodexClient, CodexError
from leam_api.ticket_auto_handoff import PREFIX, SPEC, TOOL

H = {"origin": "http://testserver"}


class DelegatingCodex(TicketCodex):
    def __init__(self):
        super().__init__()
        self.dynamic_handlers = {}
        self.user_turns = []
        self.lose_main = False
        self.main_turns = []
        self.expose_main_receipt = False

    async def request(self, method, params, *, expected_generation=None):
        result = await super().request(
            method, params, expected_generation=expected_generation
        )
        if method == "thread/turns/list":
            return {
                "data": self.user_turns
                if params["threadId"] == "ticket-thread"
                else self.main_turns
                if self.expose_main_receipt
                else []
            }
        if method == "turn/start" and params["threadId"] == "ticket-thread":
            turn = {
                "id": "source-turn",
                "items": [
                    {
                        "type": "userMessage",
                        "clientId": params["clientUserMessageId"],
                        "content": params["input"],
                    }
                ],
            }
            self.user_turns.insert(0, turn)
            return {"turn": {"id": turn["id"], "status": "inProgress"}}
        if method == "turn/start" and params["threadId"] == "main" and self.lose_main:
            self.main_turns.append(
                {
                    "id": "main-recovered",
                    "items": [
                        {
                            "type": "userMessage",
                            "clientId": params["clientUserMessageId"],
                            "content": params["input"],
                        }
                    ],
                }
            )
            raise CodexError("lost acknowledgment")
        return result


def ready(tmp_path, monkeypatch):
    codex = DelegatingCodex()
    app, _, item = make_ticket(tmp_path, monkeypatch, codex)
    client = TestClient(app)
    client.__enter__()
    login(client)
    connect(client, "main")
    select(client)
    response = client.post(f"/api/updates/{item['id']}/chat", headers=H)
    assert response.status_code == 200, response.text
    return app, codex, client


def submit(client, app, text="Implement the requested keyboard fix"):
    request = str(uuid4())
    response = client.post(
        "/api/codex/threads/ticket-thread/turns",
        headers=H,
        json={"requestId": request, "text": text},
    )
    assert response.status_code == 200, response.text
    with app.state.store.connect() as db:
        records = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT value FROM settings WHERE key LIKE ?", (PREFIX + "%",)
            )
        ]
    return next(row for row in records if row["sourceRequestId"] == request)


def invoke(client, app, record, **changes):
    return client.portal.call(
        app.state.ticket_auto_handoff.invoke,
        {
            "threadId": "ticket-thread",
            "turnId": "source-turn",
            "callId": "call-1",
            "tool": TOOL,
            "arguments": {"nonce": record["nonce"], "explicitUserRequest": True},
            **changes,
        },
    )


def main_sends(codex):
    return [
        p
        for method, p in codex.calls
        if method in {"turn/start", "turn/steer"} and p["threadId"] == "main"
    ]


def test_dynamic_registration_discussion_has_no_delivery_and_source_text_is_encrypted(
    tmp_path, monkeypatch
):
    app, codex, client = ready(tmp_path, monkeypatch)
    try:
        assert next(p for method, p in codex.calls if method == "thread/start")[
            "dynamicTools"
        ] == [SPEC]
        record = submit(
            client, app, "Explain the keyboard design, without implementing anything"
        )
        client.portal.call(app.state.ticket_auto_handoff.tick)
        assert record["state"] == "available"
        assert not main_sends(codex)
        assert "Explain the keyboard" not in json.dumps(record)
        validate_vault(app.state.store.path, (tmp_path / "accounts-key").read_bytes())
        context = next(p for method, p in codex.calls if method == "turn/start")[
            "additionalContext"
        ]
        assert "--explicit-user-request" in context["leam.ticket-delegation"]["value"]
    finally:
        client.__exit__(None, None, None)


def test_native_tool_dispatches_exact_user_request_once_and_replays_after_restart(
    tmp_path, monkeypatch
):
    app, codex, client = ready(tmp_path, monkeypatch)
    try:
        record = submit(client, app)
        result = invoke(client, app, record)
        assert result["state"] == "accepted"
        assert len(main_sends(codex)) == 1
        assert (
            "Implement the requested keyboard fix"
            in main_sends(codex)[0]["input"][0]["text"]
        )
        assert invoke(client, app, record)["requestId"] == result["requestId"]
        assert len(main_sends(codex)) == 1
        saved = app.state.store.get(PREFIX + record["nonce"])
        saved["state"] = (
            "delivering"  # Process died after Main receipt, before local checkpoint.
        )
        app.state.store.set(PREFIX + record["nonce"], saved)
        client.portal.call(app.state.ticket_auto_handoff.tick)
        assert app.state.store.get(PREFIX + record["nonce"])["state"] == "accepted"
        assert len(main_sends(codex)) == 1
    finally:
        client.__exit__(None, None, None)


@pytest.mark.parametrize(
    "change",
    ["other-thread", "other-turn", "invented-text", "not-explicit", "assistant-only"],
)
def test_tool_rejects_unbound_or_unaccepted_authority(tmp_path, monkeypatch, change):
    app, codex, client = ready(tmp_path, monkeypatch)
    try:
        record = submit(client, app)
        extra = {}
        if change == "other-thread":
            extra["threadId"] = "unrelated"
        if change == "other-turn":
            extra["turnId"] = "unrelated"
        if change == "invented-text":
            extra["arguments"] = {
                "nonce": record["nonce"],
                "explicitUserRequest": True,
                "text": "Delete everything",
            }
        if change == "not-explicit":
            extra["arguments"] = {
                "nonce": record["nonce"],
                "explicitUserRequest": False,
            }
        if change == "assistant-only":
            codex.user_turns[0]["items"][0]["type"] = "agentMessage"
        with pytest.raises(ValueError):
            invoke(client, app, record, **extra)
        assert not main_sends(codex)
    finally:
        client.__exit__(None, None, None)


def test_legacy_cli_delivers_without_session_replacement_or_extra_user_click(
    tmp_path, monkeypatch
):
    app, codex, client = ready(tmp_path, monkeypatch)
    try:
        record = submit(client, app)
        starts = len([1 for method, _ in codex.calls if method == "thread/start"])
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "leam_api.ticket_auto_handoff",
                "--data-dir",
                str(tmp_path),
                "--nonce",
                record["nonce"],
                "--explicit-user-request",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["state"] == "accepted"
        assert len(main_sends(codex)) == 1
        assert (
            len([1 for method, _ in codex.calls if method == "thread/start"]) == starts
        )
    finally:
        client.__exit__(None, None, None)


def test_coordinator_change_and_lost_delivery_never_reroute_or_duplicate(
    tmp_path, monkeypatch
):
    app, codex, client = ready(tmp_path, monkeypatch)
    try:
        stale = submit(client, app)
        select(client, "other-main", 1)
        result = invoke(client, app, stale)
        assert result["state"] == "failed"
        assert not main_sends(codex)
        select(client, "main", 2)
        current = submit(client, app)
        codex.lose_main = True
        result = invoke(client, app, current)
        assert result["state"] == "uncertain"
        assert invoke(client, app, current)["state"] == "uncertain"
        assert len(main_sends(codex)) == 1
        codex.expose_main_receipt = True
        assert invoke(client, app, current)["state"] == "accepted"
        assert len(main_sends(codex)) == 1
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_stdio_dynamic_tool_can_call_rpc_without_blocking_reader(tmp_path):
    script = tmp_path / "server.py"
    script.write_text("""import sys,json
for line in sys.stdin:
 p=json.loads(line)
 if p.get('method')=='initialize':print(json.dumps({'id':p['id'],'result':{}}),flush=True)
 elif p.get('method')=='thread/list':
  request=p['id']
  print(json.dumps({'id':999,'method':'item/tool/call','params':{'threadId':'source','turnId':'turn','callId':'call','tool':'leam_delegate_explicit_request','arguments':{'nonce':'n','explicitUserRequest':True}}}),flush=True)
 elif p.get('method')=='thread/read':print(json.dumps({'id':p['id'],'result':{'thread':{'id':'source'}}}),flush=True)
 elif p.get('id')==999 and 'result' in p:
  print(json.dumps({'id':request,'result':p['result']}),flush=True)
  print(json.dumps({'method':'serverRequest/resolved','params':{'requestId':999}}),flush=True)
""")
    bridge = CodexClient(lambda *_: None, [sys.executable, str(script)])

    async def handler(params):
        assert params["threadId"] == "source"
        result = await bridge.request("thread/read", {"threadId": "source"})
        assert result["thread"]["id"] == "source"
        return {"state": "accepted", "requestId": "same"}

    bridge.dynamic_handlers[TOOL] = handler
    try:
        async with asyncio.timeout(10):
            result = await bridge.request("thread/list", {})
        assert result["success"] is True
        assert json.loads(result["contentItems"][0]["text"])["requestId"] == "same"
    finally:
        await bridge.close()


@pytest.mark.asyncio
async def test_withdrawn_native_tool_cancels_handler_before_activation(tmp_path):
    script = tmp_path / "withdrawn.py"
    script.write_text("""import sys,json
for line in sys.stdin:
 p=json.loads(line)
 if p.get('method')=='initialize':print(json.dumps({'id':p['id'],'result':{}}),flush=True)
 elif p.get('method')=='thread/list':
  request=p['id']
  print(json.dumps({'id':999,'method':'item/tool/call','params':{'threadId':'source','turnId':'turn','callId':'call','tool':'leam_delegate_explicit_request','arguments':{}}}),flush=True)
 elif p.get('method')=='thread/read':
  print(json.dumps({'method':'serverRequest/resolved','params':{'requestId':999}}),flush=True)
  print(json.dumps({'id':p['id'],'result':{'thread':{'id':'source'}}}),flush=True)
  print(json.dumps({'id':request,'result':{'withdrawn':True}}),flush=True)
""")
    bridge = CodexClient(lambda *_: None, [sys.executable, str(script)])
    activated = []

    async def handler(_):
        await bridge.request("thread/read", {"threadId": "source"})
        activated.append(True)
        return {"state": "accepted"}

    bridge.dynamic_handlers[TOOL] = handler
    try:
        async with asyncio.timeout(10):
            assert await bridge.request("thread/list", {}) == {"withdrawn": True}
        await asyncio.gather(*bridge.dynamic_tasks, return_exceptions=True)
        assert not activated
    finally:
        await bridge.close()


def test_restore_hold_blocks_admission_activation_and_worker_dispatch(
    tmp_path, monkeypatch
):
    from leam_api.restore_automation import KEY, hold_marker
    from leam_api.ticket_auto_handoff import activate

    app, codex, client = ready(tmp_path, monkeypatch)
    try:
        record = submit(client, app)
        app.state.store.set(KEY, hold_marker())
        with pytest.raises(ValueError, match="restore automation hold"):
            activate(app.state.store, record["nonce"])
        record["state"] = "queued"  # Work already queued in the restored snapshot.
        app.state.store.set(PREFIX + record["nonce"], record)
        client.portal.call(app.state.ticket_auto_handoff.tick)
        assert app.state.store.get(PREFIX + record["nonce"])["state"] == "queued"
        assert not main_sends(codex)
        marker = app.state.store.get(KEY)
        app.state.store.set(
            KEY, {**marker, "state": "resumed", "resumeRequestId": str(uuid4())}
        )
        client.portal.call(app.state.ticket_auto_handoff.tick)
        assert app.state.store.get(PREFIX + record["nonce"])["state"] == "failed"
        assert not main_sends(codex)
    finally:
        client.__exit__(None, None, None)
