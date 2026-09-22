"""User policy controls memory review; domain changes keep atomic receipts."""

import asyncio
import json
import sqlite3
import time
from collections import defaultdict
from datetime import date
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, ValidationError

from . import approval_policy, memory_policy, proposal_decisions
from .agenda import Agenda, Selection, Triage
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
    SubtaskChange,
    card_defaults,
    initial_log,
)
from .companion import Memory, MemoryEdit

Operation = Literal[
    "agenda.triage",
    "coding.handoff",
    "commitment.create",
    "commitment.edit",
    "commitment.progress",
    "commitment.subtask",
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


class ApprovalVersion(Input):
    fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ProposalVersion(Input):
    id: UUID
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class BulkApprove(Input):
    items: list[ProposalVersion] = Field(min_length=1, max_length=100)


class EditProposal(Input):
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    input: dict
    reason: str | None = Field(default=None, min_length=1, max_length=2000)


class EditCommitment(CommitmentEdit):
    id: UUID


class ChangeSubtask(SubtaskChange):
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
    "agenda.triage": Triage,
    "coding.handoff": CodingTask,
    "commitment.create": Commitment,
    "commitment.edit": EditCommitment,
    "commitment.progress": RecordProgress,
    "commitment.subtask": ChangeSubtask,
    "capacity.create": Capacity,
    "capacity.edit": EditCapacity,
    "memory.create": Memory,
    "memory.edit": EditMemory,
    "memory.remove": RemoveMemory,
    "calendar.create": Schedule,
    "calendar.edit": EditCalendar,
    "calendar.remove": CalendarVersion,
}


READ_MATCH = """CASE WHEN json_type(r.value)='object' THEN
    json_extract(r.value,'$.fingerprint')=p.fingerprint
    AND json_extract(r.value,'$.state')=p.state
    AND json_extract(r.value,'$.updated')=p.updated
    WHEN json_type(r.value) IN ('integer','real') THEN CAST(r.value AS REAL)>=p.updated
    ELSE 0 END"""


def read_at(marker, proposal):
    if isinstance(marker, dict):
        if all(
            marker.get(k) == proposal[k] for k in ("fingerprint", "state", "updated")
        ):
            return marker.get("readAt")
    elif type(marker) in (float, int) and marker >= proposal["updated"]:
        return marker
    return None


