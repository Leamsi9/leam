import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_backlog import review

from leam_api.backlog import (
    CONTENT_DEFAULTS,
    DELIVERY_DEFAULTS,
    Assessment,
    Backlog,
    Reconciliation,
    router,
)
from leam_api.backlog_assignment import Assignment, transition
from leam_api.backlog_edit import acknowledge_delete
from leam_api.store import Store
from leam_api.updates import Publication, Updates


def fixture(tmp_path, **extra):
    backlog = Backlog(Store(tmp_path))
    item = backlog.upsert(
        Assessment(
            feature="fixture",
            title="Original",
            currentStep="Operator evidence",
            percent=20,
            subtasks=[
                {"id": "keep", "title": "Keep task"},
                {"id": "remove", "title": "Remove task"},
            ],
            **extra,
        )
    )
    app = FastAPI()
    app.include_router(router(backlog))
    return backlog, TestClient(app), item


def write(client, operation, revision, **extra):
    body = {"requestId": str(uuid4()), "revision": revision, **extra}
    return client.post("/api/backlog/fixture/" + operation, json=body), body


def test_real_edit_caller_preserves_user_fields_across_operator_upsert_and_reconciliation(
    tmp_path,
):
    backlog, client, item = fixture(tmp_path)
    response, request = write(
        client,
        "edit",
        item["revision"],
        changes={
            "title": "My title",
            "rationale": "Why this matters",
            "scope": "Desired result",
            "subtasks": [
                {"id": "keep", "title": "My label"},
                {"id": "user-new", "title": "My added step"},
            ],
        },
    )
    assert response.status_code == 200
    assert (
        client.post("/api/backlog/fixture/edit", json=request).json() == response.json()
    )
    current = backlog.list()["items"][0]
    assert current["assessedAt"] == item["assessedAt"] and current["percent"] == 20
    assert current["rank"] == 1 and current["currentStep"] == "Operator evidence"
    update = Assessment(
        feature="fixture",
        title="Old operator title",
        rationale="Old why",
        scope="Old scope",
        currentStep="New measured progress",
        percent=50,
        assessedAt=datetime.now(UTC),
        subtasks=[
            {"id": "keep", "title": "Old label", "state": "done"},
            {"id": "remove", "title": "Old removed task"},
            {"id": "operator-new", "title": "New operator step"},
        ],
    )
    backlog.upsert(update)
    backlog.reconcile(
        review(
            backlog,
            changes={
                "fixture": {
                    "title": "Another stale title",
                    "rationale": "stale",
                    "scope": "stale",
                }
            },
        )
    )
    current = backlog.list()["items"][0]
    assert (current["title"], current["rationale"], current["scope"]) == (
        "My title",
        "Why this matters",
        "Desired result",
    )
    assert [
        (task["id"], task["title"], task["state"]) for task in current["subtasks"]
    ] == [
        ("keep", "My label", "done"),
        ("user-new", "My added step", "todo"),
        ("operator-new", "New operator step", "todo"),
    ]
    assert current["percent"] == 50
    assert (
        client.post("/api/backlog/fixture/edit", json=request).json() == response.json()
    )
    assert backlog.list()["items"][0]["revision"] == current["revision"]


def test_edit_revision_conflict_does_not_modify_ticket_or_reuse_request(tmp_path):
    backlog, client, item = fixture(tmp_path)
    first, request = write(
        client, "edit", item["revision"], changes={"rationale": "Reason"}
    )
    assert first.status_code == 200
    second, _ = write(
        client, "edit", item["revision"], changes={"title": "Stale title"}
    )
    assert second.status_code == 409
    assert (
        client.post(
            "/api/backlog/fixture/edit",
            json={**request, "changes": {"rationale": "Different"}},
        ).status_code
        == 409
    )
    assert backlog.list()["items"][0]["title"] == "Original"
    for field in ["percent", "deliveryState", "owner", "worker", "currentStep"]:
        response, _ = write(
            client, "edit", first.json()["revision"], changes={field: "forbidden"}
        )
        assert response.status_code == 422


