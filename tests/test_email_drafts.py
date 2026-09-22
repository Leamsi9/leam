import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx2
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_accounts import H
from test_email import mailbox as _mailbox

from leam_api.backups import validate_vault
from leam_api.email_drafts import DraftSave, EmailDrafts
from leam_api.mcp_server import create_mcp_app
from leam_api.store import Store
from leam_api.vault import Vault

mailbox = _mailbox


def payload(account, **extra):
    return {
        "revision": 0,
        "accountId": account,
        "to": ["person@example.test"],
        "subject": "Private draft",
        "body": "  Private draft body\n",
        **extra,
    }


def test_actual_routes_are_local_encrypted_revision_guarded_and_retry_safe(
    mailbox, tmp_path
):
    c, app, state, account = mailbox
    key = str(uuid.uuid4())
    url = "/api/email/drafts/" + key
    before = len(state["calls"])
    body = payload(account)
    r = c.put(url, json=body, headers=H)
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["body"] == body["body"] and first["revision"] == 1
    assert (
        first["storage"] == "leam" and not first["sent"] and not first["savedInGmail"]
    )
    assert c.put(url, json=body, headers=H).json() == first
    assert c.put(url, json={**body, "body": "other"}, headers=H).status_code == 409
    updated = c.put(
        url, json={**body, "revision": 1, "body": "edited"}, headers=H
    ).json()
    assert updated["revision"] == 2 and c.get(url).json() == updated
    listing = c.get("/api/email/drafts").json()
    assert listing["total"] == 1 and "body" not in listing["items"][0]
    raw = app.state.store.get("email-draft:" + key)
    assert "Private draft" not in raw and "edited" not in raw
    assert len(state["calls"]) == before
    with app.state.store.connect() as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    validate_vault(app.state.store.path, (tmp_path / "accounts-key").read_bytes())
    assert c.delete(url, headers=H).status_code == 422
    assert (
        c.request(
            "DELETE", url, json={"revision": 1, "confirmed": True}, headers=H
        ).status_code
        == 409
    )
    assert (
        c.request(
            "DELETE", url, json={"revision": 2, "confirmed": False}, headers=H
        ).status_code
        == 422
    )
    assert (
        c.request(
            "DELETE", url, json={"revision": 2, "confirmed": True}, headers=H
        ).json()["gmailChanged"]
        is False
    )
    assert c.get(url).status_code == 404
    # A delayed create retry must never resurrect deleted content or reset revision.
    assert c.put(url, json=body, headers=H).status_code == 410
    assert c.request(
        "DELETE", url, json={"revision": 2, "confirmed": True}, headers=H
    ).json()["removed"]


@pytest.mark.parametrize(
    "edit",
    [
        {"subject": "Hello\r\nBcc: injected"},
        {"to": ["a\nb"]},
        {"revision": True},
        {"accountId": "foreign-account"},
        {"unknown": "no"},
        {"body": "x" * 100001},
    ],
)
def test_save_validation_through_route(mailbox, edit):
    c, _app, _state, account = mailbox
    r = c.put(
        "/api/email/drafts/" + str(uuid.uuid4()),
        json=payload(account, **edit),
        headers=H,
    )
    assert r.status_code == 422
    assert c.get("/api/email/drafts").json()["total"] == 0


def test_auth_origin_account_scope_and_corrupt_vault(mailbox, tmp_path):
    c, app, _state, account = mailbox
    key = str(uuid.uuid4())
    url = "/api/email/drafts/" + key
    assert (
        c.put(
            url, json=payload(account), headers={"origin": "https://foreign.test"}
        ).status_code
        == 403
    )
    assert c.put(url, json=payload(account), headers=H).status_code == 200
    other = Store(tmp_path / "other")
    drafts = EmailDrafts(other, Vault(other.path.parent))
    with pytest.raises(HTTPException) as caught:
        drafts.read(key)
    assert caught.value.status_code == 404
    app.state.store.set("email-draft:" + key, "broken")
    assert c.get(url).status_code == 503
    with app.state.store.connect() as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    with pytest.raises(ValueError):
        validate_vault(app.state.store.path, (tmp_path / "accounts-key").read_bytes())
    c.cookies.clear()
    assert c.get("/api/email/drafts").status_code == 401
    assert c.get(url).status_code == 401
    assert c.put(url, json=payload(account), headers=H).status_code == 401


def test_concurrent_cas_and_restart_persistence(mailbox):
    _c, app, _state, account = mailbox
    key = str(uuid.uuid4())
    drafts = app.state.email_drafts
    drafts.save(key, DraftSave(**payload(account)))

    def edit(body):
        try:
            return drafts.save(
                key, DraftSave(**payload(account, revision=1, body=body))
            )["revision"]
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(edit, ["first", "second"])) == [2, 409]
    restored = EmailDrafts(
        Store(app.state.store.path.parent), Vault(app.state.store.path.parent)
    )
    assert restored.read(key)["revision"] == 2


def test_mcp_actual_caller_uses_same_local_operation_and_never_provider(
    mailbox, tmp_path
):
    c, app, state, account = mailbox
    key = str(uuid.uuid4())
    token = (tmp_path / "tools-token").read_text().strip()
    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        with TestClient(app, client=("127.0.0.1", 4211)) as internal:
            response = internal.post(
                "/api/internal/tools",
                content=request.content,
                headers={
                    "authorization": "Bearer " + token,
                    "content-type": "application/json",
                },
            )
        return httpx2.Response(response.status_code, json=response.json())

    mcp_app = create_mcp_app(
        "http://127.0.0.1:46400", token, transport=httpx2.MockTransport(handle)
    )
    before = len(state["calls"])
    with TestClient(
        mcp_app, base_url="http://127.0.0.1:46420", client=("127.0.0.1", 4321)
    ) as bridge:
        result = bridge.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "leam_email_draft",
                    "arguments": {
                        "action": "save",
                        "id": key,
                        "draft": payload(account),
                    },
                },
            },
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/json, text/event-stream",
            },
        )
        assert result.status_code == 200, result.text
        assert not result.json()["result"].get("isError")
    assert seen[0]["tool"] == "leam_email_draft"
    assert c.get("/api/email/drafts/" + key).json()["revision"] == 1
    assert len(state["calls"]) == before
