"""Commitments domain. UI and future companion tools call the same operations."""

import copy
import hashlib
import json
import time
import uuid
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .board_order import MoveCard, read_orders
from .board_order import move as move_card


class Input(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, allow_inf_nan=False
    )


class Capacity(Input):
    name: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=10000)
    record: str = Field(default="", max_length=10000)


class CapacityEdit(Capacity):
    revision: int = Field(ge=1)


Owner = Literal["user", "leam"]
Stage = Literal["todo", "in_progress", "blocked"]
Priority = Literal["low", "normal", "high"]
SubtaskStatus = Literal["todo", "in_progress", "blocked", "completed"]
MAX_SUBTASKS = 32
MAX_SUBTASK_DEPTH = 3


class Subtask(Input):
    id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    owner: Owner = "user"
    status: SubtaskStatus = "todo"
    notes: str = Field(default="", max_length=500)
    startDate: date | None = None
    endDate: date | None = None
    dueDate: date | None = None
    children: list["Subtask"] = Field(default_factory=list, max_length=MAX_SUBTASKS)

    @model_validator(mode="after")
    def valid_dates(self):
        if self.startDate and self.endDate and self.endDate < self.startDate:
            raise ValueError("Subtask end date must be on or after start date")
        return self


def validate_subtasks(items):
    seen = set()

    def visit(rows, depth):
        if rows and depth > MAX_SUBTASK_DEPTH:
            raise ValueError("Subtasks support at most three levels")
        for item in rows:
            if item.id in seen:
                raise ValueError("Subtask IDs must be unique across the card")
            seen.add(item.id)
            if len(seen) > MAX_SUBTASKS:
                raise ValueError("A card supports at most 32 subtasks")
            visit(item.children, depth + 1)

    visit(items, 1)
    return items


class SubtaskChange(Input):
    revision: int = Field(ge=1)
    action: Literal["add", "edit", "remove"]
    subtaskId: uuid.UUID
    parentId: uuid.UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    owner: Owner | None = None
    status: SubtaskStatus | None = None
    notes: str | None = Field(default=None, max_length=500)
    startDate: date | None = None
    endDate: date | None = None
    dueDate: date | None = None

    @model_validator(mode="after")
    def valid_action(self):
        fields = self.model_fields_set - {"revision", "action", "subtaskId", "id"}
        if self.action == "add" and not self.title:
            raise ValueError("A new subtask needs a title")
        if self.action != "add" and "parentId" in fields:
            raise ValueError("Parent is only supplied when adding a subtask")
        if self.action == "remove" and fields:
            raise ValueError("Removal accepts only the exact subtask ID and revision")
        if self.action == "edit" and not fields:
            raise ValueError("Choose a subtask field to change")
        return self


def card_defaults(data):
    # Read-time defaults keep older entities valid without rewriting user data.
    return {
        "owner": "user",
        "stage": "todo",
        "priority": "normal",
        "dueDate": None,
        "subtasks": [],
        **data,
    }


class Commitment(Input):
    title: str = Field(min_length=1, max_length=500)
    owner: Owner = "user"
    stage: Stage = "todo"
    priority: Priority = "normal"
    dueDate: date | None = None
    subtasks: list[Subtask] = Field(default_factory=list, max_length=MAX_SUBTASKS)
    kind: Literal["task", "habit", "goal"] = "task"
    measure: Literal["boolean", "count", "minutes"] = "boolean"
    target: float = Field(default=1, ge=0, le=1000000)
    notes: str = Field(default="", max_length=20000)
    status: Literal["active", "completed", "paused"] = "active"
    capacityId: str | None = None
    startDate: date | None = None
    endDate: date | None = None
    timezone: str = "Europe/London"
    reminderTime: str | None = Field(
        default=None, pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$"
    )
    reward: str = Field(default="", max_length=2000)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Choose a valid IANA timezone")
        return value

    @model_validator(mode="after")
    def valid_window(self):
        if self.startDate and self.endDate and self.endDate < self.startDate:
            raise ValueError("End date must be on or after start date")
        validate_subtasks(self.subtasks)
        if self.measure == "boolean":
            self.target = 1
        return self


