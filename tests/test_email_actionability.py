import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from test_agenda import DAY, H, application
from test_agenda_email import seed_mail
from test_api import login


def decision(key, *, state="action", kind="todo", evidence="PRIVATE SNIPPET"):
    return {
        "id": key,
        "state": state,
        "kind": kind if state == "action" else None,
        "basis": "explicit" if state == "action" else None,
        "reason": "Source identifies a requested task",
        "action": "Complete requested task" if state == "action" else "",
        "evidence": evidence,
    }


def install_model(app, outputs, *, before_result=None):
    calls = []

    async def handle(request):
        calls.append(request)
        if request.url.path.endswith("/llm/providers"):
            return httpx.Response(
                200,
                json={
                    "active": {
                        "model": "selected-main-model",
                        "provider_id": "configured",
                    }
                },
            )
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert (
            body["model"] == "selected-main-model"
            and body["tools"] == []
            and body["stream"] is False
        )
        assert "tool_choice" not in body
        assert body["response_format"]["type"] == "json_schema"
        assert "untrusted" in body["messages"][0]["content"].lower()
        inputs = json.loads(body["messages"][1]["content"])["messages"]
        if before_result:
            before_result()
        result = outputs(inputs)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"decisions": result}),
                        }
                    }
                ]
            },
        )

    app.state.emails.classifier.runtime.client = httpx.AsyncClient(
        base_url="http://127.0.0.1:46410", transport=httpx.MockTransport(handle)
    )
    app.state.emails.classifier.runtime.token = "fixture"
    return calls


def settled(client):
    for _ in range(100):
        data = client.get("/api/email/triage").json()
        if data["classification"]["state"] != "running":
            return data
        time.sleep(0.01)
    raise AssertionError("classification did not settle")


def test_unclassified_mail_stays_outside_today_and_explicit_triage_is_tool_free(
    tmp_path,
):
    app, _, _ = application(tmp_path)
    seed_mail(app, count=3, classified=False)
    with TestClient(app) as c:
        login(c)
        calls = install_model(
            app,
            lambda rows: [
                decision(x["id"], state=("action", "ignore", "review")[i])
                for i, x in enumerate(rows)
            ],
        )
        assert c.get("/api/agenda", params=DAY).json()["emails"] == []
        assert not calls
        assert c.post("/api/email/triage").status_code == 403
        assert c.post("/api/email/triage", headers=H).status_code == 202
        result = settled(c)
        assert result["classification"]["counts"] == {
            "action": 1,
            "ignore": 1,
            "review": 1,
            "pending": 0,
        }
        data = c.get("/api/agenda", params=DAY).json()
        assert len(data["emails"]) == data["total"]["emails"] == 1
        assert data["emails"][0]["actionability"]["evidence"] == "PRIVATE SNIPPET"
        assert len(calls) == 2
        assert c.post("/api/email/triage", headers=H).status_code == 202
        settled(c)
        assert len([r for r in calls if r.method == "POST"]) == 1
        with app.state.store.connect() as db:
            dump = "\n".join(db.iterdump())
        assert "Source identifies a requested task" not in dump
        assert "PRIVATE SNIPPET" not in dump


def test_unverified_evidence_reply_without_thread_and_duplicate_ids_fail_closed(
    tmp_path,
):
    app, _, _ = application(tmp_path)
    seed_mail(app, count=3, classified=False)
    with TestClient(app) as c:
        login(c)
        install_model(
            app,
            lambda rows: [
                decision(rows[0]["id"], evidence="invented"),
                decision(rows[1]["id"], kind="reply"),
                decision(rows[2]["id"]),
                decision(rows[2]["id"]),
            ],
        )
        assert c.post("/api/email/triage", headers=H).status_code == 202
        data = settled(c)
        assert data["classification"]["counts"]["action"] == 0
        assert c.get("/api/agenda", params=DAY).json()["emails"] == []


def test_disconnect_during_model_completion_cannot_resurrect_decisions(tmp_path):
    app, _, _ = application(tmp_path)
    seed_mail(app, classified=False)

    def remove():
        with app.state.store.connect() as db:
            db.execute("DELETE FROM email_snapshots")

    with TestClient(app) as c:
        login(c)
        install_model(
            app, lambda rows: [decision(x["id"]) for x in rows], before_result=remove
        )
        assert c.post("/api/email/triage", headers=H).status_code == 202
        assert settled(c)["items"] == []
        assert c.get("/api/agenda", params=DAY).json()["emails"] == []


