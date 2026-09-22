from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from leam_api.backlog import Assessment, Backlog, router
from leam_api.store import Store
from leam_api.updates import Publication, Updates


def assessment(**changes):
    return Assessment(
        feature="recovery-controls",
        title="Recovery controls",
        currentStep="Implement independent service restart",
        percent=35,
        blockers=["Remote wake host still required"],
        **changes,
    )


def test_route_persists_estimates_ages_without_inflation_and_excludes_deployed(
    tmp_path,
):
    now = [datetime.now(UTC).timestamp()]
    store = Store(tmp_path)
    backlog = Backlog(store, clock=lambda: now[0])
    app = FastAPI()
    app.include_router(router(backlog))
    client = TestClient(app)
    saved = backlog.upsert(assessment())
    first = client.get("/api/backlog").json()
    assert first["items"][0]["percent"] == 35 and not first["items"][0]["stale"]
    now[0] += 1201
    later = client.get("/api/backlog").json()
    assert later["items"][0]["percent"] == 35 and later["items"][0]["stale"]
    assert (
        Backlog(Store(tmp_path)).list()["items"][0]["assessedAt"] == saved["assessedAt"]
    )
    updates = Updates(store)
    updates.publish(
        Publication(
            feature="recovery-controls",
            title="Recovery",
            summary="Deployed recovery controls",
            deploymentId="test-artifact",
            deployedAt=datetime.now(UTC),
        )
    )
    assert client.get("/api/backlog").json()["items"] == []
    assert client.post("/api/backlog", json={}).status_code == 405


def test_validation_and_older_heartbeat_cannot_replace_newer_assessment(tmp_path):
    backlog = Backlog(Store(tmp_path))
    latest = backlog.upsert(assessment())
    earlier = assessment(assessedAt=datetime.now(UTC) - timedelta(hours=1))
    with pytest.raises(ValueError, match="older"):
        backlog.upsert(earlier)
    assert backlog.list()["items"][0]["revision"] == latest["revision"]
    for change in [
        {"percent": 100},
        {"percent": -1},
        {"feature": "../path"},
        {"blockers": ["x"] * 21},
        {"assessedAt": "2026-01-01T00:00:00"},
    ]:
        base = assessment().model_dump()
        with pytest.raises(ValidationError):
            Assessment(**{**base, **change})


def test_real_app_auth_protects_backlog_and_cli_upsert(tmp_path):
    import json
    import subprocess
    import sys

    from test_api import FakeCodex, login

    from leam_api.app import create_app

    store = Store(tmp_path)
    path = tmp_path / "assessment.json"
    path.write_text(assessment().model_dump_json())
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "leam_api.backlog",
            "--data-dir",
            str(tmp_path),
            "upsert",
            "--input",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout)["percent"] == 35
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    app.include_router(router(Backlog(store)))
    with TestClient(app) as client:
        assert client.get("/api/backlog").status_code == 401
        login(client)
        assert (
            client.get("/api/backlog").json()["items"][0]["title"]
            == "Recovery controls"
        )


def test_backlog_route_orders_delivery_stage_rank_importance_not_estimates(tmp_path):
    backlog = Backlog(Store(tmp_path))
    rows = [
        ("queued-high", "queued", 0, "high", 99, []),
        ("ready-low", "ready", 1, "low", 5, []),
        ("ready-high", "ready", 1, "high", 0, []),
        ("ready-ranked", "ready", 0, "low", 1, []),
        ("ready-unranked", "ready", None, "high", 99, []),
        ("handover", "handover", 9, "normal", 20, []),
        ("active", "in_progress", 0, "high", 40, []),
        ("blocked", "blocked", 0, "high", 95, []),
        ("blocked-notes", "ready", 2, "high", 99, ["Await access"]),
    ]
    for feature, state, rank, priority, percent, blockers in rows:
        backlog.upsert(
            Assessment(
                feature=feature,
                title=feature,
                percent=percent,
                currentStep="Test assessment",
                deliveryState=state,
                rank=rank,
                priority=priority,
                blockers=blockers,
            )
        )
    backlog.upsert(
        Assessment(
            feature="dependent",
            title="Dependent",
            percent=90,
            currentStep="Wait",
            deliveryState="ready",
            dependencies=["active"],
        )
    )
    app = FastAPI()
    app.include_router(router(backlog))
    with TestClient(app) as client:
        result = client.get("/api/backlog").json()
        # New tickets append to saved canonical order; factual stages do not rerank.
        assert [i["feature"] for i in result["items"]] == [r[0] for r in rows] + ["dependent"]
        assert result["items"][-1]["deliveryLane"] == "blocked"
        assert result["items"][-1]["deliveryState"] == "ready"
        backlog.clock = lambda: datetime.now(UTC).timestamp() + 2000
        assert [i["feature"] for i in client.get("/api/backlog").json()["items"]] == [
            i["feature"] for i in result["items"]
        ]


