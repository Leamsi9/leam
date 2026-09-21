import json
import time
import uuid

import httpx2
from fastapi.testclient import TestClient
from test_agenda import DAY, H, application
from test_api import login

from leam_api.accounts import EMAIL_SCOPE


def seed_mail(app, count=1, *, granted=True, title="Mailbox subject", synced=None):
    now = time.time() if synced is None else synced
    vault, store = app.state.accounts.vault, app.state.store
    store.set(
        "account_config:google",
        vault.seal(
            "config:google",
            {
                "clientId": "fixture-client",
                "clientSecret": "fixture-secret",
                "revision": 1,
            },
        ),
    )
    account = {
        "clientId": "fixture-client",
        "token": {"access_token": "calendar-fixture"},
    }
    if granted:
        account["email"] = {
            "token": {
                "scope": EMAIL_SCOPE,
                "access_token": "mail-fixture",
                "refresh_token": "fixture-refresh",
            },
            "state": "connected",
            "grantId": "fixture-grant",
        }
    items = [
        {
            "id": f"message{index}",
            "threadId": f"thread{index}",
            "accountId": "mail-account",
            "subject": title + str(index),
            "from": "Sender <sender@example.test>",
            "snippet": "PRIVATE SNIPPET NOT FOR MODEL REFERENCE",
            "receivedAt": "2026-10-25T09:00:00+00:00",
            "unread": True,
            "important": False,
            "url": "https://mail.google.com/mail/#all/fixture",
        }
        for index in range(count)
    ]
    with store.connect() as db:
        db.execute(
            "INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "mail-account",
                "google",
                "fixture@example.test",
                vault.seal("account:mail-account", account),
                "connected",
                None,
                now,
                now,
                "fixture-subject",
            ),
        )
        db.execute(
            "INSERT INTO email_snapshots VALUES (?,?,?,NULL)",
            (
                "mail-account",
                vault.seal(
                    "email-snapshot:mail-account", {"items": items, "truncated": False}
                ),
                now,
            ),
        )
    return items


def forbid_provider(app):
    calls = []

    def handle(request):
        calls.append(request)
        raise AssertionError("Agenda must not request provider data")

    app.state.accounts.transport = httpx2.MockTransport(handle)
    return calls


def test_agenda_uses_cached_mail_exact_keys_and_local_scoped_triage(tmp_path):
    app, runtime_calls, _ = application(tmp_path)
    items = seed_mail(app)
    provider_calls = forbid_provider(app)
    with TestClient(app) as client:
        login(client)
        data = client.get("/api/agenda", params=DAY).json()
        assert data["sources"]["email"]["state"] == "ready"
        assert "items" not in data["sources"]["email"]["accounts"][0]
        item = data["emails"][0]
        assert item["subject"] == items[0]["subject"] and item["sourceState"] == "ready"
        assert item["key"].startswith("email:")
        with app.state.store.connect() as db:
            before = db.execute("SELECT body FROM email_snapshots").fetchone()[0]
        body = {**DAY, "key": item["key"], "revision": 0, "disposition": "focus"}
        assert client.put("/api/agenda/triage", headers=H, json=body).status_code == 200
        assert client.put("/api/agenda/triage", headers=H, json=body).status_code == 409
        assert (
            client.get("/api/agenda", params=DAY).json()["emails"][0]["triage"][
                "disposition"
            ]
            == "focus"
        )
        assert (
            client.get("/api/agenda", params={**DAY, "date": "2026-10-26"}).json()[
                "emails"
            ][0]["triage"]["disposition"]
            == "none"
        )
        with app.state.store.connect() as db:
            assert (
                db.execute("SELECT body FROM email_snapshots").fetchone()[0] == before
            )
    assert not provider_calls and not runtime_calls


