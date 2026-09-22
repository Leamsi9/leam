"""Exercise renewal through authenticated API and the actual failed-delivery worker."""

import httpx
from fastapi.testclient import TestClient
from test_api import FakeCodex, login
from test_push import subscription

from leam_api.app import create_app


def test_expired_credentials_cannot_be_reactivated_but_fresh_keys_can(tmp_path):
    calls = []

    def gone(request):
        calls.append(request)
        return httpx.Response(410)

    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        push_transport=httpx.MockTransport(gone),
        scheduler_clock=lambda: 1800000000,
    )
    h = {"origin": "http://testserver"}
    sub, _, _ = subscription()
    with TestClient(app) as c:
        assert (
            c.post(
                "/api/push/devices",
                json={"name": "Phone", "subscription": sub},
                headers=h,
            ).status_code
            == 401
        )
        login(c)
        c.put(
            "/api/push/config", json={"contact": "mailto:test@example.com"}, headers=h
        )
        body = {"name": "Phone", "subscription": sub}
        response = c.post("/api/push/devices", json=body, headers=h)
        assert response.status_code == 200
        device = response.json()["id"]
        # Healthy registration is idempotent and does not deliver a notification.
        assert c.post("/api/push/devices", json=body, headers=h).json()["id"] == device
        assert calls == []
        c.post(f"/api/push/devices/{device}/test", json={}, headers=h)
        c.portal.call(app.state.push.tick)
        assert len(calls) == 1
        assert c.get("/api/push/status").json()["devices"][0]["state"] == "expired"
        assert c.post("/api/push/devices", json=body, headers=h).status_code == 409
        # Neither a changed display name nor a caller-provided future expiry fixes keys.
        altered = {**sub, "expirationTime": 1900000000000}
        assert (
            c.post(
                "/api/push/devices",
                json={"name": "Renamed", "subscription": altered},
                headers=h,
            ).status_code
            == 409
        )
        assert c.get("/api/push/status").json()["devices"][0]["state"] == "expired"
        fresh, _, _ = subscription(sub["endpoint"])
        assert (
            c.post(
                "/api/push/devices",
                json={"name": "Phone", "subscription": fresh},
                headers={"origin": "https://evil.test"},
            ).status_code
            == 403
        )
        assert c.post(
            "/api/push/devices",
            json={"name": "Phone", "subscription": fresh},
            headers=h,
        ).json() == {"id": device, "name": "Phone", "state": "active"}
        c.portal.call(app.state.push.tick)
        assert len(calls) == 1  # Renewal does not requeue the failed test.
        assert len(c.get("/api/push/status").json()["devices"]) == 1


def test_browser_expiration_timestamp_is_not_accepted_as_live(tmp_path):
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        scheduler_clock=lambda: 1800000000,
    )
    h = {"origin": "http://testserver"}
    sub, _, _ = subscription()
    with TestClient(app) as c:
        login(c)
        c.put(
            "/api/push/config", json={"contact": "mailto:test@example.com"}, headers=h
        )
        for expiration in [0, 1799999999999, 1800000000000]:
            assert (
                c.post(
                    "/api/push/devices",
                    json={
                        "name": "Phone",
                        "subscription": {**sub, "expirationTime": expiration},
                    },
                    headers=h,
                ).status_code
                == 409
            )
        assert c.get("/api/push/status").json()["devices"] == []
        assert (
            c.post(
                "/api/push/devices",
                json={
                    "name": "Phone",
                    "subscription": {**sub, "expirationTime": 1800000000001},
                },
                headers=h,
            ).status_code
            == 200
        )
