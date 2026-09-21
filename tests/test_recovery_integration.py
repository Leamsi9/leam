import asyncio
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from test_mobile_restore import Services, fixture
from test_push import subscription

from leam_api import candidate_launch
from leam_api.attachments import AttachmentStore
from leam_api.backups import Backups
from leam_api.candidate_deployment import digest
from leam_api.mobile_restore import RestoreController
from leam_api.push import Device, Push
from leam_api.restore_automation import RestoreAutomation, Resume
from leam_api.routines import Routines
from leam_api.store import Store


def test_actual_launcher_preserves_reviewed_shared_owner_and_workspace(
    tmp_path, monkeypatch
):
    _, _, deployment, _ = fixture(tmp_path)
    before = deployment.read()
    owner = str(uuid4())
    environment = {**before["environment"], "LEAM_SHARED_CODEX_THREAD": owner}
    with deployment.lock():
        deployment.replace_locked(
            {
                **before,
                "environment": environment,
                "environmentSha256": digest(environment),
            },
            before,
        )
    captured = {}
    monkeypatch.setattr(candidate_launch, "CandidateDeployment", lambda: deployment)
    monkeypatch.setattr(sys, "argv", ["candidate_launch", "app"])
    monkeypatch.setenv("LEAM_SHARED_CODEX_THREAD", str(uuid4()))
    monkeypatch.setenv("LEAM_UNREVIEWED_OVERRIDE", "must disappear")
    monkeypatch.setattr(
        candidate_launch.os, "chdir", lambda path: captured.update(cwd=path)
    )
    monkeypatch.setattr(
        candidate_launch.os,
        "execve",
        lambda binary, argv, env: captured.update(binary=binary, argv=argv, env=env),
    )
    candidate_launch.main()
    assert captured["env"]["LEAM_SHARED_CODEX_THREAD"] == owner
    assert captured["env"]["LEAM_CODING_WORKSPACE"] == "/home/fixture/Github/leam"
    assert "LEAM_UNREVIEWED_OVERRIDE" not in captured["env"]
    assert captured["argv"][-2:] == ["--timeout-graceful-shutdown", "10"]
    assert captured["cwd"] == before["releaseDirectory"]


def test_invalid_shared_binding_rejected_before_descriptor_switch(tmp_path):
    _, _, deployment, _ = fixture(tmp_path)
    before = deployment.read()
    environment = {**before["environment"], "LEAM_SHARED_CODEX_THREAD": "not-an-id"}
    with deployment.lock(), pytest.raises(ValueError):
        deployment.replace_locked(
            {
                **before,
                "environment": environment,
                "environmentSha256": digest(environment),
            },
            before,
        )
    assert deployment.read() == before


def test_controller_restore_and_rollback_hold_real_sender_and_preserve_attachments(
    tmp_path,
):
    async def scenario():
        _, store, deployment, runtime = fixture(tmp_path)
        Routines(store)
        files = AttachmentStore(store)
        content = b"Private fixture file preserved through restore"
        attachment = files.add(content, "text/plain", "fixture.txt")
        files.bind([attachment["id"]], "fixture-request")
        calls = []

        def provider(request):
            calls.append(request)
            return httpx.Response(201)

        transport = httpx.MockTransport(provider)
        original = Push(store, transport=transport)
        store.set("push_contact", "mailto:fixture@example.com")
        device = original.subscribe(
            Device(name="Fixture", subscription=subscription()[0])
        )
        original.queue_test(device["id"])
        backup = Backups(store).create()
        await original.tick()
        assert len(calls) == 1
        await original.close()
        services = Services(runtime)
        controller = RestoreController(deployment, services)
        preview = await controller.preview(backup["id"])
        request = str(uuid4())
        await controller.start_restore(request, backup["id"], preview["previewToken"])
        await controller.wait(request)
        assert controller.status(request)["state"] == "ready_for_uat"
        active = Store(Path(deployment.read()["dataDirectory"]))
        restored_files = AttachmentStore(active)
        materialized = restored_files.materialize(
            restored_files.rows([attachment["id"]])[0]
        )
        assert Path(materialized).read_bytes() == content
        restored = Push(active, transport=transport)
        await restored.tick()
        assert len(calls) == 1
        automation = RestoreAutomation(active)
        review = automation.preview()
        assert review["pendingPushes"] == 1
        automation.resume(
            Resume(
                requestId=uuid4(),
                previewToken=review["previewToken"],
                cutoff=review["cutoff"],
                confirmed=True,
            )
        )
        await restored.tick()
        assert len(calls) == 1
        await restored.close()
        rollback = await controller.rollback_preview(request)
        rollback_id = str(uuid4())
        await controller.start_rollback(rollback_id, request, rollback["previewToken"])
        await controller.wait(rollback_id)
        assert controller.status(rollback_id)["state"] == "rolled_back"
        assert deployment.read()["dataDirectory"] == str(store.path.parent)
        assert RestoreAutomation(store).status()["held"] is True
        old_worker = Push(store, transport=transport)
        await old_worker.tick()
        assert len(calls) == 1
        await old_worker.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_mode", ["cancel", "timeout", "shutdown", "invoker"])
