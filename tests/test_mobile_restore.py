from test_maintenance import AllowedDrain
import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from test_backups import prepared

from leam_api.backups import Backups
from leam_api.candidate_deployment import CandidateDeployment, Roots
from leam_api.local_tls import provision
from leam_api.mobile_restore import RestoreController
from leam_api.store import Store


def fixture(tmp_path):
    roots = Roots(tmp_path / "installation")
    for directory in (
        roots.base,
        roots.data,
        roots.releases,
        roots.venvs,
        roots.recovery,
    ):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = roots.data / "original"
    store = prepared(data)
    provision(data)
    store.create("memory", {"text": "State A", "source": "fixture"})
    source = "a" * 40
    release = roots.releases / source
    (release / "apps/web/dist").mkdir(parents=True)
    release.chmod(0o755)
    (release / "apps/web/dist/index.html").write_text("fixture public shell")
    requirements = "b" * 64
    environment = roots.venvs / requirements
    (environment / "bin").mkdir(parents=True)
    environment.chmod(0o755)
    (environment / "bin/python").write_text("fixture executable")
    (environment / "bin/python").chmod(0o700)
    manifest = {
        "schemaVersion": 1,
        "sourceCommit": source,
        "clientIndexSha256": hashlib.sha256(b"fixture public shell").hexdigest(),
        "requirementsSha256": requirements,
        "installedPackagesSha256": requirements,
        "pythonEnvironment": str(environment),
    }
    (release / "release.json").write_text(json.dumps(manifest))
    runtime = {
        "sourceCommit": "c" * 40,
        "binarySha256": "d" * 64,
        "ceilingSha256": "e" * 64,
    }
    deployment = CandidateDeployment(roots)
    deployment.adopt(
        data,
        release,
        runtime,
        {
            "LEAM_ORIGINS": "http://testserver",
            "LEAM_CODING_WORKSPACE": "/home/fixture/Github/leam",
        },
    )
    return roots, store, deployment, runtime


class Services:
    def __init__(self, runtime):
        self.runtime, self.calls, self.fail = runtime, [], None

    async def identity(self):
        return self.runtime

    async def stop(self):
        self.calls.append("stop")
        if self.fail == "stop":
            raise ValueError("private diagnostic must not escape")

    async def start(self):
        self.calls.append("start")

    async def healthy(self):
        return self.fail != "health"


def test_real_restore_and_rollback_preserve_current_state(tmp_path):
    async def scenario():
        roots, store, deployment, runtime = fixture(tmp_path)
        backup = Backups(store).create()
        b = store.create("memory", {"text": "State B", "source": "fixture"})
        services = Services(runtime)
        controller = RestoreController(deployment, services)
        preview = await controller.preview(backup["id"])
        request = str(uuid4())
        receipt = await controller.start_restore(
            request, backup["id"], preview["previewToken"]
        )
        await controller.wait(request)
        receipt = controller.status(request)
        assert receipt["state"] == "ready_for_uat"
        active = deployment.read()
        assert active["dataDirectory"] != str(store.path.parent)
        restored = Store(Path(active["dataDirectory"]))
        assert [m["text"] for m in restored.entities("memory")] == ["State A"]
        assert {m["text"] for m in store.entities("memory")} == {"State A", "State B"}
        assert (
            store.path.parent / "backups" / (receipt["safetyBackupId"] + ".zip")
        ).is_file()
        with restored.connect() as db:
            assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        count = len(services.calls)
        replay = await controller.start_restore(
            request, backup["id"], preview["previewToken"]
        )
        assert replay == receipt and len(services.calls) == count
        rollback = await controller.rollback_preview(request)
        rollback_id = str(uuid4())
        await controller.start_rollback(rollback_id, request, rollback["previewToken"])
        await controller.wait(rollback_id)
        assert controller.status(rollback_id)["state"] == "rolled_back"
        assert deployment.read()["dataDirectory"] == str(store.path.parent)
        assert any(
            m["id"] == b["id"] for m in Store(store.path.parent).entities("memory")
        )
        assert Path(active["dataDirectory"]).exists()
        assert services.calls == ["stop", "start", "stop", "start"]

    asyncio.run(scenario())


