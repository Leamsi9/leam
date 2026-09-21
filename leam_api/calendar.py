"""Provider calendar snapshots; incomplete fetches never replace accepted data."""

import asyncio
import json
import time
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import AwareDatetime, model_validator

from .commitments import Input


class Window(Input):
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def bounded(self):
        duration = (self.end - self.start).total_seconds()
        if not 0 < duration <= 31 * 86400:
            raise ValueError("Choose a calendar range of up to 31 days")
        return self


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Calendar timestamp has no offset")
    return parsed.astimezone(timezone.utc).isoformat()


def event_record(provider, item):
    event_id = item["id"]
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("Missing event ID")
    if provider == "google":
        all_day = "date" in item["start"]
        start = item["start"]["date"] if all_day else instant(item["start"]["dateTime"])
        end = item["end"]["date"] if all_day else instant(item["end"]["dateTime"])
        title = item.get("summary") or "(Untitled event)"
        link = item.get("htmlLink")
    else:
        all_day = bool(item.get("isAllDay"))

        def graph_time(value):
            # Every Graph read requests UTC; unexpected local/Windows zones fail closed.
            if value.get("timeZone") not in ("UTC", "Etc/UTC"):
                raise ValueError("Provider did not return requested UTC timestamps")
            raw = value["dateTime"]
            return instant(
                raw if raw.endswith("Z") or "+" in raw[10:] else raw + "+00:00"
            )

        if all_day:
            # All-day values are dates, not instants to shift into a viewer's zone.
            start = item["start"]["dateTime"][:10]
            end = item["end"]["dateTime"][:10]
        else:
            start, end = graph_time(item["start"]), graph_time(item["end"])
        title = item.get("subject") or "(Untitled event)"
        link = item.get("webLink")
    if not isinstance(title, str):
        raise ValueError("Invalid event title")
    if all_day:
        date.fromisoformat(start)
        date.fromisoformat(end)
    if start >= end:
        raise ValueError("Invalid event date range")
    if not isinstance(link, str) or urlsplit(link).scheme != "https":
        link = None
    return {
        "id": event_id,
        "title": title,
        "start": start,
        "end": end,
        "allDay": all_day,
        "url": link,
        "etag": item.get("etag") or item.get("@odata.etag"),
    }


