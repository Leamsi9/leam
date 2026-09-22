"""Metadata-only usage accounting and deterministic analytics; never invokes models."""

import asyncio
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field

from .commitments import Input
from .usage_codex_trace import codex_journey, init_codex_trace
from .usage_exhaustion import ExhaustionEvent, UsageExhaustions


class UsageSettings(Input):
    enabled: bool = True
    dailyWarningTokens: int = Field(default=0, ge=0, le=10**12, strict=True)
    largeCallTokens: int = Field(default=100000, ge=1000, le=10**12, strict=True)


class GoalAssociation(Input):
    name: str = Field(min_length=1, max_length=100)
    threadId: str = Field(min_length=1, max_length=128)
    includeDescendants: bool = False


class UsageService:
    def __init__(self, store, codex_home=None):
        self.store = store
        self.codex_home = Path(codex_home) if codex_home else None
        self.runtime = None
        self.wakeup = asyncio.Event()
        with store.connect() as db:
            db.executescript(
                """
            CREATE TABLE IF NOT EXISTS usage_records (
              id TEXT PRIMARY KEY, source TEXT NOT NULL, thread_id TEXT NOT NULL,
              turn_id TEXT, model TEXT NOT NULL, operation TEXT NOT NULL,
              timestamp REAL NOT NULL, input INTEGER NOT NULL, output INTEGER NOT NULL,
              cached INTEGER, reasoning INTEGER, cache_write INTEGER, digest TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS usage_records_time ON usage_records(timestamp);
            CREATE INDEX IF NOT EXISTS usage_records_thread ON usage_records(thread_id,timestamp);
            CREATE TABLE IF NOT EXISTS usage_conflicts(id TEXT PRIMARY KEY, observed REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_threads(id TEXT PRIMARY KEY, parent TEXT, goal_id TEXT);
            CREATE TABLE IF NOT EXISTS usage_goals(id TEXT PRIMARY KEY, name TEXT NOT NULL, root_thread TEXT NOT NULL, descendants INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_files(thread_id TEXT PRIMARY KEY, file_identity TEXT NOT NULL, offset INTEGER NOT NULL, model TEXT NOT NULL, turn_id TEXT, skipping INTEGER NOT NULL DEFAULT 0, checked REAL NOT NULL DEFAULT 0);
            """
            )

        with store.connect() as db:
            init_codex_trace(db)
            from .usage_runtime import initialize

            initialize(db)
        self.exhaustions = UsageExhaustions(self)

    def settings(self):
        return UsageSettings(**self.store.get("usage.settings", {})).model_dump()

    def ingest(
        self,
        db,
        *,
        response_id,
        thread_id,
        turn_id,
        model,
        timestamp,
        usage,
        source="codex.response",
        operation="coding.turn",
    ):
        """One own-thread response is charged once; conflicting evidence is quarantined."""
        for value in (response_id, thread_id, turn_id or "", model):
            if (
                not isinstance(value, str)
                or len(value) > 256
                or any(ord(c) < 32 for c in value)
            ):
                raise ValueError("Invalid usage identity")
        if (
            not response_id
            or not thread_id
            or not isinstance(timestamp, (int, float))
            or not 0 < timestamp < time.time() + 300
        ):
            raise ValueError("Invalid usage timestamp or identity")

        if not isinstance(usage, dict):
            raise ValueError("Invalid usage object")

        def count(key, required=False):
            value = usage.get(key)
            if value is None and not required:
                return None
            if type(value) is not int or not 0 <= value <= 10**12:
                raise ValueError("Invalid token quantity")
            return value

        i = count("input_tokens", True)
        o = count("output_tokens", True)
        c = count("cached_input_tokens")
        r = count("reasoning_output_tokens")
        w = count("cache_write_input_tokens")
        if (
            (c is not None and c > i)
            or (w is not None and w > i)
            or (r is not None and r > o)
            or (c is not None and w is not None and c + w > i)
        ):
            raise ValueError("Invalid token subsets")
        t = count("total_tokens")
        if t is not None and t != i + o:
            raise ValueError("Inconsistent total")
        if (source, operation) not in {
            ("codex.response", "coding.turn"),
            ("ironclaw.model_attempt", "companion.model_attempt"),
        }:
            raise ValueError("Unsupported usage source")
        key = ("codex:" if source == "codex.response" else source + ":") + response_id
        # Timestamp/model metadata can be filled differently across mirrors; charge identity and quantities cannot.
        digest = hashlib.sha256(
            json.dumps(
                [thread_id, turn_id, i, o, c, r, w], separators=(",", ":")
            ).encode()
        ).hexdigest()
        old = db.execute(
            "SELECT digest FROM usage_records WHERE id=?", (key,)
        ).fetchone()
        if old:
            if old["digest"] != digest:
                db.execute(
                    "INSERT OR IGNORE INTO usage_conflicts VALUES (?,?)",
                    (key, time.time()),
                )
            return False
        db.execute("INSERT OR IGNORE INTO usage_threads(id) VALUES (?)", (thread_id,))
        db.execute(
            "INSERT INTO usage_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                key,
                source,
                thread_id,
                turn_id,
                model or "unknown",
                operation,
                timestamp,
                i,
                o,
                c,
                r,
                w,
                digest,
            ),
        )
        return True

    def associate(self, body):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "SELECT 1 FROM usage_threads WHERE id=?", (body.threadId,)
            ).fetchone():
                raise ValueError("Select a collected thread first")
            if not body.name.strip():
                raise ValueError("Goal name cannot be blank")
            if db.execute("SELECT count(*) FROM usage_goals").fetchone()[0] >= 500:
                raise ValueError("Goal association limit reached")
            key = str(uuid.uuid4())
            db.execute(
                "INSERT INTO usage_goals VALUES (?,?,?,?)",
                (key, body.name.strip(), body.threadId, int(body.includeDescendants)),
            )
            # Recompute inherited descendants while preserving each explicit
            # child-root association, including its own propagation policy.
            db.execute(
                """WITH RECURSIVE descendants(id) AS (
                    SELECT id FROM usage_threads WHERE parent=?
                    UNION SELECT child.id FROM usage_threads child
                    JOIN descendants parent ON child.parent=parent.id
                ) UPDATE usage_threads SET goal_id=NULL
                WHERE id IN (SELECT id FROM descendants) AND NOT EXISTS (
                    SELECT 1 FROM usage_goals g
                    WHERE g.id=usage_threads.goal_id AND g.root_thread=usage_threads.id
                )""",
                (body.threadId,),
            )
            db.execute(
                "UPDATE usage_threads SET goal_id=? WHERE id=?", (key, body.threadId)
            )
            self.link_descendants(db)
        return {"id": key, "name": body.name.strip()}

    @staticmethod
    def link_descendants(db):
        # Bounded propagation and explicit parent edges; no title/time similarity guesses.
        for _ in range(32):
            changed = db.execute(
                """UPDATE usage_threads AS child SET goal_id=(SELECT parent.goal_id FROM usage_threads parent JOIN usage_goals g ON g.id=parent.goal_id WHERE parent.id=child.parent AND g.descendants=1)
            WHERE child.goal_id IS NULL AND EXISTS(SELECT 1 FROM usage_threads parent JOIN usage_goals g ON g.id=parent.goal_id WHERE parent.id=child.parent AND g.descendants=1)"""
            ).rowcount
            if not changed:
                break

    @staticmethod
    def where(days, model="", operation="", goal=""):
        conditions = ["NOT EXISTS (SELECT 1 FROM usage_conflicts c WHERE c.id=r.id)"]
        args = []
        if days:
            conditions.append("r.timestamp>=?")
            args.append(time.time() - days * 86400)
        for column, value in [
            ("r.model", model),
            ("r.operation", operation),
            ("t.goal_id", goal),
        ]:
            if value:
                conditions.append(column + "=?")
                args.append(value)
        return " AND ".join(conditions), args

    @staticmethod
    def totals(row):
        n = row["requests"]
        i = row["input"] or 0
        o = row["output"] or 0
        c = row["cached"]
        known = row["cacheKnownRequests"] or 0
        return {
            "requests": n,
            "input": str(i),
            "output": str(o),
            "total": str(i + o),
            "cached": str(c or 0) if known == n else None,
            "reasoning": (
                str(row["reasoning"] or 0) if row["reasoningKnown"] == n else None
            ),
            "nonCached": str(i + o - (c or 0)) if known == n else None,
            "cacheKnownRequests": known,
        }

    AGG = "COUNT(*) requests,SUM(r.input) input,SUM(r.output) output,SUM(r.cached) cached,SUM(r.reasoning) reasoning,COUNT(r.cached) cacheKnownRequests,COUNT(r.reasoning) reasoningKnown"
    JOIN = " FROM usage_records r LEFT JOIN usage_threads t ON t.id=r.thread_id "

    def overview(self, days=7, model="", operation="", goal=""):
        where, args = self.where(days, model, operation, goal)
        settings = self.settings()
        with self.store.connect() as db:
            totals = self.totals(
                db.execute(
                    "SELECT " + self.AGG + self.JOIN + "WHERE " + where, args
                ).fetchone()
            )
            daily = [
                {"day": r["day"], **self.totals(r)}
                for r in db.execute(
                    "SELECT strftime('%Y-%m-%d',r.timestamp,'unixepoch') day,"
                    + self.AGG
                    + self.JOIN
                    + "WHERE "
                    + where
                    + " GROUP BY day ORDER BY day",
                    args,
                )
            ]
            groups = []
            for kind, col in [
                ("model", "r.model"),
                ("operation", "r.operation"),
                ("goal", "COALESCE(t.goal_id,'unassigned')"),
                ("thread", "r.thread_id"),
            ]:
                groups.extend(
                    {"kind": kind, "key": r["key"], **self.totals(r)}
                    for r in db.execute(
                        "SELECT "
                        + col
                        + " key,"
                        + self.AGG
                        + self.JOIN
                        + "WHERE "
                        + where
                        + " GROUP BY "
                        + col
                        + " ORDER BY SUM(r.input+r.output) DESC LIMIT 100",
                        args,
                    )
                )
            conflicts = db.execute("SELECT count(*) FROM usage_conflicts").fetchone()[0]
            unassigned = db.execute(
                "SELECT count(*)"
                + self.JOIN
                + "WHERE "
                + where
                + " AND t.goal_id IS NULL",
                args,
            ).fetchone()[0]
            models = [
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT model FROM usage_records ORDER BY model LIMIT 500"
                )
            ]
            goals = [
                dict(r)
                for r in db.execute(
                    "SELECT id,name FROM usage_goals ORDER BY name LIMIT 500"
                )
            ]
            large = db.execute(
                "SELECT count(*)"
                + self.JOIN
                + "WHERE "
                + where
                + " AND r.input+r.output>=?",
                args + [settings["largeCallTokens"]],
            ).fetchone()[0]
            midnight = (
                datetime.now(timezone.utc)
                .replace(hour=0, minute=0, second=0, microsecond=0)
                .timestamp()
            )
            today = db.execute(
                "SELECT COALESCE(SUM(input+output),0) FROM usage_records r WHERE timestamp>=? AND NOT EXISTS(SELECT 1 FROM usage_conflicts c WHERE c.id=r.id)",
                (midnight,),
            ).fetchone()[0]
        status = self.store.get("usage.source", {})
        source_status = (
            "paused"
            if not settings["enabled"]
            else status.get(
                "status", "pending" if self.codex_home else "not_configured"
            )
        )
        opportunities = []
        if large:
            opportunities.append(
                {
                    "id": "large-calls",
                    "title": f"{large} large model calls",
                    "details": f"At least {settings['largeCallTokens']:,} tokens each in this view. Inspect context and cache share before reducing useful context.",
                    "severity": "info",
                }
            )
        if unassigned:
            opportunities.append(
                {
                    "id": "attribution",
                    "title": "Associate work with goals",
                    "details": f"{unassigned} measured calls have no explicit goal association.",
                    "severity": "info",
                }
            )
        if conflicts:
            opportunities.append(
                {
                    "id": "conflicts",
                    "title": "Conflicting usage evidence",
                    "details": f"{conflicts} response identities are excluded until reconciled; displayed usage is incomplete.",
                    "severity": "warning",
                }
            )
        return {
            "totals": totals,
            "daily": daily,
            "groups": groups,
            "coverage": {
                "sources": [
                    {
                        "source": "Codex responses",
                        "status": source_status,
                        "lastSuccess": status.get("lastSuccess"),
                        "details": status.get(
                            "details",
                            "Only own-thread per-response records are counted. Cumulative snapshots are not added.",
                        ),
                    },
                    self.runtime_coverage(),
                ],
                "conflicts": conflicts,
                "unassignedRequests": unassigned,
            },
            "opportunities": opportunities,
            "filters": {
                "models": models,
                "operations": ["coding.turn", "companion.model_attempt"],
                "goals": goals,
            },
            "settings": settings,
            "warning": {
                "exceeded": bool(
                    settings["dailyWarningTokens"]
                    and today >= settings["dailyWarningTokens"]
                ),
                "observed": str(today),
                "limit": settings["dailyWarningTokens"],
            },
            "generatedAt": time.time(),
        }

    def records(self, days=7, model="", operation="", goal="", offset=0, limit=30):
        where, args = self.where(days, model, operation, goal)
        with self.store.connect() as db:
            count = db.execute(
                "SELECT count(*)" + self.JOIN + "WHERE " + where, args
            ).fetchone()[0]
            rows = db.execute(
                "SELECT r.*,t.goal_id"
                + self.JOIN
                + "WHERE "
                + where
                + " ORDER BY timestamp DESC,r.id LIMIT ? OFFSET ?",
                args + [limit, offset],
            ).fetchall()
        return {
            "items": [
                {
                    "id": r["id"],
                    "source": r["source"],
                    "threadId": r["thread_id"],
                    "turnId": r["turn_id"],
                    "model": r["model"],
                    "operation": r["operation"],
                    "goalId": r["goal_id"],
                    "timestamp": r["timestamp"],
                    "input": str(r["input"]),
                    "output": str(r["output"]),
                    "total": str(r["input"] + r["output"]),
                    "cached": str(r["cached"]) if r["cached"] is not None else None,
                    "reasoning": (
                        str(r["reasoning"]) if r["reasoning"] is not None else None
                    ),
                }
                for r in rows
            ],
            "total": count,
        }

    def runtime_coverage(self):
        status = self.store.get("usage.runtime", {})
        return {
            "source": "Companion / IronClaw",
            "status": "paused"
            if not self.settings()["enabled"]
            else status.get("status", "pending" if self.runtime else "not_configured"),
            "lastSuccess": status.get("lastSuccess"),
            "details": status.get(
                "details",
                "Only retained run diagnostics can be captured. Unreported counters, provider identity, auxiliary calls and evicted history remain unavailable.",
            ),
        }

    async def run(self):
        from .usage_sources import CodexUsageImporter

        importer = CodexUsageImporter(self)
        from .usage_runtime import RuntimeUsageImporter

        runtime_importer = RuntimeUsageImporter(self)
        while True:
            if self.settings()["enabled"] and self.codex_home:
                try:
                    await asyncio.to_thread(importer.step)
                except Exception:
                    previous = self.store.get("usage.source", {})
                    self.store.set(
                        "usage.source",
                        {
                            **previous,
                            "status": "error",
                            "details": "Local usage import failed; previous records retained. No inference was invoked.",
                        },
                    )
            if self.settings()["enabled"] and self.runtime:
                try:
                    await runtime_importer.step()
                except Exception:
                    previous = self.store.get("usage.runtime", {})
                    self.store.set(
                        "usage.runtime",
                        {
                            **previous,
                            "status": "error",
                            "details": "Runtime usage observation failed; retained measurements are unchanged. No inference was invoked.",
                        },
                    )
            try:
                await asyncio.wait_for(self.wakeup.wait(), timeout=10)
            except TimeoutError:
                pass
            self.wakeup.clear()


