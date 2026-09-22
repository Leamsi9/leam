"""Actual local-removal callers, retry boundaries and provider non-mutation."""
# ruff: noqa: F811 -- imported pytest fixture is intentionally injected.

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from test_api import login
from test_artifacts import client_for
from test_email import mailbox  # noqa: F401
from test_inbox import note
from test_inbox_mail import prepared

from leam_api.agenda import source_key
from leam_api.email import Emails
from leam_api.inbox import Inbox, InboxCreate
from leam_api.inbox_removal import PREFIX
from leam_api.store import Store

H = {"origin": "http://testserver"}


def request(*items, **changes):
    return {
        "requestId": str(uuid4()),
        "confirmed": True,
        "items": list(items),
        **changes,
    }


def test_remove_note_auth_retry_replay_and_read_watermark(tmp_path):
    with client_for(tmp_path) as client:
        payload = note()
        removal = request({"source": "leam", "id": payload["requestId"]})
        assert (
            client.post("/api/inbox/remove", json=removal, headers=H).status_code == 401
        )
        login(client)
        saved = client.post("/api/inbox", json=payload, headers=H).json()["item"]
        assert client.post("/api/inbox/remove", json=removal).status_code == 403
        first = client.post("/api/inbox/remove", json=removal, headers=H)
        assert first.status_code == 200, first.text
        assert first.json()["providerChanged"] is False
        assert first.json()["notes"] == {
            "total": 0,
            "unreadCount": 0,
            "throughSequence": saved["sequence"],
        }
        assert (
            client.post("/api/inbox/remove", json=removal, headers=H).json()
            == first.json()
        )
        assert client.get("/api/inbox/" + saved["id"]).status_code == 404
        assert (
            client.post("/api/inbox", json=payload, headers=H).json()["state"]
            == "removed"
        )
        assert (
            client.post(
                "/api/inbox", json={**payload, "body": "Changed"}, headers=H
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/inbox/read-all",
                json={"throughSequence": saved["sequence"]},
                headers=H,
            ).status_code
            == 200
        )
        store = Store(tmp_path)
        tombstone = store.get(PREFIX + "leam:" + saved["id"])
        assert set(tombstone) == {"fingerprint", "removedAt"}
        assert payload["body"] not in json.dumps(tombstone)
        assert Inbox(store).list()["items"] == []


def test_bulk_atomic_validation_and_id_conflicts(tmp_path):
    with client_for(tmp_path) as client:
        login(client)
        payload = note()
        client.post("/api/inbox", json=payload, headers=H)
        selected = {"source": "leam", "id": payload["requestId"]}
        invalid = request(selected, {"source": "mail", "id": "email:" + "a" * 64})
        assert (
            client.post("/api/inbox/remove", json=invalid, headers=H).status_code == 409
        )
        assert client.get("/api/inbox/status").json()["total"] == 1
        for data in (
            request(selected, selected),
            request(selected, confirmed="true"),
            request(*([selected] * 101)),
            request({"source": "mail", "id": "raw-id"}),
        ):
            assert (
                client.post("/api/inbox/remove", json=data, headers=H).status_code
                == 422
            )
        assert (
            client.post(
                "/api/inbox/remove", json=request(selected, confirmed=False), headers=H
            ).status_code
            == 422
        )
        valid = request(selected)
        assert (
            client.post("/api/inbox/remove", json=valid, headers=H).status_code == 200
        )
        changed = {**valid, "items": [{"source": "leam", "id": str(uuid4())}]}
        assert (
            client.post("/api/inbox/remove", json=changed, headers=H).status_code == 409
        )


def test_snapshot_selection_preserves_later_arrivals_and_pagination(tmp_path):
    with client_for(tmp_path) as client:
        login(client)
        items = [
            client.post("/api/inbox", json=note(subject=f"Note {i}"), headers=H).json()[
                "item"
            ]
            for i in range(4)
        ]
        first = client.get("/api/inbox?limit=2").json()
        later = client.post(
            "/api/inbox", json=note(subject="Later arrival"), headers=H
        ).json()["item"]
        body = request(
            *({"source": "leam", "id": item["id"]} for item in first["items"])
        )
        assert client.post("/api/inbox/remove", json=body, headers=H).status_code == 200
        older = client.get(f"/api/inbox?before={first['nextCursor']}&limit=2").json()
        assert {item["id"] for item in older["items"]} == {
            item["id"] for item in items[:2]
        }
        assert client.get("/api/inbox/" + later["id"]).json()["unread"]
        assert client.get("/api/inbox/status").json()["unreadCount"] == 3


