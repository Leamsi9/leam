"""Actual proposal callers with SQLite, permission decisions and domain receipts."""

from uuid import uuid4

import pytest
from fastapi import HTTPException

from leam_api import approval_policy
from leam_api.proposals import (
    BulkApprove,
    EditProposal,
    Proposals,
    ProposalVersion,
    Propose,
)
from leam_api.store import Store


def proposal(title="Send invoice", operation="commitment.create", **changes):
    return Propose(
        requestId=uuid4(),
        threadId="today-thread",
        operation=operation,
        input={"title": title, **changes},
        reason="User requested this task",
    )


def setup(tmp_path):
    store = Store(tmp_path)
    return store, Proposals(store, None, None)


@pytest.mark.asyncio
async def test_default_automatic_scoped_only_trusted_today_goals(tmp_path):
    store, service = setup(tmp_path)
    manual = await service.propose(proposal("Generic tool"))
    assert manual["state"] == "pending"
    today = await service.propose(proposal("Today task"), origin="today")
    goals = await service.propose(proposal("Goals task"), origin="goals")
    assert today["state"] == goals["state"] == "complete"
    assert today["review"]["approval"]["origin"] == "today"
    assert len(store.entities("commitment")) == 2


@pytest.mark.asyncio
async def test_policy_cas_and_existing_pending_review_remains_manual(tmp_path):
    store, service = setup(tmp_path)
    body = proposal()
    pending = await service.propose(body)
    policy = approval_policy.get(store)
    assert policy == {
        "revision": 0,
        "todayRequiresApproval": False,
        "goalsRequiresApproval": False,
    }
    updated = approval_policy.update(
        store,
        approval_policy.ApprovalPolicy(**{**policy, "todayRequiresApproval": True}),
    )
    assert updated["revision"] == 1
    with pytest.raises(HTTPException) as error:
        approval_policy.update(store, approval_policy.ApprovalPolicy(**policy))
    assert error.value.status_code == 409
    assert (await service.propose(body, origin="today"))["state"] == "pending"
    assert (await service.propose(proposal("Today manual"), origin="today"))[
        "state"
    ] == "pending"
    assert service.get(pending["id"])["review"]["approval"]["mode"] == "manual"


@pytest.mark.asyncio
async def test_decline_deletes_content_and_suppresses_retries(tmp_path):
    store, service = setup(tmp_path)
    body = proposal("Private declined client name")
    pending = await service.propose(body)
    store.set(
        "today-obligation-source:source",
        {"proposalId": pending["id"], "evidence": "Private declined client name"},
    )
    deleted = await service.decline(pending["id"])
    assert deleted == {"id": pending["id"], "state": "declined", "deleted": True}
    with pytest.raises(HTTPException) as error:
        service.get(pending["id"])
    assert error.value.status_code == 404
    assert service.decision_state(pending["id"]) == "declined"
    assert await service.propose(body) == deleted
    again = await service.propose(proposal("Private declined client name"))
    assert again["deleted"] is True
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM proposals").fetchone()[0] == 0
        assert all(
            "Private declined client name" not in row["value"]
            for row in db.execute("SELECT value FROM settings")
        )


@pytest.mark.asyncio
async def test_edit_replaces_identity_and_rejects_stale_approval(tmp_path):
    _store, service = setup(tmp_path)
    pending = await service.propose(proposal("Wrong title"))
    replacement = await service.edit(
        pending["id"],
        EditProposal(
            fingerprint=pending["fingerprint"], input={"title": "Correct title"}
        ),
    )
    assert replacement["id"] != pending["id"]
    assert replacement["input"]["title"] == "Correct title"
    assert replacement["state"] == "pending"
    assert service.decision_state(pending["id"]) == "superseded"
    with pytest.raises(HTTPException):
        await service.approve(pending["id"])
    await service.approve(replacement["id"], replacement["fingerprint"])


@pytest.mark.asyncio
async def test_read_status_and_global_listing(tmp_path):
    _store, service = setup(tmp_path)
    pending = await service.propose(proposal())
    assert service.status() == {"unreadCount": 1, "pendingCount": 1}
    assert service.list(None, exclude_memory=True)["items"][0]["unread"] is True
    assert service.mark_read(pending["id"])["unread"] is False
    assert service.status() == {"unreadCount": 0, "pendingCount": 1}


@pytest.mark.asyncio
async def test_bulk_approves_only_reviewed_fingerprints_and_returns_partial(tmp_path):
    store, service = setup(tmp_path)
    first = await service.propose(proposal("First"))
    second = await service.propose(proposal("Second"))
    result = await service.approve_all(
        BulkApprove(
            items=[
                ProposalVersion(id=first["id"], fingerprint=first["fingerprint"]),
                ProposalVersion(id=second["id"], fingerprint="0" * 64),
            ]
        )
    )
    assert [item["id"] for item in result["items"]] == [first["id"]]
    assert result["failed"][0]["id"] == second["id"]
    assert len(store.entities("commitment")) == 1
    assert service.get(second["id"])["state"] == "pending"


