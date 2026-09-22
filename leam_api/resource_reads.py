"""Explicit acknowledgments for immutable private Resources; no content scanning."""

import json
import time

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

PREFIX = "resource-read:"


class ResourceRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(max_length=2000)

    @field_validator("ids")
    @classmethod
    def valid_ids(cls, value):
        from .artifacts import ID

        if len(set(value)) != len(value) or any(not ID.fullmatch(key) for key in value):
            raise ValueError("Use unique published resource IDs")
        return value


def states(db):
    return {
        row["key"][len(PREFIX) :]: json.loads(row["value"])
        for row in db.execute(
            "SELECT key,value FROM settings WHERE key >= ? AND key < ?",
            (PREFIX, "resource-read;"),
        )
    }


def status(db):
    # Covering key-index query: does not load/parse multi-MiB resource bodies.
    ids = [
        row["key"][9:]
        for row in db.execute(
            "SELECT key FROM settings WHERE key >= 'artifact:' AND key < 'artifact;' ORDER BY key"
        )
    ]
    read = states(db)
    unread = [key for key in ids if key not in read]
    return {"total": len(ids), "unreadCount": len(unread), "unreadIds": unread}


class ResourceReads:
    def __init__(self, store, clock=None):
        self.store = store
        self.clock = clock or time.time

    def status(self):
        with self.store.connect() as db:
            db.execute("BEGIN")
            return status(db)

    def decorate(self, items):
        with self.store.connect() as db:
            read = states(db)
        return [
            {**item, "unread": item["id"] not in read, "readAt": read.get(item["id"])}
            for item in items
        ]

    def mark(self, body):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = {
                row["key"][9:]
                for row in db.execute(
                    "SELECT key FROM settings WHERE key >= 'artifact:' AND key < 'artifact;'"
                )
            }
            if any(key not in existing for key in body.ids):
                raise HTTPException(
                    404, "A resource is no longer available; reload before marking read"
                )
            # Bound markers to the same bounded resource inventory, including
            # operator revocation; never mark unseen or future resources.
            for key in states(db):
                if key not in existing:
                    db.execute("DELETE FROM settings WHERE key=?", (PREFIX + key,))
            now = json.dumps(self.clock())
            db.executemany(
                "INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)",
                [(PREFIX + key, now) for key in body.ids],
            )
            return status(db)
