"""Exact period arithmetic through real UsageService/HTTP, no provider inference."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_api import FakeCodex, login

from leam_api.app import create_app
from leam_api.backups import validate_database
from leam_api.store import Store
from leam_api.usage import UsageService
from leam_api.usage_exhaustion import ExhaustionEvent

FIRST = "2026-09-21T10:22:39.509Z"
SECOND = "2026-09-21T14:04:03.236Z"


def body(stamp=FIRST, request_id=None):
    return ExhaustionEvent(
        requestId=request_id or uuid4(),
        provider="chatgpt",
        observedAt=stamp,
        timezone="Europe/London",
        source="user_report",
        sourceRef="user-turn:fixture",
        attributeCodexToChatGPT=True,
    )


def add(service, key, stamp, input_tokens=100, output_tokens=20, cached=80):
    with service.store.connect() as db:
        service.ingest(
            db,
            response_id=key,
            thread_id="thread",
            turn_id="turn",
            model="unknown",
            timestamp=datetime.fromisoformat(stamp).timestamp(),
            usage={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_input_tokens": cached,
                "reasoning_output_tokens": 5,
            },
        )


def test_events_create_periods_preserve_history_boundaries_and_late_imports(tmp_path):
    service = UsageService(Store(tmp_path))
    add(service, "before", "2026-09-21T10:00:00Z")
    add(service, "at-first", FIRST)
    add(service, "between", "2026-09-21T12:00:00Z")
    add(service, "at-second", SECOND)
    service.exhaustions.record(body())
    service.exhaustions.record(body(SECOND))
    data = service.exhaustions.overview()
    assert data["current"]["totals"]["total"] == "120"
    previous = data["periods"][1]
    assert previous["totals"]["requests"] == 2
    assert previous["totals"]["total"] == "240"
    assert previous["totals"]["cached"] == "160"
    assert previous["totals"]["nonCached"] == "80"
    assert service.overview(0)["totals"]["total"] == "480"
    add(service, "late", "2026-09-21T11:00:00Z")
    assert service.exhaustions.overview()["periods"][1]["totals"]["total"] == "360"


def test_request_id_conflict_and_duplicate_observation_converge(tmp_path):
    service = UsageService(Store(tmp_path))
    event = body()
    first = service.exhaustions.record(event)
    assert service.exhaustions.record(event) == first
    assert service.exhaustions.record(body()) == first
    with pytest.raises(HTTPException) as error:
        service.exhaustions.record(body(SECOND, event.requestId))
    assert error.value.status_code == 409
    assert len(service.exhaustions.overview()["events"]) == 1


def test_concurrent_duplicate_events_and_restart(tmp_path):
    service = UsageService(Store(tmp_path))
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: service.exhaustions.record(body()), range(8)))
    assert len({row["id"] for row in rows}) == 1
    restarted = UsageService(Store(tmp_path))
    assert len(restarted.exhaustions.overview()["events"]) == 1


def test_historical_event_insertion_repartitions_without_ledger_mutation(tmp_path):
    service = UsageService(Store(tmp_path))
    add(service, "between", "2026-09-21T12:00:00Z")
    service.exhaustions.record(body(SECOND))
    assert service.exhaustions.overview()["current"]["totals"]["total"] == "0"
    service.exhaustions.record(body())
    assert service.exhaustions.overview()["periods"][1]["totals"]["total"] == "120"
    assert service.records(0)["total"] == 1


def test_source_isolation_unknown_cache_and_conflicts(tmp_path):
    service = UsageService(Store(tmp_path))
    service.exhaustions.record(body())
    add(service, "kept", SECOND, cached=None)
    add(service, "foreign", SECOND)
    add(service, "conflict", SECOND)
    add(service, "conflict", SECOND, output_tokens=21)
    with service.store.connect() as db:
        db.execute(
            "UPDATE usage_records SET source='other-provider.response' WHERE id='codex:foreign'"
        )
    totals = service.exhaustions.overview()["current"]["totals"]
    assert totals["requests"] == 1
    assert totals["cached"] is None and totals["nonCached"] is None


def test_exact_decimal_totals_above_javascript_safe_integer(tmp_path):
    service = UsageService(Store(tmp_path))
    service.exhaustions.record(body())
    add(service, "large", SECOND)
    with service.store.connect() as db:
        db.execute(
            "UPDATE usage_records SET input=?,output=100,cached=NULL WHERE id='codex:large'",
            (9007199254740993,),
        )
    assert (
        service.exhaustions.overview()["current"]["totals"]["total"]
        == "9007199254741093"
    )


def test_timezone_offset_is_explicit_and_naive_or_unknown_provider_rejected():
    event = body("2026-09-21T11:22:39.509+01:00")
    assert (
        event.observedAt.astimezone(UTC).isoformat()
        == "2026-09-21T10:22:39.509000+00:00"
    )
    with pytest.raises(ValidationError):
        body("2026-09-21T11:22:39")
    with pytest.raises(ValidationError):
        ExhaustionEvent(**{**event.model_dump(), "provider": "other"})
    with pytest.raises(ValidationError):
        ExhaustionEvent(**{**event.model_dump(), "attributeCodexToChatGPT": False})


def test_plain_429_and_auth_errors_never_implicitly_create_event(tmp_path):
    service = UsageService(Store(tmp_path))
    service.store.event(
        "codex",
        {
            "method": "error",
            "params": {"error": {"code": 429, "message": "budget exhausted"}},
        },
    )
    service.store.event(
        "codex", {"method": "error", "params": {"error": {"code": 401}}}
    )
    data = service.exhaustions.overview()
    assert data["current"] is None and data["events"] == []
    assert data["coverage"]["automaticDetection"] == "not_connected"


def test_backup_schema_accepts_new_exhaustion_tables(tmp_path):
    service = UsageService(Store(tmp_path))
    service.exhaustions.record(body())
    validate_database(service.store.path)


def test_actual_authenticated_routes_and_no_model_calls(tmp_path):
    codex = FakeCodex()
    app = create_app(
        tmp_path, {"http://testserver"}, bootstrap="bootstrap-for-tests", codex=codex
    )
    with TestClient(app) as client:
        assert client.get("/api/usage/exhaustions").status_code == 401
        login(client)
        data = body().model_dump(mode="json")
        saved = client.post(
            "/api/usage/exhaustions", json=data, headers={"origin": "http://testserver"}
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["attribution"] == "user_declared"
        assert (
            client.get("/api/usage/exhaustions").json()["events"][0]["id"]
            == saved.json()["id"]
        )
        assert (
            client.post(
                "/api/usage/exhaustions",
                json=data,
                headers={"origin": "http://testserver"},
            ).json()
            == saved.json()
        )
        assert client.get("/api/usage/exhaustions?provider=unknown").status_code == 422