class CommitmentEdit(Input):
    revision: int = Field(ge=1)
    owner: Owner | None = None
    stage: Stage | None = None
    priority: Priority | None = None
    dueDate: date | None = None
    subtasks: list[Subtask] | None = Field(default=None, max_length=MAX_SUBTASKS)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    kind: Literal["task", "habit", "goal"] | None = None
    measure: Literal["boolean", "count", "minutes"] | None = None
    target: float | None = Field(default=None, ge=0, le=1000000)
    notes: str | None = None
    status: Literal["active", "completed", "paused"] | None = None
    capacityId: str | None = None
    startDate: date | None = None
    endDate: date | None = None
    timezone: str | None = None
    reminderTime: str | None = None
    reward: str | None = None

    @field_validator("subtasks")
    @classmethod
    def bounded_subtasks(cls, value):
        return validate_subtasks(value) if value is not None else value


class Progress(Input):
    revision: int = Field(ge=0)
    commitmentRevision: int = Field(ge=1)
    operation: Literal["set", "toggle", "complete"] = "set"
    value: float | None = Field(default=None, ge=0, le=1000000)

    @model_validator(mode="after")
    def needs_value(self):
        if self.operation == "set" and self.value is None:
            raise ValueError("A measurement is required")
        return self


class Removal(Input):
    previewToken: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: Literal[True]


class CapacityRemoval(Removal):
    operation: Literal["empty", "move", "cascade"]
    targetCapacityId: str | None = Field(default=None, min_length=1, max_length=100)
    targetRevision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def valid_target(self):
        if self.operation == "move":
            if not self.targetCapacityId or self.targetRevision is None:
                raise ValueError("Choose a destination capacity")
        elif self.targetCapacityId is not None or self.targetRevision is not None:
            raise ValueError("A destination is only valid when moving commitments")
        return self


def entity(row):
    result = dict(json.loads(row["body"]), id=row["id"], revision=row["revision"])
    return card_defaults(result) if row["kind"] == "commitment" else result


def initial_log(day):
    return {
        "date": str(day),
        "value": 0,
        "done": False,
        "preDoneValue": None,
        "revision": 0,
    }


