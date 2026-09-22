"""Worker/caller tests use canonical ThreadMessageRecord and scoped timeline shapes.

Contract: ironclaw_threads/contract.rs and reborn_services/types.rs, pinned runtime.
"""

import asyncio
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi import HTTPException

from leam_api.store import Store
from leam_api.today_reconciliation import Retry, TodayReconciliation


class Runtime:
    def __init__(self, now):
        self.now = now
        self.owner = {"tenant_id": "tenant-a", "user_id": "user-a"}
        self.messages = []
        self.calls = []
        self.fail = False

    async def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self.fail:
            raise RuntimeError("private provider payload")
        if path == "/session":
            return self.owner
        query = parse_qs(urlsplit(path).query)
        end = int(query.get("cursor", [len(self.messages)])[0])
        start = max(0, end - int(query.get("limit", [100])[0]))
        return {
            "thread": {
                "thread_id": "thread-a",
                "scope": {
                    "tenant_id": self.owner["tenant_id"],
                    "owner_user_id": self.owner["user_id"],
                },
            },
            "messages": self.messages[start:end],
            "next_cursor": str(start) if start else None,
        }

    def message(self, kind, number, *, status=None, run="run-a", turn="turn-a"):
        stamp = datetime.fromtimestamp(self.now + 1, UTC).isoformat()
        item = {
            "message_id": f"message-{number}",
            "thread_id": "thread-a",
            "sequence": number,
            "kind": kind,
            "status": status or ("submitted" if kind == "user" else "finalized"),
            "turn_id": turn if kind == "user" else None,
            "turn_run_id": run,
            "created_at": stamp,
            "updated_at": stamp,
            "content": "My personal action" if kind == "user" else "Response",
        }
        self.messages.append(item)
        return item


class Domain:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.result = {
            "outcome": "no_action",
            "proposalIds": [],
            "findings": [],
            "clarification": None,
        }

    async def reconcile(self, context):
        self.calls.append(context)
        if self.fail:
            raise ValueError("sensitive error")
        return self.result


def setup(tmp_path):
    now = 2_000_000_000.0
    store = Store(tmp_path)
    store.set(
        "companion-agenda:thread-a", {"date": "2033-05-18", "timezone": "Europe/London"}
    )
    runtime, domain = Runtime(now), Domain()
    manager = TodayReconciliation(
        store, runtime, None, clock=lambda: now, reconciler_factory=lambda *args: domain
    )
    return manager, runtime, domain


@pytest.mark.asyncio
async def test_completed_exchange_durable_once_and_no_source_content(tmp_path):
    manager, runtime, domain = setup(tmp_path)
    runtime.message("user", 1)
    runtime.message("assistant", 2)
    await manager.tick()
    await manager.tick()
    items = (await manager.status("thread-a"))["items"]
    assert len(items) == len(domain.calls) == 1
    assert items[0]["state"] == "done" and items[0]["outcome"] == "no_action"
    assert domain.calls[0]["turn_id"] == "turn-a"
    assert domain.calls[0]["source_refs"]["userMessageId"] == "message-1"
    with manager.store.connect() as db:
        assert "My personal action" not in " ".join(
            str(tuple(row))
            for row in db.execute("SELECT * FROM today_reconciliation_jobs")
        )


@pytest.mark.asyncio
async def test_interrupted_stream_only_finalization_triggers_and_restart_recovers(
    tmp_path,
):
    manager, runtime, domain = setup(tmp_path)
    runtime.message("user", 1)
    draft = runtime.message("assistant", 2, status="draft")
    await manager.tick()
    assert not domain.calls
    assert (await manager.status("thread-a"))["items"][0]["state"] == "waiting_exchange"
    draft["status"] = "finalized"
    resumed = TodayReconciliation(
        manager.store,
        runtime,
        None,
        clock=lambda: runtime.now + 60,
        reconciler_factory=lambda *args: domain,
    )
    await resumed.tick()
    assert len(domain.calls) == 1


