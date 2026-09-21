"""Durable automation fence and explicit future-only restart after product restore."""

import hashlib
import json
import math
import time
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

KEY = "restore_automation_hold"
MESSAGE = "Automations paused after restore. Review and resume future schedules in Settings → Backups."


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def hold_marker(request_id=None, now=None):
    now = time.time() if now is None else now
    return {
        "version": 1,
        "requestId": str(request_id or uuid4()),
        "restoredAt": now,
        "cutoff": now,
        "state": "held",
    }


def write_setting(db, key, value):
    db.execute(
        "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value, allow_nan=False)),
    )


def state(db):
    row = db.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    if row is None:
        return {"held": False, "invalid": False, "marker": None, "cutoff": None}
    try:
        value = json.loads(row[0])
        if (
            not isinstance(value, dict)
            or type(value.get("version")) is not int
            or value["version"] != 1
        ):
            raise ValueError()
        UUID(value["requestId"])
        if value["state"] not in {"held", "resumed"}:
            raise ValueError()
        for key in ("restoredAt", "cutoff"):
            if (
                type(value[key]) not in (int, float)
                or not math.isfinite(value[key])
                or value[key] < 0
            ):
                raise ValueError()
        if value["cutoff"] < value["restoredAt"]:
            raise ValueError()
        if value["state"] == "held" and (
            set(value) != {"version", "requestId", "restoredAt", "cutoff", "state"}
            or value["cutoff"] != value["restoredAt"]
        ):
            raise ValueError()
        if value["state"] == "resumed":
            if set(value) != {
                "version",
                "requestId",
                "restoredAt",
                "cutoff",
                "state",
                "resumeRequestId",
            }:
                raise ValueError()
            UUID(value["resumeRequestId"])
        return {
            "held": value["state"] == "held",
            "invalid": False,
            "marker": value,
            "cutoff": value["cutoff"] if value["state"] == "resumed" else None,
        }
    except (ValueError, TypeError, KeyError, AttributeError):
        return {"held": True, "invalid": True, "marker": None, "cutoff": None}