def test_delete_tombstone_blocks_reconciliation_upsert_and_exact_retry_cannot_remove_restore(
    tmp_path,
):
    backlog, client, item = fixture(tmp_path)
    old_review = review(backlog)
    response, request = write(client, "delete", item["revision"])
    assert response.status_code == 200 and not backlog.list()["items"]
    assert client.get("/api/backlog/deleted").json()["items"][0]["feature"] == "fixture"
    with pytest.raises(ValueError, match="changed"):
        backlog.reconcile(old_review)
    old_review.expectedRevisions = {}
    with pytest.raises(ValueError, match="deleted"):
        backlog.reconcile(old_review)
    with pytest.raises(ValueError, match="deleted"):
        backlog.upsert(
            Assessment(
                feature="fixture", title="Recreated", currentStep="Old work", percent=0
            )
        )
    restored, _ = write(client, "restore", response.json()["revision"])
    assert restored.status_code == 200
    assert (
        client.post("/api/backlog/fixture/delete", json=request).json()
        == response.json()
    )
    current = backlog.list()["items"][0]
    assert (
        current["revision"] > response.json()["revision"]
        and current["deliveryState"] == "queued"
    )
    assert current["worker"] is None


def test_active_delete_requests_cancellation_and_blocks_restore_and_worker_handoff(
    tmp_path,
):
    backlog, client, item = fixture(
        tmp_path, deliveryState="in_progress", owner="main", worker="worker-a"
    )
    rejected, _ = write(client, "delete", item["revision"])
    assert rejected.status_code == 409
    response, _ = write(client, "delete", item["revision"], confirmActive=True)
    assert response.status_code == 200 and response.json()["cancellationRequired"]
    with backlog.store.connect() as db:
        events = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT payload FROM events WHERE topic='backlog.user_change'"
            )
        ]
    assert (
        len(events) == 1
        and events[0]["actor"] == "user"
        and events[0]["worker"] == "worker-a"
    )
    assert write(client, "restore", response.json()["revision"])[0].status_code == 409
    with pytest.raises(ValueError, match="Register"):
        transition(
            backlog,
            Assignment(
                requestId=uuid4(),
                feature="fixture",
                worker="worker-a",
                expectedRevision=item["revision"],
                operation="handoff",
                note="Late handoff",
            ),
        )
    acknowledge_delete(backlog, "fixture", response.json()["revision"])
    assert write(client, "restore", response.json()["revision"])[0].status_code == 200


def test_deployed_feature_cannot_be_edited_deleted_or_restored_through_backlog(
    tmp_path,
):
    backlog, client, item = fixture(tmp_path)
    Updates(backlog.store).publish(
        Publication(
            feature="fixture",
            title="Released",
            summary="Deployed",
            deploymentId="fixture-build",
            deployedAt=datetime.now(UTC),
        )
    )
    for operation, fields in [
        ("edit", {"changes": {"title": "Changed"}}),
        ("delete", {}),
        ("restore", {}),
    ]:
        assert (
            write(client, operation, item["revision"], **fields)[0].status_code == 409
        )


def test_v2_reconciliation_receipt_survives_new_content_defaults(tmp_path):
    backlog, _, _ = fixture(tmp_path)
    raw = review(backlog).model_dump(mode="json")
    for item in raw["assessments"]:
        for field in CONTENT_DEFAULTS:
            item.pop(field)
    request = Reconciliation.model_validate(raw)
    serialized = request.model_dump_json(
        exclude={"assessments": {"__all__": set(CONTENT_DEFAULTS)}}
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "request": json.loads(serialized),
                "deliveryFields": [
                    sorted(set(item.model_fields_set) & DELIVERY_DEFAULTS.keys())
                    for item in request.assessments
                ],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    receipt = {
        "requestId": str(request.requestId),
        "schemaVersion": 2,
        "fingerprint": fingerprint,
    }
    with backlog.store.connect() as db:
        db.execute(
            "INSERT INTO settings VALUES (?,?)",
            ("backlog:review:" + str(request.requestId), json.dumps(receipt)),
        )
    assert backlog.reconcile(request) == receipt


def test_concurrent_user_edits_have_one_revision_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    backlog, client, item = fixture(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                write, client, "edit", item["revision"], changes={"title": title}
            )
            for title in ["First", "Second"]
        ]
        results = [future.result()[0] for future in futures]
    assert sorted(result.status_code for result in results) == [200, 409]
    assert backlog.list()["items"][0]["revision"] == item["revision"] + 1
