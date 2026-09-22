"""Authenticated caller coverage for recovered derived native observations."""

import json

from fastapi.testclient import TestClient
from test_api import FakeCodex, login
from test_backups import prepared
from test_usage import fixture, record
from test_usage_codex_trace import call, own_meta

from leam_api.app import create_app
from leam_api.backups import Backups, restore
from leam_api.store import Store
from leam_api.usage import UsageService
from leam_api.usage_codex_trace import codex_journey


def test_actual_authenticated_journey_routes_page_without_models_or_transcripts(
    tmp_path,
):
    service, importer, _ = fixture(
        tmp_path, {"thread-1": [own_meta(), call(), record()]}
    )
    importer.step()
    service.store.set("usage.settings", {"enabled": False})
    codex = FakeCodex()
    app = create_app(
        tmp_path / "data",
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=codex,
    )
    with TestClient(app) as client:
        assert client.get("/api/usage/journey?threadId=thread-1").status_code == 401
        login(client)
        response = client.get(
            "/api/usage/journey", params={"threadId": "thread-1", "limit": 1}
        )
        assert response.status_code == 200
        first = response.json()
        assert first["collection"] == "paused"
        assert first["counts"] == {"model_call": 1}
        assert first["inferredCounts"] == {"tool_call": 1}
        assert first["hasMore"] and first["nextOffset"] == 1
        second = client.get(
            "/api/usage/journey",
            params={"threadId": "thread-1", "limit": 1, "offset": 1},
        ).json()
        assert not second["hasMore"]
        assert first["items"][0]["id"] != second["items"][0]["id"]
        assert "PRIVATE_ARGUMENT" not in json.dumps([first, second])
        model = next(
            row
            for row in first["items"] + second["items"]
            if row["kind"] == "model_call"
        )
        assert model["tokens"]["total"] == "120"
        assert (
            client.get(
                "/api/usage/journey",
                params={"threadId": "thread-1", "turnId": "turn-1"},
            ).json()["total"]
            == 1
        )
        assert (
            client.get("/api/usage/journey?threadId=missing").json()["coverage"][
                "importComplete"
            ]
            is False
        )
        for query in [
            "",
            "?threadId=thread-1&limit=501",
            "?threadId=thread-1&offset=-1",
        ]:
            assert client.get("/api/usage/journey" + query).status_code == 422
        assert client.get("/api/usage?days=0").json()["totals"]["requests"] == 1
        assert codex.calls == []


def test_backup_export_restore_recognizes_derived_trace_tables(tmp_path):
    service, importer, _ = fixture(
        tmp_path, {"thread-1": [own_meta(), call(), record()]}
    )
    importer.step()
    source = tmp_path / "data"
    prepared(source)
    manager = Backups(service.store)
    archive = manager.path(manager.create()["id"])
    target = tmp_path / "restored"
    restore(archive, target, source)
    restored = UsageService(Store(target))
    assert codex_journey(restored.store, "thread-1") == codex_journey(
        service.store, "thread-1"
    )
    assert restored.overview(0)["totals"] == service.overview(0)["totals"]
