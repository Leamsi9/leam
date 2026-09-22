import asyncio
import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient
from test_agenda import H, application
from test_agenda_email import seed_mail
from test_api import login
from test_email_actionability import decision, rewrite_items, settled


@pytest.mark.parametrize("phase", ["provider_lookup", "first_batch"])
@pytest.mark.parametrize(
    "change",
    [
        "disconnect",
        "delete_account",
        "new_grant",
        "source",
        "client",
        "scope_revoked",
        "reconnect",
    ],
)
def test_changed_authority_or_source_cannot_enter_another_model_batch(
    tmp_path, phase, change
):
    app, _, _ = application(tmp_path)
    seed_mail(app, count=6, classified=False)
    entered, release = threading.Event(), threading.Event()
    batches = []

    async def hold():
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)

    async def receive(request):
        if request.url.path.endswith("/llm/providers"):
            if phase == "provider_lookup":
                await hold()
            return httpx.Response(
                200, json={"active": {"model": "selected-main-model"}}
            )
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["tools"] == [] and body["stream"] is False
        assert body["model"] == "selected-main-model"
        assert body["response_format"]["type"] == "json_schema"
        assert request.headers["idempotency-key"].startswith("email-triage-")
        rows = json.loads(body["messages"][1]["content"])["messages"]
        batches.append(rows)
        if phase == "first_batch" and len(batches) == 1:
            await hold()
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

    with TestClient(app) as client:
        login(client)
        app.state.emails.classifier.runtime.client = httpx.AsyncClient(
            base_url="http://127.0.0.1:46410", transport=httpx.MockTransport(receive)
        )
        assert client.post("/api/email/triage", headers=H).status_code == 202
        assert entered.wait(2)
        try:
            if change == "disconnect":
                assert (
                    client.delete(
                        "/api/email/accounts/mail-account", headers=H
                    ).status_code
                    == 200
                )
            elif change == "delete_account":
                assert (
                    client.delete("/api/accounts/mail-account", headers=H).status_code
                    == 200
                )
            elif change == "source":
                rewrite_items(
                    app,
                    lambda items: [
                        item.update(subject="New source after synchronization")
                        for item in items
                    ],
                )
            elif change == "client":
                app.state.store.set(
                    "account_config:google",
                    app.state.accounts.vault.seal(
                        "config:google",
                        {
                            "clientId": "changed-client",
                            "clientSecret": "fixture-secret",
                            "revision": 2,
                        },
                    ),
                )
            else:
                with app.state.store.connect() as db:
                    raw = db.execute(
                        "SELECT body FROM accounts WHERE id='mail-account'"
                    ).fetchone()[0]
                    account = app.state.accounts.vault.open("account:mail-account", raw)
                    if change == "new_grant":
                        account["email"]["grantId"] = "new-current-grant"
                    elif change == "scope_revoked":
                        account["email"]["token"]["scope"] = ""
                    else:
                        account["email"]["state"] = "reconnect"
                    db.execute(
                        "UPDATE accounts SET body=? WHERE id='mail-account'",
                        (
                            app.state.accounts.vault.seal(
                                "account:mail-account", account
                            ),
                        ),
                    )
        finally:
            release.set()
        settled(client)
        assert len(batches) == (1 if phase == "first_batch" else 0), (
            "Changed authority/source entered a not-yet-admitted model request"
        )
        assert app.state.store.get("email-decisions:mail-account") is None