def test_changed_refill_generation_rejects_an_already_admitted_result(tmp_path):
    app, _, _ = application(tmp_path)
    seed_mail(app, classified=False)

    def rebuild_generation():
        rewrite_items(
            app, lambda items: items[0].update(refillGeneration="new-generation")
        )

    with TestClient(app) as c:
        login(c)
        install_model(
            app,
            lambda rows: [decision(x["id"]) for x in rows],
            before_result=rebuild_generation,
        )
        assert c.post("/api/email/triage", headers=H).status_code == 202
        assert settled(c)["classification"]["counts"]["action"] == 0
        assert c.get("/api/agenda", params=DAY).json()["emails"] == []


def rewrite_items(app, change):
    vault = app.state.accounts.vault
    with app.state.store.connect() as db:
        raw = db.execute(
            "SELECT body FROM email_snapshots WHERE account_id='mail-account'"
        ).fetchone()[0]
        data = vault.open("email-snapshot:mail-account", raw)
        change(data["items"])
        db.execute(
            "UPDATE email_snapshots SET body=? WHERE account_id='mail-account'",
            (vault.seal("email-snapshot:mail-account", data),),
        )


def test_bulk_spam_exclusions_no_reply_sender_and_verified_thread_status(tmp_path):
    app, _, _ = application(tmp_path)
    seed_mail(app, count=5, classified=False)

    def edit(items):
        items[0]["labels"] = ["SPAM", "INBOX"]
        items[1]["listUnsubscribe"] = True
        items[2]["from"] = "noreply@example.test"
        items[3]["thread"] = {
            "verified": True,
            "newerSent": False,
            "newerIncoming": False,
        }
        items[4]["thread"] = {
            "verified": True,
            "newerSent": True,
            "newerIncoming": False,
        }

    rewrite_items(app, edit)
    with TestClient(app) as c:
        login(c)
        calls = install_model(
            app,
            lambda rows: [
                decision(x["id"], kind="reply" if x.get("thread") else "todo")
                for x in rows
            ],
        )
        assert c.post("/api/email/triage", headers=H).status_code == 202
        result = settled(c)
        assert result["classification"]["counts"] == {
            "action": 2,
            "ignore": 2,
            "review": 1,
            "pending": 0,
        }
        posted = json.loads(next(r for r in calls if r.method == "POST").content)
        assert len(json.loads(posted["messages"][1]["content"])["messages"]) == 3
        assert {x["id"] for x in c.get("/api/agenda", params=DAY).json()["emails"]} == {
            "message2",
            "message3",
        }


def test_changed_input_and_changed_grant_never_reuse_old_decisions(tmp_path):
    app, _, _ = application(tmp_path)
    seed_mail(app)
    with TestClient(app) as c:
        login(c)
        assert len(c.get("/api/agenda", params=DAY).json()["emails"]) == 1
        rewrite_items(
            app, lambda items: items[0].update(subject="Changed current source")
        )
        assert c.get("/api/agenda", params=DAY).json()["emails"] == []
        calls = install_model(app, lambda rows: [decision(x["id"]) for x in rows])
        assert c.post("/api/email/triage", headers=H).status_code == 202
        settled(c)
        assert len(c.get("/api/agenda", params=DAY).json()["emails"]) == 1
        with app.state.store.connect() as db:
            row = db.execute(
                "SELECT body FROM accounts WHERE id='mail-account'"
            ).fetchone()[0]
            account = app.state.accounts.vault.open("account:mail-account", row)
            account["email"]["grantId"] = "new-grant"
            db.execute(
                "UPDATE accounts SET body=? WHERE id='mail-account'",
                (app.state.accounts.vault.seal("account:mail-account", account),),
            )
        assert c.get("/api/agenda", params=DAY).json()["emails"] == []
        assert len([r for r in calls if r.method == "POST"]) == 1


