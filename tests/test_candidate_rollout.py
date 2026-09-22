from test_maintenance import AllowedDrain
import asyncio
import json
import shutil

import pytest
from test_mobile_restore import fixture

from leam_api.candidate_deployment import CandidateDeployment, digest
from leam_api.candidate_rollout import rollout


def target(roots, before):
    release = roots.releases / ("f" * 40)
    shutil.copytree(before["releaseDirectory"], release)
    manifest = json.loads((release / "release.json").read_text())
    manifest["sourceCommit"] = release.name
    (release / "release.json").write_text(json.dumps(manifest))
    return release


class Services:
    def __init__(self, deployment):
        self.deployment, self.calls = deployment, []
        self.fail_new = False
        self.started = asyncio.Event()
        self.continue_start = None

    async def identity(self):
        return self.deployment.read()["runtime"]

    def locked(self):
        with (
            pytest.raises(ValueError, match="in progress"),
            CandidateDeployment(self.deployment.roots).lock(),
        ):
            raise AssertionError("operation lock released")

    async def stop(self):
        self.locked()
        self.calls.append(("stop", self.deployment.read()["sourceCommit"]))

    async def start(self):
        self.locked()
        self.calls.append(("start", self.deployment.read()["sourceCommit"]))
        self.started.set()
        if self.continue_start:
            await self.continue_start.wait()

    async def healthy(self):
        self.locked()
        return not (
            self.fail_new and self.deployment.read()["sourceCommit"] == "f" * 40
        )


def test_actual_rollout_switches_both_roles_under_one_lock(tmp_path):
    async def scenario():
        roots, _, deployment, _ = fixture(tmp_path)
        before = deployment.read()
        services = Services(deployment)
        result = await rollout(
            deployment, services, target(roots, before), digest(before)
        )
        assert result["state"] == "deployed"
        after = deployment.read()
        assert after["revision"] == before["revision"] + 1
        assert after["generationId"] == before["generationId"]
        assert after["dataDirectory"] == before["dataDirectory"]
        assert (
            deployment.launch_plan("app")["cwd"]
            == deployment.launch_plan("mcp")["cwd"]
            == after["releaseDirectory"]
        )
        assert services.calls == [("stop", "a" * 40), ("start", "f" * 40)]

    asyncio.run(scenario())


def test_stale_descriptor_and_unresolved_restore_never_stop_services(tmp_path):
    async def scenario():
        roots, _, deployment, _ = fixture(tmp_path)
        before = deployment.read()
        release = target(roots, before)
        services = Services(deployment)
        with pytest.raises(ValueError, match="changed"):
            await rollout(deployment, services, release, "0" * 64)
        directory = roots.recovery / "restore-operations"
        directory.mkdir(mode=0o700)
        row = directory / "active.json"
        row.write_text(json.dumps({"state": "needs_review"}))
        row.chmod(0o600)
        with pytest.raises(ValueError, match="unfinished restore"):
            await rollout(deployment, services, release, digest(before))
        assert not services.calls
        assert deployment.read() == before

    asyncio.run(scenario())


def test_failed_new_release_returns_verified_previous_pointer(tmp_path):
    async def scenario():
        roots, _, deployment, _ = fixture(tmp_path)
        before = deployment.read()
        services = Services(deployment)
        services.fail_new = True
        result = await rollout(
            deployment, services, target(roots, before), digest(before)
        )
        assert result["state"] == "rolled_back"
        assert deployment.read()["sourceCommit"] == before["sourceCommit"]
        assert deployment.read()["revision"] == before["revision"] + 2
        assert services.calls == [
            ("stop", "a" * 40),
            ("start", "f" * 40),
            ("stop", "f" * 40),
            ("start", "a" * 40),
        ]

    asyncio.run(scenario())


def test_repeated_cancellation_does_not_unlock_active_switch(tmp_path):
    async def scenario():
        roots, _, deployment, _ = fixture(tmp_path)
        before = deployment.read()
        services = Services(deployment)
        services.continue_start = asyncio.Event()
        task = asyncio.create_task(
            rollout(deployment, services, target(roots, before), digest(before))
        )
        await services.started.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        services.locked()
        services.continue_start.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert deployment.read()["sourceCommit"] == "f" * 40
        with deployment.lock():
            pass

    asyncio.run(scenario())


def test_post_publication_fsync_failure_rolls_back_observed_pointer(
    tmp_path, monkeypatch
):
    from leam_api import candidate_deployment

    async def scenario():
        roots, _, deployment, _ = fixture(tmp_path)
        before = deployment.read()
        services = Services(deployment)
        original = candidate_deployment.fsync_directory
        calls = []

        def fail_once(path):
            calls.append(path)
            if len(calls) == 1:
                raise OSError("Failed after pointer publication")
            original(path)

        monkeypatch.setattr(candidate_deployment, "fsync_directory", fail_once)
        result = await rollout(
            deployment, services, target(roots, before), digest(before)
        )
        assert result["state"] == "rolled_back"
        assert deployment.read()["sourceCommit"] == before["sourceCommit"]
        assert services.calls == [
            ("stop", "a" * 40),
            ("stop", "f" * 40),
            ("start", "a" * 40),
        ]

    asyncio.run(scenario())


def test_repeated_publication_failure_reports_review_without_claiming_old_pointer(
    tmp_path, monkeypatch
):
    async def scenario():
        roots, _, deployment, _ = fixture(tmp_path)
        before = deployment.read()
        services = Services(deployment)

        def fail(_path):
            raise OSError("Persistent fsync failure")

        monkeypatch.setattr("leam_api.candidate_deployment.fsync_directory", fail)
        with pytest.raises(ValueError, match="rollback require operator review"):
            await rollout(deployment, services, target(roots, before), digest(before))
        assert deployment.read()["sourceCommit"] == before["sourceCommit"]
        assert all(call[0] != "start" for call in services.calls)

    asyncio.run(scenario())


@pytest.mark.parametrize("leftover", [False, True])
def test_fixed_release_services_preserve_runtime_and_recovery(tmp_path, leftover):
    from leam_api.candidate_rollout import ReleaseServices
    from leam_api.recovery import SERVICES as UNITS

    async def scenario():
        _, _, deployment, _ = fixture(tmp_path)

        class Control:
            def __init__(self):
                self.calls = []

            async def _systemctl(self, *arguments):
                self.calls.append(arguments)
                if arguments[0] == "show":
                    return "MainPID=0\nActiveState=inactive\n"
                return ""

            async def status(self):
                return [
                    {
                        "id": name,
                        "reachable": (leftover if name == "app" else name == "runtime"),
                    }
                    for name in UNITS
                ]

        control = Control()
        services = ReleaseServices(deployment, control, drain=AllowedDrain())
        if leftover:
            with pytest.raises(ValueError, match="listener remains"):
                await services.stop()
        else:
            await services.stop()
            await services.start()
        writes = [args for args in control.calls if args[0] in {"stop", "start"}]
        assert writes[0] == ("stop", UNITS["app"][1], UNITS["mcp"][1])
        if not leftover:
            assert writes[1] == ("start", UNITS["app"][1], UNITS["mcp"][1])
        assert all(UNITS["runtime"][1] not in args for args in control.calls)

    asyncio.run(scenario())
