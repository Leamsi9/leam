import asyncio
import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient
from test_agenda import DAY, H, application
from test_agenda_email import forbid_provider, seed_mail
from test_api import login
from test_email_actionability import (
    clone_mail_account,
    decision,
    rewrite_items,
    settled,
)


def request(
    item,
    *,
    decision="action",
    action="Reply with the requested availability",
    kind="reply",
):
    return {
        "accountId": item["accountId"],
        "messageId": item["id"],
        "reviewRevision": item["reviewRevision"],
        "decision": decision,
        "kind": kind if decision == "action" else None,
        "action": action if decision == "action" else "",
    }


def first(client):
    return client.get("/api/email/triage").json()["items"][0]


def test_owner_can_add_or_exclude_without_model_quote_or_implicit_focus(tmp_path):
    app, calls, _ = application(tmp_path)
    seed_mail(app, classified=False)
    provider = forbid_provider(app)
    with TestClient(app) as c:
        assert c.put("/api/email/triage/review", headers=H, json={}).status_code == 401
        login(c)
        item = first(c)
        body = request(item)
        assert c.put("/api/email/triage/review", json=body).status_code == 403
        response = c.put("/api/email/triage/review", headers=H, json=body)
        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["reviewRevision"] != body["reviewRevision"]
        assert saved["actionability"]["reviewedBy"] == "user"
        assert (
            saved["actionability"]["basis"] is None
            and saved["actionability"]["evidence"] == ""
        )
        assert saved["actionability"]["action"] == body["action"]
        row = c.get("/api/agenda", params=DAY).json()["emails"][0]
        assert row["triage"]["disposition"] == "none"
        assert row["reviewRevision"] == saved["reviewRevision"]
        assert (
            c.put("/api/email/triage/review", headers=H, json=body).status_code == 409
        )
        excluded = c.put(
            "/api/email/triage/review",
            headers=H,
            json=request(first(c), decision="ignore"),
        )
        assert excluded.status_code == 200
        assert c.get("/api/agenda", params=DAY).json()["emails"] == []
        with app.state.store.connect() as db:
            assert (
                db.execute(
                    "SELECT count(*) FROM entities WHERE kind='commitment'"
                ).fetchone()[0]
                == 0
            )
            assert body["action"] not in "\n".join(db.iterdump())
    assert not provider and not calls


@pytest.mark.parametrize("change", ["source", "grant", "scope", "client", "delete"])
def test_stale_owner_review_never_overrides_changed_source_or_access(tmp_path, change):
    app, _, _ = application(tmp_path)
    seed_mail(app, classified=False)
    with TestClient(app) as c:
        login(c)
        body = request(first(c))
        if change == "source":
            rewrite_items(app, lambda items: items[0].update(subject="Changed source"))
        elif change == "delete":
            assert (
                c.delete("/api/email/accounts/mail-account", headers=H).status_code
                == 200
            )
        else:
            vault = app.state.accounts.vault
            with app.state.store.connect() as db:
                row = db.execute(
                    "SELECT body FROM accounts WHERE id='mail-account'"
                ).fetchone()[0]
                data = vault.open("account:mail-account", row)
                if change == "grant":
                    data["email"]["grantId"] = "replacement-grant"
                if change == "scope":
                    data["email"]["token"]["scope"] = "openid email"
                if change == "client":
                    data["clientId"] = "replacement-client"
                db.execute(
                    "UPDATE accounts SET body=? WHERE id='mail-account'",
                    (vault.seal("account:mail-account", data),),
                )
        assert (
            c.put("/api/email/triage/review", headers=H, json=body).status_code == 409
        )
        assert c.get("/api/agenda", params=DAY).json()["emails"] == []


def test_review_schema_requires_label_kind_and_exact_account_revision(tmp_path):
    app, _, _ = application(tmp_path)
    seed_mail(app, classified=False)
    clone_mail_account(app)
    with TestClient(app) as c:
        login(c)
        body = request(first(c))
        for patch in (
            {"action": "   "},
            {"kind": None},
            {"reviewedBy": "model"},
            {"decision": "ignore", "action": "surprise action"},
        ):
            assert (
                c.put(
                    "/api/email/triage/review", headers=H, json={**body, **patch}
                ).status_code
                == 422
            )
        assert (
            c.put(
                "/api/email/triage/review",
                headers=H,
                json={**body, "accountId": "second-account"},
            ).status_code
            == 409
        )
        assert (
            c.put("/api/email/triage/review", headers=H, json=body).status_code == 200
        )
        assert len(c.get("/api/agenda", params=DAY).json()["emails"]) == 1
        rewrite_items(
            app, lambda items: items[0].update(snippet="Changed current evidence")
        )
        assert c.get("/api/agenda", params=DAY).json()["emails"] == []


@pytest.mark.parametrize("human_choice", ["action", "ignore"])
def test_inflight_model_cannot_overwrite_newer_explicit_owner_choice(
    tmp_path, human_choice
):
    app, _, _ = application(tmp_path)
    seed_mail(app, classified=False)
    entered, release = threading.Event(), threading.Event()

    async def receive(model_request):
        if model_request.url.path.endswith("/llm/providers"):
            return httpx.Response(
                200, json={"active": {"model": "selected-main-model"}}
            )
        rows = json.loads(json.loads(model_request.content)["messages"][1]["content"])[
            "messages"
        ]
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        opposite = "ignore" if human_choice == "action" else "action"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "decisions": [
                                        decision(row["id"], state=opposite)
                                        for row in rows
                                    ]
                                }
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
        body = request(first(c), decision=human_choice)
        assert c.post("/api/email/triage", headers=H).status_code == 202
        assert entered.wait(2)
        response = c.put("/api/email/triage/review", headers=H, json=body)
        assert response.status_code == 200, response.text
        saved = response.json()
        release.set()
        result = settled(c)["items"][0]
        assert result["actionability"] == saved["actionability"]
        assert result["reviewRevision"] == saved["reviewRevision"]
        # Explicit owner re-review remains allowed with the current optimistic revision.
        assert (
            c.put(
                "/api/email/triage/review",
                headers=H,
                json=request(
                    result, decision="ignore" if human_choice == "action" else "action"
                ),
            ).status_code
            == 200
        )


def test_human_choice_invalidates_on_new_grant_and_model_review_race_is_stale(tmp_path):
    app, _, _ = application(tmp_path)
    items = seed_mail(app, classified=False)
    with TestClient(app) as c:
        login(c)
        body = request(first(c))
        app.state.emails.classifier.persist(
            items[0],
            "fixture-grant",
            {
                "state": "review",
                "kind": None,
                "basis": None,
                "reason": "New model generation",
                "action": "",
                "evidence": "",
            },
        )
        assert (
            c.put("/api/email/triage/review", headers=H, json=body).status_code == 409
        )
        assert (
            c.put(
                "/api/email/triage/review", headers=H, json=request(first(c))
            ).status_code
            == 200
        )
        with app.state.store.connect() as db:
            vault = app.state.accounts.vault
            data = vault.open(
                "account:mail-account",
                db.execute(
                    "SELECT body FROM accounts WHERE id='mail-account'"
                ).fetchone()[0],
            )
            data["email"]["grantId"] = "replacement-grant"
            db.execute(
                "UPDATE accounts SET body=? WHERE id='mail-account'",
                (vault.seal("account:mail-account", data),),
            )
        assert c.get("/api/agenda", params=DAY).json()["emails"] == []
        assert first(c)["actionability"]["pending"] is True