@pytest.mark.asyncio
async def test_failure_bounded_and_retry_cas_never_resends(tmp_path):
    manager, runtime, domain = setup(tmp_path)
    runtime.message("user", 1)
    runtime.message("assistant", 2)
    domain.fail = True
    for n in range(4):
        manager.clock = lambda n=n: runtime.now + n * 300
        await manager.tick()
    item = (await manager.status("thread-a"))["items"][0]
    assert item["state"] == "failed" and item["attempts"] == 3
    assert "sensitive" not in str(item)
    request = Retry(revision=item["revision"], requestId=uuid4())
    await manager.retry(item["id"], request)
    await manager.retry(item["id"], request)
    with pytest.raises(HTTPException) as error:
        await manager.retry(
            item["id"], Retry(revision=item["revision"], requestId=uuid4())
        )
    assert error.value.status_code == 409
    domain.fail = False
    await manager.tick()
    assert (await manager.status("thread-a"))["items"][0]["state"] == "done"
    assert all(method == "GET" for method, _, _ in runtime.calls)


@pytest.mark.asyncio
async def test_changed_owner_blocks_before_classification(tmp_path):
    manager, runtime, domain = setup(tmp_path)
    await manager.watch("thread-a")
    runtime.owner["user_id"] = "other-user"
    runtime.message("user", 1)
    runtime.message("assistant", 2)
    with pytest.raises(HTTPException):
        await manager.status("thread-a")
    await manager.tick()
    assert not domain.calls


@pytest.mark.asyncio
async def test_pagination_concurrency_and_tool_exclusion(tmp_path):
    manager, runtime, domain = setup(tmp_path)
    for n in range(120):
        runtime.message("user", n * 3, run=f"run-{n}", turn=f"turn-{n}")
        runtime.message("tool_result_reference", n * 3 + 1, run=f"run-{n}")
        runtime.message("assistant", n * 3 + 2, run=f"run-{n}")
    await asyncio.gather(manager.tick(), manager.tick())
    for _ in range(125):
        manager.clock = lambda: runtime.now + 300
        await manager.tick()
    with manager.store.connect() as db:
        rows = db.execute("SELECT * FROM today_reconciliation_jobs").fetchall()
    assert len(rows) == len(domain.calls) == 120
    assert all(row["state"] == "done" for row in rows)
    assert all("tool" not in str(context) for context in domain.calls)


@pytest.mark.asyncio
async def test_inflight_activation_coverage_excludes_completed_old_history(tmp_path):
    manager, runtime, domain = setup(tmp_path)
    old_stamp = datetime.fromtimestamp(runtime.now - 60, UTC).isoformat()
    for row in [
        runtime.message("user", 1, run="old", turn="old"),
        runtime.message("assistant", 2, run="old"),
    ]:
        row.update(created_at=old_stamp, updated_at=old_stamp)
    user = runtime.message("user", 3)
    user.update(created_at=old_stamp, updated_at=old_stamp)
    runtime.message("assistant", 4)
    await manager.tick()
    assert len(domain.calls) == 1 and domain.calls[0]["turn_id"] == "turn-a"


@pytest.mark.asyncio
async def test_scope_mismatch_and_failed_scan_are_visible_and_retryable(tmp_path):
    from leam_api.today_reconciliation import ScanRetry

    manager, runtime, domain = setup(tmp_path)
    await manager.watch("thread-a")
    original = runtime.request

    async def wrong_scope(method, path, body=None):
        result = await original(method, path, body)
        if "/timeline" in path:
            result["thread"]["scope"]["owner_user_id"] = "other-user"
        return result

    runtime.request = wrong_scope
    for n in range(3):
        manager.clock = lambda n=n: runtime.now + n * 60
        await manager.tick()
    assert not domain.calls
    runtime.request = original
    status = await manager.status("thread-a")
    assert status["coverage"]["error"] and status["coverage"]["retryable"]
    assert await manager.retry_scan(
        ScanRetry(threadId="thread-a", requestId=uuid4())
    ) == {"queued": True}
    await manager.tick()
    assert (await manager.status("thread-a"))["coverage"]["error"] is None


