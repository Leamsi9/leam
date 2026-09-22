"""User-owned backlog descriptions, durable deletion intent and exact retry receipts."""

import hashlib
import json
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, model_validator

from .backlog import Assessment, FeatureId
from .commitments import Input


def saved(db, key):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def put(db, key, value):
    db.execute(
        "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )


def guard_deleted(db, feature):
    tombstone = saved(db, "backlog:deleted:" + feature)
    if tombstone and not tombstone.get("restoredAt"):
        raise ValueError(
            "Ticket was deleted by the user; explicit restore is required: " + feature
        )


def preserve_user_fields(db, feature, facts):
    override = saved(db, "backlog:user-fields:" + feature) or {}
    facts.update(override.get("fields", {}))
    if "subtasks" in override:
        incoming = {task["id"]: task for task in facts["subtasks"]}
        manual = override["subtasks"]
        ordered = []
        for task in manual["items"]:
            current = incoming.pop(task["id"], task)
            ordered.append({**current, "title": task["title"]})
        ordered.extend(
            task for id, task in incoming.items() if id not in manual["removed"]
        )
        if len(ordered) > 40:
            raise ValueError(
                "User-preserved and new subtasks exceed 40; review scope explicitly"
            )
        facts["subtasks"] = ordered
    return Assessment.model_validate(facts).model_dump(mode="json")


class EditableSubtask(Input):
    id: FeatureId
    title: str = Field(min_length=1, max_length=200)


class Changes(Input):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    rationale: str | None = Field(default=None, max_length=2000)
    scope: str | None = Field(default=None, max_length=4000)
    subtasks: list[EditableSubtask] | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def meaningful(self):
        if not self.model_fields_set or any(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("Provide at least one non-null editable field")
        if self.title is not None and not self.title.strip():
            raise ValueError("Title must not be blank")
        if self.subtasks is not None and (
            len({task.id for task in self.subtasks}) != len(self.subtasks)
            or any(not task.title.strip() for task in self.subtasks)
        ):
            raise ValueError("Subtasks require unique IDs and nonblank titles")
        return self


class Edit(Input):
    requestId: UUID
    revision: int = Field(ge=1, strict=True)
    changes: Changes


class Delete(Input):
    requestId: UUID
    revision: int = Field(ge=1, strict=True)
    confirmActive: bool = False


class Restore(Input):
    requestId: UUID
    revision: int = Field(ge=1, strict=True)


def mutate(backlog, feature, operation, request):
    payload = {
        "feature": feature,
        "operation": operation,
        **request.model_dump(mode="json", exclude_unset=True),
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()
    key = "backlog:user-receipt:" + str(request.requestId)
    with backlog.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        old_receipt = saved(db, key)
        if old_receipt:
            if old_receipt["fingerprint"] != fingerprint:
                raise HTTPException(409, "This request ID has different contents")
            return old_receipt
        if feature in backlog.deployed(db):
            raise HTTPException(
                409, "This feature is deployed; its history belongs in Updates"
            )
        old = db.execute(
            "SELECT * FROM backlog_assessments WHERE feature=?", (feature,)
        ).fetchone()
        tombstone = saved(db, "backlog:deleted:" + feature)
        now = backlog.clock()
        if operation == "restore":
            if old or not tombstone or tombstone.get("restoredAt"):
                raise HTTPException(409, "This ticket is not deleted")
            if (
                tombstone["cancellationRequired"]
                and not tombstone["cancellationAcknowledgedAt"]
            ):
                raise HTTPException(
                    409,
                    "Coordinator must acknowledge cancellation before this ticket can be restored",
                )
            if tombstone["revision"] != request.revision:
                raise HTTPException(
                    409, "Deleted ticket changed; reload before restoring"
                )
            if (
                db.execute("SELECT count(*) FROM backlog_assessments").fetchone()[0]
                >= 500
            ):
                raise HTTPException(409, "Backlog has reached the 500-item limit")
            facts = tombstone["body"]
            # A restored tracking record never restarts an external worker.
            facts.update(deliveryState="queued", owner=None, worker=None)
            revision = backlog.next_revision(db, feature, None)
            db.execute(
                "INSERT INTO backlog_assessments VALUES (?,?,?,?)",
                (feature, revision, tombstone["assessed"], json.dumps(facts)),
            )
            tombstone["restoredAt"] = now
            put(db, "backlog:deleted:" + feature, tombstone)
        else:
            if not old:
                raise HTTPException(404, "Backlog ticket not found")
            if old["revision"] != request.revision:
                raise HTTPException(
                    409, "Backlog ticket changed; reload and review your draft"
                )
            facts = json.loads(old["body"])
            revision = backlog.next_revision(db, feature, old)
            if operation == "edit":
                override = saved(db, "backlog:user-fields:" + feature) or {"fields": {}}
                changes = request.changes.model_dump(mode="json", exclude_unset=True)
                tasks = changes.pop("subtasks", None)
                override["fields"].update(changes)
                if tasks is not None:
                    current = {task["id"]: task for task in facts.get("subtasks", [])}
                    previous = override.get("subtasks", {"removed": []})
                    removed = set(previous["removed"]) | (
                        set(current) - {task["id"] for task in tasks}
                    )
                    removed -= {task["id"] for task in tasks}
                    override["subtasks"] = {
                        "items": [
                            {
                                **current.get(
                                    task["id"], {"state": "todo", "blocker": None}
                                ),
                                **task,
                            }
                            for task in tasks
                        ],
                        "removed": sorted(removed),
                    }
                put(db, "backlog:user-fields:" + feature, override)
                facts = preserve_user_fields(db, feature, facts)
                db.execute(
                    "UPDATE backlog_assessments SET revision=?,body=? WHERE feature=?",
                    (revision, json.dumps(facts), feature),
                )
            elif operation == "delete":
                active = facts.get("deliveryState") in {
                    "in_progress",
                    "handover",
                } and bool(facts.get("worker"))
                if active and not request.confirmActive:
                    raise HTTPException(
                        409, "Assigned work requires explicit deletion acknowledgement"
                    )
                tombstone = {
                    "feature": feature,
                    "revision": revision,
                    "deletedAt": now,
                    "restoredAt": None,
                    "body": facts,
                    "assessed": old["assessed"],
                    "cancellationRequired": active,
                    "cancellationAcknowledgedAt": None,
                }
                put(db, "backlog:deleted:" + feature, tombstone)
                db.execute(
                    "DELETE FROM backlog_assessments WHERE feature=?", (feature,)
                )
            else:
                raise HTTPException(422, "Unsupported backlog operation")
        backlog.stabilize_priority(db)
        receipt = {
            "requestId": str(request.requestId),
            "fingerprint": fingerprint,
            "feature": feature,
            "operation": operation,
            "previousRevision": request.revision,
            "revision": revision,
            "changedAt": now,
            "actor": "user",
            "cancellationRequired": bool(
                operation == "delete" and tombstone["cancellationRequired"]
            ),
        }
        put(db, key, receipt)
        db.execute(
            "INSERT INTO events(topic,payload,created) VALUES (?,?,?)",
            (
                "backlog.user_change",
                json.dumps(
                    {
                        **receipt,
                        "owner": facts.get("owner"),
                        "worker": facts.get("worker"),
                    }
                ),
                now,
            ),
        )
        return receipt


def deleted(backlog):
    with backlog.store.connect() as db:
        items = []
        for row in db.execute(
            "SELECT value FROM settings WHERE key LIKE 'backlog:deleted:%'"
        ):
            item = json.loads(row[0])
            if not item.get("restoredAt"):
                items.append(
                    {
                        key: item[key]
                        for key in (
                            "feature",
                            "revision",
                            "deletedAt",
                            "cancellationRequired",
                            "cancellationAcknowledgedAt",
                        )
                    }
                    | {
                        "title": item["body"]["title"],
                        "owner": item["body"].get("owner"),
                        "worker": item["body"].get("worker"),
                    }
                )
        return {
            "items": sorted(items, key=lambda item: item["deletedAt"], reverse=True)
        }


def acknowledge_delete(backlog, feature, revision):
    with backlog.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        item = saved(db, "backlog:deleted:" + feature)
        if not item or item["revision"] != revision:
            raise ValueError(
                "Deletion revision changed; inspect the current cancellation request"
            )
        item["cancellationAcknowledgedAt"] = (
            item["cancellationAcknowledgedAt"] or backlog.clock()
        )
        put(db, "backlog:deleted:" + feature, item)
        return {
            "feature": feature,
            "revision": revision,
            "acknowledgedAt": item["cancellationAcknowledgedAt"],
        }