class Proposals:
    def __init__(self, store, calendar_actions, calendar_changes, *, agenda=None):
        self.store = store
        self.commitments = Commitments(store)
        self.calendar_actions = calendar_actions
        self.calendar_changes = calendar_changes
        self.agenda = agenda or Agenda(store)
        self.locks = defaultdict(asyncio.Lock)
        self.on_content_deleted = None

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

        read = self.store.get("proposal-read:" + key)
        result["readAt"] = read_at(read, result)
        result["unread"] = result["readAt"] is None
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
                + (" AND ".join(conditions) or "1=1")
                + " ORDER BY (state IN ('pending','executing')) DESC,created DESC,id LIMIT 51 OFFSET ?",
                (*arguments, offset),
            ).fetchall()
        return {
            "items": [self.get(r["id"]) for r in rows[:50]],
            "nextOffset": offset + 50 if len(rows) > 50 else None,
            "unreadCount": self.status(thread_id)["unreadCount"],
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
        result = {
            **json.loads(row["body"]),
            "id": row["id"],
            "revision": row["revision"],
        }
        return card_defaults(result) if kind == "commitment" else result

    def change(self, key, operation, body):
        return Change(
            requestId=uuid5(NAMESPACE_URL, "leam:proposal:" + key),
            operation="edit" if operation == "calendar.edit" else "delete",
            **body.model_dump(),
        )

    async def preview(self, body, parsed):
        key = str(body.requestId)
        review = {
            "before": self.before(body.operation, parsed),
            "after": parsed.model_dump(
                mode="json", exclude_unset=not body.operation.endswith(".create")
            ),
        }
        if body.operation == "agenda.triage":
            self.agenda_scope(body.threadId, parsed)
            item = self.agenda.triage_item(parsed)
            review["before"] = {
                **Selection(date=parsed.date, timezone=parsed.timezone).identity(),
                "title": item.get("title", item.get("subject", "")),
                "key": item["key"],
                **item["triage"],
            }
        if body.operation == "commitment.subtask":
            try:
                review = self.commitments.preview_subtask(str(parsed.id), parsed)
            except KeyError as error:
                raise HTTPException(404, str(error)) from None
            except (ValueError, ValidationError) as error:
                raise HTTPException(409, str(error)) from None
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
        return review

    def agenda_scope(self, thread_id, body, *, origin=None):
        binding = self.store.get("companion-agenda:" + thread_id)
        selection = Selection(date=body.date, timezone=body.timezone).identity()
        if binding is not None and binding != selection:
            raise HTTPException(
                409, "Daily conversation belongs to a different date or timezone"
            )
        if origin == "today" and binding is None:
            raise HTTPException(
                409, "Trusted Today triage requires a canonical daily binding"
            )

    async def propose(self, body, *, origin=None, actor="user"):
        if actor not in {"user", "companion", "automation"}:
            raise ValueError("Invalid server-selected proposal actor")
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
            if body.operation == "agenda.triage":
                self.agenda_scope(body.threadId, parsed, origin=origin)
                if origin not in (None, "today"):
                    origin = None
            with self.store.connect() as db:
                decision = proposal_decisions.suppressed(
                    db,
                    key,
                    body.operation,
                    parsed.model_dump(mode="json", exclude_unset=True),
                )
            if decision:
                return {"id": key, "state": decision, "deleted": True}
            review = await self.preview(body, parsed)
            review["actionActor"] = actor
            now = time.time()
            try:
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    decision = proposal_decisions.suppressed(
                        db,
                        key,
                        body.operation,
                        parsed.model_dump(mode="json", exclude_unset=True),
                    )
                    if decision:
                        return {"id": key, "state": decision, "deleted": True}
                    review["approval"] = memory_policy.decision(
                        db, body.operation, origin=origin
                    )
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

    async def supersede_pending(self, key, expected_fingerprint, replacement):
        """Replace an unapproved create without allowing stale-ID approval.

        Internal reconciliation operation, not a permission or policy change.
        A new proposal ID is essential: approve(id) cannot attest that a user
        saw an in-place content mutation. Both state changes share a transaction.
        """
        async with self.locks[key]:
            saved = self.get(key)
            if (
                saved["operation"] != "commitment.create"
                or replacement.operation != "commitment.create"
            ):
                raise HTTPException(409, "Only a pending task creation can be replaced")
            parsed = self.model(replacement.operation, replacement.input)
            replacement_id = str(replacement.requestId)
            if replacement_id == key:
                raise HTTPException(409, "Replacement requires a new review identity")
            fingerprint = digest(replacement.model_dump(mode="json"))
            now = time.time()
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute(
                    "SELECT * FROM proposals WHERE id=?", (key,)
                ).fetchone()
                if (
                    current is None
                    or current["state"] != "pending"
                    or current["fingerprint"] != expected_fingerprint
                ):
                    raise HTTPException(
                        409, "Proposal changed; refresh before correcting it"
                    )
                approval = json.loads(current["review"])["approval"]
                # Existing review remains mandatory. No model-derived decision
                # can lower approval even if future installation defaults change.
                if approval.get("mode") != "manual":
                    raise HTTPException(409, "Proposal has already been authorized")
                review = {
                    "before": None,
                    "after": parsed.model_dump(mode="json"),
                    "approval": approval,
                    "supersedes": key,
                    "actionActor": saved["review"].get("actionActor", "user"),
                }
                db.execute(
                    "INSERT INTO proposals VALUES (?,?,?,?,?,?,?,'pending',NULL,NULL,?,?)",
                    (
                        replacement_id,
                        fingerprint,
                        replacement.threadId,
                        replacement.operation,
                        encoded(parsed.model_dump(mode="json", exclude_unset=True)),
                        encoded(review),
                        replacement.reason,
                        now,
                        now,
                    ),
                )
                self._delete_content(db, saved, "superseded")
                db.execute(
                    "INSERT INTO events(topic,payload,created) VALUES (?,?,?)",
                    (
                        "proposal.superseded",
                        encoded({"id": key, "replacementId": replacement_id}),
                        now,
                    ),
                )
            return self.get(replacement_id)

    def record(self, db, key, result, *, mark_read=False):
        from .inbox_events import proposal_event

        proposal = db.execute("SELECT * FROM proposals WHERE id=?", (key,)).fetchone()
        updated = db.execute(
            "UPDATE proposals SET state='complete',result=?,error=NULL,updated=? WHERE id=? AND state IN ('pending','executing')",
            (encoded(result), time.time(), key),
        )
        if updated.rowcount != 1:
            raise ValueError("Proposal is no longer awaiting execution")
        proposal_event(db, proposal, result)
        if mark_read:
            self._mark_read(db, key)
        db.execute(
            "INSERT INTO events(topic,payload,created) VALUES (?,?,?)",
            ("proposal.completed", encoded({"id": key}), time.time()),
        )

    def local(self, operation, body, record):
        if operation == "agenda.triage":
            return self.agenda.triage(body, record=record)
        if operation == "commitment.create":
            return self.commitments.create(body, record=record)
        if operation == "commitment.edit":
            return self.commitments.edit(
                str(body.id),
                CommitmentEdit(**body.model_dump(exclude={"id"}, exclude_unset=True)),
                record=record,
            )
        if operation == "commitment.subtask":
            return self.commitments.subtask(str(body.id), body, record=record)
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

    async def approve(self, key, fingerprint=None):
        if self.decision_state(key) is not None:
            raise HTTPException(
                409, "Proposal was declined or replaced; refresh before approving"
            )
        if self.get(key)["operation"] == "coding.handoff":
            raise HTTPException(409, "Review the exact task and use Start in Coding")
        async with self.locks[key]:
            if fingerprint is not None and self.get(key)["fingerprint"] != fingerprint:
                raise HTTPException(409, "Proposal changed; review before approving")
            return await self._approve_locked(key, mark_read=True)

    async def _approve_locked(self, key, *, mark_read=False):
        saved = self.get(key)
        if saved["state"] == "complete":
            if mark_read:
                self.mark_completed_read(key)
                return self.get(key)
            return saved
        if saved["state"] not in ("pending", "executing"):
            raise HTTPException(409, "Proposal is no longer awaiting approval")
        operation = saved["operation"]
        body = self.model(operation, saved["input"])
        try:
            if operation == "agenda.triage":
                self.agenda_scope(saved["thread_id"], body)
            if operation.startswith("calendar."):
                with self.store.connect() as db:
                    changed = db.execute(
                        "UPDATE proposals SET state='executing',updated=? WHERE id=? AND state IN ('pending','executing') AND fingerprint=?",
                        (time.time(), key, saved["fingerprint"]),
                    )
                    if changed.rowcount != 1:
                        raise HTTPException(
                            409,
                            "Proposal changed before execution; refresh before approving",
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
                    self.record(db, key, result, mark_read=mark_read)
            else:
                self.local(
                    operation,
                    body,
                    lambda db, result: self.record(
                        db, key, result, mark_read=mark_read
                    ),
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
                if mark_read:
                    self.mark_completed_read(key)
                    return self.get(key)
                return current
            raise HTTPException(status, str(message)) from None
        return self.get(key)

    def decision_state(self, key):
        with self.store.connect() as db:
            return proposal_decisions.state(db, key)

    def _delete_content(self, db, saved, state):
        key = saved["id"]
        proposal_decisions.record(db, key, saved["operation"], saved["input"], state)
        db.execute("DELETE FROM proposals WHERE id=?", (key,))
        db.execute(
            "DELETE FROM settings WHERE key IN (?,?)",
            ("proposal-read:" + key, HANDOFF_PREFIX + key),
        )
        # Reconciliation provenance is derived suggestion content, not transcript.
        db.execute(
            "DELETE FROM settings WHERE key LIKE 'today-obligation-source:%' AND json_extract(value,'$.proposalId')=?",
            (key,),
        )
        if self.on_content_deleted:
            self.on_content_deleted(db, key)

    async def decline(self, key):
        async with self.locks[key]:
            if self.decision_state(key) == "declined":
                return {"id": key, "state": "declined", "deleted": True}
            saved = self.get(key)
            if saved["state"] != "pending":
                raise HTTPException(409, "Only an unapproved proposal can be declined")
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute(
                    "SELECT state,fingerprint FROM proposals WHERE id=?", (key,)
                ).fetchone()
                if (
                    current is None
                    or current["state"] != "pending"
                    or current["fingerprint"] != saved["fingerprint"]
                ):
                    raise HTTPException(
                        409, "Proposal started or changed; refresh before declining"
                    )
                self._delete_content(db, saved, "declined")
            return {"id": key, "state": "declined", "deleted": True}

    def purge_declined(self):
        """Explicit startup cleanup after the coordinator's data backup."""
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM proposals WHERE state='declined'"
            ).fetchall()
            for row in rows:
                saved = {**dict(row), "input": json.loads(row["input"])}
                self._delete_content(db, saved, "declined")
        return len(rows)

    def status(self, thread_id=None):
        conditions = ["operation NOT LIKE 'memory.%'"]
        args = []
        if thread_id is not None:
            conditions.append("thread_id=?")
            args.append(thread_id)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT COUNT(CASE WHEN NOT coalesce(("
                + READ_MATCH
                + "),0) THEN 1 END) unread, COUNT(CASE WHEN p.state='pending' THEN 1 END) pending FROM proposals p LEFT JOIN settings r ON r.key='proposal-read:' || p.id WHERE "
                + " AND ".join(conditions),
                args,
            ).fetchone()
        return {"unreadCount": row["unread"], "pendingCount": row["pending"]}

    def _mark_read(self, db, key, *, complete_only=False):
        row = db.execute(
            "SELECT fingerprint,state,updated FROM proposals WHERE id=?", (key,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Proposal not found")
        if complete_only and row["state"] != "complete":
            return False
        existing = db.execute(
            "SELECT value FROM settings WHERE key=?", ("proposal-read:" + key,)
        ).fetchone()
        prior = json.loads(existing[0]) if existing else None
        if isinstance(prior, dict) and read_at(prior, row) is not None:
            return True
        value = {**dict(row), "readAt": time.time()}
        db.execute(
            "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("proposal-read:" + key, encoded(value)),
        )
        return True

    def mark_completed_read(self, key):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._mark_read(db, key, complete_only=True)

    def mark_read(self, key):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._mark_read(db, key)
        return {"id": key, "unread": False}

    def mark_all_read(self):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT id FROM proposals WHERE operation NOT LIKE 'memory.%'"
            ).fetchall()
            for row in rows:
                self._mark_read(db, row["id"])
        return {"readCount": len(rows)}

    async def approve_all(self, body):
        result = {"items": [], "failed": [], "skipped": []}
        seen = set()
        for item in body.items:
            key = str(item.id)
            if key in seen:
                continue
            seen.add(key)
            try:
                saved = self.get(key)
                if saved["operation"] == "coding.handoff":
                    result["skipped"].append(key)
                    continue
                result["items"].append(await self.approve(key, item.fingerprint))
            except (HTTPException, ValueError, KeyError):
                result["failed"].append(
                    {
                        "id": key,
                        "error": "Approval could not complete; refresh and review this item.",
                    }
                )
        return result

    async def edit(self, key, body):
        async with self.locks[key]:
            saved = self.get(key)
            if saved["state"] != "pending" or saved["fingerprint"] != body.fingerprint:
                raise HTTPException(409, "Proposal changed; refresh before editing")
            if saved["operation"] == "coding.handoff":
                raise HTTPException(
                    409, "Edit the coding prompt through its reviewed handoff control"
                )
            parsed = self.model(saved["operation"], body.input)
            # Keep target/concurrency identities from the original reviewed source.
            for field in (
                "id",
                "revision",
                "commitmentRevision",
                "creationId",
                "expectedEtag",
            ):
                if saved["input"].get(field) != body.input.get(field):
                    raise HTTPException(
                        409, "Record identities and revisions cannot be edited"
                    )
            replacement = Propose(
                requestId=uuid4(),
                threadId=saved["thread_id"],
                operation=saved["operation"],
                input=parsed.model_dump(mode="json", exclude_unset=True),
                reason=body.reason or saved["reason"],
            )
            review = await self.preview(replacement, parsed)
            replacement_id = str(replacement.requestId)
            now = time.time()
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute(
                    "SELECT state,fingerprint FROM proposals WHERE id=?", (key,)
                ).fetchone()
                if (
                    current is None
                    or current["state"] != "pending"
                    or current["fingerprint"] != body.fingerprint
                ):
                    raise HTTPException(409, "Proposal changed; refresh before editing")
                review["approval"] = {"mode": "manual"}
                review["supersedes"] = key
                review["actionActor"] = saved["review"].get("actionActor", "user")
                db.execute(
                    "INSERT INTO proposals VALUES (?,?,?,?,?,?,?,'pending',NULL,NULL,?,?)",
                    (
                        replacement_id,
                        digest(replacement.model_dump(mode="json")),
                        replacement.threadId,
                        replacement.operation,
                        encoded(replacement.input),
                        encoded(review),
                        replacement.reason,
                        now,
                        now,
                    ),
                )
                self._delete_content(db, saved, "superseded")
            return self.get(replacement_id)


def router(proposals):
    routes = APIRouter(prefix="/api/proposals")

    @routes.get("/policy")
    async def get_policy():
        return approval_policy.get(proposals.store)

    @routes.put("/policy")
    async def set_policy(body: approval_policy.ApprovalPolicy):
        return approval_policy.update(proposals.store, body)

    @routes.get("/status")
    async def status(
        threadId: str | None = Query(default=None, min_length=1, max_length=200),
    ):
        return proposals.status(threadId)

    @routes.post("/read-all")
    async def read_all():
        return proposals.mark_all_read()

    @routes.post("/approve-all")
    async def approve_all(body: BulkApprove):
        return await proposals.approve_all(body)

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
        threadId: str | None = Query(default=None, min_length=1, max_length=200),
        offset: int = Query(default=0, ge=0),
        excludeMemory: bool = False,
    ):
        return proposals.list(threadId, offset, exclude_memory=excludeMemory)

    @routes.post("")
    async def propose(body: Propose):
        return await proposals.propose(body)

    @routes.get("/{key}")
    async def get(key: UUID):
        return proposals.get(str(key))

    @routes.post("/{key}/approve")
    async def approve(key: UUID, body: ApprovalVersion | None = None):
        return await proposals.approve(str(key), body.fingerprint if body else None)

    @routes.post("/{key}/read")
    async def read(key: UUID):
        return proposals.mark_read(str(key))

    @routes.patch("/{key}")
    async def edit(key: UUID, body: EditProposal):
        return await proposals.edit(str(key), body)

    @routes.post("/{key}/decline")
    async def decline(key: UUID):
        return await proposals.decline(str(key))

    return routes
