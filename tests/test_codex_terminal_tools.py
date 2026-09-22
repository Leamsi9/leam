"""Real stdio reader callers: native terminal receipt, never a model text claim."""

import asyncio
import sys

import pytest

from leam_api.codex import CodexClient

SCRIPT = """import sys,json
for line in sys.stdin:
 p=json.loads(line);method=p.get('method');args=p.get('params',{})
 if method=='initialize': print(json.dumps({'id':p['id'],'result':{}}),flush=True)
 elif method=='fixture/open':
  opening=p['id'];wait=args.get('waitResponse',True)
  print(json.dumps({'id':999,'method':'item/tool/call','params':{'threadId':'source','turnId':'turn','callId':'call','tool':'internal_test','arguments':{}}}),flush=True)
  if args.get('external'): print(json.dumps({'id':1000,'method':'item/commandExecution/requestApproval','params':{'threadId':'source','turnId':'turn','itemId':'external'}}),flush=True)
  if not wait: print(json.dumps({'id':opening,'result':{}}),flush=True)
 elif p.get('id')==999 and 'result' in p:
  if wait: print(json.dumps({'id':opening,'result':{}}),flush=True)
 elif method=='fixture/terminal':
  print(json.dumps({'method':'turn/completed','params':args}),flush=True)
  print(json.dumps({'id':p['id'],'result':{}}),flush=True)
 elif method=='thread/loaded/list':print(json.dumps({'id':p['id'],'result':{'data':['source'],'nextCursor':None}}),flush=True)
 elif method=='thread/read':print(json.dumps({'id':p['id'],'result':{'thread':{'id':'source','status':{'type':'idle'}}}}),flush=True)
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed", "interrupted"])
async def test_terminal_native_turn_retires_responded_internal_request_and_unblocks_drain(
    tmp_path, status
):
    script = tmp_path / "native.py"
    script.write_text(SCRIPT)
    observed = []
    bridge = CodexClient(
        lambda topic, packet: observed.append(packet), [sys.executable, str(script)]
    )

    async def handler(_):
        return {"state": "accepted"}

    bridge.dynamic_handlers["internal_test"] = handler
    try:
        await bridge.request("fixture/open", {})
        assert len(bridge.requests) == len(bridge.responding) == 1
        assert not (await bridge.drain_status())["idle"]
        await bridge.request(
            "fixture/terminal",
            {"threadId": "source", "turn": {"id": "turn", "status": status}},
        )
        await asyncio.gather(*tuple(bridge.dynamic_tasks))
        assert (
            not bridge.requests
            and not bridge.responding
            and not bridge.upstream_requests
        )
        assert (await bridge.drain_status()) == {"idle": True, "loadedThreads": 1}
        assert not any(
            packet.get("method") == "serverRequest/resolved" for packet in observed
        )
    finally:
        await bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "thread,turn,status",
    [
        ("other", "turn", "completed"),
        ("source", "other", "completed"),
        ("source", "turn", "inProgress"),
        ("source", "turn", "unknown"),
    ],
)
async def test_wrong_or_nonterminal_native_turn_preserves_request(
    tmp_path, thread, turn, status
):
    script = tmp_path / "native.py"
    script.write_text(SCRIPT)
    bridge = CodexClient(lambda *_: None, [sys.executable, str(script)])

    async def handler(_):
        return {"state": "accepted"}

    bridge.dynamic_handlers["internal_test"] = handler
    try:
        await bridge.request("fixture/open", {})
        keys = set(bridge.requests)
        await bridge.request(
            "fixture/terminal",
            {"threadId": thread, "turn": {"id": turn, "status": status}},
        )
        assert set(bridge.requests) == keys == bridge.responding
        assert not (await bridge.drain_status())["idle"]
    finally:
        await bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("responded", [False, True])
async def test_external_approval_is_never_retired_by_internal_terminal_cleanup(
    tmp_path, responded
):
    script = tmp_path / "native.py"
    script.write_text(SCRIPT)
    bridge = CodexClient(lambda *_: None, [sys.executable, str(script)])

    async def handler(_):
        return {"state": "accepted"}

    bridge.dynamic_handlers["internal_test"] = handler
    try:
        await bridge.request("fixture/open", {"external": True})
        external = next(
            key for key, value in bridge.requests.items() if not value.get("_internal")
        )
        if responded:
            await bridge.respond(external, {"decision": "accept"})
        await bridge.request(
            "fixture/terminal",
            {"threadId": "source", "turn": {"id": "turn", "status": "completed"}},
        )
        assert set(bridge.requests) == {external}
        assert (external in bridge.responding) == responded
        assert not (await bridge.drain_status())["idle"]
    finally:
        await bridge.close()


@pytest.mark.asyncio
async def test_unanswered_internal_handler_is_not_retired_or_cancelled(tmp_path):
    script = tmp_path / "native.py"
    script.write_text(SCRIPT)
    bridge = CodexClient(lambda *_: None, [sys.executable, str(script)])
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(_):
        entered.set()
        await release.wait()
        return {"state": "accepted"}

    bridge.dynamic_handlers["internal_test"] = handler
    try:
        await bridge.request("fixture/open", {"waitResponse": False})
        await asyncio.wait_for(entered.wait(), 2)
        await bridge.request(
            "fixture/terminal",
            {"threadId": "source", "turn": {"id": "turn", "status": "completed"}},
        )
        assert len(bridge.requests) == 1 and not bridge.responding
        assert all(not task.done() for task in bridge.dynamic_tasks)
        assert not (await bridge.drain_status())["idle"]
    finally:
        await bridge.close()
