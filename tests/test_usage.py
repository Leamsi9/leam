import json
import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient
from test_api import FakeCodex, login
from leam_api.app import create_app
from leam_api.store import Store
from leam_api.usage import UsageService
from leam_api.usage_sources import CodexUsageImporter


def fixture(tmp_path, records):
    home = tmp_path / "codex"
    home.mkdir()
    sessions = home / "sessions"
    sessions.mkdir()
    with sqlite3.connect(home / "state_5.sqlite") as db:
        db.execute(
            "CREATE TABLE threads(id TEXT PRIMARY KEY,rollout_path TEXT,updated_at INTEGER)"
        )
        db.execute(
            "CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT,status TEXT)"
        )
        for thread, items in records.items():
            path = sessions / (thread + ".jsonl")
            path.write_text("".join(json.dumps(r) + "\n" for r in items))
            db.execute("INSERT INTO threads VALUES (?,?,?)", (thread, str(path), 1))
    service = UsageService(Store(tmp_path / "data"), home)
    return service, CodexUsageImporter(service), home


def record(response="response-1", thread="thread-1", usage=None):
    return {
        "type": "token_usage_record",
        "timestamp": "2026-09-20T18:00:00Z",
        "payload": {
            "thread_id": thread,
            "turn_id": "turn-1",
            "response_id": response,
            "usage": usage
            or {
                "input_tokens": 100,
                "cached_input_tokens": 80,
                "cache_write_input_tokens": 0,
                "output_tokens": 20,
                "reasoning_output_tokens": 5,
                "total_tokens": 120,
            },
        },
    }


def test_import_caller_deduplicates_foreign_ancestors_and_preserves_temporal_model(
    tmp_path,
):
    context = {
        "type": "turn_context",
        "payload": {"turn_id": "turn-1", "model": "model-one"},
    }
    s, importer, home = fixture(
        tmp_path,
        {
            "thread-1": [context, record(), record()],
            "child": [record(), record("child-response", "child")],
        },
    )
    importer.step()
    importer.step()
    result = s.overview(0)
    assert result["totals"] == {
        "requests": 2,
        "input": "200",
        "output": "40",
        "total": "240",
        "cached": "160",
        "reasoning": "10",
        "nonCached": "80",
        "cacheKnownRequests": 2,
    }
    rows = s.records(0)["items"]
    assert next(r for r in rows if r["threadId"] == "thread-1")["model"] == "model-one"
    assert next(r for r in rows if r["threadId"] == "child")["model"] == "unknown"
    assert "prompt" not in json.dumps(rows)


def test_conflicting_final_identity_quarantined_not_summed(tmp_path):
    second = record()
    second["payload"]["usage"]["output_tokens"] = 30
    second["payload"]["usage"]["total_tokens"] = 130
    s, importer, _ = fixture(tmp_path, {"thread-1": [record(), second]})
    importer.step()
    assert s.overview(0)["totals"]["requests"] == 0
    assert s.overview(0)["coverage"]["conflicts"] == 1


def test_unknown_cache_not_zero_and_bad_counts_rejected(tmp_path):
    valid = record(usage={"input_tokens": 100, "output_tokens": 20})
    bad = record("bool")
    bad["payload"]["usage"]["input_tokens"] = True
    s, importer, _ = fixture(tmp_path, {"thread-1": [valid, bad]})
    importer.step()
    t = s.overview(0)["totals"]
    assert t["requests"] == 1 and t["cached"] is None and t["nonCached"] is None


def test_partial_tail_and_replacement_resume_without_duplicate_spend(tmp_path):
    s, importer, home = fixture(tmp_path, {"thread-1": [record()]})
    path = home / "sessions/thread-1.jsonl"
    tail = json.dumps(record("second"))
    path.write_text(path.read_text() + tail[:40])
    importer.step()
    assert s.overview(0)["totals"]["requests"] == 1
    with path.open("a") as file:
        file.write(tail[40:] + "\n")
    importer.step()
    assert s.overview(0)["totals"]["requests"] == 2
    replacement = path.with_suffix(".new")
    replacement.write_text(json.dumps(record()) + "\n")
    replacement.replace(path)
    importer.step()
    assert s.overview(0)["totals"]["requests"] == 2


