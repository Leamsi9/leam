"""Authenticated callers plus the actual persisted worker across restarts and races."""

import asyncio
import json
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app
from leam_api.background_jobs import BackgroundJobs
from leam_api.ironclaw import IronClaw
from leam_api.restore_automation import hold_marker

ORIGIN = {"origin": "http://testserver"}
RUN = "7461168f-d5b4-40c5-9fd7-4bd0c277dafb"
GATE = "dadc3609-d7b8-45db-ad0d-776276de212a"


class Wire:
    def __init__(self):
        self.writes = []
        self.owner = "user"
        self.status = "running"
        self.lose = 0
        self.busy = False
        self.session_hook = None
        self.result = "Report saved: /?artifact=report-one"
        self.cancel_hook = None
        self.cancel_reply = {
            "status": "Cancelled",
            "run_id": RUN,
            "already_terminal": False,
            "event_cursor": 1,
        }

    async def handle(self, request):
        path = request.url.path.removeprefix("/api/webchat/v2")
        body = json.loads(request.content) if request.content else None
        if request.method != "GET":
            self.writes.append((path, body))
        if path == "/session":
            if self.session_hook:
                hook, self.session_hook = self.session_hook, None
                await hook()
            return httpx.Response(
                200,
                json={
                    "tenant_id": "tenant",
                    "user_id": self.owner,
                    "session_channel_extension_id": "web",
                },
            )
        if path == "/threads":
            return httpx.Response(
                200,
                json={
                    "thread": {"thread_id": "worker"},
                    "threads": [
                        {"thread_id": "parent", "title": "My chat"},
                        {"thread_id": "worker", "title": "Worker task"},
                    ],
                },
            )
        if path.endswith("/timeline"):
            if "other-owner" in path:
                return httpx.Response(404, json={"error": "missing"})
            return httpx.Response(200, json={"messages": []})
        if path.endswith("/messages"):
            if self.cancel_hook:
                self.cancel_hook()
                self.cancel_hook = None
            if self.lose:
                self.lose -= 1
                raise httpx.ReadTimeout("lost accepted response")
            return httpx.Response(
                200,
                json={
                    "outcome": "deferred_busy" if self.busy else "submitted",
                    "thread_id": body["thread_id"],
                    "run_id": RUN,
                },
            )
        if path.endswith("/cancel"):
            return httpx.Response(200, json=self.cancel_reply)
        if path.endswith("/events"):
            items = [{"run_status": {"run_id": RUN, "status": self.status}}]
            if self.status == "blocked_approval":
                items.append(
                    {
                        "gate": {
                            "run_id": RUN,
                            "gate_kind": "approval",
                            "gate_ref": "gate:approval-" + GATE,
                        }
                    }
                )
            if self.status == "completed":
                items.append(
                    {
                        "text": {
                            "run_id": RUN,
                            "finalized": True,
                            "body": self.result,
                        }
                    }
                )
            frame = {"state": {"thread_id": "worker", "items": items}}
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content="event: projection_update\ndata: " + json.dumps(frame) + "\n\n",
            )
        return httpx.Response(200, json={"items": [], "providers": []})


def setup(tmp_path, wire):
    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="fixture",
        transport=httpx.MockTransport(wire.handle),
    )
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        runtime=runtime,
    )
    return app, TestClient(app, client=("127.0.0.1", 12345))


async def idle(_self):
    await asyncio.Event().wait()


@pytest.fixture(autouse=True)
def manual_worker():
    with patch.object(BackgroundJobs, "run", idle):
        yield


def start(client, **extra):
    payload = {
        "requestId": str(uuid4()),
        "threadId": "parent",
        "title": "Useful report",
        "task": "Write the requested report",
        "context": "Minimal reference only",
        **extra,
    }
    response = client.post("/api/companion/jobs", json=payload, headers=ORIGIN)
    return payload, response


def tick(client, app):
    client.portal.call(app.state.background_jobs.tick)


