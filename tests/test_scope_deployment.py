"""Scope opt-in survives real candidate launch/rollout and rejects invalid values."""

import asyncio
import sys

import pytest
from test_candidate_rollout import Services, target
from test_mobile_restore import fixture

from leam_api import candidate_launch
from leam_api.candidate_deployment import digest
from leam_api.candidate_rollout import rollout


def configured(deployment, value):
    with deployment.lock():
        before = deployment.read()
        environment = {**before["environment"], "LEAM_RUNTIME_SCOPE_ENABLED": value}
        return deployment.replace_locked(
            {
                **before,
                "environment": environment,
                "environmentSha256": digest(environment),
            },
            before,
        )


@pytest.mark.parametrize("role", ["app", "mcp"])
def test_launcher_uses_descriptor_scope_not_unreviewed_environment(
    tmp_path, monkeypatch, role
):
    _, _, deployment, _ = fixture(tmp_path)
    configured(deployment, "1")
    monkeypatch.setattr(candidate_launch, "CandidateDeployment", lambda: deployment)
    monkeypatch.setattr(sys, "argv", ["candidate_launch", role])
    monkeypatch.setenv("LEAM_RUNTIME_SCOPE_ENABLED", "0")
    monkeypatch.setenv("LEAM_UNREVIEWED_SCOPE", "unsafe")
    monkeypatch.setattr(candidate_launch.os, "chdir", lambda _: None)
    calls = []
    monkeypatch.setattr(candidate_launch.os, "execve", lambda *args: calls.append(args))
    candidate_launch.main()
    assert len(calls) == 1
    assert calls[0][2]["LEAM_RUNTIME_SCOPE_ENABLED"] == "1"
    assert "LEAM_UNREVIEWED_SCOPE" not in calls[0][2]
    assert calls[0][2]["LEAM_DATA_DIR"] == deployment.read()["dataDirectory"]


@pytest.mark.parametrize("value", ["true", "yes", "2", "", 1])
def test_bad_activation_flag_cannot_replace_descriptor(tmp_path, value):
    _, _, deployment, _ = fixture(tmp_path)
    before = deployment.read()
    with pytest.raises(ValueError):
        configured(deployment, value)
    assert deployment.read() == before


def test_rollout_preserves_reviewed_scope_for_both_service_roles(tmp_path):
    async def scenario():
        roots, _, deployment, _ = fixture(tmp_path)
        before = configured(deployment, "1")
        services = Services(deployment)
        result = await rollout(
            deployment, services, target(roots, before), digest(before)
        )
        assert result["state"] == "deployed"
        for role in ("app", "mcp"):
            assert (
                deployment.launch_plan(role)["environment"][
                    "LEAM_RUNTIME_SCOPE_ENABLED"
                ]
                == "1"
            )

    asyncio.run(scenario())
