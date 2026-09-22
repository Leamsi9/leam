"""Actual authenticated MCP/API callers for optional trusted Today provenance."""

import json
import time
from contextlib import contextmanager
from uuid import uuid4

import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api import app as app_module
from leam_api import approval_policy
from leam_api.domain_tools import DomainTools
from leam_api.ironclaw import IronClaw
from leam_api.mcp_server import create_mcp_app
from leam_api.tool_scope import META_KEY, HostScope, forwarding_proof

CREDENTIAL = "synthetic-runtime-scope-" + "k" * 48
THREAD = "bc720fca-b85e-45bc-90c0-bbad7fd911ba"


def host_scope(**changes):
    return HostScope(
        version=1,
        tenantId="tenant-a",
        userId="user-a",
        threadId=THREAD,
        invocationId=str(uuid4()),
        **changes,
    )


def proposal(**changes):
    return {
        "requestId": str(uuid4()),
        "threadId": THREAD,
        "operation": "commitment.create",
        "input": {"title": "Synthetic Today task"},
        "reason": "Explicit user planning request",
        **changes,
    }


@contextmanager
def setup(tmp_path, monkeypatch, *, enabled=True):
    state = {"timelineOwner": ["tenant-a", "user-a"], "fail": False, "calls": []}

    def runtime_response(request):
        state["calls"].append(request.url.path)
        if state["fail"]:
            return httpx.Response(503)
        if request.url.path.endswith("/session"):
            return httpx.Response(
                200, json={"tenant_id": "tenant-a", "user_id": "user-a"}
            )
        return httpx.Response(
            200,
            json={
                "thread": {
                    "thread_id": THREAD,
                    "scope": {
                        "tenant_id": state["timelineOwner"][0],
                        "owner_user_id": state["timelineOwner"][1],
                    },
                },
                "messages": [],
                "next_cursor": None,
            },
        )

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="synthetic-token",
        transport=httpx.MockTransport(runtime_response),
    )

    def factory(*args, **kwargs):
        domain = DomainTools(*args, **kwargs)
        state["domain"] = domain
        return domain

    monkeypatch.setattr(app_module, "DomainTools", factory)
    app = app_module.create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        runtime=runtime,
        runtime_scope_credential=CREDENTIAL if enabled else None,
    )
    with TestClient(app, client=("127.0.0.1", 1234)) as browser:
        login(browser)
        domain = state["domain"]
        domain.store.set("today-reconciliation:owner", ["tenant-a", "user-a"])
        domain.store.set(
            "companion-agenda:" + THREAD, {"date": "2026-09-22", "timezone": "UTC"}
        )
        yield app, browser, domain, state


def invoke(client, domain, arguments, *, scope=None, proof=None, tool="leam_propose"):
    body = {"tool": tool, "arguments": arguments}
    if scope is not None:
        body["hostScope"] = scope.model_dump()
        body["scopeProof"] = (
            proof or forwarding_proof(CREDENTIAL, tool, arguments, scope)
        ).model_dump()
    return client.post(
        "/api/internal/tools",
        json=body,
        headers={"authorization": "Bearer " + domain.token},
    )


def test_inert_default_rejects_scope_and_preserves_manual_legacy(tmp_path, monkeypatch):
    with setup(tmp_path, monkeypatch, enabled=False) as (_, client, domain, _):
        assert invoke(client, domain, proposal(), scope=host_scope()).status_code == 403
        result = invoke(client, domain, proposal())
        assert result.status_code == 200
        assert result.json()["state"] == "pending"
        assert domain.store.entities("commitment") == []


def test_verified_today_uses_policy_schema_and_idempotency(tmp_path, monkeypatch):
    with setup(tmp_path, monkeypatch) as (_, client, domain, state):
        scope = host_scope()
        schema = invoke(
            client,
            domain,
            {"operation": "commitment.create"},
            scope=scope,
            tool="leam_operation_schema",
        )
        assert schema.json()["approvalPolicy"]["origin"] == "today"
        assert schema.json()["approvalPolicy"]["mode"] == "automatic"
        arguments = proposal()
        result = invoke(client, domain, arguments, scope=scope)
        assert result.status_code == 200, result.text
        assert result.json()["state"] == "complete"
        assert (
            invoke(client, domain, arguments, scope=scope).json()["id"]
            == result.json()["id"]
        )
        assert len(domain.store.entities("commitment")) == 1
        assert any("/timeline" in path for path in state["calls"])
        coding = proposal(
            operation="coding.handoff",
            input={
                "title": "Synthetic code review",
                "instructions": "Review the synthetic fixture",
            },
        )
        assert invoke(client, domain, coding, scope=scope).json()["state"] == "pending"
        policy = approval_policy.get(domain.store)
        approval_policy.update(
            domain.store,
            approval_policy.ApprovalPolicy(**{**policy, "todayRequiresApproval": True}),
        )
        assert (
            invoke(client, domain, proposal(), scope=scope).json()["state"] == "pending"
        )
        assert (
            invoke(
                client,
                domain,
                {"operation": "coding.handoff"},
                scope=scope,
                tool="leam_operation_schema",
            ).json()["approvalPolicy"]["mode"]
            == "manual"
        )