def test_corrupt_derived_cache_fails_review_without_overwriting_evidence(tmp_path):
    app, _, _ = application(tmp_path)
    seed_mail(app)
    key = "email-decisions:mail-account"
    app.state.store.set(key, "invalid-encrypted-evidence")
    with TestClient(app) as c:
        login(c)
        calls = install_model(app, lambda rows: [decision(x["id"]) for x in rows])
        data = c.get("/api/agenda", params=DAY).json()
        assert (
            data["emails"] == []
            and data["sources"]["email"]["classification"]["state"] == "error"
        )
        c.post("/api/email/triage", headers=H)
        settled(c)
        assert not calls
        assert app.state.store.get(key) == "invalid-encrypted-evidence"


@pytest.mark.parametrize(
    "route",
    [
        "/api/accounts/mail-account",
        "/api/accounts/providers/google",
        "/api/email/accounts/mail-account",
    ],
)
def test_removing_mail_account_cleans_only_its_derived_decisions(tmp_path, route):
    app, _, _ = application(tmp_path)
    seed_mail(app)
    app.state.store.set(
        "email-decisions:unrelated-account", "unrelated preserved fixture"
    )
    with TestClient(app) as c:
        login(c)
        assert c.delete(route, headers=H).status_code == 200
        assert app.state.store.get("email-decisions:mail-account") is None
        assert (
            app.state.store.get("email-decisions:unrelated-account")
            == "unrelated preserved fixture"
        )


@pytest.mark.parametrize("mode", ["wrong-aad", "invalid-envelope"])
def test_backup_caller_rejects_invalid_derived_email_state(tmp_path, mode):
    from test_backups import prepared

    from leam_api.backups import Backups
    from leam_api.vault import Vault

    store = prepared(tmp_path)
    vault = Vault(tmp_path)
    key = "email-decisions:fixture-account"
    store.set(
        key,
        vault.seal("wrong-label" if mode == "wrong-aad" else key, {"invalid": "shape"}),
    )
    with pytest.raises(ValueError, match="vault"):
        Backups(store).create()


def clone_mail_account(app, key="second-account"):
    vault = app.state.accounts.vault
    with app.state.store.connect() as db:
        row = dict(
            db.execute("SELECT * FROM accounts WHERE id='mail-account'").fetchone()
        )
        body = vault.open("account:mail-account", row["body"])
        row.update(
            id=key,
            subject=key + "-subject",
            identity=key + "@example.test",
            body=vault.seal("account:" + key, body),
        )
        columns = list(row)
        db.execute(
            "INSERT INTO accounts ("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join("?" for _ in columns)
            + ")",
            [row[k] for k in columns],
        )
        snapshot = vault.open(
            "email-snapshot:mail-account",
            db.execute(
                "SELECT body FROM email_snapshots WHERE account_id='mail-account'"
            ).fetchone()[0],
        )
        for item in snapshot["items"]:
            item["accountId"] = key
        db.execute(
            "INSERT INTO email_snapshots VALUES (?,?,?,NULL)",
            (
                key,
                vault.seal("email-snapshot:" + key, snapshot),
                time.time(),
            ),
        )


def test_corrupt_account_does_not_block_other_mailbox_and_removal_recovers(tmp_path):
    app, _, _ = application(tmp_path)
    seed_mail(app, classified=False)
    clone_mail_account(app)
    key = "email-decisions:mail-account"
    app.state.store.set(key, "corrupt-original")
    with TestClient(app) as c:
        login(c)
        install_model(app, lambda rows: [decision(x["id"]) for x in rows])
        c.post("/api/email/triage", headers=H)
        result = settled(c)
        assert result["classification"]["counts"]["action"] == 1
        assert app.state.store.get(key) == "corrupt-original"
        assert (
            c.get("/api/agenda", params=DAY).json()["emails"][0]["accountId"]
            == "second-account"
        )
        assert c.delete("/api/accounts/mail-account", headers=H).status_code == 200
        assert c.get("/api/email/triage").json()["classification"]["state"] == "ready"