def test_local_mail_suppression_survives_sync_without_provider_writes(mailbox):
    client, app, state, account, keys = prepared(mailbox)
    assert (
        client.post("/api/inbox-mail/read", json={"keys": keys}, headers=H).status_code
        == 200
    )
    before = len(state["calls"])
    payload = request({"source": "mail", "id": keys[0]})
    assert client.post("/api/inbox/remove", json=payload, headers=H).status_code == 200
    assert len(state["calls"]) == before
    status = client.get("/api/inbox-mail/read").json()
    assert status["removedKeys"] == keys and status["readKeys"] == keys
    assert client.get("/api/email").json()["items"][0]["unread"]
    assert (
        client.post(f"/api/email/accounts/{account}/sync", headers=H).status_code == 200
    )
    assert client.get("/api/inbox-mail/read").json()["removedKeys"] == keys
    assert all(
        call.method == "GET"
        for call in state["calls"]
        if call.url.host == "gmail.googleapis.com"
    )
    assert set(app.state.store.get(PREFIX + keys[0])) == {"removedAt"}


def test_account_message_keys_do_not_collide_and_missing_key_is_atomic(
    tmp_path, monkeypatch
):
    messages = [
        {"accountId": "first-account", "id": "same-provider-id"},
        {"accountId": "second-account", "id": "same-provider-id"},
    ]
    monkeypatch.setattr(Emails, "overview", lambda _: {"items": messages})
    keys = [source_key("email", item["accountId"], item["id"]) for item in messages]
    with client_for(tmp_path) as client:
        login(client)
        assert (
            client.post(
                "/api/inbox/remove",
                headers=H,
                json=request({"source": "mail", "id": keys[0]}),
            ).status_code
            == 200
        )
        assert client.get("/api/inbox-mail/read").json()["removedKeys"] == [keys[0]]
        assert keys[0] != keys[1]
        assert client.get("/api/inbox-mail/read").json()["readKeys"] == []


def test_capacity_failure_preserves_notes_and_existing_tombstones(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("leam_api.inbox_removal.MAX_REMOVALS", 1)
    with client_for(tmp_path) as client:
        login(client)
        items = [
            client.post("/api/inbox", json=note(), headers=H).json()["item"]
            for _ in range(2)
        ]
        body = request(*({"source": "leam", "id": item["id"]} for item in items))
        assert client.post("/api/inbox/remove", headers=H, json=body).status_code == 429
        assert client.get("/api/inbox/status").json()["total"] == 2
        with Store(tmp_path).connect() as db:
            assert (
                db.execute(
                    "SELECT count(*) FROM settings WHERE key GLOB 'inbox-removed:*'"
                ).fetchone()[0]
                == 0
            )


def test_concurrent_retries_remove_once_and_other_installation_isolated(tmp_path):
    with client_for(tmp_path / "one") as client:
        login(client)
        payload = note()
        item = client.post("/api/inbox", json=payload, headers=H).json()["item"]
        body = request({"source": "leam", "id": item["id"]})
        with ThreadPoolExecutor(2) as pool:
            replies = list(
                pool.map(
                    lambda _: client.post("/api/inbox/remove", headers=H, json=body),
                    range(2),
                )
            )
        assert all(reply.status_code == 200 for reply in replies)
        assert replies[0].json() == replies[1].json()
        separate = Inbox(Store(tmp_path / "two"))
        assert (
            separate.create(InboxCreate.model_validate(payload), origin="user")["state"]
            == "saved"
        )
        assert separate.status()["unreadCount"] == 1


def test_repeated_automation_notification_stays_removed_without_new_note(tmp_path):
    store = Store(tmp_path)
    inbox = Inbox(store)
    with store.connect() as db:
        created = inbox.emit_automation(
            db, event_id="same-action", subject="Updated", body="An action completed"
        )
    with client_for(tmp_path) as client:
        login(client)
        assert (
            client.post(
                "/api/inbox/remove",
                headers=H,
                json=request({"source": "leam", "id": created["item"]["id"]}),
            ).status_code
            == 200
        )
        with store.connect() as db:
            replay = inbox.emit_automation(
                db,
                event_id="same-action",
                subject="Updated",
                body="An action completed",
            )
        assert replay["state"] == "removed"
        assert client.get("/api/inbox/status").json()["total"] == 0


def test_cached_mail_tombstone_survives_snapshot_replacement(mailbox):
    client, _app, state, account, keys = prepared(mailbox)
    assert (
        client.post(
            "/api/inbox/remove",
            headers=H,
            json=request({"source": "mail", "id": keys[0]}),
        ).status_code
        == 200
    )
    state["messages"] = 0
    assert (
        client.post(f"/api/email/accounts/{account}/sync", headers=H).status_code == 200
    )
    assert client.get("/api/email").json()["items"] == []
    calls = len(state["calls"])
    result = client.get("/api/inbox-mail/read", params=[("keys", keys[0])])
    assert result.status_code == 200 and result.json()["removedKeys"] == keys
    assert len(state["calls"]) == calls
    assert (
        client.get("/api/inbox-mail/read", params=[("keys", keys[0])] * 101).status_code
        == 422
    )
    assert (
        client.get("/api/inbox-mail/read", params={"keys": "foreign-key"}).status_code
        == 422
    )
