import json
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fastapi.testclient import TestClient
from test_accounts import H, connect
from test_api import FakeCodex, login

from leam_api.accounts import EMAIL_SCOPE, GMAIL_READONLY
from leam_api.app import create_app


@pytest.fixture
def mailbox(tmp_path):
    state = {
        "mail": False,
        "scope": EMAIL_SCOPE,
        "refresh": True,
        "subject": "google-identity",
        "fail": False,
        "calls": [],
        "messages": 1,
        "next": False,
    }

    def handle(request):
        state["calls"].append(request)
        if request.url.path.endswith("/token"):
            return httpx2.Response(
                200,
                json={
                    "access_token": "mail-access"
                    if state["mail"]
                    else "calendar-access",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    **(
                        {
                            "refresh_token": "mail-refresh"
                            if state["mail"]
                            else "calendar-refresh"
                        }
                        if state["refresh"]
                        else {}
                    ),
                    **(
                        {"scope": state["scope"]}
                        if state["mail"] and state["scope"] is not None
                        else {}
                    ),
                },
            )
        if "/userinfo" in request.url.path:
            return httpx2.Response(
                200, json={"sub": state["subject"], "email": "example@example.test"}
            )
        assert request.method == "GET"
        assert request.url.host == "gmail.googleapis.com"
        assert request.headers["authorization"] == "Bearer mail-access"
        if state["fail"]:
            return httpx2.Response(503, json={"error": "private-mail-provider-failure"})
        if request.url.path.endswith("/messages"):
            return httpx2.Response(
                200,
                json={
                    "messages": [
                        {"id": f"message{i}"} for i in range(state["messages"])
                    ],
                    **({"nextPageToken": "next"} if state["next"] else {}),
                },
            )
        return httpx2.Response(
            200,
            json={
                "id": request.url.path.rsplit("/", 1)[1],
                "threadId": "thread1",
                "labelIds": ["INBOX", "UNREAD", "IMPORTANT"],
                "internalDate": "1790000000000",
                "snippet": "Synthetic private snippet",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "sender@example.test"},
                        {"name": "Subject", "value": "Synthetic subject"},
                    ],
                    "body": {"data": "must-never-be-stored"},
                },
            },
        )

    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        account_transport=httpx2.MockTransport(handle),
    )
    with TestClient(app) as client:
        login(client)
        account = connect(client)
        yield client, app, state, account["id"]


def saved(app, key):
    with app.state.store.connect() as db:
        row = db.execute("SELECT body FROM accounts WHERE id=?", (key,)).fetchone()
    return app.state.accounts.vault.open("account:" + key, row["body"])


