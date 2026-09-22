"""Successful explicit approvals acknowledge exact versions, not future changes."""

from uuid import uuid4

import pytest
from test_api import login
from test_approval_policy import proposal, setup
from test_artifacts import client_for

from leam_api.commitments import CommitmentEdit
from leam_api.proposals import BulkApprove, ProposalVersion

H = {"origin": "http://testserver"}


def test_single_and_bulk_approval_http_mark_only_successes_read(tmp_path):
    with client_for(tmp_path) as client:
        login(client)

        def create(title):
            result = client.post(
                "/api/proposals",
                headers=H,
                json={
                    "requestId": str(uuid4()),
                    "threadId": "fixture",
                    "operation": "commitment.create",
                    "input": {"title": title},
                    "reason": "Explicit fixture action",
                },
            )
            assert result.status_code == 200
            return result.json()

        first, second, failed, unrelated = [
            create(t) for t in ("First", "Second", "Failed", "New unrelated")
        ]
        result = client.post(
            f"/api/proposals/{first['id']}/approve",
            headers=H,
            json={"fingerprint": first["fingerprint"]},
        )
        assert result.status_code == 200 and result.json()["unread"] is False
        original_read_at = result.json()["readAt"]
        retry = client.post(
            f"/api/proposals/{first['id']}/approve",
            headers=H,
            json={"fingerprint": first["fingerprint"]},
        )
        assert retry.json()["readAt"] == original_read_at
        bulk = client.post(
            "/api/proposals/approve-all",
            headers=H,
            json={
                "items": [
                    {"id": second["id"], "fingerprint": second["fingerprint"]},
                    {"id": failed["id"], "fingerprint": "0" * 64},
                ]
            },
        )
        assert bulk.status_code == 200
        assert bulk.json()["items"][0]["unread"] is False
        assert bulk.json()["failed"][0]["id"] == failed["id"]
        status = client.get("/api/proposals/status").json()
        assert status == {"unreadCount": 2, "pendingCount": 2}
        rows = {v["id"]: v for v in client.get("/api/proposals").json()["items"]}
        assert rows[failed["id"]]["unread"] and rows[unrelated["id"]]["unread"]


@pytest.mark.asyncio
async def test_automatic_changes_are_unread_until_explicit_acknowledgement(tmp_path):
    _store, service = setup(tmp_path)
    automatic = await service.propose(proposal("Automatic"), origin="today")
    assert automatic["state"] == "complete" and automatic["unread"]
    assert service.status()["unreadCount"] == 1
    await service.approve(automatic["id"], automatic["fingerprint"])
    assert service.status()["unreadCount"] == 0


@pytest.mark.asyncio
async def test_read_marker_does_not_hide_later_state_or_version_and_legacy_upgrades(
    tmp_path,
):
    store, service = setup(tmp_path)
    saved = await service.propose(proposal("Exact version"))
    service.mark_read(saved["id"])
    first_marker = store.get("proposal-read:" + saved["id"])
    assert first_marker["fingerprint"] == saved["fingerprint"]
    assert not service.get(saved["id"])["unread"]
    with store.connect() as db:
        db.execute("UPDATE proposals SET state='conflict' WHERE id=?", (saved["id"],))
    assert service.get(saved["id"])["unread"] and service.status()["unreadCount"] == 1
    service.mark_all_read()
    assert not service.get(saved["id"])["unread"]
    with store.connect() as db:
        db.execute("UPDATE proposals SET updated=updated+1 WHERE id=?", (saved["id"],))
    assert service.get(saved["id"])["unread"] and service.status()["unreadCount"] == 1
    current = service.get(saved["id"])
    store.set("proposal-read:" + saved["id"], current["updated"])
    assert not service.get(saved["id"])["unread"]
    service.mark_read(saved["id"])
    assert isinstance(store.get("proposal-read:" + saved["id"]), dict)


@pytest.mark.asyncio
async def test_execution_conflict_stays_unread_after_prior_read(tmp_path):
    store, service = setup(tmp_path)
    created = await service.propose(proposal("Initial"), origin="today")
    card = store.entities("commitment")[0]
    change = await service.propose(
        proposal(
            "unused",
            operation="commitment.edit",
            id=card["id"],
            revision=card["revision"],
        )
    )
    service.mark_read(change["id"])
    service.commitments.edit(
        card["id"], CommitmentEdit(revision=card["revision"], title="Changed elsewhere")
    )
    result = await service.approve_all(
        BulkApprove(
            items=[ProposalVersion(id=change["id"], fingerprint=change["fingerprint"])]
        )
    )
    assert result["failed"] and not result["items"]
    assert service.get(change["id"])["unread"]
    assert service.get(change["id"])["state"] == "conflict"
    assert service.get(created["id"])["unread"]
