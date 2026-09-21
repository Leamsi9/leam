from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from leam_api.backlog import Assessment, Backlog, router
from leam_api.store import Store
from leam_api.updates import Publication, Updates


def assessment(**changes):
    return Assessment(
        feature="recovery-controls",
        title="Recovery controls",
        currentStep="Implement independent service restart",
        percent=35,
        blockers=["Remote wake host still required"],
        **changes,
    )


def test_route_persists_estimates_ages_without_inflation_and_excludes_deployed(
    tmp_path,
):
    now = [datetime.now(timezone.utc).timestamp()]
    store = Store(tmp_path)
    backlog = Backlog(store, clock=lambda: now[0])
    app = FastAPI()
    app.include_router(router(backlog))
    client = TestClient(app)
    saved = backlog.upsert(assessment())
    first = client.get("/api/backlog").json()
    assert first["items"][0]["percent"] == 35 and not first["items"][0]["stale"]
    now[0] += 1201
    later = client.get("/api/backlog").json()
    assert later["items"][0]["percent"] == 35 and later["items"][0]["stale"]
    assert (
        Backlog(Store(tmp_path)).list()["items"][0]["assessedAt"] == saved["assessedAt"]
    )
    updates = Updates(store)
    updates.publish(
        Publication(
            feature="recovery-controls",
            title="Recovery",
            summary="Deployed recovery controls",
            deploymentId="test-artifact",
            deployedAt=datetime.now(timezone.utc),
        )
    )
    assert client.get("/api/backlog").json()["items"] == []
    assert client.post("/api/backlog", json={}).status_code == 405


def test_validation_and_older_heartbeat_cannot_replace_newer_assessment(tmp_path):
    backlog = Backlog(Store(tmp_path))
    latest = backlog.upsert(assessment())
    earlier = assessment(assessedAt=datetime.now(timezone.utc) - timedelta(hours=1))
    with pytest.raises(ValueError, match="older"):
        backlog.upsert(earlier)
    assert backlog.list()["items"][0]["revision"] == latest["revision"]
    for change in [
        {"percent": 100},
        {"percent": -1},
        {"feature": "../path"},
        {"blockers": ["x"] * 21},
        {"assessedAt": "2026-01-01T00:00:00"},
    ]:
        base = assessment().model_dump()
        with pytest.raises(ValidationError):
            Assessment(**{**base, **change})


def test_real_app_auth_protects_backlog_and_cli_upsert(tmp_path):
    import json
    import subprocess
    import sys

    from test_api import FakeCodex, login

    from leam_api.app import create_app

    store = Store(tmp_path)
    path = tmp_path / "assessment.json"
    path.write_text(assessment().model_dump_json())
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "leam_api.backlog",
            "--data-dir",
            str(tmp_path),
            "upsert",
            "--input",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout)["percent"] == 35
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    app.include_router(router(Backlog(store)))
    with TestClient(app) as client:
        assert client.get("/api/backlog").status_code == 401
        login(client)
        assert (
            client.get("/api/backlog").json()["items"][0]["title"]
            == "Recovery controls"
        )


def test_backlog_route_orders_closest_to_deployment_first(tmp_path):
    backlog = Backlog(Store(tmp_path))
    for feature, percent in [
        ("nearly-ready", 90),
        ("started-latest", 10),
        ("middle", 50),
    ]:
        backlog.upsert(
            Assessment(
                feature=feature,
                title=feature,
                percent=percent,
                currentStep="Test assessment",
            )
        )
    app = FastAPI()
    app.include_router(router(backlog))
    with TestClient(app) as client:
        assert [i["feature"] for i in client.get("/api/backlog").json()["items"]] == [
            "nearly-ready",
            "middle",
            "started-latest",
        ]
