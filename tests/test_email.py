import json
from urllib.parse import parse_qs, urlsplit

import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient
from test_accounts import H, connect
from test_api import FakeCodex, login

from leam_api.accounts import EMAIL_SCOPE, GMAIL_READONLY
from leam_api.app import create_app
from leam_api.ironclaw import IronClaw


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
        "unique_threads": False,
        "bulk_messages": 0,
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
        if "/threads/" in request.url.path:
            thread = request.url.path.rsplit("/", 1)[1]
            suffix = thread.removeprefix("thread")
            return httpx2.Response(
                200,
                json={
                    "id": thread,
                    "messages": [
                        {
                            "id": "message" + suffix
                            if state["unique_threads"]
                            else "message0",
                            "labelIds": ["INBOX"],
                            "internalDate": "1790000000000",
                        },
                        *(
                            [
                                {
                                    "id": "sent-reply",
                                    "labelIds": ["SENT"],
                                    "internalDate": "1790000001000",
                                }
                            ]
                            if state.get("sent")
                            else []
                        ),
                    ],
                },
            )
        message_id = request.url.path.rsplit("/", 1)[1]
        index = int(message_id.removeprefix("message"))
        headers = [
            {"name": "From", "value": "sender@example.test"},
            {"name": "Subject", "value": "Synthetic subject"},
        ]
        if index < state["bulk_messages"]:
            headers.append({"name": "List-Id", "value": "bulk.example.test"})
        return httpx2.Response(
            200,
            json={
                "id": message_id,
                "threadId": "thread" + str(index)
                if state["unique_threads"]
                else "thread1",
                "labelIds": ["INBOX", "UNREAD", "IMPORTANT"],
                "internalDate": "1790000000000",
                "snippet": "Synthetic private snippet",
                "payload": {
                    "headers": headers,
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
        runtime=IronClaw(
            "http://127.0.0.1:46410",
            None,
            token="fixture",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    503, json={"detail": "No fixture model configured"}
                )
            ),
        ),
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
    assert len(requests) == 3 and all(r.method == "GET" for r in requests)
    assert (
        requests[0].url.params["maxResults"] == "20"
        and requests[0].url.params["q"] == "in:inbox newer_than:30d"
    )
    assert requests[1].url.params["format"] == "metadata"
    assert requests[1].url.params.get_list("metadataHeaders") == [
        "From",
        "Subject",
        "List-Id",
        "List-Unsubscribe",
        "Precedence",
    ]
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


def test_sync_triggers_background_triage_and_newer_sent_reply_invalidates_action(
    mailbox,
):
    from test_email_actionability import decision, install_model, settled

    client, app, state, key = mailbox
    consent(client, state, key)
    calls = install_model(
        app,
        lambda rows: [
            decision(x["id"], kind="reply", evidence="Synthetic private snippet")
            for x in rows
        ],
    )
    response = client.post(f"/api/email/accounts/{key}/sync", headers=H)
    assert response.status_code == 200
    first = settled(client)
    assert first["classification"]["counts"]["action"] == 1
    assert first["items"][0]["thread"]["verified"] is True
    state["sent"] = True
    assert client.post(f"/api/email/accounts/{key}/sync", headers=H).status_code == 200
    second = settled(client)
    assert second["classification"]["counts"]["action"] == 0
    assert second["classification"]["counts"]["review"] == 1
    assert second["items"][0]["thread"]["newerSent"] is True
    assert len([r for r in calls if r.method == "POST"]) == 2
    thread_reads = [r for r in state["calls"] if "/threads/" in r.url.path]
    assert len(thread_reads) == 2
    assert all(
        r.method == "GET"
        and r.url.params["format"] == "metadata"
        and r.url.params["fields"] == "id,messages(id,labelIds,internalDate)"
        for r in thread_reads
    )


def test_rebuild_scans_beyond_spam_to_fill_twenty_local_actions(mailbox):
    from test_email_actionability import decision, install_model, settled

    client, app, state, key = mailbox
    consent(client, state, key)
    state.update(messages=40, unique_threads=True, bulk_messages=20, next=True)
    calls_before = len(state["calls"])
    install_model(
        app,
        lambda rows: [
            decision(x["id"], evidence="Synthetic private snippet") for x in rows
        ],
    )

    assert client.post(f"/api/email/accounts/{key}/rebuild").status_code == 403
    response = client.post(f"/api/email/accounts/{key}/rebuild", headers=H)
    assert response.status_code == 200
    result = settled(client)
    assert result["classification"]["counts"] == {
        "action": 20,
        "ignore": 20,
        "review": 0,
        "pending": 0,
    }
    assert result["classification"]["limit"] == 100
    assert result["classification"]["actionLimit"] == 20
    agenda = client.get(
        "/api/agenda",
        params={"date": "2026-09-21", "timezone": "Europe/London"},
    ).json()
    assert len(agenda["emails"]) == agenda["total"]["emails"] == 20
    provider_calls = state["calls"][calls_before:]
    assert provider_calls[0].url.params["maxResults"] == "100"
    assert all(request.method == "GET" for request in provider_calls)
    assert response.json()["limit"] == 100


def test_failed_rebuild_keeps_previous_local_view_and_decisions(mailbox):
    client, app, state, key = mailbox
    consent(client, state, key)
    good = client.post(f"/api/email/accounts/{key}/sync", headers=H).json()
    decision_key = "email-decisions:" + key
    from test_email_review import request

    assert (
        client.put(
            "/api/email/triage/review", headers=H, json=request(good["items"][0])
        ).status_code
        == 200
    )
    good = next(
        item
        for item in client.get("/api/email").json()["accounts"]
        if item["accountId"] == key
    )
    previous_decisions = app.state.store.get(decision_key)
    state["fail"] = True

    failed = client.post(f"/api/email/accounts/{key}/rebuild", headers=H).json()
    assert failed["state"] == "error"
    assert failed["items"] == good["items"]
    assert failed["syncedAt"] == good["syncedAt"]
    assert app.state.store.get(decision_key) == previous_decisions


def test_rebuilt_mail_snapshot_round_trips_backup_restore(mailbox, tmp_path):
    from leam_api.backups import Backups, restore
    from leam_api.store import Store
    from leam_api.vault import Vault

    client, app, state, key = mailbox
    consent(client, state, key)
    state.update(messages=30, unique_threads=True, bulk_messages=30)
    source = client.post(f"/api/email/accounts/{key}/rebuild", headers=H).json()
    assert len(source["items"]) == 30
    backup = client.post("/api/backups", headers=H)
    assert backup.status_code == 200, backup.text
    manager = Backups(app.state.store)
    target = tmp_path / "restored-fixture"
    restore(manager.path(backup.json()["id"]), target, app.state.store.path.parent)
    restored = Store(target)
    with restored.connect() as db:
        encrypted = db.execute(
            "SELECT body FROM email_snapshots WHERE account_id=?", (key,)
        ).fetchone()[0]
    snapshot = Vault(target).open("email-snapshot:" + key, encrypted)
    assert snapshot["limit"] == 100 and len(snapshot["items"]) == 30
    assert {x["refillGeneration"] for x in snapshot["items"]} == {
        x["refillGeneration"] for x in source["items"]
    }


def test_normal_sync_preserves_owner_decision_after_rebuild(mailbox):
    from test_email_review import request

    client, _app, state, key = mailbox
    consent(client, state, key)
    rebuilt = client.post(f"/api/email/accounts/{key}/rebuild", headers=H).json()
    item = rebuilt["items"][0]
    reviewed = client.put("/api/email/triage/review", json=request(item), headers=H)
    assert reviewed.status_code == 200
    synced = client.post(f"/api/email/accounts/{key}/sync", headers=H).json()
    assert synced["items"][0]["refillGeneration"] == item["refillGeneration"]
    assert synced["items"][0]["actionability"]["reviewedBy"] == "user"
    assert synced["items"][0]["reviewRevision"] == reviewed.json()["reviewRevision"]


def test_legacy_owner_review_hash_remains_valid_without_refill_marker(mailbox):
    import hashlib

    from test_email_review import request

    from leam_api.email_actionability import VERSION, encoded, identity

    client, app, state, key = mailbox
    consent(client, state, key)
    item = client.post(f"/api/email/accounts/{key}/sync", headers=H).json()["items"][0]
    assert "refillGeneration" not in item
    assert (
        client.put(
            "/api/email/triage/review", headers=H, json=request(item)
        ).status_code
        == 200
    )
    cachekey = "email-decisions:" + key
    cache = app.state.accounts.vault.open(cachekey, app.state.store.get(cachekey))
    # Captured pre-refill source contract: omitted generation is not a JSON null field.
    legacy = {
        "id": identity(item),
        **{
            k: item.get(k)
            for k in (
                "threadId",
                "subject",
                "from",
                "snippet",
                "receivedAt",
                "labels",
                "listId",
                "listUnsubscribe",
                "precedence",
                "thread",
            )
        },
    }
    cache[identity(item)]["inputHash"] = hashlib.sha256(
        encoded([VERSION, legacy])
    ).hexdigest()
    app.state.store.set(cachekey, app.state.accounts.vault.seal(cachekey, cache))
    observed = client.get("/api/email/triage").json()["items"][0]
    assert (
        observed["actionability"]["reviewedBy"] == "user"
        and observed["actionability"]["pending"] is False
    )
    synced = client.post(f"/api/email/accounts/{key}/sync", headers=H).json()["items"][
        0
    ]
    assert synced["actionability"] == observed["actionability"]