def test_launch_plan_uses_one_descriptor_and_fixed_ports(tmp_path):
    _, _, deployment, _ = fixture(tmp_path)
    app = deployment.launch_plan("app")
    mcp = deployment.launch_plan("mcp")
    assert app["environment"]["LEAM_DATA_DIR"] == mcp["environment"]["LEAM_DATA_DIR"]
    assert app["cwd"] == mcp["cwd"] == deployment.read()["releaseDirectory"]
    assert app["environment"]["LEAM_CODING_WORKSPACE"] == "/home/fixture/Github/leam"
    assert app["argv"][-2:] == ["--timeout-graceful-shutdown", "10"]
    assert "46400" in app["argv"] and "46420" in mcp["argv"]
    assert "--no-proxy-headers" in app["argv"] and "--no-proxy-headers" in mcp["argv"]
    assert (
        str(Path(app["environment"]["LEAM_DATA_DIR"]) / "mcp-tls/key.pem")
        in mcp["argv"]
    )
    with pytest.raises(ValueError):
        deployment.launch_plan("arbitrary.service")


def test_stale_preview_runtime_drift_and_two_request_lock_do_not_write(tmp_path):
    async def scenario():
        roots, store, deployment, runtime = fixture(tmp_path)
        backup = Backups(store).create()
        services = Services(runtime)
        controller = RestoreController(deployment, services)
        preview = await controller.preview(backup["id"])
        before = deployment.read()
        with deployment.lock():
            deployment.replace_locked(before, before)
        with pytest.raises(ValueError, match="Preview changed"):
            await controller.start_restore(
                str(uuid4()), backup["id"], preview["previewToken"]
            )
        assert not services.calls and not list(roots.data.glob("restored-*"))
        services.runtime = {**runtime, "binarySha256": "f" * 64}
        with pytest.raises(ValueError, match="runtime"):
            await controller.preview(backup["id"])
        services.runtime = runtime
        preview = await controller.preview(backup["id"])
        started, proceed = asyncio.Event(), asyncio.Event()
        original = services.stop

        async def stop():
            started.set()
            await proceed.wait()
            await original()

        services.stop = stop
        first = str(uuid4())
        await controller.start_restore(first, backup["id"], preview["previewToken"])
        await started.wait()
        with pytest.raises(ValueError, match="in progress"):
            await controller.start_restore(
                str(uuid4()), backup["id"], preview["previewToken"]
            )
        with pytest.raises(ValueError, match="in progress"):
            with deployment.lock():
                pass
        proceed.set()
        await controller.wait(first)
        assert controller.status(first)["state"] == "ready_for_uat"

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["stop", "health"])
def test_partial_failure_is_durable_non_replay_and_can_explicitly_rollback(
    tmp_path, failure
):
    async def scenario():
        roots, store, deployment, runtime = fixture(tmp_path)
        backup = Backups(store).create()
        services = Services(runtime)
        services.fail = failure
        controller = RestoreController(deployment, services)
        preview = await controller.preview(backup["id"])
        request = str(uuid4())
        await controller.start_restore(request, backup["id"], preview["previewToken"])
        await controller.wait(request)
        result = controller.status(request)
        assert result[
            "state"
        ] == "needs_review" and "private diagnostic" not in json.dumps(result)
        calls = list(services.calls)
        restarted = RestoreController(deployment, services)
        assert (
            await restarted.start_restore(
                request, backup["id"], preview["previewToken"]
            )
            == result
        )
        assert services.calls == calls
        services.fail = None
        undo = await restarted.rollback_preview(request)
        undo_id = str(uuid4())
        await restarted.start_rollback(undo_id, request, undo["previewToken"])
        await restarted.wait(undo_id)
        assert restarted.status(undo_id)["state"] == "rolled_back"
        assert deployment.read()["dataDirectory"] == str(store.path.parent)

    asyncio.run(scenario())


def test_recovery_restart_marks_reserved_receipt_uncertain_without_actions(tmp_path):
    from leam_api.candidate_deployment import atomic_json

    roots, _, deployment, runtime = fixture(tmp_path)
    services = Services(runtime)
    controller = RestoreController(deployment, services)
    request = str(uuid4())
    atomic_json(
        controller._path(request),
        {
            "requestId": request,
            "operation": "restore",
            "state": "switching",
            "createdAt": 1,
            "beforeDescriptor": deployment.read(),
        },
    )
    restarted = RestoreController(deployment, services)
    assert restarted.status(request)["state"] == "needs_review"
    assert services.calls == []


