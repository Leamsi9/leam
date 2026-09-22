"""Actual CLI/domain callers for mechanical evidence checks and explicit deltas."""

import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from leam_api.backlog import Assessment, Backlog, ReviewedAssessment, UserOrder
from leam_api.backlog_edit import Changes, Delete, Edit, mutate
from leam_api.backlog_evidence import (
    MAX_SNAPSHOTS,
    BacklogEvidence,
    DeltaRequest,
    ExistingStore,
)
from leam_api.store import Store
from leam_api.updates import QA, Publication, Updates


def setup(path, count=3):
    backlog = Backlog(Store(path))
    now = datetime.now(UTC) - timedelta(seconds=20)
    for n in range(count):
        backlog.upsert(
            Assessment(
                feature=f"task-{n}",
                title=f"Task {n}",
                currentStep="PRIVATE_SCOPE " + "x" * 1200,
                percent=20,
                assessedAt=now,
                deliveryState="queued",
                nextAction="PRIVATE_NEXT",
            )
        )
    return backlog, BacklogEvidence(backlog)


def capture(service, **kwargs):
    snapshot = service.snapshot(uuid4())
    delta = service.delta(snapshot["snapshotId"])
    request = DeltaRequest(
        requestId=uuid4(),
        snapshotId=snapshot["snapshotId"],
        acknowledge={
            feature: "Explicitly inspected recorded evidence; no scope change"
            for feature in delta["changedFeatures"]
        },
        acknowledgeMetadata=delta["metadataChanged"],
        **kwargs,
    )
    return request


def db_rows(backlog):
    with backlog.store.connect() as db:
        return [
            tuple(row)
            for row in db.execute("SELECT * FROM backlog_assessments ORDER BY feature")
        ]


def cli(path, *args, ok=True):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "leam_api.backlog_evidence",
            "--data-dir",
            str(path),
            *args,
        ],
        capture_output=True,
        check=False,        text=True,
        env=os.environ.copy(),
    )
    if ok:
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    assert result.returncode != 0
    return result


def test_cli_snapshot_dry_run_apply_preserves_semantics_and_reduces_bytes(tmp_path):
    backlog, service = setup(tmp_path, count=8)
    before = db_rows(backlog)
    snapshot = cli(tmp_path, "snapshot", "--request-id", str(uuid4()))
    delta = cli(tmp_path, "delta", "--snapshot", snapshot["snapshotId"])
    assert len(delta["changedFeatures"]) == 8 and delta["metadataChanged"]
    request = DeltaRequest(
        requestId=uuid4(),
        snapshotId=snapshot["snapshotId"],
        acknowledge={
            key: "Reviewed recorded evidence" for key in delta["changedFeatures"]
        },
        acknowledgeMetadata=True,
    )
    path = tmp_path / "decisions.json"
    path.write_text(request.model_dump_json())
    with backlog.store.connect() as db:
        prior = list(db.iterdump())
    dry = cli(tmp_path, "dry-run", "--input", str(path))
    with backlog.store.connect() as db:
        assert list(db.iterdump()) == prior
    assert not dry["mutated"] and dry["mechanicallyPreservedCount"] == 8
    checked = cli(tmp_path, "apply", "--input", str(path))
    assert checked["semanticAssessmentCount"] == 0 and db_rows(backlog) == before
    assert cli(tmp_path, "apply", "--input", str(path)) == checked
    assert cli(tmp_path, "status")["current"]
    second = cli(tmp_path, "snapshot", "--request-id", str(uuid4()))
    unchanged = cli(tmp_path, "delta", "--snapshot", second["snapshotId"])
    assert unchanged["changes"] == [] and unchanged["unchangedCount"] == 8
    assert "PRIVATE_SCOPE" not in json.dumps(unchanged)
    full_bytes = len(json.dumps(backlog.list()).encode())
    delta_bytes = len(json.dumps(unchanged).encode())
    assert delta_bytes < full_bytes
    print(
        json.dumps(
            {
                "fixture": "8 synthetic unchanged tickets with1200-character progress notes",
                "fullListBytes": full_bytes,
                "deltaBytes": delta_bytes,
                "tokenSavings": None,
                "modelCallsMadeByTool": 0,
            }
        )
    )
    no_change = DeltaRequest(requestId=uuid4(), snapshotId=second["snapshotId"])
    result = service.prepare(no_change, dry_run=False)
    assert result["reviewedCount"] == 0 and db_rows(backlog) == before