def test_sync_request_during_active_classification_coalesces_fresh_snapshot(tmp_path):
    import asyncio
    import threading

    app, _, _ = application(tmp_path)
    seed_mail(app, classified=False)
    entered, release = threading.Event(), threading.Event()
    posts = []

    async def receive(request):
        if request.url.path.endswith("/llm/providers"):
            return httpx.Response(
                200, json={"active": {"model": "selected-main-model"}}
            )
        body = json.loads(request.content)
        rows = json.loads(body["messages"][1]["content"])["messages"]
        posts.append(rows)
        if len(posts) == 1:
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"decisions": [decision(x["id"]) for x in rows]}
                            )
                        }
                    }
                ]
            },
        )

    with TestClient(app) as c:
        login(c)
        app.state.emails.classifier.runtime.client = httpx.AsyncClient(
            base_url="http://127.0.0.1:46410", transport=httpx.MockTransport(receive)
        )
        assert c.post("/api/email/triage", headers=H).status_code == 202
        assert entered.wait(2)
        rewrite_items(
            app, lambda items: items[0].update(subject="New synchronized source")
        )
        assert c.post("/api/email/triage", headers=H).status_code == 202
        release.set()
        result = settled(c)
        assert len(posts) == 2 and posts[1][0]["subject"] == "New synchronized source"
        assert result["classification"]["counts"]["action"] == 1


def test_every_retrieved_multiaccount_message_is_classified_and_eligible(tmp_path):
    from leam_api.email_actionability import identity

    app, _, _ = application(tmp_path)
    seed_mail(app, count=18, classified=False)
    clone_mail_account(app)
    target = identity({"accountId": "second-account", "id": "message17"})
    observed = []

    def classify(rows):
        observed.extend(row["id"] for row in rows)
        assert len(rows) <= 5
        return [
            decision(row["id"], state="action" if row["id"] == target else "ignore")
            for row in rows
        ]

    with TestClient(app) as client:
        login(client)
        calls = install_model(app, classify)
        before = client.get("/api/email").json()
        assert len(before["items"]) == 36
        assert before["classification"]["counts"]["pending"] == 36
        assert client.post("/api/email/triage", headers=H).status_code == 202
        result = settled(client)
        assert result["classification"]["state"] == "ready"
        assert result["classification"]["limit"] == 100
        assert result["classification"]["counts"] == {
            "action": 1,
            "ignore": 35,
            "review": 0,
            "pending": 0,
        }
        assert len(result["items"]) == len(observed) == len(set(observed)) == 36
        assert not result["truncated"]
        assert target in observed[20:]
        agenda = client.get("/api/agenda", params=DAY).json()
        assert agenda["total"]["emails"] == 1
        assert agenda["emails"][0]["accountId"] == "second-account"
        assert agenda["emails"][0]["id"] == "message17"
        assert not agenda["sources"]["email"]["truncated"]
        model_calls = [r for r in calls if r.method == "POST"]
        assert len(model_calls) == 8
        assert all(
            len(json.loads(r.content)["messages"][1]["content"].encode()) <= 14000
            for r in model_calls
        )
        assert client.post("/api/email/triage", headers=H).status_code == 202
        settled(client)
        assert len([r for r in calls if r.method == "POST"]) == 8


def test_classification_never_exceeds_the_exposed_hundred_source_overview(tmp_path):
    from leam_api.email_actionability import identity

    app, _, _ = application(tmp_path)
    seed_mail(app, count=20, classified=False)
    for index in range(5):
        clone_mail_account(app, key=f"extra-account-{index}")
    observed = []

    def classify(rows):
        observed.extend(row["id"] for row in rows)
        return [decision(row["id"], state="ignore") for row in rows]

    with TestClient(app) as client:
        login(client)
        calls = install_model(app, classify)
        before = client.get("/api/email").json()
        assert sum(len(account["items"]) for account in before["accounts"]) == 120
        assert len(before["items"]) == 100 and before["truncated"]
        assert client.post("/api/email/triage", headers=H).status_code == 202
        result = settled(client)
        assert result["classification"]["counts"] == {
            "action": 0,
            "ignore": 100,
            "review": 0,
            "pending": 0,
        }
        assert len(observed) == 100 and set(observed) == {
            identity(item) for item in before["items"]
        }
        assert len(result["items"]) == 100 and result["truncated"]
        assert len([r for r in calls if r.method == "POST"]) == 20
