"""Conditional edits/deletes of Leam-created personal calendar events."""

import asyncio
import json
import time
from collections import defaultdict
from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import Field, model_validator

from .calendar import event_record
from .calendar_actions import TimeBlock, digest, encoded, event_path
from .commitments import Input


class EventEdit(TimeBlock):
    title: str = Field(min_length=1, max_length=500)


class Change(Input):
    requestId: UUID
    creationId: UUID
    operation: Literal["edit", "delete"]
    expectedEtag: str = Field(
        min_length=1, max_length=2000, pattern=r'^(W/)?"[^\r\n"]+"$'
    )
    edit: EventEdit | None = None

    @model_validator(mode="after")
    def operation_shape(self):
        if (self.operation == "edit") != (self.edit is not None):
            raise ValueError("Edit details are required only for an edit")
        return self


class CalendarChanges:
    def __init__(self, actions):
        self.actions = actions
        self.store = actions.store
        self.accounts = actions.accounts
        self.locks = defaultdict(asyncio.Lock)

    def target(self, creation_id):
        row = self.actions.get(creation_id)
        if not row or not row["result"]:
            raise HTTPException(409, "Recover the event creation before changing it")
        calendar = self.actions.calendars.get(row["calendar_id"])
        event = json.loads(row["result"])["event"]
        return calendar, event_path(calendar, event["id"])

    async def current(self, creation_id):
        calendar, path = self.target(creation_id)
        response = await self.accounts.authorized(
            calendar["account_id"],
            "GET",
            path,
            accepted_statuses={404, 410},
            headers={"Prefer": 'outlook.timezone="UTC"'},
        )
        if response.status_code in (404, 410):
            return calendar, path, None
        try:
            raw = response.json()
            if not isinstance(raw, dict):
                raise ValueError("Invalid event")
            if raw.get("status") != "cancelled" and not raw.get("isCancelled"):
                event_record(calendar["provider"], raw)
        except (ValueError, KeyError, TypeError):
            raise HTTPException(
                502, "Provider returned an invalid event; previous state retained"
            ) from None
        if raw.get("status") == "cancelled" or raw.get("isCancelled"):
            return calendar, path, None
        return calendar, path, raw

    def editability(self, calendar, raw):
        if raw.get("isAllDay") or "date" in raw.get("start", {}):
            return "All-day events must be managed in the provider calendar"
        if not calendar["can_write"]:
            return "This calendar is read-only"
        if (
            raw.get("attendees")
            or raw.get("isOrganizer") is False
            or raw.get("organizer", {}).get("self") is False
        ):
            return (
                "This event involves other people; manage it in the provider calendar"
            )
        if (
            raw.get("recurrence")
            or raw.get("recurringEventId")
            or raw.get("type") in ("seriesMaster", "occurrence", "exception")
        ):
            return "Recurring events must be managed in the provider calendar"
        if not (raw.get("etag") or raw.get("@odata.etag")):
            return (
                "Provider did not supply an event version; refresh before changing it"
            )
        return None

    async def inspect(self, creation_id):
        calendar, _, raw = await self.current(creation_id)
        edit = None
        if raw and not self.editability(calendar, raw):
            event = event_record(calendar["provider"], raw)
            review = json.loads(self.actions.get(creation_id)["review"])
            zone = review["schedule"]["timezone"]
            start = datetime.fromisoformat(event["start"]).astimezone(ZoneInfo(zone))
            end = datetime.fromisoformat(event["end"])
            edit = {
                "title": event["title"],
                "date": str(start.date()),
                "time": start.strftime("%H:%M"),
                "timezone": zone,
                "minutes": (end - start).total_seconds() / 60,
                "fold": start.fold,
            }
        return {
            "creationId": creation_id,
            "edit": edit,
            "deleted": raw is None,
            "event": event_record(calendar["provider"], raw) if raw else None,
            "editBlocked": self.editability(calendar, raw) if raw else None,
            "checkedAt": time.time(),
        }

    async def preview(self, body):
        current = await self.inspect(str(body.creationId))
        if current["deleted"]:
            raise HTTPException(409, "Event is already removed; refresh its state")
        if current["editBlocked"]:
            raise HTTPException(409, current["editBlocked"])
        if current["event"]["etag"] != body.expectedEtag:
            raise HTTPException(
                409, "Event changed externally; inspect and review a new change"
            )
        after = None
        if body.edit:
            start, end, zone = body.edit.instants()
            after = {
                "title": body.edit.title,
                "localStart": start.astimezone(zone).isoformat(),
                "localEnd": end.astimezone(zone).isoformat(),
                "timezone": body.edit.timezone,
            }
        return {
            "request": body.model_dump(mode="json"),
            "before": current["event"],
            "after": after,
        }

    def saved(self, key):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM calendar_changes WHERE id=?", (key,)
            ).fetchone()
        return dict(row) if row else None

    def history(self, creation_id):
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT * FROM calendar_changes WHERE creation_id=? ORDER BY created DESC",
                (creation_id,),
            ).fetchall()
        return {
            "items": [
                {
                    "requestId": r["id"],
                    "request": json.loads(r["request"]),
                    "state": r["state"],
                    "result": json.loads(r["result"]) if r["result"] else None,
                    "error": r["error"],
                }
                for r in rows
            ]
        }

    def finish(self, key, creation_id, calendar, result):
        result = {"requestId": key, "creationId": creation_id, **result}
        with self.store.connect() as db:
            db.execute(
                "UPDATE calendar_changes SET state='complete',result=?,error=NULL WHERE id=?",
                (encoded(result), key),
            )
            db.execute(
                "UPDATE calendar_snapshots SET error='Calendar changed; refresh the saved view' WHERE calendar_id=?",
                (calendar["id"],),
            )
        return result

    def conflict(self, key, message):
        with self.store.connect() as db:
            db.execute(
                "UPDATE calendar_changes SET state='conflict',error=? WHERE id=?",
                (message, key),
            )
        raise HTTPException(409, message)

    async def apply(self, body):
        key, creation_id = str(body.requestId), str(body.creationId)
        request = body.model_dump(mode="json")
        # One writer per linked event, including distinct simultaneous request IDs.
        async with self.locks["request:" + key], self.locks["event:" + creation_id]:
            saved = self.saved(key)
            if saved:
                if saved["fingerprint"] != digest(request):
                    raise HTTPException(
                        409, "Request ID belongs to a different event change"
                    )
                if saved["state"] == "complete":
                    return json.loads(saved["result"])
                if saved["state"] == "conflict":
                    raise HTTPException(409, saved["error"])
            if not saved:
                with self.store.connect() as db:
                    pending = db.execute(
                        "SELECT id FROM calendar_changes WHERE creation_id=? AND state='pending' LIMIT 1",
                        (creation_id,),
                    ).fetchone()
                if pending:
                    raise HTTPException(
                        409, "Recover the pending event change before creating another"
                    )
            calendar, path, raw = await self.current(creation_id)
            if saved:
                payload = json.loads(saved["payload"]) if saved["payload"] else None
                if raw is None:
                    if body.operation == "delete":
                        return self.finish(
                            key,
                            creation_id,
                            calendar,
                            {"deleted": True, "reconciled": True},
                        )
                    self.conflict(key, "Event was removed; inspect its current state")
                current = event_record(calendar["provider"], raw)
                if body.operation == "edit":
                    intended = event_record(
                        calendar["provider"], {**payload, "id": current["id"]}
                    )
                    if all(
                        current[k] == intended[k]
                        for k in ("title", "start", "end", "allDay")
                    ):
                        return self.finish(
                            key,
                            creation_id,
                            calendar,
                            {"event": current, "reconciled": True},
                        )
                if current["etag"] != body.expectedEtag:
                    self.conflict(
                        key, "Event changed externally; inspect and review a new change"
                    )
            else:
                if raw is None:
                    raise HTTPException(
                        409, "Event is already removed; refresh its state"
                    )
                current = event_record(calendar["provider"], raw)
                reason = self.editability(calendar, raw)
                if reason:
                    raise HTTPException(409, reason)
                if current["etag"] != body.expectedEtag:
                    raise HTTPException(
                        409, "Event changed externally; inspect and review a new change"
                    )
                payload = None
                if body.edit:
                    start, end, _ = body.edit.instants()
                    payload = (
                        {
                            "summary": body.edit.title,
                            "start": {"dateTime": start.isoformat()},
                            "end": {"dateTime": end.isoformat()},
                        }
                        if calendar["provider"] == "google"
                        else {
                            "subject": body.edit.title,
                            "start": {
                                "dateTime": start.isoformat().removesuffix("+00:00"),
                                "timeZone": "UTC",
                            },
                            "end": {
                                "dateTime": end.isoformat().removesuffix("+00:00"),
                                "timeZone": "UTC",
                            },
                        }
                    )
                with self.store.connect() as db:
                    db.execute(
                        "INSERT INTO calendar_changes VALUES (?,?,?,?,?,'pending',NULL,NULL,?)",
                        (
                            key,
                            creation_id,
                            digest(request),
                            encoded(request),
                            encoded(payload),
                            time.time(),
                        ),
                    )
            reason = self.editability(calendar, raw)
            if reason:
                self.conflict(key, reason)
            try:
                kwargs = {
                    "headers": {
                        "If-Match": body.expectedEtag,
                        "Prefer": 'outlook.timezone="UTC"',
                    },
                    "accepted_statuses": {404, 410, 412},
                }
                if calendar["provider"] == "google":
                    kwargs["params"] = {"sendUpdates": "none"}
                if body.operation == "edit":
                    kwargs["json"] = payload
                response = await self.accounts.authorized(
                    calendar["account_id"],
                    "PATCH" if body.operation == "edit" else "DELETE",
                    path,
                    **kwargs,
                )
                if response.status_code == 412:
                    self.conflict(
                        key, "Event changed externally; inspect and review a new change"
                    )
                if response.status_code in (404, 410) and body.operation == "edit":
                    self.conflict(key, "Event was removed; inspect its current state")
                result = (
                    {"deleted": True}
                    if body.operation == "delete"
                    else {"event": event_record(calendar["provider"], response.json())}
                )
                return self.finish(key, creation_id, calendar, result)
            except Exception as error:
                message = (
                    error.detail
                    if isinstance(error, HTTPException)
                    else "Event change is unconfirmed; recover this same change"
                )
                with self.store.connect() as db:
                    db.execute(
                        "UPDATE calendar_changes SET error=? WHERE id=?",
                        (str(message), key),
                    )
                raise HTTPException(
                    error.status_code if isinstance(error, HTTPException) else 502,
                    str(message),
                ) from None


def router(changes):
    routes = APIRouter(prefix="/api/calendar")

    @routes.get("/actions/{creation_id}/event")
    async def inspect(creation_id: UUID):
        return await changes.inspect(str(creation_id))

    @routes.get("/actions/{creation_id}/changes")
    async def history(creation_id: UUID):
        return changes.history(str(creation_id))

    @routes.post("/changes/preview")
    async def preview(body: Change):
        return await changes.preview(body)

    @routes.post("/changes")
    async def apply(body: Change):
        try:
            return await changes.apply(body)
        except HTTPException as error:
            saved = changes.saved(str(body.requestId))
            if not saved or saved["state"] == "conflict":
                error.headers = {
                    **(error.headers or {}),
                    "X-Leam-Action-Reserved": "no",
                }
            raise

    return routes