def current(client, identity):
    return client.get("/api/companion/jobs/" + identity).json()


def test_authenticated_job_idempotency_hidden_worker_and_inbox_completion(tmp_path):
    wire = Wire()
    app, client = setup(tmp_path, wire)
    with client:
        assert client.get("/api/companion/jobs").status_code == 401
        login(client)
        payload, response = start(client)
        identity = payload["requestId"]
        assert response.status_code == 200 and response.json()["state"] == "queued"
        assert (
            client.post("/api/companion/jobs", json=payload, headers=ORIGIN).json()[
                "id"
            ]
            == identity
        )
        assert (
            client.post(
                "/api/companion/jobs",
                json={**payload, "task": "Different"},
                headers=ORIGIN,
            ).status_code
            == 409
        )
        tick(client, app)
        assert current(client, identity)["state"] == "running"
        sent = [body for path, body in wire.writes if path.endswith("/messages")]
        assert len(sent) == 1 and sent[0]["thread_id"] == "worker"
        assert "Minimal reference only" in sent[0]["model_context"]["reference_text"]
        assert [
            row["thread_id"]
            for row in client.get("/api/companion/threads").json()["threads"]
        ] == ["parent"]
        wire.status = "completed"
        tick(client, app)
        result = current(client, identity)
        assert result["state"] == "completed" and "Report saved" in result["result"]
        assert result["inboxId"]
        note = client.get("/api/inbox/" + result["inboxId"]).json()
        assert identity in json.dumps(note) and RUN in json.dumps(note)
        tick(client, app)
        assert len([path for path, _ in wire.writes if path.endswith("/messages")]) == 1
        assert (
            "Write the requested report"
            not in app.state.store.path.read_bytes().decode(errors="ignore")
        )


def test_restart_exact_receipt_recovery_and_no_silent_busy_steering_replay(tmp_path):
    wire = Wire()
    wire.lose = 1
    app, client = setup(tmp_path, wire)
    with client:
        login(client)
        payload, _ = start(client)
        tick(client, app)
        first = next(body for path, body in wire.writes if path.endswith("/messages"))
        with app.state.store.connect() as db:
            db.execute("UPDATE background_jobs SET body=json_set(body,'$.nextTry',0)")
    app2, client2 = setup(tmp_path, wire)
    with client2:
        signed_in = client2.post(
            "/api/auth/login",
            json={"password": "long-password-for-tests"},
            headers=ORIGIN,
        )
        assert signed_in.status_code == 200, signed_in.text
        tick(client2, app2)
        second = [body for path, body in wire.writes if path.endswith("/messages")][1]
        assert first == second
        assert current(client2, payload["requestId"])["state"] == "running"
        wire.status = "completed"
        tick(client2, app2)
        wire.busy = True
        other, _ = start(client2)
        tick(client2, app2)
        assert current(client2, other["requestId"])["state"] == "unknown"
        writes = len(wire.writes)
        tick(client2, app2)
        assert len(wire.writes) == writes


def test_approval_pauses_worker_not_parent_and_cancel_is_exact_run(tmp_path):
    wire = Wire()
    app, client = setup(tmp_path, wire)
    with client:
        login(client)
        payload, _ = start(client)
        identity = payload["requestId"]
        tick(client, app)
        wire.status = "blocked_approval"
        tick(client, app)
        saved = current(client, identity)
        assert saved["state"] == "awaiting_approval" and saved["approvalId"] == GATE
        assert saved["automaticTodayApproval"] is False
        # Approval is worker-scoped. The foreground event/history route remains accessible.
        assert client.get("/api/companion/threads/parent").status_code == 200
        stale = client.post(
            "/api/companion/jobs/" + identity,
            json={"revision": 1, "action": "cancel"},
            headers=ORIGIN,
        )
        assert stale.status_code == 409
        assert (
            client.post(
                "/api/companion/jobs/" + identity,
                json={"revision": saved["revision"], "action": "cancel"},
                headers=ORIGIN,
            ).status_code
            == 200
        )
        tick(client, app)
        assert current(client, identity)["state"] == "cancelled"
        cancelled = [entry for entry in wire.writes if entry[0].endswith("/cancel")]
        assert len(cancelled) == 1 and "/threads/worker/" in cancelled[0][0]
        assert cancelled[0][1]["run_id"] == RUN