def review(backlog, *, changes=None, remove=None, request_id=None):
    import uuid

    from leam_api.backlog import Reconciliation

    items = backlog.list()["items"]
    removed = remove or []
    assessments = []
    for item in items:
        if item["feature"] in removed:
            continue
        row = {
            key: value
            for key, value in item.items()
            if key not in {"revision", "stale", "deliveryLane"}
        }
        row.update(
            assessedAt=datetime.now(UTC),
            evidence="Current deployment/source checked; remaining scope unchanged",
        )
        row.update((changes or {}).get(item["feature"], {}))
        assessments.append(row)
    return Reconciliation(
        requestId=request_id or uuid.uuid4(),
        trigger="status",
        expectedRevisions={item["feature"]: item["revision"] for item in items},
        assessments=assessments,
        removals=[
            {"feature": key, "reason": "Covered by deployed increment"}
            for key in removed
        ],
    )


def test_full_review_caller_atomic_removal_addition_retry_and_status(tmp_path):
    import json
    import subprocess
    import sys

    from leam_api.backlog import ReviewedAssessment

    b = Backlog(Store(tmp_path))
    b.upsert(assessment())
    r = review(b, remove=["recovery-controls"])
    r.assessments.append(
        ReviewedAssessment(
            feature="new-scope",
            title="New scope",
            currentStep="Not implemented",
            percent=0,
            evidence="Explicit new user request",
            deliveryState="ready",
            rank=1,
            priority="high",
            nextAction="Build the requested scope",
            owner="main",
            worker="session-new",
            subtasks=[{"id": "build", "title": "Implement scope"}],
        )
    )
    path = tmp_path / "review.json"
    path.write_text(r.model_dump_json())
    command = [
        sys.executable,
        "-m",
        "leam_api.backlog",
        "--data-dir",
        str(tmp_path),
        "reconcile",
        "--input",
        str(path),
    ]
    first = subprocess.run(command, check=True, capture_output=True, text=True)
    second = subprocess.run(command, check=True, capture_output=True, text=True)
    assert json.loads(first.stdout) == json.loads(second.stdout)
    assert b.status()["reviewDue"] is False
    assert b.status()["tickets"] == 1
    assert b.list()["items"][0]["percent"] == 0
    assert b.list()["items"][0]["worker"] == "session-new"
    assert json.loads(first.stdout)["assessments"]["new-scope"]["rank"] == 1
    assert b.list()["review"]["reviewedCount"] == 1
    b.clock = lambda: datetime.now(UTC).timestamp() + 1201
    assert b.status()["reviewDue"] and b.status()["staleTickets"] == 1
    assert b.list()["items"][0]["percent"] == 0


def test_full_review_rejects_omissions_and_stale_inventory_without_partial_writes(
    tmp_path,
):
    b = Backlog(Store(tmp_path))
    b.upsert(assessment())
    raw = review(b).model_dump()
    raw["assessments"] = []
    from leam_api.backlog import Reconciliation

    with pytest.raises(ValueError, match="Every existing"):
        Reconciliation(**raw)
    stale = review(b, remove=["recovery-controls"])
    latest = b.upsert(assessment())
    with pytest.raises(ValueError, match="changed"):
        b.reconcile(stale)
    assert b.list()["items"][0]["revision"] == latest["revision"]
    assert b.status()["reviewDue"]
    current = review(b)
    b.reconcile(current)
    changed = current.model_copy(deep=True)
    changed.assessments[0].percent = 1
    with pytest.raises(ValueError, match="reused"):
        b.reconcile(changed)
    assert b.list()["items"][0]["percent"] == 35
    b.upsert(assessment())
    assert b.status()["reviewDue"]


