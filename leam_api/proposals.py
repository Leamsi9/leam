"""User policy controls memory review; domain changes keep atomic receipts."""

import asyncio
import json
import sqlite3
import time
from collections import defaultdict
from datetime import date
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, ValidationError

from . import memory_policy
from .calendar_actions import CreateAction, Schedule, digest, encoded
from .calendar_changes import Change, EventEdit
from .coding_handoff import (
    PREFIX as HANDOFF_PREFIX,
)
from .coding_handoff import (
    CodingTask,
)
from .coding_handoff import (
    snapshot as handoff_snapshot,
)
from .commitments import (
    Capacity,
    CapacityEdit,
    Commitment,
    CommitmentEdit,
    Commitments,
    Input,
    Progress,
    initial_log,
)
from .companion import Memory, MemoryEdit

Operation = Literal[
    "coding.handoff",
    "commitment.create",
    "commitment.edit",
    "commitment.progress",
    "capacity.create",
    "capacity.edit",
    "memory.create",
    "memory.edit",
    "memory.remove",
    "calendar.create",
    "calendar.edit",
    "calendar.remove",
]


class Propose(Input):
    requestId: UUID
    threadId: str = Field(min_length=1, max_length=200)
    operation: Operation
    input: dict
    reason: str = Field(min_length=1, max_length=2000)


class EditCommitment(CommitmentEdit):
    id: UUID


class RecordProgress(Progress):
    id: UUID
    date: date


class EditCapacity(CapacityEdit):
    id: UUID


class EditMemory(MemoryEdit):
    id: UUID


class RemoveMemory(Input):
    id: UUID
    revision: int = Field(ge=1)


class CalendarVersion(Input):
    creationId: UUID
    expectedEtag: str = Field(
        min_length=1, max_length=2000, pattern=r'^(W/)?"[^\r\n"]+"$'
    )


class EditCalendar(CalendarVersion):
    edit: EventEdit


INPUTS = {
    "coding.handoff": CodingTask,
    "commitment.create": Commitment,
    "commitment.edit": EditCommitment,
    "commitment.progress": RecordProgress,
    "capacity.create": Capacity,
    "capacity.edit": EditCapacity,
    "memory.create": Memory,
    "memory.edit": EditMemory,
    "memory.remove": RemoveMemory,
    "calendar.create": Schedule,
    "calendar.edit": EditCalendar,
    "calendar.remove": CalendarVersion,
}