def test_descriptor_rejects_unknown_fields_paths_and_launch_environment(tmp_path):
    from leam_api.candidate_deployment import atomic_json, digest

    roots, _, deployment, _ = fixture(tmp_path)
    original = deployment.read()
    for bad in [
        dict(original, arbitrary="no"),
        dict(original, dataDirectory=str(tmp_path)),
        dict(
            original,
            environment={"PYTHONPATH": "/bad"},
            environmentSha256=digest({"PYTHONPATH": "/bad"}),
        ),
    ]:
        atomic_json(deployment.path, bad)
        with pytest.raises(ValueError):
            deployment.launch_plan("app")
    atomic_json(deployment.path, original)
    external = roots.data / "linked"
    external.symlink_to(Path(original["dataDirectory"]), target_is_directory=True)
    atomic_json(deployment.path, dict(original, dataDirectory=str(external)))
    with pytest.raises(ValueError):
        deployment.read()


def test_backup_different_installation_identity_refuses_before_service_operations(
    tmp_path,
):
    import zipfile
    from test_backups import rewrite

    async def scenario():
        _, store, deployment, runtime = fixture(tmp_path)
        manager = Backups(store)
        backup = manager.create()
        source = manager.path(backup["id"])

        def change(files):
            files["tools-token"] = b"y" * 48
            manifest = json.loads(files["manifest.json"])
            manifest["files"]["tools-token"] = {
                "size": 48,
                "sha256": hashlib.sha256(files["tools-token"]).hexdigest(),
            }
            files["manifest.json"] = json.dumps(manifest).encode()

        target = source.with_suffix(".new")
        rewrite(source, target, change)
        target.chmod(0o600)
        os.replace(target, source)
        services = Services(runtime)
        controller = RestoreController(deployment, services)
        with pytest.raises(ValueError, match="different MCP"):
            await controller.preview(backup["id"])
        assert services.calls == []

    asyncio.run(scenario())


def test_fixed_service_caller_stops_only_candidate_and_rejects_remaining_writer(
    tmp_path,
):
    from leam_api.restore_services import FixedRestoreServices
    from leam_api.recovery import SERVICES

    class Control:
        def __init__(self):
            self.calls, self.pid = [], "0"

        async def _systemctl(self, *args):
            self.calls.append(args)
            return f"MainPID={self.pid}\nActiveState=inactive\n"

        async def status(self):
            return [{"reachable": False} for _ in range(3)]

    async def scenario():
        _, _, deployment, _ = fixture(tmp_path)
        control = Control()
        services = FixedRestoreServices(deployment, control, drain=AllowedDrain())
        await services.stop()
        await services.start()
        assert control.calls[0] == (
            "stop",
            *(SERVICES[k][1] for k in ("app", "mcp", "runtime")),
        )
        assert [call for call in control.calls if call[0] == "start"] == [
            ("start", SERVICES[k][1]) for k in ("app", "mcp", "runtime")
        ]
        control.pid = "42"
        with pytest.raises(ValueError, match="writer"):
            await services.stop()

    asyncio.run(scenario())


def test_actual_launcher_caller_uses_validated_descriptor_and_discards_stale_overrides(
    tmp_path, monkeypatch
):
    import sys
    from leam_api import candidate_launch

    _, _, deployment, _ = fixture(tmp_path)
    captured = {}
    monkeypatch.setattr(candidate_launch, "CandidateDeployment", lambda: deployment)
    monkeypatch.setattr(sys, "argv", ["candidate_launch", "mcp"])
    monkeypatch.setenv("LEAM_DATA_DIR", "/stale-state")
    monkeypatch.setenv("LEAM_UNKNOWN", "must-not-reach-app")
    monkeypatch.setattr(os, "chdir", lambda cwd: captured.update(cwd=cwd))
    monkeypatch.setattr(
        os,
        "execve",
        lambda exe, argv, env: captured.update(exe=exe, argv=argv, env=env),
    )
    candidate_launch.main()
    assert captured["env"]["LEAM_DATA_DIR"] == deployment.read()["dataDirectory"]
    assert captured["env"]["LEAM_CODING_WORKSPACE"] == "/home/fixture/Github/leam"
    assert "LEAM_UNKNOWN" not in captured["env"]
    assert captured["argv"][0] == captured["exe"]
    assert captured["cwd"] == deployment.read()["releaseDirectory"]


def test_normal_deployment_requires_lock_and_exact_generation_and_blocks_uncertainty(
    tmp_path,
):
    from leam_api.candidate_deployment import atomic_json

    _, _, deployment, runtime = fixture(tmp_path)
    original = deployment.read()
    with pytest.raises(ValueError, match="lock"):
        deployment.replace_locked(original, original)
    with deployment.lock():
        candidate = deployment.prepare_release_locked(
            Path(original["releaseDirectory"]), runtime, original
        )
        assert candidate == original
        replacement = deployment.replace_locked(candidate, original)
        with pytest.raises(ValueError, match="changed"):
            deployment.prepare_release_locked(
                Path(original["releaseDirectory"]), runtime, original
            )
    controller = RestoreController(deployment, Services(runtime))
    request = str(uuid4())
    atomic_json(
        controller._path(request), {"requestId": request, "state": "needs_review"}
    )
    with deployment.lock():
        with pytest.raises(ValueError, match="unfinished"):
            deployment.prepare_release_locked(
                Path(original["releaseDirectory"]), runtime, replacement
            )


