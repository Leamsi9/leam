from fastapi.testclient import TestClient

from leam_api.recovery import create_recovery_app


class Control:
    def __init__(self):
        self.calls = []
        self.active = False

    async def status(self):
        return [
            {
                "id": "app",
                "name": "Leam app",
                "unit": "leam-next-candidate.service",
                "active": "active" if self.active else "inactive",
                "loaded": "loaded",
                "sub": "running" if self.active else "dead",
                "reachable": self.active,
            }
        ]

    async def operate(self, service, action):
        self.calls.append((service, action))
        self.active = True


def setup(client, directory):
    result = client.post(
        "/api/auth/setup",
        headers={"origin": "http://testserver"},
        json={
            "bootstrap": (directory / "bootstrap-token").read_text(),
            "password": "separate-recovery-password",
        },
    )
    assert result.status_code == 200, result.text


def test_recovery_is_available_with_main_down_and_only_controls_candidate(tmp_path):
    control = Control()
    app = create_recovery_app(tmp_path, {"http://testserver"}, control=control)
    with TestClient(app, client=("127.0.0.1", 4444)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/services").status_code == 401
        setup(client, tmp_path)
        assert client.get("/api/services").json()["services"][0]["reachable"] is False
        assert client.post("/api/services/app/start", json={}).status_code == 403
        headers = {"origin": "http://testserver"}
        for target in ["leam.service", "leam-bootstrap.service", "recovery", "../app"]:
            assert client.post(
                f"/api/services/{target}/restart", json={}, headers=headers
            ).status_code in [404, 422]
        assert control.calls == []
        result = client.post("/api/services/app/start", json={}, headers=headers)
        assert result.status_code == 200
        assert control.calls == [("app", "start")]
        assert result.json()["services"][0]["reachable"] is True
        assert (
            client.post("/api/services/app/stop", json={}, headers=headers).status_code
            == 404
        )
        assert (
            client.post(
                "/api/services/app/restart", content=b"a" * 9000, headers=headers
            ).status_code
            == 413
        )
        assert (
            client.post("/api/auth/logout", json={}, headers=headers).status_code == 200
        )
        assert client.get("/api/services").status_code == 401


def test_recovery_rejects_cross_origin_remote_peer_and_limits_password_guesses(
    tmp_path,
):
    app = create_recovery_app(tmp_path, {"http://testserver"}, control=Control())
    with TestClient(app, client=("192.0.2.1", 4444)) as remote:
        assert remote.get("/").status_code == 403
    with TestClient(app, client=("127.0.0.1", 4444)) as client:
        setup(client, tmp_path)
        headers = {"origin": "http://testserver"}
        client.post("/api/auth/logout", json={}, headers=headers)
        for _ in range(5):
            result = client.post(
                "/api/auth/login", json={"password": "wrong-password"}, headers=headers
            )
            assert result.status_code in [401, 429]
        assert (
            client.post(
                "/api/auth/login", json={"password": "wrong-password"}, headers=headers
            ).status_code
            == 429
        )
        assert (
            client.get("/api/auth/status", headers={"host": "evil.example"}).status_code
            == 403
        )
        assert client.post(
            "/api/auth/login",
            json={"password": "do-not-echo", "unexpected": "do-not-echo"},
            headers=headers,
        ).status_code in [422, 429]


def test_systemctl_execution_is_fixed_and_does_not_accept_request_commands(
    tmp_path, monkeypatch
):
    import asyncio

    from leam_api.recovery import ServiceControl

    calls = []

    class Process:
        returncode = 0

        async def communicate(self):
            return b"LoadState=loaded\nActiveState=active\nSubState=running\n", b""

    async def execute(*arguments, **kwargs):
        calls.append((arguments, kwargs))
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)
    app = create_recovery_app(tmp_path, {"http://testserver"}, control=ServiceControl())
    with TestClient(app, client=("127.0.0.1", 4444)) as client:
        setup(client, tmp_path)
        assert (
            client.post(
                "/api/services/runtime/restart",
                json={},
                headers={"origin": "http://testserver"},
            ).status_code
            == 200
        )
    assert calls[0][0] == (
        "/usr/bin/systemctl",
        "--user",
        "restart",
        "leam-next-runtime.service",
    )
    assert calls[0][1] == {
        "stdin": asyncio.subprocess.DEVNULL,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    assert {call[0][3] for call in calls[1:]} == {
        "leam-next-candidate.service",
        "leam-next-mcp.service",
        "leam-next-runtime.service",
    }


def test_recovery_password_survives_its_restart_but_sessions_do_not(tmp_path):
    app = create_recovery_app(tmp_path, {"https://recovery.example"}, control=Control())
    with TestClient(
        app, base_url="https://recovery.example", client=("127.0.0.1", 4444)
    ) as client:
        code = (tmp_path / "bootstrap-token").read_text()
        result = client.post(
            "/api/auth/setup",
            headers={"origin": "https://recovery.example"},
            json={"bootstrap": code, "password": "separate-recovery-password"},
        )
        assert result.status_code == 200
        assert "Secure" in result.headers["set-cookie"]
        cookie = result.cookies.get("leam_recovery_session")
    app = create_recovery_app(tmp_path, {"https://recovery.example"}, control=Control())
    with TestClient(
        app, base_url="https://recovery.example", client=("127.0.0.1", 4444)
    ) as client:
        client.cookies.set("leam_recovery_session", cookie)
        assert client.get("/api/services").status_code == 401
        result = client.post(
            "/api/auth/login",
            headers={"origin": "https://recovery.example"},
            json={"password": "separate-recovery-password"},
        )
        assert result.status_code == 200
        assert client.get("/api/services").status_code == 200
        assert not (tmp_path / "bootstrap-token").exists()
        assert (tmp_path / "password-hash").stat().st_mode & 0o777 == 0o600


def test_recovery_runs_in_a_separate_process_without_main_application(tmp_path):
    import os
    import socket
    import subprocess
    import sys
    import time

    import httpx

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    directory = tmp_path / "independent"
    origin = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "leam_api.recovery:application",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-proxy-headers",
            "--no-access-log",
        ],
        env={
            **os.environ,
            "LEAM_RECOVERY_DIR": str(directory),
            "LEAM_RECOVERY_ORIGINS": origin,
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with httpx.Client(base_url=origin, trust_env=False, timeout=2) as client:
            for _ in range(50):
                try:
                    response = client.get("/api/auth/status")
                    break
                except httpx.ConnectError:
                    assert process.poll() is None
                    time.sleep(0.1)
            else:
                raise AssertionError("Recovery did not start")
            assert response.json() == {"authenticated": False, "configured": False}
            # No Leam API import, runtime credential, session or live companion is required.
            assert client.get("/").status_code == 200
            assert (
                client.post(
                    "/api/auth/setup",
                    headers={"origin": origin},
                    json={
                        "bootstrap": (directory / "bootstrap-token").read_text(),
                        "password": "separate-recovery-password",
                    },
                ).status_code
                == 200
            )
            status = client.get("/api/services")
            assert status.status_code == 200
            assert {service["id"] for service in status.json()["services"]} == {
                "app",
                "mcp",
                "runtime",
            }
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_recovery_rejects_exposed_credentials_and_never_echoes_passwords(tmp_path):
    import pytest

    directory = tmp_path / "private"
    app = create_recovery_app(directory, {"http://testserver"}, control=Control())
    with TestClient(app, client=("127.0.0.1", 4444)) as client:
        result = client.post(
            "/api/auth/login",
            headers={"origin": "http://testserver"},
            json={"password": "secret", "extra": "must-not-echo"},
        )
        assert result.status_code == 422
        assert "secret" not in result.text and "must-not-echo" not in result.text
        client.cookies.set("leam_session", "main-app-cookie")
        assert client.get("/api/services").status_code == 401
    (directory / "bootstrap-token").chmod(0o644)
    with pytest.raises(RuntimeError, match="private owned"):
        create_recovery_app(directory, {"http://testserver"}, control=Control())
