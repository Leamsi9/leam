"""Caller fixtures use observed installed Codex 0.155.1 field shapes, no transcripts."""

import json

import pytest
from test_usage import fixture, record

from leam_api.usage_codex_trace import codex_journey


def native(kind, payload, second=1, ordinal=None):
    item = {
        "type": kind,
        "timestamp": f"2026-09-20T18:00:{second:02d}Z",
        "payload": payload,
    }
    if ordinal is not None:
        item["ordinal"] = ordinal
    return item


def own_meta(thread="thread-1", boundary=None):
    payload = {"id": thread, "timestamp": "2026-09-20T18:00:00Z"}
    if boundary is not None:
        payload["subagent_history_start_ordinal"] = boundary
    return native("session_meta", payload, 0)


def call(second=2, ordinal=None):
    return native(
        "response_item",
        {
            "type": "function_call",
            "call_id": "call-1",
            "name": "exec_command",
            "arguments": '{"cmd":"PRIVATE_ARGUMENT"}',
        },
        second,
        ordinal,
    )


def test_caller_ordered_steps_and_exact_usage_without_contents(tmp_path):
    usage = record()
    usage["timestamp"] = "2026-09-20T18:00:03Z"
    s, importer, _ = fixture(
        tmp_path,
        {
            "thread-1": [
                own_meta(),
                native("turn_context", {"turn_id": "turn-1", "model": "model-one"}),
                call(),
                usage,
                native(
                    "response_item",
                    {
                        "type": "function_call_output",
                        "call_id": "call-1",
                        "output": "PRIVATE_RESULT",
                    },
                    4,
                ),
                native(
                    "compacted",
                    {"message": "PRIVATE_REASONING", "replacement_history": []},
                    5,
                ),
            ]
        },
    )
    importer.step()
    journey = codex_journey(s.store, "thread-1", "turn-1")
    assert [x["kind"] for x in journey["items"]] == [
        "configuration",
        "tool_call",
        "model_call",
        "tool_result",
        "compaction",
    ]
    assert journey["counts"] == {"model_call": 1}
    assert journey["inferredCounts"]["tool_call"] == 1
    model = journey["items"][2]
    assert (
        model["tokens"]["total"] == "120"
        and model["usageRecordId"] == "codex:response-1"
    )
    assert journey["items"][1]["tokens"] is None
    assert journey["items"][1]["turnAttribution"] == "inferred"
    assert model["turnAttribution"] == "exact"
    assert journey["items"][3]["outputBytes"] == len("PRIVATE_RESULT")
    with s.store.connect() as db:
        dump = "\n".join(db.iterdump())
    assert "PRIVATE_" not in dump
    assert str(tmp_path) not in json.dumps(journey)
    assert journey["coverage"]["importComplete"]


def test_inherited_metadata_excluded_native_ordinals_exact_and_no_boundary_unknown(
    tmp_path,
):
    rows = [
        own_meta("child", 10),
        call(2, 2),
        call(3, 11),
        record("own", "child"),
        record(),
    ]
    s, importer, _ = fixture(
        tmp_path, {"child": rows, "unknown": [call(), record("unknown", "unknown")]}
    )
    importer.step()
    child = codex_journey(s.store, "child")
    assert child["counts"] == {"model_call": 1, "tool_call": 1}
    assert child["coverage"]["excludedInherited"] >= 1
    unknown = codex_journey(s.store, "unknown")
    assert unknown["counts"] == {"model_call": 1}
    assert unknown["coverage"]["unattributedSteps"] == 1


def test_replay_rotation_same_inode_rewrite_partial_and_pagination(tmp_path):
    s, importer, home = fixture(tmp_path, {"thread-1": [own_meta(), call(), record()]})
    path = home / "sessions/thread-1.jsonl"
    original = path.read_text()
    importer.step()
    total = codex_journey(s.store, "thread-1")["total"]
    importer.step()
    path.with_suffix(".replacement").write_text(original)
    path.with_suffix(".replacement").replace(path)
    importer.step()
    assert codex_journey(s.store, "thread-1")["total"] == total
    tail = json.dumps(record("second"))
    with path.open("a") as f:
        f.write(tail[:40])
    importer.step()
    with path.open("a") as f:
        f.write(tail[40:] + "\n")
    importer.step()
    assert codex_journey(s.store, "thread-1")["counts"]["model_call"] == 2
    path.write_text(original.replace("response-1", "response-2"))
    importer.step()
    assert codex_journey(s.store, "thread-1")["counts"]["model_call"] == 3
    page = codex_journey(s.store, "thread-1", limit=1)
    assert len(page["items"]) == 1 and page["hasMore"] and page["nextOffset"] == 1