def test_newly_deployed_ticket_invalidates_inflight_full_review(tmp_path):
    b = Backlog(Store(tmp_path))
    b.upsert(assessment())
    r = review(b)
    Updates(b.store).publish(
        Publication(
            feature="recovery-controls",
            title="Deployed",
            summary="Real receipt fixture",
            deploymentId="fixture",
            deployedAt=datetime.now(UTC),
        )
    )
    with pytest.raises(ValueError, match="changed"):
        b.reconcile(r)
    assert b.list()["items"] == []
    assert b.status()["reviewDue"]


def test_old_exact_review_retry_cannot_resurrect_removed_scope(tmp_path):
    from leam_api.backlog import ReviewedAssessment

    b = Backlog(Store(tmp_path))
    first = review(b)
    first.assessments.append(
        ReviewedAssessment(
            feature="old-scope",
            title="Old scope",
            currentStep="Not started",
            percent=0,
            evidence="Original explicit scope",
        )
    )
    original = b.reconcile(first)
    b.reconcile(review(b, remove=["old-scope"]))
    assert b.list()["items"] == []
    assert b.reconcile(first) == original
    assert b.list()["items"] == []
    assert b.status()["reviewDue"] is False


def test_status_command_refuses_to_certify_unreviewed_inventory(tmp_path):
    import subprocess
    import sys

    b = Backlog(Store(tmp_path))
    b.upsert(assessment())
    command = [
        sys.executable,
        "-m",
        "leam_api.backlog",
        "--data-dir",
        str(tmp_path),
        "status",
        "--require-current",
    ]
    assert subprocess.run(command, capture_output=True, check=False).returncode == 2
    b.reconcile(review(b))
    # Current inventory alone no longer establishes a current worker observation.
    assert subprocess.run(command, capture_output=True, check=False).returncode == 2
    from test_backlog_refill import observation

    from leam_api.backlog_refill import Staffing
    service = Staffing(b)
    assert service.check(observation(service, capacity=1, capacityReason="Only coordinator capacity exists in this fixture"))["passed"]
    assert subprocess.run(command, capture_output=True, check=False).returncode == 0
    b.upsert(assessment())
    assert subprocess.run(command, capture_output=True, check=False).returncode == 2


def test_delivery_fields_roundtrip_receipt_and_old_writer_preservation(tmp_path):
    b = Backlog(Store(tmp_path))
    b.upsert(assessment())
    r = review(
        b,
        changes={
            "recovery-controls": {
                "deliveryState": "in_progress",
                "rank": 2,
                "priority": "high",
                "nextAction": "Build restart control",
                "owner": "main",
                "worker": "session-01",
                "dependencies": ["runtime"],
                "subtasks": [
                    {
                        "id": "restart",
                        "title": "Restart control",
                        "state": "in_progress",
                    }
                ],
            }
        },
    )
    receipt = b.reconcile(r)
    item = b.list()["items"][0]
    assert item["worker"] == "session-01" and item["percent"] == 35
    assert receipt["assessments"][item["feature"]]["subtasks"] == item["subtasks"]
    assert b.reconcile(r) == receipt
    for field, value in [
        ("rank", 3),
        ("worker", "session-02"),
        ("subtasks", []),
        ("priority", "low"),
        ("dependencies", []),
        ("owner", None),
        ("nextAction", "Another step"),
        ("deliveryState", "queued"),
    ]:
        changed = r.model_copy(deep=True)
        setattr(changed.assessments[0], field, value)
        with pytest.raises(ValueError, match="reused"):
            b.reconcile(changed)
    b.upsert(assessment())  # An old caller omits all delivery fields.
    assert b.list()["items"][0]["worker"] == "session-01"
    legacy_review = review(b).model_dump()
    from leam_api.backlog import DELIVERY_DEFAULTS, Reconciliation

    for row in legacy_review["assessments"]:
        for field in DELIVERY_DEFAULTS:
            row.pop(field)
    b.reconcile(Reconciliation(**legacy_review))
    assert b.list()["items"][0]["rank"] == 1  # Canonical one-based queue rank.
    b.reconcile(
        review(b, changes={"recovery-controls": {"worker": None, "subtasks": []}})
    )
    assert b.list()["items"][0]["worker"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"rank": -1},
        {"rank": True},
        {"rank": 1000001},
        {"priority": "urgent"},
        {"deliveryState": "completed"},
        {"owner": "/home/private"},
        {"worker": "file:///tmp/session"},
        {"worker": "x" * 101},
        {"nextAction": "x" * 1001},
        {"dependencies": ["../private"]},
        {"dependencies": ["one", "one"]},
        {"dependencies": ["recovery-controls"]},
        {"dependencies": [f"dep-{i}" for i in range(41)]},
        {"subtasks": [{"id": "one", "title": "x", "state": "unknown"}]},
        {"subtasks": [{"id": "one", "title": "x"}] * 2},
        {"subtasks": [{"id": f"s-{i}", "title": "x"} for i in range(41)]},
        {"subtasks": [{"id": "one", "title": "x", "blocker": "x" * 1001}]},
    ],
)
def test_delivery_limits(change):
    with pytest.raises(ValidationError):
        Assessment(**{**assessment().model_dump(), **change})


