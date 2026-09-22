"""Operator staffing gate. External worker liveness is attested, never inferred."""

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .backlog import FeatureId, OpaqueId
from .commitments import Input

RECEIPT = "backlog:staffing-receipt"
GENERATION = "backlog:staffing-generation"
MAX_AGE = 120
TARGET = 4


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Worker(Input):
    worker: OpaqueId
    agentId: str = Field(min_length=1, max_length=160)
    state: Literal["running", "idle", "completed", "interrupted", "cancelled", "failed"]
    feature: FeatureId | None = None
    phase: Literal["implementation", "qa", "review"] = "implementation"

    @model_validator(mode="after")
    def running_task(self):
        if self.state == "running" and self.feature is None:
            raise ValueError("A running worker needs its exact canonical feature")
        return self


class Coordinator(Input):
    agentId: str = Field(min_length=1, max_length=160)
    state: Literal["running", "idle", "interrupted"]
    activity: str = Field(min_length=1, max_length=300)
    features: list[FeatureId] = Field(default_factory=list, max_length=50)


class Observation(Input):
    requestId: UUID
    snapshotHash: str = Field(pattern=r"^[a-f0-9]{64}$")
    observedAt: datetime
    source: Literal["collaboration.list_agents"]
    coordinator: Coordinator
    workers: list[Worker] = Field(max_length=16)
    capacity: int = Field(default=4, ge=1, le=4, strict=True)
    capacityReason: str | None = Field(default=None, min_length=10, max_length=500)

    @field_validator("observedAt")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Observation time needs an explicit timezone")
        return value

    @model_validator(mode="after")
    def unique_workers(self):
        if self.capacity < TARGET and not self.capacityReason:
            raise ValueError("Reduced available capacity requires an explicit reason")
        if len({item.worker for item in self.workers}) != len(self.workers):
            raise ValueError("Duplicate worker observations")
        agents = [self.coordinator.agentId, *(item.agentId for item in self.workers)]
        if len(set(agents)) != len(agents):
            raise ValueError("One real agent cannot count as multiple workstreams")
        features = [
            *self.coordinator.features,
            *(item.feature for item in self.workers if item.state == "running"),
        ]
        if len(set(features)) != len(features):
            raise ValueError("A feature cannot have multiple observed active workers")
        return self