def test_cancel_during_dispatch_is_not_overwritten_and_queued_cancel_never_sends(
    tmp_path,
):
    wire = Wire()
    app, client = setup(tmp_path, wire)
    with client:
        login(client)
        payload, _ = start(client)
        identity = payload["requestId"]
        from leam_api.background_jobs import JobAction

        wire.cancel_hook = lambda: app.state.background_jobs.change(
            identity,
            JobAction(
                revision=app.state.background_jobs.get(identity)["revision"],
                action="cancel",
            ),
        )
        tick(client, app)
        assert current(client, identity)["state"] == "cancel_requested"
        tick(client, app)
        assert current(client, identity)["state"] == "cancelled"
        other, response = start(client)
        assert (
            client.post(
                "/api/companion/jobs/" + other["requestId"],
                json={"revision": response.json()["revision"], "action": "cancel"},
                headers=ORIGIN,
            ).status_code
            == 200
        )
        count = len(wire.writes)
        tick(client, app)
        assert len(wire.writes) == count


def test_owner_change_restore_hold_and_nested_jobs_fail_closed(tmp_path):
    wire = Wire()
    app, client = setup(tmp_path, wire)
    with client:
        login(client)
        payload, _ = start(client)
        app.state.store.set("restore_automation_hold", hold_marker())
        tick(client, app)
        assert wire.writes == []
        assert start(client)[1].status_code == 409
        with app.state.store.connect() as db:
            db.execute("DELETE FROM settings WHERE key='restore_automation_hold'")
        tick(client, app)
        assert start(client, threadId="worker")[1].status_code == 409
        wire.owner = "different-user"
        tick(client, app)
        assert current(client, payload["requestId"])["state"] == "unknown"
        assert not any(path.endswith("/cancel") for path, _ in wire.writes)


def test_model_tool_caller_queues_job_but_malformed_nested_and_wrong_parent_reads_fail(
    tmp_path,
):
    wire = Wire()
    app, client = setup(tmp_path, wire)
    with client:
        login(client)
        headers = {
            "authorization": "Bearer " + (tmp_path / "tools-token").read_text().strip(),
        }
        arguments = {
            "action": "start",
            "requestId": str(uuid4()),
            "threadId": "parent",
            "title": "Report",
            "task": "Prepare requested report",
        }
        request = {"tool": "leam_background_job", "arguments": arguments}
        assert client.post("/api/internal/tools", json=request).status_code == 401
        accepted = client.post("/api/internal/tools", json=request, headers=headers)
        assert accepted.status_code == 200 and accepted.json()["state"] == "queued"
        bad = client.post(
            "/api/internal/tools",
            json={
                "tool": "leam_background_job",
                "arguments": {"action": "start", "threadId": "parent"},
            },
            headers=headers,
        )
        assert bad.status_code == 422
        read = client.post(
            "/api/internal/tools",
            json={
                "tool": "leam_background_job",
                "arguments": {
                    "action": "read",
                    "threadId": "other",
                    "id": arguments["requestId"],
                },
            },
            headers=headers,
        )
        assert read.status_code == 404
        tick(client, app)
        saved = current(client, arguments["requestId"])
        tick(client, app)
        stable = current(client, arguments["requestId"])
        # A heartbeat does not manufacture a new approval/cancellation revision.
        tick(client, app)
        assert current(client, arguments["requestId"])["revision"] == stable["revision"]
        assert saved["runId"] == stable["runId"]