def test_first_snapshot_never_blesses_new_evidence_without_decisions(tmp_path):
    backlog, service = setup(tmp_path)
    snap = service.snapshot(uuid4())
    with pytest.raises(ValueError, match="Every changed evidence"):
        service.prepare(
            DeltaRequest(requestId=uuid4(), snapshotId=snap["snapshotId"]),
            dry_run=False,
        )
    assert backlog.list()["review"]["current"] is False
    request = capture(service)
    service.prepare(request, dry_run=False)
    # A fresh snapshot after a new mutation still compares to the accepted baseline.
    item = backlog.list()["items"][0]
    mutate(
        backlog,
        item["feature"],
        "edit",
        Edit(
            requestId=uuid4(),
            revision=item["revision"],
            changes=Changes(title="User-owned correction"),
        ),
    )
    snap = service.snapshot(uuid4())
    assert service.delta(snap["snapshotId"])["changedFeatures"] == [item["feature"]]
    with pytest.raises(ValueError, match="Every changed evidence"):
        service.prepare(
            DeltaRequest(requestId=uuid4(), snapshotId=snap["snapshotId"]), dry_run=True
        )


def test_stale_snapshot_dependency_qa_and_order_changes_are_not_hidden(tmp_path):
    backlog, service = setup(tmp_path)
    updates = Updates(backlog.store)
    dependency = updates.publish(
        Publication(
            feature="dependency",
            title="Dependency",
            summary="private report",
            deploymentId="fixture-release",
            deployedAt=datetime.now(UTC),
        )
    )
    item = backlog.list()["items"][0]
    backlog.upsert(
        Assessment(
            **{
                **{
                    key: value
                    for key, value in item.items()
                    if key in Assessment.model_fields
                },
                "dependencies": ["dependency"],
                "assessedAt": datetime.now(UTC),
            }
        )
    )
    service.prepare(capture(service), dry_run=False)
    request = capture(service)
    updates.qa(
        dependency["id"],
        QA(
            deploymentId="fixture-release",
            state="failed",
            details="private provider evidence",
        ),
    )
    assert not service.status()["current"] and not backlog.list()["review"]["current"]
    with pytest.raises(ValueError, match="Source evidence changed"):
        service.prepare(request, dry_run=False)
    changed = capture(service)
    delta = service.delta(changed.snapshotId)
    assert delta["changedFeatures"] == [item["feature"]]
    assert "private provider evidence" not in json.dumps(delta)
    service.prepare(changed, dry_run=False)
    request = capture(service)
    state = backlog.list()
    backlog.reorder(
        UserOrder(
            requestId=uuid4(),
            revision=state["ordering"]["revision"],
            expectedRevisions={
                row["feature"]: row["revision"] for row in state["items"]
            },
            lane="all",
            features=[row["feature"] for row in state["items"]][::-1],
        )
    )
    with pytest.raises(ValueError, match="Source evidence changed"):
        service.prepare(request, dry_run=True)
    request = capture(service)
    assert request.acknowledgeMetadata
    canonical = backlog.list()["ordering"]["features"]
    service.prepare(request, dry_run=False)
    assert backlog.list()["ordering"]["features"] == canonical


