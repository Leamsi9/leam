"""Explicit, local priority checks: canonical reads, durable receipts, no model calls."""

import json
import sqlite3
import time
import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import Field

from .agenda import Selection, source_key
from .commitments import entity, initial_log

PREFIX = "agenda-priority-check:"
EXCLUSIONS = "agenda-priority-exclusions:"


class Check(Selection):
    requestId: uuid.UUID
    order: Literal["urgent", "important", "short"] = "urgent"
    owner: Literal["all", "user", "leam"] = "all"
    capacityId: uuid.UUID | None = None


class Exclusion(Selection):
    key: str = Field(min_length=1, max_length=100)
    excluded: bool = True


def rank(snapshot, body, now):
    selected = str(body.date)
    candidates = []
    for item in snapshot.get("commitments", []):
        if (
            item.get("kind") != "task"
            or item.get("status") != "active"
            or item.get("stage") == "blocked"
            or item.get("hidden")
            or item.get("priorityExcluded")
            or item.get("log", {}).get("done")
            or item.get("triage", {}).get("disposition", "none") != "none"
            or (body.owner != "all" and item.get("owner") != body.owner)
            or (body.capacityId and item.get("capacityId") != str(body.capacityId))
        ):
            continue
        urgency, importance, duration = 4, 1, None
        reasons = []
        due = item.get("dueDate")
        if due and due <= selected:
            urgency = 0 if due < selected else 1
            reasons.append("Overdue" if urgency == 0 else "Due on this day")
        importance = (
            0
            if item.get("priority") in {"high", "urgent"}
            else 2
            if item.get("priority") == "low"
            else 1
        )
        if importance == 0:
            reasons.append("Marked important")
        if (
            item.get("measure") == "minutes"
            and isinstance(item.get("target"), (float, int))
            and item["target"] > 0
        ):
            duration = item["target"]
            reasons.append(f"Recorded target: {duration:g} min")
        if item.get("stage") == "in_progress":
            reasons.append("In progress")
        if item.get("startDate") and item["startDate"] > selected:
            reasons.append(f"Scheduled to start {item['startDate']}")
        if not reasons:
            reasons.append("Pending task · no deadline recorded")
        axes = {
            "urgent": (urgency, importance, duration is None, duration or 0),
            "important": (importance, urgency, duration is None, duration or 0),
            "short": (duration is None, duration or 0, urgency, importance),
        }
        candidates.append(
            (
                axes[body.order],
                str(item["key"]),
                {
                    "key": item["key"],
                    "entityId": item.get("id"),
                    "kind": "task",
                    "reason": " · ".join(reasons),
                    "revision": item.get("revision"),
                    "durationMinutes": duration,
                },
            )
        )
    candidates.sort(key=lambda entry: (entry[0], entry[1]))
    return [row[2] for row in candidates[:5]]