def test_manual_pending_does_not_promote_on_trusted_retry(tmp_path, monkeypatch):
    with setup(tmp_path, monkeypatch) as (_, client, domain, _):
        arguments = proposal()
        original = invoke(client, domain, arguments).json()
        retried = invoke(client, domain, arguments, scope=host_scope()).json()
        assert original["id"] == retried["id"]
        assert retried["state"] == "pending"
        assert domain.store.entities("commitment") == []


@pytest.mark.parametrize(
    "case",
    [
        "foreign-user",
        "foreign-tenant",
        "wrong-thread",
        "wrong-owner",
        "no-owner",
        "runtime-failure",
        "bad-proof",
        "expired-proof",
        "changed-body",
    ],
)
def test_scope_failures_never_mutate_records(tmp_path, monkeypatch, case):
    with setup(tmp_path, monkeypatch) as (_, client, domain, state):
        scope = host_scope()
        arguments = proposal()
        if case == "foreign-user":
            scope = scope.model_copy(update={"userId": "someone-else"})
        if case == "foreign-tenant":
            scope = scope.model_copy(update={"tenantId": "other-tenant"})
        if case == "wrong-thread":
            arguments["threadId"] = str(uuid4())
        if case == "wrong-owner":
            state["timelineOwner"] = ["tenant-a", "someone-else"]
        if case == "no-owner":
            domain.store.set("today-reconciliation:owner", None)
        if case == "runtime-failure":
            state["fail"] = True
        proof = forwarding_proof(CREDENTIAL, "leam_propose", arguments, scope)
        if case == "bad-proof":
            proof = proof.model_copy(update={"signature": "0" * 64})
        if case == "expired-proof":
            proof = forwarding_proof(
                CREDENTIAL, "leam_propose", arguments, scope, now=time.time() - 120
            )
        if case == "changed-body":
            arguments["input"]["title"] = "Changed after signing"
        result = invoke(client, domain, arguments, scope=scope, proof=proof)
        assert result.status_code == (503 if case == "runtime-failure" else 403), (
            result.text
        )
        assert domain.store.entities("commitment") == []
        assert domain.proposals.list(THREAD)["items"] == []


def test_unbound_thread_has_no_automatic_origin(tmp_path, monkeypatch):
    with setup(tmp_path, monkeypatch) as (_, client, domain, _):
        with domain.store.connect() as db:
            db.execute(
                "DELETE FROM settings WHERE key=?", ("companion-agenda:" + THREAD,)
            )
        assert (
            invoke(client, domain, proposal(), scope=host_scope()).json()["state"]
            == "pending"
        )


@pytest.mark.parametrize(
    "trusted,with_meta,expected",
    [(True, True, "complete"), (False, True, "pending"), (True, False, "pending")],
)
def test_actual_mcp_context_authentication_and_domain_boundary(
    tmp_path, monkeypatch, trusted, with_meta, expected
):
    with setup(tmp_path, monkeypatch) as (app, _browser, domain, _):
        server = create_mcp_app(
            "http://127.0.0.1:46400",
            domain.token,
            runtime_scope_credential=CREDENTIAL,
            transport=httpx2.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        )
        with TestClient(
            server, base_url="https://127.0.0.1:46420", client=("127.0.0.1", 1234)
        ) as mcp:
            headers = {
                "Authorization": "Bearer " + (CREDENTIAL if trusted else domain.token),
                "Accept": "application/json, text/event-stream",
            }
            params = {"name": "leam_propose", "arguments": {"request": proposal()}}
            if with_meta:
                params["_meta"] = {META_KEY: host_scope().model_dump()}
            response = mcp.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": params,
                },
            )
            result = response.json()["result"]
            assert result["isError"] is False, response.text
            assert domain.proposals.list(THREAD)["items"][0]["state"] == expected
            assert len(domain.store.entities("commitment")) == (
                1 if expected == "complete" else 0
            )
            listing = mcp.post(
                "/mcp",
                headers=headers,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            ).json()["result"]["tools"]
            for tool in listing:
                if tool["name"] in ("leam_propose", "leam_operation_schema"):
                    assert "context" not in tool["inputSchema"]["properties"]
                    assert "hostScope" not in json.dumps(tool["inputSchema"])
            assert CREDENTIAL not in response.text
            assert (
                mcp.post(
                    "/mcp", headers={**headers, "origin": "https://evil.test"}, json={}
                ).status_code
                == 403
            )