def test_legacy_body_and_preupgrade_receipt_retry(tmp_path):
    import hashlib
    import json

    from leam_api.backlog import CONTENT_DEFAULTS, DELIVERY_DEFAULTS, Reconciliation

    b = Backlog(Store(tmp_path))
    old = assessment().model_dump(mode="json", exclude=set(DELIVERY_DEFAULTS) | set(CONTENT_DEFAULTS))
    with b.store.connect() as db:
        db.execute(
            "INSERT INTO backlog_assessments VALUES (?,?,?,?)",
            (
                old["feature"],
                1,
                datetime.fromisoformat(old["assessedAt"]).timestamp(),
                json.dumps(old),
            ),
        )
    item = b.list()["items"][0]
    assert item["deliveryState"] == "queued" and item["worker"] is None
    assert item["rank"] == 1 and item["subtasks"] == []  # Legacy records receive a queue rank.
    raw = review(b).model_dump(mode="json")
    for row in raw["assessments"]:
        for key in {*DELIVERY_DEFAULTS, *CONTENT_DEFAULTS}:
            row.pop(key)
    r = Reconciliation(**raw)
    legacy_json = r.model_dump_json(
        exclude={"assessments": {"__all__": set(DELIVERY_DEFAULTS) | set(CONTENT_DEFAULTS)}}
    )
    receipt = {
        "requestId": str(r.requestId),
        "fingerprint": hashlib.sha256(legacy_json.encode()).hexdigest(),
        "trigger": "status",
        "reviewedAt": b.clock(),
        "reviewedCount": 1,
        "revisions": {old["feature"]: 1},
        "evidence": {},
        "removed": [],
        "addedCount": 0,
    }
    with b.store.connect() as db:
        db.execute(
            "INSERT INTO settings VALUES (?,?)",
            ("backlog:last-review", json.dumps(receipt)),
        )
    assert b.reconcile(r) == receipt
    assert b.list()["items"][0]["revision"] == 1
    changed = r.model_copy(deep=True)
    changed.assessments[0].rank = 1
    with pytest.raises(ValueError, match="reused"):
        b.reconcile(changed)
    # Same-timestamp old upsert is idempotent despite additive read defaults.
    assert b.upsert(Assessment(**old))["revision"] == 1


def test_delivery_reconciliation_rejects_aba_inventory_and_stale_decisions(tmp_path):
    from leam_api.backlog import ReviewedAssessment

    b = Backlog(Store(tmp_path))
    b.upsert(assessment())
    stale = review(b)
    b.reconcile(review(b, remove=["recovery-controls"]))
    added = review(b)
    added.assessments.append(
        ReviewedAssessment(**assessment().model_dump(), evidence="Reopened scope")
    )
    b.reconcile(added)
    assert (
        b.list()["items"][0]["revision"] > stale.expectedRevisions["recovery-controls"]
    )
    with pytest.raises(ValueError, match="changed"):
        b.reconcile(stale)
    assert b.status()["reviewDue"] is False