class Priorities:
    def __init__(self, agenda):
        self.agenda, self.store = agenda, agenda.store

    def canonical_tasks(self, selection, db):
        triage_row = db.execute(
            "SELECT value FROM settings WHERE key=?",
            ("agenda-triage:" + selection.scope_key(),),
        ).fetchone()
        triage = json.loads(triage_row[0]) if triage_row else {}
        excluded_row = db.execute(
            "SELECT value FROM settings WHERE key=?",
            (EXCLUSIONS + selection.scope_key(),),
        ).fetchone()
        exclusions = json.loads(excluded_row[0]) if excluded_row else {}
        day = str(selection.date)
        logs = {
            row["commitment_id"]: {
                **json.loads(row["body"]),
                "revision": row["revision"],
            }
            for row in db.execute("SELECT * FROM daily_logs WHERE day=?", (day,))
        }
        result = []
        for row in db.execute(
            "SELECT * FROM entities WHERE kind='commitment' ORDER BY id"
        ):
            item = entity(row)
            if item.get("kind") != "task":
                continue
            key = source_key("commitment", item["id"], day)
            result.append(
                {
                    **item,
                    "key": key,
                    "date": day,
                    "log": logs.get(item["id"], initial_log(day)),
                    "triage": triage.get(key, {"revision": 0, "disposition": "none"}),
                    "priorityExcluded": key in exclusions,
                    "focusEligible": not (
                        (item.get("startDate") and item["startDate"] > day)
                        or (item.get("endDate") and item["endDate"] < day)
                    ),
                }
            )
        return result

    def view(self, record, selection):
        if not record:
            return None
        if record.get("state") != "completed":
            return record
        with self.store.connect() as db:
            current = {
                item["key"]: item for item in self.canonical_tasks(selection, db)
            }
            exclusions = self.excluded(selection, db)
        return {
            **record,
            "excludedKeys": exclusions,
            "suggestions": [
                {**choice, "currentItem": current.get(choice["key"])}
                for choice in record.get("suggestions", [])
                if choice["key"] not in exclusions
                and choice.get("kind") == "task"
                and current.get(choice["key"], {}).get("kind") == "task"
            ],
        }

    def excluded(self, selection, db):
        row = db.execute(
            "SELECT value FROM settings WHERE key=?",
            (EXCLUSIONS + selection.scope_key(),),
        ).fetchone()
        return list(json.loads(row[0])) if row else []

    def latest(self, selection):
        return self.view(self.store.get(PREFIX + selection.scope_key()), selection)

    def exclude(self, body):
        selection = Selection(date=body.date, timezone=body.timezone)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            keys = {item["key"] for item in self.canonical_tasks(selection, db)}
            latest = self.store.get(PREFIX + selection.scope_key())
            keys.update(
                item["key"]
                for item in (latest or {}).get("suggestions", [])
                if item.get("kind") == "task"
            )
            excluded = set(self.excluded(selection, db))
            if body.key not in keys and body.key not in excluded:
                raise HTTPException(
                    404, "This suggestion is no longer available; run triage again"
                )
            if body.excluded:
                excluded.add(body.key)
            else:
                excluded.discard(body.key)
            db.execute(
                "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (EXCLUSIONS + selection.scope_key(), json.dumps(sorted(excluded))),
            )
        return {"check": self.latest(selection), "excludedKeys": sorted(excluded)}

    def check(self, body):
        selection = Selection(date=body.date, timezone=body.timezone)
        key = PREFIX + selection.scope_key()
        receipt_key = PREFIX + "request:" + str(body.requestId)
        request = body.model_dump(mode="json")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value FROM settings WHERE key=?", (receipt_key,)
            ).fetchone()
            previous = json.loads(row[0]) if row else None
            if previous and previous["requestId"] == str(body.requestId):
                if previous["request"] != request:
                    raise HTTPException(
                        409, "This priority check ID belongs to different criteria"
                    )
                if previous["state"] == "completed":
                    return previous
                latest = db.execute(
                    "SELECT value FROM settings WHERE key=?", (key,)
                ).fetchone()
                if latest and json.loads(latest[0])["requestId"] != str(body.requestId):
                    raise HTTPException(
                        409,
                        "A newer priority check replaced this interrupted request; refresh its status",
                    )
            record = {
                "requestId": str(body.requestId),
                "request": request,
                "state": "pending",
                "startedAt": time.time(),
                "suggestions": [],
            }
            db.execute(
                "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(record)),
            )
            db.execute(
                "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (receipt_key, json.dumps(record)),
            )
        try:
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = json.loads(
                    db.execute(
                        "SELECT value FROM settings WHERE key=?", (key,)
                    ).fetchone()[0]
                )
                if current["requestId"] != record["requestId"]:
                    raise HTTPException(
                        409,
                        "A newer priority check replaced this request; refresh its status",
                    )
                if current["state"] == "completed":
                    return current
                snapshot = {
                    "commitments": self.canonical_tasks(selection, db),
                    "observedAt": time.time(),
                }
                excluded = set(self.excluded(selection, db))
                active_tasks = [
                    item
                    for item in snapshot["commitments"]
                    if item.get("kind") == "task" and item.get("status") == "active"
                ]
                record.update(
                    state="completed",
                    coverage={
                        "canonicalTasks": len(active_tasks),
                        "alreadyFocused": sum(
                            item.get("triage", {}).get("disposition") == "focus"
                            for item in active_tasks
                        ),
                        "excludedForDay": len(excluded),
                    },
                    completedAt=time.time(),
                    observedAt=snapshot["observedAt"],
                    suggestions=rank(snapshot, body, time.time()),
                    partial=False,
                    sources={"tasks": "local"},
                )
                db.execute(
                    "UPDATE settings SET value=? WHERE key=?", (json.dumps(record), key)
                )
                db.execute(
                    "UPDATE settings SET value=? WHERE key=?",
                    (json.dumps(record), receipt_key),
                )
                return record
        except (
            HTTPException,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            sqlite3.Error,
        ) as exc:
            if isinstance(exc, HTTPException) and exc.status_code == 409:
                raise
            # Fixed public label; don't expose snapshot content or provider/config errors.
            record.update(
                state="failed",
                failedAt=time.time(),
                error="Could not check saved priorities. Retry this check.",
            )
            with self.store.connect() as db:
                db.execute(
                    "UPDATE settings SET value=? WHERE key=? AND json_extract(value,'$.requestId')=?",
                    (json.dumps(record), key, record["requestId"]),
                )
                db.execute(
                    "UPDATE settings SET value=? WHERE key=?",
                    (json.dumps(record), receipt_key),
                )
            return record


def router(agenda):
    routes = APIRouter(prefix="/api/agenda/priorities")
    domain = Priorities(agenda)

    @routes.get("")
    def latest(date: date, timezone: str):
        try:
            selection = Selection(date=date, timezone=timezone)
        except ValueError:
            raise HTTPException(422, "Choose a valid date and timezone") from None
        with domain.store.connect() as db:
            excluded = domain.excluded(selection, db)
        return {"check": domain.latest(selection), "excludedKeys": excluded}

    @routes.post("")
    def check(body: Check):
        return domain.view(
            domain.check(body), Selection(date=body.date, timezone=body.timezone)
        )

    @routes.put("/exclusions")
    def exclude(body: Exclusion):
        return domain.exclude(body)

    return routes
