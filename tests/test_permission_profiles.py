import json
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from test_api import FakeCodex, login
from test_tool_permissions import fixture

from leam_api.app import create_app
from leam_api.ironclaw import IronClaw
from leam_api.tool_permissions import ALLOWED


def setup(tmp_path):
    template = fixture()
    names = {entry["key"][5:] for entry in template["entries"][1:]}
    for name in ALLOWED - names:
        entry = deepcopy(template["entries"][1])
        entry["key"] = "tool." + name
        entry["value"]["name"] = name
        template["entries"].append(entry)
    state = {
        "global": True,
        "overrides": {},
        "writes": [],
        "fail": False,
        "template": template,
        "reads": 0,
    }

    def handler(request):
        if request.method == "GET":
            state["reads"] += 1
            if state.get("mutate_at") == state["reads"]:
                state["overrides"]["builtin.time"] = "disabled"
        if request.method != "GET":
            body = json.loads(request.content)
            state["writes"].append((request.url.path, body))
            if state["fail"]:
                return httpx.Response(500, json={"secret": "private-upstream"})
            if request.url.path.endswith("/settings/tools"):
                state["global"] = body["enabled"]
            else:
                name = request.url.path.rsplit("/", 1)[-1]
                if body["state"] == "default":
                    state["overrides"].pop(name, None)
                else:
                    state["overrides"][name] = body["state"]
        result = deepcopy(template)
        result["entries"][0]["value"] = state["global"]
        for entry in result["entries"][1:]:
            value = entry["value"]
            if value["locked"]:
                continue
            value["state"] = state["overrides"].get(
                value["name"], "always_allow" if state["global"] else "ask_each_time"
            )
            value["effective_source"] = (
                "override" if value["name"] in state["overrides"] else "global"
            )
        return httpx.Response(200, json=result)

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="private-runtime",
        transport=httpx.MockTransport(handler),
    )
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        runtime=runtime,
    )
    return app, state


def test_explicit_profile_apply_restore_backup_and_replay(tmp_path):
    with patch("leam_api.system_inspection.SystemInspector._services", return_value={}):
        app, state = setup(tmp_path)
        with TestClient(app) as client:
            path = "/api/companion/tools/profile"
            assert client.get(path).status_code == 401
            login(client)
            preview = client.get(path).json()
            assert preview["hostCeiling"]["enforcementStatus"] == "unknown"
            assert state["writes"] == []
            body = {
                "requestId": str(uuid4()),
                "confirmed": True,
                "previewToken": preview["previewToken"],
            }
            assert client.post(path + "/apply", json=body).status_code == 403
            headers = {"origin": "http://testserver"}
            result = client.post(path + "/apply", json=body, headers=headers)
            assert result.status_code == 200, result.text
            assert result.json()["state"] == "complete"
            assert (
                not state["global"]
                and state["overrides"]["builtin.shell"] == "disabled"
            )
            assert all(state["overrides"][name] == "always_allow" for name in ALLOWED)
            count = len(state["writes"])
            assert (
                client.post(path + "/apply", json=body, headers=headers).json()
                == result.json()
            )
            assert len(state["writes"]) == count
            stored = app.state.store.get("permission-profile:" + body["requestId"])
            assert stored["snapshot"]["settings"]["autoApprove"] is True
            assert "private-runtime" not in json.dumps(stored)
            # Database snapshot remains sufficient when a separate file is absent.
            for file in (tmp_path / "permission-snapshots").glob("*.json"):
                file.unlink()
            restore = client.get(path + "/restore/" + body["requestId"]).json()
            restored = client.post(
                path + "/restore",
                headers=headers,
                json={
                    "requestId": str(uuid4()),
                    "confirmed": True,
                    "backupId": body["requestId"],
                    "previewToken": restore["previewToken"],
                },
            )
            assert restored.status_code == 200, restored.text
            assert restored.json()["state"] == "complete" and state["global"]
            assert state["overrides"] == {}


def test_stale_profile_and_partial_failure_never_replay_mutations(tmp_path):
    with patch("leam_api.system_inspection.SystemInspector._services", return_value={}):
        app, state = setup(tmp_path)
        with TestClient(app) as client:
            login(client)
            path = "/api/companion/tools/profile"
            headers = {"origin": "http://testserver"}
            preview = client.get(path).json()
            state["global"] = False
            body = {
                "requestId": str(uuid4()),
                "confirmed": True,
                "previewToken": preview["previewToken"],
            }
            assert (
                client.post(path + "/apply", json=body, headers=headers).status_code
                == 409
            )
            assert not state["writes"]
            state["fail"] = True
            body.update(
                requestId=str(uuid4()),
                previewToken=client.get(path).json()["previewToken"],
            )
            failed = client.post(path + "/apply", json=body, headers=headers)
            assert (
                failed.status_code == 200 and failed.json()["state"] == "needs_review"
            )
            assert "private-upstream" not in failed.text
            count = len(state["writes"])
            assert (
                client.post(path + "/apply", json=body, headers=headers).json()
                == failed.json()
            )
            assert len(state["writes"]) == count
            assert app.state.store.get("permission-profile:" + body["requestId"])[
                "snapshot"
            ]