def test_finite_dispatch_attempts_and_restart_lease_recovery(tmp_path):
    wire = Wire()
    wire.lose = 4
    app, client = setup(tmp_path, wire)
    with client:
        login(client)
        payload, _ = start(client)
        for _ in range(3):
            tick(client, app)
            with app.state.store.connect() as db:
                db.execute(
                    "UPDATE background_jobs SET body=json_set(body,'$.nextTry',0)"
                )
        assert current(client, payload["requestId"])["state"] == "unknown"
        writes = len(wire.writes)
        tick(client, app)
        assert len(wire.writes) == writes
        # Only an explicit user retry opens another bounded batch, keeping identity.
        saved = current(client, payload["requestId"])
        wire.lose = 0
        response = client.post(
            "/api/companion/jobs/" + payload["requestId"],
            json={"revision": saved["revision"], "action": "retry"},
            headers=ORIGIN,
        )
        assert response.status_code == 200
        tick(client, app)
        assert current(client, payload["requestId"])["state"] == "running"
        identities = {
            body["client_action_id"]
            for path, body in wire.writes
            if path.endswith("/messages")
        }
        assert len(identities) == 1


def test_restart_recovers_terminal_notification_without_new_inference(tmp_path):
    wire = Wire()
    app, client = setup(tmp_path, wire)
    with client:
        login(client)
        payload, _ = start(client)
        tick(client, app)
        wire.status = "completed"

        async def crash_before_notification(_identity):
            raise OSError("simulated process boundary")

        with (
            patch.object(
                app.state.background_jobs, "notify", crash_before_notification
            ),
            pytest.raises(OSError),
        ):
            tick(client, app)
        assert current(client, payload["requestId"])["state"] == "completed"
        assert current(client, payload["requestId"])["inboxId"] is None
    writes = len(wire.writes)
    app2, client2 = setup(tmp_path, wire)
    with client2:
        signed_in = client2.post(
            "/api/auth/login",
            json={"password": "long-password-for-tests"},
            headers=ORIGIN,
        )
        assert signed_in.status_code == 200, signed_in.text
        tick(client2, app2)
        assert current(client2, payload["requestId"])["inboxId"]
        assert len(wire.writes) == writes


def test_pending_domain_change_is_not_completed_and_review_resume_keeps_worker(
    tmp_path,
):
    wire = Wire()
    app, client = setup(tmp_path, wire)
    with client:
        login(client)
        payload, _ = start(client)
        tick(client, app)
        headers = {
            "authorization": "Bearer " + (tmp_path / "tools-token").read_text().strip(),
        }
        proposal = client.post(
            "/api/internal/tools",
            json={
                "tool": "leam_propose",
                "arguments": {
                    "requestId": str(uuid4()),
                    "threadId": "worker",
                    "operation": "commitment.create",
                    "input": {"title": "User requested task"},
                    "reason": "Explicit task from delegated worker",
                },
            },
            headers=headers,
        )
        assert proposal.status_code == 200 and proposal.json()["state"] == "pending"
        wire.status = "completed"
        tick(client, app)
        saved = current(client, payload["requestId"])
        assert saved["state"] == "awaiting_approval"
        assert saved["proposalIds"] == [proposal.json()["id"]]
        action = "/api/companion/jobs/" + payload["requestId"]
        assert (
            client.post(
                action,
                json={"revision": saved["revision"], "action": "resume"},
                headers=ORIGIN,
            ).status_code
            == 409
        )
        accepted = client.post(
            "/api/proposals/" + proposal.json()["id"] + "/approve",
            json={},
            headers=ORIGIN,
        )
        assert accepted.status_code == 200

        async def resume_during_observer():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                cookies=dict(client.cookies),
            ) as browser:
                response = await browser.post(
                    action,
                    json={"revision": saved["revision"], "action": "resume"},
                    headers=ORIGIN,
                )
                assert response.status_code == 200

        wire.session_hook = resume_during_observer
        tick(client, app)
        assert current(client, payload["requestId"])["state"] == "queued"
        tick(client, app)
        turns = [body for path, body in wire.writes if path.endswith("/messages")]
        assert (
            len(turns) == 2
            and turns[0]["thread_id"] == turns[1]["thread_id"] == "worker"
        )
        assert turns[0]["client_action_id"] != turns[1]["client_action_id"]


