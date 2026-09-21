"""Shared read-only, sourced context. Never reads event payloads or Codex history."""

import json
import sqlite3
import time
from datetime import datetime
from typing import Literal

from .backlog import STALE_AFTER
from .coding_handoff import snapshot as handoff_snapshot
from .updates import Updates

ContextKind = Literal[
    "coding",
    "all",
    "memory",
    "commitment",
    "capacity",
    "calendar",
    "routine",
    "event_rule",
    "notification",
    "project",
]
KINDS = (
    "coding",
    "memory",
    "commitment",
    "capacity",
    "calendar",
    "routine",
    "event_rule",
    "notification",
    "project",
)
MAX_SCAN = 500


def stamp(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return None


def latest(values):
    return max((v for v in values if v is not None), default=None)


def select(body, fields):
    return {k: body[k] for k in fields if k in body}


class ContextReader:
    def __init__(self, store, calendars, clock=None):
        self.store, self.calendars = store, calendars
        self.clock = clock or time.time

    def record(
        self,
        kind,
        item,
        updated,
        observed,
        source=None,
        availability="available",
        source_type="local_snapshot",
    ):
        return {
            **item,
            "recordType": kind,
            "source": source or f"leam:{kind}/{item['id']}",
            "sourceType": source_type,
            "observedAt": observed,
            "dataAsOf": updated,
            "availability": availability,
        }

    def entity_rows(self, db, kind, bounded=True):
        count = db.execute(
            "SELECT count(*) FROM entities WHERE kind=?", (kind,)
        ).fetchone()[0]
        return (
            db.execute(
                "SELECT * FROM entities WHERE kind=? ORDER BY id"
                + (" LIMIT ?" if bounded else ""),
                (kind, MAX_SCAN) if bounded else (kind,),
            ).fetchall(),
            count,
        )

    def project(self, db, observed):
        updates = db.execute(
            "SELECT * FROM deployment_updates WHERE superseded=0 ORDER BY sequence DESC LIMIT ?",
            (MAX_SCAN,),
        ).fetchall()
        deployed_total = db.execute(
            "SELECT count(*) FROM deployment_updates WHERE superseded=0"
        ).fetchone()[0]
        backlog = db.execute(
            "SELECT * FROM backlog_assessments WHERE feature NOT IN (SELECT feature FROM deployment_updates) ORDER BY assessed DESC,feature LIMIT ?",
            (MAX_SCAN,),
        ).fetchall()
        backlog_total = db.execute(
            "SELECT count(*) FROM backlog_assessments WHERE feature NOT IN (SELECT feature FROM deployment_updates)"
        ).fetchone()[0]
        records = [Updates.item(row) for row in updates]
        work = [
            {
                **json.loads(row["body"]),
                "revision": row["revision"],
                "stale": observed - row["assessed"] > STALE_AFTER,
            }
            for row in backlog
        ]
        if not records and not work:
            return [], 0
        dates = [
            stamp(v)
            for item in records
            for v in (
                item.get("deployedAt"),
                item["qa"].get("updatedAt"),
                item["uat"].get("updatedAt"),
            )
        ] + [row["assessed"] for row in backlog]
        data = {
            "id": "leam-build",
            "name": "Leam",
            "description": "Build and acceptance status recorded in Leam Updates and Backlog.",
            "deployedFeatures": deployed_total,
            "userAcceptedFeatures": sum(r["completed"] for r in records),
            "qaPassedFeatures": sum(r["qa"]["state"] == "passed" for r in records),
            "awaitingUatFeatures": sum(
                r["qa"]["state"] == "passed" and r["uat"]["state"] == "pending"
                for r in records
            ),
            "failedQaFeatures": sum(r["qa"]["state"] == "failed" for r in records),
            "failedUatFeatures": sum(r["uat"]["state"] == "failed" for r in records),
            "unfinishedFeatures": len(work),
            "staleAssessments": sum(w["stale"] for w in work),
            "estimateBasis": "deployment",
            "countsTruncated": deployed_total > MAX_SCAN or backlog_total > MAX_SCAN,
            "backlogTotal": backlog_total,
            "sources": ["leam:updates", "leam:backlog"],
            "recentDeployments": [
                select(
                    r, ("id", "feature", "title", "deploymentId", "deployedAt", "stage")
                )
                for r in records[:5]
            ],
            "workItems": [
                {
                    **select(
                        w,
                        (
                            "feature",
                            "title",
                            "currentStep",
                            "percent",
                            "assessedAt",
                            "stale",
                        ),
                    ),
                    "blockerCount": len(w.get("blockers", [])),
                }
                for w in work[:5]
            ],
            "scope": "Leam build only; not an inventory of unrelated projects.",
        }
        return [
            self.record(
                "project",
                data,
                latest(dates),
                observed,
                "leam:build/updates-backlog",
                (
                    "partial"
                    if data["countsTruncated"]
                    else "stale"
                    if data["staleAssessments"]
                    else "available"
                ),
            )
        ], 1

    def read_kind(self, db, kind, observed):
        if kind == "coding":
            rows = db.execute(
                "SELECT value FROM settings WHERE key GLOB 'coding-handoff:*' ORDER BY rowid DESC LIMIT ?",
                (MAX_SCAN,),
            ).fetchall()
            count = db.execute(
                "SELECT count(*) FROM settings WHERE key GLOB 'coding-handoff:*'"
            ).fetchone()[0]
            items = [handoff_snapshot(db, json.loads(row["value"])) for row in rows]
            return [
                self.record(
                    kind,
                    item,
                    item.get("statusObservedAt"),
                    observed,
                    source=f"leam:coding-handoff/{item['id']}",
                )
                for item in items
            ], count

        if kind in ("memory", "commitment", "capacity"):
            rows, count = self.entity_rows(db, kind, bounded=False)
            return [
                self.record(
                    kind,
                    {**json.loads(r["body"]), "id": r["id"], "revision": r["revision"]},
                    r["updated"],
                    observed,
                    json.loads(r["body"]).get("source"),
                    source_type="user_record",
                )
                for r in rows
            ], count
        if kind == "calendar":
            items = self.calendars.list()["items"]
            return [
                self.record(
                    kind,
                    i,
                    i.get("syncedAt"),
                    observed,
                    availability=(
                        "unavailable"
                        if i.get("syncedAt") is None
                        else "stale"
                        if i.get("error")
                        else "available"
                    ),
                    source_type="provider_snapshot",
                )
                for i in items[:MAX_SCAN]
            ], len(items)
        if kind == "routine":
            rows = db.execute(
                "SELECT * FROM routines ORDER BY id LIMIT ?", (MAX_SCAN,)
            ).fetchall()
            count = db.execute("SELECT count(*) FROM routines").fetchone()[0]
            return [
                self.record(
                    kind,
                    {
                        **select(
                            json.loads(r["body"]),
                            (
                                "title",
                                "message",
                                "enabled",
                                "action",
                                "time",
                                "timezone",
                                "days",
                            ),
                        ),
                        "id": r["id"],
                        "revision": r["revision"],
                        "nextDueAt": r["next_due"],
                        "executionStatus": "not_inspected",
                    },
                    r["updated"],
                    observed,
                )
                for r in rows
            ], count
        if kind == "event_rule":
            rows, count = self.entity_rows(db, "routine_event_rule")
            items = []
            for r in rows:
                body = json.loads(r["body"])
                item = {
                    **select(
                        body,
                        (
                            "title",
                            "message",
                            "enabled",
                            "action",
                            "eventType",
                            "cooldownSeconds",
                            "lastDeliveredAt",
                        ),
                    ),
                    "eventSource": body.get("source"),
                    "hasCondition": body.get("match") is not None,
                    "match": body.get("match"),
                    "id": r["id"],
                    "revision": r["revision"],
                }
                items.append(self.record(kind, item, r["updated"], observed))
            return items, count
        if kind == "notification":
            items = []
            count = 0
            rows = db.execute(
                "SELECT n.*,e.body FROM reminder_jobs n JOIN entities e ON e.id=n.commitment_id WHERE n.state='ready' ORDER BY n.id LIMIT ?",
                (MAX_SCAN,),
            ).fetchall()
            count += db.execute(
                "SELECT count(*) FROM reminder_jobs WHERE state='ready'"
            ).fetchone()[0]
            for r in rows:
                items.append(
                    self.record(
                        kind,
                        {
                            "id": r["id"],
                            "title": json.loads(r["body"])["title"],
                            "notificationKind": "commitment",
                            "commitmentId": r["commitment_id"],
                            "dueAt": r["due"],
                            "state": "ready",
                        },
                        r["updated"],
                        observed,
                        f"leam:notification/commitment/{r['id']}",
                    )
                )
            rows = db.execute(
                "SELECT * FROM routine_runs WHERE state='delivered' AND dismissed IS NULL ORDER BY id LIMIT ?",
                (MAX_SCAN,),
            ).fetchall()
            count += db.execute(
                "SELECT count(*) FROM routine_runs WHERE state='delivered' AND dismissed IS NULL"
            ).fetchone()[0]
            for r in rows:
                items.append(
                    self.record(
                        kind,
                        {
                            "id": r["id"],
                            "title": r["title"],
                            "message": r["message"],
                            "notificationKind": "routine",
                            "routineId": r["routine_id"],
                            "dueAt": r["due"],
                            "state": "ready",
                        },
                        r["created"],
                        observed,
                        f"leam:notification/routine/{r['id']}",
                    )
                )
            rows = db.execute(
                "SELECT * FROM entities WHERE kind='routine_event_notification' AND json_extract(body,'$.dismissedAt') IS NULL ORDER BY id LIMIT ?",
                (MAX_SCAN,),
            ).fetchall()
            count += db.execute(
                "SELECT count(*) FROM entities WHERE kind='routine_event_notification' AND json_extract(body,'$.dismissedAt') IS NULL"
            ).fetchone()[0]
            for r in rows:
                b = json.loads(r["body"])
                items.append(
                    self.record(
                        kind,
                        {
                            **select(b, ("title", "message", "ruleId", "createdAt")),
                            "id": r["id"],
                            "notificationKind": "event_rule",
                            "state": "ready",
                        },
                        r["updated"],
                        observed,
                        f"leam:notification/event/{r['id']}",
                    )
                )
            return items, count
        return self.project(db, observed)

    def query(self, body):
        observed = self.clock()
        rows = []
        modules = []
        project = None
        truncated = False
        with self.store.connect() as db:
            db.execute("BEGIN")
            for kind in KINDS:
                if body.kind not in ("all", kind):
                    continue
                try:
                    items, count = self.read_kind(db, kind, observed)
                    cutoff = count > len(items) or any(
                        i.get("countsTruncated", False) for i in items
                    )
                    availability = (
                        "empty"
                        if count == 0
                        else (
                            "stale"
                            if any(i["availability"] == "stale" for i in items)
                            else "available"
                        )
                    )
                    if items and all(i["availability"] == "unavailable" for i in items):
                        availability = "unavailable"
                    elif cutoff or any(
                        i["availability"] in ("partial", "unavailable") for i in items
                    ):
                        availability = "partial"
                    module = {
                        "kind": kind,
                        "source": f"leam:{kind}",
                        "observedAt": observed,
                        "dataAsOf": latest(i["dataAsOf"] for i in items),
                        "availability": availability,
                        "count": count,
                        "truncated": cutoff,
                    }
                    if kind == "project":
                        project = items[0] if items else None
                    truncated |= cutoff
                    rows.extend(
                        i
                        for i in items
                        if body.query.casefold()
                        in json.dumps(i, ensure_ascii=False).casefold()
                    )
                except (sqlite3.Error, ValueError, TypeError, KeyError):
                    module = {
                        "kind": kind,
                        "source": f"leam:{kind}",
                        "observedAt": observed,
                        "dataAsOf": None,
                        "availability": "unavailable",
                        "count": None,
                        "truncated": False,
                        "detail": "Local source could not be read; no state or freshness is inferred.",
                    }
                modules.append(module)
        rows.sort(key=lambda i: (i["recordType"], i["id"], i["source"]))
        return {
            "items": rows[body.offset : body.offset + body.limit],
            "nextOffset": (
                body.offset + body.limit
                if len(rows) > body.offset + body.limit
                else None
            ),
            "total": len(rows),
            "totalIsExact": not truncated
            and all(m["count"] is not None for m in modules),
            "truncated": truncated,
            "scanLimitPerSource": MAX_SCAN,
            "unboundedSearchKinds": ["memory", "commitment", "capacity"],
            "referenceData": True,
            "observedAt": observed,
            "modules": modules,
            "project": project,
        }
