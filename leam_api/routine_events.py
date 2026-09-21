"""Versioned owner-authenticated event inputs; deterministic in-app actions only.

The ingress receipt, provenance, cooldown state and notifications commit together.
All private state uses existing product entities/requests and is included in backups.
"""

import hashlib
import json
import time
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
)

NAME = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$"
Scalar = StrictBool | StrictInt | StrictFloat | StrictStr | None
RULE = "routine_event_rule"
EVENT = "routine_event_input"
NOTE = "routine_event_notification"


def encoded(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def scalar(value):
    if isinstance(value, str) and len(value) > 256:
        raise ValueError("Attribute strings are limited to 256 characters")
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and abs(value) > 9007199254740991
    ):
        raise ValueError(
            "Attribute integers must fit the exact JSON safe integer range"
        )
    return value


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Event(Input):
    version: Literal[1]
    requestId: uuid.UUID
    source: str = Field(pattern=NAME)
    type: str = Field(pattern=NAME)
    occurredAt: AwareDatetime
    attributes: dict[str, Scalar] = Field(default_factory=dict, max_length=16)

    @field_validator("version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("Supported event version is integer 1")
        return value

    @field_validator("occurredAt", mode="before")
    @classmethod
    def timestamp_string(cls, value):
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError("Use an ISO timestamp with timezone")
        return value

    @field_validator("attributes")
    @classmethod
    def attributes_bounded(cls, value):
        for key, item in value.items():
            if (
                not key
                or len(key) > 48
                or not key.isascii()
                or not all(c.isalnum() or c in "_.-" for c in key)
            ):
                raise ValueError(
                    "Attribute names must be 1–48 letters, numbers, dots, underscores or hyphens"
                )
            scalar(item)
        if len(encoded(value).encode()) > 8192:
            raise ValueError("Attributes exceed 8192 bytes")
        return value


class Match(Input):
    field: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,48}$")
    equals: Scalar

    @field_validator("equals")
    @classmethod
    def bounded(cls, value):
        return scalar(value)


class Rule(Input):
    title: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=2000)
    source: str = Field(pattern=NAME)
    eventType: str = Field(pattern=NAME)
    match: Match | None = None
    cooldownSeconds: Annotated[int, Field(strict=True, ge=0, le=86400)] = 60
    enabled: StrictBool = True
    action: Literal["notification"] = "notification"

    @field_validator("title", "message")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Enter text")
        return value.strip()


class Edit(Rule):
    revision: Annotated[int, Field(strict=True, ge=1)]