@pytest.mark.asyncio
async def test_expired_lease_recovers_and_busy_does_not_consume_attempt(tmp_path):
    from leam_api.obligation_reconciliation import ReconciliationBusy

    manager, runtime, domain = setup(tmp_path)
    runtime.message("user", 1)
    runtime.message("assistant", 2)
    original = domain.reconcile

    async def busy(context):
        raise ReconciliationBusy()

    domain.reconcile = busy
    await manager.tick()
    item = (await manager.status("thread-a"))["items"][0]
    assert item["attempts"] == 0 and item["state"] == "queued"
    owner, _ = await manager.owner()
    manager.clock = lambda: runtime.now + 5
    claimed = manager.claim(owner)
    assert claimed
    domain.reconcile = original
    manager.clock = lambda: runtime.now + 300
    await manager.tick()
    item = (await manager.status("thread-a"))["items"][0]
    assert item["state"] == "done" and item["attempts"] == 2


def test_actual_send_registers_before_dispatch_and_status_auth(tmp_path):
    import time

    from fastapi.testclient import TestClient
    from test_api import FakeCodex, login

    from leam_api.app import create_app

    runtime = Runtime(time.time())
    runtime.owner["session_channel_extension_id"] = "web"
    domain = Domain()
    calls = []
    original = runtime.request
    app = None

    async def request(method, path, body=None):
        if path.endswith("/messages"):
            with app.state.store.connect() as db:
                assert db.execute(
                    "SELECT 1 FROM today_reconciliation_threads WHERE thread_id='thread-a'"
                ).fetchone()
                assert db.execute(
                    "SELECT 1 FROM runtime_actions WHERE id=?",
                    (body["client_action_id"],),
                ).fetchone()
            calls.append(body)
            runtime.now = time.time()
            runtime.message("user", 1)
            runtime.message("assistant", 2)
            return {
                "outcome": "Started",
                "thread_id": "thread-a",
                "run_id": "run-a",
                "accepted_message_ref": "msg:message-1",
            }
        if path == "/session" or "/timeline" in path:
            return await original(method, path, body)
        return {}

    async def close():
        pass

    runtime.request, runtime.close = request, close
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        runtime=runtime,
    )
    app.state.reconciliation.factory = lambda *args: domain
    app.state.store.set(
        "companion-agenda:thread-a", {"date": "2026-09-21", "timezone": "Europe/London"}
    )
    with TestClient(app) as client:
        assert (
            client.get("/api/agenda/reconciliation?threadId=thread-a").status_code
            == 401
        )
        login(client)
        body = {"text": "I must send my invoice", "requestId": str(uuid4())}
        result = client.post(
            "/api/companion/threads/thread-a/messages",
            json=body,
            headers={"Origin": "http://testserver"},
        )
        assert result.status_code == 200, result.text
        # Completion wakes discovery but must not bypass its persisted cadence.
        app.state.reconciliation.clock = lambda: runtime.now + 11
        client.portal.call(app.state.reconciliation.tick)
        status = client.get("/api/agenda/reconciliation?threadId=thread-a")
        assert status.status_code == 200
        assert status.json()["items"][0]["state"] == "done"
        assert len(calls) == len(domain.calls) == 1
        # Duplicate original delivery uses its existing receipt; no extra send/check.
        assert (
            client.post(
                "/api/companion/threads/thread-a/messages",
                json=body,
                headers={"Origin": "http://testserver"},
            ).status_code
            == 200
        )
        assert len(calls) == 1
        runtime.owner["user_id"] = "other-user"
        assert (
            client.get("/api/agenda/reconciliation?threadId=thread-a").status_code
            == 403
        )
        assert (
            client.post(
                "/api/companion/threads/thread-a/messages",
                json={**body, "requestId": str(uuid4())},
                headers={"Origin": "http://testserver"},
            ).status_code
            == 403
        )
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_pending_and_executing_are_not_confirmed_complete(tmp_path):
    import json

    manager, runtime, domain = setup(tmp_path)
    proposal_id = str(uuid4())
    with manager.store.connect() as db:
        db.execute(
            "INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                proposal_id,
                "fingerprint",
                "thread-a",
                "commitment.create",
                "{}",
                "{}",
                "Review",
                "pending",
                None,
                None,
                runtime.now,
                runtime.now,
            ),
        )
    runtime.message("user", 1)
    runtime.message("assistant", 2)
    domain.result = {
        "outcome": "proposals_pending",
        "proposalIds": [proposal_id],
        "findings": [],
        "clarification": None,
    }
    await manager.tick()
    owner, _ = await manager.owner()
    for state in ("pending", "executing"):
        with manager.store.connect() as db:
            db.execute("UPDATE proposals SET state=? WHERE id=?", (state, proposal_id))
        manager.refresh(owner)
        assert (await manager.status("thread-a"))["items"][0][
            "outcome"
        ] == "proposals_pending"
    with manager.store.connect() as db:
        db.execute(
            "UPDATE proposals SET state='complete',result=? WHERE id=?",
            (
                json.dumps({"record": {"id": "commitment-a", "revision": 1}}),
                proposal_id,
            ),
        )
    manager.refresh(owner)
    assert (await manager.status("thread-a"))["items"][0][
        "outcome"
    ] == "changes_confirmed_complete"
    assert len(domain.calls) == 1


