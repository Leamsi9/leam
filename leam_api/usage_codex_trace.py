"""Bounded metadata-only native journey evidence; provider usage stays in its ledger."""

import hashlib
import hmac
import json
import re
from datetime import datetime

SCHEMA_VERSION = 1
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,256}$")


def identifier(value):
    return value if isinstance(value, str) and _IDENTIFIER.fullmatch(value) else None


def stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, AttributeError, TypeError, OverflowError):
        return None


def init_codex_trace(db):
    # No executescript here: callers commit steps and source cursors atomically.
    db.execute(
        "CREATE TABLE IF NOT EXISTS usage_codex_steps (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, turn_id TEXT, timestamp REAL, sequence INTEGER NOT NULL, kind TEXT NOT NULL, evidence TEXT NOT NULL, call_id TEXT, usage_record_id TEXT, metadata TEXT NOT NULL)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS usage_codex_steps_thread ON usage_codex_steps(thread_id,timestamp,sequence)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS usage_codex_steps_turn ON usage_codex_steps(thread_id,turn_id,timestamp)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS usage_codex_scan(thread_id TEXT PRIMARY KEY, state TEXT NOT NULL)"
    )


def fresh_state():
    return {
        "version": SCHEMA_VERSION,
        "ownerSeen": False,
        "created": None,
        "boundary": None,
        "turn": None,
        "excludedInherited": 0,
        "unattributed": 0,
        "oversized": 0,
    }


def _size(value):
    if value is None:
        return None
    raw = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    )
    return len(raw.encode("utf-8"))


def observe(db, thread, record, sequence, state, key):
    """Record observed structure; uncertainty never becomes an exact owned count."""
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return
    kind = record.get("type")
    subtype = payload.get("type")
    at = stamp(record.get("timestamp"))
    explicit = payload.get("thread_id")
    if explicit is not None and explicit != thread:
        state["excludedInherited"] += 1
        return
    if kind == "session_meta":
        if payload.get("id") != thread:
            state["excludedInherited"] += 1
            return
        state["ownerSeen"] = True
        state["created"] = stamp(payload.get("timestamp")) or at
        cutoffs = [
            payload.get("subagent_history_start_ordinal"),
            payload.get("forked_from_ordinal_exclusive"),
        ]
        state["boundary"] = max(
            (n for n in cutoffs if type(n) is int and n >= 0), default=None
        )
        return
    if kind == "token_usage_record":
        if explicit != thread:
            return
        response = identifier(payload.get("response_id"))
        if not response:
            return
        usage_id = "codex:" + response
        if not db.execute(
            "SELECT 1 FROM usage_records WHERE id=? AND thread_id=?", (usage_id, thread)
        ).fetchone():
            return
        step_kind, evidence = "model_call", "exact_response"
        turn = identifier(payload.get("turn_id"))
        metadata = {"responseId": response}
        identity = usage_id
    else:
        ordinal = record.get("ordinal")
        cutoff = state.get("boundary")
        if explicit == thread:
            evidence = "explicit_thread"
        elif state.get("ownerSeen") and type(ordinal) is int and cutoff is not None:
            if ordinal < cutoff:
                state["excludedInherited"] += 1
                return
            evidence = "native_ordinal_boundary"
        elif (
            state.get("ownerSeen")
            and at is not None
            and state.get("created") is not None
        ):
            if at <= state["created"]:
                state["excludedInherited"] += 1
                return
            evidence = "timestamp_boundary_inferred"
        else:
            state["unattributed"] += 1
            return
        usage_id = None
        turn = identifier(payload.get("turn_id")) or state.get("turn")
        metadata = {}
        if kind == "turn_context":
            state["turn"] = identifier(payload.get("turn_id"))
            turn = state["turn"]
            step_kind = "configuration"
            metadata = {
                "model": identifier(payload.get("model")),
                "effort": identifier(payload.get("effort")),
            }
        elif kind == "thread_settings_applied" or (
            kind == "event_msg" and subtype == "thread_settings_applied"
        ):
            step_kind = "configuration"
            settings = payload.get("thread_settings", {})
            if not isinstance(settings, dict):
                return
            metadata = {
                "model": identifier(settings.get("model")),
                "effort": identifier(settings.get("reasoning_effort")),
            }
        elif kind == "response_item":
            if subtype in ("function_call", "custom_tool_call"):
                step_kind = "tool_call"
                metadata = {
                    "toolName": identifier(payload.get("name")),
                    "inputBytes": _size(payload.get("arguments", payload.get("input"))),
                }
            elif subtype in ("function_call_output", "custom_tool_call_output"):
                step_kind = "tool_result"
                metadata = {"outputBytes": _size(payload.get("output"))}
            elif subtype in ("message", "agent_message"):
                step_kind = "message"
                role = payload.get(
                    "role", "assistant" if subtype == "agent_message" else None
                )
                metadata = {
                    "role": role
                    if role in ("user", "assistant", "system", "developer", "tool")
                    else "unknown",
                    "outputBytes": _size(payload.get("content")),
                }
            elif subtype == "reasoning":
                step_kind = "reasoning"
                # Raw or encrypted reasoning is neither copied nor fingerprinted.
                metadata = {}
            elif subtype == "web_search_call":
                step_kind = "tool_call"
                metadata = {"toolName": "web_search"}
            else:
                return
        elif kind == "compacted":
            step_kind = "compaction"
            metadata = {"responseId": identifier(payload.get("compaction_response_id"))}
        elif kind == "event_msg" and subtype in (
            "task_started",
            "task_complete",
            "turn_aborted",
            "sub_agent_activity",
            "stream_error",
            "retry",
        ):
            step_kind = {
                "task_started": "turn_started",
                "task_complete": "turn_completed",
                "turn_aborted": "turn_aborted",
                "sub_agent_activity": "delegation",
                "stream_error": "error",
                "retry": "retry",
            }[subtype]
            if subtype == "task_started":
                state["turn"] = identifier(payload.get("turn_id"))
            if subtype == "sub_agent_activity":
                metadata = {"childThreadId": identifier(payload.get("agent_thread_id"))}
        else:
            return
        # Keyed digests cannot reveal private content by dictionary guessing in exports.
        # Identity uses metadata and native timestamp/ordinal, never private arguments.
        stable_item = identifier(payload.get("id")) or identifier(
            payload.get("call_id")
        )
        identity = json.dumps(
            [
                thread,
                kind,
                subtype,
                at,
                ordinal,
                stable_item,
                sequence if not stable_item else None,
            ],
            separators=(",", ":"),
        )
    metadata["turnAttribution"] = (
        "exact"
        if identifier(payload.get("turn_id"))
        else "inferred"
        if turn
        else "unknown"
    )
    step_id = hmac.new(key, identity.encode(), hashlib.sha256).hexdigest()
    db.execute(
        "INSERT OR IGNORE INTO usage_codex_steps VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            step_id,
            thread,
            turn,
            at,
            sequence,
            step_kind,
            evidence,
            identifier(payload.get("call_id")),
            usage_id,
            json.dumps(metadata, separators=(",", ":")),
        ),
    )