def router(service):
    routes = APIRouter(prefix="/api/usage")

    @routes.get("")
    def overview(
        days: int = Query(7, ge=0, le=3650),
        model: str = Query("", max_length=256),
        operation: str = Query("", max_length=128),
        goal: str = Query("", max_length=128),
    ):
        return service.overview(days, model, operation, goal)

    @routes.get("/journey")
    def journey(
        threadId: str = Query(..., min_length=1, max_length=128),
        turnId: str | None = Query(None, max_length=128),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0, le=10000000),
    ):
        return {
            **codex_journey(service.store, threadId, turnId, limit, offset),
            "threadId": threadId,
            "collection": "paused"
            if not service.settings()["enabled"]
            else "enabled"
            if service.codex_home
            else "not_configured",
        }

    @routes.get("/runtime-attempts")
    def runtime_attempts(
        threadId: str = Query("", max_length=128),
        runId: str = Query("", max_length=128),
        limit: int = Query(30, ge=1, le=100),
        offset: int = Query(0, ge=0, le=10000000),
    ):
        from .usage_runtime import attempts

        return attempts(service, threadId, runId, limit, offset)

    @routes.get("/exhaustions")
    def exhaustions(
        provider: Literal["chatgpt"] = "chatgpt",
        offset: int = Query(0, ge=0, le=100000),
        limit: int = Query(50, ge=1, le=100),
    ):
        return service.exhaustions.overview(provider, offset, limit)

    @routes.post("/exhaustions")
    def record_exhaustion(body: ExhaustionEvent):
        return service.exhaustions.record(body)

    @routes.get("/records")
    def records(
        days: int = Query(7, ge=0, le=3650),
        model: str = Query("", max_length=256),
        operation: str = Query("", max_length=128),
        goal: str = Query("", max_length=128),
        offset: int = Query(0, ge=0, le=10000000),
        limit: int = Query(30, ge=1, le=100),
    ):
        return service.records(days, model, operation, goal, offset, limit)

    @routes.get("/export")
    def export(
        days: int = Query(7, ge=0, le=3650),
        model: str = Query("", max_length=256),
        operation: str = Query("", max_length=128),
        goal: str = Query("", max_length=128),
    ):
        rows = service.records(days, model, operation, goal, 0, 10000)
        return {
            "schemaVersion": 1,
            "metadataOnly": True,
            "tokens": "decimal strings; cached input and reasoning are subsets",
            "truncated": rows["total"] > len(rows["items"]),
            **rows,
        }

    @routes.put("/settings")
    async def settings(body: UsageSettings):
        service.store.set("usage.settings", body.model_dump())
        service.wakeup.set()
        return body.model_dump()

    @routes.post("/goals")
    def goals(body: GoalAssociation):
        try:
            return service.associate(body)
        except ValueError as e:
            raise HTTPException(422, str(e))

    @routes.post("/refresh")
    async def refresh():
        service.wakeup.set()
        return {"scheduled": True}

    return routes
