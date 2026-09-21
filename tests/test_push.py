import base64
import json

import http_ece
import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app


def subscription(endpoint="https://fcm.googleapis.com/fcm/send/test-only"):
    key = ec.generate_private_key(ec.SECP256R1())
    auth = b"0123456789abcdef"
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )

    def encode(b):
        return base64.urlsafe_b64encode(b).decode().rstrip("=")

    return (
        {
            "endpoint": endpoint,
            "keys": {"p256dh": encode(public), "auth": encode(auth)},
        },
        key,
        auth,
    )


def test_push_encrypts_and_persists_provider_acceptance_without_claiming_display(
    tmp_path,
):
    sub, key, auth = subscription()
    calls = []

    def receive(request):
        calls.append(request)
        decoded = json.loads(
            http_ece.decrypt(
                request.content, private_key=key, auth_secret=auth, version="aes128gcm"
            )
        )
        assert decoded["title"] == "Leam"
        assert "test notification" in decoded["body"]
        assert request.headers["authorization"].startswith("vapid t=")
        assert request.headers["content-encoding"] == "aes128gcm"
        return httpx.Response(201)

    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        push_transport=httpx.MockTransport(receive),
    )
    h = {"origin": "http://testserver"}
    with TestClient(app) as c:
        login(c)
        assert not c.get("/api/notifications/status").json()["pushConfigured"]
        assert (
            c.put(
                "/api/push/config",
                json={"contact": "mailto:test@example.com"},
                headers=h,
            ).status_code
            == 200
        )
        device = c.post(
            "/api/push/devices",
            json={"name": "Test phone", "subscription": sub},
            headers=h,
        ).json()
        job = c.post(
            "/api/push/devices/" + device["id"] + "/test", json={}, headers=h
        ).json()
        assert c.get("/api/notifications/status").json()["pushConfigured"]
        c.portal.call(app.state.push.tick)
        status = c.get("/api/push/status").json()
        assert len(calls) == 1
        assert status["deliveries"][0]["state"] == "accepted"
        assert status["deliveries"][0]["confirmedAt"] is None
        assert "endpoint" not in json.dumps(status)
        c.portal.call(app.state.push.tick)
        assert len(calls) == 1
        assert (
            c.post(
                "/api/push/deliveries/" + job["id"] + "/confirm", json={}, headers=h
            ).status_code
            == 200
        )
        assert (
            c.get("/api/push/status").json()["deliveries"][0]["confirmedAt"] is not None
        )


def test_push_endpoint_and_key_validation_rejects_local_and_malformed_targets(tmp_path):
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    h = {"origin": "http://testserver"}
    with TestClient(app) as c:
        login(c)
        c.put(
            "/api/push/config", json={"contact": "mailto:test@example.com"}, headers=h
        )
        for endpoint in [
            "http://fcm.googleapis.com/x",
            "https://127.0.0.1/x",
            "https://fcm.googleapis.com.evil.test/x",
            "https://user:pass@fcm.googleapis.com/x",
            "https://fcm.googleapis.com:443/x",
            "https://fcm.googleapis.com/x#secret",
        ]:
            sub, _, _ = subscription(endpoint)
            assert (
                c.post(
                    "/api/push/devices",
                    json={"name": "Bad", "subscription": sub},
                    headers=h,
                ).status_code
                == 422
            )
        sub, _, _ = subscription()
        sub["keys"]["p256dh"] = "bad"
        assert (
            c.post(
                "/api/push/devices",
                json={"name": "Bad", "subscription": sub},
                headers=h,
            ).status_code
            == 422
        )


def test_push_retries_after_restart_and_disables_gone_subscription(tmp_path):
    sub, _, _ = subscription()
    clock = [1800000000.0]
    calls = []

    def receive(request):
        calls.append(request)
        return httpx.Response(
            503 if len(calls) == 1 else 410, headers={"Retry-After": "120"}
        )

    def make():
        return create_app(
            tmp_path,
            {"http://testserver"},
            bootstrap="bootstrap-for-tests",
            codex=FakeCodex(),
            push_transport=httpx.MockTransport(receive),
            scheduler_clock=lambda: clock[0],
        )

    h = {"origin": "http://testserver"}
    app = make()
    with TestClient(app) as c:
        login(c)
        c.put(
            "/api/push/config", json={"contact": "mailto:test@example.com"}, headers=h
        )
        device = c.post(
            "/api/push/devices", json={"name": "Phone", "subscription": sub}, headers=h
        ).json()
        c.post("/api/push/devices/" + device["id"] + "/test", json={}, headers=h)
        c.portal.call(app.state.push.tick)
        assert c.get("/api/push/status").json()["deliveries"][0]["state"] == "pending"
    app = make()
    with TestClient(app) as c:
        c.post(
            "/api/auth/login", json={"password": "long-password-for-tests"}, headers=h
        )
        c.portal.call(app.state.push.tick)
        assert len(calls) == 1
        clock[0] += 121
        c.portal.call(app.state.push.tick)
        status = c.get("/api/push/status").json()
        assert status["devices"][0]["state"] == "expired"
        assert status["deliveries"][0]["state"] == "failed"


