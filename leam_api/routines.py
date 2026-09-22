"""Local wall-clock routines. Receipt, notification and event commit together."""

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, field_validator

from .maintenance import work_admission
from .restore_automation import state as automation_state, MESSAGE as AUTOMATION_PAUSED
from .commitments import Commitment, Input
from .reminders import due_at


class Routine(Input):
    title: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=2000)
    time: str = Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    timezone: str = "Europe/London"
    days: list[int] = Field(
        default_factory=lambda: list(range(7)), min_length=1, max_length=7
    )
    enabled: bool = True
    action: Literal["notification"] = "notification"

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        return Commitment.valid_zone(value)

    @field_validator("days")
    @classmethod
    def valid_days(cls, value):
        if any(day < 0 or day > 6 for day in value) or len(set(value)) != len(value):
            raise ValueError("Choose unique weekdays, Monday=0 through Sunday=6")
        return sorted(value)


class Edit(Routine):
    revision: int = Field(ge=1)


class Run(Input):
    revision: int = Field(ge=1)
    requestId: uuid.UUID


def occurrence(body, now, *, previous=False):
    zone = ZoneInfo(body["timezone"])
    today = datetime.fromtimestamp(now, zone).date()
    for offset in range(9):
        day = today + timedelta(days=-offset if previous else offset)
        if day.weekday() not in body["days"]:
            continue
        due = due_at(day.isoformat(), body["time"], zone)
        if (previous and due <= now) or (not previous and due > now):
            return due
    raise ValueError("Could not resolve next routine occurrence")


