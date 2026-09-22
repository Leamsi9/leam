"""Operator work reservations before worker dispatch; no worker-liveness inference."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from .backlog import Assessment, FeatureId, OpaqueId
from .commitments import Input


class Assignment(Input):
    requestId: UUID
    feature: FeatureId
    worker: OpaqueId
    owner: OpaqueId = "main"
    expectedRevision: int = Field(ge=1, strict=True)
    operation: Literal["start", "handoff", "blocked", "failed", "pause"]
    note: str = Field(min_length=1, max_length=1000)


def transition(backlog, body):
    payload = body.model_dump(mode="json")
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()
    key = "backlog:assignment-receipt:" + str(body.requestId)
    with backlog.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        saved = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if saved:
            receipt = json.loads(saved[0])
            if receipt["fingerprint"] != fingerprint:
                raise ValueError(
                    "Assignment request ID was reused with different inputs"
                )
            return receipt
        row = db.execute(
            "SELECT * FROM backlog_assessments WHERE feature=?", (body.feature,)
        ).fetchone()
        if row is None:
            raise ValueError("Register this task in the backlog before assigning work")
        if body.operation == "start" and body.feature in backlog.deployed(db):
            raise ValueError(
                "Deployed features belong in Updates; register remaining work first"
            )
        if row["revision"] != body.expectedRevision:
            raise ValueError("Backlog revision changed; reload before assigning work")
        facts = backlog.item(row)
        if body.operation == "start":
            if facts.get("paused"):
                raise ValueError("Ticket is paused by the user; unpause it before assigning work")
            if facts["deliveryState"] not in {"queued", "ready"}:
                raise ValueError(
                    "Task must be queued or ready before assigning a worker"
                )
            if facts["blockers"] or any(
                task["state"] == "blocked" or task.get("blocker")
                for task in facts["subtasks"]
            ):
                raise ValueError("Task has unresolved blockers")
            for dependency in facts["dependencies"]:
                if not db.execute(
                    "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='deployment_updates'"
                ).fetchone():
                    raise ValueError(
                        "Dependency is not available as deployed work: " + dependency
                    )
                deployed = db.execute(
                    "SELECT body FROM deployment_updates WHERE feature=? AND superseded=0 ORDER BY sequence DESC LIMIT 1",
                    (dependency,),
                ).fetchone()
                if not deployed or json.loads(deployed[0]).get("qa", {}).get(
                    "state"
                ) not in {"pending", "passed"}:
                    raise ValueError(
                        "Dependency is unavailable or failed QA: " + dependency
                    )
            state = "in_progress"
        else:
            if facts["deliveryState"] not in {"in_progress", "handover"}:
                raise ValueError("Only active or handed-over work can transition")
            if facts["worker"] != body.worker or facts["owner"] != body.owner:
                raise ValueError("Worker/owner does not match the active assignment")
            state = {"handoff": "handover", "pause": "queued"}.get(
                body.operation, "blocked"
            )
        updated = json.loads(row["body"])
        now = backlog.clock()
        updated.update(
            deliveryState=state,
            worker=body.worker,
            owner=body.owner,
            currentStep=body.note,
            nextAction=body.note,
            assessedAt=datetime.fromtimestamp(
                max(now, row["assessed"]), UTC
            ).isoformat(),
        )
        if body.operation == "pause":
            updated.update(owner=None, worker=None)
        if body.operation in {"blocked", "failed"}:
            blocker = (
                "Worker failed: " if body.operation == "failed" else ""
            ) + body.note
            updated["blockers"] = [*facts["blockers"][:19], blocker[:1000]]
        assessment = Assessment.model_validate(updated)
        revision = backlog.next_revision(db, body.feature, row)
        db.execute(
            "UPDATE backlog_assessments SET revision=?,assessed=?,body=? WHERE feature=?",
            (
                revision,
                assessment.assessedAt.timestamp(),
                assessment.model_dump_json(),
                body.feature,
            ),
        )
        # Canonical priority is intentionally untouched by assignment transitions.
        receipt = {
            "requestId": str(body.requestId),
            "fingerprint": fingerprint,
            "feature": body.feature,
            "operation": body.operation,
            "state": state,
            "owner": body.owner,
            "worker": body.worker,
            "previousRevision": body.expectedRevision,
            "revision": revision,
            "transitionedAt": now,
            "note": body.note,
            "meaning": "assignment recorded; worker liveness, deployment and completion are not implied",
        }
        db.execute("INSERT INTO settings VALUES (?,?)", (key, json.dumps(receipt)))
        db.execute(
            "INSERT INTO events(topic,payload,created) VALUES (?,?,?)",
            ("backlog.assignment", json.dumps(receipt), now),
        )
        return receipt
