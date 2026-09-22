# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor

from test_accounts import H
from test_email import consent, mailbox  # noqa: F401

from leam_api.agenda import source_key
from leam_api.approval_policy import ApprovalPolicy, update


def prepared(mailbox):
    client, app, provider, account = mailbox
    consent(client, provider, account)
    assert (
        client.post(f"/api/email/accounts/{account}/sync", headers=H).status_code == 200
    )
    item = client.get("/api/email").json()["items"][0]
    reviewed = client.put(
        "/api/email/triage/review",
        headers=H,
        json={
            "accountId": account,
            "messageId": item["id"],
            "reviewRevision": item["reviewRevision"],
            "decision": "action",
            "kind": "todo",
            "action": "Prepare the response",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    key = source_key("email", account, item["id"])
    return client, app, provider, account, item, key


def test_explicit_conversion_retries_reload_concurrency_and_source_provenance(mailbox):
    client, _app, provider, _, item, key = prepared(mailbox)
    calls = len(provider["calls"])
    preview = client.get("/api/inbox-mail/task", params={"key": key})
    assert preview.status_code == 200 and preview.json()["state"] == "new"
    capacity = client.post("/api/capacities", headers=H, json={"name": "Work"}).json()
    body = {"key": key, "title": "Review the plan", "capacityId": capacity["id"]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: client.post("/api/inbox-mail/task", headers=H, json=body),
                range(2),
            )
        )
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    first = responses[0].json()
    assert first == responses[1].json() and first["state"] == "complete"
    assert client.get("/api/inbox-mail/task", params={"key": key}).json() == first
    tasks = client.get("/api/commitments").json()["items"]
    assert len(tasks) == 1 and tasks[0]["capacityId"] == capacity["id"]
    assert item["url"] in tasks[0]["notes"]
    assert len(provider["calls"]) == calls
    assert (
        client.post(
            "/api/inbox-mail/task", headers=H, json={**body, "title": "A duplicate"}
        ).status_code
        == 409
    )
    assert client.post("/api/inbox-mail/task", headers=H, json=body).json() == first


def test_manual_policy_pending_then_approve_or_decline_never_recreates(mailbox):
    client, app, _provider, _, _, key = prepared(mailbox)
    update(app.state.store, ApprovalPolicy(revision=0, todayRequiresApproval=True))
    body = {"key": key, "title": "Explicit request", "capacityId": None}
    result = client.post("/api/inbox-mail/task", headers=H, json=body).json()
    assert result["state"] == "pending"
    assert client.get("/api/commitments").json()["items"] == []
    proposal = client.get("/api/proposals/" + result["proposalId"]).json()
    assert proposal["review"]["approval"]["mode"] == "manual"
    assert (
        client.post(
            "/api/proposals/" + result["proposalId"] + "/decline", headers=H, json={}
        ).status_code
        == 200
    )
    retry = client.post("/api/inbox-mail/task", headers=H, json=body)
    assert retry.status_code == 200 and retry.json()["state"] == "declined"
    assert app.state.store.get("email-task:v1:" + key) is None
    assert client.get("/api/commitments").json()["items"] == []


def test_origin_account_and_classification_boundaries(mailbox):
    client, _app, _provider, account, item, key = prepared(mailbox)
    body = {"key": key, "title": "Task"}
    assert client.post("/api/inbox-mail/task", json=body).status_code == 403
    assert (
        client.post(
            "/api/inbox-mail/task", headers=H, json={**body, "key": "email:" + "0" * 64}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/inbox-mail/task",
            headers=H,
            json={**body, "origin": "today", "notes": "Injected content"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/inbox-mail/task", headers=H, json={**body, "capacityId": "missing"}
        ).status_code
        == 404
    )
    fresh = client.get("/api/email").json()["items"][0]
    assert (
        client.put(
            "/api/email/triage/review",
            headers=H,
            json={
                "accountId": account,
                "messageId": item["id"],
                "reviewRevision": fresh["reviewRevision"],
                "decision": "ignore",
            },
        ).status_code
        == 200
    )
    assert client.post("/api/inbox-mail/task", headers=H, json=body).status_code == 409
    assert client.delete("/api/email/accounts/" + account, headers=H).status_code == 200
    assert client.post("/api/inbox-mail/task", headers=H, json=body).status_code == 404
    assert client.get("/api/commitments").json()["items"] == []


def test_restart_after_reservation_reuses_the_same_proposal(mailbox, monkeypatch):
    client, app, _, _, _, key = prepared(mailbox)
    original = app.state.proposals.propose

    async def fail(*args, **kwargs):
        from fastapi import HTTPException

        raise HTTPException(503, "Synthetic interrupted dispatch")

    monkeypatch.setattr(app.state.proposals, "propose", fail)
    body = {"key": key, "title": "Durable request", "capacityId": None}
    assert client.post("/api/inbox-mail/task", headers=H, json=body).status_code == 503
    preview = client.get("/api/inbox-mail/task", params={"key": key}).json()
    assert preview["state"] == "prepared"
    monkeypatch.setattr(app.state.proposals, "propose", original)
    result = client.post("/api/inbox-mail/task", headers=H, json=body).json()
    assert (
        result["state"] == "complete" and result["proposalId"] == preview["proposalId"]
    )
    assert len(client.get("/api/commitments").json()["items"]) == 1
