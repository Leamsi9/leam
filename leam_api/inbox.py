"""Private action notes with immutable identity and snapshot-aware read markers."""

import hashlib
import json
import time
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, model_validator

from .commitments import Input
from .resource_links import ResourceLinks, Target

PREFIX = "action-inbox:"
SEQUENCE = "action-inbox.sequence"
MAX_ITEMS = 1000


class InboxLink(Input):
    type: Literal["resource", "feature", "commitment", "proposal"]
    id: str = Field(min_length=1, max_length=96)

    @model_validator(mode="after")
    def identity(self):
        if self.type == "resource":
            from .artifacts import ID

            if not ID.fullmatch(self.id):
                raise ValueError("Invalid resource identity")
        else:
            self.id = Target(targetType=self.type, targetId=self.id).targetId
        return self


class InboxCreate(Input):
    requestId: UUID
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=8192)
    links: list[InboxLink] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def bounded(self):
        if not self.subject.strip() or not self.body.strip():
            raise ValueError("Subject and body must contain text")
        if len(self.body.encode("utf-8")) > 16384:
            raise ValueError("Inbox body exceeds 16 KiB")
        if len({(link.type, link.id) for link in self.links}) != len(self.links):
            raise ValueError("Duplicate inbox links")
        return self


class InboxQuery(Input):
    id: UUID | None = None
    before: int | None = Field(default=None, ge=1)
    limit: int = Field(default=30, ge=1, le=50)


class ReadAll(Input):
    throughSequence: int = Field(ge=0, strict=True)