class EventInputs:
    def __init__(self, store, clock=None):
        self.store = store
        self.clock = clock or time.time

    @staticmethod
    def item(row):
        return {**json.loads(row["body"]), "id": row["id"], "revision": row["revision"]}

    @staticmethod
    def put(db, key, kind, revision, body, now):
        changed = db.execute(
            "INSERT INTO entities VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,body=excluded.body,updated=excluded.updated WHERE entities.kind=excluded.kind",
            (key, kind, revision, encoded(body), now),
        ).rowcount
        if not changed:
            raise HTTPException(409, "Identity belongs to another product record")

    def list(self, kind, *, limit=50, offset=0, waiting=False):
        with self.store.connect() as db:
            where = "kind=?" + (
                " AND json_extract(body,'$.dismissedAt') IS NULL" if waiting else ""
            )
            total = db.execute(
                f"SELECT count(*) FROM entities WHERE {where}", (kind,)
            ).fetchone()[0]
            rows = db.execute(
                f"SELECT * FROM entities WHERE {where} ORDER BY updated DESC,id DESC LIMIT ? OFFSET ?",
                (kind, limit, offset),
            ).fetchall()
            return {
                "items": [self.item(row) for row in rows],
                "total": total,
                "nextOffset": offset + limit if total > offset + limit else None,
            }

    def save(self, body, key=None):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = (
                db.execute(
                    "SELECT * FROM entities WHERE id=? AND kind=?", (key, RULE)
                ).fetchone()
                if key
                else None
            )
            if key and not old:
                raise HTTPException(404, "Event rule not found")
            if old and old["revision"] != body.revision:
                raise HTTPException(409, "Rule changed. Refresh before editing.")
            if (
                not old
                and db.execute(
                    "SELECT count(*) FROM entities WHERE kind=?", (RULE,)
                ).fetchone()[0]
                >= 50
            ):
                raise HTTPException(409, "Limit of 50 event rules reached")
            data = body.model_dump(exclude={"revision"})
            data["lastDeliveredAt"] = (
                json.loads(old["body"]).get("lastDeliveredAt") if old else None
            )
            key = key or str(uuid.uuid4())
            self.put(
                db, key, RULE, old["revision"] + 1 if old else 1, data, self.clock()
            )
            return self.item(
                db.execute("SELECT * FROM entities WHERE id=?", (key,)).fetchone()
            )

    def ingest(self, body):
        now = self.clock()
        data = body.model_dump(mode="json")
        fingerprint = hashlib.sha256(
            ("routine-event-v1:" + encoded(data)).encode()
        ).hexdigest()
        key = str(body.requestId)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM requests WHERE id=?", (key,)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise HTTPException(
                        409, "Request ID already belongs to different input"
                    )
                if old["state"] != "complete":
                    raise HTTPException(
                        409, "Input receipt is not complete; inspect before retrying"
                    )
                return json.loads(old["result"])
            age = now - body.occurredAt.timestamp()
            if not -300 <= age <= 86400:
                raise HTTPException(
                    422, "Event time must be within the past 24 hours or next 5 minutes"
                )
            if (
                db.execute(
                    "SELECT count(*) FROM entities WHERE kind=? AND updated>?",
                    (EVENT, now - 60),
                ).fetchone()[0]
                >= 60
            ):
                raise HTTPException(
                    429,
                    "Limit of 60 new inputs per minute reached; retry this same input later",
                )
            deliveries, suppressed = [], []
            for row in db.execute(
                "SELECT * FROM entities WHERE kind=? ORDER BY id", (RULE,)
            ).fetchall():
                rule = json.loads(row["body"])
                if (
                    not rule["enabled"]
                    or rule["source"] != body.source
                    or rule["eventType"] != body.type
                ):
                    continue
                match = rule.get("match")
                if match and (
                    match["field"] not in body.attributes
                    or encoded(body.attributes[match["field"]])
                    != encoded(match["equals"])
                ):
                    continue
                previous = rule.get("lastDeliveredAt")
                if previous is not None and now < previous + rule["cooldownSeconds"]:
                    suppressed.append(
                        {
                            "ruleId": row["id"],
                            "revision": row["revision"],
                            "reason": "cooldown",
                        }
                    )
                    continue
                waiting = db.execute(
                    "SELECT count(*) FROM entities WHERE kind=? AND json_extract(body,'$.dismissedAt') IS NULL",
                    (NOTE,),
                ).fetchone()[0]
                if waiting >= 1000:
                    raise HTTPException(
                        409,
                        "Dismiss waiting event notifications before accepting more matching inputs",
                    )
                note_id = str(uuid.uuid5(uuid.UUID(key), row["id"]))
                note = {
                    "version": 1,
                    "eventId": key,
                    "ruleId": row["id"],
                    "ruleRevision": row["revision"],
                    "title": rule["title"],
                    "message": rule["message"],
                    "createdAt": now,
                    "dismissedAt": None,
                    "source": body.source,
                    "eventType": body.type,
                }
                self.put(db, note_id, NOTE, 1, note, now)
                rule["lastDeliveredAt"] = now
                # Execution metadata does not change the configuration revision.
                self.put(db, row["id"], RULE, row["revision"], rule, now)
                deliveries.append(note_id)
            receipt = {
                "version": 1,
                "eventId": key,
                "receivedAt": now,
                "deliveries": deliveries,
                "suppressed": suppressed,
            }
            recorded = {
                **data,
                "receivedAt": now,
                "provenance": {
                    "channel": "owner-session",
                    "sourceClaim": "declared by authenticated owner; not independently verified",
                },
                "receipt": receipt,
            }
            self.put(db, key, EVENT, 1, recorded, now)
            db.execute(
                "INSERT INTO requests VALUES (?,?,'complete',?)",
                (key, fingerprint, encoded(receipt)),
            )
            db.execute(
                "INSERT INTO events(topic,payload,created) VALUES (?,?,?)",
                ("routine.event.accepted", encoded(receipt), now),
            )
            # Recent inspection data is bounded; durable receipts remain for replay protection.
            db.execute(
                "DELETE FROM entities WHERE kind=? AND id NOT IN (SELECT id FROM entities WHERE kind=? ORDER BY updated DESC,id DESC LIMIT 1000)",
                (EVENT, EVENT),
            )
            db.execute(
                "DELETE FROM entities WHERE kind=? AND json_extract(body,'$.dismissedAt') IS NOT NULL AND id NOT IN (SELECT id FROM entities WHERE kind=? AND json_extract(body,'$.dismissedAt') IS NOT NULL ORDER BY updated DESC,id DESC LIMIT 1000)",
                (NOTE, NOTE),
            )
            return receipt

    def dismiss(self, key):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM entities WHERE id=? AND kind=?", (key, NOTE)
            ).fetchone()
            if not row:
                raise HTTPException(404, "Event notification not found")
            body = json.loads(row["body"])
            if body["dismissedAt"] is None:
                body["dismissedAt"] = self.clock()
                self.put(db, key, NOTE, row["revision"] + 1, body, self.clock())
            return {"dismissed": True, "id": key}


def router(domain):
    routes = APIRouter(prefix="/api")

    @routes.get("/routine-events/capabilities")
    def capabilities():
        return {
            "versions": [1],
            "authentication": "owner-session-and-trusted-origin",
            "actions": ["notification"],
            "matching": "exact-source-type-and-optional-scalar-field",
            "limits": {
                "attributes": 16,
                "rules": 50,
                "newEventsPerMinute": 60,
                "retainedEvents": 1000,
                "maxAgeSeconds": 86400,
            },
            "sensors": False,
            "externalIngressToken": False,
        }

    @routes.get("/routine-event-rules")
    def rules():
        return domain.list(RULE, limit=50)

    @routes.post("/routine-event-rules")
    def create(body: Rule):
        return domain.save(body)

    @routes.put("/routine-event-rules/{key}")
    def update(key: uuid.UUID, body: Edit):
        return domain.save(body, str(key))

    @routes.post("/routine-events")
    def ingest(body: Event):
        return domain.ingest(body)

    @routes.get("/routine-events")
    def inputs(
        limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=1000)
    ):
        return domain.list(EVENT, limit=limit, offset=offset)

    @routes.get("/routine-event-notifications")
    def notifications(limit: int = Query(50, ge=1, le=100)):
        return domain.list(NOTE, limit=limit, waiting=True)

    @routes.post("/routine-event-notifications/{key}/dismiss")
    def dismiss(key: uuid.UUID):
        return domain.dismiss(str(key))

    return routes