class Calendars:
    def __init__(self, store, accounts):
        self.store = store
        self.accounts = accounts
        self.locks = defaultdict(asyncio.Lock)

    def list(self):
        with self.store.connect() as db:
            items = [
                dict(r)
                for r in db.execute("""SELECT c.id,c.account_id AS accountId,c.name,c.timezone,c.can_write AS canWrite,
                c.listed AS listedAt,a.provider,a.identity,s.synced AS syncedAt,s.error,s.start,s.end
                FROM calendars c JOIN accounts a ON a.id=c.account_id LEFT JOIN calendar_snapshots s ON s.calendar_id=c.id ORDER BY a.identity,c.name""")
            ]
            accounts = [
                dict(r)
                for r in db.execute("""SELECT a.id,a.provider,a.identity,a.state,s.synced AS calendarsListedAt,s.error
                FROM accounts a LEFT JOIN calendar_scans s ON s.account_id=a.id ORDER BY a.created""")
            ]
        return {"items": items, "accounts": accounts}

    def get(self, key):
        with self.store.connect() as db:
            row = db.execute(
                """SELECT c.*,a.provider,a.identity FROM calendars c JOIN accounts a ON a.id=c.account_id WHERE c.id=?""",
                (key,),
            ).fetchone()
        if not row:
            raise HTTPException(
                404, "Calendar not found; refresh the account calendar list"
            )
        return dict(row)

    def snapshot(self, key):
        calendar = self.get(key)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM calendar_snapshots WHERE calendar_id=?", (key,)
            ).fetchone()
        return {
            "id": key,
            "name": calendar["name"],
            "accountId": calendar["account_id"],
            "provider": calendar["provider"],
            "identity": calendar["identity"],
            "canWrite": bool(calendar["can_write"]),
            "timezone": calendar["timezone"],
            "events": json.loads(row["body"]) if row else [],
            "start": row["start"] if row else None,
            "end": row["end"] if row else None,
            "syncedAt": row["synced"] if row else None,
            "error": row["error"] if row else None,
        }

    async def pages(self, account_id, provider, path, params):
        items = []
        seen = set()
        initial = urlsplit(path)
        async with asyncio.timeout(45):
            for _ in range(20):
                response = await self.accounts.authorized(
                    account_id,
                    "GET",
                    path,
                    params=params,
                    headers={"Prefer": 'outlook.timezone="UTC"'},
                )
                body = response.json()
                field = "items" if provider == "google" else "value"
                if not isinstance(body.get(field), list):
                    raise ValueError("Provider did not return a calendar collection")
                items.extend(body[field])
                if len(items) > 5000:
                    raise ValueError(
                        "Calendar range exceeds 5000 events; narrow the range"
                    )
                next_page = body.get(
                    "nextPageToken" if provider == "google" else "@odata.nextLink"
                )
                if not next_page:
                    return items
                if not isinstance(next_page, str) or next_page in seen:
                    raise ValueError("Provider returned an invalid pagination cursor")
                seen.add(next_page)
                if provider == "google":
                    params = {**params, "pageToken": next_page}
                else:
                    following = urlsplit(next_page)
                    if (
                        following.scheme != initial.scheme
                        or following.netloc != initial.netloc
                        or following.path != initial.path
                        or following.fragment
                    ):
                        raise ValueError("Provider pagination changed its API target")
                    path, params = next_page, None
        raise ValueError(
            "Calendar pagination exceeds the supported range; narrow the range"
        )

    async def sync_calendars(self, account_id):
        async with self.locks["account:" + account_id]:
            provider = self.accounts.provider_of(account_id)
            path = (
                "https://www.googleapis.com/calendar/v3/users/me/calendarList"
                if provider == "google"
                else "https://graph.microsoft.com/v1.0/me/calendars"
            )
            params = {"maxResults": 250} if provider == "google" else {"$top": 100}
            try:
                raw = await self.pages(account_id, provider, path, params)
                items = []
                for r in raw:
                    external = r["id"]
                    if not isinstance(external, str) or not external:
                        raise ValueError("Missing calendar ID")
                    key = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL, account_id + ":calendar:" + external
                        )
                    )
                    name = r.get("summary") if provider == "google" else r.get("name")
                    if not isinstance(name, str):
                        raise ValueError("Invalid calendar name")
                    writable = (
                        r.get("accessRole")
                        in ["owner", "writer", "writerWithoutPrivateAccess"]
                        if provider == "google"
                        else r.get("canEdit") is True
                    )
                    items.append(
                        (
                            key,
                            account_id,
                            external,
                            name,
                            r.get("timeZone", "UTC"),
                            int(writable),
                            time.time(),
                        )
                    )
                now = time.time()
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    if not db.execute(
                        "SELECT id FROM accounts WHERE id=?", (account_id,)
                    ).fetchone():
                        raise HTTPException(
                            409, "Account was disconnected during synchronization"
                        )
                    current = {r[0] for r in items}
                    for old in db.execute(
                        "SELECT id FROM calendars WHERE account_id=?", (account_id,)
                    ).fetchall():
                        if old["id"] not in current:
                            db.execute("DELETE FROM calendars WHERE id=?", (old["id"],))
                    db.executemany(
                        """INSERT INTO calendars VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                        timezone=excluded.timezone,can_write=excluded.can_write,listed=excluded.listed""",
                        items,
                    )
                    db.execute(
                        "INSERT INTO calendar_scans VALUES (?,?,NULL) ON CONFLICT(account_id) DO UPDATE SET synced=excluded.synced,error=NULL",
                        (account_id, now),
                    )
            except Exception as error:
                message = (
                    error.detail
                    if isinstance(error, HTTPException)
                    else "Calendar list synchronization failed; previous data retained"
                )
                with self.store.connect() as db:
                    if db.execute(
                        "SELECT id FROM accounts WHERE id=?", (account_id,)
                    ).fetchone():
                        db.execute(
                            "INSERT INTO calendar_scans VALUES (?,NULL,?) ON CONFLICT(account_id) DO UPDATE SET error=excluded.error",
                            (account_id, str(message)),
                        )
                raise HTTPException(
                    error.status_code if isinstance(error, HTTPException) else 502,
                    str(message),
                ) from None
            return {
                "items": [
                    i for i in self.list()["items"] if i["accountId"] == account_id
                ],
                "syncedAt": now,
            }

    async def sync_events(self, key, window):
        async with self.locks["calendar:" + key]:
            calendar = self.get(key)
            provider = calendar["provider"]
            external = quote(calendar["external_id"], safe="")
            start, end = window.start.isoformat(), window.end.isoformat()
            if provider == "google":
                path = (
                    "https://www.googleapis.com/calendar/v3/calendars/"
                    + external
                    + "/events"
                )
                params = {
                    "timeMin": start,
                    "timeMax": end,
                    "singleEvents": "true",
                    "maxResults": 250,
                    "orderBy": "startTime",
                    "timeZone": "UTC",
                }
            else:
                path = (
                    "https://graph.microsoft.com/v1.0/me/calendars/"
                    + external
                    + "/calendarView"
                )
                params = {"startDateTime": start, "endDateTime": end, "$top": 250}
            try:
                raw = await self.pages(calendar["account_id"], provider, path, params)
                events = [
                    event_record(provider, r)
                    for r in raw
                    if r.get("status") != "cancelled" and not r.get("isCancelled")
                ]
                events = sorted(
                    {r["id"]: r for r in events}.values(), key=lambda r: r["start"]
                )
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    if not db.execute(
                        "SELECT id FROM calendars WHERE id=?", (key,)
                    ).fetchone():
                        raise HTTPException(
                            409, "Calendar was removed during synchronization"
                        )
                    db.execute(
                        """INSERT INTO calendar_snapshots VALUES (?,?,?,?,?,NULL) ON CONFLICT(calendar_id)
                        DO UPDATE SET start=excluded.start,end=excluded.end,body=excluded.body,synced=excluded.synced,error=NULL""",
                        (key, start, end, json.dumps(events), time.time()),
                    )
            except Exception as error:
                message = (
                    error.detail
                    if isinstance(error, HTTPException)
                    else "Event synchronization failed; previous data retained"
                )
                with self.store.connect() as db:
                    if db.execute(
                        "SELECT id FROM calendars WHERE id=?", (key,)
                    ).fetchone():
                        db.execute(
                            """INSERT INTO calendar_snapshots VALUES (?,NULL,NULL,'[]',NULL,?) ON CONFLICT(calendar_id) DO UPDATE SET error=excluded.error""",
                            (key, str(message)),
                        )
                raise HTTPException(
                    error.status_code if isinstance(error, HTTPException) else 502,
                    str(message),
                ) from None
            return self.snapshot(key)


def router(calendars):
    routes = APIRouter(prefix="/api/calendar")

    @routes.get("")
    async def list_calendars():
        return calendars.list()

    @routes.post("/accounts/{key}/sync")
    async def sync_calendars(key: str):
        return await calendars.sync_calendars(key)

    @routes.get("/{key}")
    async def snapshot(key: str):
        return calendars.snapshot(key)

    @routes.post("/{key}/sync")
    async def sync_events(key: str, body: Window):
        return await calendars.sync_events(key, body)

    return routes