class Inbox:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock

    @staticmethod
    def _load(db, identity):
        row = db.execute(
            "SELECT value FROM settings WHERE key=?", (PREFIX + str(identity),)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Inbox item not found")
        return json.loads(row[0])

    @staticmethod
    def _links(db, links, *, require=False):
        items = []
        for link in links:
            if link["type"] == "resource":
                try:
                    item = ResourceLinks.resource(db, link["id"])
                    item = {
                        "available": True,
                        "title": item["title"],
                        "url": item["url"],
                    }
                except HTTPException as error:
                    if error.status_code != 404:
                        raise
                    item = {"available": False, "title": None, "url": None}
            else:
                item = ResourceLinks.target(db, link["type"], link["id"])
                location = item.get("location")
                item = {
                    "available": item["available"],
                    "title": item.get("title"),
                    "url": "/?view=" + location if location else None,
                }
            if require and not item["available"]:
                raise HTTPException(404, "An inbox link target is unavailable")
            items.append({**link, **item})
        return items

    def _public(self, db, value, *, detail=False):
        item = {
            key: value[key]
            for key in ("id", "sequence", "subject", "createdAt", "origin", "readAt")
        }
        item.update(
            unread=value["readAt"] is None,
            url="/?view=today&inboxItem=" + value["id"] + "#today/inbox",
            links=self._links(db, value["links"]),
        )
        if detail:
            item["body"] = value["body"]
            item["referenceData"] = True
        else:
            item["preview"] = value["body"][:180]
        return item

    def _create(self, db, body, *, origin, require_links=True):
        if origin not in {"user", "companion", "automation"}:
            raise ValueError("Inbox origin must be assigned by a trusted caller")
        data = body.model_dump(mode="json")
        fingerprint = hashlib.sha256(
            json.dumps({"input": data, "origin": origin}, sort_keys=True).encode()
        ).hexdigest()
        identity = str(body.requestId)
        key = PREFIX + identity
        from .inbox_removal import removed

        tombstone = removed(db, "leam:" + identity)
        if tombstone:
            if tombstone["fingerprint"] != fingerprint:
                raise HTTPException(409, "Request ID belongs to a removed Inbox item")
            return {
                "state": "removed",
                "item": {"id": identity, "removed": True},
                "url": None,
            }
        saved = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if saved:
            value = json.loads(saved[0])
            if value["fingerprint"] != fingerprint:
                raise HTTPException(
                    409, "Request ID already belongs to a different inbox item"
                )
            return {
                "state": "saved",
                "item": self._public(db, value),
                "url": "/?view=today&inboxItem=" + identity + "#today/inbox",
            }
        self._links(db, data["links"], require=require_links)
        count = db.execute(
            "SELECT count(*) FROM settings WHERE substr(key,1,?)=?",
            (len(PREFIX), PREFIX),
        ).fetchone()[0]
        if count >= MAX_ITEMS:
            raise HTTPException(429, "Inbox capacity reached; no item was created")
        row = db.execute(
            "SELECT value FROM settings WHERE key=?", (SEQUENCE,)
        ).fetchone()
        sequence = (json.loads(row[0]) if row else 0) + 1
        value = {
            "id": identity,
            "sequence": sequence,
            "subject": body.subject,
            "body": body.body,
            "links": data["links"],
            "origin": origin,
            "createdAt": self.clock(),
            "readAt": None,
            "fingerprint": fingerprint,
        }
        db.execute("INSERT INTO settings VALUES (?,?)", (key, json.dumps(value)))
        db.execute(
            "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (SEQUENCE, json.dumps(sequence)),
        )
        return {
            "state": "saved",
            "item": self._public(db, value),
            "url": "/?view=today&inboxItem=" + identity + "#today/inbox",
        }

    def create(self, body, *, origin):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._create(db, body, origin=origin)

    def emit_automation(self, db, *, event_id, subject, body, links=()):
        """Trusted domain transaction only; not an HTTP/model-controlled actor field.

        Call after a confirmed automated change in the SAME transaction. This
        primitive alone is not a subscription to arbitrary store events.
        """
        request = InboxCreate(
            requestId=uuid5(NAMESPACE_URL, "leam:action-inbox:" + event_id),
            subject=subject,
            body=body,
            links=list(links),
        )
        return self._create(db, request, origin="automation")

    @staticmethod
    def _status(db):
        row = db.execute(
            "SELECT count(*),COALESCE(sum(json_extract(value,'$.readAt') IS NULL),0),COALESCE(max(json_extract(value,'$.sequence')),0) FROM settings WHERE substr(key,1,?)=?",
            (len(PREFIX), PREFIX),
        ).fetchone()
        watermark = db.execute(
            "SELECT value FROM settings WHERE key=?", (SEQUENCE,)
        ).fetchone()
        return {
            "total": row[0],
            "unreadCount": row[1],
            "throughSequence": max(
                row[2], json.loads(watermark[0]) if watermark else 0
            ),
        }

    def status(self):
        from .inbox_events import InboxEvents

        with self.store.connect() as db:
            return {**self._status(db), "automationDelivery": InboxEvents.status(db)}

    def list(self, before=None, limit=30):
        with self.store.connect() as db:
            db.execute("BEGIN")  # Items and read-all watermark share one snapshot.
            rows = db.execute(
                "SELECT value FROM settings WHERE substr(key,1,?)=? AND (? IS NULL OR json_extract(value,'$.sequence')<?) ORDER BY json_extract(value,'$.sequence') DESC LIMIT ?",
                (len(PREFIX), PREFIX, before, before, limit + 1),
            ).fetchall()
            values = [json.loads(row[0]) for row in rows[:limit]]
            return {
                "items": [self._public(db, item) for item in values],
                "nextCursor": values[-1]["sequence"] if len(rows) > limit else None,
                **self._status(db),
            }

    def get(self, identity):
        with self.store.connect() as db:
            return self._public(db, self._load(db, identity), detail=True)

    def read(self, identity, *, read=True):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            value = self._load(db, identity)
            if (value["readAt"] is None) == read:
                value["readAt"] = self.clock() if read else None
                db.execute(
                    "UPDATE settings SET value=? WHERE key=?",
                    (json.dumps(value), PREFIX + str(identity)),
                )
            return {"item": self._public(db, value, detail=True), **self._status(db)}

    def read_all(self, through):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._status(db)
            if through > current["throughSequence"]:
                raise HTTPException(
                    409, "Inbox snapshot is ahead of saved items; refresh"
                )
            db.execute(
                "UPDATE settings SET value=json_set(value,'$.readAt',?) WHERE substr(key,1,?)=? AND json_extract(value,'$.readAt') IS NULL AND json_extract(value,'$.sequence')<=?",
                (self.clock(), len(PREFIX), PREFIX, through),
            )
            return self._status(db)


def router(inbox):
    routes = APIRouter(prefix="/api/inbox")

    @routes.get("")
    async def listing(
        before: int | None = Query(None, ge=1), limit: int = Query(30, ge=1, le=50)
    ):
        return inbox.list(before, limit)

    @routes.get("/status")
    async def status():
        return inbox.status()

    @routes.post("")
    async def create(body: InboxCreate):
        return inbox.create(body, origin="user")

    @routes.post("/read-all")
    async def read_all(body: ReadAll):
        return inbox.read_all(body.throughSequence)

    @routes.post("/automation/{event_id}/retry")
    async def retry_delivery(event_id: int):
        from .inbox_events import InboxEvents

        return InboxEvents(inbox.store).retry(event_id)

    @routes.get("/{identity}")
    async def detail(identity: UUID):
        return inbox.get(identity)

    @routes.post("/{identity}/read")
    async def read(identity: UUID):
        return inbox.read(identity)

    @routes.post("/{identity}/unread")
    async def unread(identity: UUID):
        return inbox.read(identity, read=False)

    return routes
