"""Read-only, bounded observation of retained IronClaw inspector attempts.

Only allowlisted numeric/identity metadata crosses into the durable usage ledger.
Inspector retention is not a complete provider billing history.
"""

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime
from uuid import UUID

SOURCE = "usage.runtime"
OWNER = "usage.runtimeOwner"
MAX_RUNS = 4
MAX_CALLS = 256


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS usage_runtime_runs (
          thread_id TEXT NOT NULL, run_id TEXT NOT NULL, checked REAL NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'pending', PRIMARY KEY(thread_id,run_id));
        CREATE TABLE IF NOT EXISTS usage_runtime_attempts (
          id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, run_id TEXT NOT NULL,
          call_id TEXT NOT NULL, body TEXT NOT NULL, observed REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS usage_runtime_attempts_run ON usage_runtime_attempts(thread_id,run_id);
    """)


def identity(value):
    return str(UUID(value)) if isinstance(value, str) else None


def quantity(value):
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 10**12:
        raise ValueError("Invalid runtime quantity")
    return value


def stamp(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Invalid runtime timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Missing timestamp timezone")
    result = parsed.timestamp()
    if not 0 < result < time.time() + 300:
        raise ValueError("Invalid runtime timestamp")
    return result


def model_label(value):
    text = value.get("content") if isinstance(value, dict) else None
    return (
        text
        if isinstance(text, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", text)
        else "unknown"
    )


def attempt(call, thread, run, owner):
    call_id = identity(call.get("call_id"))
    if not call_id or call.get("status") not in {"started", "succeeded", "failed"}:
        raise ValueError("Invalid runtime attempt")
    usage = call.get("usage") or {}
    if not isinstance(usage, dict):
        raise ValueError("Invalid runtime usage")
    counts = {
        key: quantity(usage.get(key))
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
    }
    i, c, w = (
        counts[key]
        for key in (
            "input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
    )
    if i is not None and ((c or 0) > i or (w or 0) > i or (c or 0) + (w or 0) > i):
        raise ValueError("Invalid runtime token subsets")
    key = hashlib.sha256(
        json.dumps([*owner, thread, run, call_id]).encode()
    ).hexdigest()
    return {
        "id": key,
        "source": "ironclaw.model_attempt",
        "provider": None,
        "threadId": thread,
        "runId": run,
        "callId": call_id,
        "iteration": quantity(call.get("iteration")),
        "status": call["status"],
        "model": model_label(
            call.get("effective_model") or call.get("requested_model")
        ),
        "startedAt": stamp(call.get("started_at")),
        "completedAt": stamp(call["completed_at"])
        if call.get("completed_at")
        else None,
        "durationMs": quantity(call.get("duration_ms")),
        "input": counts["input_tokens"],
        "output": counts["output_tokens"],
        "cached": c,
        "cacheWrite": w,
        "reasoning": None,
    }


class RuntimeUsageImporter:
    def __init__(self, service):
        self.service = service
        self.store = service.store

    async def step(self):
        runtime = self.service.runtime
        if runtime is None:
            return
        # Project IDs in SQL: never load dispatch content or model context.
        with self.store.connect() as db:
            receipts = db.execute("""SELECT json_extract(body,'$.thread_id') thread_id,
                json_extract(result,'$.run_id') run_id FROM runtime_actions
                WHERE path GLOB '/channels/*/messages' AND result IS NOT NULL
                ORDER BY rowid DESC LIMIT 1000""").fetchall()
            for row in receipts:
                try:
                    thread, run = identity(row[0]), identity(row[1])
                except ValueError:
                    continue
                if thread and run:
                    db.execute(
                        "INSERT OR IGNORE INTO usage_runtime_runs(thread_id,run_id) VALUES (?,?)",
                        (thread, run),
                    )
            # Two most recent accepted runs stay hot; remaining quota walks history.
            hot = []
            for receipt in receipts:
                pair = (receipt["thread_id"], receipt["run_id"])
                if pair not in hot and all(pair):
                    hot.append(pair)
                if len(hot) == 2:
                    break
            historical = db.execute(
                "SELECT * FROM usage_runtime_runs ORDER BY checked,run_id LIMIT ?",
                (MAX_RUNS + 2,),
            ).fetchall()
            hot_rows = [
                db.execute(
                    "SELECT * FROM usage_runtime_runs WHERE thread_id=? AND run_id=?",
                    pair,
                ).fetchone()
                for pair in hot
            ]
            rows = [row for row in hot_rows if row is not None]
            rows += [
                row
                for row in historical
                if (row["thread_id"], row["run_id"]) not in hot
            ][: MAX_RUNS - len(rows)]
        if not rows:
            self.store.set(
                SOURCE,
                {
                    "status": "partial",
                    "details": "No captured run receipts yet. Auxiliary completions and evicted diagnostics are not covered.",
                },
            )
            return
        observed = False
        async with asyncio.timeout(8):
            session = await runtime.request("GET", "/session")
            owner = [session.get("tenant_id"), session.get("user_id")]
            if any(not isinstance(v, str) or not v or len(v) > 256 for v in owner):
                raise ValueError("Runtime owner unavailable")
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                pinned = db.execute(
                    "SELECT value FROM settings WHERE key=?", (OWNER,)
                ).fetchone()
                if pinned and json.loads(pinned[0]) != owner:
                    raise ValueError("Runtime owner changed")
                if not pinned:
                    db.execute(
                        "INSERT INTO settings VALUES (?,?)", (OWNER, json.dumps(owner))
                    )
            for row in rows:
                thread, run = row["thread_id"], row["run_id"]
                try:
                    response = await runtime.request(
                        "GET", f"/operator/inspector/threads/{thread}/runs/{run}"
                    )
                    snapshot = response.get("snapshot")
                    if snapshot is None:
                        state = "unavailable"
                    else:
                        scope = snapshot.get("scope") or {}
                        if (
                            [scope.get("tenant_id"), scope.get("user_id")] != owner
                            or scope.get("thread_id") != thread
                            or scope.get("run_id") != run
                        ):
                            raise ValueError("Runtime snapshot owner mismatch")
                        calls = snapshot.get("model_calls")
                        if not isinstance(calls, list) or len(calls) > MAX_CALLS:
                            raise ValueError("Unsupported runtime snapshot")
                        clean = [attempt(call, thread, run, owner) for call in calls]
                        self.persist(clean)
                        state = "observed"
                        observed = True
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Neither exception text nor source payload enters settings/logs.
                    state = "error"
                with self.store.connect() as db:
                    db.execute(
                        "UPDATE usage_runtime_runs SET checked=?,status=? WHERE thread_id=? AND run_id=?",
                        (time.time(), state, thread, run),
                    )
        with self.store.connect() as db:
            counts = dict(
                db.execute(
                    "SELECT status,count(*) FROM usage_runtime_runs GROUP BY status"
                ).fetchall()
            )
        self.store.set(
            SOURCE,
            {
                "status": "error" if counts.get("error") else "partial",
                "lastSuccess": time.time()
                if observed
                else self.store.get(SOURCE, {}).get("lastSuccess"),
                "details": f"Observed retained run diagnostics; {counts.get('observed', 0)} runs observed, {counts.get('unavailable', 0)} unavailable, {counts.get('error', 0)} checks failed, {counts.get('pending', 0)} pending. Auxiliary completions and evicted diagnostics are not covered.",
            },
        )

    def persist(self, items):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in items:
                old = db.execute(
                    "SELECT body FROM usage_runtime_attempts WHERE id=?", (row["id"],)
                ).fetchone()
                previous = json.loads(old[0]) if old else None
                # Late started snapshots cannot erase finalized observations.
                if (
                    previous
                    and previous["status"] != "started"
                    and row["status"] == "started"
                ):
                    continue
                if (
                    row["status"] != "started"
                    and row["input"] is not None
                    and row["output"] is not None
                ):
                    self.service.ingest(
                        db,
                        response_id=row["id"],
                        thread_id="ironclaw:" + row["threadId"],
                        turn_id=row["runId"],
                        model=row["model"],
                        timestamp=row["startedAt"],
                        usage={
                            "input_tokens": row["input"],
                            "output_tokens": row["output"],
                            "cached_input_tokens": row["cached"],
                            "cache_write_input_tokens": row["cacheWrite"],
                        },
                        source="ironclaw.model_attempt",
                        operation="companion.model_attempt",
                    )
                # Keep first complete final evidence; ledger quarantines quantity conflicts.
                if (
                    previous
                    and previous["status"] != "started"
                    and previous["input"] is not None
                    and previous["output"] is not None
                ):
                    continue
                db.execute(
                    "INSERT INTO usage_runtime_attempts VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,observed=excluded.observed",
                    (
                        row["id"],
                        row["threadId"],
                        row["runId"],
                        row["callId"],
                        json.dumps(row),
                        time.time(),
                    ),
                )


def attempts(service, thread="", run="", limit=30, offset=0):
    clauses, args = [], []
    for column, value in (("thread_id", thread), ("run_id", run)):
        if value:
            clauses.append(column + "=?")
            args.append(value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with service.store.connect() as db:
        total = db.execute(
            "SELECT count(*) FROM usage_runtime_attempts" + where, args
        ).fetchone()[0]
        rows = db.execute(
            "SELECT body FROM usage_runtime_attempts"
            + where
            + " ORDER BY observed DESC,id LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
        conflicts = {
            r[0]
            for r in db.execute(
                "SELECT id FROM usage_conflicts WHERE id LIKE 'ironclaw.model_attempt:%'"
            )
        }
    items = []
    for record in rows:
        row = json.loads(record[0])
        row["conflict"] = "ironclaw.model_attempt:" + row["id"] in conflicts
        for key in ("input", "output", "cached", "cacheWrite", "reasoning"):
            if row[key] is not None:
                row[key] = str(row[key])
        items.append(row)
    return {"items": items, "total": total, "coverage": service.runtime_coverage()}
