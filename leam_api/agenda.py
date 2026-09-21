"""Saved-source daily planning. Local triage never mutates provider records."""

import hashlib
import json
import time
import uuid
from datetime import date as Day
from datetime import datetime, timedelta
from datetime import time as DayTime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, field_validator

from .accounts import GMAIL_READONLY
from .calendar_visibility import CalendarVisibility, VisibilityChange, calendar_key
from .commitments import Commitments, Input
from .conversation_titles import ConversationTitles

REFERENCE_BYTES = 2048
FRESH_SECONDS = 900


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


class Selection(Input):
    date: Day
    timezone: str = Field(min_length=1, max_length=100)

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Choose a valid IANA timezone") from None
        return value

    @field_validator("date")
    @classmethod
    def bounded_date(cls, value):
        if value == Day.max:
            raise ValueError("Choose a date with a representable following day")
        return value

    def identity(self):
        return self.model_dump(mode="json")

    def scope_key(self):
        return hashlib.sha256(encoded(self.identity())).hexdigest()

    def window(self):
        zone = ZoneInfo(self.timezone)
        start = datetime.combine(self.date, DayTime(), zone)
        end = datetime.combine(self.date + timedelta(days=1), DayTime(), zone)
        return start, end


class Triage(Selection):
    key: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=0)
    disposition: Literal["focus", "later", "dismissed", "none"]


def source_key(kind, *ids):
    return kind + ":" + hashlib.sha256(encoded(ids)).hexdigest()


def overlaps(event, selection):
    if event["allDay"]:
        return event["start"] <= str(selection.date) < event["end"]
    start, end = selection.window()
    event_start = datetime.fromisoformat(event["start"])
    event_end = datetime.fromisoformat(event["end"])
    if event_start == event_end:
        return start <= event_start < end
    return event_start < end and event_end > start