def test_restore_retains_lock_until_real_service_process_settles(
    tmp_path, monkeypatch, cancel_mode
):
    from leam_api import recovery
    from leam_api.candidate_deployment import CandidateDeployment

    async def scenario():
        _, store, deployment, runtime = fixture(tmp_path)
        backup = Backups(store).create()
        release = tmp_path / "release-child"
        created = asyncio.Event()
        original_spawn = asyncio.create_subprocess_exec
        children = []

        async def harmless_spawn(*_args, **kwargs):
            child = await original_spawn(
                sys.executable,
                "-c",
                "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]);\nwhile not p.exists(): time.sleep(.01)",
                str(release),
                **kwargs,
            )
            children.append(child)
            created.set()
            return child

        monkeypatch.setattr(recovery.asyncio, "create_subprocess_exec", harmless_spawn)
        if cancel_mode == "timeout":
            monkeypatch.setattr(recovery, "COMMAND_TIMEOUT", 0.02, raising=False)
        services = Services(runtime)

        async def stop():
            await recovery.ServiceControl()._systemctl("stop", "synthetic-unused-unit")

        services.stop = stop
        controller = RestoreController(deployment, services)
        preview = await controller.preview(backup["id"])
        request = str(uuid4())
        await controller.start_restore(request, backup["id"], preview["previewToken"])
        await created.wait()
        task = controller.tasks[request]
        try:
            if cancel_mode == "timeout":
                await asyncio.sleep(0.06)
            elif cancel_mode == "invoker":
                pending = next(
                    p
                    for p in asyncio.all_tasks()
                    if p.get_coro().__qualname__ == "ServiceControl._invoke"
                )
                pending.cancel()
                await asyncio.sleep(0.03)
            elif cancel_mode == "shutdown":
                for pending in asyncio.all_tasks() - {asyncio.current_task()}:
                    pending.cancel()
                await asyncio.sleep(0.03)
            else:
                task.cancel()
                await asyncio.sleep(0.02)
                task.cancel()
                await asyncio.sleep(0.02)
            assert not task.done(), "operation finished before manager client settled"
            with (
                pytest.raises(ValueError, match="in progress"),
                CandidateDeployment(deployment.roots).lock(),
            ):
                pass
        finally:
            release.touch()
            try:
                await task
            except asyncio.CancelledError:
                pass
            for child in children:
                await child.wait()
        assert controller.status(request)["state"] == "needs_review"
        assert services.calls == []  # Never proceed into the next start phase.
        with CandidateDeployment(deployment.roots).lock():
            pass

    asyncio.run(scenario())


def test_cancelled_manual_recovery_action_keeps_cross_process_lock(tmp_path):
    from leam_api.candidate_deployment import CandidateDeployment
    from leam_api.recovery import create_recovery_app

    async def scenario():
        roots, _, deployment, _ = fixture(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()

        class Control:
            async def operate(self, service, action):
                assert (service, action) == ("app", "restart")
                entered.set()
                await release.wait()

            async def status(self):
                return []

        app = create_recovery_app(
            roots.recovery,
            {"http://testserver"},
            control=Control(),
            deployment=deployment,
        )
        route = next(
            route
            for route in app.routes
            if route.path == "/api/services/{service_id}/{action}"
        )
        task = asyncio.create_task(route.endpoint("app", "restart"))
        await entered.wait()
        try:
            task.cancel()
            await asyncio.sleep(0.01)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
            with (
                pytest.raises(ValueError, match="in progress"),
                CandidateDeployment(roots).lock(),
            ):
                pass
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        with CandidateDeployment(roots).lock():
            pass

    asyncio.run(scenario())