def test_host_ceiling_claim_requires_reviewed_source_binary_and_live_environment(
    tmp_path,
):
    from leam_api.host_tool_ceiling import RECOMMENDED
    from leam_api.system_inspection import SystemInspector

    root = tmp_path / "release"
    (root / "docs/architecture").mkdir(parents=True)
    lock = {"sourceCommit": "a" * 40, "sha256": "b" * 64}
    lock_file = root / "docs/architecture/runtime-lock.json"
    lock_file.write_text(json.dumps(lock))
    services = {
        "runtime": {
            "binary": {"sha256": "b" * 64, "observedAt": 1},
            "hostCeiling": {"available": True, "configured": True, "ids": RECOMMENDED},
        }
    }
    original = SystemInspector.__init__

    def create(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.root = root

    with (
        patch.object(SystemInspector, "__init__", create),
        patch.object(SystemInspector, "_services", return_value=services),
    ):
        app, _ = setup(tmp_path / "data")
        with TestClient(app) as client:
            login(client)
            path = "/api/companion/system?section=tools"
            assert (
                client.get(path).json()["sections"]["tools"]["data"]["hostCeiling"][
                    "enforcementStatus"
                ]
                == "unknown"
            )
            lock["hostCapabilityCeilingVersion"] = 1
            lock_file.write_text(json.dumps(lock))
            verified = client.get(path).json()["sections"]["tools"]["data"][
                "hostCeiling"
            ]
            assert (
                verified["enforcementStatus"] == "configured"
                and verified["matchesRecommended"]
            )
            assert verified["behaviorAccepted"] is False
            services["runtime"]["binary"]["sha256"] = "c" * 64
            assert (
                client.get(path).json()["sections"]["tools"]["data"]["hostCeiling"][
                    "enforcementStatus"
                ]
                == "unknown"
            )


def test_environment_inspection_discards_other_values_and_bounds_input():
    import io

    from leam_api.host_tool_ceiling import read_environment

    with patch(
        "builtins.open",
        return_value=io.BytesIO(
            b'SECRET=never-show-this\0IRONCLAW_REBORN_ALLOWED_CAPABILITY_IDS=["builtin.time"]\0'
        ),
    ):
        actual = read_environment(123)
    assert actual == {"available": True, "configured": True, "ids": ["builtin.time"]}
    assert "never-show-this" not in json.dumps(actual)
    for raw in [
        b"IRONCLAW_REBORN_ALLOWED_CAPABILITY_IDS={",
        b'IRONCLAW_REBORN_ALLOWED_CAPABILITY_IDS=["a.b","a.b"]',
        b"x" * (2 * 1024 * 1024 + 1),
    ]:
        with patch("builtins.open", return_value=io.BytesIO(raw)):
            assert read_environment(123)["available"] is False


def test_loop_helper_catalog_drift_refuses_profile_before_writes(tmp_path):
    with patch("leam_api.system_inspection.SystemInspector._services", return_value={}):
        app, state = setup(tmp_path)
        extra = deepcopy(state["template"]["entries"][1])
        extra["key"] = "tool.builtin.result_read"
        extra["value"]["name"] = "builtin.result_read"
        state["template"]["entries"].append(extra)
        with TestClient(app) as client:
            login(client)
            path = "/api/companion/tools/profile"
            preview = client.get(path).json()
            assert preview["unexpectedHelperEntries"] == ["builtin.result_read"]
            response = client.post(
                path + "/apply",
                headers={"origin": "http://testserver"},
                json={
                    "requestId": str(uuid4()),
                    "confirmed": True,
                    "previewToken": preview["previewToken"],
                },
            )
            assert response.status_code == 409
            assert state["writes"] == []
            assert not list((tmp_path / "permission-snapshots").glob("*.json"))


@pytest.mark.parametrize("changed_read", [2, 3])
def test_restore_rechecks_modes_on_every_prewrite_read(tmp_path, changed_read):
    with patch("leam_api.system_inspection.SystemInspector._services", return_value={}):
        app, state = setup(tmp_path)
        with TestClient(app) as client:
            login(client)
            path = "/api/companion/tools/profile"
            headers = {"origin": "http://testserver"}
            backup_id = str(uuid4())
            applied = client.post(
                path + "/apply",
                headers=headers,
                json={
                    "requestId": backup_id,
                    "confirmed": True,
                    "previewToken": client.get(path).json()["previewToken"],
                },
            )
            assert applied.json()["state"] == "complete"
            preview = client.get(path + "/restore/" + backup_id).json()
            state["writes"].clear()
            state["mutate_at"] = state["reads"] + changed_read
            response = client.post(
                path + "/restore",
                headers=headers,
                json={
                    "requestId": str(uuid4()),
                    "confirmed": True,
                    "backupId": backup_id,
                    "previewToken": preview["previewToken"],
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["state"] == "needs_review"
            assert state["writes"] == []
