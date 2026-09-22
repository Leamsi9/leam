"""Actual Main transport caller and explicit operator completion evidence."""

import asyncio
import json
import subprocess
import sys
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_api import login
from test_backlog_evidence import capture
from test_main_coding import H, connect, select, setup

from leam_api.backlog import Assessment, Backlog
from leam_api.backlog_evidence import BacklogEvidence
from leam_api.backlog_triage import PREFIX, Start, TriageJobs, finish


def ready(tmp_path):
    app, codex = setup(tmp_path)
    backlog = Backlog(app.state.store)
    backlog.upsert(
        Assessment(
            feature="backlog-triage",
            title="Backlog triage",
            currentStep="Review canonical backlog",
            percent=50,
        )
    )
    return app, codex, TestClient(app), backlog


def start(client, identity=None, priorities=""):
    body = {"requestId": identity or str(uuid4()), "priorities": priorities}
    return body, client.post("/api/coding/main/triage", json=body, headers=H)


def mutations(codex):
    return [
        (method, params)
        for method, params in codex.calls
        if method in {"turn/start", "turn/steer"}
    ]


def review(backlog):
    service = BacklogEvidence(backlog)
    request = capture(service)
    return service.prepare(request, dry_run=False)


def test_auth_origin_missing_main_and_accepted_is_not_completion(tmp_path):
    _app, codex, client, _backlog = ready(tmp_path)
    with client:
        assert client.get("/api/coding/main/triage").status_code == 401
        login(client)
        body, rejected = start(client)
        assert (
            rejected.status_code == 409
            and rejected.headers["X-Leam-Action-Reserved"] == "false"
        )
        assert not mutations(codex)
        assert client.post("/api/coding/main/triage", json=body).status_code == 403
        select(client)
        connect(client)
        body, response = start(client, priorities="Reliability first")
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "in_progress"
        sent = mutations(codex)[0][1]
        assert "Reliability first" in sent["input"][0]["text"]
        assert "leam_api.backlog_triage" in sent["input"][0]["text"]
        assert "--request-id " + body["requestId"] in sent["input"][0]["text"]
        assert "leam.agent-protocols" in sent["additionalContext"]
        assert (
            client.get("/api/coding/main/triage").json()["job"]["state"]
            == "in_progress"
        )
        assert start(client)[1].status_code == 409


def test_same_identity_replays_and_changed_payload_never_resends(tmp_path):
    _app, codex, client, _backlog = ready(tmp_path)
    with client:
        login(client)
        select(client)
        connect(client)
        body, first = start(client)
        assert first.status_code == 200
        assert start(client, body["requestId"])[1].json() == first.json()
        assert start(client, body["requestId"], "Different")[1].status_code == 409
        assert len(mutations(codex)) == 1


def test_lost_ack_restart_and_reconcile_never_redispatch(tmp_path):
    app, codex, client, _backlog = ready(tmp_path)
    with client:
        login(client)
        select(client)
        connect(client)
        codex.lose_reply = True
        body, response = start(client)
        assert response.status_code == 502
        assert (
            client.get("/api/coding/main/triage/" + body["requestId"]).json()["state"]
            == "uncertain"
        )
        assert start(client, body["requestId"])[1].json()["state"] == "uncertain"
        restarted = TriageJobs(app.state.main_coding)
        assert restarted.get(body["requestId"])["state"] == "uncertain"
        client.post(
            "/api/coding/main/triage/" + body["requestId"] + "/reconcile",
            json={},
            headers=H,
        )
        assert len(mutations(codex)) == 1


def test_preflight_failure_is_durable_and_later_explicit_new_request_allowed(tmp_path):
    _app, codex, client, _backlog = ready(tmp_path)
    with client:
        login(client)
        select(client)
        body, result = start(
            client
        )  # Not connected: Main caller rejects before dispatch.
        assert result.status_code == 409
        saved = client.get("/api/coding/main/triage/" + body["requestId"]).json()
        assert saved["state"] == "failed"
        assert not mutations(codex)
        connect(client)
        new, result = start(client)
        assert (
            new["requestId"] != body["requestId"]
            and result.json()["state"] == "in_progress"
        )
        assert len(mutations(codex)) == 1


