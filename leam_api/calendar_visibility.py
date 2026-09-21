"""Permanent local visibility for exact calendar entries, never provider edits."""

import hashlib
import json
import time

from fastapi import HTTPException
from pydantic import Field, StrictBool

from .commitments import Input

PREFIX = "agenda-calendar-visibility:"


def calendar_key(calendar_id, event_id):
    # Preserve the existing agenda source-key encoding and full provider identity.
    raw = json.dumps((calendar_id, event_id), ensure_ascii=False, separators=(",", ":"))
    return "calendar:" + hashlib.sha256(raw.encode()).hexdigest()


class VisibilityChange(Input):
    key: str = Field(pattern=r"^calendar:[0-9a-f]{64}$")
    revision: int = Field(ge=0)
    hidden: StrictBool


class CalendarVisibility:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def preferences(db):
        return {
            row["key"].removeprefix(PREFIX): json.loads(row["value"])
            for row in db.execute(
                "SELECT key,value FROM settings WHERE key LIKE ?", (PREFIX + "%",)
            )
        }

    @staticmethod
    def sources(db):
        items = {}
        for row in db.execute(
            "SELECT c.id,c.name,s.body FROM calendars c JOIN calendar_snapshots s ON s.calendar_id=c.id"
        ):
            for event in json.loads(row["body"]):
                key = calendar_key(row["id"], event["id"])
                items[key] = {
                    "key": key,
                    "calendarId": row["id"],
                    "eventId": event["id"],
                    "title": event["title"][:512],
                    "calendarName": row["name"][:512],
                }
        return items

    def listing(self, *, offset=0, limit=50):
        with self.store.connect() as db:
            db.execute("BEGIN")
            preferences = self.preferences(db)
            sources = self.sources(db)
        hidden = [
            {
                **saved,
                **sources.get(key, {}),
                "key": key,
                "sourceAvailable": key in sources,
            }
            for key, saved in preferences.items()
            if saved["hidden"]
        ]
        hidden.sort(key=lambda item: (-item["updatedAt"], item["key"]))
        page = hidden[offset : offset + limit]
        next_offset = offset + len(page)
        return {
            "items": page,
            "total": len(hidden),
            "nextOffset": next_offset if next_offset < len(hidden) else None,
        }

    def change(self, body):
        with self.store.connect() as db:
            # Source membership and optimistic revision share this write transaction.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value FROM settings WHERE key=?", (PREFIX + body.key,)
            ).fetchone()
            previous = json.loads(row[0]) if row else None
            if body.revision != (previous or {}).get("revision", 0):
                raise HTTPException(
                    409, "Calendar visibility changed; refresh before editing"
                )
            source = self.sources(db).get(body.key)
            if (body.hidden and source is None) or (
                source is None and previous is None
            ):
                raise HTTPException(
                    404, "Calendar entry is no longer in the saved snapshot"
                )
            record = {
                **(source or previous),
                "hidden": body.hidden,
                "revision": body.revision + 1,
                "updatedAt": time.time(),
            }
            db.execute(
                "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (PREFIX + body.key, json.dumps(record)),
            )
        return {"key": body.key, "hidden": body.hidden, "revision": record["revision"]}