def test_cancelled_filesystem_worker_keeps_lock_until_worker_stops(
    tmp_path, monkeypatch
):
    import threading
    import leam_api.mobile_restore as module

    async def scenario():
        _, store, deployment, runtime = fixture(tmp_path)
        backup = Backups(store).create()
        services = Services(runtime)
        controller = RestoreController(deployment, services)
        preview = await controller.preview(backup["id"])
        started, proceed = threading.Event(), threading.Event()
        original = module.restore

        def paused(*args):
            started.set()
            proceed.wait(5)
            return original(*args)

        monkeypatch.setattr(module, "restore", paused)
        request = str(uuid4())
        await controller.start_restore(request, backup["id"], preview["previewToken"])
        await asyncio.to_thread(started.wait, 5)
        task = controller.tasks[request]
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        with pytest.raises(ValueError, match="in progress"):
            with deployment.lock():
                pass
        proceed.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        with deployment.lock():
            pass
        assert controller.status(request)["state"] == "needs_review"
        assert deployment.read()["dataDirectory"] == str(store.path.parent)

    asyncio.run(scenario())


def test_recovery_authenticated_api_performs_restore_and_rollback_without_main_app(
    tmp_path,
):
    from fastapi.testclient import TestClient
    from leam_api.recovery import create_recovery_app
    from test_recovery import setup as pair
    from test_recovery import Control

    roots, store, deployment, runtime = fixture(tmp_path)
    backup = Backups(store).create()
    store.create("memory", {"text": "State B", "source": "fixture"})
    controller = RestoreController(deployment, Services(runtime))
    app = create_recovery_app(
        roots.recovery, {"http://testserver"}, control=Control(), restore=controller
    )
    headers = {"origin": "http://testserver"}
    with TestClient(app, client=("127.0.0.1", 4444)) as client:
        assert client.get("/api/restores").status_code == 401
        pair(client, roots.recovery)
        assert client.get("/api/restores").json()["backups"][0]["id"] == backup["id"]
        body = {"backupId": backup["id"]}
        assert client.post("/api/restores/preview", json=body).status_code == 403
        preview = client.post(
            "/api/restores/preview", json=body, headers=headers
        ).json()
        request = str(uuid4())
        result = client.post(
            "/api/restores",
            headers=headers,
            json={
                **body,
                "requestId": request,
                "previewToken": preview["previewToken"],
                "confirmed": True,
            },
        )
        assert result.status_code == 202, result.text
        client.portal.call(controller.wait, request)
        assert client.get("/api/restores/" + request).json()["state"] == "ready_for_uat"
        rollback = client.post(
            "/api/restores/" + request + "/rollback-preview", json={}, headers=headers
        ).json()
        undo = str(uuid4())
        result = client.post(
            "/api/restores/" + request + "/rollback",
            headers=headers,
            json={
                "requestId": undo,
                "previewToken": rollback["previewToken"],
                "confirmed": True,
            },
        )
        assert result.status_code == 202, result.text
        client.portal.call(controller.wait, undo)
        assert client.get("/api/restores/" + undo).json()["state"] == "rolled_back"
        assert deployment.read()["dataDirectory"] == str(store.path.parent)
        assert (
            client.post(
                "/api/restores/preview", headers=headers, json={"backupId": "../../bad"}
            ).status_code
            == 422
        )
        assert client.get("/restore.js").status_code == 200


def test_failed_rollback_after_pointer_switch_can_be_explicitly_retried(tmp_path):
    async def scenario():
        _, store, deployment, runtime = fixture(tmp_path)
        backup = Backups(store).create()
        services = Services(runtime)
        controller = RestoreController(deployment, services)
        preview = await controller.preview(backup["id"])
        request = str(uuid4())
        await controller.start_restore(request, backup["id"], preview["previewToken"])
        await controller.wait(request)
        services.fail = "health"
        first_preview = await controller.rollback_preview(request)
        first = str(uuid4())
        await controller.start_rollback(first, request, first_preview["previewToken"])
        await controller.wait(first)
        assert controller.status(first)["state"] == "needs_review"
        assert deployment.read()["dataDirectory"] == str(store.path.parent)
        second_preview = await controller.rollback_preview(request)
        assert second_preview["previewToken"] != first_preview["previewToken"]
        services.fail = None
        second = str(uuid4())
        await controller.start_rollback(second, request, second_preview["previewToken"])
        await controller.wait(second)
        assert controller.status(second)["state"] == "rolled_back"
        assert controller.status(first)["state"] == "rolled_back"
        assert controller.status(request)["state"] == "rolled_back"

    asyncio.run(scenario())