@pytest.mark.asyncio
async def test_decline_content_hook_and_tombstone_are_atomic(tmp_path):
    _store, service = setup(tmp_path)
    pending = await service.propose(proposal())

    def fail(db, proposal_id):
        raise ValueError("job cleanup failed")

    service.on_content_deleted = fail
    with pytest.raises(ValueError):
        await service.decline(pending["id"])
    assert service.get(pending["id"])["state"] == "pending"
    assert service.decision_state(pending["id"]) is None


@pytest.mark.asyncio
async def test_coding_handoff_never_auto_or_bulk_approves(tmp_path):
    _store, service = setup(tmp_path)
    body = Propose(
        requestId=uuid4(),
        threadId="today",
        operation="coding.handoff",
        input={"title": "Fix app", "instructions": "Fix the app"},
        reason="User requested coding",
    )
    saved = await service.propose(body, origin="today")
    assert saved["state"] == "pending"
    result = await service.approve_all(
        BulkApprove(
            items=[ProposalVersion(id=saved["id"], fingerprint=saved["fingerprint"])]
        )
    )
    assert result == {"items": [], "failed": [], "skipped": [saved["id"]]}
    with pytest.raises(HTTPException):
        await service.approve(saved["id"])


def test_authenticated_approval_routes_and_origin_cannot_be_forged(tmp_path):
    from fastapi.testclient import TestClient
    from test_api import FakeCodex, login

    from leam_api.app import create_app

    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    headers = {"origin": "http://testserver"}
    with TestClient(app) as client:
        assert client.get("/api/proposals/status").status_code == 401
        login(client)
        data = proposal().model_dump(mode="json")
        assert (
            client.post(
                "/api/proposals", json={**data, "origin": "today"}, headers=headers
            ).status_code
            == 422
        )
        pending = client.post("/api/proposals", json=data, headers=headers).json()
        assert pending["state"] == "pending"
        assert (
            client.get("/api/proposals?excludeMemory=true").json()["items"][0]["id"]
            == pending["id"]
        )
        assert client.get("/api/proposals/status").json()["pendingCount"] == 1
        assert (
            client.post(
                "/api/proposals/" + pending["id"] + "/read", json={}, headers=headers
            ).json()["unread"]
            is False
        )
        edited = client.patch(
            "/api/proposals/" + pending["id"],
            json={
                "fingerprint": pending["fingerprint"],
                "input": {"title": "Edited task"},
            },
            headers=headers,
        )
        assert edited.status_code == 200, edited.text
        saved = edited.json()
        assert saved["id"] != pending["id"]
        bulk = client.post(
            "/api/proposals/approve-all",
            json={"items": [{"id": saved["id"], "fingerprint": saved["fingerprint"]}]},
            headers=headers,
        )
        assert bulk.status_code == 200, bulk.text
        assert bulk.json()["items"][0]["state"] == "complete"
        policy = client.get("/api/proposals/policy").json()
        assert policy["todayRequiresApproval"] is False
        assert (
            client.put(
                "/api/proposals/policy",
                json={**policy, "todayRequiresApproval": True},
                headers=headers,
            ).status_code
            == 200
        )


@pytest.mark.asyncio
async def test_edit_never_auto_applies_after_memory_policy_change(tmp_path):
    from leam_api import memory_policy

    store, service = setup(tmp_path)
    memory_policy.update(
        store, memory_policy.MemoryPolicy(revision=0, createRequiresApproval=True)
    )
    saved = await service.propose(
        Propose(
            requestId=uuid4(),
            threadId="memory",
            operation="memory.create",
            input={"text": "Original memory", "source": "User"},
            reason="User requested",
        )
    )
    memory_policy.update(
        store, memory_policy.MemoryPolicy(revision=1, createRequiresApproval=False)
    )
    fresh = await service.edit(
        saved["id"],
        EditProposal(
            fingerprint=saved["fingerprint"],
            input={"text": "Edited memory", "source": "User"},
        ),
    )
    assert fresh["state"] == "pending"
    assert store.entities("memory") == []


@pytest.mark.asyncio
async def test_distinct_memories_not_suppressed_by_deleted_memory(tmp_path):
    from leam_api import memory_policy

    store, service = setup(tmp_path)
    memory_policy.update(
        store, memory_policy.MemoryPolicy(revision=0, createRequiresApproval=True)
    )
    saved = await service.propose(
        Propose(
            requestId=uuid4(),
            threadId="memory",
            operation="memory.create",
            input={"text": "Likes tea", "source": "User"},
            reason="User requested",
        )
    )
    await service.decline(saved["id"])
    other = await service.propose(
        Propose(
            requestId=uuid4(),
            threadId="memory",
            operation="memory.create",
            input={"text": "Likes coffee", "source": "User"},
            reason="User requested",
        )
    )
    assert other["state"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("with_marker", [False, True])
async def test_decision_reads_never_write_while_worker_holds_transaction(
    tmp_path, with_marker
):
    store, service = setup(tmp_path)
    saved = await service.propose(proposal())
    if with_marker:
        await service.decline(saved["id"])
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO settings VALUES ('worker-held-transaction','true')")
        assert service.decision_state(saved["id"]) == (
            "declined" if with_marker else None
        )
