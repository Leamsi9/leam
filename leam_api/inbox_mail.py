"""Local Inbox read markers only; Gmail messages/labels are never mutated."""

import json
import time
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, StrictBool

from .agenda import source_key
from .commitments import Input

KEY = "inbox.mail-read.v1"
MAX_MARKERS = 5000


class MailRead(Input):
    keys: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        min_length=1, max_length=100
    )
    read: StrictBool = True


class InboxMail:
    def __init__(self, store, emails, clock=time.time):
        self.store, self.emails, self.clock = store, emails, clock

    def current(self):
        return {
            source_key("email", item["accountId"], item["id"])
            for item in self.emails.overview()["items"]
        }

    @staticmethod
    def markers(db):
        row = db.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
        return json.loads(row[0]) if row else {}

    def status(self, keys=None):
        # Explicit display keys can belong to an older Today snapshot. Metadata
        # reads must retain its tombstones even when a fresh mail sync omits it.
        current = self.current() if keys is None else set(keys)
        with self.store.connect() as db:
            markers = self.markers(db)
            from .inbox_removal import removed

            removed_keys = sorted(key for key in current if removed(db, key))
        return {
            "readKeys": sorted(current.intersection(markers)),
            "removedKeys": removed_keys,
            "scope": "leam_only",
        }

    def mark(self, body):
        wanted = set(body.keys)
        if len(wanted) != len(body.keys) or not wanted <= self.current():
            raise HTTPException(
                409, "Saved mail changed or is unavailable. Refresh the Inbox."
            )
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            markers = self.markers(db)
            for key in wanted:
                if body.read:
                    markers.setdefault(key, self.clock())
                else:
                    markers.pop(key, None)
            if len(markers) > MAX_MARKERS:
                # Never silently reclassify old read states just to make room.
                raise HTTPException(429, "Local mail read-marker capacity reached")
            db.execute(
                "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (KEY, json.dumps(markers)),
            )
        return {"keys": body.keys, "read": body.read, "scope": "leam_only"}


def router(store, emails):
    routes = APIRouter(prefix="/api/inbox-mail")
    service = InboxMail(store, emails)

    @routes.get("/read")
    async def status(
        keys: Annotated[
            list[Annotated[str, Field(pattern=r"^email:[0-9a-f]{64}$")]] | None,
            Query(max_length=100),
        ] = None,
    ):
        return service.status(keys)

    @routes.post("/read")
    async def mark(body: MailRead):
        return service.mark(body)

    return routes
