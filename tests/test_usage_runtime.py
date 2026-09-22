"""Caller tests use the real loopback adapter + mocked GET-only runtime transport."""

import asyncio
import json
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app
from leam_api.ironclaw import IronClaw
from leam_api.store import Store
from leam_api.usage import UsageService
from leam_api.usage_runtime import RuntimeUsageImporter, attempts

THREAD, RUN, CALL = (str(uuid.uuid4()) for _ in range(3))
OWNER = ["tenant-a", "user-a"]


def snapshot(status="succeeded", usage=None):
    return {
        "snapshot": {
            "scope": dict(
                zip(
                    ["tenant_id", "user_id", "thread_id", "run_id"],
                    [*OWNER, THREAD, RUN],
                )
            ),
            "prompt": {"reconstructed_prompt": "SECRET PROMPT"},
            "model_calls": [
                {
                    "call_id": CALL,
                    "iteration": 1,
                    "requested_model": {"content": "gpt-5.6-sol"},
                    "effective_model": None,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "completed_at": None,
                    "duration_ms": 123,
                    "status": status,
                    "usage": usage
                    if usage is not None
                    else {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "cache_read_input_tokens": 80,
                    },
                    "failure_summary": {"content": "SECRET FAILURE"},
                }
            ],
            "tool_executions": [{"output": "SECRET TOOL"}],
        }
    }


def setup(tmp_path, current=None):
    service = UsageService(Store(tmp_path / "data"))
    data = {"value": current if current is not None else snapshot(), "owner": OWNER}
    calls = []

    def response(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.headers["authorization"] == "Bearer fixture"
        if request.url.path.endswith("/session"):
            return httpx.Response(
                200, json=dict(zip(["tenant_id", "user_id"], data["owner"]))
            )
        assert request.url.path.endswith(f"/inspector/threads/{THREAD}/runs/{RUN}")
        return httpx.Response(200, json=data["value"])

    service.runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="fixture",
        transport=httpx.MockTransport(response),
    )
    with service.store.connect() as db:
        # Use existing dispatch storage contract, not a new import test hook.
        db.execute(
            "INSERT INTO runtime_actions(id,fingerprint,path,body,result) VALUES (?,?,?,?,?)",
            (
                "request",
                "fixture",
                "/channels/test/messages",
                json.dumps({"thread_id": THREAD, "content": "SECRET DISPATCH"}),
                json.dumps({"run_id": RUN}),
            ),
        )
    return service, RuntimeUsageImporter(service), data, calls


def test_real_adapter_retries_restart_dedupe_and_privacy(tmp_path):
    s, collector, data, calls = setup(tmp_path)
    asyncio.run(collector.step())
    asyncio.run(RuntimeUsageImporter(s).step())
    assert s.overview(0)["totals"]["total"] == "120"
    rows = attempts(s)
    assert rows["total"] == 1
    assert rows["items"][0]["cached"] == "80"
    assert rows["items"][0]["provider"] is None
    assert rows["items"][0]["reasoning"] is None
    with s.store.connect() as db:
        persisted = " ".join(
            row[0] for row in db.execute("SELECT body FROM usage_runtime_attempts")
        )
    assert "SECRET" not in persisted
    assert all(request.method == "GET" for request in calls)
    data["value"]["snapshot"]["model_calls"][0]["call_id"] = str(uuid.uuid4())
    asyncio.run(collector.step())
    assert s.overview(0)["totals"]["total"] == "240"
    assert len(s.records(0)["items"]) == 2


def test_started_unknown_failed_known_and_late_started(tmp_path):
    s, collector, data, _ = setup(tmp_path, snapshot("started", {}))
    asyncio.run(collector.step())
    assert attempts(s)["items"][0]["input"] is None
    assert s.overview(0)["totals"]["requests"] == 0
    data["value"] = snapshot("failed")
    asyncio.run(collector.step())
    assert s.overview(0)["totals"]["total"] == "120"
    data["value"] = snapshot("started", {})
    asyncio.run(collector.step())
    assert attempts(s)["items"][0]["status"] == "failed"
    assert s.overview(0)["totals"]["requests"] == 1


