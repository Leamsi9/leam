import json
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app

H = {"origin": "http://testserver"}


def provider_transport(provider, calls):
    def handle(request):
        calls.append(request)
        if request.url.path.endswith("/token"):
            body = parse_qs(request.content.decode())
            assert body["client_secret"] == ["private-app-secret"]
            if body["grant_type"] == ["authorization_code"]:
                assert len(body["code_verifier"][0]) >= 43
            return httpx2.Response(
                200,
                json={
                    "access_token": "private-access-token",
                    "refresh_token": "private-refresh-token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        assert request.headers["authorization"] == "Bearer private-access-token"
        if provider == "google":
            return httpx2.Response(
                200, json={"sub": "google-identity", "email": "test@example.com"}
            )
        return httpx2.Response(
            200,
            json={
                "id": "microsoft-identity",
                "userPrincipalName": "test@example.com",
                "displayName": "Example",
            },
        )

    return httpx2.MockTransport(handle)


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_account_authorization_is_bound_single_use_and_secret_material_is_encrypted(
    tmp_path, provider
):
    calls = []
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        account_transport=provider_transport(provider, calls),
    )
    with TestClient(app) as c:
        login(c)
        config = c.put(
            "/api/accounts/providers/" + provider,
            json={"clientId": "client-for-tests", "clientSecret": "private-app-secret"},
            headers=H,
        )
        assert config.status_code == 200, config.text
        started = c.post(
            "/api/accounts/providers/" + provider + "/authorize", json={}, headers=H
        ).json()
        params = parse_qs(urlsplit(started["url"]).query)
        assert params["code_challenge_method"] == ["S256"]
        assert "private-app-secret" not in started["url"]
        state = params["state"][0]
        callback = (
            "/api/accounts/oauth/"
            + provider
            + "/callback?code=synthetic-code&state="
            + state
        )
        # Cross-site redirect does not carry the Strict session cookie.
        saved = c.cookies.get("leam_session")
        c.cookies.clear()
        result = c.get(callback, follow_redirects=False)
        assert result.status_code == 303, result.text
        assert "connected" in result.headers["location"]
        assert c.get(callback, follow_redirects=False).status_code == 400
        assert len(calls) == 2
        c.cookies.set("leam_session", saved)
        status = c.get("/api/accounts").json()
        account = status["items"][0]
        assert account["provider"] == provider
        assert account["identity"] == "test@example.com"
        assert "private-" not in json.dumps(status)
        with app.state.store.connect() as db:
            dump = "\n".join(db.iterdump())
        for secret in [
            "private-app-secret",
            "private-access-token",
            "private-refresh-token",
            "synthetic-code",
        ]:
            assert secret not in dump
        assert c.delete("/api/accounts/" + account["id"], headers=H).status_code == 200
        assert c.get("/api/accounts").json()["items"] == []


def test_oauth_state_rejects_provider_mixup_and_logout(tmp_path):
    calls = []
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        account_transport=provider_transport("google", calls),
    )
    with TestClient(app) as c:
        login(c)
        c.put(
            "/api/accounts/providers/google",
            json={"clientId": "client-for-tests", "clientSecret": "private-app-secret"},
            headers=H,
        )
        started = c.post(
            "/api/accounts/providers/google/authorize", json={}, headers=H
        ).json()
        state = parse_qs(urlsplit(started["url"]).query)["state"][0]
        assert (
            c.get(
                "/api/accounts/oauth/microsoft/callback?code=x&state=" + state
            ).status_code
            == 400
        )
        c.post("/api/auth/logout", json={}, headers=H)
        assert (
            c.get(
                "/api/accounts/oauth/google/callback?code=x&state=" + state
            ).status_code
            == 400
        )
        assert calls == []


def connect(c, provider="google"):
    c.put(
        "/api/accounts/providers/" + provider,
        json={"clientId": "client-for-tests", "clientSecret": "private-app-secret"},
        headers=H,
    )
    started = c.post(
        "/api/accounts/providers/" + provider + "/authorize", json={}, headers=H
    ).json()
    state = parse_qs(urlsplit(started["url"]).query)["state"][0]
    assert (
        c.get(
            "/api/accounts/oauth/"
            + provider
            + "/callback?code=synthetic-code&state="
            + state,
            follow_redirects=False,
        ).status_code
        == 303
    )
    return c.get("/api/accounts").json()["items"][0]


def test_expired_state_and_provider_failure_do_not_leak_or_call_token_endpoint(
    tmp_path,
):
    calls = []
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        account_transport=provider_transport("google", calls),
    )
    with TestClient(app) as c:
        login(c)
        c.put(
            "/api/accounts/providers/google",
            json={"clientId": "client-for-tests", "clientSecret": "private-app-secret"},
            headers=H,
        )
        started = c.post(
            "/api/accounts/providers/google/authorize", json={}, headers=H
        ).json()
        state = parse_qs(urlsplit(started["url"]).query)["state"][0]
        with app.state.store.connect() as db:
            db.execute("UPDATE oauth_states SET expires=0")
        assert (
            c.get(
                "/api/accounts/oauth/google/callback?code=secret-code&state=" + state
            ).status_code
            == 400
        )
        assert calls == []


