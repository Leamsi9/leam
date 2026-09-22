import json
from copy import deepcopy

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from leam_api.ironclaw import IronClaw
from leam_api.tool_permissions import ToolPermissions, router


def fixture():
    return {
        "entries": [
            {
                "key": "agent.auto_approve_tools",
                "value": True,
                "source": "default",
                "redacted": False,
                "mutable": True,
            },
            *[
                {
                    "key": "tool." + name,
                    "value": {
                        "name": name,
                        "description": name + " description",
                        "state": "always_allow" if not locked else "ask_each_time",
                        "default_state": "ask_each_time",
                        "locked": locked,
                        "effective_source": "global" if not locked else "locked",
                    },
                    "source": "global" if not locked else "locked",
                    "redacted": False,
                    "mutable": not locked,
                }
                for name, locked in [
                    ("builtin.time", False),
                    ("builtin.shell", False),
                    ("builtin.operator_config_set_tool_permission", True),
                    ("mcp-leam.leam_today", False),
                ]
            ],
        ]
    }


def setup():
    state = fixture()
    calls = []

    async def handler(request):
        calls.append(
            (
                request.method,
                request.url.path,
                json.loads(request.content) if request.content else None,
                request.headers.get("authorization"),
            )
        )
        assert request.headers["authorization"] == "Bearer fixture-runtime-token"
        if request.method == "GET":
            return httpx.Response(200, json=state)
        body = json.loads(request.content)
        key = "tool." + request.url.path.rsplit("/", 1)[-1]
        entry = next(row for row in state["entries"] if row["key"] == key)
        entry["value"]["state"] = body["state"]
        return httpx.Response(200, json={"entry": deepcopy(entry)})

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="fixture-runtime-token",
        transport=httpx.MockTransport(handler),
    )
    domain = ToolPermissions(runtime)
    app = FastAPI()
    app.include_router(router(domain))
    return domain, TestClient(app), calls


def test_proxy_preserves_exact_method_path_body_and_protects_coding_boundary():
    _domain, client, calls = setup()
    with client:
        listing = client.get("/api/companion/tools")
        assert listing.status_code == 200
        items = {item["id"]: item for item in listing.json()["items"]}
        assert items["builtin.shell"]["protected"]
        assert (
            client.post(
                "/api/companion/tools/builtin.shell", json={"state": "always_allow"}
            ).status_code
            == 409
        )
        assert not any(call[0] == "POST" for call in calls)
        saved = client.post(
            "/api/companion/tools/builtin.time", json={"state": "ask_each_time"}
        )
        assert saved.status_code == 200 and saved.json()["state"] == "ask_each_time"
        assert (
            "POST",
            "/api/webchat/v2/settings/tools/builtin.time",
            {"state": "ask_each_time"},
            "Bearer fixture-runtime-token",
        ) in calls
        assert (
            client.post(
                "/api/companion/tools/missing.tool", json={"state": "disabled"}
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/companion/tools/builtin.time", json={"state": "anything"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/companion/tools/builtin.operator_config_set_tool_permission",
                json={"state": "disabled"},
            ).status_code
            == 409
        )


def test_upstream_failure_is_redacted():
    async def handler(request):
        return httpx.Response(500, text="secret-upstream-diagnostic")

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="private-runtime-token",
        transport=httpx.MockTransport(handler),
    )
    app = FastAPI()
    app.include_router(router(ToolPermissions(runtime)))
    with TestClient(app) as client:
        response = client.get("/api/companion/tools")
        assert response.status_code == 503
        assert "secret-upstream-diagnostic" not in response.text
        assert "private-runtime-token" not in response.text


async def test_profile_backup_precedes_mutation_and_exact_effective_settings_restore(
    tmp_path,
):
    from leam_api.tool_permissions import ALLOWED

    state = fixture()
    names = {
        entry["key"][5:]
        for entry in state["entries"]
        if entry["key"].startswith("tool.")
    }
    template = deepcopy(state["entries"][1])
    for name in ALLOWED - names:
        entry = deepcopy(template)
        entry["key"] = "tool." + name
        entry["value"]["name"] = name
        state["entries"].append(entry)
    global_setting = True
    overrides = {}
    calls = []
    backup = tmp_path / "permissions.json"

    def effective():
        current = deepcopy(state)
        current["entries"][0]["value"] = global_setting
        for entry in current["entries"][1:]:
            name = entry["value"]["name"]
            if entry["value"]["locked"]:
                continue
            entry["value"]["state"] = overrides.get(
                name, "always_allow" if global_setting else "ask_each_time"
            )
            entry["value"]["effective_source"] = (
                "override" if name in overrides else "global"
            )
        return current

    async def handler(request):
        nonlocal global_setting
        if request.method == "GET":
            return httpx.Response(200, json=effective())
        assert backup.is_file() and backup.stat().st_mode & 0o077 == 0
        body = json.loads(request.content)
        calls.append((request.method, request.url.path, body))
        if request.url.path.endswith("/settings/tools"):
            global_setting = body["enabled"]
        else:
            name = request.url.path.rsplit("/", 1)[-1]
            assert name != "builtin.operator_config_set_tool_permission"
            if body["state"] == "default":
                overrides.pop(name, None)
            else:
                overrides[name] = body["state"]
        return httpx.Response(200, json={})

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="fixture",
        transport=httpx.MockTransport(handler),
    )
    permissions = ToolPermissions(runtime)
    before = await permissions.list()
    preview = await permissions.preview()
    assert preview["mandatoryApprovalTools"] == [
        "builtin.operator_config_set_tool_permission"
    ]
    assert calls == []
    applied = await permissions.apply_profile(backup, "http://127.0.0.1:46410")
    assert applied["configurationMatched"] and not global_setting
    assert overrides["builtin.shell"] == "disabled"
    # Explicitly requested private Resource saves must survive profile reapplication.
    assert overrides["mcp-leam.leam_resource_save"] == "always_allow"
    assert (
        "POST",
        "/api/webchat/v2/settings/tools/mcp-leam.leam_resource_save",
        {"state": "always_allow"},
    ) in calls
    # Do not broaden a different write permission or enable global auto-approval.
    assert overrides["mcp-leam.create_inbox_item"] == "ask_each_time"
    assert all(
        overrides[name] == "always_allow"
        for name in ALLOWED - {"mcp-leam.create_inbox_item"}
    )
    assert (await permissions.restore_profile(backup, "http://127.0.0.1:46410"))[
        "restored"
    ]
    assert (await permissions.list()) == before
    await runtime.close()