def test_reminder_delivery_cancels_retry_after_completion_and_rejects_redirect(
    tmp_path,
):
    sub, _, _ = subscription()
    clock = [1800000000.0]
    calls = []

    def receive(request):
        calls.append(request)
        return httpx.Response(
            503 if len(calls) == 1 else 302,
            headers={"location": "http://127.0.0.1/private"},
        )

    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        push_transport=httpx.MockTransport(receive),
        scheduler_clock=lambda: clock[0],
    )
    h = {"origin": "http://testserver"}
    with TestClient(app) as c:
        login(c)
        c.put(
            "/api/push/config", json={"contact": "mailto:test@example.com"}, headers=h
        )
        device = c.post(
            "/api/push/devices", json={"name": "Phone", "subscription": sub}, headers=h
        ).json()
        c.post(
            "/api/commitments",
            json={
                "title": "Private title never sent",
                "kind": "habit",
                "reminderTime": "00:00",
                "timezone": "UTC",
            },
            headers=h,
        )
        c.post("/api/notifications/check", json={}, headers=h)
        notice = c.get("/api/notifications").json()["items"][0]
        c.portal.call(app.state.push.tick)
        assert len(calls) == 1
        assert c.get("/api/push/status").json()["deliveries"][0]["state"] == "pending"
        c.post(
            "/api/notifications/" + notice["id"] + "/complete",
            json={"revision": notice["revision"]},
            headers=h,
        )
        clock[0] += 120
        c.portal.call(app.state.push.tick)
        assert len(calls) == 1
        assert c.get("/api/push/status").json()["deliveries"][0]["state"] == "cancelled"
        c.post("/api/push/devices/" + device["id"] + "/test", json={}, headers=h)
        c.portal.call(app.state.push.tick)
        assert len(calls) == 2
        assert c.get("/api/push/status").json()["deliveries"][0]["state"] == "failed"
        assert (
            c.delete("/api/push/devices/" + device["id"], headers=h).status_code == 200
        )
        assert c.get("/api/push/status").json()["devices"] == []


def test_interrupted_key_write_does_not_publish_partial_identity(tmp_path, monkeypatch):
    import pytest

    import leam_api.push as module

    original = module.os.fdopen

    class BrokenWriter:
        def __init__(self, fd, mode):
            self.file = original(fd, mode)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.file.close()

        def write(self, value):
            raise OSError("interrupted write")

    monkeypatch.setattr(module.os, "fdopen", BrokenWriter)
    with pytest.raises(OSError, match="interrupted write"):
        create_app(
            tmp_path,
            {"http://testserver"},
            bootstrap="bootstrap-for-tests",
            codex=FakeCodex(),
        )
    assert not (tmp_path / "push-vapid.pem").exists()


def test_old_response_cannot_expire_replacement_subscription(tmp_path):
    import asyncio

    old, _, _ = subscription()
    replacement, _, _ = subscription()
    entered, release = asyncio.Event(), asyncio.Event()

    async def receive(request):
        entered.set()
        await release.wait()
        return httpx.Response(410)

    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        push_transport=httpx.MockTransport(receive),
    )
    h = {"origin": "http://testserver"}
    with TestClient(app) as c:
        login(c)
        c.put(
            "/api/push/config", json={"contact": "mailto:test@example.com"}, headers=h
        )
        device = c.post(
            "/api/push/devices", json={"name": "Phone", "subscription": old}, headers=h
        ).json()
        c.post("/api/push/devices/" + device["id"] + "/test", json={}, headers=h)
        future = c.portal.start_task_soon(app.state.push.tick)
        c.portal.call(entered.wait)
        c.post(
            "/api/push/devices",
            json={"name": "Renewed phone", "subscription": replacement},
            headers=h,
        )
        c.portal.call(release.set)
        future.result(timeout=5)
        assert c.get("/api/push/status").json()["devices"][0]["state"] == "active"


def test_concurrent_key_initializers_publish_one_identity_and_keep_it(tmp_path):
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from leam_api.push import Push
    from leam_api.store import Store

    store = Store(tmp_path)
    barrier = threading.Barrier(4)

    def initialize():
        barrier.wait(timeout=5)
        push = Push(store)
        try:
            return push.public_key
        finally:
            asyncio.run(push.close())

    with ThreadPoolExecutor(max_workers=4) as executor:
        identities = list(executor.map(lambda _: initialize(), range(4)))
    assert len(set(identities)) == 1
    path = tmp_path / "push-vapid.pem"
    original = path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    push = Push(store)
    assert push.public_key == identities[0]
    assert path.read_bytes() == original
    asyncio.run(push.close())