def test_steps_and_cursor_roll_back_together(tmp_path, monkeypatch):
    import leam_api.usage_sources as source

    s, importer, _ = fixture(tmp_path, {"thread-1": [own_meta(), call(), record()]})
    original = source.observe

    def fail(*args):
        original(*args)
        raise RuntimeError("interrupted")

    monkeypatch.setattr(source, "observe", fail)
    with pytest.raises(RuntimeError):
        importer.step()
    with s.store.connect() as db:
        assert db.execute("SELECT count(*) FROM usage_codex_steps").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM usage_files").fetchone()[0] == 0
    monkeypatch.setattr(source, "observe", original)
    importer.step()
    assert codex_journey(s.store, "thread-1")["counts"]["model_call"] == 1


def test_recent_append_imports_while_history_remains_and_progress_completes(tmp_path):
    rows = {
        f"thread-{i:02}": [record(f"response-{i}", f"thread-{i:02}")] for i in range(30)
    }
    s, importer, home = fixture(tmp_path, rows)
    importer.step()
    hot = home / "sessions/thread-00.jsonl"
    with hot.open("a") as f:
        f.write(json.dumps(record("fresh", "thread-00")) + "\n")
    importer.step()
    assert codex_journey(s.store, "thread-00")["counts"]["model_call"] == 2
    assert s.store.get("usage.source")["completedThreads"] < 30
    for _ in range(5):
        importer.step()
    assert s.store.get("usage.source")["completedThreads"] == 30
    assert s.store.get("usage.source")["status"] == "ready"


def test_existing_usage_cursor_backfills_steps_without_charging_again(tmp_path):
    s, importer, _ = fixture(tmp_path, {"thread-1": [own_meta(), call(), record()]})
    importer.step()
    with s.store.connect() as db:
        db.execute("DELETE FROM usage_codex_scan")
        db.execute("DELETE FROM usage_codex_steps")
    importer.step()
    assert codex_journey(s.store, "thread-1")["total"] == 2
    assert s.overview(0)["totals"]["requests"] == 1


def test_conflict_usage_has_no_tokens_and_nested_settings_updates_model(tmp_path):
    altered = record()
    altered["payload"]["usage"]["output_tokens"] = 30
    altered["payload"]["usage"]["total_tokens"] = 130
    s, importer, _ = fixture(
        tmp_path,
        {
            "thread-1": [
                own_meta(),
                native("turn_context", {"turn_id": "turn-1", "model": "original"}),
                native(
                    "event_msg",
                    {
                        "type": "thread_settings_applied",
                        "thread_id": "thread-1",
                        "thread_settings": {"model": "rerouted"},
                    },
                    2,
                ),
                record(),
                altered,
            ]
        },
    )
    importer.step()
    model = next(
        x
        for x in codex_journey(s.store, "thread-1")["items"]
        if x["kind"] == "model_call"
    )
    assert model["tokens"] is None and model["status"] == "conflict"
    assert model["model"] == "rerouted"


def test_oversized_record_skip_is_bounded_and_empty_session_completes(
    tmp_path, monkeypatch
):
    import leam_api.usage_sources as source

    monkeypatch.setattr(source, "MAX_CHUNK", 1024)
    huge = native(
        "response_item",
        {"type": "message", "role": "user", "content": "PRIVATE" * 900},
        2,
    )
    s, importer, _ = fixture(
        tmp_path, {"thread-1": [own_meta(), huge, record()], "empty": []}
    )
    for _ in range(10):
        importer.step()
    journey = codex_journey(s.store, "thread-1")
    assert journey["counts"]["model_call"] == 1
    assert journey["coverage"]["oversizedRecords"] == 1
    assert codex_journey(s.store, "empty")["coverage"]["importComplete"]
    assert s.store.get("usage.source")["completedThreads"] == 2