class Agenda:
    def __init__(self, store, runtime=None, *, emails=None):
        self.store, self.runtime, self.emails = store, runtime, emails

    def snapshot(self, selection, *, offset=0, limit=50, include_hidden=False):
        start, end = selection.window()
        now = time.time()
        commitments = Commitments(self.store).today(selection.date)["items"]
        for item in commitments:
            item["key"] = source_key("commitment", item["id"], str(selection.date))
        commitments.sort(key=lambda x: (x["title"].casefold(), x["id"]))
        events, snapshots = [], []
        hidden_event_count = 0
        with self.store.connect() as db:
            db.execute("BEGIN")
            accounts = [
                dict(r)
                for r in db.execute(
                    "SELECT a.id,a.provider,a.state,s.synced AS calendarsListedAt,s.error "
                    "FROM accounts a LEFT JOIN calendar_scans s ON s.account_id=a.id ORDER BY a.id"
                )
            ]
            rows = db.execute(
                "SELECT c.id,c.name,s.start,s.end,s.synced,s.error,s.body "
                "FROM calendars c LEFT JOIN calendar_snapshots s ON s.calendar_id=c.id ORDER BY c.id"
            ).fetchall()
            saved = db.execute(
                "SELECT value FROM settings WHERE key=?",
                ("agenda-triage:" + selection.scope_key(),),
            ).fetchone()
            triage = json.loads(saved[0]) if saved else {}
            visibility = CalendarVisibility.preferences(db)
        for row in rows:
            complete = bool(
                row["start"]
                and row["end"]
                and datetime.fromisoformat(row["start"]) <= start
                and datetime.fromisoformat(row["end"]) >= end
            )
            coverage = (
                "complete" if complete else "partial" if row["synced"] else "none"
            )
            state = (
                "error"
                if row["error"]
                else "not_synced"
                if not row["synced"]
                else "partial"
                if not complete
                else "stale"
                if now - row["synced"] > FRESH_SECONDS
                else "current"
            )
            snapshots.append(
                {
                    "calendarId": row["id"],
                    "name": row["name"],
                    "syncedAt": row["synced"],
                    "start": row["start"],
                    "end": row["end"],
                    "coverage": coverage,
                    "state": state,
                    "error": row["error"],
                }
            )
            for event in json.loads(row["body"] or "[]"):
                if overlaps(event, selection):
                    key = calendar_key(row["id"], event["id"])
                    preference = visibility.get(key, {"hidden": False, "revision": 0})
                    if preference["hidden"]:
                        hidden_event_count += 1
                        if not include_hidden:
                            continue
                    events.append(
                        {
                            "key": key,
                            "visibility": {
                                k: preference[k] for k in ("hidden", "revision")
                            },
                            "calendarId": row["id"],
                            "eventId": event["id"],
                            "title": event["title"],
                            "start": event["start"],
                            "end": event["end"],
                            "allDay": event["allDay"],
                            "url": event.get("url"),
                            "syncedAt": row["synced"],
                        }
                    )
        events.sort(key=lambda x: (not x["allDay"], x["start"], x["key"]))
        mailbox = (
            self.emails.overview()
            if self.emails
            else {"state": "not_connected", "accounts": [], "items": []}
        )
        mail_accounts = {item["accountId"]: item for item in mailbox["accounts"]}
        emails = [
            {
                **item,
                "key": source_key("email", item["accountId"], item["id"]),
                "syncedAt": mail_accounts[item["accountId"]]["syncedAt"],
                "sourceState": mail_accounts[item["accountId"]]["state"],
            }
            for item in mailbox["items"][:20]
        ]
        email_source = {
            key: value
            for key, value in mailbox.items()
            if key not in ("items", "accounts")
        }
        email_source["accounts"] = [
            {key: value for key, value in item.items() if key != "items"}
            for item in mailbox["accounts"]
        ]
        email_source["truncated"] = bool(
            mailbox.get("truncated") or len(mailbox["items"]) > 20
        )
        if mailbox["state"] == "not_connected":
            email_source["reason"] = "mail_consent_not_granted"
        for item in [*commitments, *events, *emails]:
            item["triage"] = triage.get(
                item["key"], {"revision": 0, "disposition": "none"}
            )
        if not accounts:
            state = "not_connected"
        elif not snapshots and all(a["calendarsListedAt"] is None for a in accounts):
            state = "not_synced"
        elif any(
            a["state"] != "connected" or a["error"] or a["calendarsListedAt"] is None
            for a in accounts
        ) or any(s["state"] not in ("current", "stale") for s in snapshots):
            state = "partial"
        elif any(s["state"] == "stale" for s in snapshots) or any(
            now - a["calendarsListedAt"] > FRESH_SECONDS for a in accounts
        ):
            state = "stale"
        else:
            state = "current"
        combined = (
            [("event", x) for x in events]
            + [("email", x) for x in emails]
            + [("commitment", x) for x in commitments]
        )
        # Stable partition: focused rows survive a first-page reload across source kinds.
        combined.sort(key=lambda row: row[1]["triage"]["disposition"] != "focus")
        page = combined[offset : offset + limit]
        next_offset = offset + len(page)
        return {
            **selection.identity(),
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "observedAt": now,
            "hiddenEventCount": hidden_event_count,
            "sources": {
                "calendar": {
                    "state": state,
                    "accounts": accounts,
                    "snapshots": snapshots,
                },
                "email": email_source,
            },
            "commitments": [item for kind, item in page if kind == "commitment"],
            "events": [item for kind, item in page if kind == "event"],
            "emails": [item for kind, item in page if kind == "email"],
            "total": {
                "commitments": len(commitments),
                "events": len(events),
                "emails": len(emails),
            },
            "partial": offset > 0 or next_offset < len(combined),
            "nextOffset": next_offset if next_offset < len(combined) else None,
        }

    def triage(self, body):
        selection = Selection(date=body.date, timezone=body.timezone)
        # Full eligible source identities, independent of the UI's current page.
        snapshot = self.snapshot(selection, limit=2**31)
        item = next(
            (
                x
                for x in snapshot["commitments"]
                + snapshot["events"]
                + snapshot["emails"]
                if x["key"] == body.key
            ),
            None,
        )
        if item is None:
            raise HTTPException(404, "This source is no longer in the selected day")
        key = "agenda-triage:" + selection.scope_key()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if "accountId" in item:
                # Recheck canonical cache membership within this same write transaction.
                row = db.execute(
                    "SELECT e.body,a.body AS account_body FROM email_snapshots e JOIN accounts a ON a.id=e.account_id WHERE e.account_id=?",
                    (item["accountId"],),
                ).fetchone()
                account = (
                    self.emails.accounts.vault.open(
                        "account:" + item["accountId"], row["account_body"]
                    )
                    if row
                    else {}
                )
                cached = (
                    self.emails.accounts.vault.open(
                        "email-snapshot:" + item["accountId"], row["body"]
                    )
                    if row
                    else {}
                )
                valid = GMAIL_READONLY in str(
                    account.get("email", {}).get("token", {}).get("scope", "")
                ).split() and any(
                    message["id"] == item["id"]
                    and message["accountId"] == item["accountId"]
                    for message in cached.get("items", [])
                )
            elif "calendarId" in item:
                row = db.execute(
                    "SELECT body FROM calendar_snapshots WHERE calendar_id=?",
                    (item["calendarId"],),
                ).fetchone()
                valid = row and any(
                    e["id"] == item["eventId"] and overlaps(e, selection)
                    for e in json.loads(row[0])
                )
            else:
                valid = db.execute(
                    "SELECT 1 FROM entities WHERE kind='commitment' AND id=? AND revision=?",
                    (item["id"], item["revision"]),
                ).fetchone()
            if not valid:
                raise HTTPException(409, "Source changed; refresh the agenda")
            row = db.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
            saved = json.loads(row[0]) if row else {}
            previous = saved.get(body.key, {"revision": 0})
            if previous["revision"] != body.revision:
                raise HTTPException(409, "Daily choice changed; refresh before editing")
            result = {"revision": body.revision + 1, "disposition": body.disposition}
            saved[body.key] = result
            db.execute(
                "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(saved)),
            )
        return {"key": body.key, **result}

    def chat(self, selection, *, reserve=False):
        key = "agenda-chat:" + selection.scope_key()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE" if reserve else "BEGIN")
            row = db.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
            saved = json.loads(row[0]) if row else None
            if saved is None and reserve:
                saved = {"actionId": str(uuid.uuid4()), "threadId": None}
                db.execute(
                    "INSERT INTO settings VALUES (?,?)", (key, json.dumps(saved))
                )
        return saved

    async def ensure_chat(self, selection):
        saved = self.chat(selection, reserve=True)
        if saved["threadId"]:
            return {**selection.identity(), "threadId": saved["threadId"]}
        result = await self.runtime.request(
            "POST", "/threads", {"client_action_id": saved["actionId"]}
        )
        thread_id = result.get("thread", {}).get("thread_id")
        if not isinstance(thread_id, str) or not thread_id or len(thread_id) > 200:
            raise HTTPException(
                502, "Companion did not return a conversation identifier"
            )
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            key = "agenda-chat:" + selection.scope_key()
            latest = json.loads(
                db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()[
                    0
                ]
            )
            if latest.get("threadId") not in (None, thread_id):
                raise HTTPException(409, "Daily conversation binding changed; reload")
            existing = db.execute(
                "SELECT value FROM settings WHERE key=?",
                ("companion-agenda:" + thread_id,),
            ).fetchone()
            if existing and json.loads(existing[0]) != selection.identity():
                raise HTTPException(409, "Runtime returned another day's conversation")
            db.execute(
                "UPDATE settings SET value=? WHERE key=?",
                (json.dumps({**latest, "threadId": thread_id}), key),
            )
            for key, value in [
                ("companion-agenda:" + thread_id, selection.identity()),
                (
                    ConversationTitles.key(thread_id),
                    {
                        "title": f"Today: {selection.date} · {selection.timezone}",
                        "revision": 1,
                    },
                ),
            ]:
                db.execute(
                    "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO NOTHING",
                    (key, json.dumps(value)),
                )
        return {**selection.identity(), "threadId": thread_id}

    def retire_chat(self, thread_id):
        """Only after confirmed native deletion; audit/admitted payloads survive."""
        reverse_key = "companion-agenda:" + thread_id
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value FROM settings WHERE key=?", (reverse_key,)
            ).fetchone()
            if not row:
                return
            selection = Selection(**json.loads(row[0]))
            key = "agenda-chat:" + selection.scope_key()
            saved = db.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
            if saved and json.loads(saved[0]).get("threadId") == thread_id:
                db.execute("DELETE FROM settings WHERE key=?", (key,))
            db.execute("DELETE FROM settings WHERE key=?", (reverse_key,))

    def reference(self, thread_id):
        binding = self.store.get("companion-agenda:" + thread_id)
        if not binding:
            return None
        snapshot = self.snapshot(Selection(**binding), limit=2**31)
        result = {
            k: snapshot[k]
            for k in (
                "date",
                "timezone",
                "window",
                "observedAt",
                "total",
                "hiddenEventCount",
            )
        }
        email_accounts = snapshot["sources"]["email"].get("accounts", [])
        state_counts = {}
        for account in email_accounts:
            state_counts[account["state"]] = state_counts.get(account["state"], 0) + 1
        synced = [
            account["syncedAt"]
            for account in email_accounts
            if account.get("syncedAt") is not None
        ]
        result.update(
            source="leam:agenda",
            calendarState=snapshot["sources"]["calendar"]["state"],
            emailState=snapshot["sources"]["email"]["state"],
            emailTruncated=snapshot["sources"]["email"]["truncated"],
            emailFreshness={
                "states": state_counts,
                "oldestSyncedAt": min(synced) if synced else None,
            },
            untrustedSourceData=True,
            partial=snapshot["partial"],
            items=[],
        )
        # Summaries are only working context; complete records remain in the UI.
        records = snapshot["commitments"] + snapshot["events"] + snapshot["emails"]
        records.sort(key=lambda x: x["triage"]["disposition"] != "focus")
        for item in records:
            entry = {
                k: item[k]
                for k in (
                    "key",
                    "id",
                    "title",
                    "triage",
                    "revision",
                    "date",
                    "log",
                    "start",
                    "end",
                    "allDay",
                    "calendarId",
                    "eventId",
                    "syncedAt",
                    "accountId",
                    "threadId",
                    "receivedAt",
                    "unread",
                    "important",
                    "sourceState",
                )
                if k in item
            }
            entry["title"] = item.get("title", item.get("subject", ""))
            if len(entry["title"]) > 160:
                entry["titlePartial"] = True
            entry["title"] = entry["title"][:160]
            candidate = {**result, "items": [*result["items"], entry]}
            if (
                len(json.dumps(candidate, ensure_ascii=False).encode())
                > REFERENCE_BYTES
            ):
                result["partial"] = True
                break
            result = candidate
        return result


def router(agenda):
    routes = APIRouter(prefix="/api/agenda")
    visibility = CalendarVisibility(agenda.store)

    def selection(day, timezone):
        try:
            return Selection(date=day, timezone=timezone)
        except ValueError:
            raise HTTPException(422, "Choose a valid date and IANA timezone") from None

    @routes.get("")
    async def snapshot(
        date: Day,
        timezone: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
        includeHidden: bool = False,
    ):
        return agenda.snapshot(
            selection(date, timezone),
            offset=offset,
            limit=limit,
            include_hidden=includeHidden,
        )

    @routes.get("/calendar-visibility")
    async def hidden_calendars(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return visibility.listing(offset=offset, limit=limit)

    @routes.put("/calendar-visibility")
    async def change_visibility(body: VisibilityChange):
        return visibility.change(body)

    @routes.put("/triage")
    async def triage(body: Triage):
        return agenda.triage(body)

    @routes.get("/chat")
    async def chat(date: Day, timezone: str):
        chosen = selection(date, timezone)
        saved = agenda.chat(chosen)
        return {**chosen.identity(), "threadId": saved["threadId"] if saved else None}

    @routes.post("/chat")
    async def create_chat(body: Selection):
        return await agenda.ensure_chat(body)

    return routes
