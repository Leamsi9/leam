"""Local shared-owner discovery must not wait for native session discovery."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from test_api import login
from test_shared_coding_api import OtherCodex

from leam_api.app import create_app

OWNER = "00000000-0000-0000-0000-000000000001"


@pytest.mark.parametrize("owner", [None, OWNER])
def test_shared_metadata_is_authenticated_local_and_does_not_start_owner(
    tmp_path, monkeypatch, owner
):
    if owner:
        monkeypatch.setenv("LEAM_SHARED_CODEX_THREAD", owner)
    else:
        monkeypatch.delenv("LEAM_SHARED_CODEX_THREAD", raising=False)
    bridge = OtherCodex()
    app = create_app(
        tmp_path, {"http://testserver"}, bootstrap="bootstrap-for-tests", codex=bridge
    )
    with TestClient(app) as client:
        assert client.get("/api/codex/shared-thread").status_code == 401
        login(client)
        response = client.get("/api/codex/shared-thread")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        value = response.json()
        assert value["configured"] is bool(owner)
        if owner:
            assert value["thread"]["id"] == owner
            assert value["thread"]["transport"] == "ide-owner"
            assert "turns" not in value["thread"]
        else:
            assert value["thread"] is None
        assert bridge.calls == []
        assert app.state.shared_coding.task is None


def test_shared_metadata_responds_while_native_list_is_still_pending(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEAM_SHARED_CODEX_THREAD", OWNER)

    class Held(OtherCodex):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = None

        async def request(self, method, params, *, expected_generation=None):
            if method == "thread/list":
                self.calls.append((method, params, expected_generation))
                self.release = asyncio.Event()
                self.entered.set()
                await self.release.wait()
                return {
                    "data": [{"id": "native", "name": "Native fixture"}],
                    "nextCursor": "next-native",
                }
            return await super().request(
                method, params, expected_generation=expected_generation
            )

    bridge = Held()
    app = create_app(
        tmp_path, {"http://testserver"}, bootstrap="bootstrap-for-tests", codex=bridge
    )
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        login(client)
        native = pool.submit(client.get, "/api/codex/threads")
        try:
            assert bridge.entered.wait(2)
            assert not native.done()
            response = client.get("/api/codex/shared-thread")
            assert response.status_code == 200
            assert response.json()["thread"]["id"] == OWNER
            assert not native.done(), "local metadata waited for native discovery"
        finally:
            if bridge.release is not None:
                client.portal.call(bridge.release.set)
        result = native.result(timeout=2).json()
        assert [row["id"] for row in result["data"]] == [OWNER, "native"]
        assert result["nextCursor"] == "next-native"
        assert bridge.calls == [
            (
                "thread/list",
                {
                    "limit": 50,
                    "sourceKinds": ["cli", "vscode", "appServer"],
                    "sortKey": "recency_at",
                    "sortDirection": "desc",
                    "archived": False,
                },
                None,
            )
        ]