class Routines:
    def __init__(self, store, clock=None):
        self.store = store
        self.clock = clock or time.time
        self.last_check = None
        self.last_error = None
        with store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS routines (
                    id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                    body TEXT NOT NULL, next_due REAL, created REAL NOT NULL, updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS routine_runs (
                    id TEXT PRIMARY KEY, routine_id TEXT NOT NULL REFERENCES routines(id),
                    revision INTEGER NOT NULL, trigger TEXT NOT NULL, due REAL NOT NULL,
                    state TEXT NOT NULL, title TEXT NOT NULL, message TEXT NOT NULL,
                    created REAL NOT NULL, dismissed REAL
                );
                CREATE INDEX IF NOT EXISTS routine_runs_created ON routine_runs(created DESC);
            """
            )

    @staticmethod
    def item(row):
        return {
            **json.loads(row["body"]),
            "id": row["id"],
            "revision": row["revision"],
            "nextDueAt": row["next_due"],
        }

    @staticmethod
    def receipt(row):
        return {
            "id": row["id"],
            "routineId": row["routine_id"],
            "revision": row["revision"],
            "trigger": row["trigger"],
            "dueAt": row["due"],
            "state": row["state"],
            "title": row["title"],
            "message": row["message"],
            "createdAt": row["created"],
            "dismissedAt": row["dismissed"],
        }

    def list(self):
        with self.store.connect() as db:
            return {
                "items": [
                    self.item(row)
                    for row in db.execute("SELECT * FROM routines ORDER BY created,id")
                ]
            }

    def save(self, body, key=None):
        now = self.clock()
        data = body.model_dump(exclude={"revision"})
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = (
                db.execute("SELECT * FROM routines WHERE id=?", (key,)).fetchone()
                if key
                else None
            )
            if key and not old:
                raise KeyError("Routine not found")
            if old and old["revision"] != body.revision:
                raise ValueError("Routine changed on another device. Refresh first.")
            if (
                not old
                and db.execute("SELECT count(*) FROM routines").fetchone()[0] >= 100
            ):
                raise ValueError("Limit of 100 routines reached")
            key = key or str(uuid.uuid4())
            revision = old["revision"] + 1 if old else 1
            # Every edit starts a new schedule after save; missed old work is not replayed.
            due = occurrence(data, now) if data["enabled"] else None
            db.execute(
                "INSERT INTO routines VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,body=excluded.body,next_due=excluded.next_due,updated=excluded.updated",
                (
                    key,
                    revision,
                    json.dumps(data),
                    due,
                    old["created"] if old else now,
                    now,
                ),
            )
            return self.item(
                db.execute("SELECT * FROM routines WHERE id=?", (key,)).fetchone()
            )

    def _deliver(self, db, row, key, trigger, due, now, state="delivered"):
        body = json.loads(row["body"])
        inserted = db.execute(
            "INSERT OR IGNORE INTO routine_runs VALUES (?,?,?,?,?,?,?,?,?,NULL)",
            (
                key,
                row["id"],
                row["revision"],
                trigger,
                due,
                state,
                body["title"],
                body["message"],
                now,
            ),
        ).rowcount
        if inserted and state == "delivered":
            db.execute(
                "INSERT INTO events(topic,payload,created) VALUES (?,?,?)",
                (
                    "routine.notification.ready",
                    json.dumps(
                        {
                            "version": 1,
                            "id": key,
                            "routineId": row["id"],
                            "revision": row["revision"],
                            "occurredAt": due,
                            "trigger": trigger,
                        }
                    ),
                    now,
                ),
            )
        return inserted

    def run_now(self, key, body):
        now = self.clock()
        request_id = str(body.requestId)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if automation_state(db)["held"]:
                raise ValueError(AUTOMATION_PAUSED)
            old = db.execute(
                "SELECT * FROM routine_runs WHERE id=?", (request_id,)
            ).fetchone()
            if old:
                if (
                    old["routine_id"] != key
                    or old["revision"] != body.revision
                    or old["trigger"] != "manual"
                ):
                    raise ValueError(
                        "Request identity already belongs to another operation"
                    )
                return self.receipt(old)
            row = db.execute("SELECT * FROM routines WHERE id=?", (key,)).fetchone()
            if not row:
                raise KeyError("Routine not found")
            if row["revision"] != body.revision:
                raise ValueError("Routine changed on another device. Refresh first.")
            self._deliver(db, row, request_id, "manual", now, now)
            return self.receipt(
                db.execute(
                    "SELECT * FROM routine_runs WHERE id=?", (request_id,)
                ).fetchone()
            )

    def tick(self):
        now = self.clock()
        delivered = 0
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            automation = automation_state(db)
            if automation["held"]:
                self.last_check = now
                self.last_error = None
                return {"delivered": 0, "checkedAt": now, "automationHeld": True}
            for row in db.execute(
                "SELECT * FROM routines WHERE next_due<=? ORDER BY next_due", (now,)
            ).fetchall():
                body = json.loads(row["body"])
                latest = max(row["next_due"], occurrence(body, now, previous=True))
                # Coalesce downtime to the latest occurrence. Expire anything >24h old.
                state = "delivered" if now - latest <= 86400 else "expired"
                if automation["cutoff"] is not None and latest <= automation["cutoff"]:
                    state = "expired"
                key = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"leam-routine:{row['id']}:{row['revision']}:{latest}",
                    )
                )
                inserted = self._deliver(db, row, key, "schedule", latest, now, state)
                delivered += inserted if state == "delivered" else 0
                db.execute(
                    "UPDATE routines SET next_due=? WHERE id=?",
                    (occurrence(body, now), row["id"]),
                )
        self.last_check = now
        self.last_error = None
        return {"delivered": delivered, "checkedAt": now}

    async def run(self):
        while True:
            try:
                with work_admission(self.store):
                    self.tick()
            except Exception as error:
                self.last_error = type(error).__name__
            await asyncio.sleep(15)

    def history(self, limit=50, before=None, notifications=False):
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT rowid AS cursor,* FROM routine_runs WHERE (? IS NULL OR rowid<?) AND (?=0 OR (state='delivered' AND dismissed IS NULL)) ORDER BY rowid DESC LIMIT ?",
                (before, before, int(notifications), limit + 1),
            ).fetchall()
        return {
            "items": [self.receipt(row) for row in rows[:limit]],
            "nextCursor": rows[limit - 1]["cursor"] if len(rows) > limit else None,
        }

    def dismiss(self, key):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM routine_runs WHERE id=?", (key,)).fetchone()
            if not row:
                raise KeyError("Notification not found")
            db.execute(
                "UPDATE routine_runs SET dismissed=COALESCE(dismissed,?) WHERE id=?",
                (self.clock(), key),
            )
        return {"id": key, "dismissed": True}


def router(domain):
    routes = APIRouter(prefix="/api/routines")

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @routes.get("")
    async def listing():
        return domain.list()

    @routes.post("")
    async def create(body: Routine):
        return call(domain.save, body)

    @routes.get("/status")
    async def status():
        with domain.store.connect() as db:
            automation = automation_state(db)
        return {
            "automationHeld": automation["held"],
            "automationInvalid": automation["invalid"],
            "lastCheck": domain.last_check,
            "error": domain.last_error,
            "healthy": domain.last_error is None
            and domain.last_check is not None
            and domain.clock() - domain.last_check < 45,
            "delivery": "in-app",
        }

    @routes.post("/check")
    async def check():
        try:
            return domain.tick()
        except Exception as error:
            domain.last_error = type(error).__name__
            raise HTTPException(
                503, "Routine scheduler could not complete; saved work will retry"
            ) from error

    @routes.get("/runs")
    async def history(
        limit: int = Query(50, ge=1, le=100), before: int | None = Query(None, ge=1)
    ):
        return domain.history(limit, before)

    @routes.get("/notifications")
    async def notifications(
        limit: int = Query(50, ge=1, le=100), before: int | None = Query(None, ge=1)
    ):
        return domain.history(limit, before, notifications=True)

    @routes.post("/notifications/{key}/dismiss")
    async def dismiss(key: str):
        return call(domain.dismiss, key)

    @routes.put("/{key}")
    async def edit(key: str, body: Edit):
        return call(domain.save, body, key)

    @routes.post("/{key}/run")
    async def run_now(key: str, body: Run):
        return call(domain.run_now, key, body)

    return routes