def codex_journey(store, thread_id, turn_id=None, limit=200, offset=0):
    limit = min(max(int(limit), 1), 500)
    offset = max(int(offset), 0)
    with store.connect() as db:
        init_codex_trace(db)
        where, args = "s.thread_id=?", [thread_id]
        if turn_id:
            where += " AND s.turn_id=?"
            args.append(turn_id)
        counts, inferred = {}, {}
        for row in db.execute(
            f"SELECT kind,evidence,count(*) n FROM usage_codex_steps s WHERE {where} GROUP BY kind,evidence",
            args,
        ):
            target = (
                inferred if row["evidence"] == "timestamp_boundary_inferred" else counts
            )
            target[row["kind"]] = target.get(row["kind"], 0) + row["n"]
        rows = db.execute(
            f"SELECT s.*,r.input,r.output,r.cached,r.reasoning,r.model,c.id conflict FROM usage_codex_steps s LEFT JOIN usage_records r ON r.id=s.usage_record_id LEFT JOIN usage_conflicts c ON c.id=r.id WHERE {where} ORDER BY s.timestamp,s.sequence,s.id LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
        scan = db.execute(
            "SELECT state FROM usage_codex_scan WHERE thread_id=?", (thread_id,)
        ).fetchone()
    items = []
    for row in rows:
        meta = json.loads(row["metadata"])
        tokens = None
        if row["input"] is not None and row["conflict"] is None:
            tokens = {
                k: str(row[k]) if row[k] is not None else None
                for k in ("input", "output", "cached", "reasoning")
            }
            tokens["total"] = str(row["input"] + row["output"])
        items.append(
            {
                "id": row["id"],
                "threadId": row["thread_id"],
                "turnId": row["turn_id"],
                "kind": row["kind"],
                "timestamp": row["timestamp"],
                "callId": row["call_id"],
                "usageRecordId": row["usage_record_id"],
                "source": "codex.native",
                "evidence": row["evidence"],
                "inputBytes": None,
                "outputBytes": None,
                "toolName": None,
                "role": None,
                **meta,
                "model": row["model"] or meta.get("model"),
                "status": "conflict" if row["conflict"] else "observed",
                "tokens": tokens,
            }
        )
    state = json.loads(scan["state"]) if scan else {}
    total = sum(counts.values()) + sum(inferred.values())
    return {
        "items": items,
        "counts": counts,
        "inferredCounts": inferred,
        "total": total,
        "hasMore": offset + len(items) < total,
        "nextOffset": offset + len(items),
        "coverage": {
            "ownership": "explicit_usage_and_native_boundary_else_inferred",
            "inferredSteps": sum(inferred.values()),
            "unattributedSteps": state.get("unattributed", 0),
            "excludedInherited": state.get("excludedInherited", 0),
            "oversizedRecords": state.get("oversized", 0),
            "importComplete": bool(state.get("complete")),
            "tokenAllocation": "Provider response totals only; per-tool token contribution unavailable",
            "limitations": "Timestamp-boundary steps are inferred and excluded from exact counts. Native hidden prompt stages, detached rollouts and unreported retries are unavailable.",
        },
    }