class Proposals:
    def __init__(self, store, calendar_actions, calendar_changes):
        self.store = store
        self.commitments = Commitments(store)
        self.calendar_actions = calendar_actions
        self.calendar_changes = calendar_changes
        self.locks = defaultdict(asyncio.Lock)

    def get(self, key):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM proposals WHERE id=?", (key,)).fetchone()
        if not row:
            raise HTTPException(404, "Proposal not found")
        result = {
            **dict(row),
            "input": json.loads(row["input"]),
            "review": json.loads(row["review"]),
            "result": json.loads(row["result"]) if row["result"] else None,
        }

        if result["operation"] == "coding.handoff":
            saved = self.store.get(HANDOFF_PREFIX + key)
            if saved:
                with self.store.connect() as db:
                    result["handoff"] = handoff_snapshot(db, saved)
        return result

    def list(self, thread_id, offset=0, *, memory_only=False, exclude_memory=False):
        conditions, arguments = [], []
        if thread_id is not None:
            conditions.append("thread_id=?")
            arguments.append(thread_id)
        if memory_only:
            conditions.append("operation LIKE 'memory.%'")
        if exclude_memory:
            conditions.append("operation NOT LIKE 'memory.%'")
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT id FROM proposals WHERE "
                + " AND ".join(conditions)
                + " ORDER BY (state IN ('pending','executing')) DESC,created DESC,id LIMIT 51 OFFSET ?",
                (*arguments, offset),
            ).fetchall()
        return {
            "items": [self.get(r["id"]) for r in rows[:50]],
            "nextOffset": offset + 50 if len(rows) > 50 else None,
        }

    def model(self, operation, data):
        try:
            return INPUTS[operation](**data)
        except ValidationError as error:
            raise HTTPException(
                422,
                [
                    {"loc": e["loc"], "msg": e["msg"], "type": e["type"]}
                    for e in error.errors()
                ],
            ) from None

    def before(self, operation, body):
        if not hasattr(body, "id"):
            return None
        kind = operation.split(".")[0]
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM entities WHERE id=? AND kind=?", (str(body.id), kind)
            ).fetchone()
        if not row:
            raise HTTPException(404, "Record not found")
        expected = (
            body.commitmentRevision
            if operation == "commitment.progress"
            else body.revision
        )
        if row["revision"] != expected:
            raise HTTPException(409, "Record changed; read its current revision first")
        return {**json.loads(row["body"]), "id": row["id"], "revision": row["revision"]}

    def change(self, key, operation, body):
        return Change(
            requestId=uuid5(NAMESPACE_URL, "leam:proposal:" + key),
            operation="edit" if operation == "calendar.edit" else "delete",
            **body.model_dump(),
        )

    async def propose(self, body):
        key = str(body.requestId)
        fingerprint = digest(body.model_dump(mode="json"))
        async with self.locks[key]:
            with self.store.connect() as db:
                previous = db.execute(
                    "SELECT fingerprint FROM proposals WHERE id=?", (key,)
                ).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise HTTPException(
                        409, "Proposal ID belongs to a different suggestion"
                    )
                saved = self.get(key)
                if (
                    saved["state"] == "pending"
                    and saved["review"].get("approval", {}).get("mode") == "automatic"
                ):
                    return await self._approve_locked(key)
                return saved
            parsed = self.model(body.operation, body.input)
            review = {
                "before": self.before(body.operation, parsed),
                "after": parsed.model_dump(
                    mode="json", exclude_unset=not body.operation.endswith(".create")
                ),
            }
            if body.operation == "commitment.progress":
                logs = self.commitments.history(str(parsed.id))["items"]
                log = next(
                    (item for item in logs if item["date"] == str(parsed.date)),
                    initial_log(parsed.date),
                )
                if log["revision"] != parsed.revision:
                    raise HTTPException(
                        409, "Progress changed; read its current revision first"
                    )
                commitment = review["before"]
                done = (
                    commitment["status"] == "completed"
                    if commitment["kind"] == "task"
                    else log["done"]
                )
                review["before"] = {
                    "title": commitment["title"],
                    "date": str(parsed.date),
                    "value": log["value"],
                    "completed": done,
                }
                review["after"] = {
                    "date": str(parsed.date),
                    "action": "Record amount"
                    if parsed.operation == "set"
                    else "Undo completion"
                    if parsed.operation == "toggle" and done
                    else "Mark complete",
                }
                if parsed.operation == "set":
                    review["after"]["value"] = parsed.value
            if body.operation == "calendar.create":
                review = self.calendar_actions.preview(parsed)
            elif body.operation in ("calendar.edit", "calendar.remove"):
                review = await self.calendar_changes.preview(
                    self.change(key, body.operation, parsed)
                )
            now = time.time()
            try:
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    review["approval"] = memory_policy.decision(db, body.operation)
                    db.execute(
                        "INSERT INTO proposals VALUES (?,?,?,?,?,?,?,'pending',NULL,NULL,?,?)",
                        (
                            key,
                            fingerprint,
                            body.threadId,
                            body.operation,
                            encoded(parsed.model_dump(mode="json", exclude_unset=True)),
                            encoded(review),
                            body.reason,
                            now,
                            now,
                        ),
                    )
            except sqlite3.IntegrityError:
                saved = self.get(key)
                if saved["fingerprint"] != fingerprint:
                    raise HTTPException(
                        409, "Proposal ID belongs to a different suggestion"
                    ) from None
            saved = self.get(key)
            if (
                saved["state"] == "pending"
                and saved["review"].get("approval", {}).get("mode") == "automatic"
            ):
                return await self._approve_locked(key)
            return saved

    def record(self, db, key, result):
        updated = db.execute(
            "UPDATE proposals SET state='complete',result=?,error=NULL,updated=? WHERE id=? AND state IN ('pending','executing')",
            (encoded(result), time.time(), key),
        )
        if updated.rowcount != 1:
            raise ValueError("Proposal is no longer awaiting execution")
        db.execute(
            "INSERT INTO events(topic,payload,created) VALUES (?,?,?)",
            ("proposal.completed", encoded({"id": key}), time.time()),
        )

    def local(self, operation, body, record):
        if operation == "commitment.create":
            return self.commitments.create(body, record=record)
        if operation == "commitment.edit":
            return self.commitments.edit(
                str(body.id),
                CommitmentEdit(**body.model_dump(exclude={"id"}, exclude_unset=True)),
                record=record,
            )
        if operation == "commitment.progress":
            return self.commitments.progress(
                str(body.id),
                body.date,
                Progress(**body.model_dump(exclude={"id", "date"})),
                record=record,
            )
        if operation in ("capacity.create", "memory.create"):
            return self.store.create(
                operation.split(".")[0], body.model_dump(mode="json"), record=record
            )
        if operation in ("capacity.edit", "memory.edit"):
            return self.store.update(
                str(body.id),
                operation.split(".")[0],
                body.revision,
                body.model_dump(
                    mode="json", exclude={"id", "revision"}, exclude_unset=True
                ),
                record=record,
            )
        if operation == "memory.remove":
            return self.store.delete(
                str(body.id), "memory", body.revision, record=record
            )
        raise ValueError("Unsupported local operation")

    async def approve(self, key):
        if self.get(key)["operation"] == "coding.handoff":
            raise HTTPException(409, "Review the exact task and use Start in Coding")
        async with self.locks[key]:
            return await self._approve_locked(key)

    async def _approve_locked(self, key):
        saved = self.get(key)
        if saved["state"] == "complete":
            return saved
        if saved["state"] not in ("pending", "executing"):
            raise HTTPException(409, "Proposal is no longer awaiting approval")
        operation = saved["operation"]
        body = self.model(operation, saved["input"])
        try:
            if operation.startswith("calendar."):
                with self.store.connect() as db:
                    db.execute(
                        "UPDATE proposals SET state='executing',updated=? WHERE id=?",
                        (time.time(), key),
                    )
                if operation == "calendar.create":
                    result = await self.calendar_actions.create(
                        CreateAction(
                            requestId=uuid5(NAMESPACE_URL, "leam:proposal:" + key),
                            schedule=body,
                            previewDigest=saved["review"]["digest"],
                        )
                    )
                else:
                    result = await self.calendar_changes.apply(
                        self.change(key, operation, body)
                    )
                with self.store.connect() as db:
                    self.record(db, key, result)
            else:
                self.local(
                    operation, body, lambda db, result: self.record(db, key, result)
                )
        except (HTTPException, ValueError, KeyError) as error:
            status = error.status_code if isinstance(error, HTTPException) else 409
            message = error.detail if isinstance(error, HTTPException) else str(error)
            # Network failures retain an approved, recoverable external action.
            external_id = str(uuid5(NAMESPACE_URL, "leam:proposal:" + key))
            reserved = (
                self.calendar_actions.get(external_id)
                if operation == "calendar.create"
                else self.calendar_changes.saved(external_id)
                if operation.startswith("calendar.")
                else None
            )
            recoverable = reserved and reserved.get("state") != "conflict"
            state = (
                "executing"
                if operation.startswith("calendar.") and (status >= 500 or recoverable)
                else "conflict"
            )
            with self.store.connect() as db:
                db.execute(
                    "UPDATE proposals SET state=?,error=?,updated=? WHERE id=? AND state IN ('pending','executing')",
                    (state, str(message), time.time(), key),
                )
            current = self.get(key)
            if current["state"] == "complete":
                return current
            raise HTTPException(status, str(message)) from None
        return self.get(key)

    async def decline(self, key):
        async with self.locks[key]:
            saved = self.get(key)
            if saved["state"] == "declined":
                return saved
            if saved["state"] != "pending":
                raise HTTPException(
                    409, "Only an unapproved suggestion can be declined"
                )
            with self.store.connect() as db:
                changed = db.execute(
                    "UPDATE proposals SET state='declined',updated=? WHERE id=? AND state='pending'",
                    (time.time(), key),
                )
                if changed.rowcount != 1:
                    raise HTTPException(
                        409, "Proposal started or changed; refresh before declining"
                    )
            return self.get(key)


def router(proposals):
    routes = APIRouter(prefix="/api/proposals")

    @routes.get("/memory/policy")
    async def get_memory_policy():
        return memory_policy.get(proposals.store)

    @routes.put("/memory/policy")
    async def set_memory_policy(body: memory_policy.MemoryPolicy):
        return memory_policy.update(proposals.store, body)

    @routes.get("/memory")
    async def memory_suggestions(offset: int = Query(default=0, ge=0)):
        return proposals.list(None, offset, memory_only=True)

    @routes.get("")
    async def list(
        threadId: str = Query(min_length=1, max_length=200),
        offset: int = Query(default=0, ge=0),
        excludeMemory: bool = False,
    ):
        return proposals.list(threadId, offset, exclude_memory=excludeMemory)

    @routes.post("")
    async def propose(body: Propose):
        return await proposals.propose(body)

    @routes.post("/{key}/approve")
    async def approve(key: UUID):
        return await proposals.approve(str(key))

    @routes.post("/{key}/decline")
    async def decline(key: UUID):
        return await proposals.decline(str(key))

    return routes
