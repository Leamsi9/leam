"""Actual operator CLI and lifecycle callers for attested worker refill."""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from test_backlog import review

from leam_api.backlog import Assessment, Backlog, UserOrder
from leam_api.backlog_assignment import Assignment, transition
from leam_api.backlog_edit import Delete, acknowledge_delete, mutate
from leam_api.backlog_refill import Observation, Staffing
from leam_api.store import Store
from leam_api.updates import QA, Publication, Updates


def add(backlog, name, **changes):
    return backlog.upsert(
        Assessment(
            feature=name, title=name, currentStep="Fixture work", percent=10, **changes
        )
    )


def observation(service, workers=(), **changes):
    return Observation.model_validate(
        {
            "requestId": str(uuid4()),
            "snapshotHash": service.snapshot()["snapshotHash"],
            "observedAt": datetime.fromtimestamp(service.backlog.clock(), UTC),
            "source": "collaboration.list_agents",
            "coordinator": {
                "agentId": "root",
                "state": "running",
                "activity": "Integrating deployed work",
            },
            "workers": list(workers),
            **changes,
        }
    )


def running(feature, worker=None, **changes):
    worker = worker or feature
    return {
        "feature": feature,
        "worker": worker,
        "agentId": "agent-" + worker,
        "state": "running",
        **changes,
    }