@pytest.mark.asyncio
async def test_preactivation_draft_finalizes_outside_latest_page_after_restart(
    tmp_path,
):
    manager, runtime, domain = setup(tmp_path)
    old_stamp = datetime.fromtimestamp(runtime.now - 60, UTC).isoformat()
    user = runtime.message("user", 1)
    draft = runtime.message("assistant", 2, status="draft")
    for row in (user, draft):
        row.update(created_at=old_stamp, updated_at=old_stamp)
    for index in range(3, 205):
        runtime.message("tool_result_reference", index)
    await manager.tick()
    assert not domain.calls
    assert (await manager.status("thread-a"))["items"] == []
    draft["status"] = "finalized"
    draft["updated_at"] = datetime.fromtimestamp(runtime.now + 60, UTC).isoformat()
    resumed = TodayReconciliation(
        manager.store,
        runtime,
        None,
        clock=lambda: runtime.now + 120,
        reconciler_factory=lambda *args: domain,
    )
    await resumed.tick()
    status = await resumed.status("thread-a")
    assert len(domain.calls) == 1
    assert status["items"][0]["state"] == "done"
    assert status["coverage"]["state"] == "idle"
    assert domain.calls[0]["completed_at"] == draft["updated_at"]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["turn_id", "turn_run_id"])
async def test_submitted_turn_missing_identity_is_visible_not_success(
    tmp_path, missing
):
    manager, runtime, domain = setup(tmp_path)
    user = runtime.message("user", 1)
    user[missing] = None
    runtime.message("assistant", 2)
    for n in range(3):
        manager.clock = lambda n=n: runtime.now + n * 60
        await manager.tick()
    status = await manager.status("thread-a")
    assert not domain.calls
    assert status["coverage"]["state"] == "failed"
    assert status["coverage"]["error"] and status["coverage"]["retryable"]
    assert not any(item["state"] == "done" for item in status["items"])


@pytest.mark.asyncio
async def test_late_declined_result_cannot_restore_derived_content(tmp_path):
    from types import SimpleNamespace

    manager, runtime, domain = setup(tmp_path)
    proposal_id = str(uuid4())
    manager.proposals = SimpleNamespace(
        decision_state=lambda key: "declined" if key == proposal_id else None
    )
    runtime.message("user", 1)
    runtime.message("assistant", 2)
    with manager.store.connect() as db:
        db.execute(
            "INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                proposal_id,
                "fingerprint",
                "thread-a",
                "commitment.create",
                "{}",
                "{}",
                "Review",
                "pending",
                None,
                None,
                runtime.now,
                runtime.now,
            ),
        )

    async def declined_after_extraction(context):
        with manager.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM proposals WHERE id=?", (proposal_id,))
            manager.forget_proposal_content(db, proposal_id)
        return {
            "outcome": "proposals_pending",
            "proposalIds": [proposal_id],
            "clarification": "Private declined task content",
            "findings": [
                {
                    "proposalId": proposal_id,
                    "evidence": "Private declined task content",
                    "kind": "action",
                    "confidence": 1,
                }
            ],
        }

    domain.reconcile = declined_after_extraction
    await manager.tick()
    item = (await manager.status("thread-a"))["items"][0]
    assert item["outcome"] == "no_action" and item["clarification"] is None
    with manager.store.connect() as db:
        saved = db.execute("SELECT result FROM today_reconciliation_jobs").fetchone()[0]
    assert "Private declined task content" not in saved