def consent(client, state, key, complete=True):
    response = client.post(f"/api/accounts/{key}/email/authorize", json={}, headers=H)
    assert response.status_code == 200
    params = parse_qs(urlsplit(response.json()["url"]).query)
    assert params["scope"] == [EMAIL_SCOPE]
    assert params["include_granted_scopes"] == ["true"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["login_hint"] == ["example@example.test"]
    state["mail"] = True
    callback = (
        "/api/accounts/oauth/google/callback?code=synthetic-mail-code&state="
        + params["state"][0]
    )
    return client.get(callback, follow_redirects=False) if complete else callback


def test_calendar_identity_is_not_mail_consent_and_get_never_syncs(mailbox):
    client, _app, state, key = mailbox
    before = len(state["calls"])
    assert client.get("/api/email").json()["state"] == "not_connected"
    assert client.get("/api/accounts").json()["items"][0]["email"]["granted"] is False
    assert client.post(f"/api/email/accounts/{key}/sync", headers=H).status_code == 409
    assert len(state["calls"]) == before
    assert (
        client.post(
            f"/api/accounts/{key}/email/authorize",
            headers={"origin": "https://untrusted.test"},
        ).status_code
        == 403
    )


def test_explicit_mail_grant_preserves_calendar_and_encrypts_bounded_snapshot(mailbox):
    client, app, state, key = mailbox
    old = saved(app, key)["token"]
    response = consent(client, state, key)
    assert (
        response.status_code == 303
        and "email-connected" in response.headers["location"]
    )
    assert saved(app, key)["token"] == old
    assert client.get("/api/email").json()["accounts"][0]["state"] == "never_synced"
    state["next"] = True
    result = client.post(f"/api/email/accounts/{key}/sync", headers=H).json()
    assert result["state"] == "ready" and result["truncated"]
    item = result["items"][0]
    assert (
        item["unread"] and item["important"] and item["subject"] == "Synthetic subject"
    )
    assert item["url"].startswith("https://mail.google.com/")
    requests = [r for r in state["calls"] if r.url.host == "gmail.googleapis.com"]
    assert len(requests) == 2 and all(r.method == "GET" for r in requests)
    assert (
        requests[0].url.params["maxResults"] == "20"
        and requests[0].url.params["q"] == "in:inbox newer_than:30d"
    )
    assert requests[1].url.params["format"] == "metadata"
    assert requests[1].url.params.get_list("metadataHeaders") == ["From", "Subject"]
    with app.state.store.connect() as db:
        dump = "\n".join(db.iterdump())
    for secret in [
        "mail-access",
        "mail-refresh",
        "Synthetic private snippet",
        "Synthetic subject",
        "must-never-be-stored",
    ]:
        assert secret not in dump
    assert "must-never-be-stored" not in json.dumps(result)
    state["fail"] = True
    failed = client.post(f"/api/email/accounts/{key}/sync", headers=H).json()
    assert (
        failed["state"] == "error"
        and failed["items"] == result["items"]
        and failed["syncedAt"] == result["syncedAt"]
    )
    assert "private-mail-provider-failure" not in json.dumps(failed)
    assert (
        client.delete(f"/api/email/accounts/{key}", headers=H).json()[
            "providerGrantRevoked"
        ]
        is False
    )
    assert saved(app, key)["token"] == old and "email" not in saved(app, key)
    assert client.get("/api/email").json()["items"] == []


@pytest.mark.parametrize(
    "scope", [None, "openid email", "https://www.googleapis.com/auth/gmail.modify"]
)
def test_missing_exact_scope_never_replaces_calendar_or_enables_mail(mailbox, scope):
    client, app, state, key = mailbox
    before = saved(app, key)
    state["scope"] = scope
    result = consent(client, state, key)
    assert result.status_code == 303 and "email-denied" in result.headers["location"]
    assert saved(app, key) == before
    assert not client.get("/api/accounts").json()["items"][0]["email"]["granted"]


def test_wrong_account_and_missing_refresh_token_fail_without_replacing_calendar(
    mailbox,
):
    client, app, state, key = mailbox
    before = saved(app, key)
    state["subject"] = "other-account"
    assert consent(client, state, key).status_code == 409
    assert saved(app, key) == before
    state["subject"] = "google-identity"
    state["refresh"] = False
    assert consent(client, state, key).status_code == 409
    assert saved(app, key) == before
    state["refresh"] = True
    assert consent(client, state, key).status_code == 303
    previous = saved(app, key)["email"]["token"]["refresh_token"]
    state["refresh"] = False
    assert consent(client, state, key).status_code == 303
    assert saved(app, key)["email"]["token"]["refresh_token"] == previous


def test_bound_failure_never_replaces_last_good_and_account_delete_cascades(mailbox):
    client, app, state, key = mailbox
    consent(client, state, key)
    good = client.post(f"/api/email/accounts/{key}/sync", headers=H).json()
    state["messages"] = 21
    before = len(state["calls"])
    failed = client.post(f"/api/email/accounts/{key}/sync", headers=H).json()
    assert failed["state"] == "error" and failed["items"] == good["items"]
    assert len(state["calls"]) == before + 1
    assert client.delete(f"/api/accounts/{key}", headers=H).status_code == 200
    assert client.get("/api/email").json()["accounts"] == []
    with app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM email_snapshots").fetchone()[0] == 0


def test_mail_refresh_uses_only_mail_slot_and_preserves_calendar(mailbox):
    client, app, state, key = mailbox
    consent(client, state, key)
    data = saved(app, key)
    old = dict(data["token"])
    data["email"]["token"]["expires_at"] = 0
    with app.state.store.connect() as db:
        db.execute(
            "UPDATE accounts SET body=? WHERE id=?",
            (app.state.accounts.vault.seal("account:" + key, data), key),
        )
    assert (
        client.post(f"/api/email/accounts/{key}/sync", headers=H).json()["state"]
        == "ready"
    )
    refresh = [
        r
        for r in state["calls"]
        if r.url.path.endswith("/token") and b"grant_type=refresh_token" in r.content
    ]
    assert len(refresh) == 1
    params = parse_qs(refresh[0].content.decode())
    assert (
        params["refresh_token"] == ["mail-refresh"]
        and GMAIL_READONLY in params["scope"][0]
    )
    assert saved(app, key)["token"] == old


def test_disabling_email_invalidates_pending_consent(mailbox):
    client, _app, state, key = mailbox
    pending = consent(client, state, key, complete=False)
    assert client.delete(f"/api/email/accounts/{key}", headers=H).status_code == 200
    assert client.get(pending, follow_redirects=False).status_code == 400
    assert not client.get("/api/accounts").json()["items"][0]["email"]["granted"]


def test_email_removal_preserves_an_independent_pending_calendar_consent(mailbox):
    client, _app, state, key = mailbox
    calendar = client.post(
        "/api/accounts/providers/google/authorize", headers=H, json={}
    ).json()
    calendar_state = parse_qs(urlsplit(calendar["url"]).query)["state"][0]
    pending_mail = consent(client, state, key, complete=False)
    assert client.delete(f"/api/email/accounts/{key}", headers=H).status_code == 200
    assert client.get(pending_mail, follow_redirects=False).status_code == 400
    state["mail"] = False
    result = client.get(
        "/api/accounts/oauth/google/callback?code=fixture-calendar&state="
        + calendar_state,
        follow_redirects=False,
    )
    assert result.status_code == 303 and result.headers["location"].endswith(
        "account=connected"
    )
    assert client.get("/api/accounts").json()["items"][0]["email"]["granted"] is False


def expire_mail(app, key):
    data = saved(app, key)
    data["email"]["token"]["expires_at"] = 0
    with app.state.store.connect() as db:
        db.execute(
            "UPDATE accounts SET body=? WHERE id=?",
            (app.state.accounts.vault.seal("account:" + key, data), key),
        )


def test_refresh_narrowed_scope_preserves_calendar_and_previous_snapshot(mailbox):
    client, app, state, key = mailbox
    assert consent(client, state, key).status_code == 303
    before = saved(app, key)["token"]
    good = client.post(f"/api/email/accounts/{key}/sync", headers=H).json()
    expire_mail(app, key)
    state["scope"] = "openid email"
    called = len(state["calls"])
    response = client.post(f"/api/email/accounts/{key}/sync", headers=H)
    assert response.status_code == 200
    result = response.json()
    assert result["state"] == "reconnect"
    assert result["items"] == good["items"] and result["syncedAt"] == good["syncedAt"]
    assert saved(app, key)["token"] == before
    assert not any(
        r.url.host == "gmail.googleapis.com" for r in state["calls"][called:]
    )


def test_refresh_omitted_scope_keeps_previously_confirmed_scope_only(mailbox):
    client, app, state, key = mailbox
    assert consent(client, state, key).status_code == 303
    expire_mail(app, key)
    state["scope"] = None
    response = client.post(f"/api/email/accounts/{key}/sync", headers=H)
    assert response.status_code == 200
    assert response.json()["state"] == "ready"


def test_expired_session_during_mail_exchange_cannot_commit(mailbox):
    client, app, state, key = mailbox
    old = saved(app, key)
    pending = consent(client, state, key, complete=False)
    original = app.state.accounts.transport.handler

    def handler(request):
        response = original(request)
        if "/userinfo" in request.url.path:
            with app.state.store.connect() as db:
                db.execute("DELETE FROM sessions")
        return response

    app.state.accounts.transport = httpx2.MockTransport(handler)
    response = client.get(pending, follow_redirects=False)
    assert response.status_code == 409
    assert saved(app, key) == old


def test_provider_mismatched_message_identity_keeps_last_good(mailbox):
    client, app, state, key = mailbox
    consent(client, state, key)
    good = client.post(f"/api/email/accounts/{key}/sync", headers=H).json()
    original = app.state.accounts.transport.handler

    def handler(request):
        response = original(request)
        if request.url.path.endswith("/message0"):
            body = response.json()
            body["id"] = "different-message"
            return httpx2.Response(200, json=body)
        return response

    app.state.accounts.transport = httpx2.MockTransport(handler)
    response = client.post(f"/api/email/accounts/{key}/sync", headers=H)
    assert response.status_code == 200
    result = response.json()
    assert result["state"] == "error"
    assert result["items"] == good["items"] and result["syncedAt"] == good["syncedAt"]