def cli(path, *args):
    return subprocess.run(
        [sys.executable, "-m", "leam_api.backlog", "--data-dir", str(path), *args],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_cli_rejects_shortfall_returns_canonical_priority_then_accepts_four_real_observations(
    tmp_path,
):
    backlog = Backlog(Store(tmp_path))
    for name in ("a", "b", "c"):
        add(backlog, name)
    current = backlog.list()
    backlog.reorder(
        UserOrder(
            requestId=uuid4(),
            revision=current["ordering"]["revision"],
            expectedRevisions={
                item["feature"]: item["revision"] for item in current["items"]
            },
            lane="all",
            features=["c", "a", "b"],
        )
    )
    service = Staffing(backlog)
    path = tmp_path / "observation.json"
    path.write_text(observation(service).model_dump_json())
    failed = cli(tmp_path, "staffing-check", "--input", str(path))
    assert failed.returncode == 2, failed.stderr
    result = json.loads(failed.stdout)
    assert [item["feature"] for item in result["nextWork"]] == ["c", "a", "b"]
    assert result["activeWorkstreams"] == 1 and not result["externalLivenessProven"]
    for row in backlog.list()["items"]:
        transition(
            backlog,
            Assignment(
                requestId=uuid4(),
                feature=row["feature"],
                worker=row["feature"],
                expectedRevision=row["revision"],
                operation="start",
                note="Start fixture worker",
            ),
        )
    backlog.reconcile(review(backlog))
    path.write_text(
        observation(
            service, [running(name) for name in ("a", "b", "c")]
        ).model_dump_json()
    )
    passed = cli(tmp_path, "staffing-check", "--input", str(path))
    assert passed.returncode == 0, passed.stderr
    assert json.loads(passed.stdout)["activeWorkstreams"] == 4
    assert cli(tmp_path, "status", "--require-current").returncode == 0
    assert backlog.list()["ordering"]["features"] == ["c", "a", "b"]


@pytest.mark.parametrize(
    "state", ["interrupted", "cancelled", "idle", "completed", "failed"]
)
def test_reservation_never_counts_as_live_when_worker_stopped(tmp_path, state):
    backlog = Backlog(Store(tmp_path))
    add(backlog, "work", deliveryState="in_progress", worker="worker", owner="main")
    service = Staffing(backlog)
    result = service.check(
        observation(service, [running("work", "worker", state=state)])
    )
    assert not result["passed"] and result["activeWorkstreams"] == 1
    assert any(item.get("observedState") == state for item in result["problems"])
    resumed = service.check(observation(service, [running("work", "worker")]))
    assert resumed["passed"] and resumed["activeWorkstreams"] == 2


def test_publication_invalidates_receipt_but_keeps_same_active_assignment_and_allows_rightful_handoff(
    tmp_path,
):
    backlog = Backlog(Store(tmp_path))
    row = add(
        backlog, "work", deliveryState="in_progress", worker="worker", owner="main"
    )
    service = Staffing(backlog)
    assert service.check(observation(service, [running("work", "worker")]))["passed"]
    Updates(backlog.store).publish(
        Publication(
            feature="work",
            title="Work",
            summary="Deployed",
            deploymentId="fixture",
            deployedAt=datetime.now(UTC),
        )
    )
    assert service.status()["checkDue"]
    assert backlog.list()["items"] == []
    assert backlog.status()["workload"]["recordedAssignments"] == {"worker": ["work"]}
    assert len(service.snapshot()["assignments"]) == 1
    body = Assignment(
        requestId=uuid4(),
        feature="work",
        worker="worker",
        expectedRevision=row["revision"],
        operation="handoff",
        note="Deployed, now handing off QA",
    )
    result = transition(backlog, body)
    assert transition(backlog, body) == result
    assert result["state"] == "handover"
    assert service.check(observation(service, [running("work", "worker", phase="qa")]))[
        "passed"
    ]
    with pytest.raises(ValueError, match="Worker/owner"):
        transition(
            backlog,
            body.model_copy(
                update={
                    "requestId": uuid4(),
                    "expectedRevision": result["revision"],
                    "worker": "intruder",
                    "operation": "blocked",
                }
            ),
        )
    blocked = transition(
        backlog,
        body.model_copy(
            update={
                "requestId": uuid4(),
                "expectedRevision": result["revision"],
                "operation": "blocked",
            }
        ),
    )
    assert blocked["state"] == "blocked" and service.status()["checkDue"]
    with pytest.raises(ValueError, match="Deployed"):
        transition(
            backlog,
            body.model_copy(
                update={
                    "requestId": uuid4(),
                    "expectedRevision": blocked["revision"],
                    "operation": "start",
                }
            ),
        )


def test_observation_age_snapshot_generation_and_rank_changes_invalidate(tmp_path):
    backlog = Backlog(Store(tmp_path))
    service = Staffing(backlog)
    observed = observation(service)
    assert service.check(observed)["passed"]
    assert service.status()["current"]
    service.invalidate("User interrupted the active turn; inspect all workers")
    assert service.status()["checkDue"]
    with pytest.raises(ValueError, match="Canonical"):
        service.check(observed)
    for offset in (-121, 10):
        with pytest.raises(ValueError, match="stale or in the future"):
            service.check(
                observation(
                    service, observedAt=datetime.now(UTC) + timedelta(seconds=offset)
                )
            )
    assert service.check(observation(service))["passed"]
    now = backlog.clock()
    backlog.clock = lambda: now + 121
    assert service.status()["checkDue"]


def test_no_eligible_work_and_explicit_capacity_exception_are_distinct(tmp_path):
    backlog = Backlog(Store(tmp_path))
    add(backlog, "blocked", blockers=["Awaiting account access"])
    add(backlog, "dependent", dependencies=["missing"])
    service = Staffing(backlog)
    empty = service.check(observation(service))
    assert empty["passed"] and empty["underCapacityReason"] == "no eligible queued work"
    assert service.snapshot()["blockedCount"] == 2
    add(backlog, "eligible")
    assert not service.check(observation(service))["passed"]
    with pytest.raises(ValueError, match="capacity requires"):
        observation(service, capacity=1)
    limited = service.check(
        observation(
            service,
            capacity=1,
            capacityReason="Only the coordinator slot is available in this fixture",
        )
    )
    assert (
        limited["passed"]
        and limited["underCapacityReason"] == "explicit capacity exception"
    )
    assert limited["eligibleCount"] == 1


def test_duplicate_agents_and_unregistered_running_work_cannot_satisfy_gate(tmp_path):
    backlog = Backlog(Store(tmp_path))
    service = Staffing(backlog)
    with pytest.raises(ValueError, match="real agent"):
        observation(service, [running("one", agentId="root")])
    with pytest.raises(ValueError, match="Duplicate worker"):
        observation(
            service, [running("one", "same"), running("two", "same", agentId="other")]
        )
    result = service.check(observation(service, [running("unknown")]))
    assert not result["passed"] and result["activeWorkstreams"] == 1
    assert result["occupiedSlots"] == 2


def test_deleted_active_task_requires_acknowledged_cancellation(tmp_path):
    backlog = Backlog(Store(tmp_path))
    row = add(
        backlog, "work", deliveryState="in_progress", worker="worker", owner="main"
    )
    service = Staffing(backlog)
    assert service.check(observation(service, [running("work", "worker")]))["passed"]
    deleted = mutate(
        backlog,
        "work",
        "delete",
        Delete(requestId=uuid4(), revision=row["revision"], confirmActive=True),
    )
    result = service.check(
        observation(service, [running("work", "worker", state="cancelled")])
    )
    assert not result["passed"]
    assert any("cancellation" in item["reason"] for item in result["problems"])
    acknowledge_delete(backlog, "work", deleted["revision"])
    assert service.check(observation(service))["passed"]


def test_dependency_qa_and_lifecycle_handoff_invalidate_receipts(tmp_path):
    backlog = Backlog(Store(tmp_path))
    updates = Updates(backlog.store)
    deployed = updates.publish(
        Publication(
            feature="dep",
            title="Dependency",
            summary="Deployed",
            deploymentId="fixture",
            deployedAt=datetime.now(UTC),
        )
    )
    add(backlog, "waiting", dependencies=["dep"])
    service = Staffing(backlog)
    assert service.snapshot()["eligibleCount"] == 1
    limited = observation(
        service, capacity=1, capacityReason="No external worker slots in this fixture"
    )
    assert service.check(limited)["passed"]
    updates.qa(
        deployed["id"],
        QA(deploymentId="fixture", state="failed", details="Synthetic failure"),
    )
    assert service.status()["checkDue"]
    assert service.snapshot()["eligibleCount"] == 0


def test_cli_invalidation_and_missing_database_do_not_launch_or_bootstrap(tmp_path):
    missing = tmp_path / "missing"
    assert cli(missing, "staffing-snapshot").returncode != 0
    assert not missing.exists()
    backlog = Backlog(Store(tmp_path / "existing"))
    service = Staffing(backlog)
    assert service.check(observation(service))["passed"]
    result = cli(
        backlog.store.path.parent,
        "staffing-invalidate",
        "--reason",
        "Observed interrupted workers",
    )
    assert result.returncode == 0 and json.loads(result.stdout)["checkDue"]
    assert service.status()["checkDue"]