def test_concurrent_scopes_do_not_lend_today_authority(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    with setup(tmp_path, monkeypatch) as (_, client, domain, _):
        good = host_scope()
        foreign = host_scope().model_copy(update={"userId": "someone-else"})
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(invoke, client, domain, proposal(), scope=scope)
                for scope in (good, foreign)
            ]
            results = [future.result() for future in futures]
        assert [result.status_code for result in results] == [200, 403]
        assert len(domain.store.entities("commitment")) == 1


def test_scope_proof_cannot_be_transplanted_to_another_tool(tmp_path, monkeypatch):
    with setup(tmp_path, monkeypatch) as (_, client, domain, _):
        scope = host_scope()
        arguments = proposal()
        proof = forwarding_proof(CREDENTIAL, "leam_propose", arguments, scope)
        assert (
            invoke(
                client,
                domain,
                {"operation": "commitment.create"},
                scope=scope,
                proof=proof,
                tool="leam_operation_schema",
            ).status_code
            == 403
        )
        assert domain.store.entities("commitment") == []


def test_private_derivation_and_backup_restore_preserve_runtime_credential(
    tmp_path, monkeypatch
):
    import os
    from pathlib import Path

    from test_backups import prepared

    from leam_api.backups import Backups, restore
    from leam_api.tool_scope import configured_credential, installation_credential

    store = prepared(tmp_path / "data")
    monkeypatch.delenv("LEAM_RUNTIME_SCOPE_ENABLED", raising=False)
    assert configured_credential(store.path.parent) is None
    monkeypatch.setenv("LEAM_RUNTIME_SCOPE_ENABLED", "1")
    credential = configured_credential(store.path.parent)
    assert len(credential) == 64
    assert credential != (store.path.parent / "tools-token").read_text().strip()
    backup = Backups(store).create()
    target = tmp_path / "restored"
    restore(Backups(store).path(backup["id"]), target, store.path.parent)
    assert configured_credential(target) == credential
    assert set(Path(target).glob("*scope*")) == set()
    key = store.path.parent / "accounts-key"
    key.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        configured_credential(store.path.parent)
    key.chmod(0o600)
    other = tmp_path / "other"
    other.mkdir()
    (other / "accounts-key").symlink_to(key)
    with pytest.raises(OSError):
        installation_credential(other, enabled=True)
    assert installation_credential(other, enabled=False) is None
    key.write_bytes(os.urandom(32))
    assert configured_credential(store.path.parent) != credential


def test_both_production_factories_use_explicit_startup_opt_in(tmp_path, monkeypatch):
    from leam_api import mcp_server
    from leam_api.tool_scope import installation_credential

    (tmp_path / "accounts-key").write_bytes(b"k" * 32)
    (tmp_path / "accounts-key").chmod(0o600)
    (tmp_path / "tools-token").write_text("t" * 48)
    monkeypatch.setenv("LEAM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LEAM_RUNTIME_SCOPE_ENABLED", "1")
    captured = []
    monkeypatch.setattr(app_module, "create_app", lambda *a, **kw: captured.append(kw))
    monkeypatch.setattr(
        mcp_server, "create_mcp_app", lambda *a, **kw: captured.append(kw)
    )
    app_module.application()
    mcp_server.application()
    assert all(
        row["runtime_scope_credential"]
        == installation_credential(tmp_path, enabled=True)
        for row in captured
    )
    monkeypatch.setenv("LEAM_RUNTIME_SCOPE_ENABLED", "0")
    captured.clear()
    app_module.application()
    mcp_server.application()
    assert all(row["runtime_scope_credential"] is None for row in captured)


@pytest.mark.parametrize("invalid_scope", [[], "not-an-identity", {"version": 1}])
def test_actual_mcp_rejects_invalid_authenticated_scope_before_domain_write(tmp_path, monkeypatch, invalid_scope):
    with setup(tmp_path, monkeypatch) as (app, _browser, domain, _):
        server = create_mcp_app(
            "http://127.0.0.1:46400", domain.token,
            runtime_scope_credential=CREDENTIAL,
            transport=httpx2.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        )
        with TestClient(server, base_url="https://127.0.0.1:46420", client=("127.0.0.1", 1234)) as mcp:
            response = mcp.post("/mcp", headers={
                "Authorization": "Bearer " + CREDENTIAL,
                "Accept": "application/json, text/event-stream",
            }, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "leam_propose", "arguments": {"request": proposal()},
                "_meta": {META_KEY: invalid_scope},
            }})
            assert response.json()["result"]["isError"] is True
            assert domain.proposals.list(THREAD)["items"] == []
            assert domain.store.entities("commitment") == []