def test_user_overrides_tombstones_and_new_assessment_scope_are_preserved(tmp_path):
    backlog, service = setup(tmp_path)
    service.prepare(capture(service), dry_run=False)
    item = backlog.list()["items"][0]
    mutate(
        backlog,
        item["feature"],
        "edit",
        Edit(
            requestId=uuid4(),
            revision=item["revision"],
            changes=Changes(title="Protected title", scope="PRIVATE_SCOPE_EDIT"),
        ),
    )
    updated = next(
        row for row in backlog.list()["items"] if row["feature"] == item["feature"]
    )
    request = capture(service)
    changed = ReviewedAssessment(
        **{
            **{
                key: value
                for key, value in updated.items()
                if key in Assessment.model_fields
            },
            "title": "Automated rewrite",
            "percent": 30,
            "assessedAt": datetime.now(UTC),
            "evidence": "Explicit reviewed progress evidence",
        }
    )
    request.assessments = [changed]
    request.acknowledge.pop(item["feature"])
    service.prepare(request, dry_run=False)
    assert (
        next(
            row for row in backlog.list()["items"] if row["feature"] == item["feature"]
        )["title"]
        == "Protected title"
    )
    current = backlog.list()["items"][0]
    mutate(
        backlog,
        current["feature"],
        "delete",
        Delete(requestId=uuid4(), revision=current["revision"], confirmActive=False),
    )
    request = capture(service)
    assert current["feature"] in request.acknowledge
    service.prepare(request, dry_run=False)
    assert current["feature"] not in {row["feature"] for row in backlog.list()["items"]}
    request = capture(service)
    request.assessments = [
        ReviewedAssessment(
            feature=current["feature"],
            title="Recreate",
            currentStep="Wrong",
            percent=10,
            evidence="Must remain rejected",
        )
    ]
    with pytest.raises(ValueError, match="deleted by the user"):
        service.prepare(request, dry_run=False)


def test_concurrent_reviews_have_one_winner_and_old_retry_does_not_reapply(tmp_path):
    _backlog, service = setup(tmp_path)
    service.prepare(capture(service), dry_run=False)
    one = capture(service)
    two = one.model_copy(update={"requestId": uuid4()})

    def apply(request):
        try:
            return service.prepare(request, dry_run=False)
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, [one, two]))
    assert sum(isinstance(value, dict) for value in results) == 1
    winner = one if isinstance(results[0], dict) else two
    saved = next(value for value in results if isinstance(value, dict))
    service.prepare(capture(service), dry_run=False)
    assert service.prepare(winner, dry_run=False) == saved
    with pytest.raises(ValueError, match="reused"):
        service.prepare(winner.model_copy(update={"trigger": "status"}), dry_run=True)


def test_bounded_snapshots_and_missing_database_dry_run_never_bootstrap(tmp_path):
    missing = tmp_path / "missing"
    assert cli(missing, "status", ok=False).stderr
    assert not missing.exists()
    backlog, service = setup(tmp_path / "saved")
    first = capture(service)
    receipt = service.prepare(first, dry_run=False)
    for _ in range(MAX_SNAPSHOTS + 3):
        service.snapshot(uuid4())
    with backlog.store.connect() as db:
        count = db.execute(
            "SELECT count(*) FROM settings WHERE key GLOB 'backlog:evidence-snapshot:*'"
        ).fetchone()[0]
    assert count <= MAX_SNAPSHOTS
    assert service.prepare(first, dry_run=False) == receipt
    empty = tmp_path / "empty"
    empty.mkdir()
    with sqlite3.connect(empty / "leam.sqlite3"):
        pass
    with pytest.raises(ValueError, match="Initialize"):
        Backlog(ExistingStore(empty), initialize=False)


def test_existing_cli_status_preserves_unstabilized_order_and_missing_schema(tmp_path):
    backlog, _ = setup(tmp_path)
    with backlog.store.connect() as db:
        db.execute("DELETE FROM settings WHERE key='backlog:user-order'")
        before = list(db.iterdump())
    assert cli(tmp_path, "status")["current"] is False
    with backlog.store.connect() as db:
        assert list(db.iterdump()) == before
        db.execute("DROP TABLE backlog_assessments")
        missing = list(db.iterdump())
    result = cli(tmp_path, "status", ok=False)
    assert "Initialize" in result.stderr and "Traceback" not in result.stderr
    with backlog.store.connect() as db:
        assert list(db.iterdump()) == missing
