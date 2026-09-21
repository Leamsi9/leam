"""Reviewed calendar writes with durable, exact-payload provider retry identities."""

import asyncio
import hashlib
import json
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from urllib.parse import quote
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field

from .calendar import event_record
from .commitments import Commitment, Input, entity


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class TimeBlock(Input):
    date: date
    time: str = Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    timezone: str = Field(max_length=100)
    minutes: int = Field(ge=1, le=1440)
    fold: Literal[0, 1] = 0

    def instants(self):
        try:
            Commitment.valid_zone(self.timezone)
            local = datetime.fromisoformat(f"{self.date}T{self.time}")
            zone = ZoneInfo(self.timezone)
            start = local.replace(tzinfo=zone, fold=self.fold).astimezone(timezone.utc)
            if start.astimezone(zone).replace(tzinfo=None) != local:
                raise ValueError(
                    "This local time does not exist because the clocks change"
                )
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return start, start + timedelta(minutes=self.minutes), zone


class Schedule(TimeBlock):
    calendarId: str = Field(min_length=1, max_length=100)
    commitmentId: str = Field(min_length=1, max_length=100)
    commitmentRevision: int = Field(ge=1)


def event_path(calendar, event_id=None):
    external = quote(calendar["external_id"], safe="")
    base = (
        f"https://www.googleapis.com/calendar/v3/calendars/{external}/events"
        if calendar["provider"] == "google"
        else f"https://graph.microsoft.com/v1.0/me/calendars/{external}/events"
    )
    return base + ("/" + quote(event_id, safe="") if event_id else "")


class CreateAction(Input):
    requestId: UUID
    schedule: Schedule
    previewDigest: str = Field(pattern=r"^[a-f0-9]{64}$")