def test_mail_capped_twenty_and_missing_grant_never_becomes_empty_connected_inbox(
    tmp_path,
):
    app, _, _ = application(tmp_path)
    seed_mail(app, 25, synced=time.time() - 1000)
    provider_calls = forbid_provider(app)
    with TestClient(app) as client:
        login(client)
        data = client.get("/api/agenda", params=DAY).json()
        assert len(data["emails"]) == data["total"]["emails"] == 20
        assert (
            data["sources"]["email"]["truncated"]
            and data["sources"]["email"]["state"] == "attention"
        )
        assert all(item["sourceState"] == "stale" for item in data["emails"])
        with app.state.store.connect() as db:
            row = db.execute("SELECT body FROM accounts").fetchone()[0]
            saved = app.state.accounts.vault.open("account:mail-account", row)
            saved.pop("email")
            db.execute(
                "UPDATE accounts SET body=?",
                (app.state.accounts.vault.seal("account:mail-account", saved),),
            )
        missing = client.get("/api/agenda", params=DAY).json()
        assert (
            missing["sources"]["email"]["state"] == "not_connected"
            and missing["emails"] == []
        )
        assert (
            client.put(
                "/api/agenda/triage",
                headers=H,
                json={
                    **DAY,
                    "key": data["emails"][0]["key"],
                    "revision": 0,
                    "disposition": "focus",
                },
            ).status_code
            == 404
        )
    assert not provider_calls


def test_email_triage_rechecks_membership_at_write_boundary(tmp_path, monkeypatch):
    app, _, _ = application(tmp_path)
    seed_mail(app)
    with TestClient(app) as client:
        login(client)
        item = client.get("/api/agenda", params=DAY).json()["emails"][0]
        original = app.state.agenda.snapshot

        def raced_snapshot(*args, **kwargs):
            result = original(*args, **kwargs)
            with app.state.store.connect() as db:
                db.execute("DELETE FROM email_snapshots")
            return result

        monkeypatch.setattr(app.state.agenda, "snapshot", raced_snapshot)
        response = client.put(
            "/api/agenda/triage",
            headers=H,
            json={**DAY, "key": item["key"], "revision": 0, "disposition": "focus"},
        )
        assert response.status_code == 409
        with app.state.store.connect() as db:
            assert (
                db.execute(
                    "SELECT count(*) FROM settings WHERE key LIKE 'agenda-triage:%'"
                ).fetchone()[0]
                == 0
            )


def test_daily_companion_uses_same_live_cache_with_bounded_reference_and_exact_retry(
    tmp_path,
):
    app, calls, _ = application(tmp_path)
    items = seed_mail(app, 5, title="界" * 300)
    provider_calls = forbid_provider(app)
    with TestClient(app) as client:
        login(client)
        first = client.get("/api/agenda", params=DAY).json()["emails"][0]
        assert (
            client.put(
                "/api/agenda/triage",
                headers=H,
                json={
                    **DAY,
                    "key": first["key"],
                    "revision": 0,
                    "disposition": "focus",
                },
            ).status_code
            == 200
        )
        thread = client.post("/api/agenda/chat", headers=H, json=DAY).json()["threadId"]
        path = f"/api/companion/threads/{thread}/messages"
        message = {"requestId": str(uuid.uuid4()), "text": "Exact daily request"}
        assert client.post(path, headers=H, json=message).status_code == 200
        reference = calls[-1]["model_context"]["reference_text"]
        context, _ = json.JSONDecoder().raw_decode(
            reference.split("<leam_context>", 1)[1]
        )
        agenda = context["dailyAgenda"]
        assert len(json.dumps(agenda, ensure_ascii=False).encode()) <= 2048
        assert agenda["emailState"] == "ready" and agenda["emailFreshness"][
            "states"
        ] == {"ready": 1}
        assert agenda["partial"] and agenda["items"][0]["id"] == first["id"]
        assert (
            agenda["items"][0]["accountId"] == "mail-account"
            and agenda["untrustedSourceData"]
        )
        assert (
            "PRIVATE SNIPPET" not in reference
            and calls[-1]["content"] == message["text"]
        )
        items[0]["subject"] = "Updated inbox subject"
        with app.state.store.connect() as db:
            db.execute(
                "UPDATE email_snapshots SET body=?",
                (
                    app.state.accounts.vault.seal(
                        "email-snapshot:mail-account",
                        {"items": items, "truncated": False},
                    ),
                ),
            )
        assert (
            client.post(path, headers=H, json=message).status_code == 200
            and len(calls) == 1
        )
        assert (
            client.post(
                path, headers=H, json={**message, "requestId": str(uuid.uuid4())}
            ).status_code
            == 200
        )
        assert "Updated inbox subject" in calls[-1]["model_context"]["reference_text"]
    assert not provider_calls
