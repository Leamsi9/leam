import sys

import pytest

from leam_api.codex import CodexClient, CodexError, CodexGenerationError


@pytest.mark.asyncio
async def test_failed_initialization_is_retried_cleanly(tmp_path):
    marker = tmp_path / "once"
    script = tmp_path / "server.py"
    script.write_text("""import sys,json,pathlib
marker=pathlib.Path(sys.argv[1])
for line in sys.stdin:
 p=json.loads(line)
 if p.get('method')=='initialize':
  if not marker.exists():
   marker.touch();print(json.dumps({'id':p['id'],'error':{'message':'initialization failed'}}),flush=True)
  else:print(json.dumps({'id':p['id'],'result':{}}),flush=True)
 elif 'id' in p:print(json.dumps({'id':p['id'],'result':{'ok':True}}),flush=True)
""")
    c = CodexClient(lambda *args: None, [sys.executable, str(script), str(marker)])
    try:
        with pytest.raises(CodexError):
            await c.request("thread/list", {})
        assert not c.ready
        assert await c.request("thread/list", {}) == {"ok": True}
        assert c.generation == 1
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_dead_reader_restarts_process(tmp_path):
    marker = tmp_path / "once"
    script = tmp_path / "server.py"
    script.write_text("""import sys,json,pathlib
marker=pathlib.Path(sys.argv[1])
for line in sys.stdin:
 p=json.loads(line)
 if p.get('method')=='initialize':print(json.dumps({'id':p['id'],'result':{}}),flush=True)
 elif 'id' in p:
  if not marker.exists():marker.touch();print('malformed',flush=True)
  else:print(json.dumps({'id':p['id'],'result':{'ok':True}}),flush=True)
""")
    c = CodexClient(lambda *args: None, [sys.executable, str(script), str(marker)])
    try:
        with pytest.raises(CodexError):
            await c.request("thread/list", {})
        with pytest.raises(CodexGenerationError):
            await c.request("turn/start", {}, expected_generation=1)
        assert await c.request("thread/list", {}) == {"ok": True}
        assert c.generation == 2
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_approval_validation_and_duplicate_response():
    c = CodexClient(lambda *args: None)
    key = c.register_request(
        {
            "id": 9,
            "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "t"},
        }
    )["id"]
    sent = []

    async def send(packet):
        sent.append(packet)

    c.send = send
    with pytest.raises(ValueError):
        await c.respond(key, {"decision": "acceptForSession"})
    assert key in c.requests and not sent
    await c.respond(key, {"decision": "accept"})
    assert key in c.requests
    with pytest.raises(ValueError):
        await c.respond(key, {"decision": "decline"})
    assert len(sent) == 1
