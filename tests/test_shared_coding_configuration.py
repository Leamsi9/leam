"""Installation binding is optional and scoped to each app construction."""

import pytest
from fastapi.testclient import TestClient
from test_api import login
from test_shared_coding_api import OtherCodex

from leam_api.app import create_app

SYNTHETIC_THREAD = "00000000-0000-0000-0000-000000000001"


@pytest.mark.parametrize("configured", [None, "", "   "])
def test_unset_app_has_no_shared_listing_or_ide_connection(
    tmp_path, monkeypatch, configured
):
    if configured is None:
        monkeypatch.delenv("LEAM_SHARED_CODEX_THREAD", raising=False)
    else:
        monkeypatch.setenv("LEAM_SHARED_CODEX_THREAD", configured)
    observed = []

    async def forbidden_watch(self, thread):
        observed.append(thread)
        raise AssertionError("unset installation must not contact an IDE owner")
        yield  # async iterator interface

    monkeypatch.setattr(
        "leam_api.shared_session_stream.PrivateIdeStream.watch", forbidden_watch
    )
    bridge = OtherCodex()
    app = create_app(
        tmp_path, {"http://testserver"}, bootstrap="bootstrap-for-tests", codex=bridge
    )
    with TestClient(app) as client:
        login(client)
        response = client.get("/api/codex/threads")
        assert response.status_code == 200
        assert response.json()["data"] == []
        assert not app.state.shared_coding.owns(SYNTHETIC_THREAD)
        assert not app.state.shared_coding.owns(None)
        assert not app.state.shared_coding.owns("")
        read = client.get(f"/api/codex/threads/{SYNTHETIC_THREAD}")
        assert read.status_code == 200
        assert bridge.calls[-1][0] == "thread/read"
        removed = client.request(
            "DELETE",
            "/api/codex/threads/ordinary",
            headers={"origin": "http://testserver"},
            json={"confirmed": True, "deleteChildren": True, "stopRunning": True},
        )
        assert removed.status_code == 200, removed.text
        assert app.state.shared_coding.task is None
        assert observed == []


def test_configured_app_lists_and_protects_only_its_installation_binding(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEAM_SHARED_CODEX_THREAD", SYNTHETIC_THREAD)
    bridge = OtherCodex()
    app = create_app(
        tmp_path, {"http://testserver"}, bootstrap="bootstrap-for-tests", codex=bridge
    )
    # Changing the process environment cannot retarget an already-bound app.
    monkeypatch.setenv(
        "LEAM_SHARED_CODEX_THREAD", "00000000-0000-0000-0000-000000000002"
    )
    with TestClient(app) as client:
        login(client)
        data = client.get("/api/codex/threads").json()["data"]
        assert [row["id"] for row in data] == [SYNTHETIC_THREAD]
        assert data[0]["transport"] == "ide-owner"
        assert app.state.shared_coding.owns(SYNTHETIC_THREAD)
        assert not app.state.shared_coding.owns("ordinary")
        assert client.get("/api/codex/threads?cursor=next").json()["data"] == []
        before = len(bridge.calls)
        removed = client.request(
            "DELETE",
            f"/api/codex/threads/{SYNTHETIC_THREAD}",
            headers={"origin": "http://testserver"},
            json={"confirmed": True, "deleteChildren": True, "stopRunning": True},
        )
        assert removed.status_code == 409
        assert len(bridge.calls) == before
        assert app.state.shared_coding.task is None


def test_invalid_installation_binding_fails_without_echoing_the_value(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEAM_SHARED_CODEX_THREAD", "invalid-private-value")
    with pytest.raises(ValueError, match="LEAM_SHARED_CODEX_THREAD") as error:
        create_app(
            tmp_path,
            {"http://testserver"},
            bootstrap="bootstrap-for-tests",
            codex=OtherCodex(),
        )
    assert "invalid-private-value" not in str(error.value)