@pytest.mark.asyncio
async def test_wake_storm_preserves_scan_cadence_and_eventual_finalization(tmp_path):
    manager, runtime, domain = setup(tmp_path)
    runtime.message("user", 1)
    draft = runtime.message("assistant", 2, status="draft")
    await manager.tick()
    calls = len(runtime.calls)
    draft["status"] = "finalized"
    for _ in range(100):
        manager.notify("thread-a")
        await manager.tick()
    assert len(runtime.calls) == calls
    assert not domain.calls
    manager.clock = lambda: runtime.now + 10
    await manager.tick()
    assert len(domain.calls) == 1
    assert (await manager.status("thread-a"))["items"][0]["state"] == "done"


@pytest.mark.asyncio
async def test_owner_backoff_survives_wakes_and_restart_without_consuming_jobs(tmp_path):
    from leam_api.today_reconciliation import OWNER_RETRY

    manager, runtime, domain = setup(tmp_path)
    runtime.message("user", 1)
    runtime.message("assistant", 2)
    runtime.fail = True
    await manager.tick()
    assert len(runtime.calls) == 1
    assert manager.store.get(OWNER_RETRY)["nextRetryAt"] == runtime.now + 30
    resumed = TodayReconciliation(
        manager.store, runtime, None, clock=lambda: runtime.now + 29,
        reconciler_factory=lambda *args: domain,
    )
    for _ in range(100):
        resumed.notify("thread-a")
        await resumed.tick()
    assert len(runtime.calls) == 1 and not domain.calls
    runtime.fail = False
    coverage = (await resumed.status("thread-a"))["coverage"]
    assert coverage["error"] and coverage["nextRetryAt"] == runtime.now + 30
    resumed.clock = lambda: runtime.now + 30
    await resumed.tick()
    assert len(domain.calls) == 1
    assert resumed.store.get(OWNER_RETRY) is None
    assert (await resumed.status("thread-a"))["items"][0]["state"] == "done"


@pytest.mark.asyncio
async def test_wakes_do_not_reset_failed_scan_retry_deadline(tmp_path):
    manager, runtime, domain = setup(tmp_path)
    await manager.watch("thread-a")
    original = runtime.request

    async def failed_timeline(method, path, body=None):
        if "/timeline" in path:
            raise HTTPException(429, "private response detail")
        return await original(method, path, body)

    runtime.request = failed_timeline
    await manager.tick()
    with manager.store.connect() as db:
        first = dict(db.execute("SELECT * FROM today_reconciliation_threads").fetchone())
    for _ in range(100):
        manager.notify("thread-a")
        await manager.tick()
    with manager.store.connect() as db:
        later = dict(db.execute("SELECT * FROM today_reconciliation_threads").fetchone())
    assert later == first
    assert later["failures"] == 1 and later["next_scan"] == runtime.now + 30
    assert "private" not in later["error"] and not domain.calls


@pytest.mark.asyncio
async def test_running_worker_coalesces_wakes_with_minimum_cadence(tmp_path, monkeypatch):
    import leam_api.today_reconciliation as module

    manager, _, _ = setup(tmp_path)
    monkeypatch.setattr(module, "WORKER_INTERVAL", 0.05)
    first, second = asyncio.Event(), asyncio.Event()
    ticks = []
    original = manager.tick

    async def observed_tick():
        ticks.append(asyncio.get_running_loop().time())
        await original()
        (first if len(ticks) == 1 else second).set()

    manager.tick = observed_tick
    task = asyncio.create_task(manager.run())
    try:
        await asyncio.wait_for(first.wait(), 1)
        for _ in range(100):
            manager.notify("thread-a")
        await asyncio.wait_for(second.wait(), 1)
        assert ticks[1] - ticks[0] >= 0.045
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
