"""Local source-keyed suppression. No mail provider writes or transcript copying."""

import hashlib
import json
import time
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import Field, StrictBool, model_validator

from .agenda import source_key
from .commitments import Input

PREFIX = "inbox-removed:"
RECEIPT = "inbox-remove-receipt:"
MAX_REMOVALS = 10000
MAX_RECEIPTS = 10000


def removed(db, key):
    row = db.execute(
        "SELECT value FROM settings WHERE key=?", (PREFIX + key,)
    ).fetchone()
    return json.loads(row[0]) if row else None


class RemovalItem(Input):
    source: Literal["leam", "mail"]
    id: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def valid_identity(self):
        import re

        if self.source == "leam":
            if str(UUID(self.id)) != self.id:
                raise ValueError("Use the canonical Inbox note ID")
        elif not re.fullmatch(r"email:[0-9a-f]{64}", self.id):
            raise ValueError("Use the canonical account/message source key")
        return self

    def key(self):
        return "leam:" + self.id if self.source == "leam" else self.id


class RemoveItems(Input):
    requestId: UUID
    confirmed: StrictBool
    items: list[RemovalItem] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique(self):
        if len({item.key() for item in self.items}) != len(self.items):
            raise ValueError("Duplicate removal identities")
        return self


class InboxRemoval:
    def __init__(self, store, emails, clock=time.time):
        self.store, self.emails, self.clock = store, emails, clock

    def remove(self, body):
        if not body.confirmed:
            raise HTTPException(422, "Confirm removal from Leam Inbox only")
        keys = sorted(item.key() for item in body.items)
        fingerprint = hashlib.sha256(json.dumps(keys).encode()).hexdigest()
        receipt_key = RECEIPT + str(body.requestId)
        previous = self.store.get(receipt_key)
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise HTTPException(
                    409, "Removal request ID has different selected items"
                )
            return previous
        # Existing snapshot reads only. No refresh, provider API or model request.
        current_mail = (
            {
                source_key("email", item["accountId"], item["id"])
                for item in self.emails.overview()["items"]
            }
            if any(item.source == "mail" for item in body.items)
            else set()
        )
        from .inbox import PREFIX as NOTES
        from .inbox import Inbox

        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT value FROM settings WHERE key=?", (receipt_key,)
            ).fetchone()
            if previous:
                receipt = json.loads(previous[0])
                if receipt["fingerprint"] != fingerprint:
                    raise HTTPException(
                        409, "Removal request ID has different selected items"
                    )
                return receipt
            new = []
            for item in body.items:
                if removed(db, item.key()):
                    continue
                if item.source == "mail":
                    if item.id not in current_mail:
                        raise HTTPException(
                            409,
                            "Saved mail changed or is unavailable. Refresh the Inbox.",
                        )
                    mark = {"removedAt": self.clock()}
                else:
                    row = db.execute(
                        "SELECT value FROM settings WHERE key=?", (NOTES + item.id,)
                    ).fetchone()
                    if row is None:
                        raise HTTPException(
                            409,
                            "An Inbox note is unavailable. Refresh before removing.",
                        )
                    note = json.loads(row[0])
                    mark = {
                        "removedAt": self.clock(),
                        "fingerprint": note["fingerprint"],
                    }
                new.append((item, mark))
            count = db.execute(
                "SELECT count(*) FROM settings WHERE substr(key,1,?)=?",
                (len(PREFIX), PREFIX),
            ).fetchone()[0]
            receipts = db.execute(
                "SELECT count(*) FROM settings WHERE substr(key,1,?)=?",
                (len(RECEIPT), RECEIPT),
            ).fetchone()[0]
            if count + len(new) > MAX_REMOVALS or receipts >= MAX_RECEIPTS:
                raise HTTPException(
                    429,
                    "Local Inbox removal capacity reached; existing removals were preserved",
                )
            for item, mark in new:
                db.execute(
                    "INSERT INTO settings VALUES (?,?)",
                    (PREFIX + item.key(), json.dumps(mark)),
                )
                if item.source == "leam":
                    db.execute("DELETE FROM settings WHERE key=?", (NOTES + item.id,))
            receipt = {
                "requestId": str(body.requestId),
                "fingerprint": fingerprint,
                "removedKeys": keys,
                "newlyRemoved": len(new),
                "scope": "leam_inbox_only",
                "providerChanged": False,
                "removedAt": self.clock(),
                "notes": Inbox._status(db),
            }
            db.execute(
                "INSERT INTO settings VALUES (?,?)", (receipt_key, json.dumps(receipt))
            )
        return receipt


def router(store, emails):
    routes = APIRouter(prefix="/api/inbox")
    service = InboxRemoval(store, emails)

    @routes.post("/remove")
    async def remove(body: RemoveItems):
        return service.remove(body)

    return routes