def test_operator_finish_requires_fresh_review_and_accepted_source_bound_delivery(
    tmp_path,
):
    app, _codex, client, backlog = ready(tmp_path)
    with client:
        login(client)
        select(client)
        connect(client)
        body, result = start(client)
        assert result.status_code == 200
        with pytest.raises(ValueError, match="fresh canonical"):
            finish(
                app.state.store,
                body["requestId"],
                "completed",
                "Reviewed and reordered",
            )
        review(backlog)
        saved = app.state.store.get("coding-main-handoff:" + body["requestId"])
        with app.state.store.connect() as db:
            db.execute(
                "UPDATE requests SET state='pending' WHERE id=?",
                (saved["submissionId"],),
            )
        with pytest.raises(ValueError, match="Confirmed Main"):
            finish(
                app.state.store,
                body["requestId"],
                "completed",
                "Reviewed and reordered",
            )
        with app.state.store.connect() as db:
            db.execute(
                "UPDATE requests SET state='complete' WHERE id=?",
                (saved["submissionId"],),
            )
        command = [
            sys.executable,
            "-m",
            "leam_api.backlog_triage",
            "--data-dir",
            str(tmp_path),
            "finish",
            "--request-id",
            body["requestId"],
            "--state",
            "completed",
            "--summary",
            "Reviewed and reordered",
        ]
        complete = subprocess.run(command, capture_output=True, text=True, check=True)
        assert json.loads(complete.stdout)["state"] == "completed"
        again = subprocess.run(command, capture_output=True, text=True, check=True)
        assert json.loads(again.stdout) == json.loads(complete.stdout)
        assert (
            client.get("/api/coding/main/triage").json()["job"]["state"] == "completed"
        )


def test_changed_canonical_review_evidence_rejects_completion(tmp_path):
    app, _codex, client, backlog = ready(tmp_path)
    with client:
        login(client)
        select(client)
        connect(client)
        body, _ = start(client)
        review(backlog)
        backlog.upsert(
            Assessment(
                feature="new-work", title="New work", currentStep="Review me", percent=0
            )
        )
        with pytest.raises(ValueError, match="fresh canonical"):
            finish(app.state.store, body["requestId"], "completed", "Not current")
        assert (
            client.get("/api/coding/main/triage").json()["job"]["state"]
            == "in_progress"
        )


def test_legacy_accepted_handoff_imports_but_does_not_claim_finished(tmp_path):
    app, codex, client, _backlog = ready(tmp_path)
    with client:
        login(client)
        select(client)
        connect(client)
        identity = str(uuid4())
        response = client.post(
            "/api/coding/main/handoffs",
            headers=H,
            json={
                "requestId": identity,
                "mainThreadId": "main",
                "mainRevision": 1,
                "sourceTicketId": "feature:backlog-triage",
                "text": "Please triage the Leam build backlog now. Previously requested review",
                "context": "Explicit user action from the Backlog triage button.",
            },
        )
        assert response.json()["state"] == "accepted"
        assert app.state.store.get(PREFIX + identity) is None
        imported = client.get("/api/coding/main/triage").json()["job"]
        assert (
            imported["requestId"] == identity
            and imported["legacy"] is True
            and imported["state"] == "in_progress"
        )
        assert start(client)[1].status_code == 409
        assert len(mutations(codex)) == 1


def test_slow_main_preflight_cannot_dispatch_after_operator_failure(tmp_path):
    app, codex, client, _backlog = ready(tmp_path)
    with client:
        login(client)
        select(client)
        connect(client)
        original = codex.request
        identity = str(uuid4())

        async def guarded(method, params, **kwargs):
            if method == "thread/turns/list":
                finish(
                    app.state.store,
                    identity,
                    "failed",
                    "Review was withdrawn before sending",
                )
            return await original(method, params, **kwargs)

        codex.request = guarded
        _, response = start(client, identity)
        assert response.status_code == 409
        assert not mutations(codex)
        assert (
            client.get("/api/coding/main/triage/" + identity).json()["state"]
            == "failed"
        )