def test_concurrent_claims_and_time_budget_do_not_spawn_extra_workers(tmp_path):
    wire = Wire()
    app, client = setup(tmp_path, wire)
    with client:
        login(client)
        payload, _ = start(client)
        other, _ = start(client)

        async def together():
            await asyncio.gather(
                app.state.background_jobs.tick(), app.state.background_jobs.tick()
            )

        client.portal.call(together)
        assert (
            len([body for path, body in wire.writes if path.endswith("/messages")]) == 1
        )
        assert current(client, other["requestId"])["state"] == "queued"
        now = app.state.background_jobs.clock()
        app.state.background_jobs.clock = lambda: now + 500
        tick(client, app)
        assert current(client, payload["requestId"])["state"] == "cancel_requested"
        tick(client, app)
        assert current(client, payload["requestId"])["state"] == "cancelled"


def test_pending_cancel_observes_terminal_after_restart_without_replaying_cached_receipt(
    tmp_path,
):
    wire = Wire()
    wire.cancel_reply = {
        "status": "CancelRequested",
        "run_id": RUN,
        "already_terminal": False,
        "event_cursor": 9,
    }
    app, client = setup(tmp_path, wire)
    with client:
        login(client)
        payload, _ = start(client)
        tick(client, app)
        saved = current(client, payload["requestId"])
        assert (
            client.post(
                "/api/companion/jobs/" + payload["requestId"],
                json={"revision": saved["revision"], "action": "cancel"},
                headers=ORIGIN,
            ).status_code
            == 200
        )
        tick(client, app)
        assert current(client, payload["requestId"])["state"] == "cancel_requested"
    wire.status = "cancelled"
    app2, client2 = setup(tmp_path, wire)
    with client2:
        signed_in = client2.post(
            "/api/auth/login",
            json={"password": "long-password-for-tests"},
            headers=ORIGIN,
        )
        assert signed_in.status_code == 200, signed_in.text
        tick(client2, app2)
        assert current(client2, payload["requestId"])["state"] == "cancelled"
        assert len([path for path, _ in wire.writes if path.endswith("/cancel")]) == 1


@pytest.mark.parametrize(
    "invalid",
    [
        {"run_id": str(uuid4())},
        {"event_cursor": -1},
        {"event_cursor": True},
        {"already_terminal": None},
    ],
)
def test_cancel_receipt_requires_exact_identity_and_cursor(tmp_path, invalid):
    wire = Wire()
    wire.cancel_reply.update(invalid)
    app, client = setup(tmp_path, wire)
    with client:
        login(client)
        payload, _ = start(client)
        tick(client, app)
        saved = current(client, payload["requestId"])
        client.post(
            "/api/companion/jobs/" + payload["requestId"],
            json={"revision": saved["revision"], "action": "cancel"},
            headers=ORIGIN,
        )
        tick(client, app)
        saved = current(client, payload["requestId"])
        assert saved["state"] == "cancel_requested" and saved["error"]


def test_unicode_completion_notification_does_not_starve_next_job(tmp_path):
    wire = Wire()
    wire.result = "完成🙂" * 4000
    app, client = setup(tmp_path, wire)
    with client:
        login(client)
        payload, _ = start(client)
        other, _ = start(client)
        tick(client, app)
        wire.status = "completed"
        tick(client, app)
        result = current(client, payload["requestId"])
        assert result["inboxId"] and not result["notificationError"]
        tick(client, app)
        assert current(client, other["requestId"])["state"] == "running"