def test_fork_is_not_automatically_goal_parent_and_explicit_delegate_is(tmp_path):
    s, importer, home = fixture(
        tmp_path,
        {
            "thread-1": [record()],
            "fork": [
                {
                    "type": "session_meta",
                    "payload": {"id": "fork", "forked_from_id": "thread-1"},
                },
                record("fork-response", "fork"),
            ],
            "child": [record("child-response", "child")],
        },
    )
    with sqlite3.connect(home / "state_5.sqlite") as db:
        db.execute(
            "INSERT INTO thread_spawn_edges VALUES (?,?,?)",
            ("thread-1", "child", "running"),
        )
    importer.step()
    from leam_api.usage import GoalAssociation

    goal = s.associate(
        GoalAssociation(name="Build", threadId="thread-1", includeDescendants=True)
    )
    assert s.overview(0, goal=goal["id"])["totals"]["requests"] == 2
    assert s.overview(0)["coverage"]["unassignedRequests"] == 1


def test_authenticated_actual_routes_settings_goal_export_and_no_model_calls(tmp_path):
    codex = FakeCodex()
    app = create_app(
        tmp_path,
        origins={"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=codex,
    )
    with TestClient(app) as client:
        assert client.get("/api/usage").status_code == 401
        login(client)
        service = app.state.usage
        with service.store.connect() as db:
            service.ingest(
                db,
                response_id="live-1",
                thread_id="thread-1",
                turn_id="turn-1",
                model="model-one",
                timestamp=time.time(),
                usage={
                    "input_tokens": 1000,
                    "cached_input_tokens": 700,
                    "output_tokens": 100,
                    "reasoning_output_tokens": 30,
                },
            )
        headers = {"origin": "http://testserver"}
        settings = {
            "enabled": False,
            "dailyWarningTokens": 1000,
            "largeCallTokens": 1000,
        }
        assert (
            client.put("/api/usage/settings", json=settings, headers=headers).json()
            == settings
        )
        response = client.get("/api/usage").json()
        assert response["warning"]["exceeded"]
        assert response["coverage"]["sources"][0]["status"] == "paused"
        assert (
            client.post(
                "/api/usage/goals",
                json={"name": "Build", "threadId": "unknown"},
                headers=headers,
            ).status_code
            == 422
        )
        goal = client.post(
            "/api/usage/goals",
            json={"name": "Build", "threadId": "thread-1"},
            headers=headers,
        ).json()
        assert (
            client.get("/api/usage/records", params={"goal": goal["id"]}).json()[
                "total"
            ]
            == 1
        )
        export = client.get("/api/usage/export").json()
        assert export["metadataOnly"] and export["items"][0]["total"] == "1100"
        assert client.get("/api/usage?days=-1").status_code == 422
        assert client.get("/api/usage/records?limit=101").status_code == 422
        assert (
            client.post(
                "/api/usage/goals",
                json={"name": "X", "threadId": "thread-1"},
                headers={"origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert codex.calls == []


def test_malformed_usage_and_timestamp_do_not_stall_following_rows(tmp_path):
    malformed = record("malformed")
    malformed["payload"]["usage"] = []
    bad_stamp = record("stamp")
    bad_stamp["timestamp"] = 17
    s, importer, _ = fixture(tmp_path, {"thread-1": [malformed, bad_stamp, record()]})
    importer.step()
    importer.step()
    assert s.overview(0)["totals"]["requests"] == 1
    assert s.store.get("usage.source")["issues"] == 2


def test_reassociate_parent_recomputes_inherited_goals_preserving_explicit_child(
    tmp_path,
):
    from leam_api.usage import GoalAssociation

    s, importer, home = fixture(
        tmp_path,
        {name: [record(name, name)] for name in ["root", "child", "override", "leaf"]},
    )
    with sqlite3.connect(home / "state_5.sqlite") as db:
        db.executemany(
            "INSERT INTO thread_spawn_edges VALUES (?,?,?)",
            [
                ("root", "child", "done"),
                ("root", "override", "done"),
                ("override", "leaf", "done"),
            ],
        )
    importer.step()
    old = s.associate(
        GoalAssociation(name="A", threadId="root", includeDescendants=True)
    )
    explicit = s.associate(
        GoalAssociation(name="Own", threadId="override", includeDescendants=True)
    )
    new = s.associate(
        GoalAssociation(name="B", threadId="root", includeDescendants=True)
    )
    assert s.overview(0, goal=old["id"])["totals"]["requests"] == 0
    assert s.overview(0, goal=new["id"])["totals"]["requests"] == 2
    assert s.overview(0, goal=explicit["id"])["totals"]["requests"] == 2


def test_idle_index_stays_caught_up(tmp_path):
    s, importer, _ = fixture(tmp_path, {"thread-1": [record()]})
    importer.step()
    importer.step()
    assert s.store.get("usage.source")["status"] == "ready"
