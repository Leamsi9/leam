from uuid import uuid4

import pytest
from test_backlog_edit import fixture

from leam_api.backlog import Assessment
from leam_api.backlog_assignment import Assignment, transition
from leam_api.backlog_refill import Staffing


def test_pause_caller_preserves_rank_prevents_dispatch_survives_review(tmp_path):
    backlog, client, row = fixture(tmp_path)
    request = {
        "requestId": str(uuid4()),
        "paused": True,
        "allQueued": True,
        "expectedRevisions": {"fixture": row["revision"]},
    }
    before = backlog.list()["ordering"]
    result = client.post("/api/backlog/pause", json=request)
    assert result.status_code == 200, result.text
    assert client.post("/api/backlog/pause", json=request).json() == result.json()
    assert backlog.list()["ordering"] == before
    assert Staffing(backlog).snapshot()["eligibleCount"] == 0
    revised = backlog.upsert(
        Assessment(
            feature="fixture",
            title="Original",
            percent=21,
            currentStep="Reviewed",
            paused=False,
        )
    )
    assert revised["paused"] is True
    with pytest.raises(ValueError, match="paused"):
        transition(
            backlog,
            Assignment(
                requestId=uuid4(),
                feature="fixture",
                worker="test",
                expectedRevision=revised["revision"],
                operation="start",
                note="Must not start paused work",
            ),
        )
    response = client.post(
        "/api/backlog/pause",
        json={
            "requestId": str(uuid4()),
            "paused": False,
            "expectedRevisions": {"fixture": revised["revision"]},
        },
    )
    assert response.status_code == 200, response.text
    assert Staffing(backlog).snapshot()["eligibleCount"] == 1
    revision = response.json()["items"][0]["revision"]
    transition(
        backlog,
        Assignment(
            requestId=uuid4(),
            feature="fixture",
            worker="test",
            expectedRevision=revision,
            operation="start",
            note="Explicitly unpaused",
        ),
    )
    assert (
        client.post(
            "/api/backlog/pause",
            json={
                "requestId": str(uuid4()),
                "paused": True,
                "expectedRevisions": {"fixture": revision + 1},
            },
        ).status_code
        == 409
    )


def test_bulk_pause_conflicts_are_atomic_and_request_bound(tmp_path):
    backlog, client, row = fixture(tmp_path)
    second = backlog.upsert(
        Assessment(feature="second", title="Second", percent=0, currentStep="Queued")
    )
    request = {
        "requestId": str(uuid4()),
        "paused": True,
        "allQueued": True,
        "expectedRevisions": {"fixture": row["revision"]},
    }
    assert client.post("/api/backlog/pause", json=request).status_code == 409
    assert not any(x["paused"] for x in backlog.list()["items"])
    request["expectedRevisions"]["second"] = second["revision"]
    assert client.post("/api/backlog/pause", json=request).status_code == 200
    assert (
        client.post("/api/backlog/pause", json={**request, "paused": False}).status_code
        == 409
    )
    assert all(x["paused"] for x in backlog.list()["items"])