def test_recovery_other_browser_cannot_restart_writers_during_restore(tmp_path):
    from fastapi.testclient import TestClient
    from leam_api.recovery import create_recovery_app
    from test_recovery import setup as pair, Control

    roots, store, deployment, runtime = fixture(tmp_path)
    backup = Backups(store).create()
    entered = asyncio.Event()
    release = asyncio.Event()

    class HeldServices(Services):
        async def stop(self):
            entered.set()
            await release.wait()
            await super().stop()

    controller = RestoreController(deployment, HeldServices(runtime))
    control = Control()
    app = create_recovery_app(
        roots.recovery,
        {"http://testserver"},
        control=control,
        restore=controller,
        drain=AllowedDrain(),
    )
    headers = {"origin": "http://testserver"}
    with TestClient(app, client=("127.0.0.1", 4444)) as client:
        pair(client, roots.recovery)
        preview = client.post(
            "/api/restores/preview", json={"backupId": backup["id"]}, headers=headers
        ).json()
        request = str(uuid4())
        assert (
            client.post(
                "/api/restores",
                json={
                    "requestId": request,
                    "backupId": backup["id"],
                    "previewToken": preview["previewToken"],
                    "confirmed": True,
                },
                headers=headers,
            ).status_code
            == 202
        )
        client.portal.call(entered.wait)
        # A separate browser has its own authenticated cookie, sharing only server state.
        first_cookie = client.cookies.get("leam_recovery_session")
        client.cookies.clear()
        assert (
            client.post(
                "/api/auth/login",
                json={"password": "separate-recovery-password"},
                headers=headers,
            ).status_code
            == 200
        )
        assert client.cookies.get("leam_recovery_session") != first_cookie
        assert (
            client.post(
                "/api/services/app/restart", json={}, headers=headers
            ).status_code
            == 409
        )
        assert control.calls == []
        client.portal.call(release.set)
        client.portal.call(controller.wait, request)
        assert (
            client.post(
                "/api/services/app/restart", json={}, headers=headers
            ).status_code
            == 200
        )
        assert control.calls == [("app", "restart")]


def test_recovery_service_lock_survives_unavailable_restore_controller(tmp_path):
    from fastapi.testclient import TestClient
    from leam_api.recovery import create_recovery_app
    from test_recovery import setup as pair, Control

    roots, _, deployment, _ = fixture(tmp_path)
    control = Control()
    app = create_recovery_app(
        roots.recovery, {"http://testserver"}, control=control, deployment=deployment
    )
    with TestClient(app, client=("127.0.0.1", 4444)) as client:
        pair(client, roots.recovery)
        assert client.get("/api/restores").json()["available"] is False
        with deployment.lock():
            assert (
                client.post(
                    "/api/services/app/start",
                    json={},
                    headers={"origin": "http://testserver"},
                ).status_code
                == 409
            )
        assert control.calls == []


def test_restore_and_rollback_hold_automations_before_any_service_start(tmp_path):
    async def scenario():
        _, store, deployment, runtime = fixture(tmp_path)
        backup = Backups(store).create()
        observed = []

        class InspectBeforeStart(Services):
            async def start(self):
                value = deployment.read()
                marker = Store(Path(value["dataDirectory"])).get(
                    "restore_automation_hold"
                )
                assert marker["version"] == 1 and marker["state"] == "held"
                assert marker["cutoff"] == marker["restoredAt"]
                observed.append(marker["requestId"])
                await super().start()

        controller = RestoreController(deployment, InspectBeforeStart(runtime))
        preview = await controller.preview(backup["id"])
        request = str(uuid4())
        await controller.start_restore(request, backup["id"], preview["previewToken"])
        await controller.wait(request)
        assert controller.status(request)["automationHeld"] is True
        rollback = await controller.rollback_preview(request)
        undo = str(uuid4())
        await controller.start_rollback(undo, request, rollback["previewToken"])
        await controller.wait(undo)
        assert controller.status(undo)["automationHeld"] is True
        assert observed == [request, undo]
        assert store.get("restore_automation_hold")["requestId"] == undo

    asyncio.run(scenario())