def test_real_app_auth_and_readback_mismatch(tmp_path):
    from test_api import FakeCodex, login

    from leam_api.app import create_app

    calls = []

    async def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json=fixture())

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="fixture",
        transport=httpx.MockTransport(handler),
    )
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        runtime=runtime,
    )
    app.include_router(router(ToolPermissions(runtime)))
    with TestClient(app) as client:
        assert client.get("/api/companion/tools").status_code == 401
        login(client)
        assert (
            client.post(
                "/api/companion/tools/builtin.time", json={"state": "disabled"}
            ).status_code
            == 403
        )
        result = client.post(
            "/api/companion/tools/builtin.time",
            json={"state": "disabled"},
            headers={"origin": "http://testserver"},
        )
        assert result.status_code == 503 and "readback" in result.text
        assert ("POST", "/api/webchat/v2/settings/tools/builtin.time") in calls


async def test_profile_failure_retains_backup_and_does_not_claim_success(tmp_path):
    import pytest

    from leam_api.ironclaw import RuntimeError

    writes = []

    async def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=fixture())
        writes.append((request.url.path, json.loads(request.content)))
        return (
            httpx.Response(200, json={})
            if len(writes) == 1
            else httpx.Response(500, json={"private": "do not show"})
        )

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="fixture",
        transport=httpx.MockTransport(handler),
    )
    permissions = ToolPermissions(runtime)
    backup = tmp_path / "previous.json"
    with pytest.raises(RuntimeError, match="HTTP 500"):
        await permissions.apply_profile(backup, "http://127.0.0.1:46410")
    assert json.loads(backup.read_text())["settings"]["autoApprove"] is True
    assert writes[0] == ("/api/webchat/v2/settings/tools", {"enabled": False})
    assert len(writes) == 2
    await runtime.close()


def test_unreviewed_future_capability_cannot_be_enabled_through_owner_ui():
    state = fixture()
    extra = deepcopy(state["entries"][1])
    extra["key"] = "tool.future.dynamic_executor"
    extra["value"]["name"] = "future.dynamic_executor"
    state["entries"].append(extra)
    writes = []

    async def handler(request):
        if request.method == "POST":
            writes.append(request.url.path)
        return httpx.Response(200, json=state)

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="fixture",
        transport=httpx.MockTransport(handler),
    )
    app = FastAPI()
    app.include_router(router(ToolPermissions(runtime)))
    with TestClient(app) as client:
        response = client.post(
            "/api/companion/tools/future.dynamic_executor",
            json={"state": "always_allow"},
        )
        assert response.status_code == 409
        assert writes == []


async def test_apply_requires_exact_reviewed_preview_before_backup_or_write(tmp_path):
    import pytest

    writes = []

    async def handler(request):
        if request.method == "POST":
            writes.append(request.url.path)
        return httpx.Response(200, json=fixture())

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="fixture",
        transport=httpx.MockTransport(handler),
    )
    domain = ToolPermissions(runtime)
    preview = await domain.preview()
    assert len(preview["previewToken"]) == 64
    backup = tmp_path / "stale.json"
    with pytest.raises(ValueError, match="changed"):
        await domain.apply_profile(
            backup, "http://127.0.0.1:46410", expected_preview="0" * 64
        )
    assert not backup.exists() and writes == []
    await runtime.close()


async def test_restore_rejects_changed_lock_or_default_before_any_write(tmp_path):
    import pytest

    for changed in ("locked", "default_state"):
        state = fixture()
        writes = []

        async def handler(request, state=state, writes=writes):
            if request.method != "GET":
                writes.append(
                    (request.method, request.url.path, json.loads(request.content))
                )
            return httpx.Response(200, json=state)

        runtime = IronClaw(
            "http://127.0.0.1:46410",
            None,
            token="fixture",
            transport=httpx.MockTransport(handler),
        )
        permissions = ToolPermissions(runtime)
        backup = tmp_path / (changed + ".json")
        backup.write_text(
            json.dumps(
                {
                    "version": 1,
                    "runtimeUrl": "http://127.0.0.1:46410",
                    "settings": await permissions.list(),
                }
            )
        )
        backup.chmod(0o600)
        state["entries"][1]["value"][changed] = (
            True if changed == "locked" else "disabled"
        )
        with pytest.raises(ValueError, match="locks or defaults changed"):
            await permissions.restore_profile(backup, "http://127.0.0.1:46410")
        assert writes == []
        await runtime.close()
