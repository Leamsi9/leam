"""Durable date-based reminders. Browser timers are never authoritative."""

import asyncio
import json
import math
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import Field

from .restore_automation import state as automation_state
from .commitments import Commitments, Input, Progress, entity


def due_at(day, clock_time, zone):
    naive = datetime.fromisoformat(day + "T" + clock_time)
    # First occurrence in a repeated hour; first valid minute after a DST gap.
    for minutes in range(181):
        local = naive + timedelta(minutes=minutes)
        aware = local.replace(tzinfo=zone, fold=0)
        if (
            datetime.fromtimestamp(aware.timestamp(), zone).replace(tzinfo=None)
            == local
        ):
            return aware.timestamp()
    raise ValueError("No valid reminder time in this timezone transition")


class Revision(Input):
    revision: int = Field(ge=1)


class Snooze(Revision):
    minutes: int = Field(default=10, ge=1, le=1440)


class Scheduler:
    def __init__(self, store, clock=None):
        self.store = store
        self.clock = clock or time.time
        self.last_check = None
        self.last_error = None

    async def run(self):
        while True:
            try:
                self.tick()
                self.last_error = None
            except Exception as error:
                self.last_error = type(error).__name__
            await asyncio.sleep(15)

    def tick(self):
        now = self.clock()
        ready = 0
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            automation = automation_state(db)
            if automation["held"]:
                self.last_check = now
                self.last_error = None
                return {"newlyReady": 0, "checkedAt": now, "automationHeld": True}
            cutoff = automation["cutoff"]
            commitments = {
                r["id"]: json.loads(r["body"])
                for r in db.execute(
                    "SELECT id,body FROM entities WHERE kind='commitment'"
                )
            }
            for row in db.execute(
                "SELECT * FROM reminder_jobs WHERE state IN ('ready','scheduled')"
            ).fetchall():
                item = commitments.get(row["commitment_id"])
                invalid = (
                    not item
                    or item.get("status") != "active"
                    or not item.get("reminderTime")
                    or (item.get("startDate") and row["day"] < item["startDate"])
                    or (item.get("endDate") and row["day"] > item["endDate"])
                )
                log = db.execute(
                    "SELECT body FROM daily_logs WHERE commitment_id=? AND day=?",
                    (row["commitment_id"], row["day"]),
                ).fetchone()
                if invalid or (log and json.loads(log["body"]).get("done")):
                    db.execute(
                        "UPDATE reminder_jobs SET state=?,revision=revision+1,updated=? WHERE id=?",
                        (
                            (
                                "completed"
                                if log and json.loads(log["body"]).get("done")
                                else "cancelled"
                            ),
                            now,
                            row["id"],
                        ),
                    )
            for key, item in commitments.items():
                if item.get("status") != "active" or not item.get("reminderTime"):
                    continue
                zone = ZoneInfo(item.get("timezone", "Europe/London"))
                day = datetime.fromtimestamp(now, zone).date().isoformat()
                if (item.get("startDate") and day < item["startDate"]) or (
                    item.get("endDate") and day > item["endDate"]
                ):
                    continue
                log = db.execute(
                    "SELECT body FROM daily_logs WHERE commitment_id=? AND day=?",
                    (key, day),
                ).fetchone()
                if log and json.loads(log["body"]).get("done"):
                    continue
                due = due_at(day, item["reminderTime"], zone)
                notification_id = str(
                    uuid.uuid5(uuid.NAMESPACE_URL, "leam-reminder:" + key + ":" + day)
                )
                existing = db.execute(
                    "SELECT * FROM reminder_jobs WHERE id=?", (notification_id,)
                ).fetchone()
                if existing is None:
                    snoozed = False
                    state = "scheduled"
                    source = db.execute(
                        "SELECT raw FROM import_records WHERE entity_id=? AND kind='commitment'",
                        (key,),
                    ).fetchone()
                    if source:
                        raw = json.loads(source["raw"])
                        until = (raw.get("snoozes") or {}).get(day)
                        if (
                            type(until) in [int, float]
                            and math.isfinite(until)
                            and until >= 0
                        ):
                            due = until / 1000
                            snoozed = True
                        elif (raw.get("lastReminder") or {}).get(day):
                            state = "dismissed"
                    if cutoff is not None and due <= cutoff:
                        state = "expired"
                    db.execute(
                        "INSERT INTO reminder_jobs VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            notification_id,
                            key,
                            day,
                            due,
                            state,
                            1,
                            int(snoozed),
                            now,
                            now,
                        ),
                    )
                elif (
                    existing["state"] == "expired"
                    and cutoff is not None
                    and due > max(cutoff, now)
                    and due != existing["due"]
                ):
                    # An explicit later reminder-time edit can schedule future work.
                    db.execute(
                        "UPDATE reminder_jobs SET state='scheduled',due=?,snoozed=0,revision=revision+1,updated=? WHERE id=?",
                        (due, now, notification_id),
                    )
                elif existing["state"] in ["cancelled", "completed"]:
                    db.execute(
                        "UPDATE reminder_jobs SET state='scheduled',due=?,revision=revision+1,updated=? WHERE id=?",
                        (
                            existing["due"] if existing["snoozed"] else due,
                            now,
                            notification_id,
                        ),
                    )
                elif (
                    not existing["snoozed"]
                    and existing["state"] in ["scheduled", "ready"]
                    and existing["due"] != due
                ):
                    db.execute(
                        "UPDATE reminder_jobs SET due=?,state=?,revision=revision+1,updated=? WHERE id=?",
                        (
                            due,
                            "scheduled" if due > now else existing["state"],
                            now,
                            notification_id,
                        ),
                    )
            for row in db.execute(
                "SELECT * FROM reminder_jobs WHERE state='scheduled' AND due<=?", (now,)
            ).fetchall():
                item = commitments[row["commitment_id"]]
                today = (
                    datetime.fromtimestamp(
                        now, ZoneInfo(item.get("timezone", "Europe/London"))
                    )
                    .date()
                    .isoformat()
                )
                state = "ready" if row["day"] == today or row["snoozed"] else "expired"
                if cutoff is not None and row["due"] <= cutoff:
                    state = "expired"
                db.execute(
                    "UPDATE reminder_jobs SET state=?,revision=revision+1,updated=? WHERE id=?",
                    (state, now, row["id"]),
                )
                if state == "ready":
                    db.execute(
                        "INSERT INTO events(topic,payload,created) VALUES (?,?,?)",
                        (
                            "notification.ready",
                            json.dumps(
                                {
                                    "id": row["id"],
                                    "commitmentId": row["commitment_id"],
                                    "date": row["day"],
                                }
                            ),
                            now,
                        ),
                    )
                    ready += 1
        self.last_check = now
        self.last_error = None
        return {"newlyReady": ready, "checkedAt": now}

    def list(self):
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT n.*,e.body FROM reminder_jobs n JOIN entities e ON e.id=n.commitment_id WHERE n.state='ready' ORDER BY n.due DESC"
            ).fetchall()
            return {
                "items": [
                    {
                        "id": r["id"],
                        "commitmentId": r["commitment_id"],
                        "title": json.loads(r["body"])["title"],
                        "date": r["day"],
                        "dueAt": r["due"],
                        "revision": r["revision"],
                        "state": r["state"],
                    }
                    for r in rows
                ]
            }

    def complete(self, key, revision):
        with self.store.connect() as db:
            reminder = db.execute(
                "SELECT * FROM reminder_jobs WHERE id=?", (key,)
            ).fetchone()
            if reminder is None:
                raise KeyError("Reminder not found")
            row = db.execute(
                "SELECT * FROM entities WHERE id=? AND kind='commitment'",
                (reminder["commitment_id"],),
            ).fetchone()
            if row is None:
                raise KeyError("Commitment not found")
            item = entity(row)
            log = db.execute(
                "SELECT revision FROM daily_logs WHERE commitment_id=? AND day=?",
                (item["id"], reminder["day"]),
            ).fetchone()
        return Commitments(self.store).progress(
            item["id"],
            reminder["day"],
            Progress(
                revision=log["revision"] if log else 0,
                commitmentRevision=item["revision"],
                operation="complete",
            ),
            notification=(key, revision),
        )

    def change(self, key, revision, minutes=None):
        now = self.clock()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM reminder_jobs WHERE id=?", (key,)
            ).fetchone()
            if row is None:
                raise KeyError("Reminder not found")
            if row["revision"] != revision:
                raise ValueError("Reminder changed on another device. Refresh first.")
            if row["state"] != "ready":
                raise ValueError("This reminder is no longer awaiting a response")
            state = "scheduled" if minutes else "dismissed"
            due = now + minutes * 60 if minutes else row["due"]
            db.execute(
                "UPDATE reminder_jobs SET state=?,due=?,snoozed=?,revision=revision+1,updated=? WHERE id=?",
                (state, due, int(bool(minutes)), now, key),
            )
        return {"id": key, "state": state, "dueAt": due, "revision": revision + 1}


