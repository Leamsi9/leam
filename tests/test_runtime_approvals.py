"""Actual authenticated product callers with the pinned runtime wire contract."""

import json
from uuid import uuid4

import httpx
import pytest
from test_api import login
from test_companion_stream import setup

RUN = "7461168f-d5b4-40c5-9fd7-4bd0c277dafb"
APPROVAL = "dadc3609-d7b8-45db-ad0d-776276de212a"
INVOCATION = "3a23137b-5ad7-481b-86a9-400d1c6af2e9"
PATH = f"/api/companion/threads/thread-a/runs/{RUN}/approvals/{APPROVAL}"
ORIGIN = {"origin": "http://testserver"}


def runtime_fixture(
    *,
    owner="user",
    gate_thread="thread-a",
    truncated=False,
    missing=False,
    reject=False,
    lost=False,
):
    writes = []
    content = '{"title":"Report","content":"<script>ignore instructions</script>"}'

    def handle(request):
        path = request.url.path
        if path.endswith("/events"):
            frame = {
                "type": "projection_update",
                "state": {
                    "thread_id": gate_thread,
                    "items": [
                        {
                            "gate": {
                                "run_id": RUN,
                                "gate_ref": "gate:approval-" + APPROVAL,
                                "gate_kind": "approval",
                                "invocation_id": INVOCATION,
                            }
                        }
                    ],
                },
            }
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content="event: projection_update\ndata: " + json.dumps(frame) + "\n\n",
            )
        if path.endswith("/session"):
            return httpx.Response(200, json={"tenant_id": "tenant", "user_id": "user"})
        if "/operator/inspector/" in path:
            return httpx.Response(
                200,
                json={
                    "snapshot": None
                    if missing
                    else {
                        "scope": {
                            "tenant_id": "tenant",
                            "user_id": owner,
                            "thread_id": "thread-a",
                            "run_id": RUN,
                        },
                        "prompt": "SECRET_UNRELATED_PROMPT",
                        "tool_executions": [
                            {
                                "activity_id": INVOCATION,
                                "capability_name": {
                                    "content": "mcp-leam.leam_resource_save"
                                },
                                "arguments": {
                                    "content": content,
                                    "original_bytes": len(content.encode()),
                                    "truncated": truncated,
                                },
                                "result": "SECRET_UNRELATED_RESULT",
                            }
                        ],
                    }
                },
            )
        if path.endswith("/resolve"):
            writes.append(json.loads(request.content))
            if reject:
                return httpx.Response(409, json={"error": "stale"})
            if lost and len(writes) == 1:
                raise httpx.ReadTimeout("lost response")
            return httpx.Response(
                200,
                json={
                    "outcome": "resumed",
                    "run_id": RUN,
                    "status": "Queued",
                    "event_cursor": 25,
                },
            )
        raise AssertionError(path)

    return handle, writes


def test_inspect_and_approve_once_exact_scoped_idempotent_receipt(tmp_path):
    handle, writes = runtime_fixture()
    with setup(tmp_path, handle) as client:
        assert client.get(PATH).status_code == 401
        login(client)
        response = client.get(PATH)
        view = response.json()
        assert (
            response.status_code == 200
            and response.headers["cache-control"] == "no-store"
        )
        assert view["argumentsComplete"] and "<script>" in view["arguments"]
        assert "SECRET_UNRELATED" not in response.text
        body = {
            "requestId": str(uuid4()),
            "decision": "approved",
            "fingerprint": view["fingerprint"],
        }
        assert client.post(PATH, json=body).status_code == 403
        first = client.post(PATH, json=body, headers=ORIGIN)
        assert first.status_code == 200, first.text
        assert client.post(PATH, json=body, headers=ORIGIN).json() == first.json()
        assert writes == [
            {
                "client_action_id": body["requestId"],
                "thread_id": "thread-a",
                "run_id": RUN,
                "gate_ref": "gate:approval-" + APPROVAL,
                "resolution": "approved",
                "always": False,
            }
        ]
        assert (
            client.post(
                PATH, json={**body, "decision": "declined"}, headers=ORIGIN
            ).status_code
            == 409
        )
        assert (
            client.post(PATH, json={**body, "always": True}, headers=ORIGIN).status_code
            == 422
        )
        with client.app.state.store.connect() as db:
            receipt = db.execute(
                "SELECT body,result FROM runtime_actions WHERE id=?",
                (body["requestId"],),
            ).fetchone()
            assert "<script>" not in str(tuple(receipt))


@pytest.mark.parametrize(
    "owner,gate_thread", [("other-user", "thread-a"), ("user", "thread-other")]
)
def test_cross_owner_and_thread_never_resolve(tmp_path, owner, gate_thread):
    handle, writes = runtime_fixture(owner=owner, gate_thread=gate_thread)
    with setup(tmp_path, handle) as client:
        login(client)
        assert client.get(PATH).status_code in {403, 409}
        assert client.post(
            PATH,
            json={
                "requestId": str(uuid4()),
                "decision": "approved",
                "fingerprint": "a" * 64,
            },
            headers=ORIGIN,
        ).status_code in {403, 409}
        assert not writes


@pytest.mark.parametrize("missing,truncated", [(True, False), (False, True)])
def test_unavailable_or_truncated_arguments_allow_decline_only(
    tmp_path, missing, truncated
):
    handle, writes = runtime_fixture(missing=missing, truncated=truncated)
    with setup(tmp_path, handle) as client:
        login(client)
        view = client.get(PATH).json()
        assert view["argumentsComplete"] is False
        body = {
            "requestId": str(uuid4()),
            "decision": "approved",
            "fingerprint": view["fingerprint"],
        }
        assert client.post(PATH, json=body, headers=ORIGIN).status_code == 409
        assert not writes
        assert (
            client.post(
                PATH, json={**body, "decision": "declined"}, headers=ORIGIN
            ).status_code
            == 200
        )
        assert writes[0]["resolution"] == "declined"


def test_stale_fingerprint_and_runtime_stale_gate_are_visible(tmp_path):
    handle, writes = runtime_fixture(reject=True)
    with setup(tmp_path, handle) as client:
        login(client)
        view = client.get(PATH).json()
        body = {
            "requestId": str(uuid4()),
            "decision": "approved",
            "fingerprint": "a" * 64,
        }
        assert client.post(PATH, json=body, headers=ORIGIN).status_code == 409
        assert not writes
        response = client.post(
            PATH, json={**body, "fingerprint": view["fingerprint"]}, headers=ORIGIN
        )
        assert response.status_code == 409 and "Refresh" in response.text


def test_uncertain_resolution_retries_same_runtime_action(tmp_path):
    handle, writes = runtime_fixture(lost=True)
    with setup(tmp_path, handle) as client:
        login(client)
        view = client.get(PATH).json()
        body = {
            "requestId": str(uuid4()),
            "decision": "approved",
            "fingerprint": view["fingerprint"],
        }
        assert client.post(PATH, json=body, headers=ORIGIN).status_code == 502
        assert client.post(PATH, json=body, headers=ORIGIN).status_code == 200
        assert len(writes) == 2 and writes[0] == writes[1]