def test_refresh_rotation_is_persisted_and_invalid_grant_requires_reconnect(tmp_path):
    calls = []
    refreshes = []
    fail = [False]
    base = provider_transport("google", calls)

    def handle(request):
        body = (
            parse_qs(request.content.decode())
            if request.url.path.endswith("/token")
            else {}
        )
        if body.get("grant_type") == ["refresh_token"]:
            refreshes.append(body["refresh_token"][0])
            if fail[0]:
                return httpx2.Response(
                    400,
                    json={
                        "error": "invalid_grant",
                        "error_description": "private-provider-error",
                    },
                )
            return httpx2.Response(
                200,
                json={
                    "access_token": "private-access-token",
                    "refresh_token": "rotated-private-token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        return base.handler(request)

    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        account_transport=httpx2.MockTransport(handle),
    )
    with TestClient(app) as c:
        login(c)
        account = connect(c)

        def expire():
            with app.state.store.connect() as db:
                row = db.execute(
                    "SELECT body FROM accounts WHERE id=?", (account["id"],)
                ).fetchone()
                data = app.state.accounts.vault.open(
                    "account:" + account["id"], row["body"]
                )
                data["token"]["expires_at"] = 0
                db.execute(
                    "UPDATE accounts SET body=? WHERE id=?",
                    (
                        app.state.accounts.vault.seal("account:" + account["id"], data),
                        account["id"],
                    ),
                )

        expire()
        assert (
            c.post(
                "/api/accounts/" + account["id"] + "/verify", json={}, headers=H
            ).status_code
            == 200
        )
        assert refreshes == ["private-refresh-token"]
        assert (
            c.post(
                "/api/accounts/" + account["id"] + "/verify", json={}, headers=H
            ).status_code
            == 200
        )
        assert len(refreshes) == 1
        expire()
        fail[0] = True
        failed = c.post(
            "/api/accounts/" + account["id"] + "/verify", json={}, headers=H
        )
        assert failed.status_code == 409
        assert "private-provider-error" not in failed.text
        assert refreshes[-1] == "rotated-private-token"
        assert c.get("/api/accounts").json()["items"][0]["state"] == "reconnect"


def test_configuration_change_during_verification_keeps_reconnect_state(tmp_path):
    import asyncio

    entered, release = asyncio.Event(), asyncio.Event()
    delayed = [False]
    base = provider_transport("google", [])

    async def handle(request):
        if delayed[0] and request.url.path.endswith("/userinfo"):
            entered.set()
            await release.wait()
        return base.handler(request)

    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        account_transport=httpx2.MockTransport(handle),
    )
    with TestClient(app) as c:
        login(c)
        account = connect(c)
        delayed[0] = True
        cookie = c.cookies.get("leam_session")

        async def exercise():
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://testserver",
                cookies={"leam_session": cookie},
            ) as client:
                checking = asyncio.create_task(
                    client.post(
                        "/api/accounts/" + account["id"] + "/verify", json={}, headers=H
                    )
                )
                await entered.wait()
                changing = asyncio.create_task(
                    client.put(
                        "/api/accounts/providers/google",
                        json={"clientId": "different-client", "revision": 1},
                        headers=H,
                    )
                )
                await asyncio.sleep(0.02)
                release.set()
                results = await asyncio.gather(checking, changing)
                assert all(r.status_code == 200 for r in results)

        c.portal.call(exercise)
        assert c.get("/api/accounts").json()["items"][0]["state"] == "reconnect"


def test_secret_validation_never_echoes_submitted_values(tmp_path):
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    with TestClient(app) as c:
        login(c)
        secret = "private-secret-marker-" * 220
        result = c.put(
            "/api/accounts/providers/google",
            json={"clientId": "example", "clientSecret": secret},
            headers=H,
        )
        assert result.status_code == 422
        assert "private-secret-marker" not in result.text
        assert "input" not in result.json()["detail"][0]


def test_authorized_account_call_cannot_send_token_to_foreign_origin(tmp_path):
    from fastapi import HTTPException

    calls = []
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        account_transport=provider_transport("google", calls),
    )
    with TestClient(app) as c:
        login(c)
        account = connect(c)
        calls.clear()
        for url in [
            "https://evil.example/token-trap",
            "http://www.googleapis.com/calendar/v3/calendars",
            "https://www.googleapis.com.evil.test/calendar/v3/x",
            "https://user@www.googleapis.com/calendar/v3/x",
        ]:
            with pytest.raises(HTTPException):
                c.portal.call(app.state.accounts.authorized, account["id"], "GET", url)
        assert calls == []


def test_disconnect_invalidates_an_earlier_authorization(tmp_path):
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        account_transport=provider_transport("google", []),
    )
    with TestClient(app) as c:
        login(c)
        account = connect(c)
        started = c.post(
            "/api/accounts/providers/google/authorize", json={}, headers=H
        ).json()
        state = parse_qs(urlsplit(started["url"]).query)["state"][0]
        assert c.delete("/api/accounts/" + account["id"], headers=H).status_code == 200
        assert (
            c.get(
                "/api/accounts/oauth/google/callback?code=synthetic-code&state="
                + state,
                follow_redirects=False,
            ).status_code
            == 400
        )
        assert c.get("/api/accounts").json()["items"] == []


def test_forgetting_application_removes_connections_and_pending_flows(tmp_path):
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        account_transport=provider_transport("google", []),
    )
    with TestClient(app) as c:
        login(c)
        connect(c)
        started = c.post(
            "/api/accounts/providers/google/authorize", json={}, headers=H
        ).json()
        state = parse_qs(urlsplit(started["url"]).query)["state"][0]
        assert c.delete("/api/accounts/providers/google", headers=H).status_code == 200
        status = c.get("/api/accounts").json()
        assert status["items"] == []
        assert not status["providers"][0]["secretConfigured"]
        assert (
            c.get(
                "/api/accounts/oauth/google/callback?code=synthetic-code&state="
                + state,
                follow_redirects=False,
            ).status_code
            == 400
        )