class Commitments:
    def __init__(self, store):
        self.store = store

    def _capacity(self, db, capacity_id):
        if (
            capacity_id
            and not db.execute(
                "SELECT 1 FROM entities WHERE id=? AND kind='capacity'", (capacity_id,)
            ).fetchone()
        ):
            raise KeyError("Capacity not found")

    def create(self, body: Commitment, *, record=None):
        data = body.model_dump(mode="json")
        key = str(uuid.uuid4())
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._capacity(db, body.capacityId)
            from .completion_evidence import stamp_changes

            stamp_changes({}, data)
            db.execute(
                "INSERT INTO entities VALUES (?,?,?,?,?)",
                (key, "commitment", 1, json.dumps(data), time.time()),
            )
            result = dict(data, id=key, revision=1)
            from .completion_evidence import record_changes

            record_changes(db, key, 1, {}, data)
            if record:
                record(db, result)
        return result

    def edit(self, key, body: CommitmentEdit, *, record=None):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM entities WHERE id=? AND kind='commitment'", (key,)
            ).fetchone()
            if row is None:
                raise KeyError("Commitment not found")
            if row["revision"] != body.revision:
                raise ValueError("Changed on another device. Reload before saving.")
            data = Commitment(
                **dict(
                    {
                        k: v
                        for k, v in json.loads(row["body"]).items()
                        if k in Commitment.model_fields
                    },
                    **body.model_dump(
                        mode="json", exclude_unset=True, exclude={"revision"}
                    ),
                )
            ).model_dump(mode="json")
            self._capacity(db, data["capacityId"])
            from .completion_evidence import stamp_changes

            stamp_changes(json.loads(row["body"]), data)
            db.execute(
                "UPDATE entities SET body=?,revision=revision+1,updated=? WHERE id=?",
                (json.dumps(data), time.time(), key),
            )
            result = dict(data, id=key, revision=body.revision + 1)
            from .completion_evidence import record_changes

            record_changes(
                db,
                key,
                result["revision"],
                json.loads(row["body"]),
                data,
            )
            if record:
                record(db, result)
        return result

    @staticmethod
    def _change_subtask(current, body):
        data = copy.deepcopy(current)
        tree = data.setdefault("subtasks", [])
        nodes = {}

        def visit(rows):
            for node in rows:
                nodes[node["id"]] = (node, rows)
                visit(node.get("children", []))

        visit(tree)
        key = str(body.subtaskId)
        fields = body.model_dump(
            mode="json",
            exclude_unset=True,
            exclude={"revision", "action", "subtaskId", "parentId", "id"},
        )
        if body.action == "add":
            if key in nodes:
                raise ValueError("Subtask ID already exists; read the current card")
            siblings = tree
            if body.parentId:
                parent = nodes.get(str(body.parentId))
                if not parent:
                    raise KeyError("Parent subtask not found")
                siblings = parent[0].setdefault("children", [])
            siblings.append(
                Subtask(id=body.subtaskId, **fields).model_dump(mode="json")
            )
        else:
            found = nodes.get(key)
            if not found:
                raise KeyError("Subtask not found")
            node, siblings = found
            if body.action == "remove":
                siblings.remove(node)
            else:
                node.update(fields)
        return Commitment(
            **{k: v for k, v in data.items() if k in Commitment.model_fields}
        ).model_dump(mode="json")

    def preview_subtask(self, key, body):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM entities WHERE id=? AND kind='commitment'", (key,)
            ).fetchone()
            if row is None:
                raise KeyError("Commitment not found")
            if row["revision"] != body.revision:
                raise ValueError("Changed on another device. Reload before saving.")
            current = entity(row)
            return {
                "before": current,
                "after": self._change_subtask(current, body),
                "assignment": "Ownership only; assigning Leam does not start execution.",
            }

    def subtask(self, key, body, *, record=None):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM entities WHERE id=? AND kind='commitment'", (key,)
            ).fetchone()
            if row is None:
                raise KeyError("Commitment not found")
            if row["revision"] != body.revision:
                raise ValueError("Changed on another device. Reload before saving.")
            data = self._change_subtask(entity(row), body)
            from .completion_evidence import stamp_changes

            stamp_changes(json.loads(row["body"]), data)
            db.execute(
                "UPDATE entities SET body=?,revision=revision+1,updated=? WHERE id=?",
                (json.dumps(data), time.time(), key),
            )
            result = dict(data, id=key, revision=body.revision + 1)
            from .completion_evidence import record_changes

            record_changes(db, key, result["revision"], json.loads(row["body"]), data)
            if record:
                record(db, result)
        return result

    def progress(self, key, day, body: Progress, notification=None, *, record=None):
        day = str(day)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM entities WHERE id=? AND kind='commitment'", (key,)
            ).fetchone()
            if row is None:
                raise KeyError("Commitment not found")
            if notification:
                notice = db.execute(
                    "SELECT * FROM reminder_jobs WHERE id=? AND commitment_id=? AND day=?",
                    (notification[0], key, day),
                ).fetchone()
                if (
                    notice is None
                    or notice["revision"] != notification[1]
                    or notice["state"] != "ready"
                ):
                    raise ValueError("Reminder changed. Refresh before completing it.")
            current = entity(row)
            if current["revision"] != body.commitmentRevision:
                raise ValueError(
                    "Commitment changed. Reload before recording progress."
                )
            if (current.get("startDate") and day < current["startDate"]) or (
                current.get("endDate") and day > current["endDate"]
            ):
                raise ValueError("This date is outside the commitment window")
            saved = db.execute(
                "SELECT * FROM daily_logs WHERE commitment_id=? AND day=?", (key, day)
            ).fetchone()
            log = (
                dict(json.loads(saved["body"]), revision=saved["revision"])
                if saved
                else initial_log(day)
            )
            if current["kind"] == "task":
                log["done"] = current["status"] == "completed"
            if log["revision"] != body.revision:
                raise ValueError("Progress changed on another device. Reload first.")
            if body.operation == "set":
                value = (
                    body.value
                    if current["measure"] != "boolean"
                    else int(bool(body.value))
                )
                log.update(
                    value=value,
                    done=bool(value >= current["target"])
                    if current["target"] > 0
                    else False,
                    preDoneValue=None,
                )
            elif body.operation == "toggle" and log["done"]:
                value = log.get("preDoneValue")
                if current["measure"] == "boolean":
                    value = 0
                elif value is None:
                    value = log["value"]
                log.update(done=False, value=value, preDoneValue=None)
            elif not log["done"]:
                before = log["value"]
                log.update(
                    done=True,
                    preDoneValue=before,
                    value=1
                    if current["measure"] == "boolean"
                    else (before if before > 0 else current["target"]),
                )
            log.update(
                revision=log["revision"] + 1,
                date=day,
                updated=time.time(),
                target=current["target"],
                measure=current["measure"],
            )
            db.execute(
                "INSERT INTO daily_logs VALUES (?,?,?,?) ON CONFLICT(commitment_id,day) DO UPDATE SET revision=excluded.revision,body=excluded.body",
                (key, day, log["revision"], json.dumps(log)),
            )
            if current["kind"] == "task":
                from .completion_evidence import record_changes, stamp_changes

                before = dict(current)
                current["status"] = "completed" if log["done"] else "active"
                current["revision"] += 1
                stamp_changes(before, current)
                stored = {
                    k: v for k, v in current.items() if k not in ["id", "revision"]
                }
                db.execute(
                    "UPDATE entities SET body=?,revision=?,updated=? WHERE id=?",
                    (json.dumps(stored), current["revision"], time.time(), key),
                )
                record_changes(db, key, current["revision"], before, current)
            if log["done"]:
                if current["kind"] == "task":
                    db.execute(
                        "UPDATE reminder_jobs SET state='completed',revision=revision+1,updated=? WHERE commitment_id=? AND state IN ('ready','scheduled')",
                        (time.time(), key),
                    )
                else:
                    db.execute(
                        "UPDATE reminder_jobs SET state='completed',revision=revision+1,updated=? WHERE commitment_id=? AND day=? AND state IN ('ready','scheduled')",
                        (time.time(), key, day),
                    )
            result = {"commitment": current, "log": log}
            db.execute(
                "INSERT INTO events(topic,payload,created) VALUES (?,?,?)",
                (
                    "commitment.progress",
                    json.dumps({"id": key, "date": day, "revision": log["revision"]}),
                    time.time(),
                ),
            )
            if record:
                record(db, result)
        return result

    def today(self, chosen: date | None = None):
        items = []
        with self.store.connect() as db:
            for row in db.execute(
                "SELECT * FROM entities WHERE kind='commitment' ORDER BY updated DESC"
            ):
                item = entity(row)
                day = str(
                    chosen
                    or datetime.now(
                        ZoneInfo(item.get("timezone", "Europe/London"))
                    ).date()
                )
                if item["status"] == "paused" or (
                    item["kind"] != "task" and item["status"] == "completed"
                ):
                    continue
                if (item.get("startDate") and day < item["startDate"]) or (
                    item.get("endDate") and day > item["endDate"]
                ):
                    continue
                saved = db.execute(
                    "SELECT * FROM daily_logs WHERE commitment_id=? AND day=?",
                    (item["id"], day),
                ).fetchone()
                if (
                    item["kind"] == "task"
                    and item["status"] == "completed"
                    and saved is None
                ):
                    continue
                log = (
                    dict(json.loads(saved["body"]), revision=saved["revision"])
                    if saved
                    else initial_log(day)
                )
                items.append(dict(item, log=log, date=day))
        return {"items": items, "date": str(chosen) if chosen else None}

    def history(self, key):
        with self.store.connect() as db:
            return {
                "items": [
                    dict(json.loads(r["body"]), revision=r["revision"])
                    for r in db.execute(
                        "SELECT * FROM daily_logs WHERE commitment_id=? ORDER BY day DESC",
                        (key,),
                    )
                ]
            }

    def remove_capacity(self, key, revision):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision FROM entities WHERE id=? AND kind='capacity'", (key,)
            ).fetchone()
            if row is None:
                raise KeyError("Capacity not found")
            if row["revision"] != revision:
                raise ValueError("Capacity changed. Reload first.")
            if any(
                json.loads(r["body"]).get("capacityId") == key
                for r in db.execute("SELECT body FROM entities WHERE kind='commitment'")
            ):
                raise ValueError(
                    "Move the commitments to another capacity before removing this one"
                )
            db.execute("DELETE FROM entities WHERE id=?", (key,))
        return {"removed": True}

    def _removal_preview(self, db, kind, key):
        row = db.execute(
            "SELECT * FROM entities WHERE id=? AND kind=?", (key, kind)
        ).fetchone()
        if row is None:
            raise KeyError("Record not found; it may already have been removed")
        record = entity(row)
        members = (
            [row]
            if kind == "commitment"
            else db.execute(
                "SELECT * FROM entities WHERE kind='commitment' "
                "AND json_extract(body,'$.capacityId')=? ORDER BY id",
                (key,),
            ).fetchall()
        )
        commitments, history = [], []
        reminders = 0
        for member in members:
            item = entity(member)
            commitments.append(
                {"id": item["id"], "revision": item["revision"], "title": item["title"]}
            )
            history.extend(
                (item["id"], log["day"], log["revision"])
                for log in db.execute(
                    "SELECT day,revision FROM daily_logs WHERE commitment_id=? ORDER BY day",
                    (item["id"],),
                )
            )
            reminders += db.execute(
                "SELECT COUNT(*) FROM reminder_jobs WHERE commitment_id=? AND state IN ('ready','scheduled')",
                (item["id"],),
            ).fetchone()[0]
        review = {
            "id": key,
            "kind": kind,
            "title": record.get("title", record.get("name")),
            "revision": record["revision"],
            "commitments": commitments,
            "progressEntries": len(history),
            "reminders": reminders,
        }
        # Progress on habits does not increment the entity revision. Bind each
        # dated log too, so a reviewed deletion cannot erase a newer entry.
        token = hashlib.sha256(
            json.dumps([review, history], sort_keys=True).encode()
        ).hexdigest()
        return {**review, "previewToken": token}

    def removal_preview(self, kind, key):
        with self.store.connect() as db:
            db.execute("BEGIN")
            return self._removal_preview(db, kind, key)

    @staticmethod
    def _delete_commitment(db, key):
        # Keep delivery history, but no live reference to removed reminder jobs.
        db.execute(
            "UPDATE push_deliveries SET state=CASE WHEN state='pending' THEN 'cancelled' ELSE state END, "
            "reminder_id=NULL,reminder_revision=NULL,updated=? WHERE reminder_id IN "
            "(SELECT id FROM reminder_jobs WHERE commitment_id=?)",
            (time.time(), key),
        )
        # Foreign keys cascade daily logs and reminder jobs in this transaction.
        db.execute("DELETE FROM entities WHERE id=? AND kind='commitment'", (key,))

    def remove_reviewed(self, kind, key, body):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            review = self._removal_preview(db, kind, key)
            if review["previewToken"] != body.previewToken:
                raise ValueError(
                    "This record or its commitments/progress changed. Review the removal again."
                )
            members = review["commitments"]
            if kind == "capacity" and body.operation == "move":
                if body.targetCapacityId == key:
                    raise ValueError("Choose a different destination capacity")
                target = db.execute(
                    "SELECT revision FROM entities WHERE id=? AND kind='capacity'",
                    (body.targetCapacityId,),
                ).fetchone()
                if target is None:
                    raise KeyError("Destination capacity no longer exists")
                if target["revision"] != body.targetRevision:
                    raise ValueError(
                        "Destination capacity changed. Review the removal again."
                    )
                for member in members:
                    db.execute(
                        "UPDATE entities SET body=json_set(body,'$.capacityId',?),revision=revision+1,updated=? WHERE id=?",
                        (body.targetCapacityId, time.time(), member["id"]),
                    )
            elif kind == "capacity" and body.operation == "empty":
                if members:
                    raise ValueError("Choose to move or remove the linked commitments")
            else:
                for member in members:
                    self._delete_commitment(db, member["id"])
            if kind == "capacity":
                db.execute(
                    "DELETE FROM entities WHERE id=? AND kind='capacity'", (key,)
                )
            result = {
                "removed": True,
                "commitmentsMoved": len(members)
                if kind == "capacity" and body.operation == "move"
                else 0,
                "commitmentsRemoved": len(members)
                if kind == "commitment" or body.operation == "cascade"
                else 0,
            }
            db.execute(
                "INSERT INTO events(topic,payload,created) VALUES (?,?,?)",
                (
                    "commitment.removal",
                    json.dumps({"kind": kind, "id": key, **result}),
                    time.time(),
                ),
            )
        return result


