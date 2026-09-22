"""Work assignment CLI caller, exact retries and transaction race coverage."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from leam_api.backlog import Assessment, Backlog
from leam_api.backlog_assignment import Assignment, transition
from leam_api.store import Store
from leam_api.updates import QA, Publication, Updates


def command(
    path,
    feature="task",
    revision=1,
    worker="worker_a",
    operation="start",
    request=None,
    note="Implement bounded slice",
):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "leam_api.backlog",
            "--data-dir",
            str(path),
            operation,
            "--feature",
            feature,
            "--worker",
            worker,
            "--expected-revision",
            str(revision),
            "--request-id",
            str(request or uuid4()),
            "--note",
            note,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def seed(path, **changes):
    backlog = Backlog(Store(path))
    row = backlog.upsert(
        Assessment(
            **{
                "feature": "task",
                "title": "Bounded task",
                "currentStep": "Queued",
                "percent": 35,
                "blockers": [],
                "deliveryState": "ready",
                **changes,
            }
        )
    )
    return backlog, row


def test_cli_requires_registered_task_and_records_start_before_handoff(tmp_path):
    store = Store(tmp_path)
    missing = command(tmp_path)
    assert missing.returncode == 1 and "Register" in missing.stderr
    backlog, row = seed(tmp_path)
    before = backlog.list()["ordering"]
    request = uuid4()
    started = command(tmp_path, revision=row["revision"], request=request)
    assert started.returncode == 0, started.stderr
    receipt = json.loads(started.stdout)
    assert receipt["state"] == "in_progress" and receipt["worker"] == "worker_a"
    item = Backlog(store).list()["items"][0]
    assert (
        item["deliveryLane"] == "in_progress"
        and item["revision"] == receipt["revision"]
    )
    assert item["percent"] == row["percent"]
    assert backlog.list()["ordering"] == before
    assert (
        json.loads(command(tmp_path, revision=row["revision"], request=request).stdout)
        == receipt
    )
    assert (
        command(
            tmp_path, revision=row["revision"], request=request, note="Different scope"
        ).returncode
        == 1
    )
    assert command(tmp_path, revision=row["revision"]).returncode == 1
    handed = command(
        tmp_path,
        revision=receipt["revision"],
        operation="handoff",
        note="Commit ready for Main deployment",
    )
    assert handed.returncode == 0, handed.stderr
    handoff = json.loads(handed.stdout)
    assert handoff["state"] == "handover"
    # Old start retries cannot undo later handoff; no deployment receipt is invented.
    assert (
        json.loads(command(tmp_path, revision=row["revision"], request=request).stdout)
        == receipt
    )
    assert backlog.list()["items"][0]["deliveryLane"] == "handover"
    assert not backlog.list()["review"]["current"]
    with store.connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM events WHERE topic='backlog.assignment'"
            ).fetchone()[0]
            == 2
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"blockers": ["blocked"]},
        {"subtasks": [{"id": "part", "title": "Part", "state": "blocked"}]},
        {"dependencies": ["missing"]},
    ],
)
def test_start_refuses_blockers_and_unavailable_dependencies(tmp_path, changes):
    backlog, row = seed(tmp_path, **changes)
    result = command(tmp_path, revision=row["revision"])
    assert result.returncode == 1
    assert backlog.list()["items"][0]["revision"] == row["revision"]


def test_deployed_task_handoff_allowed_start_refused_and_dependency_requires_available_receipt(
    tmp_path,
):
    backlog, row = seed(tmp_path, dependencies=["foundation"])
    updates = Updates(backlog.store)
    deployed = updates.publish(
        Publication(
            feature="foundation",
            title="Foundation",
            summary="Available",
            deploymentId="one",
            deployedAt=datetime.now(UTC),
        )
    )
    updates.qa(deployed["id"], QA(deploymentId="one", state="failed", details="Broken"))
    assert command(tmp_path, revision=row["revision"]).returncode == 1
    updates.publish(
        Publication(
            feature="foundation",
            title="Foundation",
            summary="New deployment",
            deploymentId="two",
            deployedAt=datetime.now(UTC),
        )
    )
    assert (
        command(tmp_path, revision=row["revision"]).returncode == 0
    )  # pending QA is available, not accepted
    updates.publish(
        Publication(
            feature="task",
            title="Task",
            summary="Now deployed",
            deploymentId="three",
            deployedAt=datetime.now(UTC),
        )
    )
    handoff = command(tmp_path, revision=row["revision"] + 1, operation="handoff")
    assert handoff.returncode == 0, handoff.stderr
    receipt = json.loads(handoff.stdout)
    assert receipt["state"] == "handover"
    assert receipt["revision"] == row["revision"] + 2
    rejected = command(tmp_path, revision=receipt["revision"], operation="start")
    assert rejected.returncode == 1 and "Updates" in rejected.stderr


@pytest.mark.parametrize("operation", ["blocked", "failed"])
def test_failure_transitions_require_matching_worker_and_keep_progress_honest(
    tmp_path, operation
):
    backlog, row = seed(tmp_path)
    first = json.loads(command(tmp_path, revision=row["revision"]).stdout)
    assert (
        command(
            tmp_path, revision=first["revision"], worker="other", operation=operation
        ).returncode
        == 1
    )
    changed = command(
        tmp_path,
        revision=first["revision"],
        operation=operation,
        note="Provider unavailable",
    )
    assert changed.returncode == 0, changed.stderr
    record = backlog.list()["items"][0]
    assert record["deliveryLane"] == "blocked"
    assert record["percent"] == row["percent"]
    assert "Provider unavailable" in record["blockers"][0]
    assert command(tmp_path, revision=record["revision"]).returncode == 1


def test_concurrent_start_reserves_one_worker_and_preserves_user_priority(tmp_path):
    backlog, row = seed(tmp_path)

    def assign(worker):
        body = Assignment(
            requestId=uuid4(),
            feature="task",
            worker=worker,
            expectedRevision=row["revision"],
            operation="start",
            note="Start",
        )
        try:
            return transition(Backlog(backlog.store), body)
        except ValueError as error:
            return str(error)

    before = backlog.list()["ordering"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(assign, ["worker_a", "worker_b"]))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert backlog.list()["ordering"] == before
    assert backlog.list()["items"][0]["revision"] == row["revision"] + 1


def test_pause_releases_assignment_without_fake_completion(tmp_path):
    backlog, row = seed(tmp_path)
    started = json.loads(command(tmp_path, revision=row["revision"]).stdout)
    paused = command(
        tmp_path,
        revision=started["revision"],
        operation="pause",
        note="Higher priority work assigned",
    )
    assert paused.returncode == 0, paused.stderr
    item = backlog.list()["items"][0]
    assert item["deliveryLane"] == "queued"
    assert item["worker"] is None and item["owner"] is None
    assert item["percent"] == row["percent"]
    assert (
        command(tmp_path, revision=item["revision"], worker="worker_b").returncode == 0
    )