class CalendarActions:
    def __init__(self, calendars):
        self.calendars = calendars
        self.store = calendars.store
        self.accounts = calendars.accounts
        self.locks = defaultdict(asyncio.Lock)

    def preview(self, body):
        calendar = self.calendars.get(body.calendarId)
        if not calendar["can_write"]:
            raise HTTPException(409, "This calendar is read-only")
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM entities WHERE id=? AND kind='commitment'",
                (body.commitmentId,),
            ).fetchone()
        if not row:
            raise HTTPException(404, "Commitment not found")
        commitment = entity(row)
        if commitment["revision"] != body.commitmentRevision:
            raise HTTPException(409, "Commitment changed; review its current version")
        if commitment["status"] != "active":
            raise HTTPException(409, "Only active commitments can be scheduled")
        start, end, zone = body.instants()
        review = {
            "schedule": body.model_dump(mode="json"),
            "title": commitment["title"],
            "calendar": {
                "id": calendar["id"],
                "name": calendar["name"],
                "identity": calendar["identity"],
                "provider": calendar["provider"],
            },
            "start": start.isoformat(),
            "end": end.isoformat(),
            "localStart": start.astimezone(zone).isoformat(),
            "localEnd": end.astimezone(zone).isoformat(),
        }
        return {**review, "digest": digest(review)}

    def get(self, key):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM calendar_actions WHERE id=?", (key,)
            ).fetchone()
        return dict(row) if row else None

    def status(self, row):
        return {
            "requestId": row["id"],
            "state": "complete" if row["result"] else "unconfirmed",
            "review": json.loads(row["review"]),
            "request": json.loads(row["request"]),
            "result": json.loads(row["result"]) if row["result"] else None,
            "error": row["error"],
            "createdAt": row["created"],
        }

    def list(self, calendar_id=None, offset=0):
        with self.store.connect() as db:
            # Pending actions must remain discoverable even after many completed writes.
            rows = db.execute(
                "SELECT * FROM calendar_actions WHERE (? IS NULL OR calendar_id=?) ORDER BY (result IS NULL) DESC,created DESC,id LIMIT 51 OFFSET ?",
                (calendar_id, calendar_id, offset),
            ).fetchall()
        return {
            "items": [self.status(r) for r in rows[:50]],
            "nextOffset": offset + 50 if len(rows) > 50 else None,
        }

    async def create(self, body):
        key = str(body.requestId)
        request = body.model_dump(mode="json")
        fingerprint = digest(request)
        async with self.locks[key]:
            saved = self.get(key)
            if saved:
                if saved["fingerprint"] != fingerprint:
                    raise HTTPException(
                        409, "Request ID belongs to a different calendar action"
                    )
                if saved["result"]:
                    return json.loads(saved["result"])
            else:
                review = self.preview(body.schedule)
                if review["digest"] != body.previewDigest:
                    raise HTTPException(
                        409, "Calendar or commitment changed; preview again"
                    )
                calendar = self.calendars.get(body.schedule.calendarId)
                if calendar["provider"] == "google":
                    payload = {
                        "id": body.requestId.hex,
                        "summary": review["title"],
                        "start": {"dateTime": review["start"]},
                        "end": {"dateTime": review["end"]},
                        "extendedProperties": {
                            "private": {
                                "leamActionId": key,
                                "leamCommitmentId": body.schedule.commitmentId,
                            }
                        },
                    }
                else:
                    payload = {
                        "transactionId": key,
                        "subject": review["title"],
                        "start": {
                            "dateTime": review["start"].removesuffix("+00:00"),
                            "timeZone": "UTC",
                        },
                        "end": {
                            "dateTime": review["end"].removesuffix("+00:00"),
                            "timeZone": "UTC",
                        },
                    }
                with self.store.connect() as db:
                    db.execute(
                        "INSERT INTO calendar_actions VALUES (?,?,?,?,?,?,NULL,NULL,?)",
                        (
                            key,
                            fingerprint,
                            body.schedule.calendarId,
                            encoded(request),
                            encoded(review),
                            encoded(payload),
                            time.time(),
                        ),
                    )
                saved = self.get(key)
            # Never regenerate a reserved payload from subsequently edited personal state.
            calendar = self.calendars.get(saved["calendar_id"])
            payload = json.loads(saved["payload"])
            provider = calendar["provider"]
            path = event_path(calendar)
            try:
                response = await self.accounts.authorized(
                    calendar["account_id"],
                    "POST",
                    path,
                    json=payload,
                    accepted_statuses={409} if provider == "google" else set(),
                    headers={"Prefer": 'outlook.timezone="UTC"'},
                    params={"sendUpdates": "none"} if provider == "google" else None,
                )
                if provider == "google" and response.status_code == 409:
                    response = await self.accounts.authorized(
                        calendar["account_id"], "GET", path + "/" + payload["id"]
                    )
                    raw = response.json()
                    if (
                        raw.get("extendedProperties", {})
                        .get("private", {})
                        .get("leamActionId")
                        != key
                    ):
                        raise HTTPException(
                            409,
                            "Calendar event identity conflict; inspect the provider calendar",
                        )
                else:
                    raw = response.json()
                result = {
                    "requestId": key,
                    "calendarId": calendar["id"],
                    "commitmentId": body.schedule.commitmentId,
                    "event": event_record(provider, raw),
                }
                with self.store.connect() as db:
                    db.execute(
                        "UPDATE calendar_actions SET result=?,error=NULL WHERE id=?",
                        (encoded(result), key),
                    )
                    db.execute(
                        "UPDATE calendar_snapshots SET error='Calendar changed; refresh the saved view' WHERE calendar_id=?",
                        (calendar["id"],),
                    )
                return result
            except Exception as error:
                message = (
                    error.detail
                    if isinstance(error, HTTPException)
                    else "Calendar outcome is unconfirmed; retry this same action to recover"
                )
                with self.store.connect() as db:
                    db.execute(
                        "UPDATE calendar_actions SET error=? WHERE id=?",
                        (str(message), key),
                    )
                raise HTTPException(
                    error.status_code if isinstance(error, HTTPException) else 502,
                    str(message),
                ) from None


def router(actions):
    routes = APIRouter(prefix="/api/calendar/actions")

    @routes.get("")
    async def history(
        calendarId: str | None = None, offset: int = Query(default=0, ge=0)
    ):
        return actions.list(calendarId, offset)

    @routes.post("/preview")
    async def preview(body: Schedule):
        return actions.preview(body)

    @routes.post("")
    async def create(body: CreateAction):
        try:
            return await actions.create(body)
        except HTTPException as error:
            if actions.get(str(body.requestId)) is None:
                error.headers = {
                    **(error.headers or {}),
                    "X-Leam-Action-Reserved": "no",
                }
            raise

    return routes