def router(store):
    routes = APIRouter(prefix="/api")
    domain = Commitments(store)

    def call(fn, *args):
        try:
            return fn(*args)
        except ValidationError as error:
            raise HTTPException(422, str(error))
        except KeyError as error:
            raise HTTPException(404, str(error))
        except ValueError as error:
            raise HTTPException(409, str(error))

    @routes.get("/capacities")
    async def capacities():
        return {"items": store.entities("capacity")}

    @routes.post("/capacities")
    async def create_capacity(body: Capacity):
        return store.create("capacity", body.model_dump())

    @routes.patch("/capacities/{key}")
    async def edit_capacity(key: str, body: CapacityEdit):
        return call(
            store.update,
            key,
            "capacity",
            body.revision,
            body.model_dump(exclude={"revision"}),
        )

    @routes.delete("/capacities/{key}")
    async def remove_capacity(key: str, revision: int):
        return call(domain.remove_capacity, key, revision)

    @routes.get("/capacities/{key}/removal-preview")
    async def capacity_removal_preview(key: str):
        return call(domain.removal_preview, "capacity", key)

    @routes.post("/capacities/{key}/remove")
    async def capacity_remove(key: str, body: CapacityRemoval):
        return call(domain.remove_reviewed, "capacity", key, body)

    @routes.get("/commitments/{key}/removal-preview")
    async def commitment_removal_preview(key: str):
        return call(domain.removal_preview, "commitment", key)

    @routes.post("/commitments/{key}/remove")
    async def commitment_remove(key: str, body: Removal):
        return call(domain.remove_reviewed, "commitment", key, body)

    @routes.get("/commitments")
    async def commitments():
        with store.connect() as db:
            db.execute("BEGIN")
            cards = [
                entity(row)
                for row in db.execute(
                    "SELECT * FROM entities WHERE kind='commitment' ORDER BY updated DESC"
                )
            ]
            return {"items": cards, "boardOrders": read_orders(db, cards)}

    @routes.put("/commitments/order")
    async def reorder(body: MoveCard):
        return call(move_card, store, body)

    @routes.post("/commitments")
    async def create(body: Commitment):
        return call(domain.create, body)

    @routes.patch("/commitments/{key}")
    async def edit(key: str, body: CommitmentEdit):
        return call(domain.edit, key, body)

    @routes.post("/commitments/{key}/subtasks")
    async def change_subtask(key: str, body: SubtaskChange):
        return call(domain.subtask, key, body)

    @routes.get("/today")
    async def today(day: date | None = Query(default=None, alias="date")):
        return domain.today(day)

    @routes.put("/commitments/{key}/progress/{day}")
    async def progress(key: str, day: date, body: Progress):
        return call(domain.progress, key, day, body)

    @routes.get("/commitments/{key}/history")
    async def history(key: str):
        return domain.history(key)

    return routes