def test_concurrent_distinct_starts_have_one_durable_main_delivery(tmp_path):
    app, codex, client, _backlog = ready(tmp_path)
    with client:
        login(client)
        select(client)
        connect(client)

        async def both():
            return await asyncio.gather(
                *(
                    TriageJobs(app.state.main_coding).start(Start(requestId=uuid4()))
                    for _ in range(2)
                ),
                return_exceptions=True,
            )

        values = client.portal.call(both)
        assert (
            sum(
                isinstance(value, dict) and value["state"] == "in_progress"
                for value in values
            )
            == 1
        )
        assert len(mutations(codex)) == 1


def test_history_limit_and_terminal_outcome_cannot_be_rewritten(tmp_path):
    app, codex, client, _backlog = ready(tmp_path)
    with client:
        login(client)
        select(client)
        connect(client)
        body, _ = start(client)
        terminal = finish(
            app.state.store, body["requestId"], "failed", "Could not finish review"
        )
        with pytest.raises(ValueError, match="terminal"):
            finish(app.state.store, body["requestId"], "completed", "Changed claim")
        for _ in range(199):
            identity = str(uuid4())
            app.state.store.set(PREFIX + identity, {**terminal, "requestId": identity})
        assert start(client)[1].status_code == 409
        assert len(mutations(codex)) == 1


def test_older_unresolved_legacy_job_remains_visible_after_latest_finishes(tmp_path):
    app, codex, client, _backlog = ready(tmp_path)
    with client:
        login(client)
        select(client)
        connect(client)
        identities = [str(uuid4()), str(uuid4())]
        for identity in identities:
            result = client.post(
                "/api/coding/main/handoffs",
                headers=H,
                json={
                    "requestId": identity,
                    "mainThreadId": "main",
                    "mainRevision": 1,
                    "sourceTicketId": "feature:backlog-triage",
                    "text": "Please triage the Leam build backlog now. Legacy review "
                    + identity,
                    "context": "Explicit user action from the Backlog triage button.",
                },
            )
            assert result.json()["state"] == "accepted"
        latest = client.get("/api/coding/main/triage").json()["job"]
        assert latest["requestId"] == identities[1]
        finish(
            app.state.store, identities[1], "failed", "Latest review did not complete"
        )
        previous = client.get("/api/coding/main/triage").json()["job"]
        assert (
            previous["requestId"] == identities[0]
            and previous["state"] == "in_progress"
        )
        assert len(mutations(codex)) == 2


@pytest.mark.parametrize(
    "source_thread,context,text",
    [
        (
            "source",
            "Explicit user action from the Backlog triage button.",
            "Please triage the Leam build backlog now. Repair its UI",
        ),
        (
            None,
            "Ticket discussion",
            "Please triage the Leam build backlog now. Repair its UI",
        ),
        (
            None,
            "Explicit user action from the Backlog triage button.",
            "Fix the triage status display",
        ),
    ],
)
def test_ticket_discussion_or_coding_handoff_is_not_imported_as_triage(
    tmp_path, source_thread, context, text
):
    app, codex, client, _backlog = ready(tmp_path)
    with client:
        login(client)
        select(client)
        connect(client)
        if source_thread:
            app.state.store.set(
                "ticket-chat:feature:backlog-triage", {"threadId": source_thread}
            )
        identity = str(uuid4())
        response = client.post(
            "/api/coding/main/handoffs",
            headers=H,
            json={
                "requestId": identity,
                "mainThreadId": "main",
                "mainRevision": 1,
                "sourceThreadId": source_thread,
                "sourceTicketId": "feature:backlog-triage",
                "text": text,
                "context": context,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "accepted"
        assert client.get("/api/coding/main/triage").json()["job"] is None
        assert client.get("/api/coding/main/triage/" + identity).status_code == 404
        assert app.state.store.get(PREFIX + identity) is None
        assert len(mutations(codex)) == 1