def router(scheduler):
    routes = APIRouter(prefix="/api/notifications")

    @routes.get("")
    async def notifications():
        return scheduler.list()

    @routes.get("/status")
    async def status():
        with scheduler.store.connect() as db:
            automation = automation_state(db)
            has_device = (
                db.execute(
                    "SELECT 1 FROM push_devices WHERE state='active' LIMIT 1"
                ).fetchone()
                is not None
            )
        return {
            "automationHeld": automation["held"],
            "automationInvalid": automation["invalid"],
            "lastCheck": scheduler.last_check,
            "healthy": scheduler.last_error is None
            and scheduler.last_check is not None
            and scheduler.clock() - scheduler.last_check < 45,
            "error": scheduler.last_error,
            "delivery": "in-app",
            "pushConfigured": bool(scheduler.store.get("push_contact")) and has_device,
        }

    @routes.post("/check")
    async def check():
        return scheduler.tick()

    def change(key, revision, minutes=None):
        try:
            return scheduler.change(key, revision, minutes)
        except KeyError as error:
            raise HTTPException(404, str(error))
        except ValueError as error:
            raise HTTPException(409, str(error))

    @routes.post("/{key}/snooze")
    async def snooze(key: str, body: Snooze):
        return change(key, body.revision, body.minutes)

    @routes.post("/{key}/complete")
    async def complete(key: str, body: Revision):
        try:
            return scheduler.complete(key, body.revision)
        except KeyError as error:
            raise HTTPException(404, str(error))
        except ValueError as error:
            raise HTTPException(409, str(error))

    @routes.post("/{key}/dismiss")
    async def dismiss(key: str, body: Revision):
        return change(key, body.revision)

    return routes
