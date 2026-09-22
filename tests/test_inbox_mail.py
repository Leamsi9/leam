"""Actual authenticated local-mail callers; fixture provider asserts no writes."""
# ruff: noqa: F811 -- pytest fixture import is intentionally shadowed by injection.

import json

from test_accounts import H
from test_api import login
from test_artifacts import client_for
from test_email import consent, mailbox  # noqa: F401
from test_inbox import note

from leam_api.agenda import source_key
from leam_api.inbox_mail import KEY


def prepared(mailbox):
    client, app, state, account = mailbox
    consent(client, state, account)
    result = client.post(f"/api/email/accounts/{account}/sync", headers=H)
    assert result.status_code == 200
    items = client.get("/api/email").json()["items"]
    keys = [source_key("email", item["accountId"], item["id"]) for item in items]
    return client, app, state, account, keys


def test_mark_mail_read_unread_is_durable_local_only_and_idempotent(mailbox):
    client, app, state, _, keys = prepared(mailbox)
    calls = len(state["calls"])
    assert client.get("/api/inbox-mail/read").json() == {
        "readKeys": [],
        "removedKeys": [],
        "scope": "leam_only",
    }
    body = {"keys": keys, "read": True}
    assert client.post("/api/inbox-mail/read", json=body).status_code == 403
    first = client.post("/api/inbox-mail/read", json=body, headers=H)
    assert first.status_code == 200 and first.json()["scope"] == "leam_only"
    assert (
        client.post("/api/inbox-mail/read", json=body, headers=H).json() == first.json()
    )
    assert client.get("/api/inbox-mail/read").json()["readKeys"] == sorted(keys)
    assert client.get("/api/email").json()["items"][0][
        "unread"
    ]  # Original Gmail label unchanged.
    assert len(state["calls"]) == calls
    with app.state.store.connect() as db:
        raw = db.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()[0]
    assert "Synthetic subject" not in raw and set(json.loads(raw)) == set(keys)
    assert (
        client.post(
            "/api/inbox-mail/read", json={"keys": keys, "read": False}, headers=H
        ).status_code
        == 200
    )
    assert client.get("/api/inbox-mail/read").json()["readKeys"] == []
    assert len(state["calls"]) == calls


def test_foreign_keys_duplicates_disconnected_account_and_bounds_rejected(mailbox):
    client, _, state, account, keys = prepared(mailbox)
    calls = len(state["calls"])
    for body, expected in [
        ({"keys": ["foreign-account-message"], "read": True}, 409),
        ({"keys": [keys[0], keys[0]], "read": True}, 409),
        ({"keys": keys, "read": "false"}, 422),
        ({"keys": [keys[0]] * 101}, 422),
    ]:
        assert (
            client.post("/api/inbox-mail/read", json=body, headers=H).status_code
            == expected
        )
    assert len(state["calls"]) == calls
    assert client.delete(f"/api/email/accounts/{account}", headers=H).status_code == 200
    assert (
        client.post("/api/inbox-mail/read", json={"keys": keys}, headers=H).status_code
        == 409
    )
    assert client.get("/api/inbox-mail/read").json()["readKeys"] == []


def test_snapshot_mail_acknowledgement_does_not_acknowledge_later_arrivals(mailbox):
    # Production intentionally collapses each Gmail thread to its latest message.
    # Distinct arrivals must use distinct threads to remain two canonical items.
    mailbox[2]["unique_threads"] = True
    client, _, state, account, keys = prepared(mailbox)
    state["messages"] = 2
    assert (
        client.post(f"/api/email/accounts/{account}/sync", headers=H).status_code == 200
    )
    all_items = client.get("/api/email").json()["items"]
    assert len(all_items) == 2
    assert (
        client.post("/api/inbox-mail/read", json={"keys": keys}, headers=H).status_code
        == 200
    )
    assert client.get("/api/inbox-mail/read").json()["readKeys"] == keys


def test_marker_capacity_rolls_back_without_pruning(mailbox, monkeypatch):
    client, app, _, _, keys = prepared(mailbox)
    monkeypatch.setattr("leam_api.inbox_mail.MAX_MARKERS", 1)
    app.state.store.set(KEY, {"older-marker": 1})
    assert (
        client.post("/api/inbox-mail/read", json={"keys": keys}, headers=H).status_code
        == 429
    )
    assert app.state.store.get(KEY) == {"older-marker": 1}


def test_note_destination_unread_and_mail_auth_use_existing_boundaries(tmp_path):
    with client_for(tmp_path) as client:
        assert client.get("/api/inbox-mail/read").status_code == 401
        assert (
            client.post(
                "/api/inbox-mail/read", json={"keys": ["x"]}, headers=H
            ).status_code
            == 401
        )
        login(client)
        saved = client.post("/api/inbox", json=note(), headers=H).json()
        identity = saved["item"]["id"]
        assert saved["url"] == f"/?view=today&inboxItem={identity}#today/inbox"
        path = "/api/inbox/" + identity
        assert client.post(path + "/unread").status_code == 403
        assert client.post(path + "/read", headers=H).json()["item"]["unread"] is False
        assert client.post(path + "/unread", headers=H).json()["item"]["unread"] is True
        assert client.post(path + "/unread", headers=H).json()["unreadCount"] == 1
        assert client.get(path).json()["url"] == saved["url"]