def test_conflicting_final_evidence_quarantined(tmp_path):
    s, collector, data, _ = setup(tmp_path)
    asyncio.run(collector.step())
    data["value"]["snapshot"]["model_calls"][0]["usage"]["output_tokens"] = 90
    asyncio.run(collector.step())
    assert s.overview(0)["totals"]["requests"] == 0
    assert attempts(s)["items"][0]["conflict"]


@pytest.mark.parametrize(
    "changes",
    [
        {"input_tokens": True, "output_tokens": 1},
        {"input_tokens": -1, "output_tokens": 1},
        {"input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 11},
        {
            "input_tokens": 10,
            "output_tokens": 1,
            "cache_read_input_tokens": 6,
            "cache_creation_input_tokens": 6,
        },
    ],
)
def test_invalid_usage_cannot_pollute_ledger(tmp_path, changes):
    s, collector, _, _ = setup(tmp_path, snapshot(usage=changes))
    asyncio.run(collector.step())
    assert attempts(s)["total"] == 0
    assert s.runtime_coverage()["status"] == "error"
    assert s.runtime_coverage()["lastSuccess"] is None


def test_owner_and_thread_scope_fenced(tmp_path):
    s, collector, data, _ = setup(tmp_path)
    data["value"]["snapshot"]["scope"]["user_id"] = "someone-else"
    asyncio.run(collector.step())
    assert attempts(s)["total"] == 0
    data["value"] = snapshot()
    asyncio.run(collector.step())
    data["owner"] = ["new-tenant", "new-user"]
    with pytest.raises(ValueError, match="owner changed"):
        asyncio.run(collector.step())
    assert attempts(s)["total"] == 1


def test_absent_snapshot_not_zero_usage(tmp_path):
    s, collector, _, _ = setup(tmp_path, {"snapshot": None})
    asyncio.run(collector.step())
    assert "1 unavailable" in s.runtime_coverage()["details"]
    assert attempts(s)["total"] == 0
    assert s.runtime_coverage()["lastSuccess"] is None


def test_authenticated_api_runtime_pagination_and_sources(tmp_path):
    s, collector, _, _ = setup(tmp_path)
    asyncio.run(collector.step())
    with s.store.connect() as db:
        s.ingest(
            db,
            response_id="native",
            thread_id=THREAD,
            turn_id=RUN,
            model="same-model",
            timestamp=datetime.now(timezone.utc).timestamp(),
            usage={"input_tokens": 3, "output_tokens": 2},
        )
    app = create_app(
        s.store.path.parent,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    client = TestClient(app)
    assert client.get("/api/usage/runtime-attempts").status_code == 401
    login(client)
    page = client.get("/api/usage/runtime-attempts?limit=1").json()
    assert page["total"] == 1
    assert client.get("/api/usage/runtime-attempts?offset=1").json()["items"] == []
    assert client.get("/api/usage/runtime-attempts?threadId=other").json()["total"] == 0
    assert client.get("/api/usage/runtime-attempts?limit=101").status_code == 422
    rows = client.get("/api/usage/records?days=0").json()["items"]
    assert {row["source"] for row in rows} == {
        "codex.response",
        "ironclaw.model_attempt",
    }
    assert len({row["threadId"] for row in rows}) == 2


def test_backup_schema_accepts_older_and_new_usage_tables(tmp_path):
    from leam_api.backups import validate_database

    old = Store(tmp_path / "old")
    validate_database(old.path)
    s, collector, _, _ = setup(tmp_path)
    asyncio.run(collector.step())
    validate_database(s.store.path)


def test_exhaustion_counter_does_not_absorb_runtime_attempts(tmp_path):
    from leam_api.usage_exhaustion import ExhaustionEvent

    s, collector, _, _ = setup(tmp_path)
    asyncio.run(collector.step())
    s.exhaustions.record(
        ExhaustionEvent(
            requestId=uuid.uuid4(),
            provider="chatgpt",
            observedAt="2026-09-20T00:00:00Z",
            timezone="UTC",
            source="user_report",
            sourceRef="fixture",
            attributeCodexToChatGPT=True,
        )
    )
    assert s.overview(0)["totals"]["total"] == "120"
    assert s.exhaustions.overview()["current"]["totals"]["total"] == "0"


def test_paused_collector_never_calls_runtime(tmp_path):
    s, _, _, calls = setup(tmp_path)
    s.store.set("usage.settings", {"enabled": False})

    async def run():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert calls == []
    assert s.runtime_coverage()["status"] == "paused"