class Resume(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: UUID
    previewToken: str = Field(pattern=r"^[a-f0-9]{64}$")
    cutoff: float = Field(ge=0, allow_inf_nan=False)
    confirmed: bool = Field(strict=True)


class RestoreAutomation:
    def __init__(self, store, clock=None):
        self.store, self.clock = store, clock or time.time

    def status(self):
        with self.store.connect() as db:
            current = state(db)
            last_resume = None
            if current["marker"] and current["marker"]["state"] == "resumed":
                row = db.execute(
                    "SELECT value FROM settings WHERE key=?",
                    (
                        "restore_automation_resume:"
                        + current["marker"]["resumeRequestId"],
                    ),
                ).fetchone()
                if row:
                    last_resume = json.loads(row[0])["result"]
        return {
            "lastResume": last_resume,
            "held": current["held"],
            "invalid": current["invalid"],
            "marker": current["marker"],
            "message": (
                MESSAGE if current["held"] else "Scheduled automations are enabled."
            ),
            "policy": "future_only",
        }

    def _review(self, db, cutoff):
        cutoff = float(cutoff)
        current = state(db)
        if current["invalid"]:
            raise ValueError(
                "Restore automation marker is invalid. Operator review is required; automations remain paused."
            )
        if not current["held"]:
            raise ValueError("No restore automation hold needs resuming")
        now = self.clock()
        if (
            cutoff < current["marker"]["restoredAt"]
            or cutoff > now
            or now - cutoff > 300
        ):
            raise ValueError("Resume preview expired. Review again.")
        # All inputs affecting skipped work bind the review, not just its counts.
        rows = {}
        for table in ("push_deliveries", "reminder_jobs", "routines"):
            rows[table] = [
                dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY id")
            ]
        rows["commitments"] = [
            dict(row)
            for row in db.execute(
                "SELECT id,revision,body FROM entities WHERE kind='commitment' ORDER BY id"
            )
        ]
        counts = {
            "pendingPushes": sum(
                row["state"] == "pending" for row in rows["push_deliveries"]
            ),
            "pastReminders": sum(
                row["state"] == "ready"
                or (row["state"] == "scheduled" and row["due"] <= cutoff)
                for row in rows["reminder_jobs"]
            ),
            "overdueRoutines": sum(
                row["next_due"] is not None and row["next_due"] <= cutoff
                for row in rows["routines"]
            ),
        }
        return {
            "previewToken": digest(
                {"marker": current["marker"], "cutoff": cutoff, "rows": rows}
            ),
            "cutoff": cutoff,
            "holdRequestId": current["marker"]["requestId"],
            **counts,
            "policy": "future_only",
            "warning": "Skip pending pushes and past-due reminders; advance routine schedules. Existing records remain. Only future schedules resume; missed work is not resent.",
        }

    def preview(self):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._review(db, self.clock())

    def resume(self, body):
        if body.confirmed is not True:
            raise ValueError("Explicit confirmation is required")
        request = str(body.requestId)
        fingerprint = digest(body.model_dump(mode="json"))
        receipt_key = "restore_automation_resume:" + request
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = state(db)
            old = db.execute(
                "SELECT value FROM settings WHERE key=?", (receipt_key,)
            ).fetchone()
            if old:
                receipt = json.loads(old[0])
                if (
                    receipt["fingerprint"] != fingerprint
                    or current["invalid"]
                    or current["marker"] is None
                    or current["marker"]["requestId"] != receipt["holdRequestId"]
                ):
                    raise ValueError(
                        "Request belongs to a different review or restore generation"
                    )
                return receipt["result"]
            review = self._review(db, body.cutoff)
            if review["previewToken"] != body.previewToken:
                raise ValueError("Automations changed since review. Review again.")
            now = self.clock()
            fresh = self._review(db, now)
            if any(
                fresh[key] != review[key]
                for key in ("pendingPushes", "pastReminders", "overdueRoutines")
            ):
                raise ValueError(
                    "More schedules became due since review. Review again."
                )
            cutoff = now
            # Only schedules after actual confirmation resume. If additional
            # known work became due, the owner must review its new counts first.
            db.execute(
                "UPDATE push_deliveries SET state='cancelled',error='Skipped after product restore',updated=? WHERE state='pending'",
                (now,),
            )
            db.execute(
                "UPDATE reminder_jobs SET state='expired',revision=revision+1,updated=? WHERE state='ready' OR (state='scheduled' AND due<=?)",
                (now, cutoff),
            )
            from .routines import occurrence

            for row in db.execute("SELECT * FROM routines").fetchall():
                value = json.loads(row["body"])
                if value["enabled"]:
                    db.execute(
                        "UPDATE routines SET next_due=?,updated=? WHERE id=?",
                        (occurrence(value, cutoff), now, row["id"]),
                    )
            marker = {
                **current["marker"],
                "state": "resumed",
                "cutoff": cutoff,
                "resumeRequestId": request,
            }
            write_setting(db, KEY, marker)
            result = {
                "held": False,
                "requestId": request,
                "holdRequestId": marker["requestId"],
                "resumedAt": now,
                **{
                    key: review[key]
                    for key in (
                        "cutoff",
                        "pendingPushes",
                        "pastReminders",
                        "overdueRoutines",
                        "policy",
                    )
                },
            }
            result["cutoff"] = cutoff
            write_setting(
                db,
                receipt_key,
                {
                    "fingerprint": fingerprint,
                    "holdRequestId": marker["requestId"],
                    "result": result,
                },
            )
            return result


def router(domain):
    routes = APIRouter(prefix="/api/automation/restore")

    def call(function, *args):
        try:
            return function(*args)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @routes.get("")
    async def status():
        return domain.status()

    @routes.post("/preview")
    async def preview():
        return call(domain.preview)

    @routes.post("/resume")
    async def resume(body: Resume):
        return call(domain.resume, body)

    return routes