class Staffing:
    def __init__(self, backlog):
        self.backlog = backlog

    def _snapshot(self, db):
        rows = [
            self.backlog.item(row)
            for row in db.execute("SELECT * FROM backlog_assessments ORDER BY feature")
        ]
        has_updates = db.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='deployment_updates'"
        ).fetchone()
        deployments = {}
        if has_updates:
            for row in db.execute(
                "SELECT feature,id,sequence,body FROM deployment_updates WHERE superseded=0 ORDER BY sequence"
            ):
                data = json.loads(row["body"])
                deployments[row["feature"]] = {
                    "id": row["id"],
                    "sequence": row["sequence"],
                    "qa": data.get("qa", {}).get("state"),
                    "uat": data.get("uat", {}).get("state"),
                }
        visible = [dict(item) for item in rows if item["feature"] not in deployments]
        ordering = self.backlog.ordering(db)
        self.backlog.assign_lanes(visible)
        visible = self.backlog.ranked(visible, ordering)
        eligible, blocked = [], []
        for item in visible:
            if item["deliveryState"] not in {"queued", "ready"}:
                continue
            reasons = []
            if item.get("paused"):
                reasons.append("paused by user")
            if item["blockers"] or any(
                sub["state"] == "blocked" or sub.get("blocker")
                for sub in item["subtasks"]
            ):
                reasons.append("recorded blockers")
            if any(
                deployments.get(dep, {}).get("qa") not in {"pending", "passed"}
                for dep in item["dependencies"]
            ):
                reasons.append("dependency unavailable or failed QA")
            choice = {
                "feature": item["feature"],
                "rank": item["rank"],
                "revision": item["revision"],
                "title": item["title"],
            }
            if reasons:
                blocked.append({**choice, "reasons": reasons})
            else:
                eligible.append(choice)
        cancellations = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT value FROM settings WHERE key LIKE 'backlog:deleted:%' AND json_extract(value,'$.cancellationRequired')=1 AND coalesce(json_extract(value,'$.cancellationAcknowledgedAt'),0)=0 ORDER BY key"
            )
        ]
        generation = db.execute(
            "SELECT value FROM settings WHERE key=?", (GENERATION,)
        ).fetchone()
        generation = json.loads(generation[0]) if generation else {"generation": 0}
        basis = {
            "rows": [
                {
                    key: item[key]
                    for key in (
                        "feature",
                        "revision",
                        "deliveryState",
                        "worker",
                        "owner",
                        "dependencies",
                        "blockers",
                        "subtasks",
                    )
                }
                for item in rows
            ],
            "ordering": ordering,
            "deployments": deployments,
            "cancellations": cancellations,
            "generation": generation,
        }
        return {
            "snapshotHash": digest(basis),
            "checkedAt": self.backlog.clock(),
            "target": TARGET,
            "maxObservationAgeSeconds": MAX_AGE,
            "assignments": [
                {
                    key: item[key]
                    for key in (
                        "feature",
                        "revision",
                        "deliveryState",
                        "worker",
                        "owner",
                    )
                }
                | {"deployed": item["feature"] in deployments}
                for item in rows
                if item["deliveryState"] in {"in_progress", "handover", "blocked"}
            ],
            "eligibleNext": eligible,
            "blockedQueue": blocked,
            "pendingCancellations": [
                {"feature": item["feature"], "revision": item["revision"]}
                for item in cancellations
            ],
            "livenessBasis": "Coordinator must inspect collaboration.list_agents and attest current states; reservations are not liveness.",
        }

    def snapshot(self):
        with self.backlog.store.connect() as db:
            db.execute("BEGIN")
            result = self._snapshot(db)
            return {
                **result,
                "eligibleCount": len(result["eligibleNext"]),
                "eligibleNext": result["eligibleNext"][:TARGET],
                "blockedCount": len(result["blockedQueue"]),
                "blockedQueue": result["blockedQueue"][:20],
            }

    def check(self, body):
        now = self.backlog.clock()
        age = now - body.observedAt.timestamp()
        if not -5 <= age <= MAX_AGE:
            raise ValueError(
                "Worker observations are stale or in the future; inspect live agents again"
            )
        with self.backlog.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            snapshot = self._snapshot(db)
            if snapshot["snapshotHash"] != body.snapshotHash:
                raise ValueError(
                    "Canonical staffing state changed; take a new snapshot and inspect live workers"
                )
            fingerprint = digest(body.model_dump(mode="json"))
            previous = db.execute(
                "SELECT value FROM settings WHERE key=?", (RECEIPT,)
            ).fetchone()
            if previous:
                previous = json.loads(previous[0])
                if (
                    previous["requestId"] == str(body.requestId)
                    and previous["fingerprint"] != fingerprint
                ):
                    raise ValueError(
                        "Staffing request ID reused with different observations"
                    )
            assignments = {item["feature"]: item for item in snapshot["assignments"]}
            matched = set()
            problems = []
            for feature in body.coordinator.features:
                assignment = assignments.get(feature)
                if (
                    body.coordinator.state != "running"
                    or not assignment
                    or assignment["owner"] != "main"
                    or assignment["deliveryState"] not in {"in_progress", "handover"}
                ):
                    problems.append(
                        {
                            "feature": feature,
                            "reason": "coordinator work does not match a current assignment",
                        }
                    )
                else:
                    matched.add(feature)
            counted = int(body.coordinator.state == "running")
            occupied = counted + sum(
                worker.state == "running" for worker in body.workers
            )
            for worker in body.workers:
                if worker.state != "running":
                    continue
                assignment = assignments.get(worker.feature)
                valid = (
                    assignment
                    and assignment["worker"] == worker.worker
                    and (
                        assignment["deliveryState"] == "in_progress"
                        or (
                            assignment["deliveryState"] == "handover"
                            and assignment["deployed"]
                            and worker.phase in {"qa", "review"}
                        )
                    )
                )
                if not valid:
                    problems.append(
                        {
                            "feature": worker.feature,
                            "worker": worker.worker,
                            "reason": "running worker does not match a current assignment or deployed QA handover",
                        }
                    )
                else:
                    matched.add(worker.feature)
                    counted += 1
            for item in snapshot["assignments"]:
                if (
                    item["deliveryState"] == "in_progress"
                    and item["feature"] not in matched
                ):
                    observation = next(
                        (
                            worker
                            for worker in body.workers
                            if worker.worker == item["worker"]
                        ),
                        None,
                    )
                    problems.append(
                        {
                            "feature": item["feature"],
                            "worker": item["worker"],
                            "reason": "active assignment has no running observation",
                            "observedState": observation.state
                            if observation
                            else "missing",
                            "nextAction": "Resume the same interrupted task, or record its handoff/pause/failure before dispatching replacement work.",
                        }
                    )
            if snapshot["pendingCancellations"]:
                problems.append(
                    {
                        "reason": "Deleted worker tasks still require verified cancellation acknowledgement"
                    }
                )
            if occupied > body.capacity:
                problems.append(
                    {
                        "reason": "Observed running agents exceed the attested available capacity"
                    }
                )
            open_slots = max(0, body.capacity - occupied)
            next_work = snapshot["eligibleNext"][:open_slots]
            if next_work:
                problems.append(
                    {
                        "reason": "Available capacity and eligible ranked work remain; dispatch and recheck staffing"
                    }
                )
            receipt = {
                "requestId": str(body.requestId),
                "fingerprint": fingerprint,
                "snapshotHash": snapshot["snapshotHash"],
                "observedAt": body.observedAt.timestamp(),
                "checkedAt": now,
                "passed": not problems,
                "target": TARGET,
                "capacity": body.capacity,
                "capacityReason": body.capacityReason,
                "activeWorkstreams": counted,
                "occupiedSlots": occupied,
                "openSlots": open_slots,
                "eligibleCount": len(snapshot["eligibleNext"]),
                "nextWork": next_work,
                "problems": problems,
                "observations": body.model_dump(
                    mode="json", exclude={"requestId", "snapshotHash"}
                ),
                "externalLivenessProven": False,
                "basis": "Fresh coordinator-attested collaboration observations matched to canonical work; this command does not launch agents.",
                "underCapacityReason": "no eligible queued work"
                if not problems and counted < body.capacity
                else "explicit capacity exception"
                if not problems and body.capacity < TARGET
                else None,
            }
            db.execute(
                "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (RECEIPT, json.dumps(receipt)),
            )
            return receipt

    def status(self):
        with self.backlog.store.connect() as db:
            db.execute("BEGIN")
            snapshot = self._snapshot(db)
            row = db.execute(
                "SELECT value FROM settings WHERE key=?", (RECEIPT,)
            ).fetchone()
            receipt = json.loads(row[0]) if row else None
            current = bool(
                receipt
                and receipt["passed"]
                and receipt["snapshotHash"] == snapshot["snapshotHash"]
                and -5 <= self.backlog.clock() - receipt["observedAt"] <= MAX_AGE
            )
            return {
                "current": current,
                "checkDue": not current,
                "receipt": receipt,
                "snapshotHash": snapshot["snapshotHash"],
                "nextEligible": snapshot["eligibleNext"][:TARGET],
                "externalLivenessProven": False,
            }

    def invalidate(self, reason):
        if not reason.strip() or len(reason) > 500:
            raise ValueError("Give a bounded staffing invalidation reason")
        with self.backlog.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value FROM settings WHERE key=?", (GENERATION,)
            ).fetchone()
            prior = json.loads(row[0])["generation"] if row else 0
            value = {
                "generation": prior + 1,
                "reason": reason,
                "invalidatedAt": self.backlog.clock(),
            }
            db.execute(
                "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (GENERATION, json.dumps(value)),
            )
            return {"checkDue": True, **value}
