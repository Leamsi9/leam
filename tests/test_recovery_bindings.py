import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_mobile_restore import fixture

from leam_api import restore_services
from leam_api.recovery import SERVICES


@pytest.mark.parametrize(
    "wrong", ["", "client", "app_cwd", "mcp_cwd", "app_data", "mcp_data"]
)
def test_restore_rejects_wrong_serving_generation(tmp_path, monkeypatch, wrong):
    from fastapi.testclient import TestClient
    from test_api import FakeCodex

    from leam_api.app import create_app

    with TestClient(
        create_app(
            tmp_path / "health-caller",
            {"http://testserver"},
            bootstrap="fixture",
            codex=FakeCodex(),
        )
    ) as client:
        actual_health = client.get("/api/health").json()
    _, _, deployment, runtime = fixture(tmp_path)
    value = deployment.read()
    pids = {"app": 61001, "mcp": 61002}

    class Control:
        async def status(self):
            return [
                {"id": name, "active": "active", "reachable": True} for name in SERVICES
            ]

        async def _systemctl(self, *arguments):
            role = next(name for name, row in SERVICES.items() if row[1] in arguments)
            return str(pids[role])

    services = restore_services.FixedRestoreServices(deployment, Control())

    async def identity():
        return runtime

    services.identity = identity

    def response(request):
        if request.url.path == "/api/health":
            return httpx.Response(200, json=actual_health)
        if request.url.path == "/mcp":
            return httpx.Response(
                200, json={"result": {"tools": [{"name": "fixture"}]}}
            )
        return httpx.Response(
            200,
            content=b"wrong shell" if wrong == "client" else b"fixture public shell",
        )

    original_client = httpx.AsyncClient

    def client(**kwargs):
        kwargs["transport"] = httpx.MockTransport(response)
        return original_client(**kwargs)

    monkeypatch.setattr(restore_services.httpx, "AsyncClient", client)
    original_resolve, original_read = Path.resolve, Path.read_bytes

    def resolve(path, *args, **kwargs):
        for role, pid in pids.items():
            if str(path) == f"/proc/{pid}/cwd":
                return (
                    Path("/wrong/fixture")
                    if wrong == role + "_cwd"
                    else Path(value["releaseDirectory"])
                )
        return original_resolve(path, *args, **kwargs)

    def read(path):
        for role, pid in pids.items():
            if str(path) == f"/proc/{pid}/environ":
                data = (
                    "/wrong/fixture"
                    if wrong == role + "_data"
                    else value["dataDirectory"]
                )
                return ("LEAM_DATA_DIR=" + data + "\0").encode()
        return original_read(path)

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(Path, "read_bytes", read)
    ticks = iter(range(0, 1000, 10))
    monkeypatch.setattr(
        restore_services, "time", SimpleNamespace(monotonic=lambda: next(ticks))
    )
    assert asyncio.run(services.healthy()) is (not wrong)
